"""
Scheduled backups, and the tick everything else scheduled hangs off.

A single background thread that wakes once a minute, decides whether a backup is
due, and takes one. Deliberately not APScheduler or cron: one thread and a
persisted "last run" timestamp is the whole requirement, and adding a dependency
plus a second process to a container that must stay light is not.

Recurring announcements (`announcements.py`) run on the same tick for the same
reason — a second thread waking at the same interval to check a different table
buys nothing. Each job is wrapped separately so neither can take the other down.

Two things this must not do:

  * Pile up. If a backup is slow or the process was asleep, a missed window is
    skipped rather than replayed — nobody wants six catch-up backups at once.
  * Interfere with the game. Backups only read save files, but the parse worker
    and a scheduled backup running together are still avoidable load, so the
    thread checks the server's own load signal before firing.

Retention runs after each scheduled backup, otherwise a daily schedule quietly
fills the disk.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import announcements
import audit
import backup as backup_module
import gameversion
import db

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 60

FREQUENCIES = {
    "hourly": timedelta(hours=1),
    "every6h": timedelta(hours=6),
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
}

DEFAULT_SCHEDULE = {
    "enabled": False,
    "frequency": "daily",
    "pruneAfter": True,
    "lastRun": None,
    "lastResult": None,
}

_thread: Optional[threading.Thread] = None
_stop = threading.Event()


SCHEMA = """
CREATE TABLE IF NOT EXISTS backup_schedule (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    enabled     INTEGER NOT NULL DEFAULT 0,
    frequency   TEXT    NOT NULL DEFAULT 'daily',
    prune_after INTEGER NOT NULL DEFAULT 1,
    last_run    TEXT,
    last_result TEXT
);
"""


def init() -> None:
    with db.transaction() as conn:
        conn.executescript(SCHEMA)
        conn.execute("INSERT OR IGNORE INTO backup_schedule (id) VALUES (1)")


def get_schedule() -> dict[str, Any]:
    init()
    row = db.connect().execute("SELECT * FROM backup_schedule WHERE id = 1").fetchone()
    if row is None:
        return dict(DEFAULT_SCHEDULE)
    return {
        "enabled": bool(row["enabled"]),
        "frequency": row["frequency"],
        "pruneAfter": bool(row["prune_after"]),
        "lastRun": row["last_run"],
        "lastResult": row["last_result"],
        "nextRun": _next_run(row["last_run"], row["frequency"]) if row["enabled"] else None,
        "frequencies": sorted(FREQUENCIES),
    }


def set_schedule(
    *,
    enabled: Optional[bool] = None,
    frequency: Optional[str] = None,
    prune_after: Optional[bool] = None,
) -> dict[str, Any]:
    init()
    if frequency is not None and frequency not in FREQUENCIES:
        raise ValueError(f"Unknown frequency: {frequency}")

    fields, values = [], []
    if enabled is not None:
        fields.append("enabled = ?"); values.append(1 if enabled else 0)
    if frequency is not None:
        fields.append("frequency = ?"); values.append(frequency)
    if prune_after is not None:
        fields.append("prune_after = ?"); values.append(1 if prune_after else 0)

    if fields:
        with db.transaction() as conn:
            conn.execute(f"UPDATE backup_schedule SET {', '.join(fields)} WHERE id = 1", values)

    return get_schedule()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _next_run(last_run: Optional[str], frequency: str) -> Optional[str]:
    interval = FREQUENCIES.get(frequency)
    if interval is None:
        return None
    previous = _parse(last_run)
    if previous is None:
        return _now().isoformat()
    return (previous + interval).isoformat()


def is_due(now: Optional[datetime] = None) -> bool:
    schedule = get_schedule()
    if not schedule["enabled"]:
        return False

    interval = FREQUENCIES.get(schedule["frequency"])
    if interval is None:
        return False

    previous = _parse(schedule["lastRun"])
    if previous is None:
        return True  # never run: take one now so the first schedule is honoured

    return (now or _now()) - previous >= interval


def _record_run(result: str) -> None:
    with db.transaction() as conn:
        conn.execute(
            "UPDATE backup_schedule SET last_run = ?, last_result = ? WHERE id = 1",
            (_now().isoformat(), result[:200]),
        )


def run_scheduled_backup() -> dict[str, Any]:
    """
    Take one scheduled backup, then apply retention.

    Records the outcome either way — a schedule that has been silently failing
    for a week is worse than no schedule, so a failure is visible in both the
    schedule state and the audit log.
    """
    schedule = get_schedule()
    try:
        meta = backup_module.create_backup(
            description="Scheduled backup",
            trigger=f"schedule:{schedule['frequency']}",
            created_by="scheduler",
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Scheduled backup failed: %s", e)
        _record_run(f"failed: {e}")
        audit.record(
            audit.BACKUP_CREATE, username="scheduler", role="system",
            detail=str(e), result=audit.RESULT_FAILED,
        )
        return {"ok": False, "error": str(e)}

    _record_run(f"ok: {meta['id']}")
    audit.record(
        audit.BACKUP_CREATE, username="scheduler", role="system",
        target=meta["id"],
        detail={"trigger": meta["trigger"], "sizeBytes": meta["sizeBytes"]},
    )

    pruned = None
    if schedule["pruneAfter"]:
        try:
            pruned = backup_module.prune_backups(dry_run=False)
            if pruned["removed"]:
                audit.record(
                    audit.BACKUP_DELETE, username="scheduler", role="system",
                    target="retention",
                    detail={"removed": [r["id"] for r in pruned["removed"]],
                            "freedBytes": pruned["freedBytes"]},
                )
        except Exception as e:  # noqa: BLE001
            logger.error("Retention after scheduled backup failed: %s", e)

    return {"ok": True, "backup": meta, "pruned": pruned}


def _loop() -> None:
    logger.info("Scheduler started")
    while not _stop.wait(CHECK_INTERVAL_SECONDS):
        # Three independent jobs on one tick, each wrapped separately so a failure
        # in one cannot silently disable the others — one try block around all of
        # them is how a broken announcement stops backups.
        try:
            if is_due():
                run_scheduled_backup()
        except Exception as e:  # noqa: BLE001 - the thread must never die
            logger.exception("Backup scheduler tick failed: %s", e)
        try:
            announcements.run_due()
        except Exception as e:  # noqa: BLE001
            logger.exception("Announcement scheduler tick failed: %s", e)
        try:
            # Self-rate-limited to every few hours, so calling it on a per-minute
            # tick costs a monotonic clock read. A Palworld update lands roughly
            # monthly and the check itself is ~0.05 ms — the interval is set by how
            # often it is *worth* looking, not by what it costs.
            #
            # It only ever *notices*. Re-extracting 51,921 positions from 9,977 cell
            # packages is minutes of work beside a live server, so that stays an
            # operator decision.
            gameversion.poll()
        except Exception as e:  # noqa: BLE001
            logger.exception("Game build check failed: %s", e)
    logger.info("Scheduler stopped")


def start() -> None:
    """Start the background thread. Idempotent."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    init()
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="backup-scheduler", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
