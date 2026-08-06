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


# Gender-dependent pairs, read off the game's own DT_PalCombiUnique.
#
# Cached because it is fixed reference data and `predict_child` is called once
# per pair in a search that can walk thousands.
_gendered: Optional[dict[tuple[str, str], dict[str, str]]] = None


def _gendered_combos() -> dict[tuple[str, str], dict[str, str]]:
    """
    `{(speciesA, speciesB): {"Male+Female": child, "Female+Male": child}}`,
    keyed on the *ordered* pair as the game states it.

    **The whole game has exactly one of these**, and the palbox table cannot
    express it. `CatMage x FoxMage` yields `FoxMage_Dark` when the CatMage is
    male and `CatMage_Fire` when it is female — two outcomes for one unordered
    pair, so palcalc's table (which is keyed on `sorted([a, b])`) can only hold
    one and reports `FoxMage_Dark`. The other outcome was simply unreachable
    through this planner.

    Empty when the bundle is absent, which degrades to exactly the old
    behaviour rather than failing.
    """
    global _gendered
    if _gendered is None:
        _gendered = {}
        for combo in gamedata.unique_combos():
            gender_a, gender_b = combo.get("genderA"), combo.get("genderB")
            if "None" in (gender_a, gender_b) or not gender_a or not gender_b:
                continue
            child = str(combo.get("childId") or "")
            if not child:
                continue
            for a in combo.get("parentSpeciesA") or []:
                for b in combo.get("parentSpeciesB") or []:
                    key = (canonical_species(a), canonical_species(b))
                    _gendered.setdefault(key, {})[f"{gender_a}+{gender_b}"] = child
    return _gendered


def gendered_outcomes(parent_a: str, parent_b: str) -> dict[str, str]:
    """
    Every child this pair can produce, by parent gender — `{}` for the ordinary
    case where gender does not matter.

    Returned as a map rather than folded into `predict_child` because a planner
    showing one answer for a pair with two is the bug this exists to fix. The
    caller has to see both to report both.
    """
    table = _gendered_combos()
    a, b = canonical_species(parent_a), canonical_species(parent_b)
    if (a, b) in table:
        return dict(table[(a, b)])
    # Stated the other way round: swap the genders with the parents.
    return {
        "+".join(reversed(genders.split("+"))): child
        for genders, child in table.get((b, a), {}).items()
    }


