"""
Backups, restores, and the write-guard every mutation must pass through.

The contract, in one place:

  1. While the game server may be running, NOTHING here writes to the save
     directory. Reads are O_RDONLY (see savefiles.read_sav_bytes) and backups
     only ever read *out* of it.
  2. Any mutation of the save directory goes through `guarded_save_write`, which
     re-checks the fail-closed server state, takes a full backup *before* the
     change, then re-checks again. If the backup fails, the change does not
     happen.
  3. `PalWorldSettings.ini` follows the same rule in settings_ini.write_ini.
  4. Restores snapshot the current world first, so a restore is itself
     reversible.

Backups are now single verified archives (see backupstore.py) rather than
directory copies. Beyond being one file with checksums, this fixed a real
problem: `copytree` on the world directory also swallowed the server's own
rotating snapshots living in `<world>/backup/`, so a 2.1 MB world was producing
66 MB backups that each contained copies of all the earlier ones.
"""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Optional

import backupstore
from backupstore import BackupError, LocalBackupStore
from safety import ServerRunningError, assert_writable, get_server_state
from savefiles import BACKUP_DIR, find_settings_ini, get_default_world_dir

logger = logging.getLogger(__name__)

# Triggers that mark a backup as a rollback point for an operation in progress.
# Retention never prunes one of these while it is still young, because it is the
# only way back from an edit that went wrong.
SAFETY_TRIGGERS = {"pre-edit", "pre-restore", "pre-import", "pre-update"}
SAFETY_GRACE_HOURS = int(os.environ.get("BACKUP_SAFETY_GRACE_HOURS", "48"))

# Retention defaults. `keep_*` are counts within each bucket.
DEFAULT_RETENTION = {
    "keepLatest": int(os.environ.get("BACKUP_KEEP_LATEST", "5")),
    "keepDaily": int(os.environ.get("BACKUP_KEEP_DAILY", "7")),
    "keepWeekly": int(os.environ.get("BACKUP_KEEP_WEEKLY", "4")),
    "maxTotal": int(os.environ.get("BACKUP_MAX_TOTAL", "50")),
}

_store: Optional[LocalBackupStore] = None


def store() -> LocalBackupStore:
    global _store
    if _store is None or _store.root != BACKUP_DIR:
        _store = LocalBackupStore(BACKUP_DIR)
    return _store


def _reset_store_for_tests() -> None:
    global _store
    _store = None


def ensure_backup_dir() -> None:
    os.makedirs(BACKUP_DIR, exist_ok=True)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# ─── Creating ────────────────────────────────────────────────────


def create_backup(
    world_dir: Optional[str] = None,
    description: str = "",
    *,
    trigger: str = "manual",
    created_by: Optional[str] = None,
    include_config: bool = True,
) -> dict[str, Any]:
    """
    Snapshot the world into one verified archive.

    Safe to run while the server is live: it only reads save files and writes
    elsewhere. Files may be mid-autosave, so a backup taken on a running server
    is best-effort — that is recorded in the manifest as `serverWasRunning` so a
    restore can warn about it.
    """
    world_dir = world_dir or get_default_world_dir()
    if not world_dir:
        raise BackupError("No world directory found")

    ensure_backup_dir()
    state = get_server_state()
    backup_id = uuid.uuid4().hex[:12]

    extra: list[tuple[str, str]] = []
    if include_config:
        # A world restored without the settings it was running under is only
        # half a restore.
        ini = find_settings_ini()
        if ini and os.path.exists(ini):
            extra.append((ini, "config/PalWorldSettings.ini"))

    archive_path = store().path_for(backup_id)
    manifest = backupstore.create_archive(
        world_dir,
        archive_path,
        description=description,
        trigger=trigger,
        created_by=created_by,
        server_was_running=state.running,
        extra_files=extra,
    )
    manifest["id"] = backup_id

    # Sidecar manifest so listing does not have to open every archive.
    with open(store().manifest_path(backup_id), "w") as f:
        import json

        json.dump(manifest, f, indent=2)

    logger.info(
        "Backup %s created (%.1f MB, trigger=%s, serverWasRunning=%s)",
        backup_id, manifest["archiveBytes"] / 1024 / 1024, trigger, state.running,
    )
    return _public(manifest)


