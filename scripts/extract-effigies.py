#!/usr/bin/env python3
"""
Extract every effigy (Lifmunk Relic) from the game pak, with its world position
and the instance GUID that save files key on.

Effigies are not in a save until a player picks one up — the save records only
*which* have been collected, as GUIDs in `RelicObtainForInstanceFlag`. Where
they are is a property of the level, so it has to come from the pak.

The GUID is the point. Positions alone would let the map show every effigy;
positions *paired with the GUID the save uses* let it show which ones a
particular player has already found.

WHERE THEY LIVE
---------------
All of them are in one World Partition cell:

    .../PL_MainWorld5/_Generated_/MainGrid_L15_X0_Y0_DL961A8730.umap

Grid level 15 is the always-loaded persistent layer, not one of the 7,085
spatial L0 cells. Scanning all 9,977 generated cells finds relic actors in
exactly this one, and that cell contains nothing but relics.

HOW THE PAIRING WORKS
---------------------
The package is cooked with unversioned properties, so property *names* are not
in the stream and a field cannot be looked up by name. What is still plainly
serialised is the **export map** — every object's name, parent, and exact byte
range (`scripts/upackage.py`).

That is enough, because it gives attribution:

    BP_LevelObject_Relic_C_UAID_…   the actor    -> instance GUID at byte 252
      └── DefaultSceneRoot          its component -> the FVector position

An earlier version scanned the whole `.uexp` for coordinate triples and got 86
of them with no way to say which effigy each belonged to. Reading one object's
own bytes, knowing whose they are, gets all of them.

VERIFICATION
------------
Three independent checks, all of which must pass:

- every position must land in a streaming cell the game ships content for
- the GUID must be non-zero
- every effigy a real player has collected must appear in the output — 37 of 37
  on the reference world

The GUID byte order is `u32le`: four little-endian uint32s printed big-endian,
which is what the save's flag keys use.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upackage  # noqa: E402
from palpak import Pak  # noqa: E402

CELL_SIZE = 25600
RELIC_MARKER = b"BP_LevelObject_Relic"
GUID_OFFSET = 252          # measured; constant across all relic actor exports
ZERO_GUID = "0" * 32

WORLD_MIN, WORLD_MAX = -1_100_000, 750_000
Z_LIMIT = 50_000
MIN_ACTOR_SIZE = 300       # smaller relic-named exports are not the actor itself


def occupied_cells(pak: Pak) -> set[tuple[int, int]]:
    out = set()
    for path in pak.files:
        m = re.search(r"MainGrid_L0_X(-?\d+)_Y(-?\d+)", path)
        if m:
            out.add((int(m.group(1)), int(m.group(2))))
    return out


def find_relic_cell(pak: Pak) -> str:
    cells = sorted(f for f in pak.files if "/_Generated_/" in f and f.endswith(".umap"))
    hits = [f for f in cells if RELIC_MARKER in pak.read(f)]
    if not hits:
        raise SystemExit("No cell references BP_LevelObject_Relic — did the game update?")
    hits.sort(key=lambda f: len(pak.read(f)), reverse=True)
    return hits[0]


def read_guid(blob: bytes) -> str | None:
    """The instance GUID, in the byte order save files use for flag keys."""
    if len(blob) < GUID_OFFSET + 16:
        return None
    raw = blob[GUID_OFFSET:GUID_OFFSET + 16]
    return "".join(f"{struct.unpack('<I', raw[i:i + 4])[0]:08X}" for i in range(0, 16, 4))


def read_position(blob: bytes) -> tuple[float, float, float] | None:
    """
    The first plausible world-space FVector in a component's own bytes.

    Unversioned properties mean the transform cannot be addressed by name, but
    scanning a single component's ~100 bytes is a different proposition from
    scanning 740 KB: there is only one thing here that can look like a world
    position, and the caller verifies it against the streaming grid anyway.
    """
    for off in range(0, max(0, len(blob) - 24)):
        x, y, z = struct.unpack_from("<ddd", blob, off)
        if (WORLD_MIN < x < WORLD_MAX and WORLD_MIN < y < WORLD_MAX
                and -Z_LIMIT < z < Z_LIMIT and abs(x) > 1000 and abs(y) > 1000):
            return x, y, z
    return None


def extract(pak: Pak) -> dict:
    cell_path = find_relic_cell(pak)
    package = upackage.read(pak.read(cell_path))
    uexp = pak.read(cell_path[:-5] + ".uexp")
    grid = occupied_cells(pak)

    children: dict[int, list] = {}
    for export in package.exports:
        outer = export.outer_export
        if outer is not None:
            children.setdefault(outer, []).append(export)

    effigies: dict[str, dict] = {}
    skipped = {"noGuid": 0, "noPosition": 0, "offGrid": 0, "duplicate": 0}

    for actor in package.exports:
        if "Relic" not in actor.name or actor.size < MIN_ACTOR_SIZE:
            continue

        guid = read_guid(actor.data(uexp))
        if not guid or guid == ZERO_GUID:
            skipped["noGuid"] += 1
            continue

        position = None
        for child in children.get(actor.index, []):
            if child.name == "DefaultSceneRoot":
                position = read_position(child.data(uexp))
                if position:
                    break
        if not position:
            skipped["noPosition"] += 1
            continue

        x, y, z = position
        cell = (int(x) // CELL_SIZE, int(y) // CELL_SIZE)
        if cell not in grid:
            skipped["offGrid"] += 1
            continue
        if guid in effigies:
            skipped["duplicate"] += 1
            continue

        effigies[guid] = {
            "guid": guid,
            "kind": actor.name.split("_C_UAID")[0],
            "x": round(x, 1), "y": round(y, 1), "z": round(z, 1),
            "landmass": "worldtree" if x > 300_000 else "palpagos",
        }

    return {
        "source": cell_path,
        "cellSize": CELL_SIZE,
        "count": len(effigies),
        "skipped": skipped,
        "effigies": sorted(effigies.values(), key=lambda e: (e["landmass"], e["x"])),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pak", default=None)
    parser.add_argument("--out", default="", help="write JSON here")
    parser.add_argument("--verify-against", default="",
                        help="a world directory; checks every collected effigy is present")
    args = parser.parse_args()

    result = extract(Pak(args.pak) if args.pak else Pak())
    print(f"source:   {result['source'].split('/')[-1]}", file=sys.stderr)
    print(f"effigies: {result['count']}", file=sys.stderr)
    by_land: dict[str, int] = {}
    for e in result["effigies"]:
        by_land[e["landmass"]] = by_land.get(e["landmass"], 0) + 1
    print(f"          {by_land}", file=sys.stderr)
    if any(result["skipped"].values()):
        print(f"skipped:  {result['skipped']}", file=sys.stderr)

    if args.verify_against:
        import glob
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))
        from parser import extract_player_progress, load_gvas  # noqa: E402

        collected: set[str] = set()
        for path in sorted(glob.glob(os.path.join(args.verify_against, "Players", "*.sav"))):
            gvas = load_gvas(path)
            if gvas:
                collected.update(extract_player_progress(gvas)["effigies"]["keys"])
        known = {e["guid"] for e in result["effigies"]}
        missing = collected - known
        print(f"verify:   {len(collected - missing)}/{len(collected)} collected effigies "
              f"are in the extracted set", file=sys.stderr)
        if missing:
            print(f"MISSING:  {sorted(missing)[:5]}", file=sys.stderr)
            return 1

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=1)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        json.dump(result, sys.stdout, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
