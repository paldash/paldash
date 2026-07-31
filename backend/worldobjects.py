"""
The 51,921 static world objects, served by viewport.

Ore nodes, treasure chests, fishing spots and oil fields extracted from the game
pak (`scripts/extract-world-objects.py`). Positions are static per game build, so
this is bundled data rather than anything read from a save.

**Why the query takes a bounding box.** 51,921 markers is not a rendering
problem to solve on the client — it is a number no map should ever be asked to
draw. Culling has to happen before markers exist, and the cheapest place to do
that is where the data already is. A pan sends one small request instead of the
browser holding and re-filtering the whole set.

**The grid is built once, on first use.** A linear scan of 51,921 objects per pan
is ~10 ms of pure waste; bucketing them into 25,600-unit cells makes a viewport
query proportional to what is *in view*. That cell size is not arbitrary — it is
the game's own World Partition cell size, the same constant that placed the World
Tree landmass, so a bucket corresponds to a streaming cell.

**A capped response says so.** Returning 500 of 3,000 silently would make the map
quietly lie about what is out there, so `truncated` and `inView` travel with the
points and the UI reports them. The cap exists because the honest alternative —
drawing 24,000 circles — freezes the tab.

Nothing here is per-player or privacy-sensitive: these are fixed features of the
world, identical for everyone, and the save is not consulted. `/api/mapobjects`
is the one with player content in it.
"""

from __future__ import annotations

import gzip
import json
import logging
import math
import os
from collections import defaultdict
from typing import Any, Optional

logger = logging.getLogger(__name__)

DATA_PATH = os.environ.get(
    "WORLD_OBJECTS_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "worldobjects.json.gz"),
)

# The game's own World Partition cell size. Reused rather than re-picked so a
# bucket here is one streaming cell there.
CELL_SIZE = 25_600.0

# Above this, a response is truncated. 2,000 canvas circles is already at the
# edge of comfortable; the point of the layer is finding things, not counting them.
MAX_POINTS = 2_000

_data: Optional[dict[str, Any]] = None
_index: Optional[dict[str, dict[tuple[int, int], list[dict]]]] = None


def load() -> dict[str, Any]:
    """The bundle, or an empty one. A missing file degrades the map, never breaks it."""
    global _data
    if _data is not None:
        return _data
    try:
        with gzip.open(DATA_PATH, "rt", encoding="utf-8") as f:
            _data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("World object data unavailable (%s); the layer will be empty", e)
        _data = {"groups": {}, "cellsParsed": 0, "skipped": {}}
    return _data


def reload() -> dict[str, Any]:
    """
    Drop the cached bundle and read it again from disk.

    Regenerating this file after a game update means replacing it on disk, and
    without this the only way to pick that up was restarting the container —
    which is a heavier action than the one that made it necessary. Returns a
    summary so the caller can report what actually loaded rather than just
    claiming success.
    """
    global _data, _index
    _data = None
    _index = None
    data = load()
    groups = data.get("groups") or {}
    return {
        "path": DATA_PATH,
        "loaded": bool(groups),
        "categories": {name: len(g.get("objects") or []) for name, g in groups.items()},
        "total": sum(len(g.get("objects") or []) for g in groups.values()),
    }


def _cell(x: float, y: float) -> tuple[int, int]:
    return (int(math.floor(x / CELL_SIZE)), int(math.floor(y / CELL_SIZE)))


def index() -> dict[str, dict[tuple[int, int], list[dict]]]:
    """
    `{category: {(col, row): [objects]}}`, built once.

    Rebuilding per request would cost more than the linear scan it replaces, which
    is the whole reason this is module state rather than a local.
    """
    global _index
    if _index is not None:
        return _index

    built: dict[str, dict[tuple[int, int], list[dict]]] = {}
    for category, group in (load().get("groups") or {}).items():
        buckets: dict[tuple[int, int], list[dict]] = defaultdict(list)
        for obj in group.get("objects") or []:
            try:
                buckets[_cell(float(obj["x"]), float(obj["y"]))].append(obj)
            except (KeyError, TypeError, ValueError):
                continue        # a malformed row is dropped, not fatal
        built[category] = dict(buckets)
    _index = built
    return _index


def _parse_kinds(
    kinds: Optional[list[str]],
) -> tuple[dict[str, set[str]], Optional[set[str]]]:
    """
    Split a `kinds` list into per-category selections and a global one.

    Two forms, because "which rocks" and "which chests" are separate questions:

        `ore:BP_..._RockCoal`   — applies to that category only
        `ore:`                  — that category, no kinds: show none of it
        `BP_..._RockCoal`       — applies to every category

    A category with no entry is **unfiltered** (`None`), which is deliberately
    distinct from an empty set. Without that distinction, filtering ore to coal
    would also filter chests to nothing, because no chest class is in the list —
    the bug a single flat set produces the moment two categories are on at once.

    The empty form matters too: someone who unticks all 17 ore classes means "no
    ore", and answering with all of it would be the opposite of the request. So a
    bare `ore:` registers an empty *set*, not a class literally named `ore:`.

    The prefixed form exists rather than relying on class names being unique.
    They are today (30 classes, none shared), but a game update could introduce a
    collision and the failure would be silent.
    """
    if not kinds:
        return {}, None

    per_category: dict[str, set[str]] = {}
    global_kinds: set[str] = set()
    for entry in kinds:
        category, sep, cls = entry.partition(":")
        if sep and category:
            selection = per_category.setdefault(category, set())
            if cls:
                selection.add(cls)
        elif entry:
            global_kinds.add(entry)

    return per_category, (global_kinds or None)


