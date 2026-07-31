#!/usr/bin/env python3
"""
Extract placed world objects — ore nodes, dungeons, treasure, spawners — from the
game pak, with world coordinates.

Generalises what `extract-effigies.py` does for one object type. None of this is
in a save file: the save records what players have *done*, never what the level
contains, so a dashboard that only reads saves can never show an undiscovered
ore vein or a dungeon nobody has entered.

HOW IT WORKS
------------
Actors live in World Partition cell packages. For each cell, the export map
(`scripts/upackage.py`) gives every object's name, parent and byte range; an
actor's position lives on one of its child components. Matching actor class
names against a target's pattern and reading that child gives a position that
can be attributed with certainty rather than guessed at.

**The root component is not always called `DefaultSceneRoot`.** Blueprint
spawners name theirs `Root`. An early version hardcoded the former and found 0
of 34,000 ore nodes while reporting no error at all — every actor simply looked
like it had no position. Children are now searched in order, root-named first.

Every position is verified against the streaming grid: a real coordinate lands
in a cell the game ships content for. Anything else is dropped and counted.

COST
----
This walks ~9,977 cell packages. `--targets` exists so you extract what you need
rather than everything; `--list-classes` shows what is placeable before you
commit to a run.

    python3 scripts/extract-world-objects.py --targets ore,dungeon --out world.json
    python3 scripts/extract-world-objects.py --list-classes --grep Dungeon
"""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upackage  # noqa: E402
from palpak import Pak  # noqa: E402
from jsonout import write_json  # noqa: E402

CELL_SIZE = 25600
WORLD_MIN, WORLD_MAX = -1_100_000, 750_000
Z_LIMIT = 50_000

# Target groups: a label, the actor-class pattern, and a coarse category the map
# can colour by. Patterns match the export's class name prefix.
TARGETS: dict[str, dict] = {
    "ore": {
        "label": "Ore & mineral nodes",
        "pattern": re.compile(
            r"^BP_PalMapObjectSpawner_(Rock\w*|Crystal|PalCrystal|Sulfur|Coal|Quartz|"
            r"SmallStone|log|CaveMushroom|Ice\w*|Sand\w*)$"
        ),
    },
    "treasure": {
        "label": "Treasure chests",
        "pattern": re.compile(r"^BP_PalMapObjectSpawnerTreasureBox\w*$"),
    },
    "dungeon": {
        "label": "Dungeons",
        "pattern": re.compile(r"^BP_Dungeon\w*$"),
    },
    "fishing": {
        "label": "Fishing spots",
        "pattern": re.compile(r"^BP_MapObject_FishingJunkSpot\w*$"),
    },
    "palspawner": {
        "label": "Pal spawners",
        "pattern": re.compile(r"^BP_PalSpawner\w*$"),
    },
    "oilrig": {
        "label": "Oil fields",
        "pattern": re.compile(r"^BP_LevelObject_Oil\w*$"),
    },
    # Merchants, wandering traders and the NPC camps. `Mono` is the game's own
    # prefix for the standing NPC spawners; the camps are the hostile ones.
    "npc": {
        "label": "NPCs & camps",
        "pattern": re.compile(r"^BP_(MonoNPCSpawner\w*|NPCCampSpawner\w*)$"),
        # Explicit, because the derived prefilter cannot handle an alternation
        # at the front: splitting this pattern yields the literal `BP_`, which
        # matches every cell in the game and turns a targeted run into a full
        # 9,978-package walk. It still *works* — it is only ever a pre-filter —
        # so the failure is silent and costs minutes.
        "prefixes": ["BP_MonoNPCSpawner", "BP_NPCCampSpawner"],
    },
    "effigy": {
        "label": "Effigies",
        "pattern": re.compile(r"^BP_(LevelObject_Relic\w*|RelicObject)$"),
    },
}

# TOWERS ARE NOT IN HERE, AND LOOKING FOR THEM COST AN AFTERNOON
# ---------------------------------------------------------------
# There is no `BP_Tower*` world actor. Grepping the pak for "Tower" finds
# fortress set-dressing meshes and `BP_LevelObject_TowerLockBarrier` — which
# looks exactly like the answer and is not. Extracted, it yields 108 objects in
# **64 clusters spread across the whole map**, against a game with 8 tower
# bosses; it is the sealed-door lock minigame, placed at caves and gates.
#
# The tower bosses were already bundled, in the fast-travel table, as the eight
# points named `… Tower Entrance` (`gamedata.fast_travel_kind`). They rendered
# identically to the other 166 points, which is why the map appeared not to show
# them.
#
# The transferable lesson is the count check: a category whose size disagrees
# with what the game has is wrong however plausible its class name reads.
_ACTOR_RE = re.compile(rb"BP_[A-Za-z0-9_]{3,60}_C_UAID_")


def occupied_cells(pak: Pak) -> set[tuple[int, int]]:
    out = set()
    for path in pak.files:
        m = re.search(r"MainGrid_L0_X(-?\d+)_Y(-?\d+)", path)
        if m:
            out.add((int(m.group(1)), int(m.group(2))))
    return out


def read_position(blob: bytes) -> tuple[float, float, float] | None:
    for off in range(0, max(0, len(blob) - 24)):
        x, y, z = struct.unpack_from("<ddd", blob, off)
        if (WORLD_MIN < x < WORLD_MAX and WORLD_MIN < y < WORLD_MAX
                and -Z_LIMIT < z < Z_LIMIT and abs(x) > 1000 and abs(y) > 1000):
            return x, y, z
    return None


