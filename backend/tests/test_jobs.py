"""The durable queue must not preserve revoked authority or cross accounts."""
import threading

import pytest

import accounts
import db
import jobs
import main


@pytest.fixture
def owner(fresh_db, monkeypatch):
    monkeypatch.setattr(jobs, '_handlers', {'test': lambda user, payload: payload})
    accounts.create_user('queue-owner', 'queue-test-password', role='owner')
    jobs.init()
    return accounts.get_user('queue-owner')


def test_queued_payload_survives_connection_restart_and_is_owned(owner):
    ident = jobs.submit('test', owner, {'selected': [1, 2]})['jobId']
    db.close_connection()
    with pytest.raises(jobs.JobError, match='not found'):
        jobs.get(ident, owner['id'] + 1)
    assert jobs.run_next()
    result = jobs.get(ident, owner['id'])
    assert result['state'] == 'completed'
    assert result['result'] == {'selected': [1, 2]}


def test_cancel_is_owned_and_only_queued_work_is_cancelled(owner):
    ident = jobs.submit('test', owner, {})['jobId']
    with pytest.raises(jobs.JobError):
        jobs.cancel(ident, owner['id'] + 1)
    assert jobs.cancel(ident, owner['id'])['state'] == 'cancelled'
    assert not jobs.run_next()
    ident = jobs.submit('test', owner, {})['jobId']
    jobs.run_next()
    with pytest.raises(jobs.JobError):
        jobs.cancel(ident, owner['id'])


@pytest.mark.parametrize('column,value', [('disabled', 1), ('must_change_password', 1)])
def test_worker_rechecks_account(owner, column, value):
    ident = jobs.submit('test', owner, {})['jobId']
    with db.transaction() as conn:
        conn.execute(f'UPDATE users SET {column}=? WHERE id=?', (value, owner['id']))
    jobs.run_next()
    assert jobs.get(ident, owner['id'])['state'] == 'failed'


def test_worker_rechecks_route_permissions(owner, monkeypatch):
    monkeypatch.setitem(jobs._handlers, 'server-export', main._job_handler('server-export'))
    ident = jobs.submit('server-export', owner, {'artifactId': 'a' * 32, 'planHash': 'b' * 64})['jobId']
    with db.transaction() as conn:
        conn.execute("UPDATE users SET role='player' WHERE id=?", (owner['id'],))
    jobs.run_next()
    result = jobs.get(ident, owner['id'])
    assert result['state'] == 'failed'
    assert 'backup.manage' in result['error']


def test_relinked_self_export_is_refused(owner, monkeypatch):
    monkeypatch.setitem(jobs._handlers, 'self-export', main._job_handler('self-export'))
    ident = jobs.submit('self-export', owner, {'uid': 'a' * 32})['jobId']
    jobs.run_next()
    assert 'linked character changed' in jobs.get(ident, owner['id'])['error']


def test_duplicate_work_is_bounded(owner):
    jobs.submit('test', owner, {})
    with pytest.raises(jobs.JobError, match='already have'):
        jobs.submit('test', owner, {})


def test_restart_does_not_replay_interrupted_job_and_shutdown_drains_worker(owner, monkeypatch):
    interrupted = jobs.submit('test', owner, {})['jobId']
    with db.transaction() as conn:
        conn.execute("UPDATE jobs SET state='running' WHERE id=?", (interrupted,))
    began, release, stopped = threading.Event(), threading.Event(), threading.Event()
    def handler(user, payload):
        began.set()
        assert release.wait(10)
        return {'ok': True}
    monkeypatch.setattr(jobs, '_worker', None)
    jobs.start({'test': handler})
    closer = None
    try:
        assert jobs.get(interrupted, owner['id'])['state'] == 'failed'
        queued = jobs.submit('test', owner, {})['jobId']
        assert began.wait(5)
        closer = threading.Thread(target=lambda: (jobs.stop(), stopped.set()))
        closer.start()
        assert not stopped.wait(0.05)
        release.set()
        closer.join(5)
        assert stopped.is_set()
        assert jobs.get(queued, owner['id'])['state'] == 'completed'
    finally:
        release.set()
        jobs.stop()
        if closer:
            closer.join(5)
