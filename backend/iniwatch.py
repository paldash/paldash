"""
Does this server image rewrite PalWorldSettings.ini behind us?

THE PROBLEM
-----------
`thijsvanloef/palworld-server-docker` regenerates the INI from environment
variables **on every start**. A setting written through the Settings tab survives
until the next restart and is then silently reverted — worse than a refusal,
because the operator watched it work. `jammsen/palworld-dedicated-server` ships
`SERVER_SETTINGS_MODE=manual` and leaves it alone. The official
`ghcr.io/pocketpairjp/palserver` has one environment variable in total and leaves
it alone too.

**The dashboard cannot read the game container's environment**, so it cannot
detect this by looking. What it shipped instead was a conditional warning naming
15 keys known to be env-driven — against ~89 game settings on thijsvanloef and
127 on jammsen. Under-warning on most of the file, and hedged even where it was
right.

THE APPROACH: OBSERVE, DO NOT RECOGNISE
---------------------------------------
Hash the file when we write it. Hash it again after the server has been away and
come back. If the content changed and we did not change it, **this image
regenerates its INI** — a fact, about this deployment, covering every key rather
than a list of 15.

Deliberately not "which image is this". A name lookup is a guess that ages badly;
behaviour observed on the operator's own server does not. It also gets the
awkward cases right for free — jammsen with `SERVER_SETTINGS_MODE=auto` looks
exactly like thijsvanloef, because it *is* behaving exactly like it.

WHY THIS IS NOT A GUESS ABOUT CAUSE
-----------------------------------
A changed hash means *something* rewrote the file, which is not necessarily the
image: an operator hand-editing it between restarts produces the same evidence.
So the verdict is reported as what was measured — "your INI changed across a
restart and we did not do it" — with the count and the timestamps, rather than as
an accusation about a specific image. `describe()` returns `unknown` until there
is evidence either way, and never guesses from a container name.

AND THE FILE HASH IS THE WRONG GRANULARITY FOR THE OPERATOR'S ACTUAL QUESTION
-----------------------------------------------------------------------------
Everything above is a statement about the *deployment*. The operator asked a
narrower one: **did the setting I just changed survive?**

Those come apart in both directions, which is why the hash alone was not enough:

- An image can rewrite the file and still leave the key you changed alone —
  reporting `regenerated` there is a true statement that reads as "your change
  was lost" when it was not.
- An image can leave 126 keys alone and revert the one you cared about, and a
  whole-file verdict of `regenerated` gives the same undifferentiated warning it
  gives for a cosmetic reformat.

So `verify_written_keys` re-reads the INI once the server is back and compares
**each key we wrote** against what is on disk. `warnings` are keys that did not
survive — actionable. `notes` are true observations that are not failures, kept
separate for exactly the reason above: a benign whole-file change must not render
as VERIFY FAILED.

**THE ONE TRAP, AND IT IS A SECURITY ONE.** Verifying "what we wrote is what is
on disk" means keeping a copy of what we wrote, and `AdminPassword` and
`ServerPassword` go through this path. `settings_ini` masks those on read and in
the audit log precisely so they do not reach logs, screenshots or a network tab —
a verification record holding the plaintext would undo that in a *new* place, and
one that outlives the request. They are stored as **scrypt hashes** (the same
function that hashes account passwords, rather than a second implementation), and
neither the stored value nor the on-disk value is ever returned to a caller. The
verdict for a secret is the comparison result and nothing else.
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

import db

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS ini_watch (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    path          TEXT    NOT NULL DEFAULT '',
    hash          TEXT    NOT NULL DEFAULT '',
    -- Set when *we* wrote the file, cleared once a restart has been observed.
    -- Without it a dashboard write would itself look like a foreign rewrite.
    written_by_us INTEGER NOT NULL DEFAULT 0,
    written_at    TEXT    NOT NULL DEFAULT '',
    -- Verdict, once observed: '' unknown, 'preserved', 'regenerated'.
    verdict       TEXT    NOT NULL DEFAULT '',
    observed_at   TEXT    NOT NULL DEFAULT '',
    detail        TEXT    NOT NULL DEFAULT ''
);

-- What the dashboard wrote and has not yet been able to check, one row per key.
--
-- Separate tables rather than columns on `ini_watch` so that adding this needed
-- no migration on a database that already has that row: `CREATE TABLE IF NOT
-- EXISTS` covers a new table and silently does nothing for a new column.
CREATE TABLE IF NOT EXISTS ini_pending_keys (
    key        TEXT PRIMARY KEY,
    -- The raw INI text we wrote — or, for a secret, a scrypt hash of it.
    expected   TEXT    NOT NULL,
    secret     INTEGER NOT NULL DEFAULT 0,
    written_at TEXT    NOT NULL DEFAULT ''
);

-- The last verdict per key, kept after the pending row is consumed.
CREATE TABLE IF NOT EXISTS ini_key_verdicts (
    key         TEXT PRIMARY KEY,
    verdict     TEXT    NOT NULL,
    -- Empty for secrets, always. See the module docstring.
    expected    TEXT    NOT NULL DEFAULT '',
    actual      TEXT    NOT NULL DEFAULT '',
    secret      INTEGER NOT NULL DEFAULT 0,
    observed_at TEXT    NOT NULL DEFAULT ''
);
"""


