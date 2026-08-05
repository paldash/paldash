#!/usr/bin/env python3
"""
Where every Pal actually spawns: world positions, rosters, level ranges and
weights.

THIS SUPERSEDES THE NAME-TABLE WORKAROUND. `scripts/extract-pal-habitats.py`
intersects a spawner blueprint's name table with the known species list, because
the client pak's properties are unversioned and undecodable. It manages 348
species and 97.0% attribution, and its claim is explicitly narrow: "this
blueprint *references* this species", not "this species spawns here at this
rate". AGENTS.md says in as many words not to present it as a spawn-rate table.

The server pak's properties are tagged, and two tables give the real thing:

    DT_PalSpawnerPlacement  8,253  every spawn point's world Location
    DT_PalWildSpawner       1,691  the roster at each: species, level range,
                                   count range, weight, time and weather

Measured against the workaround: **482 species** here against its 348, and the
levels and weights are real numbers rather than an inference.

THE VERIFICATION, and it is the same one that pinned the field bosses
--------------------------------------------------------------------
`Location` is a natively-serialised `Vector` — 24 bytes, three little-endian
doubles — which is an assumption until something independent agrees. The World
Partition cell grid does:

    all 8,253 positions land on an occupied MainGrid_L0 cell at 25,600 units
    control at 12,800:  3,288 of 8,253
    control at 51,200:  7,791 of 8,253

**Both wrong cell sizes doing worse is what makes it evidence** rather than a
coincidence, and this script refuses to write if a control ever matches as well
as the real size — at that point the test is not discriminating and proves
nothing.

The Vector decoder is local to this script for the reason
`extract-boss-spawners.py` gives: `uassettable`'s contract is tagged properties,
and a natively-serialised struct is a different one, trustworthy only where
something checks it. Here the grid does.

TWO DISCREPANCIES, RECORDED RATHER THAN RESOLVED
------------------------------------------------
**72 FieldBoss placements here against 90 in `boss_spawners.json.gz`.** Those
come from `DT_BossSpawnerLoactionData`, a different table. Neither supersedes the
other on this evidence and this script does not pretend otherwise — assuming one
was a superset is exactly the kind of guess that produced "159 field bosses".
Whoever needs both should check which species each covers first.

**8 of 372 placement spawner names match no roster row.** They are kept, with an
empty roster, because a spawn point whose contents are unknown is still a spawn
point — dropping it would quietly shrink the map.

`DT_PaldexDistributionData` IS NOT USABLE, and it looked like it would be. It
carries `dayTimeLocations` / `nightTimeLocations` per species, which is exactly
the Paldeck habitat map — but the locations are a **natively-serialised struct
array** (`<458 x struct, not tagged>`), so `uassettable` cannot walk them and
there is no independent check available for 365 species x ~458 points. Left
alone deliberately; see the note in `docs/GAMEDATA-SOURCES.md`.

Usage:  python3 scripts/extract-spawns.py [--verify]
Output: backend/data/spawns.json.gz
"""

from __future__ import annotations

import os
import re
import struct
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import palpak            # noqa: E402
import uassettable       # noqa: E402
from jsonout import write_json  # noqa: E402

OUT = os.path.join(os.path.dirname(HERE), "backend", "data", "spawns.json.gz")

CELL_SIZE = 25600
CONTROLS = (12800, 51200)

# Slots per roster row. Fixed-width columns, hence enumerated.
ROSTER_SLOTS = 3

# The game's own unset values, plus `RowName` — a literal placeholder that
# appears in the species column of unused variant rows and is not a species.
UNSET = {"", "None", None, "RowName"}

# The floor the name-table workaround already reaches. Fewer than this means the
# join is wrong, not that the game shrank.
BASELINE_SPECIES = 348


def _enum(value) -> str:
    text = str(value or "")
    return text.rsplit("::", 1)[-1] if "::" in text else text


def occupied_cells(pak) -> set:
    out = set()
    for path in pak.files:
        m = re.search(r"MainGrid_L0_X(-?\d+)_Y(-?\d+)", path)
        if m:
            out.add((int(m.group(1)), int(m.group(2))))
    return out


def _with_vectors(pak, name: str) -> dict:
    """Read a table with `Vector` decoded as three doubles."""
    original = uassettable._value

    def patched(r, typ, size, extra):
        if typ == "StructProperty" and extra.get("struct") == "Vector" and size == 24:
            x, y, z = struct.unpack_from("<3d", r.b, r.o)
            r.o += 24
            return {"x": x, "y": y, "z": z}
        return original(r, typ, size, extra)

    path = next((p for p in pak.files if p.endswith(name + ".uasset")), None)
    if path is None:
        raise SystemExit(f"{name} is not in this pak — did the game update?")

    uassettable._value = patched
    try:
        return uassettable.read_table(pak, path)
    finally:
        uassettable._value = original


def _read(pak, name: str) -> dict:
    path = next((p for p in pak.files if p.endswith(name + ".uasset")), None)
    if path is None:
        raise SystemExit(f"{name} is not in this pak — did the game update?")
    return uassettable.read_table(pak, path)


