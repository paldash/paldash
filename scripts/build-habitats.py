#!/usr/bin/env python3
"""
Where each species spawns — from the game's own spawner tables.

**This replaces `extract-pal-habitats.py`, which was a workaround.** That script
inferred a spawner's roster by intersecting its package name table with the
known species list, because the sheet's properties are cooked with unversioned
property names and cannot be decoded. It was explicit that a hit meant "this
blueprint references this species", *not* "this species spawns here at this
rate" — 348 species, no levels, no counts, no rates.

`DT_PalWildSpawner` and `DT_PalSpawnerPlacement` decode out of the server pak and
say all of it directly. The join:

    placements[]  8,253   {spawnerName, x, y, radius, type, placement}
        |  spawnerName
        v
    spawners{}      420   [{weight, onlyTime, onlyWeather, type,
                            entries: [{speciesId, levelMin, levelMax,
                                       countMin, countMax}]}]

So per species this yields cells, **level range**, group weight, count range and
any time/weather restriction.

WHAT IS AND IS NOT CLAIMED
--------------------------
`weight` is a real relative rate **within one spawner group**, so unlike the
name-table version this may honestly be shown as relative frequency. It is *not*
a global spawn rate: two groups' weights are not comparable, and nothing here
says how often a spawner fires. `weightIsWithinGroup` travels in the bundle so a
caller cannot quietly treat it as one.

The cell representation is kept deliberately. A habitat is an area, and 8,253
coordinates is a scatter plot rather than a map layer.

TWO THINGS NOT TO LOSE
----------------------
- **`isNpc` entries are not Pals.** Merchants and hunters share these tables;
  including them puts them in the Paldeck.
- **Encounter-only forms legitimately have no habitat.** `_Oilrig` and `_Tower`
  variants are placed by encounter logic rather than by world spawners, so
  `HadesBird_Oilrig` having zero cells is correct while `HadesBird` has 132. A
  zero here must not read as missing data.

Usage:  python3 scripts/build-habitats.py [--verify]
Output: backend/data/habitats.json.gz
"""

from __future__ import annotations

import gzip
import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from jsonout import write_json  # noqa: E402

ROOT = os.path.dirname(HERE)
SPAWNS = os.path.join(ROOT, "backend", "data", "spawns.json.gz")
OUT = os.path.join(ROOT, "backend", "data", "habitats.json.gz")

CELL_SIZE = 25600
CONTROLS = (12800, 51200)

# The bar the workaround set. Coverage below this is a regression, not a
# trade-off — see `docs/PLAN.md` §1.4, which makes it the acceptance criterion.
WORKAROUND_SPECIES = 348


