"""
Every boss in one list, with what to bring — and the kinds are not comparable.

`/api/world/bosses` draws 90 field bosses on the map. `/api/world/raidbosses`
lists 19 altar summons. The Progression tab ticks off tower bosses. Nothing put
them side by side and answered *"which of these can I take on, and with what"*,
which is the question somebody actually has.

## Four kinds, and the differences are the reason this is not one list

| Kind | Where | Count | How it starts |
|---|---|---:|---|
| `field` | placed in the world, with a level | 90 | walk up to it |
| `tower` | a fast-travel point named `… Tower Entrance` | 8 | enter the tower |
| `raid` | no world position at all | 19 | summoned at an altar with an item |
| `predator` | roaming, no fixed spawner row | — | encounter |

**A raid boss has no location and that is not missing data.** It is summoned,
so a planner that tried to give one a map pin would be inventing a place. The
payload separates them for the same reason `raidbosses.json.gz` is a separate
bundle rather than rows in the spawner table.

## What "bring this" means, and what it does not

`elements.effectiveness` plus the game's own `DamageElementMatchRate` gives the
one number the files support, so a recommendation is **"your Fire Pals hit this
20% harder"** and never a power score. Two things travel with it:

- **The suggestion is a badge on a species list, not a ranking of your roster.**
  This module names elements; `buildplanner.rank(..., against=…)` is what turns
  that into Pals, and it keeps the un-multiplied figure visible.
- **`incoming` is reported too**, because Fire beating Grass says nothing about
  whether Grass beats you back — the two are not inverses, and a planner that
  showed only the offensive half would send somebody in glass-cannoned.

## What it will not say

- **A recommended level.** A field boss carries its own level and that is
  reported; nothing in any file states what level *you* should be, and the
  obvious "boss level + 5" is a rule of thumb somebody made up.
- **A party size.** No file states one. The task this came from assumed the
  party size differs by kind; what is actually in the data is that raid bosses
  ship a `canModeChange` flag and field bosses do not, which is not the same
  thing.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import elements
import gamedata

logger = logging.getLogger(__name__)

# The eight `… Tower Entrance` fast-travel points are the tower bosses, which is
# the check `gamedata.fast_travel_kind` already performs — reused rather than
# re-derived so the two cannot disagree about what a tower is.
_TOWER_KIND = "tower"


def _species_row(species_id: str) -> dict[str, Any]:
    """Name, elements and icon for a boss's species, alpha prefix intact."""
    entry = gamedata.pal_exact(species_id) or gamedata.pal(species_id) or {}
    return {
        "speciesId": species_id,
        "name": gamedata.character_name(species_id),
        "icon": entry.get("icon"),
        "elements": list(entry.get("elements") or []),
    }


def counters(defender_elements: list) -> dict[str, Any]:
    """
    Which elements beat this boss, and which of its own beat you.

    **Both directions, because they are not inverses.** Fire beats Grass and
    Grass beats Earth, so bringing Fire against a Grass boss is strong *and*
    safe, while bringing Water is neutral both ways — one list could not say
    that, and the missing half is the one that gets somebody killed.
    """
    defenders = [d for d in (defender_elements or []) if d]
    strong = sorted({
        attacker for attacker in elements.game_elements()
        if elements.matchup([attacker], defenders) == "strong"
    })
    risky = sorted({
        victim for victim in elements.game_elements()
        if elements.matchup(defenders, [victim]) == "strong"
    })
    return {
        "bringElements": strong,
        # Your Pals of these elements take the boss's bonus.
        "avoidElements": risky,
        "matchRate": elements.match_rate(),
        # One constant, read from both sides — there is no separate resist
        # coefficient, so this is damage dealt and damage taken, not a score.
        "matchRateAppliesBothWays": True,
    }


def _field_bosses() -> list[dict[str, Any]]:
    out = []
    for row in gamedata.boss_spawners() or []:
        species = str(row.get("speciesId") or "")
        entry = _species_row(species)
        out.append({
            "kind": "field",
            "id": str(row.get("spawnerId") or row.get("id") or ""),
            **entry,
            "level": row.get("level"),
            # A field boss has a verified world position — 90 of 90 land on an
            # occupied streaming cell, with both control cell sizes doing worse.
            "position": {"x": row.get("x"), "y": row.get("y"), "z": row.get("z")},
            "counters": counters(entry["elements"]),
        })
    return out


def _raid_bosses() -> list[dict[str, Any]]:
    out = []
    for summon_id, row in (gamedata.raid_bosses() or {}).items():
        for form in row.get("forms") or []:
            species = str(form.get("speciesId") or "")
            entry = _species_row(species)
            out.append({
                "kind": "raid",
                "id": f"{summon_id}:{species}",
                **entry,
                "level": form.get("level"),
                # NO POSITION, and that is the data rather than a gap: a raid
                # boss is summoned at an altar. Absent, never (0, 0).
                "position": None,
                "summonItemId": row.get("summonItemId") or summon_id,
                "counters": counters(entry["elements"]),
            })
    return out


def _tower_bosses() -> list[dict[str, Any]]:
    """
    The eight towers, from the fast-travel layer that already classifies them.

    Their *species* is not in that layer — a tower entrance is a location, not
    an encounter — so these carry a name and a place and no element. Inventing
    a species from the tower's name is the `TowerLockBarrier` mistake.
    """
    out = []
    for point in gamedata.fast_travel_points() or []:
        if gamedata.fast_travel_kind(str(point.get("name") or "")) != _TOWER_KIND:
            continue
        out.append({
            "kind": "tower",
            "id": str(point.get("id") or point.get("name") or ""),
            "speciesId": "",
            "name": str(point.get("name") or ""),
            "icon": None,
            "elements": [],
            "level": None,
            "position": {"x": point.get("x"), "y": point.get("y"), "z": None},
            # No species means no matchup. Absent rather than an empty
            # recommendation that reads as "bring nothing".
            "counters": None,
        })
    return out


def encounters(kind: str = "", element: str = "",
               max_level: Optional[int] = None) -> dict[str, Any]:
    """
    Every boss, optionally filtered, with counters attached where they exist.

    `element` filters on the boss's OWN element, not on what beats it — "show me
    the Fire bosses" is the question people ask, and answering the other one
    under the same parameter name would be a quiet surprise.
    """
    rows = _field_bosses() + _raid_bosses() + _tower_bosses()

    if kind:
        rows = [r for r in rows if r["kind"] == kind]
    if element:
        wanted = elements.canonical(element)
        rows = [r for r in rows if wanted and wanted in (r.get("elements") or [])]
    if max_level is not None:
        # A boss with no level is KEPT, not filtered out: towers have none and
        # dropping them would make a level filter silently narrow the kinds.
        rows = [r for r in rows
                if r.get("level") is None or int(r["level"]) <= int(max_level)]

    rows.sort(key=lambda r: (r["kind"], r.get("level") or 0, r["name"]))
    return {
        "bosses": rows,
        "counts": {k: sum(1 for r in rows if r["kind"] == k)
                   for k in ("field", "raid", "tower")},
        # Said out loud so a client does not average a level across kinds or
        # look for a raid boss on the map.
        "kindsAreNotComparable": True,
        "raidBossesHaveNoPosition": True,
        "recommendedLevelKnown": False,
        "partySizeKnown": False,
        "chartIsHandEntered": True,
    }
