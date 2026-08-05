#!/usr/bin/env python3
"""
Field boss spawn points — species, **level** and world position.

`DT_BossSpawnerLoactionData` is the table this project spent months describing as
undecodable. It is not: it holds a natively-serialised `Vector`, and once
`uassettable` learned to skip an unwalkable struct by the length in its own tag
(rather than abandoning the table), everything else in the row came out.

WHAT THIS CORRECTS. AGENTS.md and the README both said "field boss levels are
unavailable… name and artwork are what the data supports; do not invent the
rest." The levels were in this table the whole time, behind a refusal.

THE COUNT IS 90, NOT 159. The table has 159 rows and **69 carry
`CharacterID: "None"`** — unused spawner slots. Counting rows instead of
populated rows is exactly the error that made `BP_LevelObject_TowerLockBarrier`
look like the tower bosses, and this file is not going to repeat it.

THE POSITIONS ARE VERIFIED, not merely plausible
------------------------------------------------
The `Location` struct is 24 bytes — three little-endian doubles — which is an
assumption until something independent agrees with it. The World Partition cell
grid does:

    all 90 positions land on an occupied MainGrid_L0 cell at 25,600 units
    control at 12,800:  22 of 90
    control at 51,200:  83 of 90

Both wrong cell sizes do worse, which is what makes 90/90 evidence rather than a
coincidence — the same test, with the same controls, that pinned the cell size
against the 174 fast-travel points. A misread byte layout does not put 90 points
inside the cells the game ships content for.

RAID BOSSES ARE NOT HERE AND MUST NOT BE EXPECTED. Zero `RAID_` ids appear, and
that is correct: raid bosses are summoned at an altar, not placed in the world,
so a table of *locations* has nothing to say about them. See task #56.

Usage:  python3 scripts/extract-boss-spawners.py [--verify]
Output: backend/data/boss_spawners.json.gz
"""

from __future__ import annotations

import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import palpak            # noqa: E402
import uassettable       # noqa: E402
from jsonout import write_json  # noqa: E402

TABLE_NAME = "DT_BossSpawnerLoactionData.uasset"
OUT = os.path.join(os.path.dirname(HERE), "backend", "data", "boss_spawners.json.gz")

# The measured cell size. Not a guess: see the module docstring's controls.
CELL_SIZE = 25600

# An empty spawner slot. The game ships 69 of them in this table.
UNSET = {"", "None", None}


def occupied_cells(pak) -> set:
    """Every World Partition cell the game ships content for."""
    out = set()
    for path in pak.files:
        m = re.search(r"MainGrid_L0_X(-?\d+)_Y(-?\d+)", path)
        if m:
            out.add((int(m.group(1)), int(m.group(2))))
    return out


def _vector_reader():
    """
    Patch `_value` to decode a 24-byte `Vector` as three doubles.

    Deliberately local rather than added to `uassettable`: that module's job is
    tagged properties, and a natively-serialised struct is a different contract
    whose layout is only trustworthy where something checks it. Here the cell
    grid does. Somewhere else it would not, and a shared decoder would carry the
    trust along with the bytes.
    """
    original = uassettable._value

    def patched(r, typ, size, extra):
        if typ == "StructProperty" and extra.get("struct") == "Vector" and size == 24:
            x, y, z = struct.unpack_from("<3d", r.b, r.o)
            r.o += 24
            return {"x": x, "y": y, "z": z}
        return original(r, typ, size, extra)

    return original, patched


def build(pak=None) -> tuple[list, dict]:
    pak = pak or palpak.Pak()
    path = next((p for p in pak.files if p.endswith(TABLE_NAME)), None)
    if path is None:
        raise SystemExit(f"{TABLE_NAME} is not in this pak — did the game update?")

    original, patched = _vector_reader()
    uassettable._value = patched
    try:
        rows = uassettable.read_table(pak, path)
    finally:
        uassettable._value = original

    cells = occupied_cells(pak)
    bosses, off_grid = [], 0

    for key, row in rows.items():
        species = row.get("CharacterID")
        if species in UNSET:
            continue                      # an unused spawner slot, not a boss
        loc = row.get("Location") or {}
        if "x" not in loc:
            continue

        cell = (int(loc["x"]) // CELL_SIZE, int(loc["y"]) // CELL_SIZE)
        if cell not in cells:
            # Refused rather than shipped. A boss marker in the wrong valley is
            # worse than no marker, and one point outside the grid means the
            # byte layout is not what this script believes.
            off_grid += 1
            continue

        bosses.append({
            "id": str(key),
            "spawnerId": str(row.get("SpawnerID") or ""),
            "speciesId": str(species),
            "level": int(row.get("Level") or 0),
            "x": round(float(loc["x"]), 1),
            "y": round(float(loc["y"]), 1),
            "z": round(float(loc["z"]), 1),
            "cell": list(cell),
        })

    return bosses, {
        "rows": len(rows),
        "placed": len(bosses),
        "unset": sum(1 for r in rows.values() if r.get("CharacterID") in UNSET),
        "offGrid": off_grid,
        "cellSize": CELL_SIZE,
    }


def controls(pak, bosses) -> dict:
    """
    The same cell test at the wrong cell sizes, which is what makes the right
    one evidence. If a control matches as well as 25,600 does, the test is not
    discriminating and the positions are not confirmed by it.
    """
    cells = occupied_cells(pak)
    out = {}
    for size in (12800, 51200):
        out[size] = sum(
            1 for b in bosses
            if (int(b["x"]) // size, int(b["y"]) // size) in cells
        )
    return out


def main() -> int:
    pak = palpak.Pak()
    bosses, stats = build(pak)

    if stats["offGrid"]:
        print(
            f"REFUSING: {stats['offGrid']} position(s) fall outside every "
            "occupied cell. The Vector layout is not what this script assumes, "
            "and a boss marker in the wrong place is worse than none.",
            file=sys.stderr,
        )
        return 2

    ctrl = controls(pak, bosses)
    best_control = max(ctrl.values()) if ctrl else 0
    if best_control >= len(bosses):
        print(
            "REFUSING: a wrong cell size matches as well as the right one, so "
            "the check does not discriminate and proves nothing.",
            file=sys.stderr,
        )
        return 3

    if "--verify" in sys.argv:
        print(f"verified {stats['placed']}/{stats['placed']} on occupied cells "
              f"at {CELL_SIZE}; controls {ctrl}")
        return 0

    write_json(OUT, {"cellSize": CELL_SIZE, "bosses": bosses})
    levels = [b["level"] for b in bosses]
    print(f"wrote {OUT}")
    print(f"  {stats['placed']} placed bosses of {stats['rows']} rows "
          f"({stats['unset']} unused spawner slots)")
    print(f"  levels {min(levels)}-{max(levels)}, "
          f"{len({b['speciesId'] for b in bosses})} distinct species")
    print(f"  all {stats['placed']} on occupied cells at {CELL_SIZE}; "
          f"controls {ctrl} — both worse, which is the point")
    return 0


if __name__ == "__main__":
    sys.exit(main())
