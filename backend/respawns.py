"""
Respawn-timer map pins (#141): which gatherables are currently regrowing,
and where.

The two halves were always known separately and never joined. The save's
`MapObjectSpawnerInStageSaveData` records a respawn clock per spawner, keyed
by a level-object instance id that resolves against nothing in the save; the
pak knows where every node stands and, until now, carried no id. The bundle's
`guid` field closes it: `extract-world-objects.py` reads each gatherable
actor's instance GUID out of its L0 streaming cell, and on refworld
**30,708 of the save's 31,774 spawner keys resolve to a bundled position**
(96.6%) — the same class of evidence as the 157/157 fast-travel fit, two
independent readers landing on one id space.

Only PENDING timers become pins. A node standing has no timer, a due timer
respawns the moment a player streams the area in (985 of those on refworld —
noise as pins), and DateTime.MaxValue means never. The counts keep every
state visible so a quiet layer reads as a quiet world, not a broken join.

Durations are GAME time, deliberately. Game time only advances while the
server runs, so a wall-clock ETA computed from a parse would drift the
moment the server idled; `inGameHours` plus the parse timestamp is what the
data supports.
"""

from __future__ import annotations

from typing import Any, Optional

import savecache
import viewcache
import worldobjects

#: One game hour in .NET ticks (100 ns).
_TICKS_PER_GAME_HOUR = 36_000_000_000


def _guid_index() -> dict[str, dict[str, Any]]:
    """guid -> {x, y, category, cls}, rebuilt when the bundle file changes."""
    def build() -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for category, group in (worldobjects.load().get("groups") or {}).items():
            for obj in group.get("objects") or []:
                guid = obj.get("guid")
                if guid:
                    out[str(guid)] = {
                        "x": obj.get("x"), "y": obj.get("y"),
                        "category": category, "cls": obj.get("cls"),
                    }
        return out
    return viewcache.per_file("respawns:guidIndex", worldobjects.DATA_PATH, build)


def report() -> Optional[dict[str, Any]]:
    """
    The pending-respawn pins, or None when no parsed world exists — a state
    the route turns into 503, never into an empty layer that reads as
    "nothing is respawning".
    """
    data = savecache.get_data() or {}
    state = data.get("respawnState")
    if not isinstance(state, dict) or not state:
        return None

    clock = state.get("clockTicks")
    index = _guid_index()
    pins: list[dict[str, Any]] = []
    unmapped = 0
    for row in state.get("pending") or []:
        where = index.get(str(row.get("id") or ""))
        if where is None:
            # A pending timer with no bundled position: an instanced-stage
            # spawner, a node the ~0.3% locator miss left unGUIDed, or a
            # world edited since the bundle's game build. Counted, never
            # guessed onto the map.
            unmapped += 1
            continue
        ready = row.get("readyTicks")
        hours = (
            round((int(ready) - int(clock)) / _TICKS_PER_GAME_HOUR, 1)
            if isinstance(ready, int) and isinstance(clock, int)
            else None
        )
        pins.append({**where, "inGameHours": hours})

    pins.sort(key=lambda p: (p["inGameHours"] is None, p["inGameHours"]))
    return {
        "pins": pins,
        "pendingUnmapped": unmapped,
        "counts": state.get("counts") or {},
        # The clock the durations were computed against — game time does not
        # advance while the server is stopped, so "as of the last parse" is
        # part of the answer rather than a caveat.
        "clockTicks": clock,
        "note": (
            "Durations are game-hours as of the last parse. A node with a due "
            "or absent timer respawns when a player next approaches; only "
            "nodes with a running clock are pinned."
        ),
    }
