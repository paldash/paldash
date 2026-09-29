"""Online SQLite/policy snapshots and offline, recoverable dashboard restores.

Run `python backend/statebackup.py create|list|restore ID|recover`. This never
touches game saves. Restore requires the backend stopped and invalidates sessions
and export/job references. Environment secrets stay in the deployment's secret
store; the archive records the configuration boundary instead of copying .env.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tarfile
import tempfile
import time
import uuid

import db
import maintenance
import policy
import savefiles

KEEP = int(os.environ.get('STATE_BACKUP_KEEP', '10'))


def root() -> Path:
    return Path(savefiles.BACKUP_DIR) / 'dashboard-state'


def _pending() -> Path:
    return Path(db.DB_PATH).parent / '.state-restore.json'


def _safe(path: Path) -> None:
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise RuntimeError('Dashboard recovery refuses symlink paths')


@contextmanager
def service_lease(*, restoring=False):
    path = Path(db.DB_PATH).absolute()
    _safe(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path) + '.service.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('Stop the dashboard backend before restoring its state') from error
        if not restoring and _pending().exists():
            raise RuntimeError('Dashboard restore was interrupted; run statebackup.py recover before starting')
        yield
    finally:
        os.close(fd)


def _online_copy(source: str, destination: Path) -> None:
    # The backup API includes committed WAL pages; copying dashboard.db does not.
    with closing(sqlite3.connect('file:' + str(Path(source).absolute()) + '?mode=ro', uri=True)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst, pages=256)
            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('SQLite snapshot failed integrity verification')
            if dst.execute('PRAGMA foreign_key_check').fetchone():
                raise RuntimeError('SQLite snapshot contains broken references')
            dst.execute('PRAGMA journal_mode=DELETE')


@maintenance.serialized
def create(*, prune_old=True) -> dict:
    folder = root().absolute()
    _safe(folder)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    ident = time.strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(dir=folder, prefix='.snapshot-') as staging:
        stage = Path(staging)
        policy_path = Path(policy.POLICY_FILE)
        _safe(policy_path.absolute())
        before = policy_path.read_bytes() if policy_path.exists() else None
        _online_copy(db.DB_PATH, stage / 'dashboard.db')
        after = policy_path.read_bytes() if policy_path.exists() else None
        if before != after:
            raise RuntimeError('Policy changed during snapshot; retry')
        if before is not None:
            json.loads(before)
            (stage / 'policy.json').write_bytes(before)
        manifest = {'version': 1, 'createdAt': time.time(), 'files': {},
                    'configuration': 'SQLite accounts, audit, schedules and policy; environment secrets and game saves are separate'}
        for path in stage.iterdir():
            manifest['files'][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        (stage / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True))
        part = folder / (ident + '.part')
        try:
            with tarfile.open(part, 'w:gz') as archive:
                for path in stage.iterdir():
                    archive.add(path, arcname=path.name, recursive=False)
            _unpack(part, stage / 'verify')
            with part.open('rb') as handle:
                os.fsync(handle.fileno())
            os.chmod(part, 0o600)
            os.replace(part, folder / (ident + '.tar.gz'))
        finally:
            part.unlink(missing_ok=True)
    if prune_old:
        prune()
    return {'id': ident, 'bytes': (folder / (ident + '.tar.gz')).stat().st_size}


def _unpack(path: Path, destination: Path) -> dict:
    destination.mkdir(mode=0o700)
    with tarfile.open(path, 'r:gz') as archive:
        members = archive.getmembers()
        names = {m.name for m in members}
        if len(names) != len(members) or not {'dashboard.db', 'manifest.json'} <= names or names - {'dashboard.db', 'manifest.json', 'policy.json'}:
            raise RuntimeError('Invalid dashboard snapshot scope')
        if any(not m.isfile() or m.size > 512 * 1024**2 for m in members):
            raise RuntimeError('Invalid dashboard snapshot member')
        for member in members:
            handle = archive.extractfile(member)
            with (destination / member.name).open('xb') as output:
                shutil.copyfileobj(handle, output)
    manifest = json.loads((destination / 'manifest.json').read_text())
    if manifest.get('version') != 1 or set(manifest.get('files', {})) != names - {'manifest.json'}:
        raise RuntimeError('Invalid dashboard snapshot manifest')
    for name, digest in manifest['files'].items():
        if hashlib.sha256((destination / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError('Dashboard snapshot checksum mismatch')
    return manifest


def listing() -> list[dict]:
    return [{'id': p.name[:-7], 'bytes': p.stat().st_size} for p in sorted(root().glob('*.tar.gz'), reverse=True) if not p.is_symlink()]


def prune() -> None:
    if _pending().exists():
        return  # Do not prune the rollback point of an unfinished restore.
    for entry in listing()[max(KEEP, 1):]:
        (root() / (entry['id'] + '.tar.gz')).unlink()


def _sync_parent(path: Path) -> None:
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _clear(record: dict) -> None:
    _pending().unlink()
    _sync_parent(_pending())
    shutil.rmtree(record['stage'])


def recover() -> dict:
    with service_lease(restoring=True):
        return _recover()


def _recover() -> dict:
    if not _pending().exists():
        return {'recovered': False}
    record = json.loads(_pending().read_text())
    allowed = {str(Path(db.DB_PATH).absolute()), str(Path(policy.POLICY_FILE).absolute())}
    stage = Path(record['stage'])
    if stage.parent != root().absolute() or not stage.name.startswith('.restore-'):
        raise RuntimeError('Invalid dashboard recovery workspace')
    for i, entry in enumerate(record['files']):
        path = Path(entry['path'])
        _safe(path); _safe(stage / str(i))
        if str(path) not in allowed:
            raise RuntimeError('Invalid dashboard recovery target')
        if entry['before'] is not None and hashlib.sha256((stage / str(i)).read_bytes()).hexdigest() != entry['before']:
            raise RuntimeError('Dashboard recovery bytes failed verification')
    for i, entry in enumerate(record['files']):
        path = Path(entry['path'])
        current = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        if current not in {entry['before'], entry['after']}:
            raise RuntimeError('Dashboard state changed outside recovery; manual review required')
        if entry['before'] is None:
            path.unlink(missing_ok=True)
            _sync_parent(path)
        else:
            savefiles.atomic_write(str(path), (stage / str(i)).read_bytes())
    _clear(record)
    return {'recovered': True}


def restore(ident: str) -> dict:
    import re
    if not re.fullmatch(r'\d{8}-\d{6}-[0-9a-f]{12}', ident):
        raise RuntimeError('Invalid dashboard snapshot ID')
    with service_lease(restoring=True), maintenance.lease():
        if _pending().exists():
            raise RuntimeError('Recover the interrupted dashboard restore first')
        rollback = create(prune_old=False)
        stage = Path(tempfile.mkdtemp(prefix='.restore-', dir=root().absolute()))
        try:
            _unpack(root() / (ident + '.tar.gz'), stage / 'incoming')
            incoming = stage / 'incoming' / 'dashboard.db'
            with closing(sqlite3.connect(incoming)) as conn:
                if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or conn.execute('PRAGMA foreign_key_check').fetchone():
                    raise RuntimeError('Restored database failed integrity verification')
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {'users', 'sessions', 'audit_log'} <= tables:
                    raise RuntimeError('Snapshot is not a dashboard database')
                for table in ('sessions', 'self_exports', 'jobs'):
                    if table in tables:
                        conn.execute(f'DELETE FROM {table}')
                import exportidentity
                exportidentity.rotate(conn)
                conn.commit()
                conn.execute('PRAGMA journal_mode=DELETE')
            # Checkpoint the current database before removing its WAL/SHM, with the
            # backend lease held. No live connection can have uncheckpointed writes.
            with closing(sqlite3.connect(db.DB_PATH)) as conn:
                if conn.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]:
                    raise RuntimeError('Dashboard database still has active writers')
            for suffix in ('-wal', '-shm'):
                Path(db.DB_PATH + suffix).unlink(missing_ok=True)
            updates = [(Path(db.DB_PATH).absolute(), incoming.read_bytes())]
            config = stage / 'incoming' / 'policy.json'
            updates.append((Path(policy.POLICY_FILE).absolute(), config.read_bytes() if config.exists() else None))
            record = {'stage': str(stage), 'rollbackId': rollback['id'], 'files': []}
            for i, (target, data) in enumerate(updates):
                _safe(target)
                before = target.read_bytes() if target.exists() else None
                if before is not None:
                    savefiles.atomic_write(str(stage / str(i)), before)
                record['files'].append({'path': str(target), 'before': hashlib.sha256(before).hexdigest() if before is not None else None,
                                        'after': hashlib.sha256(data).hexdigest() if data is not None else None})
            _sync_parent(stage)
            savefiles.atomic_write(str(_pending()), json.dumps(record, sort_keys=True).encode())
            try:
                for target, data in updates:
                    if data is None:
                        target.unlink(missing_ok=True)
                        _sync_parent(target)
                    else:
                        savefiles.atomic_write(str(target), data)
                        if target.read_bytes() != data:
                            raise RuntimeError('Dashboard restore read-back failed')
            except BaseException:
                _recover()
                raise
            _clear(record)
            return {'restored': ident, 'rollbackId': rollback['id'], 'sessionsInvalidated': True}
        finally:
            if not _pending().exists():
                shutil.rmtree(stage, ignore_errors=True)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['create', 'list', 'restore', 'recover'])
    parser.add_argument('id', nargs='?')
    args = parser.parse_args()
    result = {'create': create, 'list': listing, 'recover': recover}.get(args.operation)
    print(json.dumps(result() if result else restore(args.id or ''), indent=2))
