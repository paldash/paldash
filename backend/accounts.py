"""
User accounts, sessions and login throttling.

PASSWORD HASHING
----------------
scrypt, from the standard library. Argon2id would be the textbook choice, but it
means shipping `argon2-cffi` into a container that already compiles a C++ Oodle
extension, and scrypt is memory-hard, well-analysed and in `hashlib` with zero
dependencies. Parameters below cost ~64 MB and ~100 ms per verification, which is
brutal for an attacker and unnoticeable for a LAN login.

Stored as `scrypt$n$r$p$salt$hash`, so the parameters travel with the hash and can
be raised later without invalidating existing passwords.

SESSIONS
--------
Opaque 256-bit random tokens, stored **hashed**. The previous design was a
stateless signed cookie, which cannot be revoked: logging out only cleared the
browser's copy, and the token stayed valid for its full 12 hours. Server-side
sessions make logout, "sign out everywhere", and disabling an account take effect
immediately.

RATE LIMITING
-------------
Per-IP and per-username, with exponential backoff, recorded in the database so a
restart does not reset an attacker's budget. This is the fix for what was the
single worst finding in the audit: one shared password with unlimited guesses.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import db
import roles

logger = logging.getLogger(__name__)

# ~64 MB, ~100 ms. n must be a power of two.
SCRYPT_N = 2 ** 16
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32

SESSION_TTL_HOURS = int(os.environ.get("SESSION_TTL_HOURS", "12"))
MIN_PASSWORD_LENGTH = int(os.environ.get("MIN_PASSWORD_LENGTH", "10"))

# Throttling. Attempts are counted in a rolling window; once over the threshold
# the lockout grows exponentially, capped so a user is never locked out forever.
ATTEMPT_WINDOW_MINUTES = 15
MAX_ATTEMPTS_PER_IP = 10
MAX_ATTEMPTS_PER_USER = 5
MAX_LOCKOUT_SECONDS = 900


class AccountError(Exception):
    """Invalid account operation — safe to show the user."""


class RateLimited(Exception):
    def __init__(self, retry_after: int):
        super().__init__(
            f"Too many failed sign-in attempts. Try again in {retry_after} seconds."
        )
        self.retry_after = retry_after


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat()


# ─── Password hashing ────────────────────────────────────────────


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_DKLEN,
        maxmem=128 * SCRYPT_N * SCRYPT_R * 2,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verification. Never raises on a malformed hash."""
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        n, r, p = int(n), int(r), int(p)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, AttributeError):
        logger.warning("Malformed password hash encountered")
        return False

    candidate = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt, n=n, r=r, p=p, dklen=len(expected),
        maxmem=128 * n * r * 2,
    )
    return hmac.compare_digest(candidate, expected)


def validate_password(password: str) -> None:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise AccountError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        )


# ─── Users ───────────────────────────────────────────────────────


def _row_to_user(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "username": row["username"],
        "role": row["role"],
        "steamUid": row["steam_uid"] or "",
        "displayName": row["display_name"] or row["username"],
        "disabled": bool(row["disabled"]),
        "mustChangePassword": bool(row["must_change_password"]),
        "createdAt": row["created_at"],
        "lastLogin": row["last_login"],
    }


def user_count() -> int:
    return db.connect().execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]


def list_users() -> list[dict[str, Any]]:
    rows = db.connect().execute(
        "SELECT * FROM users ORDER BY username COLLATE NOCASE"
    ).fetchall()
    return [_row_to_user(r) for r in rows]


def get_user(username: str) -> Optional[dict[str, Any]]:
    row = db.connect().execute(
        "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
    ).fetchone()
    return _row_to_user(row) if row else None