def _public(manifest: dict[str, Any]) -> dict[str, Any]:
    """The subset of a manifest the API returns — no full file list."""
    return {
        "id": manifest.get("id", ""),
        "timestamp": manifest.get("createdAt", ""),
        "description": manifest.get("description", ""),
        "trigger": manifest.get("trigger", "manual"),
        "createdBy": manifest.get("createdBy"),
        "sizeBytes": manifest.get("archiveBytes", 0),
        "uncompressedBytes": manifest.get("uncompressedBytes", 0),
        "fileCount": manifest.get("fileCount", 0),
        "worldGuid": manifest.get("worldGuid", ""),
        "serverWasRunning": manifest.get("serverWasRunning", False),
        "manifestVersion": manifest.get("manifestVersion", 1),
        "compressionRatio": (
            round(manifest.get("archiveBytes", 0) / manifest["uncompressedBytes"], 3)
            if manifest.get("uncompressedBytes") else None
        ),
    }


# ─── Listing ─────────────────────────────────────────────────────


def _load_manifest(backup_id: str) -> Optional[dict[str, Any]]:
    """Sidecar first; fall back to reading it out of the archive."""
    import json

    sidecar = store().manifest_path(backup_id)
    if os.path.exists(sidecar):
        try:
            with open(sidecar) as f:
                manifest = json.load(f)
            manifest.setdefault("id", backup_id)
            return manifest
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Bad sidecar manifest for %s: %s", backup_id, e)

    if not store().exists(backup_id):
        return None

    try:
        manifest = backupstore.read_manifest(store().path_for(backup_id))
        manifest.setdefault("id", backup_id)
        return manifest
    except BackupError as e:
        logger.warning("Could not read manifest for %s: %s", backup_id, e)
        return None


def list_backups() -> list[dict[str, Any]]:
    """All backups, newest first."""
    ensure_backup_dir()
    found = []
    for backup_id in store().list_ids():
        manifest = _load_manifest(backup_id)
        if manifest:
            found.append(_public(manifest))
    found.sort(key=lambda b: b.get("timestamp", ""), reverse=True)
    return found


def find_backup(backup_id: str) -> Optional[dict[str, Any]]:
    manifest = _load_manifest(backup_id)
    return _public(manifest) if manifest else None


def verify_backup(backup_id: str) -> dict[str, Any]:
    """Re-hash the archive and every file in it."""
    manifest = _load_manifest(backup_id)
    if not manifest:
        return {"ok": False, "problems": ["Backup not found"], "checkedFiles": 0}
    return backupstore.verify_archive(store().path_for(backup_id), manifest)


def describe_backup(backup_id: str) -> Optional[dict[str, Any]]:
    """Full detail including the file list, for the browser's detail view."""
    manifest = _load_manifest(backup_id)
    if not manifest:
        return None
    return {**_public(manifest), "files": manifest.get("files", [])}


def rename_backup(backup_id: str, description: str) -> Optional[dict[str, Any]]:
    """
    Change a backup's description.

    Only the sidecar is rewritten; the copy inside the archive is left alone so
    the archive's checksum stays valid. The sidecar is a convenience index, not
    the integrity record.
    """
    import json

    manifest = _load_manifest(backup_id)
    if not manifest:
        return None

    manifest["description"] = description[:500]
    with open(store().manifest_path(backup_id), "w") as f:
        json.dump(manifest, f, indent=2)
    return _public(manifest)


def delete_backup(backup_id: str) -> bool:
    if not store().exists(backup_id):
        return False
    return store().delete(backup_id)


# ─── Restore ─────────────────────────────────────────────────────

# What a restore may target. Level.sav carries guild, base and container state
# that player files reference, so the two are not independent — restoring one
# without the other is how you get a player holding items the world has never
# heard of.
RESTORE_SCOPES = {
    "world": "Everything: Level.sav, level metadata and every player file.",
    "players": "Only the Players/ directory, leaving the world as it is.",
    "config": "Only PalWorldSettings.ini.",
}


