"""Exercise online WAL capture, offline recovery and stale-session revocation."""
import json
from pathlib import Path
import sqlite3

import pytest

import accounts
import db
import policy
import savefiles
import statebackup


@pytest.fixture
def state(fresh_db, tmp_path, monkeypatch):
    monkeypatch.setattr(savefiles, 'BACKUP_DIR', str(tmp_path / 'backups'))
    monkeypatch.setattr(policy, 'POLICY_FILE', str(tmp_path / 'policy.json'))
    Path(policy.POLICY_FILE).write_text('{"securityLevel":"safe"}')
    accounts.create_user('owner', 'recovery-test-password', role='owner')
    return tmp_path


def test_online_snapshot_and_offline_restore_invalidate_sessions(state):
    token = accounts.authenticate('owner', 'recovery-test-password')
    assert token
    snapshot = statebackup.create()
    accounts.create_user('later', 'another-test-password')
    Path(policy.POLICY_FILE).write_text('{"securityLevel":"full"}')
    db.reset_for_tests()
    result = statebackup.restore(snapshot['id'])
    assert result['sessionsInvalidated']
    with sqlite3.connect(db.DB_PATH) as conn:
        assert conn.execute('SELECT username FROM users').fetchall() == [('owner',)]
        assert conn.execute('SELECT count(*) FROM sessions').fetchone()[0] == 0
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert json.loads(Path(policy.POLICY_FILE).read_text())['securityLevel'] == 'safe'
    assert any(r['id'] == result['rollbackId'] for r in statebackup.listing())


def test_active_backend_refuses_restore(state):
    snapshot = statebackup.create()
    with statebackup.service_lease():
        with pytest.raises(RuntimeError, match='Stop the dashboard'):
            statebackup.restore(snapshot['id'])


def test_corrupt_archive_is_refused_before_state_changes(state):
    snapshot = statebackup.create()
    path = statebackup.root() / (snapshot['id'] + '.tar.gz')
    path.write_bytes(b'corrupt')
    db.reset_for_tests()
    before = Path(db.DB_PATH).read_bytes()
    with pytest.raises(Exception):
        statebackup.restore(snapshot['id'])
    assert Path(db.DB_PATH).read_bytes() == before


def test_interrupted_state_restore_blocks_boot_and_can_recover(state, monkeypatch):
    import os
    snapshot = statebackup.create()
    accounts.create_user('after', 'after-snapshot-password')
    db.reset_for_tests()
    pid = os.fork()
    if pid == 0:
        original = savefiles.atomic_write
        def crash(path, data):
            original(path, data)
            if path == str(Path(db.DB_PATH).absolute()):
                os._exit(79)
        savefiles.atomic_write = crash
        statebackup.restore(snapshot['id'])
        os._exit(80)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 79
    with pytest.raises(RuntimeError, match='interrupted'):
        with statebackup.service_lease():
            pytest.fail('backend started before recovery')
    assert statebackup.recover()['recovered']
    with sqlite3.connect(db.DB_PATH) as conn:
        assert conn.execute('SELECT count(*) FROM users').fetchone()[0] == 2


def test_restore_revokes_filesystem_exports_even_when_account_ids_are_reused(state, monkeypatch):
    import exportidentity
    import serverexport
    root = state / 'exports'
    root.mkdir()
    monkeypatch.setattr(serverexport, '_base', lambda: root)
    generation = exportidentity.current()
    snapshot = statebackup.create()
    old_owner = accounts.create_user('later-owner', 'later-owner-password', role='owner')
    owner = accounts.get_user('owner')
    for ident, account in (('a' * 32, owner), ('b' * 32, old_owner)):
        folder = root / ident
        folder.mkdir()
        (folder / 'plan.json').write_text(json.dumps({'ownerId': account['id'], 'ownerGeneration': generation}))
        assert serverexport._plan(account['id'], ident)
    db.close_connection()
    statebackup.restore(snapshot['id'])
    replacement = accounts.create_user('replacement-owner', 'replacement-password', role='owner')
    assert replacement['id'] == old_owner['id']
    assert exportidentity.current() != generation
    for ident, account in (('a' * 32, owner), ('b' * 32, replacement)):
        with pytest.raises(serverexport.ServerExportError, match='earlier dashboard database'):
            serverexport._plan(account['id'], ident)
    # A legacy plan with no generation is refused, never silently adopted.
    (root / ('a' * 32) / 'plan.json').write_text(json.dumps({'ownerId': owner['id']}))
    with pytest.raises(serverexport.ServerExportError, match='earlier dashboard database'):
        serverexport._plan(owner['id'], 'a' * 32)