def init() -> None:
    with db.transaction() as conn:
        conn.executescript(SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row() -> dict[str, Any]:
    init()
    row = db.connect().execute("SELECT * FROM ini_watch WHERE id = 1").fetchone()
    return dict(row) if row else {
        "path": "", "hash": "", "written_by_us": 0,
        "written_at": "", "verdict": "", "observed_at": "", "detail": "",
    }


def hash_file(path: Optional[str]) -> str:
    """
    Content hash of the INI, or "" when it cannot be read.

    Content rather than mtime: an image that rewrites the file with byte-identical
    content has not changed anything an operator cares about, and a touch that
    changes only the timestamp should not read as a revert.
    """
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError as e:
        logger.warning("Could not hash %s: %s", path, e)
        return ""


def record_our_write(
    path: Optional[str],
    written: Optional[dict[str, str]] = None,
    secret_keys: tuple[str, ...] = (),
) -> None:
    """
    Remember what the file looked like immediately after the dashboard wrote it.

    Called from the settings write path. `written_by_us` is what stops our own
    edit being mistaken for the image's.

    `written` is `{key: raw INI text}` — exactly the substitutions `write_ini`
    made, so the comparison later is against what went into the file rather than
    against what the caller asked for. Those differ: `_format` turns `True` into
    `True` and `2` into `2.000000`, and comparing the request would report every
    float write as reverted.

    **A secret's raw value is hashed here and never stored.** See the module
    docstring; this is the line that keeps `AdminPassword` out of the database.
    """
    digest = hash_file(path)
    if not digest:
        return
    init()
    now = _now()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO ini_watch (id, path, hash, written_by_us, written_at) "
            "VALUES (1, ?, ?, 1, ?) "
            "ON CONFLICT(id) DO UPDATE SET path = excluded.path, hash = excluded.hash, "
            "written_by_us = 1, written_at = excluded.written_at",
            (path or "", digest, now),
        )
        for key, raw in (written or {}).items():
            secret = key in secret_keys
            conn.execute(
                "INSERT INTO ini_pending_keys (key, expected, secret, written_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                "expected = excluded.expected, secret = excluded.secret, "
                "written_at = excluded.written_at",
                (key, _seal(raw) if secret else raw, 1 if secret else 0, now),
            )


def _seal(raw: str) -> str:
    """
    A secret's value, in a form that can be compared but not read back.

    `accounts.hash_password` rather than a second hashing implementation here:
    a server password is a password, the parameters are already chosen and
    reviewed, and two scrypt call sites is two places to get `maxmem` wrong.
    """
    import accounts

    return accounts.hash_password(raw)


