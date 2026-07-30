#!/usr/bin/env python3
"""
Where each Pal species spawns in the world — the data behind the Paldeck's
habitat map.

    python3 scripts/extract-pal-habitats.py --out backend/data/habitats.json.gz

WHY THIS NEEDED A DETOUR
------------------------
The obvious source is the 13,851 `BP_PalSpawner_*` actors placed in the World
Partition cells, which `extract-world-objects.py` already locates. But their
class names name a **spawner sheet**, not a species:

    BP_PalSpawner_Sheets_2_1_forest_1
    BP_PalSpawner_Sheets_snow_5_1_snow_1
    BP_PalSpawner_Sheets_yamijima_7_5_RedArea_South

Which species a sheet spawns lives in the blueprint's property data, and
Palworld's packages are cooked with **unversioned properties** — property names
are absent from the stream, so decoding them is off the table (see
`scripts/upackage.py`).

The reference archive does not help either: `characters.json` carries stats,
elements, work suitabilities and partner skills, but nothing about habitat.

THE WAY THROUGH
---------------
A package's **name table** is plainly serialised even when its properties are
not. Every FName the blueprint references appears there — including the species
assets it spawns. So reading `BP_PalSpawner_Sheets_2_1_forest_1.uasset`'s name
table and intersecting it with the known species list yields:

    CatMage, CuteButterfly, Eagle, FlowerRabbit, HadesBird,
    LittleBriarRose, MimicDog, NaughtyCat, RobinHood, Werewolf

...which is a forest roster. Same trick as the effigy extractor: the name table
provides attribution when the payload cannot be decoded.

WHAT THIS IS AND IS NOT
-----------------------
A name-table hit means "this blueprint references this species", which is a
slightly broader claim than "this sheet spawns this species at this position".
A sheet may also reference a boss variant, a drop, or a related asset. So the
output is **regions where a species is known to be referenced by the spawners
placed there** — good enough to shade a habitat map, and explicitly not a
spawn-rate table.

`--verify` reports sheets whose name table yields no species at all, which is
what a future game update breaking this would look like.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

import upackage  # noqa: E402
from palpak import Pak  # noqa: E402
from jsonout import write_json  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORLD_OBJECTS = os.path.join(ROOT, "backend", "data", "worldobjects.json.gz")

# Cell size, as everywhere else in this project — measured, not assumed.
CELL_SIZE = 25600


def known_species() -> dict[str, str]:
    """
    Lowercased species id -> the spelling `gamedata` uses.

    **A map, not a set, because the sources disagree on capitalisation** and the
    output has to be keyed the way every consumer spells it. The spawner sheets
    say `SheepBall`, `OctopusGirl`, `SwordCutlassfish`; the game data says
    `Sheepball`, `OctopusGirl`, `SwordCutlassFish`. Writing the sheet's spelling
    left 8 of 348 species unreachable by any lookup — Lamball among them, which
    is how it was noticed. Same failure that `gamedata.py` and `breeding.py`
    were each fixed for; canonicalise at the boundary.
    """
    import gamedata

    return {k.lower(): k for k in (gamedata.load().get("pals") or {})}


def sheet_species(pak: Pak, species: dict[str, str]) -> dict[str, list[str]]:
    """Spawner sheet class name -> the species its package references."""
    out: dict[str, list[str]] = {}
    for path in pak.files:
        if not path.endswith(".uasset") or "/Spawner/" not in path:
            continue
        cls = os.path.splitext(os.path.basename(path))[0]
        if not cls.startswith("BP_PalSpawner_Sheets"):
            continue
        try:
            package = upackage.read(pak.read(path))
        except Exception:      # noqa: BLE001 - one unreadable sheet is not fatal
            continue
        # Canonicalised on the way out, so the bundle is keyed the way
        # gamedata spells it rather than the way this blueprint does.
        found = sorted({species[n.lower()] for n in package.names
                        if n.lower() in species})
        if found:
            out[cls] = found
    return out


def load_spawners() -> list[dict]:
    """Placed spawner actors, from the bundle `extract-world-objects.py` writes."""
    if not os.path.exists(WORLD_OBJECTS):
        raise SystemExit(
            f"{WORLD_OBJECTS} not found. Run:\n"
            "  python3 scripts/extract-world-objects.py "
            "--targets ore,treasure,fishing,oilrig,palspawner,dungeon "
            "--out backend/data/worldobjects.json.gz"
        )
    with gzip.open(WORLD_OBJECTS, "rt", encoding="utf-8") as f:
        data = json.load(f)
    group = (data.get("groups") or {}).get("palspawner") or {}
    objects = group.get("objects") or []
    if not objects:
        raise SystemExit(
            "The world-object bundle has no `palspawner` group. Re-run the "
            "extractor with `palspawner` in --targets."
        )
    return objects


def build(pak: Pak, verify: bool) -> dict:
    species = known_species()
    sheets = sheet_species(pak, species)
    spawners = load_spawners()

    # species -> occupied cells, deduplicated. Cells rather than raw points
    # because a habitat is a region: 13,851 points over ~300 species would be a
    # scatter of dots, while cells shade into an area the way the game's own
    # Paldeck map does.
    cells: dict[str, set[tuple[int, int]]] = defaultdict(set)
    unmatched: Counter[str] = Counter()
    matched_spawners = 0

    for obj in spawners:
        names = sheets.get(obj["cls"])
        if not names:
            unmatched[obj["cls"]] += 1
            continue
        matched_spawners += 1
        cell = (int(obj["x"]) // CELL_SIZE, int(obj["y"]) // CELL_SIZE)
        for name in names:
            cells[name].add(cell)

    habitats = {
        name: {
            "cells": sorted([c, r] for c, r in sorted(points)),
            "spawnerCount": sum(
                1 for o in spawners
                if name in (sheets.get(o["cls"]) or ())
            ),
        }
        for name, points in sorted(cells.items())
    }

    result = {
        "cellSize": CELL_SIZE,
        "sheets": len(sheets),
        "species": len(habitats),
        "spawnersMatched": matched_spawners,
        "spawnersTotal": len(spawners),
        "habitats": habitats,
    }

    print(f"sheets with species:  {len(sheets)}", file=sys.stderr)
    print(f"species with habitat: {len(habitats)}", file=sys.stderr)
    print(f"spawners attributed:  {matched_spawners}/{len(spawners)}", file=sys.stderr)

    if verify and unmatched:
        print(f"\nsheets yielding no species ({len(unmatched)} classes):", file=sys.stderr)
        for cls, n in unmatched.most_common(15):
            print(f"  {n:5}  {cls}", file=sys.stderr)
        print("\nA game update breaking the name-table trick would show up as "
              "this list covering everything.", file=sys.stderr)

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pak", default=None)
    parser.add_argument("--out", default="")
    parser.add_argument("--verify", action="store_true",
                        help="list spawner sheets that yielded no species")
    parser.add_argument("--species", default="",
                        help="print the cells for one species and exit")
    args = parser.parse_args()

    pak = Pak(args.pak) if args.pak else Pak()
    result = build(pak, args.verify)

    if args.species:
        entry = result["habitats"].get(args.species)
        if not entry:
            print(f"No habitat recorded for {args.species!r}", file=sys.stderr)
            return 1
        print(f"{args.species}: {len(entry['cells'])} cells, "
              f"{entry['spawnerCount']} spawners")
        return 0

    if args.out:
        write_json(args.out, result)
        print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
