"""
Progression checklists: not "52 of 82" but *which* 30 are left, by name.

`/api/progress` has counted these categories since Phase 4 and could never name
one, because a save's flag maps are keyed on ids nothing resolved:

    towerBosses      BOSS_BATTLE_NAME_GrassBoss     a localisation key
    fieldBosses      81_1_grass_FBOSS_FlameBuffalo  a spawner id
                     BOSS_Hunter_Rifle              …or an NPC id, in the SAME map
    areasFound       Grass_001                      a world-map area row
    fastTravel       6E03F846…                      an instance GUID
    effigies         A360858E…                      an instance GUID

Every one of those now has a bundle behind it, so the answer can be a list of
names — which is the difference between a progress bar and a to-do list.

THE DENOMINATOR IS THE HARD PART, AND IT IS NOT ONE NUMBER
----------------------------------------------------------
A save records only what a player has *obtained*, so it can never say how much
is left; `parser.progress_totals` already handles that by preferring a published
or extracted figure over the observed union and labelling which it used. This
module goes further only where a bundle enumerates the category outright:

- **areasFound** gets a real total for the first time: 123 rows in
  `DT_WorldMapAreaData`, every one resolving to a display name.
- **towerBosses** is 8 towers — checked against the eight "… Tower Entrance"
  fast-travel points, which come from a different file entirely.
- **fieldBosses** is *split*, because the flag map holds two different kinds of
  key and only one of them is enumerable. See `_field_bosses`.

WHAT IT WILL NOT DO
-------------------
**It will not invent a denominator from a catalogue.** The item catalogue lists
34 `BOSS_`-prefixed NPCs, and adding that to the 90 placed Pal bosses would give
a confident "124 field bosses" — on evidence that includes `BOSS_DarkTrader`, a
merchant, and `BOSS_Hunter_Fat_GatlingGun_Quest_StrongOldMan`, a quest NPC.
A category whose size disagrees with what the game has is wrong however
plausible its rows read.

**It will not repair `？？？`.** Two of the fourteen boss encounters carry that
as their name in the game's own text table — it is the game withholding a
spoiler, not a decode failure, and `hidden` travels beside it so the UI can say
so rather than print full-width question marks or humanise the key instead.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import gamedata

logger = logging.getLogger(__name__)

# `TowerBossDefeatFlag` holds more than towers. These are the kinds a player is
# scored against; `location` (the King Whale arena) is not an encounter.
COUNTED_BATTLE_KINDS = ("tower", "endgame", "worldTreeMidBoss")


def _fold(values) -> dict[str, str]:
    """`{lowercase: original}` — every join in this project folds case."""
    return {str(k).lower(): str(k) for k in values}


def _region_names() -> dict[str, dict[str, Any]]:
    """
    `{areaRowId: {name, msgId}}` for all 123 world-map areas.

    **A two-hop join, and both hops are measured.** The save names an area row
    (`Grass_001`); `progression.areas` maps that to a localisation key
    (`REGION_Grass_1`); `gamedata.regions` maps the key's suffix to the display
    name ("Windswept Island"). 123 of 123 make the second hop, and every area
    key seen across the reference world's five players makes the first.

    The first hop needs a **case-fold**: the save writes `BOSS_KingWhale` where
    the table says `Boss_KingWhale`. One row, and an exact join would have
    dropped it silently while the other 103 looked fine.
    """
    areas = gamedata.progression().get("areas") or {}
    regions = gamedata.load().get("regions") or {}
    lowered = {str(k).lower(): v for k, v in regions.items()}

    out: dict[str, dict[str, Any]] = {}
    for row_id, msg_id in areas.items():
        key = str(msg_id or "")
        suffix = key[len("REGION_"):] if key.startswith("REGION_") else key
        name = lowered.get(suffix.lower())
        out[str(row_id)] = {
            "name": name or gamedata.humanize(str(row_id)),
            # An unresolved region is reported, never quietly humanised into
            # something that reads like the game's own word for it.
            "nameIsInternal": name is None,
            "msgId": key,
        }
    return out


def _checklist(
    obtained_keys: list[str],
    catalogue: dict[str, dict[str, Any]],
    *,
    limit: Optional[int] = None,
) -> dict[str, Any]:
    """
    Split a catalogue into what this player has and what is left, by name.

    Keys not in the catalogue are counted as obtained and reported separately —
    a player has plainly done the thing, and the honest reading is that this
    bundle does not enumerate it rather than that the save is wrong.
    """
    folded = _fold(catalogue)
    have: list[dict[str, Any]] = []
    unknown: list[str] = []
    seen: set[str] = set()

    for key in obtained_keys:
        canonical = folded.get(str(key).lower())
        if canonical is None:
            unknown.append(str(key))
            continue
        seen.add(canonical)
        have.append({"id": canonical, **catalogue[canonical]})

    missing = [
        {"id": key, **value} for key, value in catalogue.items() if key not in seen
    ]
    have.sort(key=lambda r: str(r.get("name") or r["id"]))
    missing.sort(key=lambda r: str(r.get("name") or r["id"]))

    return {
        "obtained": len(have) + len(unknown),
        "of": len(catalogue),
        "have": have if limit is None else have[:limit],
        "missing": missing if limit is None else missing[:limit],
        "truncated": limit is not None and (
            len(have) > limit or len(missing) > limit
        ),
        # Ids the bundle does not list. Never silently folded into either side.
        "unlisted": unknown,
    }


def tower_bosses(keys: list[str]) -> dict[str, Any]:
    """The major boss encounters, named by the game."""
    battles = gamedata.progression().get("bossBattles") or {}
    catalogue = {
        key: {
            "name": entry.get("name") or key,
            # `？？？` — the game withholding an endgame name. Passed through
            # so the UI can say "not named yet" instead of printing it.
            "nameHidden": bool(entry.get("hidden")),
            "kind": entry.get("kind"),
        }
        for key, entry in battles.items()
        if entry.get("kind") in COUNTED_BATTLE_KINDS
    }
    return _checklist(keys, catalogue)


def field_bosses(keys: list[str]) -> dict[str, Any]:
    """
    Field bosses — **split in two, because the flag map holds two kinds of key.**

    Measured across the reference world's five players: 82 distinct keys, of
    which 59 are spawner ids resolving through `boss_spawners.json.gz` to a
    species and a level, and 23 are `BOSS_`-prefixed NPC ids for the human
    bosses. Neither kind resolves as the other, and nothing in the save
    distinguishes them but their shape.

    So the Pal half gets a real denominator — 90 placed spawners, positions
    verified against the cell grid — and the human half does **not**, because
    the only enumeration available is the catalogue's 34 `BOSS_` NPCs and that
    list contains a merchant. Its total is the observed count and is labelled
    `discovered`, which is what `progress_totals` already does for a category
    with no trustworthy figure.
    """
    # **89 spawners, not 90 rows.** `remainsIsland_1_GrassGolem_FBOSS` is listed
    # twice, same species at level 55 and level 75. The defeat flag is keyed on
    # the *spawner*, so that is one checkbox rather than two, and taking the row
    # count as the denominator would leave a player permanently one short.
    spawners = gamedata.boss_spawners() or []
    catalogue: dict[str, dict[str, Any]] = {}
    for entry in spawners:
        spawner_id = str(entry.get("spawnerId") or "")
        if not spawner_id:
            continue
        species = str(entry.get("speciesId") or "")
        level = entry.get("level")
        existing = catalogue.get(spawner_id)
        if existing is not None:
            # Keep the range rather than letting the last row win silently.
            existing["levelMax"] = max(existing["levelMax"], level or 0)
            existing["level"] = min(existing["level"] or 0, level or 0)
            continue
        catalogue[spawner_id] = {
            "name": gamedata.character_name(species) if species else spawner_id,
            "speciesId": species,
            "level": level,
            "levelMax": level,
            "x": entry.get("x"),
            "y": entry.get("y"),
        }

    folded = _fold(catalogue)
    human_keys = [k for k in keys if str(k).lower() not in folded]
    pal_keys = [k for k in keys if str(k).lower() in folded]

    pals = _checklist(pal_keys, catalogue)
    return {
        "pals": pals,
        "humans": {
            # Named from the NPC tables, which do resolve — it is only the
            # *total* that has no source.
            "have": sorted(
                (
                    {"id": key, "name": gamedata.character_name(key)}
                    for key in dict.fromkeys(human_keys)
                ),
                key=lambda r: r["name"],
            ),
            "obtained": len(dict.fromkeys(human_keys)),
            # Deliberately absent: see the docstring. `of` is not a number this
            # module is willing to make up.
            "of": None,
            "totalSource": "discovered",
        },
    }


def areas_found(keys: list[str]) -> dict[str, Any]:
    """
    Regions discovered, out of 123 — the first real denominator this had.

    `areasFound` was in `reference_totals.json`'s `unverified` list, so the tab
    could only ever say "92 discovered" with nothing to compare it against.
    """
    return _checklist(keys, _region_names())


def fast_travel(keys: list[str]) -> dict[str, Any]:
    """
    Fast-travel points unlocked, out of 174, named and split by kind.

    The eight "… Tower Entrance" points are what makes the tower count above
    checkable against a different file, so the kind travels here too.
    """
    # **Keyed on the dict key, not on the entry's own `id`.** They are different
    # values: the key is the instance GUID the save's unlock flags name, while
    # `id` is a readable slug (`WorldTree_MiddleBoss_1`). Joining on `id` would
    # match nothing at all, which is at least loud.
    catalogue = {
        str(guid): {
            "name": entry.get("name") or str(entry.get("id") or guid),
            "kind": gamedata.fast_travel_kind(str(entry.get("name") or "")),
        }
        for guid, entry in (gamedata.load().get("fastTravel") or {}).items()
    }
    return _checklist(keys, catalogue, limit=60)


def effigies(keys: list[str]) -> dict[str, Any]:
    """
    Effigies collected, out of 396.

    Positions only — a relic has no name of its own, so `missing` here is a list
    of map locations rather than a list of things. Truncated hard: 371 missing
    entries is not a checklist, and the map already draws them.
    """
    catalogue = {
        str(entry.get("guid") or ""): {
            "name": f"Effigy {str(entry.get('guid') or '')[:8]}",
            "x": entry.get("x"),
            "y": entry.get("y"),
        }
        for entry in (gamedata.effigies() or [])
        if entry.get("guid")
    }
    return _checklist(keys, catalogue, limit=40)


def pal_display(keys: list[str]) -> dict[str, Any]:
    """
    The "show me this Pal" requests, out of 54.

    Keys come from `RecordData.PalDisplayNPCDataTableProgress` and are exactly
    the `RequestID`s in `DA_PalDisplay` — a real checklist, unlike its item-request
    sibling, whose progress no save has ever been seen to record.

    **The species is named, not the request id.** `Area_F1_1` is meaningless to
    a player; "Carbunclo, Area F1" is the answer, and `pal_name` is what turns
    one into the other. An unresolvable species keeps its raw id rather than
    being dropped — the same rule as everywhere else here.
    """
    requests = ((gamedata.npc_requests() or {}).get("palDisplay") or {}).get("requests") or {}
    catalogue = {}
    for request_id, entry in requests.items():
        species = str(entry.get("speciesId") or "")
        catalogue[str(request_id)] = {
            "name": gamedata.pal_name(species) or species,
            "speciesId": species,
            "area": entry.get("category") or "",
            "rewards": entry.get("rewards") or [],
        }
    return _checklist(keys, catalogue, limit=60)


def describe(progress: dict[str, Any]) -> dict[str, Any]:
    """
    Named checklists for one player's progress, from `extract_player_progress`.

    **Takes the progress dict rather than fetching one.** The caller owns the
    privacy scoping — this is discovery data, and a module that could go and get
    a player's progress itself would be one refactor away from bypassing the
    filter that decides who may see it.
    """
    progress = progress or {}

    def keys(label: str) -> list[str]:
        entry = progress.get(label)
        return list((entry or {}).get("keys") or []) if isinstance(entry, dict) else []

    out: dict[str, Any] = {
        "towerBosses": tower_bosses(keys("towerBosses")),
        "fieldBosses": field_bosses(keys("fieldBosses")),
        "areasFound": areas_found(keys("areasFound")),
        "fastTravel": fast_travel(keys("fastTravel")),
        "effigies": effigies(keys("effigies")),
        "palDisplay": pal_display(keys("palDisplay")),
    }

    # `dungeonsCleared` is deliberately absent rather than empty. On every save
    # examined `FixedDungeonClearCount` holds nothing at all — five players on
    # the reference world, none of them with an entry — so there is no observed
    # key shape to join on and a checklist built on a guessed one would be
    # unverifiable. The count `/api/progress` already reports is unaffected.
    out["dungeonsCleared"] = {
        "available": False,
        "reason": (
            "No save examined has ever written a FixedDungeonClearCount entry, "
            "so there is no key shape to match dungeon names against."
        ),
    }
    return out


def available() -> bool:
    """Whether the bundles these checklists need actually loaded."""
    return bool(gamedata.progression().get("bossBattles")) and bool(
        gamedata.load().get("regions")
    )