def _matches(expected: str, actual: str, secret: bool) -> bool:
    if secret:
        import accounts

        return accounts.verify_password(actual, expected)
    return expected == actual


def observe_after_restart(path: Optional[str]) -> dict[str, Any]:
    """
    Compare the file against what we last wrote. Called when the server returns.

    Returns the verdict dict. Does nothing useful until the dashboard has written
    the INI at least once — there is no baseline before that, and inventing one
    from the file as found would compare it against itself.
    """
    row = _row()
    if not row["written_by_us"] or not row["hash"]:
        return describe()

    current = hash_file(path or row["path"])
    if not current:
        # The file went away. That is not evidence about regeneration, so the
        # baseline is kept rather than resolved either way.
        return describe()

    if current == row["hash"]:
        verdict, detail = "preserved", (
            "PalWorldSettings.ini was unchanged across a server restart, so this "
            "image does not regenerate it. Settings written here persist."
        )
    else:
        verdict, detail = "regenerated", (
            "PalWorldSettings.ini changed across a server restart and the "
            "dashboard did not change it. This image rewrites the file on start "
            "— most likely from environment variables in your compose file — so "
            "settings written here last only until the next restart. Change them "
            "in your compose instead, or use an image that leaves the file alone."
        )

    # Per-key BEFORE the pending rows are cleared below, and before the row is
    # marked observed: this is the only moment both halves of the comparison
    # exist at once.
    keys = verify_written_keys(path or row["path"])

    with db.transaction() as conn:
        conn.execute(
            "UPDATE ini_watch SET verdict = ?, observed_at = ?, detail = ?, "
            "written_by_us = 0, hash = ? WHERE id = 1",
            (verdict, _now(), detail, current),
        )
        conn.execute("DELETE FROM ini_pending_keys")
    logger.info(
        "INI watch verdict: %s (%d key(s) checked, %d did not survive)",
        verdict, keys["checked"], len(keys["warnings"]),
    )
    return describe()


def verify_written_keys(path: Optional[str]) -> dict[str, Any]:
    """
    Did each key the dashboard wrote survive the restart?

    **This is the question the operator actually asked**, and the whole-file hash
    above cannot answer it — see the module docstring for the two ways they come
    apart. Records a verdict per key and returns the summary.

    Reads with `reveal=True` because a secret's on-disk value is needed to
    compare against the stored hash. **The revealed value never leaves this
    function**: it goes into `_matches` and nowhere else, and the row written for
    a secret key carries empty `expected` and `actual`.

    An unreadable INI is `unchecked`, not `reverted`. "We could not look" and
    "your change was undone" are different answers and this project keeps them
    apart everywhere else — a missing ban list, an unreachable game server, an
    unparsed world.
    """
    init()
    pending = [
        dict(r) for r in db.connect()
        .execute("SELECT * FROM ini_pending_keys ORDER BY key").fetchall()
    ]
    if not pending:
        return _key_summary([])

    import settings_ini

    try:
        options = settings_ini.read_ini(path, reveal=True)["options"]
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not re-read the INI to verify keys: %s", e)
        options = None

    now = _now()
    results: list[dict[str, Any]] = []
    for row in pending:
        key, secret = row["key"], bool(row["secret"])
        if options is None:
            verdict, actual = "unchecked", ""
        elif key not in options:
            # The key vanished from OptionSettings entirely. A regenerating image
            # that does not know this key writes a file without it, and the game
            # then falls back to its own default — so this is a revert, not a
            # missing measurement.
            verdict, actual = "missing", ""
        else:
            actual = str(options[key].get("raw") or "")
            verdict = (
                "verified" if _matches(row["expected"], actual, secret) else "reverted"
            )
        # The actionable half of a failed verdict (#132): on a regenerating
        # image the INI is a projection of the compose file, so "your change
        # was reverted" without naming the env var sends the operator to edit
        # the same file that just got overwritten. Attached on every verdict,
        # not only failures — a verified key on such an image is one restart
        # of a changed compose file away from the same fate.
        env_hint = settings_ini._env_display(key)
        results.append({
            "key": key,
            "verdict": verdict,
            "secret": secret,
            # Never for a secret, in either direction, whatever the verdict.
            "expected": "" if secret else row["expected"],
            "actual": "" if secret else actual,
            "envVar": env_hint,
        })

    with db.transaction() as conn:
        for r in results:
            conn.execute(
                "INSERT INTO ini_key_verdicts "
                "(key, verdict, expected, actual, secret, observed_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                "verdict = excluded.verdict, expected = excluded.expected, "
                "actual = excluded.actual, secret = excluded.secret, "
                "observed_at = excluded.observed_at",
                (r["key"], r["verdict"], r["expected"], r["actual"],
                 1 if r["secret"] else 0, now),
            )
    return _key_summary(results)


