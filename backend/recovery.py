"""Durable undo journal for multi-file restores. Never auto-write on startup.

The active record is published and fsynced before any live replacement. Recovery
is repeatable after a second crash, and refuses a file changed by an external
writer. Rollback archives stay pinned until the journal is durably cleared.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

import maintenance
from backupstore import BackupError


def _root() -> Path:
    import backup
    return Path(backup.BACKUP_DIR) / '.recovery'


def _sync(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe(path: Path) -> None:
    # Check each component, including a missing leaf's existing parents.
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise BackupError('Recovery refuses symlinked or relative paths')


def _read() -> dict | None:
    path = _root() / 'active.json'
    _safe(path.absolute())
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        uuid.UUID(hex=data['id'])
        if data['version'] != 1 or not isinstance(data['files'], list) or not data['files']:
            raise ValueError('invalid journal')
        return data
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise BackupError('Restore recovery record is unreadable; start and edits remain blocked') from error


def status() -> dict:
    try:
        data = _read()
    except BackupError as error:
        return {'pending': True, 'error': str(error)}
    return ({'pending': True, 'rollbackId': data['rollbackId'], 'files': len(data['files'])}
            if data else {'pending': False})


def require_clear() -> None:
    if status()['pending']:
        from safety import ServerRunningError
        raise ServerRunningError('An interrupted restore needs recovery before editing or starting the server')


def protected_ids() -> set[str]:
    data = _read()  # An unreadable journal refuses pruning rather than losing its rollback.
    return {data['rollbackId']} if data else set()


def begin(targets: list[tuple[str, str, str]], rollback_id: str, world: str) -> dict:
    require_clear()
    root = _root().absolute()
    _safe(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    ident = uuid.uuid4().hex
    folder = root / ident
    folder.mkdir(mode=0o700)
    files = []
    for index, (relative, source, target) in enumerate(targets):
        path = Path(target).absolute()
        _safe(path)
        before = path.read_bytes() if path.exists() else None
        after = Path(source).read_bytes()
        saved = folder / str(index)
        if before is not None:
            with saved.open('xb') as handle:
                os.chmod(saved, 0o600)
                handle.write(before)
                handle.flush()
                os.fsync(handle.fileno())
        files.append({'path': str(path), 'relative': relative,
                      'before': _digest(before) if before is not None else None,
                      'after': _digest(after)})
    _sync(folder)
    data = {'version': 1, 'id': ident, 'rollbackId': rollback_id,
            'world': str(Path(world).absolute()), 'files': files}
    # fsync of the parent makes the undo folder durable with the active pointer.
    from savefiles import atomic_write
    atomic_write(str(root / 'active.json'), json.dumps(data, sort_keys=True).encode())
    _sync(root.parent)
    return data


def verify_target(entry: dict, *, recovering: bool = False) -> None:
    path = Path(entry['path'])
    _safe(path)
    current = _digest(path.read_bytes()) if path.exists() else None
    accepted = {entry['before'], entry['after']} if recovering else {entry['before']}
    if current not in accepted:
        raise BackupError('Restore target changed outside this operation; recovery remains pending')


def finish(data: dict) -> None:
    root = _root()
    current = _read()
    if current is None or current['id'] != data['id']:
        raise BackupError('Restore recovery record changed')
    os.unlink(root / 'active.json')
    _sync(root)
    shutil.rmtree(root / data['id'], ignore_errors=True)


@maintenance.serialized
def recover() -> dict:
    from safety import assert_writable
    from savefiles import atomic_write, get_default_world_dir, find_settings_ini
    data = _read()
    if data is None:
        return {'recovered': False}
    world = get_default_world_dir()
    if not world or str(Path(world).absolute()) != data['world']:
        raise BackupError('Recovery belongs to a different mounted world')
    folder = _root() / data['id']
    _safe(folder.absolute())
    # Validate all paths and all undo bytes before changing even one file.
    for index, entry in enumerate(data['files']):
        relative = entry['relative']
        if relative == 'config/PalWorldSettings.ini':
            expected = find_settings_ini()
        else:
            if Path(relative).is_absolute() or '..' in Path(relative).parts or relative.startswith('config/'):
                raise BackupError('Invalid recovery target')
            expected = str(Path(world) / relative)
        if not expected or str(Path(expected).absolute()) != entry['path']:
            raise BackupError('Recovery target no longer matches the configuration')
        saved = folder / str(index)
        _safe(saved.absolute())
        if entry['before'] is not None and _digest(saved.read_bytes()) != entry['before']:
            raise BackupError('Recovery data failed verification; rollback archive remains protected')
        verify_target(entry, recovering=True)
    with maintenance.stopped_writes():
        for index, entry in reversed(list(enumerate(data['files']))):
            assert_writable()
            verify_target(entry, recovering=True)
            path = Path(entry['path'])
            if entry['before'] is None:
                if path.exists():
                    maintenance.check_commit()
                    path.unlink()
                    _sync(path.parent)
            else:
                atomic_write(str(path), (folder / str(index)).read_bytes())
            if (_digest(path.read_bytes()) if path.exists() else None) != entry['before']:
                raise BackupError('Recovery read-back verification failed')
    finish(data)
    return {'recovered': True, 'rollbackId': data['rollbackId'], 'files': len(data['files'])}
