"""
Backups and the write-guard every mutation must pass through.

The contract, in one place:

  1. While the game server may be running, NOTHING here writes to the save
     directory. Reads are O_RDONLY (see savefiles.read_sav_bytes) and backups
     only ever copy *out* of it.
  2. Any mutation of the save directory goes through `guarded_save_write`, which
     (a) re-checks the fail-closed server state, and (b) takes a full backup of
     the world *before* the change is applied. If the backup fails, the change
     does not happen.
  3. PalWorldSettings.ini has the same rule in settings_ini.write_ini: it copies
     the original to BACKUP_DIR/config before writing, and writes atomically.
  4. Restores snapshot the current world first, so a restore is itself
     reversible.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

from safety import ServerRunningError, assert_writable, get_server_state
from savefiles import BACKUP_DIR, SAVE_BASE_DIR  # noqa: F401

logger = logging.getLogger(__name__)

# A backup is a directory; this file marks it and holds its metadata.
META_NAME = "_backup_meta.json"


def ensure_backup_dir() -> None:
    os.makedirs(BACKUP_DIR, exist_ok=True)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _dir_size(path: str) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
    return total


def create_backup(world_dir: str, description: str = "") -> dict:
    """
    Copy a world directory into BACKUP_DIR.

    Safe to run at any time: it only reads the world and writes elsewhere. On a
    live server the files may be mid-autosave, so the result is a best-effort
    snapshot — that is recorded in the metadata as `serverWasRunning` so a
    restore can warn about it.
    """
    ensure_backup_dir()

    state = get_server_state()
    backup_id = uuid.uuid4().hex[:12]
    stamp = _utc_now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"backup_{backup_id}_{stamp}")

    logger.info("Creating backup %s from %s", backup_id, world_dir)
    # dirs_exist_ok=False: never merge into an existing directory.
    shutil.copytree(world_dir, backup_path, symlinks=False)

    meta = {
        "id": backup_id,
        "timestamp": _utc_now().isoformat(),
        "sizeBytes": _dir_size(backup_path),
        "description": description,
        "path": backup_path,
        "sourceDir": world_dir,
        "serverWasRunning": state.running,
    }
    with open(os.path.join(backup_path, META_NAME), "w") as f:
        json.dump(meta, f, indent=2)

    logger.info(
        "Backup %s created (%.1f MB, serverWasRunning=%s)",
        backup_id, meta["sizeBytes"] / 1024 / 1024, state.running,
    )
    return meta


def list_backups() -> list[dict]:
    """All backups, newest first."""
    ensure_backup_dir()
    backups = []

    for entry in os.listdir(BACKUP_DIR):
        meta_path = os.path.join(BACKUP_DIR, entry, META_NAME)
        if not os.path.isfile(meta_path):
            continue
        try:
            with open(meta_path) as f:
                backups.append(json.load(f))
        except Exception as e:  # noqa: BLE001
            logger.warning("Bad backup metadata in %s: %s", entry, e)

    backups.sort(key=lambda b: b.get("timestamp", ""), reverse=True)
    return backups


def find_backup(backup_id: str) -> Optional[dict]:
    return next((b for b in list_backups() if b.get("id") == backup_id), None)


@contextmanager
def guarded_save_write(reason: str, world_dir: str) -> Iterator[dict]:
    """
    The only sanctioned way to modify the save directory.

    Refuses unless the server is provably stopped, then takes a full backup
    before yielding. A failed backup aborts the operation — we would rather do
    nothing than mutate a world we cannot roll back.

        with guarded_save_write("sort chests", world_dir) as backup:
            ...  # mutate here
    """
    assert_writable()  # raises ServerRunningError unless provably safe

    try:
        backup = create_backup(world_dir, f"Automatic backup before: {reason}")
    except Exception as e:  # noqa: BLE001
        logger.error("Pre-write backup failed, aborting '%s': %s", reason, e)
        raise ServerRunningError(f"Aborted: could not back up before '{reason}': {e}") from e

    logger.info("Proceeding with '%s' (rollback point: %s)", reason, backup["id"])

    # Re-check immediately before handing over: the window between the first
    # check and here is where someone restarts the server.
    assert_writable()

    yield backup


def restore_backup(backup_id: str) -> bool:
    """
    Replace the current world with a backup.

    Snapshots the current world into BACKUP_DIR first, so a restore can itself
    be undone. The old code wrote that snapshot next to the world directory,
    inside SaveGames/0, where it would be picked up as another world.
    """
    target = find_backup(backup_id)
    if not target:
        logger.error("Backup not found: %s", backup_id)
        return False

    backup_path = target["path"]
    source_dir = target.get("sourceDir")

    if not source_dir or not os.path.isdir(backup_path):
        logger.error("Invalid backup data for %s", backup_id)
        return False

    assert_writable()

    if os.path.isdir(source_dir):
        safety_copy = create_backup(source_dir, f"Pre-restore snapshot (restoring {backup_id})")
        logger.info("Pre-restore snapshot: %s", safety_copy["id"])
        shutil.rmtree(source_dir)

    shutil.copytree(
        backup_path,
        source_dir,
        symlinks=False,
        ignore=shutil.ignore_patterns(META_NAME),
    )
    logger.info("Restored backup %s to %s", backup_id, source_dir)
    return True


def delete_backup(backup_id: str) -> bool:
    target = find_backup(backup_id)
    if not target:
        return False
    # Only ever delete inside BACKUP_DIR.
    path = os.path.realpath(target["path"])
    if not path.startswith(os.path.realpath(BACKUP_DIR) + os.sep):
        logger.error("Refusing to delete outside the backup directory: %s", path)
        return False
    shutil.rmtree(path, ignore_errors=True)
    logger.info("Deleted backup %s", backup_id)
    return True