def build(pak=None) -> tuple[dict, dict]:
    pak = pak or palpak.Pak()

    # ── Rosters, grouped by the spawner they belong to ──
    rosters: dict[str, list] = defaultdict(list)
    for row in _read(pak, "DT_PalWildSpawner").values():
        name = str(row.get("SpawnerName") or "")
        if name in UNSET:
            continue
        entries = []
        for n in range(1, ROSTER_SLOTS + 1):
            species = str(row.get(f"Pal_{n}") or "")
            npc = str(row.get(f"NPC_{n}") or "")
            who = species if species not in UNSET else npc
            if who in UNSET:
                continue
            entries.append({
                "speciesId": who,
                "isNpc": species in UNSET,
                "levelMin": int(row.get(f"LvMin_{n}") or 0),
                "levelMax": int(row.get(f"LvMax_{n}") or 0),
                "countMin": int(row.get(f"NumMin_{n}") or 0),
                "countMax": int(row.get(f"NumMax_{n}") or 0),
            })
        if not entries:
            continue
        rosters[name].append({
            # Relative within this spawner. A 0 means the variant exists but is
            # never picked, which is a real state and is kept.
            "weight": float(row.get("Weight") or 0.0),
            "onlyTime": _enum(row.get("OnlyTime")),
            "onlyWeather": _enum(row.get("OnlyWeather")),
            "type": _enum(row.get("SpawnerType")),
            "entries": entries,
        })

    # ── Placements, with verified world positions ──
    placements = []
    for row in _with_vectors(pak, "DT_PalSpawnerPlacement").values():
        loc = row.get("Location")
        if not isinstance(loc, dict) or "x" not in loc:
            continue
        placements.append({
            "spawnerName": str(row.get("SpawnerName") or ""),
            "type": _enum(row.get("SpawnerType")),
            "placement": _enum(row.get("PlacementType")),
            "x": round(float(loc["x"]), 1),
            "y": round(float(loc["y"]), 1),
            "z": round(float(loc["z"]), 1),
            "radius": float(row.get("StaticRadius") or 0.0),
            "radiusType": _enum(row.get("RadiusType")),
        })

    names = {p["spawnerName"] for p in placements}
    unmatched = sorted(n for n in names if n and n not in rosters)
    species = {
        e["speciesId"]
        for variants in rosters.values()
        for v in variants
        for e in v["entries"]
        if not e["isNpc"]
    }

    return (
        {"cellSize": CELL_SIZE, "spawners": dict(rosters), "placements": placements},
        {
            "rosters": len(rosters),
            "placements": len(placements),
            "species": len(species),
            "unmatched": unmatched,
            "fieldBoss": sum(1 for p in placements if p["type"] == "FieldBoss"),
        },
    )


def grid_check(pak, placements) -> dict:
    cells = occupied_cells(pak)
    out = {}
    for size in (CELL_SIZE, *CONTROLS):
        out[size] = sum(
            1 for p in placements
            if (int(p["x"]) // size, int(p["y"]) // size) in cells
        )
    return out


def main() -> int:
    pak = palpak.Pak()
    data, stats = build(pak)
    placements = data["placements"]

    checks = grid_check(pak, placements)
    real = checks[CELL_SIZE]
    if real != len(placements):
        print(
            f"REFUSING: {len(placements) - real} of {len(placements)} positions "
            f"fall outside every occupied cell at {CELL_SIZE}. The Vector layout "
            "is not what this script assumes, and a spawn marker in the wrong "
            "valley is worse than none.",
            file=sys.stderr,
        )
        return 2

    best_control = max(checks[c] for c in CONTROLS)
    if best_control >= real:
        print(
            "REFUSING: a wrong cell size matches as well as the right one, so "
            "the check does not discriminate and proves nothing.",
            file=sys.stderr,
        )
        return 3

    if stats["species"] < BASELINE_SPECIES:
        print(
            f"REFUSING: {stats['species']} species against the name-table "
            f"workaround's {BASELINE_SPECIES}. A join that covers less than the "
            "thing it replaces is wrong, not an update.",
            file=sys.stderr,
        )
        return 4

    if "--verify" in sys.argv:
        print(f"verified {real}/{len(placements)} on occupied cells at "
              f"{CELL_SIZE}; controls {dict((c, checks[c]) for c in CONTROLS)}")
        print(f"  {stats['species']} species (baseline {BASELINE_SPECIES})")
        return 0

    write_json(OUT, data)
    print(f"wrote {OUT}")
    print(f"  {stats['placements']} spawn points, {stats['rosters']} rosters")
    print(f"  {stats['species']} species — the name-table workaround reaches "
          f"{BASELINE_SPECIES}")
    print(f"  all {real} on occupied cells at {CELL_SIZE}; controls "
          f"{dict((c, checks[c]) for c in CONTROLS)} — both worse, which is the point")
    if stats["unmatched"]:
        print(f"  {len(stats['unmatched'])} spawn points have no roster row and "
              f"are kept with an empty one: {stats['unmatched'][:3]}")
    print(f"  NOTE: {stats['fieldBoss']} FieldBoss placements here against 90 in "
          "boss_spawners.json.gz (DT_BossSpawnerLoactionData). Different tables; "
          "neither is known to supersede the other.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