def preview_restore(backup_id: str, scope: str = "world") -> dict[str, Any]:
    """
    What restoring would change, without changing anything.

    Compares the archive's manifest against what is on disk now, file by file, so
    the operator sees exactly which files would be replaced, added or left alone
    before committing.
    """
    manifest = _load_manifest(backup_id)
    if not manifest:
        raise BackupError(f"Backup {backup_id} not found")
    if scope not in RESTORE_SCOPES:
        raise BackupError(f"Unknown restore scope: {scope}")

    world_dir = get_default_world_dir()
    if not world_dir:
        raise BackupError("No world directory found")

    selected = [e for e in manifest.get("files", []) if _in_scope(e["path"], scope)]
    changes = []

    for entry in selected:
        target = _target_path(entry["path"], world_dir)
        if target is None:
            continue

        if not os.path.exists(target):
            changes.append({"path": entry["path"], "action": "create", "size": entry["size"]})
            continue

        current_size = os.path.getsize(target)
        if current_size != entry["size"]:
            changes.append({
                "path": entry["path"], "action": "replace",
                "size": entry["size"], "currentSize": current_size,
            })
            continue

        # Same size — hash to be sure. These files are small enough that this is
        # cheap, and "same size" is not "same content".
        if backupstore._sha256_file(target) != entry["sha256"]:
            changes.append({
                "path": entry["path"], "action": "replace",
                "size": entry["size"], "currentSize": current_size,
            })
        else:
            changes.append({"path": entry["path"], "action": "identical", "size": entry["size"]})

    # Files present now that the backup does not have. A restore does not delete
    # them, which matters for players who joined after the backup was taken.
    orphans = []
    if scope in ("world", "players"):
        known = {e["path"] for e in selected}
        for absolute, relative in backupstore.collect_world_files(world_dir):
            normalised = relative.replace(os.sep, "/")
            if not _in_scope(normalised, scope):
                continue
            if normalised not in known:
                orphans.append({"path": normalised, "size": os.path.getsize(absolute)})

    return {
        "backupId": backup_id,
        "scope": scope,
        "scopeDescription": RESTORE_SCOPES[scope],
        "timestamp": manifest.get("createdAt"),
        "serverWasRunning": manifest.get("serverWasRunning", False),
        "changes": changes,
        "summary": {
            "replace": sum(1 for c in changes if c["action"] == "replace"),
            "create": sum(1 for c in changes if c["action"] == "create"),
            "identical": sum(1 for c in changes if c["action"] == "identical"),
        },
        # Deliberately called "kept": a restore is not a wipe.
        "keptUntouched": orphans,
    }


def _in_scope(archive_path: str, scope: str) -> bool:
    is_config = archive_path.startswith("config/")
    is_player = archive_path.startswith("Players/")

    if scope == "config":
        return is_config
    if scope == "players":
        return is_player
    # "world" means the save data, not the server config — restoring settings is
    # a separate decision from restoring the world.
    return not is_config


def _target_path(archive_path: str, world_dir: str) -> Optional[str]:
    if archive_path.startswith("config/"):
        ini = find_settings_ini()
        return ini if ini else None
    return os.path.join(world_dir, archive_path.replace("/", os.sep))


def restore_backup(
    backup_id: str,
    scope: str = "world",
    *,
    created_by: Optional[str] = None,
) -> dict[str, Any]:
    """
    Replace the current world (or part of it) with a backup.

    Snapshots the current state first, so a restore is itself reversible, and
    verifies the archive before touching anything — restoring from a corrupt
    backup on top of a working world would be the worst possible outcome.
    """
    manifest = _load_manifest(backup_id)
    if not manifest:
        raise BackupError(f"Backup {backup_id} not found")
    if scope not in RESTORE_SCOPES:
        raise BackupError(f"Unknown restore scope: {scope}")

    world_dir = get_default_world_dir()
    if not world_dir:
        raise BackupError("No world directory found")

    assert_writable()

    verdict = backupstore.verify_archive(store().path_for(backup_id), manifest)
    if not verdict["ok"]:
        raise BackupError(
            "Refusing to restore: this backup failed verification "
            f"({'; '.join(verdict['problems'][:3])})"
        )

    rollback = create_backup(
        world_dir,
        f"Before restoring {backup_id}",
        trigger="pre-restore",
        created_by=created_by,
    )

    assert_writable()  # the window between check and write is where servers restart

    selected = [e["path"] for e in manifest.get("files", []) if _in_scope(e["path"], scope)]
    if not selected:
        raise BackupError(f"This backup contains nothing matching scope '{scope}'")

    workspace = backupstore.temp_workspace()
    try:
        backupstore.extract_archive(store().path_for(backup_id), workspace, only=selected)

        restored = []
        for archive_path in selected:
            source = os.path.join(workspace, archive_path.replace("/", os.sep))
            target = _target_path(archive_path, world_dir)
            if target is None or not os.path.exists(source):
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(source, target)
            restored.append(archive_path)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    logger.info("Restored %d file(s) from %s (scope=%s)", len(restored), backup_id, scope)
    return {
        "success": True,
        "backupId": backup_id,
        "scope": scope,
        "restoredFiles": restored,
        "rollbackId": rollback["id"],
    }


