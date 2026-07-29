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


def pal_info(internal_name: str) -> dict[str, Any]:
    """Display metadata for an internal Pal name, degrading gracefully."""
    pals = _db()["pals"]
    info = pals.get(internal_name)
    if not info:
        return {"internalName": internal_name, "name": internal_name, "known": False}
    return {
        "internalName": internal_name,
        "name": info["name"],
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


def passive_name(internal_id: str) -> str:
    entry = _db().get("passives", {}).get(internal_id)
    if entry and entry.get("name") and not str(entry["name"]).startswith("en Text"):
        return entry["name"]
    return internal_id


def is_unreleased(internal_name: str) -> bool:
    """
    True for Pals present in the game files but absent from the Paldeck
    (`zukanIndex` of -1).

    The 1.0 breeding tables reference several of these, so their pair data is
    kept — it is correct if they are ever released. They are withheld from the
    planner's target list because offering a goal nobody can obtain is worse
    than not listing it.
    """
    return bool(_db()["pals"].get(internal_name, {}).get("unreleased"))


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
    return _breeding()["pairs"].get(_pair_key(parent_a, parent_b))


# ─── Palbox analysis ─────────────────────────────────────────────


def _breedable(pal: dict) -> bool:
    """Bosses/alphas cannot breed; neither can anything outside the table."""
    if pal.get("isBoss"):
        return False
    species = pal.get("speciesId") or ""
    return species in _db()["pals"]


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
        key = pal["speciesId"]
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


def breeding_paths(target: str, owned_species: list[str], max_depth: int = MAX_PATH_DEPTH) -> dict[str, Any]:
    """
    Shortest breeding route from what you own to a target Pal.

    Breadth-first over the pair table: each step adds every child obtainable
    from the species pool, until the target appears or we hit the depth cap.
    Gender is ignored here (a route is about species reachability); the
    single-step view above is the one that enforces it.
    """
    pairs = _breeding()["pairs"]
    pool = {s for s in owned_species if s in _db()["pals"]}

    if not pool:
        return {"target": target, "reachable": False, "reason": "No breedable Pals owned", "steps": []}
    if target in pool:
        return {"target": target, "reachable": True, "alreadyOwned": True, "steps": []}

    # parent -> how it was made
    origin: dict[str, tuple[str, str]] = {}
    frontier = set(pool)

    for _depth in range(max_depth):
        new: set[str] = set()
        current = sorted(pool)[:MAX_PATH_FRONTIER]

        for i, a in enumerate(current):
            for b in current[i:]:
                child = pairs.get(_pair_key(a, b))
                if child and child not in pool and child not in new:
                    new.add(child)
                    origin[child] = (a, b)

        if not new:
            break

        pool |= new
        frontier = new

        if target in pool:
            # walk back to the owned set
            steps: list[dict[str, Any]] = []
            seen: set[str] = set()

            def unwind(node: str) -> None:
                if node in seen or node not in origin:
                    return
                seen.add(node)
                a, b = origin[node]
                unwind(a)
                unwind(b)
                steps.append(
                    {
                        "parentA": pal_info(a),
                        "parentB": pal_info(b),
                        "child": pal_info(node),
                    }
                )

            unwind(target)
            return {"target": target, "reachable": True, "alreadyOwned": False, "steps": steps}

    return {
        "target": target,
        "reachable": False,
        "reason": f"Not reachable within {max_depth} breeding steps from your current Pals",
        "steps": [],
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
