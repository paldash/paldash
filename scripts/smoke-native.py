#!/usr/bin/env python3
"""Exercise the shipped native codec with a synthetic, non-game GVAS fixture.

This carries no player data and needs no reference archive. It checks codec
loading plus tagged-property parse/edit/serialize in the actual runtime image;
the private full-world tests remain a separate acceptance gate.
"""
import sys

from palsav.core import compress_gvas_to_sav, decompress_sav_to_gvas
from palsav.gvas import GvasFile


def main():
    tree = GvasFile.load({
        'header': {
            'magic': 1396790855, 'save_game_version': 3,
            'package_file_version_ue4': 522, 'package_file_version_ue5': 1008,
            'engine_version_major': 5, 'engine_version_minor': 1,
            'engine_version_patch': 0, 'engine_version_changelist': 0,
            'engine_version_branch': 'synthetic-ci', 'custom_version_format': 3,
            'custom_versions': [], 'save_game_class_name': 'PaldashSyntheticFixture',
        },
        'properties': {
            'Count': {'type': 'IntProperty', 'value': 7},
            'Label': {'type': 'StrProperty', 'value': 'Synthetic Pal \u2603'},
            'Enabled': {'type': 'BoolProperty', 'value': True},
            'Level': {'type': 'ByteProperty', 'value': {'type': 'None', 'value': 24}},
        },
        'trailer': 'AAAAAA==',
    })
    for count in (7, 11):
        tree.properties['Count']['value'] = count
        raw = tree.write()
        encoded = compress_gvas_to_sav(raw, 0x31)
        assert encoded[8:12] == b'PlM\x31', 'Wrong save container format'
        decoded, kind = decompress_sav_to_gvas(encoded)
        assert decoded == raw and kind == 0x31, 'Native codec changed bytes'
        tree = GvasFile.read(decoded)
        assert set(tree.properties) == {'Count', 'Label', 'Enabled', 'Level'}
        assert tree.properties['Count']['value'] == count
        assert tree.properties['Label']['value'] == 'Synthetic Pal \u2603'
        assert tree.properties['Enabled']['value'] is True
        assert tree.properties['Level']['value']['value'] == 24
        assert tree.trailer == b'\0' * 4
    print(f'Native PlM parse/edit/serialize passed on Python {sys.version.split()[0]}')


if __name__ == '__main__':
    main()