def predict_child(
    parent_a: str, parent_b: str,
    gender_a: Optional[str] = None, gender_b: Optional[str] = None,
) -> Optional[str]:
    """
    The Pal produced by this pair, or None if the pair cannot breed.

    **Genders are optional and only ever matter for one pair in the game.**
    Supplied, they resolve `CatMage x FoxMage` to the right one of its two
    outcomes; omitted, the behaviour is unchanged and the palbox table answers.
    Callers that do know the genders should pass them — see `gendered_outcomes`
    for reporting both.
    """
    if gender_a and gender_b:
        outcomes = gendered_outcomes(parent_a, parent_b)
        child = outcomes.get(f"{gender_a}+{gender_b}")
        if child:
            return child

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
                # Where the copies of this species actually are.
                #
                # A parent counted here may not be in the palbox: base workers
                # and the contents of a guild's Pal stores are breedable — anyone
                # in the guild can take one out — and they are counted for that
                # reason. But a plan is a set of instructions, and "pair your two
                # Lamballs" is a bad instruction if one of them is standing in a
                # base three valleys away, or sitting in a Flea Market stall.
                #
                # So the count includes them and the note says where, rather than
                # the planner quietly meaning something different from the palbox
                # the player is looking at.
                "locations": {},
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

        # `storage` is named by its structure ("Dimensional Pal Storage", "Flea
        # Market (Pals)") rather than by the word `storage`, which tells a player
        # nothing about where to walk.
        where = str(pal.get("storageKind") or pal.get("location") or "") or "unknown"
        entry["locations"][where] = entry["locations"].get(where, 0) + 1

        entry["individuals"].append(
            {
                "instanceId": pal.get("instanceId"),
                "nickname": pal.get("nickname") or "",
                "gender": gender,
                "level": pal.get("level", 0),
                "rank": pal.get("rank", 1),
                "location": pal.get("location") or "",
                "storageKind": pal.get("storageKind") or "",
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

    **One pair in the game yields a different child depending on which parent is
    which sex**, and both outcomes are reported. `CatMage x FoxMage` gives
    `FoxMage_Dark` with a male CatMage and `CatMage_Fire` with a female one. The
    palbox pair table is keyed on an unordered pair, so it can only hold one —
    it holds `FoxMage_Dark`, and `CatMage_Fire` was unreachable through this
    planner until the game's own `DT_PalCombiUnique` was read.

    Rows carry `genderDependent` and `requiresGenders` so the UI can say which
    parent has to be which, rather than showing two identical-looking pairs.
    """
    summary = summarize_palbox(pals)
    species = {s["internalName"]: s for s in summary["species"]}
    names = sorted(species)

    # "Owned" is judged against EVERYTHING you have, not the breedable subset.
    #
    # `summarize_palbox` drops Pals that cannot breed — which is every alpha and
    # boss form — so using its species list to answer "do you already have one of
    # these" reported Kingpaca, Elizabee, Sweepa and Astegon as things the player
    # lacked while they sat in their palbox. With the default "only ones I don't
    # have" filter on, those are exactly the rows that survive it: removing them
    # is the filter's whole purpose.
    #
    # Measured on one reference player: 559 Pals, 163 distinct species, of which
    # **42 are unbreedable** — and 10 surfaced in the offspring list as unowned.
    #
    # The pairing below still uses the breedable set only, because an alpha
    # genuinely cannot go in a breeding pen. It was only the *label* that was
    # wrong. Canonicalised, because this is compared against pair-table spellings
    # and the save disagrees with them on eight species.
    owned_species = {
        canonical_species(p.get("speciesId") or "")
        for p in pals
        if p.get("speciesId")
    }

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
            # A pair with gender-dependent outcomes produces BOTH children —
            # which one depends on which parent is which sex, and a player
            # holding both sexes of both species can make either. Recording only
            # `predict_child`'s single answer hid `CatMage_Fire` completely.
            outcomes = gendered_outcomes(a, b)
            if outcomes:
                for genders, child in outcomes.items():
                    gender_a, gender_b = genders.split("+")
                    # Only if the player can actually assemble that pen.
                    if not (species[a].get(gender_a.lower())
                            and species[b].get(gender_b.lower())):
                        continue
                    record(child, a, b)
                    results[child]["genderDependent"] = True
                    results[child].setdefault("requiresGenders", []).append(
                        {"aId": a, "bId": b, "aGender": gender_a, "bGender": gender_b}
                    )
                continue

            child = predict_child(a, b)
            if child:
                record(child, a, b)

    for entry in results.values():
        entry["owned"] = entry["internalName"] in owned_species
        entry["pairCount"] = len(entry["fromPairs"])
        entry["fromPairs"] = entry["fromPairs"][:12]
        # Present on every row so a client can rely on it rather than testing
        # for the key's existence.
        entry.setdefault("genderDependent", False)
        entry.setdefault("requiresGenders", [])

    return sorted(results.values(), key=lambda r: (r.get("dex") or 9999))


def _owned_pool(owned_species: Iterable[str]) -> set[str]:
    """Canonicalised owned species that the pair table actually knows."""
    known = _db()["pals"]
    return {c for c in (canonical_species(s) for s in owned_species) if c in known}


def _pairable(a: str, b: str, genders: dict[str, dict[str, int]]) -> bool:
    """
    Whether two species can actually be put in a breeding pen together.

    A pair needs one male and one female. Same-species pairs need both genders
    of that species; different-species pairs need opposite genders across the
    two. This is the same rule `possible_offspring` applies — shared, so the
    route search and the one-step view cannot disagree about what is possible.

    A species with no gender entry is one the player does not own, which only
    happens for a bred *intermediate*; see `_expand` for why that is unrestricted.
    """
    have_a, have_b = genders.get(a), genders.get(b)
    if have_a is None or have_b is None:
        return True
    if a == b:
        return have_a["male"] > 0 and have_a["female"] > 0
    return bool(
        (have_a["male"] and have_b["female"]) or (have_a["female"] and have_b["male"])
    )


def _expand(
    pool: set[str],
    max_depth: int,
    genders: Optional[dict[str, dict[str, int]]] = None,
) -> tuple[dict[str, tuple[str, str]], dict[str, int]]:
    """
    Breadth-first over the pair table from an owned pool.

    Returns `origin` (child -> the pair that first produced it) and `depth`
    (child -> how many breeding steps deep it first appeared). Because BFS
    visits in generation order and a child is only ever recorded the first time
    it is seen, `depth` is the *shortest* route, not merely a route.

    Shared by the single-target search and the reachability listing so there is
    one implementation of the traversal rather than two that can disagree.

    GENDER
    ------
    `genders` is `{species: {"male": n, "female": n}}` for what the player
    **owns**. Pass it and the search will not propose a pair the player cannot
    physically make; omit it and the search is about species reachability alone.

    **The constraint binds on owned species only, and that is not a shortcut.**
    Parents are not consumed by breeding, so any pair that works once works
    again — which means an *intermediate* species can be re-bred until it comes
    out the gender the next step needs. An owned species cannot: if the only
    Relaxaurus you have is male, no amount of breeding turns it female. So a
    route is blocked exactly when a step pairs two species you already own
    whose genders do not oppose (or self-pairs a species you own only one
    gender of), which is what `_pairable` tests.

    Without this the planner would route a player through pairs they can never
    make, and scoping breeding to a player's own palbox made that far more than
    theoretical: a personal box is small and single-gender species in it are
    common, where a whole server's Pals almost always cover both.
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
                if genders is not None and not _pairable(a, b, genders):
                    continue
                child = pairs.get(_pair_key(a, b))
                if child and child not in reached and child not in new:
                    new.add(child)
                    origin[child] = (a, b)
                    depth[child] = step

        if not new:
            break
        reached |= new

    return origin, depth


def gender_pool(pals: Iterable[dict]) -> dict[str, dict[str, int]]:
    """
    `{species: {"male": n, "female": n}}` for a set of Pals, canonicalised.

    Canonicalised at this boundary for the same reason `_owned_pool` is: the
    save spells species inconsistently against the pair table, and an exact
    match silently drops eight real Pals — which here would read as "you own no
    female Sheepball" and block routes that are perfectly achievable.

    An unknown gender counts as **neither**. Guessing would produce a route the
    player cannot make, and this is the half of the planner where being wrong is
    expensive: a plan is followed, a listing is only read.
    """
    pool: dict[str, dict[str, int]] = {}
    for pal in pals:
        key = canonical_species(pal.get("speciesId") or "")
        entry = pool.setdefault(key, {"male": 0, "female": 0})
        gender = pal.get("gender")
        if gender == "Male":
            entry["male"] += 1
        elif gender == "Female":
            entry["female"] += 1
    return pool


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


def breeding_paths(
    target: str,
    owned_species: list[str],
    max_depth: int = MAX_PATH_DEPTH,
    genders: Optional[dict[str, dict[str, int]]] = None,
) -> dict[str, Any]:
    """
    Shortest breeding route from what you own to a target Pal.

    Pass `genders` (from `gender_pool`) and the route will only use pairs the
    player can actually put in a pen — see `_expand`. Without it the answer is
    about species reachability alone, which is the right question when the pool
    is a whole server's Pals and the wrong one for a single palbox.

    `genderAware` travels with the answer so the UI can say which question it
    asked. An unreachable-because-of-gender result is a different thing from an
    unreachable-at-all one, and the fix for it (catch or trade for the opposite
    gender) is worth naming rather than leaving as a dead end.
    """
    target = canonical_species(target)
    pool = _owned_pool(owned_species)
    aware = genders is not None

    if not pool:
        return {"target": target, "reachable": False, "genderAware": aware,
                "reason": "No breedable Pals owned", "steps": []}
    if target in pool:
        return {"target": target, "reachable": True, "genderAware": aware,
                "alreadyOwned": True, "steps": []}

    origin, depth = _expand(pool, max_depth, genders)
    if target not in depth:
        reason = f"Not reachable within {max_depth} breeding steps from your current Pals"
        if aware:
            # Was it the gender constraint, or the species themselves? Re-running
            # without it is the only honest way to tell, and the two call for
            # completely different actions from the player.
            _, ignoring = _expand(pool, max_depth)
            if target in ignoring:
                reason = (
                    "Reachable by species, but not with the genders you own — "
                    "some step needs two Pals you only have one gender of. "
                    "Catching or trading for the opposite gender opens it up."
                )
        return {
            "target": target,
            "reachable": False,
            "genderAware": aware,
            "reason": reason,
            "steps": [],
        }
    return {
        "target": target,
        "reachable": True,
        "genderAware": aware,
        "alreadyOwned": False,
        "steps": _unwind(target, origin),
    }


def indirect_targets(
    owned_species: Iterable[str],
    max_depth: int = MAX_PATH_DEPTH,
    genders: Optional[dict[str, dict[str, int]]] = None,
) -> dict[str, Any]:
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
        return {"maxDepth": max_depth, "ownedSpecies": 0,
                "genderAware": genders is not None, "targets": []}

    origin, depth = _expand(pool, max_depth, genders)

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
        "genderAware": genders is not None,
        "targets": targets,
    }


# ─── Why a species can or cannot be bred ─────────────────────────

# The game's own wording for what a mutated egg is, quoted rather than
# paraphrased. `PalEgg_MutationPal_01` and four siblings carry it verbatim.
#
# **This is a quote and not a mechanic.** The game says these eggs exist and are
# rare; no file in either pak says what produces one, at what rate, or which
# species it hatches — checked across all 471 server-pak DataTables, where
# `PalEgg_MutationPal` appears only as an icon, a visual model, a pickup
# blueprint and a particle effect. `basesupply.py`'s rule applies: report facts,
# not mechanics.
MUTATED_EGG_QUOTE = (
    "An egg that is extremely rarely obtained, having undergone a special "
    "mutation."
)

# The one other thing the game says out loud about mutation, on `Cake04`
# (Extravagant Vegetable Cake). It ties mutation to the Breeding Farm in
# Pocketpair's own words, which is why it is worth carrying and why it is
# quoted rather than turned into a claim about rates.
MUTATION_CAKE_QUOTE = (
    "Place it in the chest at a Breeding Farm to make Pals lay a particularly "
    "healthy egg. Mutations are more likely to occur, and talents will grow "
    "more easily."
)


@lru_cache(maxsize=1)
def _named_pairings() -> dict[str, list[dict[str, Any]]]:
    """
    `{childSpecies: [{a, b, aName, bName, genderA, genderB}]}` from
    `DT_PalCombiUnique` — every pairing the game names outright.

    Keyed on the **child**, which is the direction the table is not stored in
    and the direction a player asks in: "how do I get one of these".

    Tribes rather than species, because that is what the game keys on and it is
    the more useful answer — "Mossanda x Grizzbolt" covers the alpha and
    predator forms of both without listing six rows.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for combo in gamedata.unique_combos():
        child = str(combo.get("childId") or "")
        a = str(combo.get("parentTribeA") or "")
        b = str(combo.get("parentTribeB") or "")
        if not (child and a and b):
            continue
        entry = {
            "a": a,
            "b": b,
            "aName": gamedata.pal_name(a) or a,
            "bName": gamedata.pal_name(b) or b,
        }
        # A variant paired with itself yields itself — the game states this
        # explicitly for most of them. It is a real pairing and worth showing,
        # but it is **not an answer to "how do I get my first one"**, so it is
        # labelled rather than listed indistinguishably beside the pair that is.
        if a == child and b == child:
            entry["breedsTrue"] = True
        # Carried only when the game states them, which is one pair in the
        # whole table (CatMage x FoxMage). A gender on every row would read as
        # a requirement everywhere.
        for key, field in (("genderA", "genderA"), ("genderB", "genderB")):
            value = str(combo.get(field) or "None")
            if value != "None":
                entry[key] = value
        out.setdefault(child, []).append(entry)
    return out


def obtainability(species_id: str) -> dict[str, Any]:
    """
    Whether a species can be bred at all, and if so by what — from the game's
    own columns, never from this project's opinion.

    Four answers, and the middle two are the ones this exists for:

    - `standard`      — an ordinary outcome of the rank rule.
    - `named_pairing` — an element variant. The game names the pairings that
                        produce it and the general rule never will, so the
                        pairings are listed. 81 species.
    - `unverified`    — an element variant the game names **no** pairing for,
                        while the table this planner runs on offers one anyway.
                        Three species, and the disagreement is reported rather
                        than resolved. See `scripts/verify-breeding.py`.
    - `never`         — `IgnoreCombi`, the game saying this species takes no
                        part in breeding. 226 species.

    **`named_pairing` is not "unbreedable", and an earlier version of this
    project said it was.** `DT_PalCombiUnique` names an element variant as the
    child in 159 of its 256 tribe pairs; what is true is only that the rank
    fallback never produces one. Telling a player "no pairing reaches this"
    when the game ships the pairing is worse than saying nothing.

    Unknown species get `standard` with `known: False` rather than a refusal —
    a modded or unreleased id is not evidence of anything, and the caller is a
    UI badge.

    **Read off the NORMALISED species, never `pal_exact`.** The game sets
    `ZukanIndexSuffix` on the base row only and gives encounter forms
    `zukanIndex = -1`: `BOSS_GrassPanda_Electric` carries no suffix at all, so
    an exact lookup calls an alpha Mossanda Lux an ordinary Pal. `pal_exact`
    exists because *stats* differ between an alpha and its base — breeding
    eligibility is a property of the species and does not.
    """
    species, _ = gamedata.normalise_species(canonical_species(species_id))
    entry = gamedata.pal(species) or {}
    if not entry:
        return {"species": species, "kind": "standard", "known": False}

    result: dict[str, Any] = {"species": species, "known": True, "kind": "standard"}

    if entry.get("ignoreCombi"):
        result["kind"] = "never"
        result["note"] = (
            "The game marks this species as taking no part in breeding "
            "(IgnoreCombi). No pairing produces it and it cannot be a parent."
        )
        return result

    if entry.get("zukanSuffix") != "B":
        return result

    # An element variant from here down.
    pairings = _named_pairings().get(species) or []
    result["variant"] = True
    if pairings:
        result["kind"] = "named_pairing"
        result["pairings"] = pairings
        result["note"] = (
            f"Element variant. The game names {len(pairings)} pairing"
            f"{'' if len(pairings) == 1 else 's'} that produce it; the general "
            "rank rule never does, so it comes from these pairs or not at all."
        )
        return result

    result["kind"] = "unverified"
    result["note"] = (
        "Element variant. The game's own unique-combination table names no "
        "pairing for it, while the breeding table this planner runs on offers "
        "one — that disagreement is unresolved and nothing in the game files "
        "settles it. Treat a suggested route for this Pal as unconfirmed."
    )
    result["mutatedEgg"] = {
        "quote": MUTATED_EGG_QUOTE,
        "cakeQuote": MUTATION_CAKE_QUOTE,
        "cakeItem": "Cake04",
        # Said explicitly, because the absence is the point. A UI that shows the
        # quotes without this reads as "here is how you get one".
        "note": (
            "The game ships six mutated-egg items and says they are rare. No "
            "game file says what produces one, at what rate, or which species "
            "it hatches, so this dashboard does not."
        ),
    }
    return result


def unbreedable() -> dict[str, Any]:
    """
    Every species the game will not let a pairing produce, and why.

    Exists because "not reachable" with no explanation reads as a dashboard gap
    rather than as the game saying no — the same bug the Paldeck's empty
    work-suitability panel had, with the same fix.

    Unreleased and Paldeck-absent forms are excluded: a player cannot obtain
    them by any means, so listing them under "cannot be bred" implies the rest
    of the list is otherwise obtainable, which would be misleading in the one
    direction that matters.

    **A row is a PALDECK ENTRY, not a species, and getting that wrong said
    "Mossanda Lux cannot be bred" about a Pal that plainly can.**
    `GrassPanda_Electric_Tower` is the tower-boss encounter form of
    `GrassPanda_Electric`: same Paldeck number, same suffix, same display name,
    and `IgnoreCombi` true because *that form* is not a breeding outcome. Nine
    of the eleven collisions are this shape (`_Oilrig` and `_Tower` forms).
    Grouping on `(zukanIndex, zukanSuffix)` — the pair the Paldeck itself
    identifies a Pal by — and keeping the **most permissive** answer is the fix:
    if any form of a Paldeck entry can be bred, the player can breed that Pal.
    """
    ranked = {"standard": 0, "named_pairing": 1, "unverified": 2, "never": 3}
    best: dict[tuple[int, str], tuple[int, dict[str, Any]]] = {}

    for species, entry in (gamedata.load().get("pals") or {}).items():
        paldeck = entry.get("zukanIndex") or 0
        if paldeck <= 0:
            continue
        # `BOSS_`/`GYM_`/`PREDATOR_` forms share their base species' Paldeck
        # number and would double every row. They are encounter forms of a
        # species already in the list.
        if species != gamedata.normalise_species(species)[0]:
            continue
        info = obtainability(species)
        row = {
            "species": species,
            "name": gamedata.pal_name(species) or species,
            "paldeck": paldeck,
            "suffix": entry.get("zukanSuffix") or "",
            "kind": info["kind"],
            "note": info.get("note"),
        }
        if info["kind"] == "unverified":
            row["mutatedEgg"] = info.get("mutatedEgg")
        elif info["kind"] == "named_pairing":
            row["pairings"] = info.get("pairings")

        key = (paldeck, row["suffix"])
        rank = ranked[info["kind"]]
        if key not in best or rank < best[key][0]:
            best[key] = (rank, row)

    never = [r for _, r in best.values() if r["kind"] == "never"]
    unverified = [r for _, r in best.values() if r["kind"] == "unverified"]
    named = [r for _, r in best.values() if r["kind"] == "named_pairing"]

    order = lambda r: (r["paldeck"], r["suffix"], r["name"])  # noqa: E731
    return {
        "never": sorted(never, key=order),
        "unverified": sorted(unverified, key=order),
        "namedPairingOnly": sorted(named, key=order),
        "paldeckEntries": len(best),
        # A bred Pal is an alpha 5% of the time — BP_PalGameSetting's own
        # constant, carried here because the breeding tab is where somebody
        # wonders why an egg hatched a boss form.
        "alphaChance": gamedata.game_setting("Combi_BossPalRate"),
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
