"""Verified 1.0 layouts for two models the pinned native reader leaves opaque.

PalBooth trades omit the old helper's debug-name string. DroppedCharacter has
four leading bytes before its stored-record GUID and twelve before its owner.
The reference-world joins independently identify these fields; unknown suffixes
remain bytes, subject to the export's opaque-reference refusal.
"""
from copy import deepcopy

BOOTH = 'PalMapObjectPalBoothModel'
DROPPED = 'PalMapObjectDeathDroppedCharacterModel'
KINDS = {BOOTH: 'PalBooth', DROPPED: 'DroppedCharacter'}


class ConcreteModelError(ValueError):
    pass


def encode(data):
    from palsav.archive import FArchiveWriter
    writer = FArchiveWriter()
    writer.guid(data['instance_id'])
    writer.guid(data['model_instance_id'])
    writer.u32(0)
    if data['format'] == BOOTH:
        writer.i32(len(data['trades']))
        for trade in data['trades']:
            writer.guid(trade['pal_player_uid'])
            writer.guid(trade['pal_instance_id'])
            writer.fstring(trade['cost']['static_id'])
            writer.guid(trade['cost']['world_id'])
            writer.guid(trade['cost']['local_id'])
            writer.u32(trade['cost']['quantity'])
            writer.guid(trade['seller_player_uid'])
    else:
        writer.guid(data['stored_parameter_id'])
        writer.write(data['unknown_middle'])
        writer.guid(data['owner_player_uid'])
    writer.write(data['unknown_suffix'])
    return writer.bytes()


def decode(raw):
    from palsav.archive import FArchiveReader
    from palsav.rawdata import map_concrete_model
    try:
        kind = raw['concrete_model_type']
        if kind not in KINDS:
            raise ValueError('unsupported model')
        original = map_concrete_model.encode_bytes(deepcopy(raw))
        reader = FArchiveReader(original, debug=False)
        data = {'format': kind, 'instance_id': reader.guid(), 'model_instance_id': reader.guid()}
        if reader.u32() != 0:
            raise ValueError('unrecognized layout marker')
        if kind == BOOTH:
            count = reader.i32()
            # Each trade needs at least 88 bytes, plus the fixed 24-byte suffix.
            if count < 0 or count > (len(original) - 64) // 88:
                raise ValueError('invalid trade count')
            data['trades'] = []
            for _ in range(count):
                trade = {'pal_player_uid': reader.guid(), 'pal_instance_id': reader.guid()}
                trade['cost'] = {'static_id': reader.fstring(), 'world_id': reader.guid(),
                                 'local_id': reader.guid(), 'quantity': reader.u32()}
                trade['seller_player_uid'] = reader.guid()
                data['trades'].append(trade)
            data['unknown_suffix'] = reader.read_to_end()
            if len(data['unknown_suffix']) != 24:
                raise ValueError('unrecognized booth suffix')
        else:
            if len(original) != 88:
                raise ValueError('unrecognized dropped-character size')
            data['stored_parameter_id'] = reader.guid()
            data['unknown_middle'] = reader.read(12)
            data['owner_player_uid'] = reader.guid()
            data['unknown_suffix'] = reader.read_to_end()
        if encode(data) != original:
            raise ValueError('native round trip changed bytes')
        return data
    except Exception as error:
        raise ConcreteModelError('Concrete-model identity data could not be verified') from error


def update(raw, data):
    """Keep the pinned writer's representation, without changing unrelated bytes."""
    from palsav.archive import FArchiveReader
    from palsav.rawdata import map_concrete_model
    try:
        encoded = encode(data)
        replacement = map_concrete_model.decode_bytes(FArchiveReader(b'', debug=False), encoded, KINDS[data['format']])
        if map_concrete_model.encode_bytes(deepcopy(replacement)) != encoded:
            raise ValueError('native writer changed bytes')
        raw.clear()
        raw.update(replacement)
    except Exception as error:
        raise ConcreteModelError('Concrete-model identity data could not be encoded') from error


def validate_world(world):
    """Require independent character/record joins before interpreting native IDs."""
    from exportscope import _v
    from soloexport import _uid_str
    if not isinstance(world, dict) or 'MapObjectSaveData' not in world:
        return
    owners = {
        _uid_str(_v(entry, 'key', 'InstanceId')):
        _uid_str(_v(entry, 'value', 'RawData', 'value', 'object', 'SaveParameter', 'value', 'OwnerPlayerUId'))
        for entry in _v(world, 'CharacterSaveParameterMap', 'value', default=[])
    }
    stored = {
        _uid_str(_v(entry, 'ID', 'value', 'ID')): _uid_str(entry.get('LostPlayerUId'))
        for entry in _v(world, 'CharacterParameterStorageSaveData', 'value',
                        'StoredParameterInfoSaveData', 'value', 'values', default=[])
    }
    for obj in _v(world, 'MapObjectSaveData', 'value', 'values', default=[]):
        raw = _v(obj, 'ConcreteModel', 'value', 'RawData', 'value', default={})
        if raw.get('concrete_model_type') not in KINDS:
            continue
        data = decode(raw)
        if data['format'] == BOOTH:
            for trade in data['trades']:
                owner = owners.get(str(trade['pal_instance_id']))
                if not owner or owner != str(trade['seller_player_uid']):
                    raise ConcreteModelError('A market listing could not be joined to its Pal owner')
        else:
            owner = stored.get(str(data['stored_parameter_id']))
            if not owner or owner != str(data['owner_player_uid']):
                raise ConcreteModelError('A dropped character could not be joined to its stored owner')
