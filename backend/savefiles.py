"""
Save file location and corruption-safe reading.

Two jobs:

1. Locate the world directory / player saves / PalWorldSettings.ini beneath the
   bind-mounted server directory.
2. Read .sav bytes without ever risking the live file. Reads are O_RDONLY and go
   through a stability check: a live server rewriting Level.sav mid-read yields a
   torn buffer, so we verify (size, mtime) is unchanged across the read and
   retry if it moved. Nothing here ever opens a save file for writing.
"""

from __future__ import annotations

import glob
import logging
import os
import shutil
import tempfile
import time
from typing import Optional

logger = logging.getLogger(__name__)

SAVE_BASE_DIR = os.environ.get("SAVE_BASE_DIR", "/palworld/Pal/Saved/SaveGames/0")
BACKUP_DIR = os.environ.get("BACKUP_DIR", "/palworld/backups")
CACHE_DIR = os.environ.get("CACHE_DIR", "/tmp/palworld-dashboard-cache")

# How many times to re-read a file that changed underneath us.
STABILITY_RETRIES = int(os.environ.get("SAVE_READ_RETRIES", "3"))


# ─── Locating things ─────────────────────────────────────────────


def find_world_dirs() -> list[str]:
    """All world GUID directories containing a Level.sav."""
    if not os.path.isdir(SAVE_BASE_DIR):
        logger.warning("Save base dir not found: %s", SAVE_BASE_DIR)
        return []
    return sorted(
        d
        for d in glob.glob(os.path.join(SAVE_BASE_DIR, "*"))
        if os.path.isdir(d) and os.path.exists(os.path.join(d, "Level.sav"))
    )


def get_default_world_dir() -> Optional[str]:
    """
    The world to operate on. WORLD_GUID pins a specific one; otherwise the
    newest by Level.sav mtime, which is the one the server is actually using.
    """
    dirs = find_world_dirs()
    if not dirs:
        return None

    pinned = os.environ.get("WORLD_GUID", "").strip()
    if pinned:
        for d in dirs:
            if os.path.basename(d) == pinned:
                return d
        logger.warning("WORLD_GUID=%s not found; falling back to newest", pinned)

    return max(dirs, key=lambda d: os.path.getmtime(os.path.join(d, "Level.sav")))


def get_level_sav_path(world_dir: Optional[str] = None) -> Optional[str]:
    world_dir = world_dir or get_default_world_dir()
    if not world_dir:
        return None
    path = os.path.join(world_dir, "Level.sav")
    return path if os.path.exists(path) else None


def list_player_uids(world_dir: Optional[str] = None) -> list[str]:
    """Player UIDs from the Players/ directory."""
    world_dir = world_dir or get_default_world_dir()
    if not world_dir:
        return []
    players_dir = os.path.join(world_dir, "Players")
    if not os.path.isdir(players_dir):
        return []
    return sorted(
        os.path.splitext(f)[0] for f in os.listdir(players_dir) if f.endswith(".sav")
    )


def get_player_sav_path(uid: str, world_dir: Optional[str] = None) -> Optional[str]:
    """
    Path to one player's save. `uid` is sanitised to a bare filename so a
    crafted request cannot escape the Players/ directory.

    Matching is case-insensitive and ignores dashes: Level.sav stores player
    UIDs as lowercase dashed GUIDs ("22b22b02-0000-...") while the files on disk
    are undashed uppercase hex ("22B22B02000...0.sav"). Comparing them directly
    silently found nothing.
    """
    world_dir = world_dir or get_default_world_dir()
    if not world_dir:
        return None

    safe_uid = os.path.basename(uid).replace("..", "")
    if not safe_uid or not all(c.isalnum() or c in "-_" for c in safe_uid):
        logger.warning("Rejected suspicious player uid: %r", uid)
        return None

    players_dir = os.path.join(world_dir, "Players")
    if not os.path.isdir(players_dir):
        return None

    # Exact match first, then a normalised comparison.
    direct = os.path.join(players_dir, f"{safe_uid}.sav")
    if os.path.exists(direct):
        return direct

    wanted = safe_uid.replace("-", "").lower()
    for name in os.listdir(players_dir):
        if not name.endswith(".sav"):
            continue
        stem = os.path.splitext(name)[0]
        if stem.replace("-", "").lower() == wanted:
            return os.path.join(players_dir, name)

    return None


