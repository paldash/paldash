"""
SQLite storage for accounts, sessions, login attempts and the audit log.

WHY A DATABASE NOW
------------------
Everything else in this project is JSON on disk, which is fine for a policy file
one process writes occasionally. It is not fine for user records, sessions that
must be revocable, rate-limit counters that survive a restart, or an append-only
audit log — those need atomicity and indexed lookup.

SQLite specifically: stdlib, one file, no service to run, no extra container.
This is a LAN tool for a handful of users, not a SaaS; Postgres would be a
liability here, not an upgrade.

The Python backend owns this file exclusively. The Next.js layer never opens it
— it asks the backend over loopback. One owner means no cross-process locking
problem and no second database driver to ship.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator

from savefiles import CACHE_DIR

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("DASHBOARD_DB", os.path.join(CACHE_DIR, "dashboard.db"))

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    username            TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    password_hash       TEXT    NOT NULL,
    role                TEXT    NOT NULL,
    -- Links this login to a player in the save, which is what makes
    -- "show me my own bases / palbox / fog of war" possible at all.
    steam_uid           TEXT,
    display_name        TEXT,
    disabled            INTEGER NOT NULL DEFAULT 0,
    -- Per-player map privacy, set by the player about themselves. Defaults to
    -- the most private option so nobody is exposed before they know the setting
    -- exists. See privacy.py.
    map_privacy         TEXT    NOT NULL DEFAULT 'guild',
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT    NOT NULL,
    last_login          TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    -- The cookie holds a random token; only its hash is stored, so a stolen
    -- database does not hand over live sessions.
    token_hash  TEXT    PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TEXT    NOT NULL,
    expires_at  TEXT    NOT NULL,
    last_seen   TEXT    NOT NULL,
    ip          TEXT,
    user_agent  TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS login_attempts (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT    NOT NULL,
    ip       TEXT,
    username TEXT,
    success  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attempts_ip ON login_attempts(ip, ts);
CREATE INDEX IF NOT EXISTS idx_attempts_user ON login_attempts(username, ts);

-- Append-only. Nothing in the application ever updates or deletes a row here;
-- see audit.py for the pruning policy, which is time-based and logged itself.
CREATE TABLE IF NOT EXISTS audit_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT    NOT NULL,
    username TEXT,
    role     TEXT,
    action   TEXT    NOT NULL,
    target   TEXT,
    detail   TEXT,
    ip       TEXT,
    result   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);

-- Time series for the server dashboard. One row per sample, stored raw with no
-- rollup: at the default 60s interval a 30-day window is ~43,000 rows, which
-- SQLite answers instantly and which is far cheaper than maintaining downsampled
-- tables that can disagree with the raw ones.
--
-- `ts` is epoch seconds rather than the ISO text the older tables use. Charts
-- filter and bucket on it arithmetically, and doing that on text means either
-- parsing every row or comparing strings and hoping.
CREATE TABLE IF NOT EXISTS metrics (
    ts            INTEGER PRIMARY KEY,
    -- From the game's own /v1/api/metrics. NULL when it was unreachable, which
    -- is itself information: a gap in the chart is a period the server was down.
    server_fps    REAL,
    frame_time    REAL,
    players       INTEGER,
    max_players   INTEGER,
    uptime        INTEGER,
    -- Host-side, measured by this container.
    cpu_percent   REAL,
    mem_used_mb   REAL,
    mem_total_mb  REAL,
    disk_used_mb  REAL,
    disk_free_mb  REAL,
    -- From the last completed parse, so it moves in steps rather than smoothly.
    world_size_mb REAL,
    pal_count     INTEGER,
    base_count    INTEGER,
    -- Whether the game answered at all. Kept explicit so "0 players" and
    -- "we could not ask" are never confused.
    reachable     INTEGER NOT NULL DEFAULT 0,
    -- Added 2026-08-06. All nullable and all NULL when unreadable, never 0:
    -- `cpu_temp_c` at 0 reads as a machine at freezing point, and `cpu_steal`
    -- at 0 is a real and different answer from "we could not measure it".
    swap_used_mb  REAL,
    swap_total_mb REAL,
    cpu_steal     REAL,
    net_rx_kbs    REAL,
    net_tx_kbs    REAL,
    cpu_temp_c    REAL,
    -- The GAME process's resident memory. NULL whenever the dashboard cannot
    -- see it, which is the normal container deployment — and must never be 0,
    -- which would read as a server using no memory at all.
    game_mem_mb   REAL
);
CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics(ts);
"""


def _configure(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    # WAL keeps a long-running read (the audit view) from blocking a write.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")


def connect() -> sqlite3.Connection:
    """One connection per thread — sqlite3 objects are not thread-safe."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        return conn

    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    _configure(conn)
    _local.conn = conn
    return conn


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Commit on success, roll back on any exception."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


# Columns added after the first release. SQLite has no "ADD COLUMN IF NOT
# EXISTS", and re-running a plain ALTER raises, so each is applied only when
# absent. Keeping this as data rather than a migration framework is deliberate:
# the whole schema is one file and a handful of columns.
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("users", "map_privacy", "TEXT NOT NULL DEFAULT 'guild'"),
    # Host signals added 2026-08-06. No default: an existing row predates the
    # measurement and NULL is the truthful value for it — backfilling 0 would
    # draw a flat line through history that never happened.
    ("metrics", "swap_used_mb", "REAL"),
    ("metrics", "swap_total_mb", "REAL"),
    ("metrics", "cpu_steal", "REAL"),
    ("metrics", "net_rx_kbs", "REAL"),
    ("metrics", "net_tx_kbs", "REAL"),
    ("metrics", "cpu_temp_c", "REAL"),
    ("metrics", "game_mem_mb", "REAL"),
)


def _apply_column_migrations(conn: sqlite3.Connection) -> None:
    for table, column, definition in _ADDED_COLUMNS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            logger.info("Added column %s.%s", table, column)


def init() -> None:
    """Create the schema. Safe to call repeatedly."""
    with transaction() as conn:
        conn.executescript(SCHEMA)
        _apply_column_migrations(conn)
    logger.info("Database ready at %s", DB_PATH)


def reset_for_tests() -> None:
    """Drop the cached connection so a test can point DB_PATH somewhere else."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            pass
    _local.conn = None
