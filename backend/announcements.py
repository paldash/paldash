"""
Recurring in-game announcements.

Rules reminders, "restart at 4am", a Discord link — messages an operator wants
broadcast on a cadence rather than by hand.

Three things this reuses rather than reimplements:

  * **The timer.** `schedule.py` already wakes once a minute for backups, and
    `schedule._loop` calls `run_due()` on the same tick. A second thread ticking
    at the same rate buys nothing.
  * **The send path.** Every announcement goes through `moderate.announce`, so a
    scheduled broadcast is audited by exactly the same code as a manual one —
    including on failure. Nothing here talks to `gameapi` directly.
  * **The message rules.** `moderate.clean_message` collapses the characters that
    would truncate the command mid-send, and it does it at *write* time as well
    as at send time, so what the UI shows back is what will actually go out.

Two behaviours worth being explicit about, because both are the opposite of what
a naive implementation does:

**A missed window is skipped, never replayed.** Same reasoning as scheduled
backups: nobody wants the six announcements whose windows passed while the
dashboard was restarting to arrive in one burst.

**An empty server still consumes the window.** A message nobody can read is not
worth sending, so `run_due` skips when the player count is zero — but it stamps
`last_run` anyway. Not stamping it would queue the announcement against the first
player to join, so logging in would greet you with every overdue message at once,
which is the pile-up above wearing a different hat. The skip is recorded in
`last_result` so it is visible rather than mysterious.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import audit
import db
import gameapi
import moderate

logger = logging.getLogger(__name__)

# Deliberately finer-grained than the backup frequencies. A backup every 15
# minutes is absurd; a rules reminder every 15 minutes is a normal ask.
INTERVALS: dict[str, timedelta] = {
    "every15m": timedelta(minutes=15),
    "every30m": timedelta(minutes=30),
    "hourly": timedelta(hours=1),
    "every3h": timedelta(hours=3),
    "every6h": timedelta(hours=6),
    "daily": timedelta(days=1),
}

MAX_ANNOUNCEMENTS = 20

SCHEMA = """
CREATE TABLE IF NOT EXISTS announcements (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    message        TEXT    NOT NULL,
    interval_key   TEXT    NOT NULL DEFAULT 'hourly',
    enabled        INTEGER NOT NULL DEFAULT 1,
    only_when_online INTEGER NOT NULL DEFAULT 1,
    last_run       TEXT,
    last_result    TEXT,
    created_by     TEXT    NOT NULL DEFAULT '',
    created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""


def init() -> None:
    with db.transaction() as conn:
        conn.executescript(SCHEMA)


def describe_intervals() -> list[dict[str, Any]]:
    """Ordered shortest-first so a UI can present them as a list."""
    return [
        {"id": key, "seconds": int(delta.total_seconds()), "label": _interval_label(key)}
        for key, delta in sorted(INTERVALS.items(), key=lambda kv: kv[1])
    ]


def _interval_label(key: str) -> str:
    return {
        "every15m": "Every 15 minutes",
        "every30m": "Every 30 minutes",
        "hourly": "Hourly",
        "every3h": "Every 3 hours",
        "every6h": "Every 6 hours",
        "daily": "Daily",
    }.get(key, key)


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


def _row(row) -> dict[str, Any]:
    interval = INTERVALS.get(row["interval_key"])
    last = _parse(row["last_run"])
    return {
        "id": row["id"],
        "message": row["message"],
        "interval": row["interval_key"],
        "intervalLabel": _interval_label(row["interval_key"]),
        "enabled": bool(row["enabled"]),
        "onlyWhenOnline": bool(row["only_when_online"]),
        "lastRun": row["last_run"],
        "lastResult": row["last_result"],
        # A never-run announcement is due immediately, which is why nextRun is
        # "now" rather than null — otherwise the UI cannot say when it will fire.
        "nextRun": (
            None if not row["enabled"] or interval is None
            else ((last + interval).isoformat() if last else _now().isoformat())
        ),
        "createdBy": row["created_by"],
        "createdAt": row["created_at"],
    }


def list_announcements() -> list[dict[str, Any]]:
    init()
    rows = db.connect().execute(
        "SELECT * FROM announcements ORDER BY id"
    ).fetchall()
    return [_row(r) for r in rows]


def get(announcement_id: int) -> Optional[dict[str, Any]]:
    init()
    row = db.connect().execute(
        "SELECT * FROM announcements WHERE id = ?", (int(announcement_id),)
    ).fetchone()
    return _row(row) if row else None


def create(
    message: str,
    interval: str = "hourly",
    *,
    enabled: bool = True,
    only_when_online: bool = True,
    created_by: str = "",
) -> dict[str, Any]:
    init()
    if interval not in INTERVALS:
        raise ValueError(f"Unknown interval: {interval}")
    # Cleaned on the way in as well as on the way out, so the stored text is
    # exactly what will be broadcast and the UI is not showing something the
    # send path would quietly rewrite.
    text = moderate.clean_message(message, required=True)

    count = db.connect().execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
    if count >= MAX_ANNOUNCEMENTS:
        raise ValueError(
            f"At most {MAX_ANNOUNCEMENTS} scheduled announcements. Delete one first."
        )

    with db.transaction() as conn:
        cursor = conn.execute(
            "INSERT INTO announcements (message, interval_key, enabled, "
            "only_when_online, created_by) VALUES (?, ?, ?, ?, ?)",
            (text, interval, 1 if enabled else 0,
             1 if only_when_online else 0, created_by),
        )
        new_id = int(cursor.lastrowid or 0)

    result = get(new_id)
    assert result is not None
    return result


def update(
    announcement_id: int,
    *,
    message: Optional[str] = None,
    interval: Optional[str] = None,
    enabled: Optional[bool] = None,
    only_when_online: Optional[bool] = None,
) -> dict[str, Any]:
    init()
    if get(announcement_id) is None:
        raise ValueError(f"No such announcement: {announcement_id}")
    if interval is not None and interval not in INTERVALS:
        raise ValueError(f"Unknown interval: {interval}")

    fields: list[str] = []
    values: list[Any] = []
    if message is not None:
        fields.append("message = ?")
        values.append(moderate.clean_message(message, required=True))
    if interval is not None:
        fields.append("interval_key = ?"); values.append(interval)
    if enabled is not None:
        fields.append("enabled = ?"); values.append(1 if enabled else 0)
    if only_when_online is not None:
        fields.append("only_when_online = ?"); values.append(1 if only_when_online else 0)

    if fields:
        values.append(int(announcement_id))
        with db.transaction() as conn:
            conn.execute(
                f"UPDATE announcements SET {', '.join(fields)} WHERE id = ?", values
            )

    result = get(announcement_id)
    assert result is not None
    return result


def delete(announcement_id: int) -> None:
    init()
    if get(announcement_id) is None:
        raise ValueError(f"No such announcement: {announcement_id}")
    with db.transaction() as conn:
        conn.execute("DELETE FROM announcements WHERE id = ?", (int(announcement_id),))


def _stamp(announcement_id: int, result: str) -> None:
    with db.transaction() as conn:
        conn.execute(
            "UPDATE announcements SET last_run = ?, last_result = ? WHERE id = ?",
            (_now().isoformat(), result[:200], int(announcement_id)),
        )


def due(now: Optional[datetime] = None) -> list[dict[str, Any]]:
    """Enabled announcements whose interval has elapsed. Never-run counts as due."""
    moment = now or _now()
    ready = []
    for entry in list_announcements():
        if not entry["enabled"]:
            continue
        interval = INTERVALS.get(entry["interval"])
        if interval is None:
            continue
        last = _parse(entry["lastRun"])
        if last is None or moment - last >= interval:
            ready.append(entry)
    return ready


def _players_online() -> Optional[int]:
    """
    How many players are on, or None if we could not ask.

    None is *not* zero. An unreachable server means the announcement is skipped
    without claiming nobody was playing, and the recorded reason says which of
    the two happened.
    """
    try:
        return len(gameapi.players())
    except gameapi.GameApiError:
        return None


def run_due(now: Optional[datetime] = None) -> dict[str, Any]:
    """
    Send every announcement whose window has come.

    Called from `schedule._loop`, so it must never raise: a scheduler thread that
    dies takes backups down with it.
    """
    ready = due(now)
    if not ready:
        return {"sent": 0, "skipped": 0, "results": []}

    # Asked once for the whole batch, not once per announcement — three overdue
    # messages should not mean three round trips to the game just to count heads.
    online: Optional[int] = None
    if any(entry["onlyWhenOnline"] for entry in ready):
        online = _players_online()

    actor = {"username": "scheduler", "role": "system"}
    sent = skipped = 0
    results: list[dict[str, Any]] = []

    for entry in ready:
        if entry["onlyWhenOnline"]:
            # None and 0 are different answers and get different reasons: one
            # means nobody was listening, the other that we could not find out.
            reason = "server unreachable" if online is None else (
                "nobody online" if online == 0 else ""
            )
            if reason:
                _stamp(entry["id"], f"skipped: {reason}")
                skipped += 1
                results.append({"id": entry["id"], "sent": False, "reason": reason})
                continue

        try:
            moderate.announce(entry["message"], actor=actor, ip="")
        except Exception as e:  # noqa: BLE001 - the scheduler tick must survive
            logger.warning("Scheduled announcement %s failed: %s", entry["id"], e)
            _stamp(entry["id"], f"failed: {e}")
            results.append({"id": entry["id"], "sent": False, "reason": str(e)})
            continue

        _stamp(entry["id"], "ok")
        sent += 1
        results.append({"id": entry["id"], "sent": True})

    return {"sent": sent, "skipped": skipped, "results": results}


def send_now(announcement_id: int, *, actor: dict, ip: str = "") -> dict[str, Any]:
    """
    Broadcast one immediately, as the person who pressed the button.

    Attributed to them rather than to `scheduler`, because that is who did it —
    and it resets the interval, so "test it" does not mean the next scheduled
    send lands seconds later.
    """
    entry = get(announcement_id)
    if entry is None:
        raise ValueError(f"No such announcement: {announcement_id}")
    result = moderate.announce(entry["message"], actor=actor, ip=ip)
    _stamp(announcement_id, "ok: sent manually")
    return result


def record_change(action_detail: dict, *, actor: dict, ip: str = "") -> None:
    """
    Audit a change to the schedule itself.

    Separate from the broadcast audit records: "who changed what the server says
    every hour" is a different question from "what was said", and both are worth
    being able to answer.
    """
    audit.record(
        audit.SERVER_ANNOUNCE, username=actor.get("username", ""),
        role=actor.get("role", ""), target="schedule", detail=action_detail, ip=ip,
    )
