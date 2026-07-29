"""
Append-only audit log.

Every action that changes something — a save write, a settings edit, a restore, a
container stop, an account change, a policy change — lands here with who did it,
when, from where, and whether it worked.

The motivating case: `sort_containers` rewrites Level.sav. Before this, nothing
recorded that it had happened, who asked for it, or which backup it produced. If
somebody's chests came out wrong there was no way to reconstruct events.

Failures are logged as loudly as successes. A rejected save edit or a throttled
login is exactly what you want to see when investigating.

Nothing here updates or deletes an individual row. `prune` exists so the table
cannot grow without bound, is time-based only, and logs its own execution.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import db

logger = logging.getLogger(__name__)

RETENTION_DAYS = int(os.environ.get("AUDIT_RETENTION_DAYS", "180"))

# Actions, grouped by what they touch. Kept as constants so a typo produces an
# import error rather than a silently unsearchable log entry.
LOGIN = "auth.login"
LOGIN_FAILED = "auth.login.failed"
LOGOUT = "auth.logout"
RATE_LIMITED = "auth.rate_limited"

USER_CREATE = "user.create"
USER_UPDATE = "user.update"
USER_DELETE = "user.delete"
USER_PASSWORD = "user.password"

POLICY_UPDATE = "policy.update"
SETTINGS_WRITE = "settings.write"
SETTINGS_PRESET = "settings.preset"

BACKUP_CREATE = "backup.create"
BACKUP_RESTORE = "backup.restore"
BACKUP_DELETE = "backup.delete"

SAVE_SORT = "save.sort"
SAVE_EDIT = "save.edit"
# An export is the whole inventory, plus real Steam IDs, in one downloadable
# file. Auditing it matters as much as auditing a write.
EXPORT = "save.export"
SAVE_IMPORT = "save.import"

SERVER_RESTART = "server.restart"
SERVER_STOP = "server.stop"
SERVER_START = "server.start"

DENIED = "access.denied"

RESULT_OK = "ok"
RESULT_FAILED = "failed"
RESULT_DENIED = "denied"


def record(
    action: str,
    *,
    username: Optional[str] = None,
    role: Optional[str] = None,
    target: Optional[str] = None,
    detail: Any = None,
    ip: Optional[str] = None,
    result: str = RESULT_OK,
) -> None:
    """
    Write one entry. Never raises.

    An audit write must not be able to break the operation it is describing, so
    a failure here is logged to stderr and swallowed.
    """
    try:
        if detail is not None and not isinstance(detail, str):
            detail = json.dumps(detail, default=str)[:2000]

        with db.transaction() as conn:
            conn.execute(
                """INSERT INTO audit_log (ts, username, role, action, target, detail, ip, result)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now(timezone.utc).isoformat(),
                    username, role, action, target, detail, ip, result,
                ),
            )
    except Exception as e:  # noqa: BLE001 - auditing must never break the action
        logger.error("Audit write failed for %s: %s", action, e)


def query(
    *,
    limit: int = 200,
    offset: int = 0,
    action: Optional[str] = None,
    username: Optional[str] = None,
    result: Optional[str] = None,
    since: Optional[str] = None,
) -> dict[str, Any]:
    """Most recent first, with optional filters."""
    where, params = [], []
    if action:
        # Prefix match, so `save` finds `save.sort` and `save.edit`.
        where.append("action LIKE ?"); params.append(f"{action}%")
    if username:
        where.append("username = ? COLLATE NOCASE"); params.append(username)
    if result:
        where.append("result = ?"); params.append(result)
    if since:
        where.append("ts >= ?"); params.append(since)

    clause = f"WHERE {' AND '.join(where)}" if where else ""
    conn = db.connect()

    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM audit_log {clause}", params
    ).fetchone()["n"]

    rows = conn.execute(
        f"SELECT * FROM audit_log {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
        [*params, max(1, min(limit, 1000)), max(0, offset)],
    ).fetchall()

    return {
        "entries": [dict(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "retentionDays": RETENTION_DAYS,
    }


def actions_seen() -> list[str]:
    """Distinct action names present, for populating a filter dropdown."""
    rows = db.connect().execute(
        "SELECT DISTINCT action FROM audit_log ORDER BY action"
    ).fetchall()
    return [r["action"] for r in rows]


def prune() -> int:
    """
    Drop entries older than the retention window.

    Time-based only — there is deliberately no way to delete a specific entry,
    and the pruning itself is audited.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
    with db.transaction() as conn:
        cursor = conn.execute("DELETE FROM audit_log WHERE ts < ?", (cutoff,))
        removed = cursor.rowcount

    if removed:
        record(
            "audit.prune",
            detail={"removed": removed, "retentionDays": RETENTION_DAYS},
        )
    return removed