# ─── Retention ───────────────────────────────────────────────────


def prune_backups(
    retention: Optional[dict[str, int]] = None, *, dry_run: bool = False
) -> dict[str, Any]:
    """
    Apply retention, keeping a thinning history rather than a flat window.

    Keeps the newest N outright, then one per day for N days, then one per week
    for N weeks. Recent safety backups (the automatic rollback point taken before
    an edit or restore) are never pruned inside their grace period — those are
    the only way back from a bad edit.
    """
    rules = {**DEFAULT_RETENTION, **(retention or {})}
    backups = list_backups()
    if not backups:
        return {"kept": 0, "removed": [], "dryRun": dry_run}

    keep: set[str] = set()
    now = _utc_now()

    for backup in backups[: max(0, rules["keepLatest"])]:
        keep.add(backup["id"])

    for backup in backups:
        if backup.get("trigger") in SAFETY_TRIGGERS:
            age = now - _parse_ts(backup["timestamp"])
            if age <= timedelta(hours=SAFETY_GRACE_HOURS):
                keep.add(backup["id"])

    seen_days: set[str] = set()
    seen_weeks: set[str] = set()
    for backup in backups:
        moment = _parse_ts(backup["timestamp"])
        day = moment.strftime("%Y-%m-%d")
        week = moment.strftime("%G-W%V")
        if len(seen_days) < rules["keepDaily"] and day not in seen_days:
            seen_days.add(day)
            keep.add(backup["id"])
        if len(seen_weeks) < rules["keepWeekly"] and week not in seen_weeks:
            seen_weeks.add(week)
            keep.add(backup["id"])

    # Absolute ceiling, applied last and still honouring the newest-first order.
    if rules["maxTotal"] > 0:
        ordered_keeps = [b["id"] for b in backups if b["id"] in keep]
        if len(ordered_keeps) > rules["maxTotal"]:
            keep = set(ordered_keeps[: rules["maxTotal"]])

    removed = []
    for backup in backups:
        if backup["id"] in keep:
            continue
        if not dry_run:
            delete_backup(backup["id"])
        removed.append({
            "id": backup["id"],
            "timestamp": backup["timestamp"],
            "sizeBytes": backup["sizeBytes"],
        })

    if removed and not dry_run:
        logger.info("Retention removed %d backup(s)", len(removed))

    return {
        "kept": len(keep),
        "removed": removed,
        "freedBytes": sum(r["sizeBytes"] for r in removed),
        "rules": rules,
        "dryRun": dry_run,
    }


def storage_usage() -> dict[str, Any]:
    backups = list_backups()
    return {
        "count": len(backups),
        "totalBytes": sum(b["sizeBytes"] for b in backups),
        "oldest": backups[-1]["timestamp"] if backups else None,
        "newest": backups[0]["timestamp"] if backups else None,
        "directory": BACKUP_DIR,
    }


# ─── The write guard ─────────────────────────────────────────────


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
    assert_writable()

    try:
        backup = create_backup(
            world_dir, f"Automatic backup before: {reason}", trigger="pre-edit"
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Pre-write backup failed, aborting '%s': %s", reason, e)
        raise ServerRunningError(f"Aborted: could not back up before '{reason}': {e}") from e

    logger.info("Proceeding with '%s' (rollback point: %s)", reason, backup["id"])

    # Re-check immediately before handing over: the window between the first
    # check and here is where someone restarts the server.
    assert_writable()

    yield backup