def find_settings_ini() -> Optional[str]:
    """
    Locate PalWorldSettings.ini. Explicit env wins, otherwise walk up from the
    save dir to the `Saved` folder and look under Config/{Linux,Windows}Server.
    """
    explicit = os.environ.get("PALWORLD_CONFIG_INI", "").strip()
    if explicit:
        return explicit if os.path.exists(explicit) else None

    # /palworld/Pal/Saved/SaveGames/0 -> /palworld/Pal/Saved
    saved_dir = SAVE_BASE_DIR
    for _ in range(6):
        if os.path.basename(saved_dir) == "Saved":
            break
        parent = os.path.dirname(saved_dir)
        if parent == saved_dir:
            break
        saved_dir = parent

    for flavour in ("LinuxServer", "WindowsServer"):
        candidate = os.path.join(saved_dir, "Config", flavour, "PalWorldSettings.ini")
        if os.path.exists(candidate):
            return candidate

    matches = glob.glob(
        os.path.join(saved_dir, "Config", "**", "PalWorldSettings.ini"), recursive=True
    )
    return matches[0] if matches else None


# ─── Corruption-safe reading ─────────────────────────────────────


def _stat_key(path: str) -> tuple[int, float]:
    st = os.stat(path)
    return st.st_size, st.st_mtime


def read_sav_bytes(path: str) -> Optional[bytes]:
    """
    Read a .sav with a torn-read guard.

    We snapshot (size, mtime), read the whole file, then re-snapshot. If the file
    moved underneath us the server was mid-write, so we back off and retry rather
    than hand a truncated buffer to the parser.

    Read-only throughout: the live file is never opened for writing, never
    locked, and never moved.
    """
    if not os.path.exists(path):
        logger.warning("Save file not found: %s", path)
        return None

    for attempt in range(1, STABILITY_RETRIES + 1):
        try:
            before = _stat_key(path)
            fd = os.open(path, os.O_RDONLY)
            try:
                chunks = []
                while True:
                    chunk = os.read(fd, 4 * 1024 * 1024)
                    if not chunk:
                        break
                    chunks.append(chunk)
                data = b"".join(chunks)
            finally:
                os.close(fd)
            after = _stat_key(path)

            if before == after and len(data) == before[0]:
                return data

            logger.info(
                "%s changed during read (attempt %d/%d) — server is writing, retrying",
                os.path.basename(path),
                attempt,
                STABILITY_RETRIES,
            )
            time.sleep(1.5 * attempt)
        except OSError as e:
            logger.error("Read error on %s: %s", path, e)
            time.sleep(1.0)

    logger.error("Gave up reading %s — file kept changing (server actively saving)", path)
    return None


def snapshot_to_temp(path: str) -> Optional[str]:
    """
    Copy a save to a temp file and return the temp path, for when a parser wants
    a real file handle. Caller owns the temp file and must delete it.
    """
    data = read_sav_bytes(path)
    if data is None:
        return None
    os.makedirs(CACHE_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".sav", prefix="snapshot_", dir=CACHE_DIR)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return tmp
    except OSError as e:
        logger.error("Snapshot failed for %s: %s", path, e)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return None


def atomic_write(path: str, data: bytes) -> None:
    """
    Write a file atomically: temp file in the same directory, fsync, rename.
    A crash mid-write leaves the original intact rather than a half file.

    Callers must have already cleared safety.assert_writable().
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=directory)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(path):
            shutil.copystat(path, tmp)
        os.replace(tmp, path)
        # fsync the directory so the rename itself is durable
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
