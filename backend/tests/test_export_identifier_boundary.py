"""Request identifiers never become arbitrary filesystem paths."""
import pytest

import recovery
import serverexport
import soloexport
from backupstore import BackupError


@pytest.mark.parametrize('value', [
    '../' + 'a' * 29,
    '/' + 'a' * 31,
    'a' * 31 + '\\',
    'a' * 31 + '\x00',
    'a' * 31 + '\n',
    'a' * 31 + '\u0430',  # Cyrillic a is not hexadecimal.
    '%2e%2e%2f' + 'a' * 23,
])
def test_player_path_identifiers_refuse_non_hexadecimal_text(value):
    with pytest.raises(soloexport.SoloExportError):
        soloexport._file_uid(value)


@pytest.mark.parametrize('value', [
    '../' + 'a' * 29, '/' + 'a' * 31, 'a' * 31 + '\\',
    'a' * 32 + '\n', 'a' * 31 + '\x00', 'a' * 31 + '\u0430',
    'A' * 32, 'a' * 31, 'a' * 33,
])
def test_export_identifier_is_rejected_before_accessing_storage(value, monkeypatch):
    def forbidden():
        pytest.fail('invalid identifier reached export storage')
    monkeypatch.setattr(serverexport, '_base', forbidden)
    with pytest.raises(serverexport.ServerExportError, match='Invalid export identifier'):
        serverexport._plan(1, value)


def test_player_identifiers_keep_existing_case_dash_and_paste_whitespace_support():
    expected = 'abcdef01-2345-6789-abcd-ef0123456789'
    for value in (expected, expected.upper(), expected.replace('-', '').upper(),
                  ' \t' + expected + '\n'):
        assert soloexport._fmt_uid(value) == expected
        assert soloexport._file_uid(value) == 'ABCDEF0123456789ABCDEF0123456789'


def test_recovery_status_keeps_internal_exception_details_out_of_the_response(monkeypatch, caplog):
    def unreadable():
        raise BackupError('internal diagnostic sentinel /private/location')
    monkeypatch.setattr(recovery, '_read', unreadable)
    result = recovery.status()
    assert result['pending'] is True
    assert 'remain blocked' in result['error']
    assert 'sentinel' not in result['error']
    assert '/private/location' not in result['error']
    assert 'internal diagnostic sentinel' in caplog.text