#: Verdicts that mean the operator's change did not take. Everything else is
#: either fine or an admission that we could not look.
_FAILED = ("reverted", "missing")


def _key_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    """
    A `VerifyResult`: warnings are actionable, notes are merely true.

    Keeping them apart is the point. A settings change that applied cleanly on a
    server whose image also rewrote an unrelated key must read as success with a
    note — not as VERIFY FAILED, which is how a single flat list of "findings"
    renders and how an operator learns to ignore the panel.
    """
    warnings = [
        (
            f"{r['key']} did not survive the restart"
            + ("" if r["secret"] else f" — expected {r['expected']}, found "
               + (r["actual"] or "the key to be absent"))
            # The fix lives in the compose file, not this dashboard, so the
            # warning names the variable the operator must actually set.
            + (f". This image manages it as {r['envVar']} — set that in the "
               "game container's environment instead." if r.get("envVar") else "")
        )
        for r in results if r["verdict"] in _FAILED
    ]
    unchecked = [r for r in results if r["verdict"] == "unchecked"]
    notes = []
    if unchecked:
        notes.append(
            f"{len(unchecked)} setting(s) could not be checked — "
            "PalWorldSettings.ini was unreadable when the server came back."
        )
    if any(r["secret"] for r in results):
        notes.append(
            "Password settings are checked but never displayed: the dashboard "
            "stores a hash of what it wrote, not the value."
        )
    return {
        "checked": len(results),
        "verified": sum(1 for r in results if r["verdict"] == "verified"),
        "keys": results,
        "warnings": warnings,
        "notes": notes,
    }


def describe() -> dict[str, Any]:
    """
    What is known, for the Settings tab.

    `unknown` is a real answer and the honest starting state: it means the
    dashboard has not yet written the INI and seen a restart, not that the file
    is safe.
    """
    row = _row()
    verdict = row["verdict"] or "unknown"
    init()
    conn = db.connect()
    last = [
        dict(r) for r in
        conn.execute("SELECT * FROM ini_key_verdicts ORDER BY key").fetchall()
    ]
    pending = [
        r["key"] for r in
        conn.execute("SELECT key FROM ini_pending_keys ORDER BY key").fetchall()
    ]
    return {
        "verdict": verdict,
        "detail": row["detail"] or (
            "Not yet known. Save a setting here, restart the server, and the "
            "dashboard will report whether your change survived."
        ),
        "observedAt": row["observed_at"] or None,
        "awaitingRestart": bool(row["written_by_us"]),
        "lastWriteAt": row["written_at"] or None,
        # The keys written and not yet checked, by name. Without this a queued
        # verification is indistinguishable from none, and the UI cannot say
        # "restart to confirm these three".
        "pendingKeys": pending,
        "keyVerification": _key_summary([
            {
                "key": r["key"], "verdict": r["verdict"], "secret": bool(r["secret"]),
                "expected": r["expected"], "actual": r["actual"],
            }
            for r in last
        ]),
    }
