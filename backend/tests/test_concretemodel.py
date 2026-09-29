"""Wire fixtures are independent of the adapter's encoder and preserve tails."""
from copy import deepcopy
import struct

import pytest

import concretemodel as model
import soloexport

A = '00000010-0000-0000-0000-000000000010'
B = '00000020-0000-0000-0000-000000000020'
PAL = '00000030-0000-0000-0000-000000000030'
RECORD = '00000040-0000-0000-0000-000000000040'
ZERO = '00000000-0000-0000-0000-000000000000'


def wire_guid(value):
    pytest.importorskip('palsav')
    from palsav.archive import UUID
    return UUID.from_str(value).raw_bytes


def wire(kind, owner=A, count=1):
    header = wire_guid(ZERO) * 2 + bytes(4)
    if kind == model.BOOTH:
        trade = (wire_guid(ZERO) + wire_guid(PAL) + struct.pack('<i', 8) + b'DogCoin\0'
                 + wire_guid(ZERO) * 2 + struct.pack('<I', 100) + wire_guid(owner))
        return header + struct.pack('<i', count) + trade * count + bytes(range(24))
    return header + wire_guid(RECORD) + bytes(range(12)) + wire_guid(owner) + bytes(range(8))


def native(kind, data):
    from palsav.archive import FArchiveReader
    from palsav.rawdata import map_concrete_model
    return map_concrete_model.decode_bytes(FArchiveReader(b'', debug=False), data, model.KINDS[kind])


def native_bytes(raw):
    from palsav.rawdata import map_concrete_model
    return map_concrete_model.encode_bytes(deepcopy(raw))


def world_with(kind, raw):
    return {
        'MapObjectSaveData': {'value': {'values': [
            {'ConcreteModel': {'value': {'RawData': {'value': raw}}}},
        ]}},
        'CharacterSaveParameterMap': {'value': [{
            'key': {'InstanceId': PAL},
            'value': {'RawData': {'value': {'object': {'SaveParameter': {'value': {'OwnerPlayerUId': A}}}}}},
        }]},
        'CharacterParameterStorageSaveData': {'value': {'StoredParameterInfoSaveData': {'value': {'values': [
            {'ID': {'value': {'ID': RECORD}}, 'LostPlayerUId': A},
        ]}}}},
    }


@pytest.mark.parametrize('kind', [model.BOOTH, model.DROPPED])
def test_identity_change_preserves_every_other_byte(kind):
    original = wire(kind)
    raw = native(kind, original)
    before = deepcopy(raw)
    assert soloexport._walk_uids(raw, {A: B}, apply=False) == 1
    assert raw == before
    assert soloexport._walk_uids(raw, {A: B}, apply=True) == 1
    # This known fixture has one occurrence at the explicitly typed owner field.
    assert native_bytes(raw) == original.replace(wire_guid(A), wire_guid(B))
    assert soloexport._walk_uids(raw, {A: A}, apply=False) == 0


@pytest.mark.parametrize('kind', [model.BOOTH, model.DROPPED])
def test_swap_visits_native_owner_once(kind):
    raw = native(kind, wire(kind))
    assert soloexport._walk_uids(raw, {A: B, B: A}, apply=True) == 1
    assert native_bytes(raw) == wire(kind, owner=B)


@pytest.mark.parametrize('count', [0, 1, 3])
def test_booth_empty_and_multiple_trades_have_exact_boundaries(count):
    original = wire(model.BOOTH, count=count)
    decoded = model.decode(native(model.BOOTH, original))
    assert len(decoded['trades']) == count
    assert model.encode(decoded) == original


@pytest.mark.parametrize('kind', [model.BOOTH, model.DROPPED])
@pytest.mark.parametrize('change', ['extra', 'short', 'marker'])
def test_unrecognized_layout_refuses(kind, change):
    data = wire(kind)
    if change == 'extra':
        data += b'unknown'
    elif change == 'short':
        data = data[:-1]
    else:
        data = data[:32] + b'\1\0\0\0' + data[36:]
    with pytest.raises(model.ConcreteModelError):
        model.decode(native(kind, data))


@pytest.mark.parametrize('count', [-1, 2147483647])
def test_untrusted_array_count_is_bounded_by_payload(count):
    data = wire(model.BOOTH)
    data = data[:36] + struct.pack('<i', count) + data[40:]
    with pytest.raises(model.ConcreteModelError):
        model.decode(native(model.BOOTH, data))


def test_old_trade_helper_debug_name_layout_is_not_accepted_as_current():
    data = wire(model.BOOTH)
    # The legacy helper adds a debug-name FString between Pal ID and item cost.
    data = data[:72] + struct.pack('<i', 5) + b'name\0' + data[72:]
    with pytest.raises(model.ConcreteModelError):
        model.decode(native(model.BOOTH, data))


@pytest.mark.parametrize('kind', [model.BOOTH, model.DROPPED])
def test_independent_owner_join_is_required(kind):
    raw = native(kind, wire(kind))
    world = world_with(kind, raw)
    model.validate_world(world)
    mismatched = native(kind, wire(kind, owner=B))
    world['MapObjectSaveData']['value']['values'][0]['ConcreteModel']['value']['RawData']['value'] = mismatched
    with pytest.raises(model.ConcreteModelError, match='joined'):
        model.validate_world(world)


def test_unknown_suffix_is_still_checked_for_removed_identity():
    import serverexport
    data = wire(model.BOOTH)
    raw = native(model.BOOTH, data[:-24] + wire_guid(A) + bytes(8))
    assert soloexport._walk_uids(raw, {A: B}, apply=True) == 1
    with pytest.raises(serverexport.ServerExportError, match='opaque'):
        serverexport._assert_absent(raw, {A}, 'copy')
