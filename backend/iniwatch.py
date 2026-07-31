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


def record_our_write(path: Optional[str]) -> None:
    """
    Remember what the file looked like immediately after the dashboard wrote it.

    Called from the settings write path. `written_by_us` is what stops our own
    edit being mistaken for the image's.
    """
    digest = hash_file(path)
    if not digest:
        return
    init()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO ini_watch (id, path, hash, written_by_us, written_at) "
            "VALUES (1, ?, ?, 1, ?) "
            "ON CONFLICT(id) DO UPDATE SET path = excluded.path, hash = excluded.hash, "
            "written_by_us = 1, written_at = excluded.written_at",
            (path or "", digest, _now()),
        )


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

    with db.transaction() as conn:
        conn.execute(
            "UPDATE ini_watch SET verdict = ?, observed_at = ?, detail = ?, "
            "written_by_us = 0, hash = ? WHERE id = 1",
            (verdict, _now(), detail, current),
        )
    logger.info("INI watch verdict: %s", verdict)
    return describe()


def describe() -> dict[str, Any]:
    """
    What is known, for the Settings tab.

    `unknown` is a real answer and the honest starting state: it means the
    dashboard has not yet written the INI and seen a restart, not that the file
    is safe.
    """
    row = _row()
    verdict = row["verdict"] or "unknown"
    return {
        "verdict": verdict,
        "detail": row["detail"] or (
            "Not yet known. Save a setting here, restart the server, and the "
            "dashboard will report whether your change survived."
        ),
        "observedAt": row["observed_at"] or None,
        "awaitingRestart": bool(row["written_by_us"]),
        "lastWriteAt": row["written_at"] or None,
    }
