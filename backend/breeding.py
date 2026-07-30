"""
Breeding calculator driven by what the players actually own.

Data (backend/data/*.json.gz) is the Palworld 1.0 table extracted from the game's
own CombiRank/BreedingPower tables via the MIT-licensed tylercamp/palcalc
project: all 299 Pals and all 44,850 parent-pair outcomes, special combinations
included. Because it is the full precomputed pair table rather than a
reimplementation of the rank formula, special pairs (Relaxaurus x Sparkit ->
Relaxaurus Lux, etc.) are correct by construction.

Save files store *internal* names (Sparkit is "ElecCat", Relaxaurus is
"LazyDragon"), which is what the pair table is keyed on, so palbox contents map
straight onto it.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
from collections import deque
from functools import lru_cache
from typing import Any, Iterable, Optional

import gamedata

logger = logging.getLogger(__name__)

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_BREEDING_FILE = os.path.join(_DATA_DIR, "pal_breeding.json.gz")
_DB_FILE = os.path.join(_DATA_DIR, "pal_db.json.gz")

# Guard rails for the path search — a full unbounded BFS over 44k pairs would
# happily eat the CPU this dashboard is supposed to stay off.
MAX_PATH_DEPTH = int(os.environ.get("BREEDING_MAX_DEPTH", "4"))
MAX_PATH_FRONTIER = int(os.environ.get("BREEDING_MAX_FRONTIER", "400"))


class BreedingDataError(Exception):
    """Raised when the bundled breeding data is missing or unreadable."""


@lru_cache(maxsize=1)
def _breeding() -> dict[str, Any]:
    if not os.path.exists(_BREEDING_FILE):
        raise BreedingDataError(f"Missing breeding data: {_BREEDING_FILE}")
    with gzip.open(_BREEDING_FILE, "rt", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _db() -> dict[str, Any]:
    if not os.path.exists(_DB_FILE):
        raise BreedingDataError(f"Missing pal database: {_DB_FILE}")
    with gzip.open(_DB_FILE, "rt", encoding="utf-8") as f:
        return json.load(f)


def data_available() -> bool:
    try:
        _breeding()
        _db()
        return True
    except Exception:  # noqa: BLE001
        return False


def _pair_key(a: str, b: str) -> str:
    return "+".join(sorted([a, b]))


# ─── Lookups ─────────────────────────────────────────────────────


_pal_index: Optional[dict[str, str]] = None


def _folded_pals() -> dict[str, str]:
    """
    Lowercased species id -> the key `pal_db` actually stores it under.

    **Lookups here must be case-insensitive, for exactly the reason
    `gamedata.py` is.** The sources disagree on capitalisation: a save stores
    `Sheepball`, `OctopusGirl`, `SwordCutlassfish`, while palcalc spells them
    `SheepBall`, `OctopusGirl` and `SwordCutlassFish`. An exact `dict.get` misses
    those, and the miss is silent — `pal_info` falls back to echoing the internal
    id, so a breeding path renders as "Sheepball + ElecCat" instead of
    "Lamball + Sparkit". That is what this fixes.
    """
    global _pal_index
    if _pal_index is None:
        _pal_index = {key.lower(): key for key in _db()["pals"]}
    return _pal_index


def _icon(internal_name: str) -> str:
    entry = gamedata.pal(internal_name) or gamedata.character(internal_name) or {}
    return str(entry.get("icon") or "")


def canonical_species(internal_name: str) -> str:
    """
    A save's spelling of a species -> the spelling `pal_db` and the pair table
    use. Unchanged when there is no match, so callers can still test membership.

    **Canonicalise at the boundary, not just when rendering a name.** The pair
    table is keyed on palcalc's spelling and `_pair_key` joins raw ids, so a save
    that says `Sheepball` misses every pair involving Lamball. Fixing only
    `pal_info` would leave the display right and the *breeding maths* wrong,
    which is the worse of the two failures.
    """
    pals = _db()["pals"]
    if internal_name in pals:
        return internal_name
    return _folded_pals().get(str(internal_name).lower(), internal_name)


def _find_pal(internal_name: str) -> Optional[dict]:
    return _db()["pals"].get(canonical_species(internal_name))


def pal_info(internal_name: str) -> dict[str, Any]:
    """Display metadata for an internal Pal name, degrading gracefully."""
    info = _find_pal(internal_name)
    if not info:
        # palcalc's table covers breedable species only, so anything else —
        # NPCs, boss forms, a Pal added by an update — legitimately misses. The
        # bundled game database is broader, so ask it before giving up; only
        # then fall back to the humanised id, which still beats `ElecCat`.
        return {
            "internalName": internal_name,
            "name": gamedata.character_name(internal_name) or internal_name,
            "icon": _icon(internal_name),
            "known": False,
        }
    return {
        "internalName": internal_name,
        "name": info["name"],
        # From the bundled game data, which records the path directly. palcalc's
        # table has no artwork of its own.
        "icon": _icon(internal_name),
        "dex": info.get("dex"),
        "isVariant": info.get("variant", False),
        "rarity": info.get("rarity"),
        "breedingPower": info.get("power"),
        "unreleased": bool(info.get("unreleased")),
        "genderOdds": info.get("gender", {"MALE": 0.5, "FEMALE": 0.5}),
        "work": info.get("work", {}),
        "stats": info.get("stats", {}),
        "known": True,
    }


_passive_index: Optional[dict[str, str]] = None


def passive_name(internal_id: str) -> str:
    global _passive_index
    passives = _db().get("passives", {})
    entry = passives.get(internal_id)
    if entry is None:
        # Same case-insensitivity rule as the species lookup above.
        if _passive_index is None:
            _passive_index = {key.lower(): key for key in passives}
        key = _passive_index.get(str(internal_id).lower())
        entry = passives.get(key) if key else None
    if entry and entry.get("name") and not str(entry["name"]).startswith("en Text"):
        return entry["name"]
    # `en Text ...` is palcalc's placeholder for an unlocalised string, so it is
    # no better than the raw id. The bundled game database covers all 1,905
    # passives and is the better answer in both cases.
    return gamedata.passive_name(internal_id) or internal_id


def is_unreleased(internal_name: str) -> bool:
    """
    True for Pals present in the game files but absent from the Paldeck
    (`zukanIndex` of -1).

    The 1.0 breeding tables reference several of these, so their pair data is
    kept — it is correct if they are ever released. They are withheld from the
    planner's target list because offering a goal nobody can obtain is worse
    than not listing it.
    """
    return bool((_find_pal(internal_name) or {}).get("unreleased"))


def all_pals(include_unreleased: bool = False) -> list[dict[str, Any]]:
    return sorted(
        (
            pal_info(name)
            for name in _db()["pals"]
            if include_unreleased or not is_unreleased(name)
        ),
        key=lambda p: (p.get("dex") or 9999, p["name"]),
    )


def predict_child(parent_a: str, parent_b: str) -> Optional[str]:
    """The Pal produced by this pair, or None if the pair cannot breed."""
    return _breeding()["pairs"].get(
        _pair_key(canonical_species(parent_a), canonical_species(parent_b))
    )


# ─── Palbox analysis ─────────────────────────────────────────────


def _breedable(pal: dict) -> bool:
    """Bosses/alphas cannot breed; neither can anything outside the table."""
    if pal.get("isBoss"):
        return False
    species = pal.get("speciesId") or ""
    return canonical_species(species) in _db()["pals"]


def summarize_palbox(pals: Iterable[dict]) -> dict[str, Any]:
    """
    Roll a player's Pals up into species/gender availability, which is what the
    breeding UI needs in order to grey out pairs the player cannot actually make.
    """
    species: dict[str, dict[str, Any]] = {}
    skipped = 0

    for pal in pals:
        if not _breedable(pal):
            skipped += 1
            continue
        # Canonical, so this key matches the pair table's spelling.
        key = canonical_species(pal["speciesId"])
        entry = species.setdefault(
            key,
            {
                **pal_info(key),
                "count": 0,
                "male": 0,
                "female": 0,
                "unknownGender": 0,
                "bestIvs": {"hp": 0, "melee": 0, "shot": 0, "defense": 0},
                "maxLevel": 0,
                "passives": {},
                "individuals": [],
            },
        )
        entry["count"] += 1
        gender = pal.get("gender", "Unknown")
        if gender == "Male":
            entry["male"] += 1
        elif gender == "Female":
            entry["female"] += 1
        else:
            entry["unknownGender"] += 1

        entry["maxLevel"] = max(entry["maxLevel"], pal.get("level", 0))
        for stat, value in (pal.get("ivs") or {}).items():
            if stat in entry["bestIvs"]:
                entry["bestIvs"][stat] = max(entry["bestIvs"][stat], value)

        for passive in pal.get("passiveSkills") or []:
            entry["passives"][passive] = entry["passives"].get(passive, 0) + 1

        entry["individuals"].append(
            {
                "instanceId": pal.get("instanceId"),
                "nickname": pal.get("nickname") or "",
                "gender": gender,
                "level": pal.get("level", 0),
                "rank": pal.get("rank", 1),
                "ivs": pal.get("ivs", {}),
                "passives": [
                    {"id": p, "name": passive_name(p)} for p in (pal.get("passiveSkills") or [])
                ],
            }
        )

    for entry in species.values():
        entry["passives"] = sorted(
            ({"id": p, "name": passive_name(p), "count": c} for p, c in entry["passives"].items()),
            key=lambda x: -x["count"],
        )
        entry["canSelfBreed"] = entry["male"] > 0 and entry["female"] > 0

    return {
        "species": sorted(species.values(), key=lambda s: (s.get("dex") or 9999)),
        "speciesCount": len(species),
        "totalBreedable": sum(s["count"] for s in species.values()),
        "skippedUnbreedable": skipped,
    }


def possible_offspring(pals: Iterable[dict]) -> list[dict[str, Any]]:
    """
    Every child reachable in ONE breeding step from the Pals on hand, honouring
    gender: a pair needs one male and one female, and same-species pairs need
    both genders of that species.
    """
    summary = summarize_palbox(pals)
    species = {s["internalName"]: s for s in summary["species"]}
    names = sorted(species)

    results: dict[str, dict[str, Any]] = {}

    def record(child: str, a: str, b: str) -> None:
        entry = results.setdefault(child, {**pal_info(child), "fromPairs": []})
        entry["fromPairs"].append(
            {"a": species[a]["name"], "b": species[b]["name"], "aId": a, "bId": b}
        )

    for i, a in enumerate(names):
        for b in names[i:]:
            if a == b:
                if not species[a]["canSelfBreed"]:
                    continue
            else:
                # need opposite genders across the two species
                if not (
                    (species[a]["male"] and species[b]["female"])
                    or (species[a]["female"] and species[b]["male"])
                ):
                    continue
            child = predict_child(a, b)
            if child:
                record(child, a, b)

    for entry in results.values():
        entry["owned"] = entry["internalName"] in species
        entry["pairCount"] = len(entry["fromPairs"])
        entry["fromPairs"] = entry["fromPairs"][:12]

    return sorted(results.values(), key=lambda r: (r.get("dex") or 9999))


def _owned_pool(owned_species: Iterable[str]) -> set[str]:
    """Canonicalised owned species that the pair table actually knows."""
    known = _db()["pals"]
    return {c for c in (canonical_species(s) for s in owned_species) if c in known}


def _expand(pool: set[str], max_depth: int) -> tuple[dict[str, tuple[str, str]], dict[str, int]]:
    """
    Breadth-first over the pair table from an owned pool.

    Returns `origin` (child -> the pair that first produced it) and `depth`
    (child -> how many breeding steps deep it first appeared). Because BFS
    visits in generation order and a child is only ever recorded the first time
    it is seen, `depth` is the *shortest* route, not merely a route.

    Shared by the single-target search and the reachability listing so there is
    one implementation of the traversal rather than two that can disagree.
    """
    pairs = _breeding()["pairs"]
    origin: dict[str, tuple[str, str]] = {}
    depth: dict[str, int] = {}
    reached = set(pool)

    for step in range(1, max_depth + 1):
        new: set[str] = set()
        # Bounded: a full expansion over 46,655 pairs would happily eat the CPU
        # the game server needs.
        current = sorted(reached)[:MAX_PATH_FRONTIER]

        for i, a in enumerate(current):
            for b in current[i:]:
                child = pairs.get(_pair_key(a, b))
                if child and child not in reached and child not in new:
                    new.add(child)
                    origin[child] = (a, b)
                    depth[child] = step

        if not new:
            break
        reached |= new

    return origin, depth


def _unwind(target: str, origin: dict[str, tuple[str, str]]) -> list[dict[str, Any]]:
    """
    The pair sequence producing `target`, parents before children.

    Depth-first through `origin`, emitting each step only after both its parents
    have been emitted, so the list can be followed top to bottom.
    """
    steps: list[dict[str, Any]] = []
    seen: set[str] = set()

    def walk(node: str) -> None:
        if node in seen or node not in origin:
            return
        seen.add(node)
        a, b = origin[node]
        walk(a)
        walk(b)
        steps.append({"parentA": pal_info(a), "parentB": pal_info(b), "child": pal_info(node)})

    walk(target)
    return steps


def breeding_paths(target: str, owned_species: list[str], max_depth: int = MAX_PATH_DEPTH) -> dict[str, Any]:
    """
    Shortest breeding route from what you own to a target Pal.

    Gender is ignored here (a route is about species reachability); the
    single-step offspring view is the one that enforces it.
    """
    target = canonical_species(target)
    pool = _owned_pool(owned_species)

    if not pool:
        return {"target": target, "reachable": False, "reason": "No breedable Pals owned", "steps": []}
    if target in pool:
        return {"target": target, "reachable": True, "alreadyOwned": True, "steps": []}

    origin, depth = _expand(pool, max_depth)
    if target not in depth:
        return {
            "target": target,
            "reachable": False,
            "reason": f"Not reachable within {max_depth} breeding steps from your current Pals",
            "steps": [],
        }
    return {
        "target": target,
        "reachable": True,
        "alreadyOwned": False,
        "steps": _unwind(target, origin),
    }


def indirect_targets(owned_species: Iterable[str], max_depth: int = MAX_PATH_DEPTH) -> dict[str, Any]:
    """
    Every Pal reachable by breeding but **not** obtainable in one step.

    The offspring view answers "what can I make right now". This answers the
    question after it: what is within reach if you are willing to breed an
    intermediate first, and what is the shortest way there.

    **`steps` counts breedings, not BFS generations, and those differ.** A Pal
    can appear in generation 2 while needing three pairings, because *both* its
    parents may themselves have to be bred first — Cremis is exactly this. The
    useful number to a player is how many breedings they must perform, so that
    is what is counted, sorted on and labelled. A test pins it against an
    independent route lookup, because reporting "2 steps" for a three-breeding
    plan is the kind of wrong that looks right.

    One-breeding children are excluded — they already have their own view, and
    repeating them here would bury the ones that need a plan.

    Unreleased species are dropped for the same reason `all_pals` withholds
    them: offering a goal nobody can obtain is worse than not listing it. Their
    pair data stays in the table, so they can still appear as an intermediate
    step on the way to something real.
    """
    pool = _owned_pool(owned_species)
    if not pool:
        return {"maxDepth": max_depth, "ownedSpecies": 0, "targets": []}

    origin, depth = _expand(pool, max_depth)

    targets = []
    for species in depth:
        if is_unreleased(species):
            continue
        steps = _unwind(species, origin)
        if len(steps) < 2:
            continue        # obtainable in one breeding; the offspring view has it
        targets.append({**pal_info(species), "depth": len(steps), "steps": steps})

    # Fewest breedings first, then Paldeck order — a two-step target is more
    # useful than a four-step one and should not be buried under it.
    targets.sort(key=lambda t: (t["depth"], t.get("dex") or 9999, t["name"]))

    return {
        "maxDepth": max_depth,
        "ownedSpecies": len(pool),
        "targets": targets,
    }


# ─── Inheritance odds ────────────────────────────────────────────


def inheritance_odds() -> dict[str, Any]:
    """
    How many passives/IVs a child inherits, from the game's own weight tables.

    PassiveInheritanceWeights: how many of the combined parent passives carry
    over. PassiveRandomWeights: how many extra random passives get rolled on top.
    """
    mechanics = _db()["mechanics"]

    def normalize(weights: dict[str, Any]) -> list[dict[str, Any]]:
        total = sum(float(v) for v in weights.values()) or 1.0
        return [
            {"count": int(k), "weight": float(v), "chance": round(float(v) / total, 4)}
            for k, v in sorted(weights.items(), key=lambda kv: int(kv[0]))
        ]

    return {
        "passivesInherited": normalize(mechanics.get("PassiveInheritanceWeights", {})),
        "passivesRandom": normalize(mechanics.get("PassiveRandomWeights", {})),
        "ivsInherited": normalize(mechanics.get("IVInheritanceWeights", {})),
        "note": (
            "A child draws N passives from the combined pool of both parents' passives, "
            "then rolls M additional random ones. Duplicate-free, capped at 4 total."
        ),
    }
