"""
Self-serve world copy: a player takes their own progress into single-player.

This is `soloexport` with every knob pre-decided, which is the same rule
`slotedit` and `palimport` follow — the risky code stays in one module and a new
feature only parameterises it. Nothing here loads properties, walks a save tree
or writes a file except through `soloexport` and `exportscope`.

The knobs, and why each is fixed rather than offered:

  source   the caller's own linked character, resolved server-side. The client
           sends nothing, so there is no uid to forge — the same direction as
           `_owns_export_subject` on the JSON exports.
  target   the single-player / co-op host uid. That is the one destination every
           self-serve user shares; anything else is the moderators' panel.
  prune    own guild only (`keep_guilds=[]`, the exporting character's guild kept
           by `keep_uid`). And unlike the moderator flow, **a refused prune is a
           refused export**: the moderator flow writes the full copy because an
           operator asked for the whole world and the prune was an extra; here
           the prune IS the permission, so a copy that kept everyone's data must
           never reach the caller. It is deleted and the export refused.

**Solo guilds only.** The prune cannot cut below guild level — a kept guild
keeps its members' player saves, full inventories included, which is more than a
guildmate can see in game. So the export is allowed only when the caller's guild
is just them, checked against the LIVE world at create time (the parse cache
only supplies the UI hint; a stale cache must not decide what leaves the
server). Anyone in a shared guild is pointed at a moderator instead.

**One archive per account, replaced on the next export.** Disk is bounded at
accounts x one archive, and old rows past `SELF_EXPORT_RETENTION_DAYS` are swept
lazily on the next self-export call — no background job to schedule or forget.

**The download route takes no parameters.** The path comes from the caller's own
row, never from input, so there is nothing to traverse and nothing to guess.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import threading
import time
from hashlib import sha256
from typing import Any, Optional

import db
import exportscope
import privacy
import savecache
import soloexport

logger = logging.getLogger(__name__)

# The uid a single-player or co-op-hosting install presents. Corroborated twice
# in the reference archive — see the retraction in soloexport's docstring.
HOST_UID = "00000000-0000-0000-0000-000000000001"

# Captured at import time; tests monkeypatch the module attribute, not the
# environment (AGENTS.md convention).
ENABLED = os.environ.get("SELF_EXPORT_ENABLED", "true").strip().lower() not in (
    "0", "false", "no", "off",
)
MIN_INTERVAL = int(os.environ.get("SELF_EXPORT_MIN_INTERVAL", "3600"))
RETENTION_DAYS = int(os.environ.get("SELF_EXPORT_RETENTION_DAYS", "7"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS self_exports (
    username    TEXT PRIMARY KEY,
    uid         TEXT NOT NULL DEFAULT '',
    path        TEXT NOT NULL DEFAULT '',
    sha256      TEXT NOT NULL DEFAULT '',
    size_bytes  INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL DEFAULT 0
);
"""

# One export at a time, whoever asks. An export decompresses and walks the whole
# world on the machine running the game; two at once is the one load shape the
# per-account cooldown cannot prevent.
_RUNNING = threading.Lock()


class SelfExportError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def init() -> None:
    with db.transaction() as conn:
        conn.executescript(SCHEMA)


# ─── Storage ─────────────────────────────────────────────


def _base_dir() -> str:
    root = soloexport.EXPORT_DIR or os.path.join(
        os.environ.get("BACKUP_DIR", "/tmp"), "exports"
    )
    return os.path.join(root, "self")


def _slug(username: str) -> str:
    """
    A filesystem-safe per-account directory name.

    The readable half is for an operator browsing the backups volume; the hash
    half is what actually guarantees uniqueness, because sanitising can collapse
    two usernames onto one string.
    """
    readable = re.sub(r"[^A-Za-z0-9_-]", "_", username)[:32] or "account"
    return f"{readable}-{sha256(username.encode()).hexdigest()[:10]}"


