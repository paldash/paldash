"""
The egg-move pool per species (#139): which active skills a hatched Pal can
roll beyond its level-up learnset, from `egg_moves.json.gz`.

The bundle is the game's own `DT_WazaMasterTamago` (tamago = egg) checked
against `DT_WazaDataTable.IgnoreRandomInherit` — the extractor refuses unless
every pool move is one the skill table marks randomly inheritable, which held
47 of 47 at extraction. See `scripts/extract-egg-moves.py` for the whole
argument.

WHAT THIS MODULE WILL NOT SAY, because no file states it: how many moves an
egg rolls, at what rate, or that the child inherits its parents' own moves.
`poolOnly: true` travels in the payload for that reason — with no odds shown
anywhere, an unlabelled list otherwise reads as "the child will know these".
"""

from __future__ import annotations

import gzip
import json
import os
from typing import Any, Optional

import gamedata
import viewcache

DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "egg_moves.json.gz")


class EggMovesUnavailable(RuntimeError):
    pass


def _load() -> dict:
    try:
        with gzip.open(DATA_PATH, "rt", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise EggMovesUnavailable(f"egg-move bundle unreadable: {e}") from e


def _pools() -> dict[str, Any]:
    def build() -> dict:
        data = _load()
        # Case-folded index once per bundle stamp: the save writes `Sheepball`
        # where tables write `SheepBall`, the same eight-Pal trap the
        # friendly-name lookups already absorb.
        return {
            "byId": {k.lower(): k for k in data.get("pools", {})},
            "data": data,
        }
    return viewcache.per_file("eggmoves:pools", DATA_PATH, build)


def for_species(species_id: str) -> Optional[dict]:
    """
    The pool for one species, enriched with names — or None when the species
    has no pool, which is a real answer (101 of the 383 forms have none) and
    must not be dressed up as an empty success.

    `BOSS_`/variant ids resolve to the base species, `pal()`'s rule: the pool
    is stored on the boss row but describes the species, and an alpha Anubis
    hatches the same Anubis.
    """
    cleaned = str(species_id or "").split("::")[-1].strip()
    if not cleaned:
        return None
    base = cleaned[5:] if cleaned[:5].upper() == "BOSS_" else cleaned
    index = _pools()
    key = index["byId"].get(base.lower())
    if key is None:
        return None
    moves = []
    for waza in index["data"]["pools"][key]:
        entry = gamedata._lookup("activeSkills", waza) or {}
        moves.append({
            "id": waza,
            "name": entry.get("name") or gamedata.humanize(waza),
            "element": entry.get("element") or "",
            "power": entry.get("power"),
        })
    # Strongest first: someone weighing a breed wants to know the ceiling of
    # what can roll, and 30 alphabetical rows bury it.
    moves.sort(key=lambda m: (-(m["power"] or 0), m["name"]))
    return {
        "species": key,
        "name": gamedata.pal_name(key),
        "moves": moves,
        # The honesty flags the UI must render rather than know:
        "poolOnly": True,
        "note": (
            "The game's own egg-move pool for this species. How many an egg "
            "rolls, and at what rate, is stated in no file."
        ),
    }