def _cell_budget() -> int:
    """
    How many cells it is worth enumerating before scanning buckets instead.

    The total occupied-bucket count across categories: past that, the cell walk
    is doing more work than looking at every bucket there is.
    """
    return max(1, sum(len(buckets) for buckets in index().values()))


def categories() -> list[dict[str, Any]]:
    """What the layer can show, with counts, for building a legend."""
    return [
        {
            "id": name,
            "label": group.get("label") or name,
            "count": int(group.get("count") or 0),
            # The class breakdown is the useful detail: "ore" is 17 different
            # rocks, and a player looking for coal does not want copper.
            "kinds": sorted(
                ({"cls": cls, "count": n} for cls, n in (group.get("byClass") or {}).items()),
                key=lambda k: -k["count"],
            ),
        }
        for name, group in sorted((load().get("groups") or {}).items())
    ]


def totals() -> dict[str, Any]:
    groups = load().get("groups") or {}
    return {
        "objects": sum(int(g.get("count") or 0) for g in groups.values()),
        "categories": len(groups),
        "cellsParsed": load().get("cellsParsed", 0),
        "skipped": load().get("skipped", {}),
        "cellSize": CELL_SIZE,
        "maxPoints": MAX_POINTS,
    }


def query(
    *,
    category: str = "",
    min_x: Optional[float] = None,
    min_y: Optional[float] = None,
    max_x: Optional[float] = None,
    max_y: Optional[float] = None,
    kinds: Optional[list[str]] = None,
    allowed: Optional[set[str]] = None,
    limit: int = MAX_POINTS,
) -> dict[str, Any]:
    """
    Objects inside a world-space box.

    `inView` counts everything that matched before the cap, so a truncated
    response can say what it left out instead of presenting a slice as the whole.

    `allowed` restricts which categories are considered at all — the caller's
    visibility policy. It is applied *before* counting rather than by filtering
    the result afterwards, because a truncated result cannot be recounted: the cap
    has already discarded matches, so post-hoc filtering would either invent an
    `inView` or report one that promises points the viewer may never see.
    """
    limit = max(1, min(int(limit), MAX_POINTS))
    per_category, global_kinds = _parse_kinds(kinds)
    grid = index()

    names = [category] if category else list(grid)
    if allowed is not None:
        names = [name for name in names if name in allowed]
    unbounded = None in (min_x, min_y, max_x, max_y)

    cells: Optional[list[tuple[int, int]]] = None
    if not unbounded:
        # The cells containing the two corners, and everything between, is exactly
        # the set the box overlaps. Cells on the edge are only partly inside it,
        # which is what the precise per-object bounds check below is for.
        c0 = _cell(float(min_x), float(min_y))  # type: ignore[arg-type]
        c1 = _cell(float(max_x), float(max_y))  # type: ignore[arg-type]
        cols = range(min(c0[0], c1[0]), max(c0[0], c1[0]) + 1)
        rows = range(min(c0[1], c1[1]), max(c0[1], c1[1]) + 1)

        # A caller is free to send a box the size of the coordinate space, and
        # enumerating its cells would allocate billions of tuples — this OOM-killed
        # the process the first time it was measured. The grid is only ever a
        # shortcut, so when the box covers more cells than the world actually has,
        # walking the buckets that exist is both cheaper and bounded.
        if len(cols) * len(rows) <= _cell_budget():
            cells = [(col, row) for col in cols for row in rows]

    # Hoisted out of the per-object loop: converting the same four bounds 51,921
    # times is pure overhead in the degenerate whole-world case.
    bounds = (
        None if unbounded
        else (float(min_x), float(min_y), float(max_x), float(max_y))  # type: ignore[arg-type]
    )

    points: list[dict] = []
    in_view = 0

    for name in names:
        buckets = grid.get(name) or {}
        # `None` means this category is unfiltered, which is *not* the same as an
        # empty set — an empty set would be "show none of this category", and a
        # caller that deselected every kind means exactly that.
        wanted = per_category.get(name, global_kinds)
        # Generators, not lists: the fallback path visits every object in the
        # category, and materialising 51,921 of them just to filter them was
        # measurably slower than the linear scan it replaces.
        candidates = (
            (obj for bucket in buckets.values() for obj in bucket)
            if cells is None
            else (obj for cell in cells for obj in buckets.get(cell, ()))
        )
        for obj in candidates:
            if wanted is not None and obj.get("cls") not in wanted:
                continue
            if bounds is not None and not (
                bounds[0] <= obj["x"] <= bounds[2]
                and bounds[1] <= obj["y"] <= bounds[3]
            ):
                continue
            in_view += 1
            if len(points) < limit:
                points.append({**obj, "category": name})

    return {
        "points": points,
        "inView": in_view,
        "returned": len(points),
        "truncated": in_view > len(points),
        "limit": limit,
    }


def reset_for_tests() -> None:
    global _data, _index
    _data = None
    _index = None