def _row(username: str) -> Optional[dict[str, Any]]:
    init()
    r = db.connect().execute(
        "SELECT username, uid, path, sha256, size_bytes, created_at "
        "FROM self_exports WHERE username = ?",
        (username,),
    ).fetchone()
    return dict(r) if r else None


def _delete_artifacts(row: dict[str, Any]) -> None:
    """Remove a row's archive, and any unpacked directory a crash left beside it."""
    path = str(row.get("path") or "")
    if path and os.path.isfile(path):
        os.remove(path)
    unpacked = path[: -len(".tar.gz")] if path.endswith(".tar.gz") else ""
    if unpacked and os.path.isdir(unpacked):
        shutil.rmtree(unpacked, ignore_errors=True)


def _sweep_expired() -> None:
    """Lazy retention: run on every self-export call, so no scheduler exists to fail."""
    cutoff = time.time() - RETENTION_DAYS * 86400
    init()
    conn = db.connect()
    rows = conn.execute(
        "SELECT username, path FROM self_exports WHERE created_at < ?", (cutoff,)
    ).fetchall()
    for r in rows:
        try:
            _delete_artifacts(dict(r))
        except OSError as e:  # a vanished file must not wedge the sweep
            logger.warning("Self-export sweep could not remove %s: %s", r["path"], e)
    if rows:
        with db.transaction() as tx:
            tx.execute("DELETE FROM self_exports WHERE created_at < ?", (cutoff,))


# ─── Eligibility ─────────────────────────────────────────


def _cooldown_remaining(row: Optional[dict[str, Any]]) -> int:
    if not row:
        return 0
    elapsed = time.time() - float(row.get("created_at") or 0)
    return max(0, int(MIN_INTERVAL - elapsed))


def _solo_guild_cached(uid: str) -> Optional[bool]:
    """
    The UI hint, from the parse cache: True/False, or None when no parse exists.

    Never used to authorise anything — `create` re-checks against the live world,
    because a member who joined since the last parse must still block the export.
    """
    guilds = savecache.get_section("guilds")
    if not guilds:
        return None
    me = privacy.normalise_uid(uid)
    for guild in guilds:
        members = [
            privacy.normalise_uid(m.get("uid")) for m in (guild.get("members") or [])
        ]
        if me in members:
            return len([m for m in members if m]) == 1
    return None


def _require_solo_guild_live(uid: str) -> None:
    """The authoritative check, against the world as it is on disk right now."""
    try:
        guilds = exportscope.guilds(exportscope.load_world())
    except exportscope.ExportScopeError as e:
        raise SelfExportError(f"World not readable: {e}", status=503)

    me = privacy.normalise_uid(uid)
    for guild in guilds:
        members = {privacy.normalise_uid(u) for u in guild.get("playerUids") or []}
        members.add(privacy.normalise_uid(guild.get("adminUid")))
        members.discard("")
        if me in members:
            if len(members) > 1:
                raise SelfExportError(
                    "Your guild has other members, and this export would include "
                    "their data. Ask a moderator to run the export instead."
                )
            return
    # A character is always in some guild; not finding one means the linked uid
    # and the world disagree, and the safe reading is refusal, not "solo".
    raise SelfExportError(
        "Your linked character was not found in any guild in the current world."
    )


# ─── The feature ─────────────────────────────────────────


def status(username: str, uid: str) -> dict[str, Any]:
    _sweep_expired()
    row = _row(username)
    archive = None
    if row and row.get("path") and os.path.isfile(row["path"]):
        archive = {
            "createdAt": float(row["created_at"]),
            "sizeBytes": int(row["size_bytes"]),
            "sha256": row["sha256"],
        }
    return {
        "enabled": ENABLED,
        "linked": bool(uid),
        "soloGuild": _solo_guild_cached(uid) if uid else None,
        "targetUid": HOST_UID,
        "cooldownSeconds": MIN_INTERVAL,
        "retryInSeconds": _cooldown_remaining(row),
        "archive": archive,
    }


