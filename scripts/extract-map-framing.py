#!/usr/bin/env python3
"""Verified map-specific native Vector/Vector2D/soft-object decoding.

Widths are accepted only for this table. Both texture references must match the
installed map assets, and every known travel point must fit its source bounds.
Orientation retains the independent object-position control; precision is not
promoted to ground-truth calibration without matching world/pixel landmarks.
"""
import gzip
import json
import math
from pathlib import Path
import struct

import palpak
import uassettable
from sourceprovenance import archive, canonical_path, digest
from jsonout import write_json

ROOT = Path(__file__).resolve().parent.parent


def build(pak=None):
    pak = pak or palpak.Pak()
    original = uassettable._value
    def native(reader, typ, size, extra):
        count = {'Vector': 3, 'Vector2D': 2}.get(extra.get('struct'))
        if typ == 'StructProperty' and count:
            if size != count * 8:
                raise uassettable.TableError('Unexpected map vector width')
            values = struct.unpack_from('<' + 'd' * count, reader.b, reader.o)
            reader.o += size
            if not all(math.isfinite(v) for v in values):
                raise uassettable.TableError('Nonfinite map vector')
            return dict(zip('xyz', values))
        if typ == 'SoftObjectProperty':
            start = reader.o
            if size != 20:
                raise uassettable.TableError('Unexpected map texture reference width')
            package, obj, subpath = reader.name(), reader.name(), reader.i32()
            if subpath != 0 or reader.o - start != size:
                raise uassettable.TableError('Unsupported map texture subpath')
            return package + '.' + obj
        return original(reader, typ, size, extra)
    uassettable._value = native
    try:
        rows = uassettable.read_table(pak, canonical_path(pak, 'DT_WorldMapUIData'))
    finally:
        uassettable._value = original
    if set(rows) != {'MainMap', 'Tree'}:
        raise ValueError('Map region coverage changed; review the new regions')
    regions = {}
    for row_id, region, texture in [('MainMap', 'palpagos', 'T_WorldMap'), ('Tree', 'worldtree', 'T_TreeMap')]:
        row = rows[row_id]
        if row['minMapTextureBlockSize'] != {'x': 8192, 'y': 8192} or row['mapBlockNum'] != {'x': 1, 'y': 1}:
            raise ValueError('Map texture block layout changed')
        textures = [v['texture'] for v in row['textureDataMap'].values()]
        expected = f'/Game/Pal/Texture/UI/Map/{texture}.{texture}'
        if textures != [expected]:
            raise ValueError('Map table refers to a different texture')
        lo, hi = row['landScapeRealPositionMin'], row['landScapeRealPositionMax']
        if not lo['x'] < hi['x'] or not lo['y'] < hi['y']:
            raise ValueError('Map bounds are inverted')
        regions[region] = {'x1': lo['x'], 'x2': hi['x'], 'y1': lo['y'], 'y2': hi['y'], 'texture': expected, 'sourceRow': row_id}
    main = regions['palpagos']
    sx, sy = 4096 / (main['y2']-main['y1']), -4096 / (main['x2']-main['x1'])
    # Independent fitted Palpagos transform is the control, not a fitted target.
    if abs(sx / .0028463649168173903 - 1) > .01 or abs(sy / -.0028275391990127056 - 1) > .01:
        raise ValueError('Map bounds disagree with the independently fitted control')
    with gzip.open(ROOT / 'backend/data/gamedata.json.gz', 'rt') as handle:
        points = json.load(handle)['fastTravel']
    coverage = {'palpagos': 0, 'worldtree': 0}
    for point in points.values():
        region = 'worldtree' if point['x'] > 300000 else 'palpagos'
        bounds = regions[region]
        if not bounds['x1'] <= point['x'] <= bounds['x2'] or not bounds['y1'] <= point['y'] <= bounds['y2']:
            raise ValueError('A travel point falls outside its source-derived map bounds')
        coverage[region] += 1
    if not all(coverage.values()):
        raise ValueError('Travel-point control lacks one region')
    return {'source': archive(pak), 'table': 'DT_WorldMapUIData', 'rowDigest': digest(rows), 'regions': regions, 'travelPointCoverage': coverage}


if __name__ == '__main__':
    result = build()
    write_json(str(ROOT / 'backend/data/map_framing.json.gz'), result)
    (ROOT / 'src/lib/map-framing.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print('Verified and wrote framing for both map textures')
