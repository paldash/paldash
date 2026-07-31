"""
Detecting a Palworld update.

The shape of this feature is "cheap check on a timer, expensive diff on demand",
so the tests that matter are about the split:

  * `fingerprint()` reads two local files and **makes no network call**. It runs
    every minute; a round trip to the game server there would be a network
    dependency on a heartbeat.
  * `poll()` only ever *notices*. Re-extracting 51,921 positions from 9,977 cell
    packages is minutes of work next to a live game server, so it stays an
    operator decision.
  * A missing signal is `unknown`, never `current`. Asserting a match nobody
    verified is precisely the failure this module exists to prevent.
"""

from __future__ import annotations

import json
import os

import pytest

import gameversion

MANIFEST = """"AppState"
{
\t"appid"\t\t"2394010"
\t"name"\t\t"Palworld Dedicated Server"
\t"installdir"\t\t"PalServer"
\t"LastUpdated"\t\t"1785300047"
\t"buildid"\t\t"%s"
\t"InstalledDepots"
\t{
\t\t"2394012"
\t\t{
\t\t\t"manifest"\t\t"123456789"
\t\t}
\t}
}
"""


@pytest.fixture(autouse=True)
def fresh_poll_clock():
    """
    The rate limiter is module state, so one test's poll would silence the next.
    """
    gameversion.reset_for_tests()
    yield
    gameversion.reset_for_tests()


@pytest.fixture
def install(tmp_path, monkeypatch):
    """A fake install tree with a manifest and a pak."""
    root = tmp_path / "palworld"
    (root / "steamapps").mkdir(parents=True)
    paks = root / "Pal" / "Content" / "Paks"
    paks.mkdir(parents=True)
    (paks / "Pal-LinuxServer.pak").write_bytes(b"x" * 2048)

    def write_build(build_id: str) -> None:
        (root / "steamapps" / "appmanifest_2394010.acf").write_text(
            MANIFEST % build_id
        )

    write_build("24370498")
    monkeypatch.setattr(gameversion, "INSTALL_DIR", str(root))
    return {"root": root, "write_build": write_build}


@pytest.fixture
def no_network(monkeypatch):
    """
    Fail loudly if anything in the runtime path tries to reach the game server.

    `game_version()` swallows every exception by design, so a network call would
    otherwise pass silently — which is exactly how it got into `status()` in the
    first place.
    """
    calls = []

    def forbidden():
        calls.append(1)
        raise AssertionError("fingerprint() must not make a network call")

    monkeypatch.setattr(gameversion, "game_version", forbidden)
    return calls


def provenance_file(tmp_path, monkeypatch, entries: dict):
    path = tmp_path / "provenance.json"
    path.write_text(json.dumps(entries))
    monkeypatch.setattr(gameversion, "PROVENANCE_PATH", str(path))


# ─── Reading the signals ─────────────────────────────────


def test_the_build_id_comes_out_of_the_manifest(install):
    assert gameversion.fingerprint()["buildId"] == "24370498"
    assert gameversion.fingerprint()["buildIdSource"] == "appmanifest"


def test_nested_depot_keys_do_not_confuse_the_parse(install):
    """
    The loose regex parse is fine *because* no wanted key collides with anything
    inside `InstalledDepots`. Worth pinning, since a nested `manifest` key sits
    right there and a sloppier reader could pick it up as the build.
    """
    manifest = gameversion.read_manifest()
    assert manifest["buildid"] == "24370498"
    assert manifest["appid"] == "2394010"


def test_the_pak_stamp_moves_when_the_pak_does(install):
    before = gameversion.fingerprint()["pakStamp"]
    pak = install["root"] / "Pal" / "Content" / "Paks" / "Pal-LinuxServer.pak"
    pak.write_bytes(b"y" * 4096)
    assert gameversion.fingerprint()["pakStamp"] != before


def test_fingerprint_makes_no_network_call(install, no_network):
    """The whole reason it can run on a once-a-minute tick."""
    gameversion.fingerprint()
    assert no_network == []


def test_status_makes_no_network_call(install, no_network, fresh_db):
    """It backs a page load, so it must not wait on the game server either."""
    gameversion.status()
    assert no_network == []


def test_a_missing_install_yields_no_signals(monkeypatch):
    monkeypatch.setattr(gameversion, "INSTALL_DIR", "")
    monkeypatch.setattr(gameversion, "SAVE_BASE_DIR", "")
    signals = gameversion.fingerprint()
    assert signals["buildId"] == ""
    assert signals["manifestFound"] is False