def create(username: str, uid: str) -> dict[str, Any]:
    """
    Run the export and store its archive as this account's one slot.

    Ordering is cheapest-refusal-first: everything that can say no without
    touching the world says it before anything loads 55 MB.
    """
    if not ENABLED:
        raise SelfExportError("Self-serve export is disabled on this server.", 403)
    if not uid:
        raise SelfExportError(
            "This account has no linked character. Link it from the Players tab "
            "(or ask an admin) first."
        )

    _sweep_expired()
    row = _row(username)
    remaining = _cooldown_remaining(row)
    if remaining > 0:
        raise SelfExportError(
            f"Your next export is available in {remaining // 60 + 1} minute(s).",
            status=429,
        )

    verdict = savecache.load_verdict()
    if verdict.get("busy"):
        raise SelfExportError(
            f"The game server is under load ({verdict.get('reason', 'busy')}) — "
            "try again in a few minutes.",
            status=503,
        )

    if not _RUNNING.acquire(blocking=False):
        raise SelfExportError("Another export is already running — try again "
                              "shortly.", status=503)
    try:
        _require_solo_guild_live(uid)

        # Timestamped, so the new archive NEVER shares the old one's path — the
        # first version reused one name and the delete-the-old step after a
        # successful export deleted the file it had just written. A distinct
        # path also means a failed export costs nothing: the previous good
        # archive is only removed after the new one exists.
        dest = os.path.join(
            _base_dir(), _slug(username), f"world-copy-{int(time.time())}"
        )
        if os.path.isdir(dest):
            shutil.rmtree(dest, ignore_errors=True)
        os.makedirs(os.path.dirname(dest), exist_ok=True)

        result = soloexport.apply_export(
            uid, HOST_UID, destination=dest, keep_guilds=[]
        )

        # The moderator flow writes the full copy when a prune is refused,
        # because there the prune was optional. Here it is the permission.
        if not result.get("prune", {}).get("pruned"):
            shutil.rmtree(dest, ignore_errors=True)
            reason = result.get("prune", {}).get("refused") or "prune did not run"
            raise SelfExportError(
                f"The export could not be scoped to your guild ({reason}), so no "
                "copy was produced.",
                status=503,
            )

        archive = soloexport.archive_export(dest)
        # Keep only the .tar.gz — the unpacked directory doubles the disk for
        # nothing the download route can serve.
        shutil.rmtree(dest, ignore_errors=True)

        # The old archive goes only now that the new one verifiably exists.
        if row and row.get("path") != archive["path"]:
            _delete_artifacts(row)
        init()
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO self_exports "
                "(username, uid, path, sha256, size_bytes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(username) DO UPDATE SET uid = excluded.uid, "
                "path = excluded.path, sha256 = excluded.sha256, "
                "size_bytes = excluded.size_bytes, created_at = excluded.created_at",
                (username, uid, archive["path"], archive["sha256"],
                 archive["sizeBytes"], time.time()),
            )

        return {
            "ok": True,
            "mode": result["mode"],
            "referencesRemapped": result["applied"]["total"],
            "prune": {
                "guildsRemoved": len(result["prune"].get("dropGuildIds") or []),
            },
            "archive": {
                "sizeBytes": archive["sizeBytes"],
                "sha256": archive["sha256"],
            },
            "status": status(username, uid),
        }
    except soloexport.SoloExportError as e:
        raise SelfExportError(str(e))
    finally:
        _RUNNING.release()


def archive_for_download(username: str) -> dict[str, Any]:
    """The caller's own archive, or a refusal. No parameters by design."""
    row = _row(username)
    if not row or not row.get("path") or not os.path.isfile(row["path"]):
        raise SelfExportError("No export archive exists for this account — "
                              "create one first.", status=404)
    return {
        "path": row["path"],
        "sha256": row["sha256"],
        "createdAt": float(row["created_at"]),
    }