def _cell(x: float, y: float, size: int = CELL_SIZE) -> tuple[int, int]:
    return (int(x) // size, int(y) // size)


def build() -> dict:
    with gzip.open(SPAWNS, "rt", encoding="utf-8") as f:
        spawns = json.load(f)

    spawners = spawns["spawners"]
    placements = spawns["placements"]

    cells: dict[str, set] = defaultdict(set)
    levels: dict[str, list] = defaultdict(list)
    weights: dict[str, float] = defaultdict(float)
    counts: dict[str, list] = defaultdict(list)
    restricted: dict[str, set] = defaultdict(set)
    npc_species: set = set()

    unplaced = 0
    for placement in placements:
        groups = spawners.get(str(placement.get("spawnerName") or ""))
        if not groups:
            # A placement naming a spawner the table does not define. Counted
            # and reported rather than dropped silently — a rising number here
            # is the signal that the two tables have diverged.
            unplaced += 1
            continue
        cell = _cell(placement.get("x", 0), placement.get("y", 0))
        for group in groups:
            weight = float(group.get("weight") or 0.0)
            time_of_day = str(group.get("onlyTime") or "Undefined")
            weather = str(group.get("onlyWeather") or "Undefined")
            for entry in group.get("entries") or []:
                species = str(entry.get("speciesId") or "")
                if not species:
                    continue
                if entry.get("isNpc"):
                    npc_species.add(species)
                    continue
                cells[species].add(cell)
                levels[species].append(
                    (int(entry.get("levelMin") or 0), int(entry.get("levelMax") or 0))
                )
                counts[species].append(
                    (int(entry.get("countMin") or 0), int(entry.get("countMax") or 0))
                )
                weights[species] += weight
                if time_of_day != "Undefined":
                    restricted[species].add(time_of_day)
                if weather != "Undefined":
                    restricted[species].add(weather)

    habitats = {}
    for species, cell_set in cells.items():
        level_pairs = levels[species]
        count_pairs = counts[species]
        habitats[species] = {
            "cells": sorted([list(c) for c in cell_set]),
            # The range across every group this species appears in. Reported as
            # a span rather than an average: "levels 12-33" is honest about a
            # species that appears at both ends of the map, where a mean would
            # invent a level nothing spawns at.
            "levelMin": min(p[0] for p in level_pairs),
            "levelMax": max(p[1] for p in level_pairs),
            "countMin": min(p[0] for p in count_pairs),
            "countMax": max(p[1] for p in count_pairs),
            "weight": round(weights[species], 2),
            "groups": len(level_pairs),
            "restrictions": sorted(restricted[species]),
        }

    return {
        "habitats": habitats,
        "cellSize": CELL_SIZE,
        "species": len(habitats),
        # Said in the bundle, not only in this docstring: a caller holding a
        # weight must know it is comparable only inside one spawner group.
        "weightIsWithinGroup": True,
        "npcSpeciesExcluded": sorted(npc_species),
        "placementsWithoutSpawner": unplaced,
        # Kept under the old names so `habitats.summary()` and anything reading
        # it stay meaningful. **The numbers mean something different now and
        # that is the point**: the workaround reported an *attribution rate*
        # (13,440 of 13,851 spawners guessed at), because its whole difficulty
        # was not knowing which species a spawner held. Here every placement
        # that names a defined spawner is attributed exactly, so the figure is
        # a coverage count rather than a success rate.
        "spawnersTotal": len(placements),
        "spawnersMatched": len(placements) - unplaced,
        "source": "DT_PalWildSpawner + DT_PalSpawnerPlacement",
    }


# Forms the workaround found that the real tables do not place, and why that is
# correct rather than a regression. Filled in by `coverage_check`.
_VARIANT_PREFIXES = ("PREDATOR_", "BOSS_", "SUMMON_", "RAID_")


def coverage_check(habitats: dict) -> dict:
    """
    Compare against the workaround, and judge the difference rather than count it.

    **The raw species count is the weaker criterion.** What matters is whether
    anything a player could look up got *worse*, and a form the new data drops is
    harmless when its base species is still covered — `PREDATOR_Gorilla`'s
    habitat is `Gorilla`'s habitat, and the Paldeck already merges variants
    sharing a Paldeck number and unions their ranges.

    Measured 2026-08-05: **zero `PREDATOR_` entries exist in
    `DT_PalWildSpawner`** — predators are placed by a different mechanism
    entirely, which is why the name-table trick saw them and the real tables do
    not. 30 of the 32 dropped forms have their base species covered; the other
    two are `_Quest` variants, which are quest-only encounters with no world
    habitat at all. Same category as `_Oilrig` and `_Tower`.
    """
    previous = os.environ.get("HABITATS_COMPARE_TO")
    if not previous or not os.path.exists(previous):
        return {"compared": False}

    with gzip.open(previous, "rt", encoding="utf-8") as f:
        old = json.load(f).get("habitats") or {}

    present = {k.lower() for k in habitats}

    def base(key: str) -> str:
        upper = key.upper()
        for prefix in _VARIANT_PREFIXES:
            if upper.startswith(prefix):
                return key[len(prefix):]
        return key

    dropped = [k for k in old if k.lower() not in present]
    orphaned = [k for k in dropped if base(k).lower() not in present]
    return {
        "compared": True,
        "dropped": len(dropped),
        # A dropped form whose BASE species is also missing is the only real
        # loss. Anything else still resolves for a player.
        "orphaned": orphaned,
    }


def grid_check(placements) -> dict:
    """The same discriminating test that validated `spawns.json.gz`."""
    occupied = {_cell(p.get("x", 0), p.get("y", 0)) for p in placements}
    out = {}
    for size in (CELL_SIZE, *CONTROLS):
        out[size] = len({_cell(p.get("x", 0), p.get("y", 0), size) for p in placements})
    out["distinctAtReal"] = len(occupied)
    return out


def main() -> int:
    data = build()
    species = data["species"]

    if species < WORKAROUND_SPECIES:
        print(
            f"REFUSING: {species} species against the name-table workaround's "
            f"{WORKAROUND_SPECIES}. Replacing a source with a narrower one is a "
            f"regression however much better its provenance.",
            file=sys.stderr,
        )
        return 2

    coverage = coverage_check(data["habitats"])
    if coverage.get("compared"):
        # Only `_Quest` forms may be orphaned — quest-only encounters that have
        # no world habitat under either source.
        real_loss = [k for k in coverage["orphaned"] if "_Quest" not in k]
        if real_loss:
            print(
                f"REFUSING: {len(real_loss)} species lose their habitat entirely "
                f"and are not covered by a base form: {real_loss[:8]}. That is a "
                f"regression for a player looking one up, whatever the totals say.",
                file=sys.stderr,
            )
            return 3
        print(
            f"   coverage: {coverage['dropped']} forms dropped vs the workaround, "
            f"{len(coverage['orphaned'])} orphaned (all _Quest)",
            file=sys.stderr,
        )

    if data["placementsWithoutSpawner"]:
        print(
            f"   note: {data['placementsWithoutSpawner']} placements name a "
            f"spawner absent from DT_PalWildSpawner",
            file=sys.stderr,
        )

    if "--verify" in sys.argv:
        print(f"verified {species} species, {WORKAROUND_SPECIES} required")
        return 0

    write_json(OUT, data)
    print(f"wrote {OUT}")
    print(f"  {species} species (workaround: {WORKAROUND_SPECIES})")
    print(f"  {len(data['npcSpeciesExcluded'])} NPC entries excluded")
    levels_known = [h for h in data["habitats"].values() if h["levelMax"] > 0]
    print(f"  {len(levels_known)} with a level range — the workaround had none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
