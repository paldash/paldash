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
    # A COVERAGE CHECK FOUND THESE PATTERNS WERE TOO NARROW
    # ------------------------------------------------------
    # `scripts/` has no standing coverage tool, so this was measured by
    # enumerating every `BP_*_C_UAID_` class in the pak — 916 of them — and
    # asking which no TARGET matched. The misses were not exotic:
    #
    #   PalCrystal_Small, SkyIslandOre, WorldTreeOre, NightStone   ore variants
    #   Mushroom, RedBerry                                          gatherables
    #   BP_PalMapObjectSpawner_Treasure_*                           a SECOND chest family
    #   BP_FishingSpot_*                                            a SECOND fishing family
    #   Lotus_*                                                     stat lotuses
    #   DogCoin, Yakushima_Pot                                      collectibles
    #
    # The treasure one is the instructive miss. `BP_PalMapObjectSpawnerTreasureBox`
    # and `BP_PalMapObjectSpawner_Treasure_…` differ by one underscore and are
    # different families; matching the first looked complete because it returned
    # 8,386 objects. A count that looks plausible is not coverage.
    "ore": {
        "label": "Ore & mineral nodes",
        "pattern": re.compile(
            r"^BP_PalMapObjectSpawner_(Rock\w*|Crystal\w*|PalCrystal\w*|Sulfur\w*|"
            r"Coal\w*|Quartz\w*|SmallStone\w*|log\w*|CaveMushroom\w*|Mushroom\w*|"
            r"Ice\w*|Sand\w*|NightStone\w*|SkyIslandOre\w*|WorldTreeOre\w*|"
            r"RedBerry\w*|Jade\w*)$"
        ),
    },
    "treasure": {
        "label": "Treasure chests",
        # Both families, plus the oil-rig crates, which are their own class again.
        "pattern": re.compile(
            r"^(BP_PalMapObjectSpawnerTreasureBox\w*|BP_PalMapObjectSpawner_Treasure\w*|"
            r"BP_OilrigTreasureBoxSpawner\w*)$"
        ),
        "prefixes": ["BP_PalMapObjectSpawnerTreasureBox",
                     "BP_PalMapObjectSpawner_Treasure",
                     "BP_OilrigTreasureBoxSpawner"],
    },
    # Stat-boosting lotuses: Attack, HP, Stamina, Work Speed, Weight. The class
    # name carries both the stat and the drop chance
    # (`Lotus_Attack_01_40percent`), so the per-kind filter separates them.
    "lotus": {
        "label": "Stat lotuses",
        "pattern": re.compile(r"^BP_PalMapObjectSpawner_Lotus_\w+$"),
        "prefixes": ["BP_PalMapObjectSpawner_Lotus"],
    },
    # Airdropped supply crates. Their spawn points are fixed even though what
    # lands in them is not.
    "supply": {
        "label": "Supply drop points",
        "pattern": re.compile(r"^BP_SupplySpawner_\w+$"),
        "prefixes": ["BP_SupplySpawner_"],
    },
    # Dog coins and the Sakurajima pots — collectibles rather than resources.
    "collectible": {
        "label": "Coins & pots",
        "pattern": re.compile(r"^BP_PalMapObjectSpawner_(DogCoin\w*|Yakushima_Pot\w*)$"),
        "prefixes": ["BP_PalMapObjectSpawner_DogCoin", "BP_PalMapObjectSpawner_Yakushima_Pot"],
    },
    "dungeon": {
        "label": "Dungeons",
        "pattern": re.compile(r"^BP_Dungeon\w*$"),
    },
    "fishing": {
        "label": "Fishing spots",
        # Two families again: `FishingJunkSpot` is the junk you fish up,
        # `BP_FishingSpot_*` is where you actually fish.
        "pattern": re.compile(r"^(BP_MapObject_FishingJunkSpot\w*|BP_FishingSpot_\w+)$"),
        "prefixes": ["BP_MapObject_FishingJunkSpot", "BP_FishingSpot_"],
    },
    "palspawner": {
        "label": "Pal spawners",
        # Field bosses excluded explicitly rather than by target ordering — see
        # `fieldboss` above.
        "pattern": re.compile(r"^BP_PalSpawner(?!_Sheets_\w*FBOSS)\w*$"),
    },
    "oilrig": {
        "label": "Oil fields",
        "pattern": re.compile(r"^BP_LevelObject_Oil\w*$"),
    },
    # Skill fruit trees, and the affection fruit that raises a Pal's bond.
    #
    # Spotted because a community map showed them and this one did not — worth
    # recording that the fix was to extract them from the same pak everything
    # else comes from, not to copy someone's marker data. Eight biome variants,
    # which the per-kind filter keeps separable ("only World Tree fruit").
    "skillfruit": {
        "label": "Skill & affection fruit",
        "pattern": re.compile(r"^BP_PalMapObjectSpawner_(SkillFruits_\w+|AffectionFruit)$"),
        "prefixes": ["BP_PalMapObjectSpawner_SkillFruits", "BP_PalMapObjectSpawner_AffectionFruit"],
    },
    # Ground junk piles. Distinct from `fishing`, which is
    # `BP_MapObject_FishingJunkSpot` — those are fished, these are walked up to.
    "junk": {
        "label": "Junk piles",
        "pattern": re.compile(r"^BP_PalMapObjectSpawner_Junk_\w+$"),
        "prefixes": ["BP_PalMapObjectSpawner_Junk"],
    },
    # The alpha Pals that drop Ancient Technology Points.
    #
    # They were already extracted — as 99 of the 13,851 `palspawner` placements,
    # indistinguishable from ordinary spawn points and therefore unfindable. They
    # are their own category because they are their own *thing*: a fixed, named,
    # once-per-world encounter rather than a respawning population.
    #
    # `palspawner` below carries a negative lookahead for the same pattern, so an
    # object lands in exactly one group regardless of the order `--targets` names
    # them in. Relying on first-match-wins would have made the split depend on a
    # command line.
    "fieldboss": {
        "label": "Field bosses",
        "pattern": re.compile(r"^BP_PalSpawner_Sheets_\w*FBOSS\w*$"),
        "prefixes": ["BP_PalSpawner_Sheets"],
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


def name_field_bosses(pak: Pak, objects: list[dict]) -> dict[str, str]:
    """
    Field-boss spawner sheet -> the boss species it spawns.

    The same name-table trick `extract-effigies.py` uses, and it works for
    the same reason: a package's properties are cooked with unversioned names and
    cannot be decoded, but its **name table is plainly serialised**. Intersecting
    a sheet's name table with the known species list gives what it references.

    **The `BOSS_` prefix is the verification, not the search key.** These sheets
    were found by their `FBOSS` class name, which is a naming convention and
    could mean anything. That 71 of 73 independently resolve to a species the
    game data spells `BOSS_…` is what confirms the convention means what it looks
    like. A sheet resolving to no boss is left unnamed rather than guessed at.

    A sheet often names two species — the boss and its minion or base form
    (`BOSS_QueenBee` + `SoldierBee`, `BOSS_KingAlpaca` + `Alpaca`). The prefixed
    one is the encounter; the other is scenery.
    """
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "backend"))
    try:
        import gamedata
    except Exception as e:      # noqa: BLE001 - naming is a bonus, not the data
        print(f"warning: cannot name field bosses ({e})", file=sys.stderr)
        return {}

    species = {k.lower(): k for k in (gamedata.load().get("pals") or {})}
    wanted = {o["cls"] for o in objects}
    names: dict[str, str] = {}

    for path in pak.files:
        if not path.endswith(".uasset") or "/Spawner/" not in path:
            continue
        cls = os.path.splitext(os.path.basename(path))[0]
        if cls not in wanted:
            continue
        try:
            package = upackage.read(pak.read(path))
        except Exception:       # noqa: BLE001 - one unreadable sheet is not fatal
            continue
        found = sorted({species[n.lower()] for n in package.names
                        if n.lower() in species})
        boss = next((f for f in found if f.lower().startswith("boss_")), None)
        if boss:
            names[cls] = boss
    return names


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

    # Field bosses carry the species they spawn, because "BP_PalSpawner_Sheets_
    # 81_1_grass_FBOSS_23" on a map popup tells a player nothing and "Kirin"
    # tells them everything.
    if "fieldboss" in wanted and found["fieldboss"]:
        boss_names = name_field_bosses(pak, found["fieldboss"])
        for obj in found["fieldboss"]:
            species = boss_names.get(obj["cls"])
            if species:
                obj["species"] = species
        print(f"named {len(boss_names)} of "
              f"{len({o['cls'] for o in found['fieldboss']})} field boss sheets",
              file=sys.stderr)

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
