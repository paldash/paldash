"""
Backup archives and where they live.

FORMAT
------
One `.tar.gz` per backup with a sidecar `.json` manifest. The manifest is also
stored *inside* the archive, so an archive is self-describing even if the sidecar
and the database are both lost.

Compression is deliberately light. Palworld saves are already Oodle-compressed —
a 2.0 MB `Level.sav` gzips to 2.0 MB — so the archive is about bundling and
integrity, not about shrinking. Level 1 costs almost nothing and still helps the
small text files.

WHAT GOES IN
------------
An explicit include list, not "copy the directory". The previous implementation
used `shutil.copytree` on the world directory, which on a real server also swept
up the server's OWN rotating backups sitting in `<world>/backup/` — 27 snapshots,
64 MB, duplicated into every single dashboard backup. A world that is genuinely
2.1 MB was producing 66 MB backups, and each one contained copies of all the
earlier ones.

STORAGE
-------
`BackupStore` is an interface with one implementation today (local disk). Cloud
providers were explicitly a "later, without redesigning" requirement, so the rest
of the system only ever talks to this interface — never to a path.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tarfile
import tempfile
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

MANIFEST_NAME = "palworld-dashboard-backup.json"
MANIFEST_VERSION = 1

# Directory names that must never be pulled into a backup: the server's own
# rotating snapshots, and our own backup store if somebody nests it.
EXCLUDED_DIRS = {"backup", "backups", ".tmp"}

# What actually constitutes a world.
WORLD_FILE_SUFFIXES = (".sav",)


def _sha256_file(path: str, chunk: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def collect_world_files(world_dir: str) -> list[tuple[str, str]]:
    """
    (absolute path, archive-relative path) for every file belonging to the world.

    Walks only the save files, skipping nested backup directories entirely.
    """
    collected: list[tuple[str, str]] = []

    for root, dirs, files in os.walk(world_dir):
        # Prune in place so os.walk does not descend into them at all.
        dirs[:] = [d for d in dirs if d.lower() not in EXCLUDED_DIRS]

        for name in sorted(files):
            if not name.lower().endswith(WORLD_FILE_SUFFIXES):
                continue
            absolute = os.path.join(root, name)
            relative = os.path.relpath(absolute, world_dir)
            collected.append((absolute, relative))

    return sorted(collected, key=lambda pair: pair[1])


class BackupError(Exception):
    """Creating, verifying or restoring a backup failed."""


# ─── Archive creation & verification ─────────────────────────────


def create_archive(
    world_dir: str,
    destination: str,
    *,
    description: str = "",
    trigger: str = "manual",
    created_by: Optional[str] = None,
    server_was_running: bool = False,
    extra_files: Iterable[tuple[str, str]] = (),
) -> dict[str, Any]:
    """
    Build one backup archive. Returns its manifest.

    `extra_files` carries things outside the world directory that still belong in
    a backup — PalWorldSettings.ini above all, since a world restored without the
    settings it was running under is only half a restore.
    """
    files = collect_world_files(world_dir)
    files.extend(extra_files)

    if not files:
        raise BackupError(f"No save files found under {world_dir}")

    entries = []
    total = 0
    for absolute, relative in files:
        try:
            size = os.path.getsize(absolute)
        except OSError as e:
            raise BackupError(f"Could not read {absolute}: {e}") from e
        entries.append(
            {"path": relative.replace(os.sep, "/"), "size": size, "sha256": _sha256_file(absolute)}
        )
        total += size

    manifest: dict[str, Any] = {
        "manifestVersion": MANIFEST_VERSION,
        "createdAt": _utc_now_iso(),
        "description": description,
        "trigger": trigger,
        "createdBy": created_by,
        "serverWasRunning": server_was_running,
        "worldGuid": os.path.basename(world_dir.rstrip(os.sep)),
        "sourceDir": world_dir,
        "fileCount": len(entries),
        "uncompressedBytes": total,
        "files": entries,
    }

    os.makedirs(os.path.dirname(destination) or ".", exist_ok=True)

    # Write to a temp name and rename, so an interrupted backup never leaves a
    # half archive that looks complete.
    temp = destination + ".part"
    try:
        with tarfile.open(temp, "w:gz", compresslevel=1) as tar:
            for (absolute, relative), entry in zip(files, entries):
                tar.add(absolute, arcname=entry["path"])

            payload = json.dumps(manifest, indent=2).encode("utf-8")
            info = tarfile.TarInfo(MANIFEST_NAME)
            info.size = len(payload)
            info.mtime = int(datetime.now(timezone.utc).timestamp())
            import io

            tar.addfile(info, io.BytesIO(payload))

        manifest["archiveBytes"] = os.path.getsize(temp)
        manifest["archiveSha256"] = _sha256_file(temp)
        os.replace(temp, destination)
    except BaseException:
        if os.path.exists(temp):
            try:
                os.unlink(temp)
            except OSError:
                pass
        raise

    logger.info(
        "Backup archive %s: %d files, %.1f MB -> %.1f MB",
        os.path.basename(destination), len(entries),
        total / 1024 / 1024, manifest["archiveBytes"] / 1024 / 1024,
    )
    return manifest


def read_manifest(archive_path: str) -> dict[str, Any]:
    """Pull the manifest out of an archive without extracting anything else."""
    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            member = tar.getmember(MANIFEST_NAME)
            handle = tar.extractfile(member)
            if handle is None:
                raise BackupError("Manifest is unreadable")
            return json.loads(handle.read().decode("utf-8"))
    except (tarfile.TarError, KeyError, json.JSONDecodeError, OSError) as e:
        raise BackupError(f"Could not read the manifest: {e}") from e


def verify_archive(archive_path: str, manifest: Optional[dict] = None) -> dict[str, Any]:
    """
    Check an archive is intact and matches its manifest.

    Verifies the archive's own checksum, that every listed file is present, and
    that each file's content still hashes to what was recorded. This is the
    difference between "a backup exists" and "a backup will restore".
    """
    problems: list[str] = []

    if not os.path.exists(archive_path):
        return {"ok": False, "problems": ["Archive file is missing"], "checkedFiles": 0}

    try:
        stored = manifest or read_manifest(archive_path)
    except BackupError as e:
        return {"ok": False, "problems": [str(e)], "checkedFiles": 0}

    expected_digest = stored.get("archiveSha256")
    if expected_digest:
        actual = _sha256_file(archive_path)
        if actual != expected_digest:
            problems.append("Archive checksum does not match — the file has changed on disk")

    expected = {entry["path"]: entry for entry in stored.get("files", [])}
    checked = 0

    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            present = {m.name for m in tar.getmembers()}

            for path, entry in expected.items():
                if path not in present:
                    problems.append(f"Missing from archive: {path}")
                    continue

                handle = tar.extractfile(path)
                if handle is None:
                    problems.append(f"Unreadable in archive: {path}")
                    continue

                digest = hashlib.sha256()
                size = 0
                while True:
                    block = handle.read(1024 * 1024)
                    if not block:
                        break
                    digest.update(block)
                    size += len(block)

                if size != entry["size"]:
                    problems.append(f"Wrong size: {path}")
                elif digest.hexdigest() != entry["sha256"]:
                    problems.append(f"Corrupted: {path}")
                checked += 1
    except (tarfile.TarError, OSError) as e:
        problems.append(f"Archive is unreadable: {e}")

    return {
        "ok": not problems,
        "problems": problems,
        "checkedFiles": checked,
        "expectedFiles": len(expected),
    }


def extract_archive(
    archive_path: str, destination: str, *, only: Optional[Iterable[str]] = None
) -> list[str]:
    """
    Extract into `destination`, optionally only selected archive-relative paths.

    Every member is checked to stay inside the destination before anything is
    written — a tar entry named `../../etc/passwd` is a real attack against
    naive extraction, and this code path will eventually handle uploaded files.
    """
    wanted = set(only) if only is not None else None
    written: list[str] = []
    destination_real = os.path.realpath(destination)
    os.makedirs(destination, exist_ok=True)

    with tarfile.open(archive_path, "r:gz") as tar:
        for member in tar.getmembers():
            if member.name == MANIFEST_NAME:
                continue
            if wanted is not None and member.name not in wanted:
                continue
            if not member.isfile():
                continue

            target = os.path.realpath(os.path.join(destination, member.name))
            if target != destination_real and not target.startswith(destination_real + os.sep):
                raise BackupError(f"Refusing unsafe archive entry: {member.name}")

            os.makedirs(os.path.dirname(target), exist_ok=True)
            handle = tar.extractfile(member)
            if handle is None:
                continue
            with open(target, "wb") as out:
                shutil.copyfileobj(handle, out)
            written.append(member.name)

    return written


# ─── Storage ─────────────────────────────────────────────────────


class BackupStore(ABC):
    """
    Where archives live.

    Deliberately small: put, open, stat, list, delete. Anything a cloud provider
    cannot do cheaply (random access, in-place edit) is not in the interface, so
    an S3 or Backblaze implementation can be added later without reshaping the
    callers.
    """

    @abstractmethod
    def path_for(self, backup_id: str) -> str:
        """Local filesystem path to write to or read from."""

    @abstractmethod
    def exists(self, backup_id: str) -> bool: ...

    @abstractmethod
    def size(self, backup_id: str) -> int: ...

    @abstractmethod
    def list_ids(self) -> list[str]: ...

    @abstractmethod
    def delete(self, backup_id: str) -> bool: ...

    @abstractmethod
    def manifest_path(self, backup_id: str) -> str: ...


class LocalBackupStore(BackupStore):
    """Archives on the local filesystem, in a directory we own."""

    SUFFIX = ".tar.gz"

    def __init__(self, root: str):
        self.root = root

    def _safe(self, backup_id: str) -> str:
        # Backup IDs are generated hex; refuse anything that could escape.
        if not backup_id or not all(c.isalnum() or c in "-_" for c in backup_id):
            raise BackupError(f"Invalid backup id: {backup_id!r}")
        return backup_id

    def path_for(self, backup_id: str) -> str:
        return os.path.join(self.root, self._safe(backup_id) + self.SUFFIX)

    def manifest_path(self, backup_id: str) -> str:
        return os.path.join(self.root, self._safe(backup_id) + ".json")

    def exists(self, backup_id: str) -> bool:
        return os.path.exists(self.path_for(backup_id))

    def size(self, backup_id: str) -> int:
        try:
            return os.path.getsize(self.path_for(backup_id))
        except OSError:
            return 0

    def list_ids(self) -> list[str]:
        if not os.path.isdir(self.root):
            return []
        return sorted(
            name[: -len(self.SUFFIX)]
            for name in os.listdir(self.root)
            if name.endswith(self.SUFFIX)
        )

    def delete(self, backup_id: str) -> bool:
        removed = False
        for path in (self.path_for(backup_id), self.manifest_path(backup_id)):
            if os.path.exists(path):
                try:
                    os.unlink(path)
                    removed = True
                except OSError as e:
                    logger.error("Could not delete %s: %s", path, e)
        return removed


def temp_workspace(prefix: str = "restore_") -> str:
    return tempfile.mkdtemp(prefix=prefix)