def test_the_install_root_is_derived_from_the_save_path(tmp_path, monkeypatch):
    """
    The save mount is the one the dashboard is guaranteed to have, and the install
    root sits four levels above `<root>/Pal/Saved/SaveGames/0`.
    """
    root = tmp_path / "palworld"
    saves = root / "Pal" / "Saved" / "SaveGames" / "0"
    saves.mkdir(parents=True)
    monkeypatch.setattr(gameversion, "INSTALL_DIR", "")
    monkeypatch.setattr(gameversion, "SAVE_BASE_DIR", str(saves))
    assert gameversion.install_dir() == str(root)


# ─── Direction ───────────────────────────────────────────


def test_direction_distinguishes_an_update_from_a_rollback():
    """
    Both invalidate the bundled positions, but they need different responses: an
    update waits for newer data, a pinned rollback needs re-extraction against the
    build the operator chose.
    """
    assert gameversion.direction("24370498", "24500000") == "up"
    assert gameversion.direction("24500000", "24370498") == "down"
    assert gameversion.direction("24370498", "24370498") == "same"


def test_direction_is_unknown_for_non_numeric_ids():
    """These are strings from a text file; nothing guarantees they stay numeric."""
    assert gameversion.direction("", "24370498") == "unknown"
    assert gameversion.direction("1.2.3", "1.2.4") == "unknown"


# ─── The verdict ─────────────────────────────────────────


def test_matching_provenance_reads_as_current(install, fresh_db, tmp_path, monkeypatch):
    provenance_file(tmp_path, monkeypatch, {
        "worldobjects.json.gz": {"gameBuild": "24370498", "source": "pak"},
    })
    status = gameversion.status()
    assert status["verdict"] == "current"
    assert status["staleArtifacts"] == []


def test_a_newer_build_makes_the_data_stale(install, fresh_db, tmp_path, monkeypatch):
    provenance_file(tmp_path, monkeypatch, {
        "worldobjects.json.gz": {
            "gameBuild": "24370498", "source": "pak",
            "regenerateWith": "python3 scripts/extract-world-objects.py",
        },
    })
    install["write_build"]("24500000")

    status = gameversion.status()
    assert status["verdict"] == "stale"
    assert status["staleArtifacts"] == ["worldobjects.json.gz"]
    assert "extract-world-objects" in status["artifacts"][0]["regenerateWith"]
    assert "24500000" in status["reason"]


def test_unrecorded_provenance_is_unknown_not_current(
    install, fresh_db, tmp_path, monkeypatch
):
    """
    `gamedata.json.gz` genuinely has no build id — it comes from a third-party
    dump. Reporting that as current would be the exact false assurance this
    module exists to avoid.
    """
    provenance_file(tmp_path, monkeypatch, {
        "gamedata.json.gz": {"gameBuild": None, "source": "third-party archive"},
    })
    status = gameversion.status()
    assert status["verdict"] == "unknown"
    assert status["unknownArtifacts"] == ["gamedata.json.gz"]


def test_no_manifest_is_unknown_and_says_how_to_fix_it(
    fresh_db, tmp_path, monkeypatch
):
    monkeypatch.setattr(gameversion, "INSTALL_DIR", "")
    monkeypatch.setattr(gameversion, "SAVE_BASE_DIR", "")
    provenance_file(tmp_path, monkeypatch, {
        "worldobjects.json.gz": {"gameBuild": "24370498"},
    })
    status = gameversion.status()
    assert status["verdict"] == "unknown"
    assert "PALWORLD_INSTALL_DIR" in status["reason"]


def test_a_stale_artifact_wins_over_an_unknown_one(
    install, fresh_db, tmp_path, monkeypatch
):
    """
    One definitely-wrong file matters more than one we cannot date, so the overall
    verdict is the worse of the two rather than the last one read.
    """
    provenance_file(tmp_path, monkeypatch, {
        "gamedata.json.gz": {"gameBuild": None},
        "worldobjects.json.gz": {"gameBuild": "22000000"},
    })
    status = gameversion.status()
    assert status["verdict"] == "stale"


def test_a_missing_provenance_file_is_not_fatal(install, fresh_db, monkeypatch):
    monkeypatch.setattr(gameversion, "PROVENANCE_PATH", "/nonexistent/provenance.json")
    status = gameversion.status()
    assert status["verdict"] == "unknown"
    assert status["artifacts"] == []


# ─── The scheduled poll ──────────────────────────────────


def test_the_first_poll_records_without_claiming_a_change(install, fresh_db):
    """
    Nothing to compare against yet. Reporting a change would fire the banner on
    every fresh install.
    """
    result = gameversion.poll()
    assert result["checked"] is True
    assert result["changed"] is False
    assert result["buildId"] == "24370498"


def test_a_second_identical_poll_is_quiet(install, fresh_db):
    gameversion.poll()
    assert gameversion.poll(force=True)["changed"] is False


