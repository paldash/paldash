"""
Where each Pal species spawns — the data behind the Paldeck's habitat map.

Bundled by `scripts/build-habitats.py` from `DT_PalWildSpawner` and
`DT_PalSpawnerPlacement`: the placement table gives world positions, the spawner
table gives each one's roster with **species, level range, group size and
weight**.

**Regions, not points.** The bundle stores the World Partition cells a species'
spawners occupy rather than 8,253 individual coordinates. A habitat is an area
— that is how the game's own Paldeck draws it — and cells are the resolution the
map layer draws at.

**What a habitat entry claims changed on 2026-08-05, and it is now the strong
version.** This module used to be fed by `scripts/extract-pal-habitats.py`,
which inferred a spawner's roster by intersecting its package name table with
the known species list, because the blueprint's properties are cooked with
unversioned names. That could only ever claim "this blueprint *references* this
species" — 348 species, no levels, no counts, no rates. The DataTables say all
of it outright, so an entry now means "this species spawns in these cells, at
these levels, this many at a time".

**`weight` is still not a global spawn rate.** It is a real relative rate
*within one spawner group*; two groups' weights are not comparable and nothing
here says how often a spawner fires. `weightIsWithinGroup` travels in the bundle
so a caller cannot quietly treat it as one.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

DATA_PATH = os.environ.get(
    "HABITAT_DATA_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "habitats.json.gz"),
)

# Matches the extraction and every other cell figure in this project. Measured:
# at 25,600 all 174 fast-travel points land on an occupied cell.
CELL_SIZE = 25600.0

_data: Optional[dict[str, Any]] = None
_index: Optional[dict[str, str]] = None


def load() -> dict[str, Any]:
    """The bundle, or an empty one. A missing file degrades the view, never breaks it."""
    global _data
    if _data is not None:
        return _data
    try:
        with gzip.open(DATA_PATH, "rt", encoding="utf-8") as f:
            _data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Habitat data unavailable (%s); the Paldeck map will be empty", e)
        _data = {"habitats": {}, "cellSize": CELL_SIZE, "species": 0}
    return _data


def available() -> bool:
    return bool((load().get("habitats") or {}))


def reload() -> dict[str, Any]:
    """Drop the cache and read again. Counterpart to `worldobjects.reload()`."""
    global _data, _index
    _data = None
    _index = None
    data = load()
    return {
        "path": DATA_PATH,
        "loaded": available(),
        "species": len(data.get("habitats") or {}),
    }


def _folded() -> dict[str, str]:
    """
    Lowercased species id -> the key the bundle uses.

    Case-insensitive for the third time in this codebase, and for the same
    reason: the save says `Sheepball`, palcalc says `SheepBall`, and the game
    data has its own spelling again. The bundle is written canonically, but
    callers pass whatever their source gave them.
    """
    global _index
    if _index is None:
        _index = {k.lower(): k for k in (load().get("habitats") or {})}
    return _index


def for_species(species_id: str) -> dict[str, Any]:
    """
    One species' habitat, as world-space rectangles ready to draw.

    Cells are converted here rather than in the browser so there is one
    definition of what a cell covers. Returns empty rather than raising for an
    unknown species: plenty of Pals legitimately have no spawner (bosses, tower
    encounters, anything obtained only by breeding).
    """
    habitats = load().get("habitats") or {}
    key = _folded().get(str(species_id).lower())
    entry = habitats.get(key) if key else None
    if not entry:
        return {"species": species_id, "known": False, "cells": [], "regions": [],
                "spawnerCount": 0, "cellSize": CELL_SIZE,
                # Absent, not zero. An unknown species and a species that spawns
                # at level 0 must not look alike — and `_Oilrig`/`_Tower` forms
                # legitimately land here, so this is the common path.
                "levelMin": None, "levelMax": None,
                "countMin": None, "countMax": None,
                "weight": 0.0, "restrictions": []}

    size = float(load().get("cellSize") or CELL_SIZE)
    regions = [
        {
            "x": col * size,
            "y": row * size,
            "width": size,
            "height": size,
            # Which map image this rectangle belongs on. The two landmasses have
            # separate framings, so a habitat spanning both has to be split by
            # the consumer rather than drawn on one image.
            "landmass": "worldtree" if col * size > 300_000 else "palpagos",
        }
        for col, row in entry.get("cells") or []
    ]
    return {
        "species": key,
        "known": True,
        "cells": entry.get("cells") or [],
        "regions": regions,
        "spawnerCount": entry.get("spawnerCount", 0),
        "cellSize": size,
        # From the real spawner tables, which the name-table workaround could
        # not give: what level this species spawns at, how many at once, and
        # whether it only appears at a time of day or in certain weather.
        #
        # The level range spans every group the species appears in, so it is a
        # SPAN rather than an average — a species found at both ends of the map
        # would otherwise report a middling level nothing actually spawns at.
        "levelMin": entry.get("levelMin"),
        "levelMax": entry.get("levelMax"),
        "countMin": entry.get("countMin"),
        "countMax": entry.get("countMax"),
        # Comparable only inside one spawner group — see `weightIsWithinGroup`.
        "weight": entry.get("weight", 0.0),
        "restrictions": entry.get("restrictions") or [],
    }


def merged(species_ids: list[str]) -> dict[str, Any]:
    """
    One habitat covering several species ids that are the same Paldeck entry.

    Palworld ships location variants — `HadesBird` and `HadesBird_Oilrig`,
    `GrassPanda_Electric` and `GrassPanda_Electric_Tower` — that share a Paldeck
    number and a display name but spawn in different places. Showing only one
    would hide half of where the Pal is actually found, so the ranges are
    unioned. Cells are deduplicated, which matters because variants overlap.
    """
    cells: set[tuple[int, int]] = set()
    spawners = 0
    known = False
    for species_id in species_ids:
        entry = for_species(species_id)
        if not entry["known"]:
            continue
        known = True
        spawners += entry["spawnerCount"]
        cells.update((c, r) for c, r in entry["cells"])

    size = float(load().get("cellSize") or CELL_SIZE)
    ordered = sorted(cells)
    return {
        "species": species_ids[0] if species_ids else "",
        "mergedFrom": list(species_ids),
        "known": known,
        "cells": [[c, r] for c, r in ordered],
        "regions": [
            {
                "x": c * size, "y": r * size, "width": size, "height": size,
                "landmass": "worldtree" if c * size > 300_000 else "palpagos",
            }
            for c, r in ordered
        ],
        "spawnerCount": spawners,
        "cellSize": size,
    }


def summary() -> dict[str, Any]:
    data = load()
    return {
        "species": len(data.get("habitats") or {}),
        "spawnersMatched": data.get("spawnersMatched", 0),
        "spawnersTotal": data.get("spawnersTotal", 0),
        "cellSize": float(data.get("cellSize") or CELL_SIZE),
        "available": available(),
    }