def create_user(
    username: str,
    password: str,
    role: str = roles.DEFAULT_ROLE,
    steam_uid: str = "",
    display_name: str = "",
    must_change_password: bool = False,
) -> dict[str, Any]:
    username = (username or "").strip()
    if not username:
        raise AccountError("Username is required.")
    if len(username) > 64 or not all(c.isalnum() or c in "-_." for c in username):
        raise AccountError(
            "Usernames may contain letters, numbers, dot, dash and underscore only."
        )
    if role not in roles.ASSIGNABLE_ROLES:
        raise AccountError(f"Unknown role: {role}")
    validate_password(password)

    if get_user(username):
        raise AccountError(f"A user named {username!r} already exists.")

    with db.transaction() as conn:
        cursor = conn.execute(
            """INSERT INTO users
                 (username, password_hash, role, steam_uid, display_name,
                  must_change_password, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                username, hash_password(password), role,
                (steam_uid or "").replace("-", "").lower() or None,
                display_name or None,
                1 if must_change_password else 0,
                _iso(_now()),
            ),
        )
        user_id = cursor.lastrowid

    logger.info("Created user %s with role %s", username, role)
    row = db.connect().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _row_to_user(row)


def update_user(
    username: str,
    *,
    role: Optional[str] = None,
    steam_uid: Optional[str] = None,
    display_name: Optional[str] = None,
    disabled: Optional[bool] = None,
) -> dict[str, Any]:
    user = get_user(username)
    if not user:
        raise AccountError(f"No such user: {username}")

    if role is not None and role not in roles.ASSIGNABLE_ROLES:
        raise AccountError(f"Unknown role: {role}")

    # Never let the last owner be demoted or disabled — that would lock everyone
    # out of account management with no way back in.
    losing_owner = user["role"] == "owner" and (
        (role is not None and role != "owner") or disabled is True
    )
    if losing_owner and _owner_count() <= 1:
        raise AccountError(
            "This is the only Owner account. Promote another user to Owner first."
        )

    fields, values = [], []
    if role is not None:
        fields.append("role = ?"); values.append(role)
    if steam_uid is not None:
        fields.append("steam_uid = ?")
        values.append((steam_uid or "").replace("-", "").lower() or None)
    if display_name is not None:
        fields.append("display_name = ?"); values.append(display_name or None)
    if disabled is not None:
        fields.append("disabled = ?"); values.append(1 if disabled else 0)

    if fields:
        values.append(user["id"])
        with db.transaction() as conn:
            conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", values)

    # A disabled or demoted account should not keep working until its cookie
    # happens to expire.
    if disabled is True or role is not None:
        revoke_all_sessions(user["id"])

    return get_user(username)  # type: ignore[return-value]


def set_password(username: str, password: str, revoke_sessions: bool = True) -> None:
    user = get_user(username)
    if not user:
        raise AccountError(f"No such user: {username}")
    validate_password(password)

    with db.transaction() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
            (hash_password(password), user["id"]),
        )

    if revoke_sessions:
        revoke_all_sessions(user["id"])


def delete_user(username: str) -> None:
    user = get_user(username)
    if not user:
        raise AccountError(f"No such user: {username}")
    if user["role"] == "owner" and _owner_count() <= 1:
        raise AccountError("Cannot delete the only Owner account.")

    with db.transaction() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user["id"],))


def _owner_count() -> int:
    return db.connect().execute(
        "SELECT COUNT(*) AS n FROM users WHERE role = 'owner' AND disabled = 0"
    ).fetchone()["n"]


# ─── Bootstrap ───────────────────────────────────────────────────


def bootstrap_from_env() -> Optional[str]:
    """
    Create the first Owner from PANEL_PASSWORD when no users exist.

    This is the upgrade path from the old single-shared-password model: an
    existing deployment keeps working, with the same password, and the operator
    can add real accounts afterwards. Does nothing once any user exists, so it
    can never resurrect a deleted account or reset a changed password.
    """
    if user_count() > 0:
        return None

    password = (os.environ.get("PANEL_PASSWORD") or "").strip()
    if not password:
        logger.warning(
            "No users exist and PANEL_PASSWORD is unset — nobody can sign in. "
            "Set PANEL_PASSWORD and restart to create the first Owner account."
        )
        return None

    username = (os.environ.get("PANEL_ADMIN_USER") or "admin").strip() or "admin"
    try:
        validate_password(password)
    except AccountError:
        logger.error(
            "PANEL_PASSWORD is shorter than %d characters; refusing to create the "
            "first account with it.", MIN_PASSWORD_LENGTH,
        )
        return None

    create_user(username, password, role="owner", display_name="Server owner")
    logger.info("Bootstrapped Owner account %r from PANEL_PASSWORD", username)
    return username


# ─── Rate limiting ───────────────────────────────────────────────


def _recent_failures(column: str, value: str) -> int:
    if not value:
        return 0
    since = _iso(_now() - timedelta(minutes=ATTEMPT_WINDOW_MINUTES))
    return db.connect().execute(
        f"SELECT COUNT(*) AS n FROM login_attempts "
        f"WHERE {column} = ? AND success = 0 AND ts > ?",
        (value, since),
    ).fetchone()["n"]


def _lockout_seconds(failures: int, threshold: int) -> int:
    if failures < threshold:
        return 0
    over = failures - threshold
    return min(MAX_LOCKOUT_SECONDS, 15 * (2 ** min(over, 8)))


def check_rate_limit(ip: str, username: str) -> None:
    """Raise RateLimited if this IP or username has been guessing."""
    by_ip = _lockout_seconds(_recent_failures("ip", ip or ""), MAX_ATTEMPTS_PER_IP)
    by_user = _lockout_seconds(
        _recent_failures("username", (username or "").lower()), MAX_ATTEMPTS_PER_USER
    )
    delay = max(by_ip, by_user)
    if delay:
        raise RateLimited(delay)


def record_attempt(ip: str, username: str, success: bool) -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO login_attempts (ts, ip, username, success) VALUES (?, ?, ?, ?)",
            (_iso(_now()), ip or "", (username or "").lower(), 1 if success else 0),
        )
        # A successful sign-in clears that user's budget so a legitimate person
        # who mistyped twice is not still throttled.
        if success:
            conn.execute(
                "DELETE FROM login_attempts WHERE username = ? AND success = 0",
                ((username or "").lower(),),
            )


# ─── Sessions ────────────────────────────────────────────────────


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def authenticate(
    username: str, password: str, ip: str = "", user_agent: str = ""
) -> tuple[str, dict[str, Any]]:
    """
    Verify credentials and open a session. Returns (token, user).

    Raises AccountError with a deliberately vague message on any failure: a
    distinct "no such user" would let an attacker enumerate accounts.
    """
    check_rate_limit(ip, username)

    user_row = db.connect().execute(
        "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
    ).fetchone()

    if user_row is None:
        # Spend comparable time on a nonexistent user so response timing does
        # not reveal which usernames are real.
        hash_password(secrets.token_hex(16))
        record_attempt(ip, username, False)
        raise AccountError("Incorrect username or password.")

    if not verify_password(password, user_row["password_hash"]):
        record_attempt(ip, username, False)
        raise AccountError("Incorrect username or password.")

    if user_row["disabled"]:
        record_attempt(ip, username, False)
        raise AccountError("This account has been disabled.")

    record_attempt(ip, username, True)

    token = secrets.token_urlsafe(32)
    now = _now()
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO sessions
                 (token_hash, user_id, created_at, expires_at, last_seen, ip, user_agent)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                _hash_token(token), user_row["id"], _iso(now),
                _iso(now + timedelta(hours=SESSION_TTL_HOURS)),
                _iso(now), ip or "", (user_agent or "")[:200],
            ),
        )
        conn.execute(
            "UPDATE users SET last_login = ? WHERE id = ?", (_iso(now), user_row["id"])
        )

    return token, _row_to_user(user_row)


def resolve_session(token: str) -> Optional[dict[str, Any]]:
    """The user behind a session token, or None if it is invalid or expired."""
    if not token:
        return None

    row = db.connect().execute(
        """SELECT s.token_hash, s.expires_at, u.*
             FROM sessions s JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ?""",
        (_hash_token(token),),
    ).fetchone()

    if row is None:
        return None

    if row["expires_at"] <= _iso(_now()):
        with db.transaction() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (row["token_hash"],))
        return None

    if row["disabled"]:
        return None

    with db.transaction() as conn:
        conn.execute(
            "UPDATE sessions SET last_seen = ? WHERE token_hash = ?",
            (_iso(_now()), row["token_hash"]),
        )

    return _row_to_user(row)


def revoke_session(token: str) -> bool:
    with db.transaction() as conn:
        cursor = conn.execute(
            "DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),)
        )
    return cursor.rowcount > 0


def revoke_all_sessions(user_id: int) -> int:
    with db.transaction() as conn:
        cursor = conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    return cursor.rowcount


def list_sessions(user_id: int) -> list[dict[str, Any]]:
    rows = db.connect().execute(
        "SELECT created_at, expires_at, last_seen, ip, user_agent "
        "FROM sessions WHERE user_id = ? ORDER BY last_seen DESC",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def purge_expired() -> int:
    """Housekeeping: drop expired sessions and stale login attempts."""
    now = _iso(_now())
    cutoff = _iso(_now() - timedelta(days=2))
    with db.transaction() as conn:
        sessions = conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        conn.execute("DELETE FROM login_attempts WHERE ts < ?", (cutoff,))
    return sessions.rowcount
