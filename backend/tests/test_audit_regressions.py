"""Fault cases reproduced during the September security audit."""
import csv
import io
import os
import socket
import threading
import urllib.error
from datetime import timedelta

import pytest
import accounts
import authz
import backup
import backupstore
import lifecycle
import reports
import safety
import savefiles
import settings_ini
import viewcache


def test_guest_disable_is_enforced_in_backend(monkeypatch):
    monkeypatch.setenv('GUEST_VIEW_ENABLED', 'false')
    assert authz.effective_capabilities(None) == set()


def test_rotation_required_account_has_no_privileged_capabilities():
    assert authz.effective_capabilities({'role': 'owner', 'mustChangePassword': True}) == set()


@pytest.mark.parametrize('error', [socket.gaierror('DNS unavailable'), TimeoutError(), OSError('network unavailable')])
def test_network_uncertainty_does_not_prove_server_stopped(monkeypatch, error):
    def fail(*a, **kw): raise error
    monkeypatch.setattr(socket, 'create_connection', fail)
    monkeypatch.setattr(urllib.request, 'urlopen', fail)
    assert safety._probe_tcp().verdict == 'unknown'
    assert safety._probe_rest_api().verdict == 'unknown'


def test_explicit_connection_refusal_remains_stopped(monkeypatch):
    def fail(*a, **kw): raise urllib.error.URLError(ConnectionRefusedError())
    monkeypatch.setattr(urllib.request, 'urlopen', fail)
    assert safety._probe_rest_api().verdict == 'stopped'


def test_missing_pinned_world_never_selects_another(tmp_path, monkeypatch):
    world = tmp_path / 'other'; world.mkdir(); (world / 'Level.sav').write_bytes(b'save')
    monkeypatch.setattr(savefiles, 'SAVE_BASE_DIR', str(tmp_path))
    monkeypatch.setenv('WORLD_GUID', 'missing')
    assert savefiles.get_default_world_dir() is None


def test_atomic_same_size_replacement_invalidates_cached_view(tmp_path):
    path = tmp_path / 'player.sav'; path.write_bytes(b'old')
    before = savefiles._stat_key(str(path))
    assert viewcache.per_file('audit', str(path), path.read_bytes) == b'old'
    savefiles.atomic_write(str(path), b'new')
    assert savefiles._stat_key(str(path)) != before
    assert viewcache.per_file('audit', str(path), path.read_bytes) == b'new'


def test_repeated_shutdown_returns_without_reentering_lock(monkeypatch):
    monkeypatch.setitem(lifecycle._state, 'watching', True)
    done = threading.Event()
    thread = threading.Thread(target=lambda: (lifecycle.note_shutdown(), done.set()), daemon=True)
    thread.start()
    assert done.wait(1), 'repeated shutdown deadlocked'


def test_retention_ceiling_never_drops_recent_rollback_points(monkeypatch):
    backups = [{'id': str(i), 'trigger': 'pre-edit', 'timestamp': backup._utc_now().isoformat(), 'sizeBytes': 1} for i in range(51)]
    monkeypatch.setattr(backup, 'list_backups', lambda: backups)
    result = backup.prune_backups({'maxTotal': 50}, dry_run=True)
    assert result['removed'] == []
    assert result['kept'] == 51


def test_changed_backup_source_refuses_publication(tmp_path, monkeypatch):
    world = tmp_path / 'world'; world.mkdir(); source = world / 'Level.sav'; source.write_bytes(b'old')
    dest = tmp_path / 'backup.tar.gz'; original = backupstore._sha256_file
    def changing_hash(path):
        digest = original(path)
        if str(path) == str(source): source.write_bytes(b'new')
        return digest
    monkeypatch.setattr(backupstore, '_sha256_file', changing_hash)
    with pytest.raises(backupstore.BackupError, match='changed while'):
        backupstore.create_archive(str(world), str(dest))
    assert not dest.exists()
    assert not (tmp_path / 'backup.tar.gz.part').exists()


def test_throttle_retry_after_matches_actual_unlock(fresh_db, monkeypatch):
    now = accounts._now()
    monkeypatch.setattr(accounts, '_now', lambda: now)
    for _ in range(accounts.MAX_ATTEMPTS_PER_USER): accounts.record_attempt('ip', 'user', False)
    with pytest.raises(accounts.RateLimited) as denied: accounts.check_rate_limit('ip', 'user')
    monkeypatch.setattr(accounts, '_now', lambda: now + timedelta(seconds=denied.value.retry_after))
    accounts.check_rate_limit('ip', 'user')


def test_password_verification_does_not_mint_unused_sessions(fresh_db):
    import db
    accounts.create_user('test', 'long-test-password')
    token, user = accounts.authenticate('test', 'long-test-password', issue_session=False)
    assert token == '' and user['username'] == 'test'
    assert db.connect().execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == 0


def test_csv_untrusted_text_cannot_be_a_formula_and_numbers_stay_numeric():
    text = reports._to_csv(['name', 'number'], [['=1+1', -2], [' @SUM(A1)', 7]])
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[1] == ["'=1+1", '-2']
    assert rows[2][0] == "' @SUM(A1)"


@pytest.mark.parametrize('value,kind,raw', [('x\nOptionSettings=(bad)', 'string', '"old"'), ('NaN', 'float', '1.000000'), (1.5, 'int', '1'), ('nope', 'bool', 'True'), ('None,AdminPassword=x', 'enum', 'None')])
def test_invalid_setting_values_are_refused(value, kind, raw):
    with pytest.raises(settings_ini.SettingsError): settings_ini._format(value, kind, raw)


def test_maintenance_lease_serializes_threads_and_allows_nesting(tmp_path, monkeypatch):
    import maintenance
    monkeypatch.setattr(maintenance, 'LOCK_PATH', str(tmp_path/'lease'))
    entered = threading.Event()
    attempted = threading.Event()
    def other():
        attempted.set()
        with maintenance.lease(): entered.set()
    with maintenance.lease():
        with maintenance.lease():
            thread = threading.Thread(target=other)
            thread.start()
            assert attempted.wait(1)
            assert not entered.wait(0.05)
    thread.join(2)
    assert entered.is_set()


def test_rotation_blocks_inline_authenticated_routes(fresh_db, monkeypatch):
    from fastapi.testclient import TestClient
    import main
    monkeypatch.setattr(authz, 'current_user', lambda req: {'id': 1, 'username': 'rotate', 'role': 'owner', 'mustChangePassword': True})
    client = TestClient(main.app)
    assert client.get('/api/privacy/me').status_code == 403
    assert client.get('/api/auth/session').status_code == 200


def test_missing_pinned_world_does_not_borrow_activity_from_another(tmp_path, monkeypatch):
    monkeypatch.setenv('WORLD_GUID', 'missing')
    monkeypatch.setattr(savefiles, 'get_default_world_dir', lambda: None)
    assert safety._probe_save_activity().verdict == 'unknown'


def test_backend_chunked_upload_limit_applies_before_application():
    import asyncio
    from requestlimits import RequestLimits
    called, sent = [], []
    async def downstream(*args): called.append(True)
    incoming = iter([{'type': 'http.request', 'body': b'a'*4096, 'more_body': True}]*3)
    async def receive(): return next(incoming)
    async def send(message): sent.append(message)
    asyncio.run(RequestLimits(downstream)({'type':'http','method':'POST','path':'/api/auth/login'},receive,send))
    assert not called
    assert sent[0]['status']==413
