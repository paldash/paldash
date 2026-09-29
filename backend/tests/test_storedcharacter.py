"""Tiny native records exercise the same codec used by real lost Pals."""
from copy import deepcopy

import pytest

import storedcharacter


def make_native_record(owner_uid='00000000-0000-0000-0000-000000000010'):
    pytest.importorskip('palsav')
    from palsav.archive import UUID
    from palsav.rawdata import character

    zero = UUID.from_str('00000000-0000-0000-0000-000000000000')
    owner = UUID.from_str(owner_uid)
    def guid(value):
        return {'type': 'StructProperty', 'struct_type': 'Guid',
                'struct_id': zero, 'id': None, 'value': value}
    decoded = {
        'object': {'SaveParameter': {
            'type': 'StructProperty', 'struct_type': 'PalIndividualCharacterSaveParameter',
            'struct_id': zero, 'id': None,
            'value': {'OwnerPlayerUId': guid(owner), 'LastNickNameModifierPlayerUid': guid(owner)},
        }},
        'unknown_bytes': [0] * 4, 'group_id': zero, 'trailing_bytes': [0] * 4,
    }
    raw = character.encode_bytes(deepcopy(decoded))
    return {'SaveParameter': {'value': {'RawData': {'value': {'values': raw}}}}}


@pytest.fixture
def native_record():
    return make_native_record()


def test_native_decode_is_read_only_and_metadata_update_roundtrips(native_record):
    from palsav.archive import UUID
    original = deepcopy(native_record)
    decoded = storedcharacter.decode(native_record)
    assert native_record == original
    parameter = decoded['object']['SaveParameter']['value']
    owner = str(parameter['OwnerPlayerUId']['value'])
    parameter['LastNickNameModifierPlayerUid']['value'] = UUID.from_str('00000000-0000-0000-0000-000000000000')
    storedcharacter.update(native_record, decoded)
    verified = storedcharacter.decode(native_record)['object']['SaveParameter']['value']
    assert str(verified['OwnerPlayerUId']['value']) == owner
    assert str(verified['LastNickNameModifierPlayerUid']['value']).strip('0-') == ''


def test_unknown_native_tail_refuses(native_record):
    native_record['SaveParameter']['value']['RawData']['value']['values'] += b'unknown'
    with pytest.raises(storedcharacter.StoredCharacterError, match='could not be verified'):
        storedcharacter.decode(native_record)


def test_malformed_native_payload_refuses(native_record):
    native_record['SaveParameter']['value']['RawData']['value']['values'] = b'broken'
    with pytest.raises(storedcharacter.StoredCharacterError, match='could not be verified'):
        storedcharacter.decode(native_record)


def test_legacy_remap_reaches_native_ownership_and_counts_it_once(native_record):
    import soloexport
    source = '00000000-0000-0000-0000-000000000010'
    target = '00000000-0000-0000-0000-000000000020'
    native_record['LostPlayerUId'] = {'struct_type': 'Guid', 'value': source}
    world = {'CharacterParameterStorageSaveData': {'value': {
        'StoredParameterInfoSaveData': {'value': {'values': [native_record]}},
    }}}
    before = deepcopy(world)
    assert soloexport._walk_uids(world, {source: target}, apply=False) == 3
    assert world == before
    assert soloexport._walk_uids(world, {source: target}, apply=True) == 3
    assert soloexport._walk_uids(world, {source: source}, apply=False) == 0
    assert soloexport._walk_uids(world, {target: target}, apply=False) == 3
    assert native_record['LostPlayerUId']['value'] == target
    decoded = storedcharacter.decode(native_record)['object']['SaveParameter']['value']
    assert str(decoded['OwnerPlayerUId']['value']) == target


def test_legacy_swap_does_not_reverse_native_ownership_twice(native_record):
    import soloexport
    a = '00000000-0000-0000-0000-000000000010'
    b = '00000000-0000-0000-0000-000000000020'
    records = [native_record, make_native_record(b)]
    for record, owner in zip(records, (a, b)):
        record['LostPlayerUId'] = {'struct_type': 'Guid', 'value': owner}
    world = {'CharacterParameterStorageSaveData': {'value': {
        'StoredParameterInfoSaveData': {'value': {'values': records}},
    }}}
    assert soloexport._walk_uids(world, {a: b, b: a}, apply=True) == 6
    assert [r['LostPlayerUId']['value'] for r in records] == [b, a]
    assert [str(storedcharacter.decode(r)['object']['SaveParameter']['value']['OwnerPlayerUId']['value']) for r in records] == [b, a]