def class_of(export_name: str) -> str:
    return export_name.split("_C_UAID")[0]


def list_classes(pak: Pak, needle: str) -> None:
    counts: Counter[str] = Counter()
    for path in sorted(f for f in pak.files if "/_Generated_/" in f and f.endswith(".umap")):
        data = pak.read(path)
        for m in _ACTOR_RE.finditer(data):
            name = m.group().decode()[: -len("_C_UAID_")]
            if needle.lower() in name.lower():
                counts[name] += 1
    print(f"{len(counts)} classes matching {needle!r} (value = cells containing it)")
    for name, n in counts.most_common(60):
        print(f"  {n:>5}  {name}")


def extract(pak: Pak, wanted: list[str]) -> dict:
    patterns = {name: TARGETS[name]["pattern"] for name in wanted}
    # Cheap pre-filter: only parse a cell's export map if its raw bytes mention
    # something we care about. Parsing 9,977 packages we do not need is the
    # difference between seconds and minutes.
    prefilters = {
        name: TARGETS[name].get("prefixes")
        or sorted({p.split("\\w")[0].lstrip("^").split("(")[0]
                   for p in [patterns[name].pattern]})
        for name in wanted
    }
    for name, prefixes in prefilters.items():
        # A prefilter that degenerates to something this short matches every
        # package and silently disables the optimisation. Better to say so than
        # to spend ten minutes wondering why a targeted run walks the whole pak.
        if any(len(p) < 8 for p in prefixes):
            print(f"warning: {name!r} has a weak prefilter {prefixes}; "
                  f"add an explicit 'prefixes' entry to TARGETS", file=sys.stderr)
    grid = occupied_cells(pak)

    found: dict[str, list[dict]] = {name: [] for name in wanted}
    skipped = Counter()
    cells_parsed = 0

    for path in sorted(f for f in pak.files if "/_Generated_/" in f and f.endswith(".umap")):
        raw = pak.read(path)
        relevant = [
            name for name in wanted
            if any(pre.encode() in raw for pre in prefilters[name])
        ]
        if not relevant:
            continue

        try:
            package = upackage.read(raw)
            uexp = pak.read(path[:-5] + ".uexp")
        except Exception:      # noqa: BLE001 - a cell we cannot parse is not fatal
            skipped["unparsable"] += 1
            continue
        cells_parsed += 1

        children: dict[int, list] = {}
        for export in package.exports:
            outer = export.outer_export
            if outer is not None:
                children.setdefault(outer, []).append(export)

        for export in package.exports:
            if "_C_UAID" not in export.name:
                continue
            cls = class_of(export.name)
            for name in relevant:
                if not patterns[name].match(cls):
                    continue
                # Any child may carry the transform. Root components are tried
                # first because that is where it normally is, but the name
                # varies by Blueprint and guessing wrong looks like "this actor
                # has no position" rather than like a bug.
                position = None
                kids = children.get(export.index, [])
                for child in sorted(kids, key=lambda c: 0 if "Root" in c.name else 1):
                    position = read_position(child.data(uexp))
                    if position:
                        break
                if not position:
                    skipped["noPosition"] += 1
                    break
                x, y, z = position
                if (int(x) // CELL_SIZE, int(y) // CELL_SIZE) not in grid:
                    skipped["offGrid"] += 1
                    break
                found[name].append({
                    "cls": cls,
                    "x": round(x, 1), "y": round(y, 1), "z": round(z, 1),
                    "landmass": "worldtree" if x > 300_000 else "palpagos",
                })
                break

    return {
        "cellsParsed": cells_parsed,
        "skipped": dict(skipped),
        "groups": {
            name: {
                "label": TARGETS[name]["label"],
                "count": len(found[name]),
                "byClass": dict(Counter(o["cls"] for o in found[name]).most_common()),
                "objects": found[name],
            }
            for name in wanted
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pak", default=None)
    parser.add_argument("--targets", default="ore,dungeon,treasure",
                        help=f"comma-separated: {', '.join(TARGETS)}, or 'all'")
    parser.add_argument("--out", default="")
    parser.add_argument("--list-classes", action="store_true")
    parser.add_argument("--grep", default="", help="with --list-classes")
    args = parser.parse_args()

    pak = Pak(args.pak) if args.pak else Pak()

    if args.list_classes:
        list_classes(pak, args.grep)
        return 0

    wanted = list(TARGETS) if args.targets == "all" else [
        t.strip() for t in args.targets.split(",") if t.strip()
    ]
    unknown = [t for t in wanted if t not in TARGETS]
    if unknown:
        raise SystemExit(f"Unknown target(s): {', '.join(unknown)}. "
                         f"Known: {', '.join(TARGETS)}")

    result = extract(pak, wanted)
    print(f"cells parsed: {result['cellsParsed']}", file=sys.stderr)
    for name, group in result["groups"].items():
        print(f"  {group['label']:<22} {group['count']:>6}", file=sys.stderr)
    if result["skipped"]:
        print(f"skipped: {result['skipped']}", file=sys.stderr)

    if args.out:
        write_json(args.out, result)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        json.dump(result, sys.stdout, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
