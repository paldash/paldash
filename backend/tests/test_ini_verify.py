"""
"Applied" was a claim about a write, not about the setting.

`write_ini` reports success when the bytes land on disk. Whether the value is
still there after the server restarts is a different question, and on an image
that regenerates PalWorldSettings.ini from environment variables the answer is
routinely no — which the operator finds out by noticing, weeks later, that the
difficulty never changed.

`iniwatch` already hashed the whole file to answer "does this deployment rewrite
its INI". That is a fact about the deployment. These tests are about the narrower
question the operator actually asked: **did the key I changed survive?**

The two come apart in both directions, which is the reason for the second check
rather than a reformulation of the first.
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

_BASE_INI = (
    "[/Script/Pal.PalGameWorldSettings]\n"
    "OptionSettings=(Difficulty=None,DayTimeSpeedRate=1.000000,"
    'AdminPassword="old",ServerPassword="",ServerName="hi",bIsPvP=False)\n'
)


@pytest.fixture
def env(fresh_db, tmp_path, monkeypatch):
    """
    A database and an INI of our own, so nothing here touches a real server.

    **`fresh_db`, not a `setenv`.** Backend modules capture environment variables
    at import time, so tests monkeypatch the module attribute — and `db.py` also
    caches a connection per thread, which `fresh_db` drops. A first version set
    `DB_PATH` in the environment, which `db.py` does not read (the variable is
    `DASHBOARD_DB`), so all eight tests silently shared the development database
    and each saw the previous one's rows.
    """
    import db
    import iniwatch
    import settings_ini

    backups = str(tmp_path / "backups")
    monkeypatch.setattr(settings_ini, "BACKUP_DIR", backups)

    iniwatch.init()

    ini = tmp_path / "PalWorldSettings.ini"
    ini.write_text(_BASE_INI, encoding="utf-8")
    return {"ini": str(ini), "iniwatch": iniwatch, "settings_ini": settings_ini, "db": db}


def _rewrite(path: str, **pairs: str) -> None:
    """
    Stand in for an image regenerating the file with different values.

    **Reads the file as it is now**, rather than rebuilding from `_BASE_INI`. A
    first version rebuilt, which reset every key back to its starting value — so
    the test asserting that an unrelated rewrite is benign was also silently
    reverting the very key it claimed to leave alone, and "failed" against
    correct code. A helper that does more than its name says is worse than no
    helper.
    """
    import re

    with open(path, encoding="utf-8") as f:
        body = f.read()
    for key, value in pairs.items():
        body = re.sub(rf"\b{key}=[^,)]*", f"{key}={value}", body)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)


# ─── The security property, first, because it is the one that matters ───


def test_a_password_is_NEVER_stored_in_plaintext(env):
    """
    **The one trap in an otherwise read-only feature.** Verifying "what we wrote
    is what is on disk" means keeping a copy of what we wrote, and this path
    writes `AdminPassword`. `settings_ini` masks it on read and in the audit log
    so it does not reach logs or a network tab; a verification record holding the
    plaintext would undo that somewhere new, and somewhere that outlives the
    request.
    """
    env["settings_ini"].write_ini({"AdminPassword": "hunter2"}, env["ini"])

    rows = env["db"].connect().execute("SELECT * FROM ini_pending_keys").fetchall()
    assert rows, "nothing was recorded to verify"
    for row in rows:
        assert "hunter2" not in row["expected"]
        assert row["expected"].startswith("scrypt$"), (
            "a secret must be sealed, and a fast hash of a low-entropy server "
            "password is not sealing it"
        )
        assert row["secret"] == 1

    # And nowhere else in the database either — a stray audit or settings row
    # would defeat the point entirely.
    dump = "".join(env["db"].connect().iterdump())
    assert "hunter2" not in dump


def test_a_password_never_reaches_the_payload_in_either_direction(env):
    """
    Not the value we wrote, and not the value found on disk. The second is the
    easier one to leak, because it arrives through `read_ini(reveal=True)` — the
    call this module has to make and that nothing else outside the write path may.
    """
    env["settings_ini"].write_ini({"AdminPassword": "hunter2"}, env["ini"])
    _rewrite(env["ini"], AdminPassword='"leakme"')

    result = env["iniwatch"].verify_written_keys(env["ini"])
    blob = repr(result) + repr(env["iniwatch"].describe())
    assert "hunter2" not in blob
    assert "leakme" not in blob

    secret_rows = [k for k in result["keys"] if k["secret"]]
    assert secret_rows, "the secret key was not checked at all"
    for row in secret_rows:
        assert row["expected"] == "" and row["actual"] == ""


def test_a_password_that_survives_still_verifies(env):
    """
    Sealing must not become skipping. The verdict is real for secrets — only the
    values are withheld.
    """
    env["settings_ini"].write_ini({"AdminPassword": "hunter2"}, env["ini"])
    result = env["iniwatch"].verify_written_keys(env["ini"])
    secret = [k for k in result["keys"] if k["key"] == "AdminPassword"][0]
    assert secret["verdict"] == "verified"


# ─── The verification itself ───


def test_the_written_value_is_compared_not_the_requested_one(env):
    """
    `_format` renders `2.0` as `2.000000`. Comparing what the caller *asked* for
    against what is in the file would report every float write as reverted, on
    every server, forever — a permanent false alarm, which is worse than no check
    because it teaches the operator to ignore the panel.
    """
    env["settings_ini"].write_ini({"DayTimeSpeedRate": 2.0}, env["ini"])
    result = env["iniwatch"].verify_written_keys(env["ini"])
    assert result["warnings"] == []
    assert result["verified"] == 1


def test_a_reverted_key_is_named_with_both_values(env):
    env["settings_ini"].write_ini({"DayTimeSpeedRate": 2.0}, env["ini"])
    _rewrite(env["ini"], DayTimeSpeedRate="1.000000")

    result = env["iniwatch"].verify_written_keys(env["ini"])
    assert result["verified"] == 0
    assert len(result["warnings"]) == 1
    assert "DayTimeSpeedRate" in result["warnings"][0]
    assert "2.000000" in result["warnings"][0]
    assert "1.000000" in result["warnings"][0]


def test_a_key_that_vanished_is_a_revert_not_a_gap(env):
    """
    A regenerating image that has never heard of a key writes a file without it,
    and the game falls back to its own default. The operator's change is just as
    gone as if it had been overwritten, so `missing` counts as a warning.
    """
    env["settings_ini"].write_ini({"DayTimeSpeedRate": 2.0}, env["ini"])
    with open(env["ini"], "w", encoding="utf-8") as f:
        f.write(
            "[/Script/Pal.PalGameWorldSettings]\n"
            "OptionSettings=(Difficulty=None,bIsPvP=False)\n"
        )

    result = env["iniwatch"].verify_written_keys(env["ini"])
    assert [k["verdict"] for k in result["keys"]] == ["missing"]
    assert len(result["warnings"]) == 1


def test_an_unreadable_ini_is_unchecked_not_reverted(env):
    """
    "We could not look" and "your change was undone" are different answers, and
    this project keeps them apart everywhere — the missing ban list, the
    unreachable game server, the unparsed world. Collapsing them here would
    accuse an image of reverting settings every time a volume was slow to mount.
    """
    env["settings_ini"].write_ini({"DayTimeSpeedRate": 2.0}, env["ini"])
    os.remove(env["ini"])

    result = env["iniwatch"].verify_written_keys(env["ini"])
    assert [k["verdict"] for k in result["keys"]] == ["unchecked"]
    assert result["warnings"] == []
    assert any("could not be checked" in n for n in result["notes"])


# ─── The shape: warnings are actionable, notes are merely true ───


def test_a_benign_whole_file_change_is_a_NOTE_not_a_failure(env):
    """
    **This is why the result has two lists.** An image can rewrite the file and
    leave the key you changed alone; the whole-file verdict says `regenerated`
    and is right, but rendering that as VERIFY FAILED tells the operator their
    change was lost when it was not.
    """
    env["settings_ini"].write_ini({"DayTimeSpeedRate": 2.0}, env["ini"])
    # Something else in the file moved. Not our key.
    _rewrite(env["ini"], ServerName='"renamed by the image"')

    result = env["iniwatch"].verify_written_keys(env["ini"])
    assert result["warnings"] == [], "an untouched key was reported as a failure"
    assert result["verified"] == 1


def test_a_clean_result_carries_no_warnings_and_no_pending(env):
    env["settings_ini"].write_ini(
        {"DayTimeSpeedRate": 2.0, "bIsPvP": True}, env["ini"]
    )
    described = env["iniwatch"].describe()
    assert sorted(described["pendingKeys"]) == ["DayTimeSpeedRate", "bIsPvP"]
    assert described["awaitingRestart"] is True

    env["iniwatch"].observe_after_restart(env["ini"])

    after = env["iniwatch"].describe()
    assert after["pendingKeys"] == [], "pending rows outlived their verification"
    assert after["awaitingRestart"] is False
    assert after["keyVerification"]["verified"] == 2
    assert after["keyVerification"]["warnings"] == []


def test_observing_a_restart_verifies_the_keys_in_the_same_pass(env):
    """
    The per-key check runs inside `observe_after_restart` because that is the one
    moment both halves of the comparison exist — after this, the pending rows are
    gone and there is nothing left to compare against.
    """
    env["settings_ini"].write_ini({"DayTimeSpeedRate": 2.0}, env["ini"])
    _rewrite(env["ini"], DayTimeSpeedRate="1.000000")

    described = env["iniwatch"].observe_after_restart(env["ini"])
    assert described["verdict"] == "regenerated"
    assert len(described["keyVerification"]["warnings"]) == 1


def test_nothing_written_means_nothing_claimed(env):
    """
    An empty result must not read as success. Zero checked is zero checked.
    """
    result = env["iniwatch"].verify_written_keys(env["ini"])
    assert result == {
        "checked": 0, "verified": 0, "keys": [], "warnings": [], "notes": [],
    }
