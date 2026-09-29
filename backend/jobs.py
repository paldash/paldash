"""Durable, account-owned queue for costly exports, with bounded pending work.

Queued work survives a restart. Interrupted work is never silently replayed:
the user must request a fresh export against a fresh preview. Handlers recheck
the account and route capability when execution actually begins.
"""
import json
import threading
import time
import uuid

import accounts
import db

_handlers = {}
_wake = threading.Event()
_stop = threading.Event()
_worker = None
_local = threading.local()


class JobError(Exception):
    pass


def init():
    with db.transaction() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            kind TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL, stage TEXT NOT NULL,
            result TEXT, error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL)''')


def submit(kind, user, payload):
    init()
    if kind not in _handlers:
        raise JobError('Unknown background operation')
    encoded = json.dumps(payload)
    if len(encoded) > 65536:
        raise JobError('Background request is too large')
    ident = uuid.uuid4().hex
    with db.transaction() as conn:
        conn.execute("DELETE FROM jobs WHERE state NOT IN ('queued','running') AND updated_at < ?", (time.time() - 7 * 86400,))
        if conn.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0] >= 8:
            raise JobError('The export queue is full; try again later')
        if conn.execute("SELECT 1 FROM jobs WHERE owner_id=? AND state IN ('queued','running')", (user['id'],)).fetchone():
            raise JobError('You already have an export queued or running')
        conn.execute('INSERT INTO jobs (id,owner_id,kind,payload,state,stage,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)',
                     (ident, user['id'], kind, encoded, 'queued', 'Waiting for the export worker', time.time(), time.time()))
    _wake.set()
    return {'jobId': ident, 'state': 'queued'}


def get(ident, owner_id):
    init()
    row = db.connect().execute('SELECT * FROM jobs WHERE id=? AND owner_id=?', (ident, owner_id)).fetchone()
    if not row:
        raise JobError('Background operation not found')
    result = json.loads(row['result']) if row['result'] else None
    if row['state'] == 'completed' and row['kind'] == 'solo-export' and isinstance(result, dict):
        result['downloadUrl'] = f'/api/save/jobs/{ident}/download'
    return {'jobId': ident, 'kind': row['kind'], 'state': row['state'], 'stage': row['stage'],
            'result': result, 'error': row['error']}


def cancel(ident, owner_id):
    with db.transaction() as conn:
        changed = conn.execute("UPDATE jobs SET state='cancelled',stage='Cancelled before execution',updated_at=? WHERE id=? AND owner_id=? AND state='queued'",
                               (time.time(), ident, owner_id)).rowcount
    if not changed:
        raise JobError('Only a queued operation can be cancelled; a running export is allowed to finish safely')
    return get(ident, owner_id)


def checkpoint(stage):
    ident = getattr(_local, 'job_id', None)
    if ident:
        with db.transaction() as conn:
            conn.execute('UPDATE jobs SET stage=?,updated_at=? WHERE id=?', (stage, time.time(), ident))


def run_next():
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created_at LIMIT 1").fetchone()
        if not row:
            return False
        conn.execute("UPDATE jobs SET state='running',stage='Checking current permissions',updated_at=? WHERE id=?", (time.time(), row['id']))
    _local.job_id = row['id']
    try:
        current = db.connect().execute('SELECT username FROM users WHERE id=?', (row['owner_id'],)).fetchone()
        user = accounts.get_user(current['username']) if current else None
        if not user or user.get('disabled') or user.get('mustChangePassword'):
            raise JobError('Account is unavailable or requires a password change')
        handler = _handlers.get(row['kind'])
        if not handler:
            raise JobError('This operation is no longer supported')
        result = handler(user, json.loads(row['payload']))
        with db.transaction() as conn:
            conn.execute("UPDATE jobs SET state='completed',stage='Complete',result=?,updated_at=? WHERE id=?", (json.dumps(result), time.time(), row['id']))
    except Exception as error:
        message = str(getattr(error, 'detail', error))[:1000]
        with db.transaction() as conn:
            conn.execute("UPDATE jobs SET state='failed',stage='Stopped',error=?,updated_at=? WHERE id=?", (message, time.time(), row['id']))
    finally:
        _local.job_id = None
    return True


def start(handlers):
    global _worker
    if _worker and _worker.is_alive():
        raise JobError('The export worker is already running')
    _handlers.update(handlers)
    init()
    with db.transaction() as conn:
        conn.execute("UPDATE jobs SET state='failed',stage='Interrupted by backend restart',error='Preview again and request a fresh export',updated_at=? WHERE state='running'", (time.time(),))
    _stop.clear()
    def work():
        try:
            while not _stop.is_set():
                if not run_next():
                    _wake.wait(2)
                    _wake.clear()
        finally:
            db.close_connection()
    _worker = threading.Thread(target=work, name='export-jobs', daemon=True)
    _worker.start()


def stop():
    _stop.set()
    _wake.set()
    if _worker:
        # The lifespan owns the database service lease. Do not release it to
        # an offline restore while an export can still publish database rows.
        _worker.join()
