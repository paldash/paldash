"""Read the native character payload held by a lost-character storage record.

The outer record names its owner; its RawData uses the same codec as a live
character. Require a byte-exact round trip before using its ownership or item
references. Retained records stay opaque unless known history must be scrubbed.
"""
from copy import deepcopy


class StoredCharacterError(ValueError):
    pass


def _raw(record):
    try:
        raw = record['SaveParameter']['value']['RawData']['value']['values']
        if not isinstance(raw, (bytes, bytearray, list)):
            raise ValueError('not a byte array')
        return raw
    except (KeyError, TypeError, ValueError) as error:
        raise StoredCharacterError('Unsupported stored-character payload') from error


def decode(record):
    from palsav.archive import FArchiveReader
    from palsav.rawdata import character

    try:
        raw = bytes(_raw(record))
        decoded = character.decode_bytes(FArchiveReader(b'', debug=False), raw)
        if decoded.get('trailing_unknown_bytes'):
            raise ValueError('unrecognized native tail')
        if set(decoded['object']) != {'SaveParameter'}:
            raise ValueError('unrecognized native object')
        if not isinstance(decoded['object']['SaveParameter']['value'], dict):
            raise ValueError('unrecognized parameters')
        if character.encode_bytes(deepcopy(decoded)) != raw:
            raise ValueError('native round trip changed bytes')
        return decoded
    except Exception as error:
        raise StoredCharacterError('Stored-character data could not be verified') from error


def update(record, decoded):
    """Replace only the native byte array after a verified metadata change."""
    from palsav.rawdata import character

    original = _raw(record)
    try:
        encoded = character.encode_bytes(deepcopy(decoded))
    except Exception as error:
        raise StoredCharacterError('Stored-character data could not be encoded') from error
    record['SaveParameter']['value']['RawData']['value']['values'] = (
        list(encoded) if isinstance(original, list) else encoded
    )