def test_the_poll_rate_limits_itself(install, fresh_db):
    """
    The scheduler calls this every minute; this module decides how often that is
    worth acting on. A Palworld update lands roughly monthly, so re-reading the
    manifest 1,440 times a day would be ~43,000 checks per detection.
    """
    assert gameversion.poll()["checked"] is True
    second = gameversion.poll()
    assert second == {"checked": False, "changed": False, "reason": "not due"}


def test_the_first_poll_after_startup_always_runs(install, fresh_db):
    """
    The one that matters. An auto-updating server container updates and restarts,
    so boot is when a new build is actually there to find — a first call that
    deferred to the interval would miss exactly the case this is for.
    """
    gameversion.reset_for_tests()
    assert gameversion.poll()["checked"] is True


def test_a_short_interval_can_be_configured(install, fresh_db, monkeypatch):
    monkeypatch.setattr(gameversion, "CHECK_INTERVAL_SECONDS", 0)
    gameversion.poll()
    assert gameversion.poll()["checked"] is True


def test_a_build_change_is_detected_and_audited(install, fresh_db):
    import audit
    import db

    gameversion.poll()
    install["write_build"]("24500000")
    result = gameversion.poll(force=True)

    assert result["changed"] is True
    assert result["direction"] == "up"
    assert result["previousBuildId"] == "24370498"

    rows = db.connect().execute(
        "SELECT * FROM audit_log WHERE target LIKE 'game_build:%'"
    ).fetchall()
    assert len(rows) == 1
    assert "24500000" in rows[0]["detail"]
    assert rows[0]["username"] == "scheduler"


def test_the_poll_never_runs_an_extractor(install, fresh_db, monkeypatch):
    """
    The load-bearing constraint. Walking 9,977 cell packages takes minutes and
    would be doing it beside a live game server, so detection must stay a stat and
    a small read.
    """
    import subprocess

    def forbidden(*args, **kwargs):
        raise AssertionError("poll() must not spawn a process")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    install["write_build"]("24999999")
    gameversion.poll()


def test_a_poll_with_no_signal_reports_that_rather_than_a_change(fresh_db, monkeypatch):
    monkeypatch.setattr(gameversion, "INSTALL_DIR", "")
    monkeypatch.setattr(gameversion, "SAVE_BASE_DIR", "")
    result = gameversion.poll()
    assert result == {"checked": False, "changed": False, "reason": "no signal available"}


def test_first_seen_resets_on_a_change(install, fresh_db):
    """
    It dates *this* build — what "updated 3 days ago" needs — rather than when the
    dashboard was first started.
    """
    import db

    gameversion.poll()
    original = db.connect().execute(
        "SELECT first_seen FROM game_build WHERE id = 1"
    ).fetchone()["first_seen"]

    gameversion.poll(force=True)   # unchanged: first_seen must hold
    assert db.connect().execute(
        "SELECT first_seen FROM game_build WHERE id = 1"
    ).fetchone()["first_seen"] == original

    install["write_build"]("24500000")
    gameversion.poll(force=True)
    assert db.connect().execute(
        "SELECT first_seen FROM game_build WHERE id = 1"
    ).fetchone()["first_seen"] != original


# ─── Acknowledging ───────────────────────────────────────


def test_acknowledging_silences_only_that_build(
    install, fresh_db, tmp_path, monkeypatch
):
    """
    Someone who checked their data against build A has said nothing about B, so the
    next update raises the banner again rather than staying dismissed forever.
    """
    provenance_file(tmp_path, monkeypatch, {
        "worldobjects.json.gz": {"gameBuild": "22000000"},
    })
    assert gameversion.status()["acknowledged"] is False

    gameversion.acknowledge("24370498")
    assert gameversion.status()["acknowledged"] is True

    install["write_build"]("24500000")
    assert gameversion.status()["acknowledged"] is False


def test_the_bundled_provenance_file_is_valid_and_covers_every_bundle():
    """
    Not a fixture — this is the file that ships. A bundle missing from it is a
    bundle whose staleness can never be reported.
    """
    prov = gameversion.provenance()
    assert prov, "backend/data/provenance.json is missing or unreadable"

    bundled = {
        name for name in os.listdir(gameversion.DATA_DIR)
        if name.endswith(".json.gz")
    }
    documented = {name for name in prov if not name.startswith("_")}
    assert bundled - documented == set(), (
        f"bundled data with no provenance entry: {bundled - documented}"
    )

    for name, entry in prov.items():
        if name.startswith("_"):
            continue
        assert entry.get("source"), f"{name} has no source"
        assert "gameBuild" in entry, f"{name} does not say which build it came from"
