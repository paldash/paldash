"""
Palworld save parsing — wraps palworld-save-tools correctly.

The previous version called `decompress_sav_to_gvas(raw, PALWORLD_TYPE_HINTS)`
and treated the result as a GvasFile. The real signature is
`decompress_sav_to_gvas(data) -> (gvas_bytes, save_type)`, and the bytes then
have to go through `GvasFile.read(...)`. Every parse therefore raised, was
swallowed by the bare `except`, and returned None — bases, guilds and players
were always empty.

PERFORMANCE
-----------
Level.sav is 100-500MB on a mature server and a full structural decode is the
single most expensive thing this dashboard can do. Three levers keep it off your
server's back:

1. Only the custom decoders we actually use are registered. The expensive ones
   (foliage grids, map objects, work data, dynamic items) are left as opaque
   byte arrays instead of being decoded into millions of Python objects. This is
   the difference between ~30s and several minutes, and costs us nothing because
   the dashboard does not surface that data.
2. We extract the handful of fields we need and throw the parse tree away. The
   giant dict is never cached or held between requests.
3. Callers run this in a niced subprocess with a timeout (see parse_worker.py).
"""

from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache
from typing import Any, Optional

from savefiles import (  # noqa: F401 - re-exported for backwards compatibility
    find_world_dirs,
    get_default_world_dir,
    get_level_sav_path,
    get_player_sav_path,
    list_player_uids,
    read_sav_bytes,
)

logger = logging.getLogger(__name__)

# Decoders worth their cost. Anything not listed stays as raw bytes.
_NEEDED_CUSTOM_PROPERTIES = (
    ".worldSaveData.GroupSaveDataMap",
    ".worldSaveData.BaseCampSaveData.Value.RawData",
    ".worldSaveData.CharacterSaveParameterMap.Value.RawData",
    # Chests, palboxes, breeding farms and every other placed object, with world
    # coordinates and the base camp each belongs to. Measured at no extra parse
    # cost on a real save, and it is what makes the map more than dots.
    ".worldSaveData.MapObjectSaveData",
)
# Only decoded when inventory detail is requested — noticeably heavier.
_ITEM_CUSTOM_PROPERTIES = (
    ".worldSaveData.ItemContainerSaveData.Value.RawData",
    # This is the one that actually decodes slot contents. It sits in the
    # library's DISABLED_PROPERTIES (it was broken in an older game version) but
    # decodes correctly on 1.0 saves, and without it every container reads as
    # empty.
    ".worldSaveData.ItemContainerSaveData.Value.Slots.Slots.RawData",
    ".worldSaveData.CharacterContainerSaveData.Value.Slots.Slots.RawData",
)


def _custom_properties(include_items: bool = False) -> dict:
    """Build the minimal custom-property set for this parse."""
    from palsav.paltypes import PALWORLD_CUSTOM_PROPERTIES

    wanted = list(_NEEDED_CUSTOM_PROPERTIES)
    if include_items:
        wanted += list(_ITEM_CUSTOM_PROPERTIES)

    return {
        key: PALWORLD_CUSTOM_PROPERTIES[key]
        for key in wanted
        if key in PALWORLD_CUSTOM_PROPERTIES
    }


def load_gvas(path: str, include_items: bool = False) -> Optional[Any]:
    """
    Read and decode one .sav into a GvasFile.

    Bytes come from savefiles.read_sav_bytes, which guards against torn reads
    while the server is autosaving.
    """
    try:
        from palsav.core import decompress_sav_to_gvas
        from palsav.gvas import GvasFile
        from palsav.paltypes import PALWORLD_TYPE_HINTS
    except ImportError:
        logger.error(
            "palsav not installed. Palworld 1.0 saves use Oodle (PlM) compression, which "
            "the old palworld-save-tools package cannot read. See backend/requirements.txt."
        )
        return None

    data = read_sav_bytes(path)
    if data is None:
        return None

    try:
        raw_gvas, _save_type = decompress_sav_to_gvas(data)
        return GvasFile.read(
            raw_gvas, PALWORLD_TYPE_HINTS, _custom_properties(include_items)
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to parse %s: %s: %s", os.path.basename(path), type(e).__name__, e)
        return None


def _world_save_data(gvas: Any) -> dict:
    props = getattr(gvas, "properties", {}) or {}
    return ((props.get("worldSaveData") or {}).get("value")) or {}


def _v(node: Any, *keys: str, default: Any = None) -> Any:
    """Walk nested {'value': ...} dicts safely."""
    cur = node
    for key in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
        if cur is None:
            return default
    return cur


def _prop(obj: dict, name: str, default: Any = None) -> Any:
    """Read a plain property value: obj[name]['value']."""
    return _v(obj, name, "value", default=default)


def _num(obj: dict, name: str, default: int = 0) -> int:
    """
    Read a numeric property.

    Palworld 1.0 stores Level and the Talent_* IVs as ByteProperties, which nest
    one level deeper than Int properties: {'value': {'type': 'None', 'value': 24}}
    rather than {'value': 24}. Handle both so this survives format churn.
    """
    value = _v(obj, name, "value")
    if isinstance(value, dict):
        value = value.get("value")
    if isinstance(value, bool):
        return int(value)
    return int(value) if isinstance(value, (int, float)) else default


def _slot(obj: dict, *keys: str) -> Any:
    """
    Walk into the Pal's slot descriptor, which tells us which container (party,
    palbox, base) it lives in. Palworld 1.0 renamed SlotID to SlotId.
    """
    container = obj.get("SlotId") or obj.get("SlotID")
    return _v(container, "value", *keys)


def _enum(obj: dict, name: str, default: str = "") -> str:
    """Read an enum property: obj[name]['value']['value'] -> 'EPalX::Y'."""
    val = _v(obj, name, "value", "value", default=default)
    return val if isinstance(val, str) else default


# ─── Bases ───────────────────────────────────────────────────────


def extract_base_camps(gvas: Any, guild_names: Optional[dict] = None) -> list[dict]:
    """Base camps with world coordinates, from BaseCampSaveData."""
    guild_names = guild_names or {}
    bases: list[dict] = []

    camps = _v(_world_save_data(gvas), "BaseCampSaveData", "value", default=[]) or []
    for i, entry in enumerate(camps if isinstance(camps, list) else []):
        raw = _v(entry, "value", "RawData", "value")
        if not isinstance(raw, dict):
            continue

        translation = _v(raw, "transform", "translation", default={}) or {}
        guild_id = str(raw.get("group_id_belong_to") or "")

        bases.append(
            {
                "id": str(raw.get("id") or _v(entry, "key") or f"base_{i}"),
                "name": str(raw.get("name") or "") or f"Base Camp {i + 1}",
                "guildId": guild_id,
                "guildName": guild_names.get(guild_id, "Unknown Guild"),
                "x": float(translation.get("x") or 0.0),
                "y": float(translation.get("y") or 0.0),
                "z": float(translation.get("z") or 0.0),
                "radius": float(raw.get("area_range") or 0.0),
                "state": raw.get("state"),
                "palCount": 0,
                "containerIds": [],
            }
        )

    return bases


# ─── Guilds ──────────────────────────────────────────────────────


def extract_guilds(gvas: Any) -> list[dict]:
    """Guilds from GroupSaveDataMap (only Guild-type groups carry members)."""
    guilds: list[dict] = []

    groups = _v(_world_save_data(gvas), "GroupSaveDataMap", "value", default=[]) or []
    for entry in groups if isinstance(groups, list) else []:
        raw = _v(entry, "value", "RawData", "value")
        if not isinstance(raw, dict):
            continue
        if raw.get("group_type") not in (
            "EPalGroupType::Guild",
            "EPalGroupType::IndependentGuild",
        ):
            continue

        members = []
        for player in raw.get("players") or []:
            info = player.get("player_info") or {}
            members.append(
                {
                    "uid": str(player.get("player_uid") or ""),
                    "name": str(info.get("player_name") or "Unknown"),
                    "lastOnline": info.get("last_online_real_time"),
                    "level": 0,
                    "isOnline": False,
                }
            )

        guilds.append(
            {
                "id": str(raw.get("group_id") or _v(entry, "key") or ""),
                "name": str(raw.get("guild_name") or raw.get("group_name") or "Unknown Guild"),
                "baseCampLevel": raw.get("base_camp_level", 0),
                "adminPlayerUid": str(raw.get("admin_player_uid") or ""),
                "members": members,
                "baseCampIds": [str(b) for b in (raw.get("base_ids") or [])],
            }
        )

    return guilds


def guild_name_map(guilds: list[dict]) -> dict[str, str]:
    return {g["id"]: g["name"] for g in guilds if g.get("id")}


# ─── Characters (players + Pals) ─────────────────────────────────

# Palworld 1.0 dropped Talent_Melee; it is kept here so older saves still map.
_TALENTS = (
    ("hp", "Talent_HP"),
    ("shot", "Talent_Shot"),
    ("defense", "Talent_Defense"),
    ("melee", "Talent_Melee"),
)


def extract_characters(gvas: Any) -> tuple[list[dict], list[dict]]:
    """
    Split CharacterSaveParameterMap into (player characters, Pals).

    Each Pal carries everything the breeding calculator needs: species, gender,
    IVs, passive skills, and which container it sits in.
    """
    players: list[dict] = []
    pals: list[dict] = []

    chars = _v(_world_save_data(gvas), "CharacterSaveParameterMap", "value", default=[]) or []
    for entry in chars if isinstance(chars, list) else []:
        key = entry.get("key") if isinstance(entry, dict) else None
        raw = _v(entry, "value", "RawData", "value")
        if not isinstance(raw, dict):
            continue

        obj = _v(raw, "object", "SaveParameter", "value")
        if not isinstance(obj, dict):
            continue

        player_uid = str(_v(key, "PlayerUId", "value", default="") or "")
        instance_id = str(_v(key, "InstanceId", "value", default="") or "")

        if _prop(obj, "IsPlayer", False) is True:
            players.append(
                {
                    "uid": player_uid,
                    "instanceId": instance_id,
                    "name": str(_prop(obj, "NickName", "") or "Unknown"),
                    "level": _num(obj, "Level", 1),
                    "exp": _num(obj, "Exp", 0),
                    "hp": _num(obj, "Hp", 0),
                    "maxHp": _num(obj, "MaxHP", 0),
                    "guildId": str(raw.get("group_id") or ""),
                }
            )
            continue

        character_id = str(_prop(obj, "CharacterID", "") or "")
        if not character_id:
            continue

        gender_raw = _enum(obj, "Gender")
        gender = "Female" if gender_raw.endswith("Female") else "Male" if gender_raw else "Unknown"

        passives = _v(obj, "PassiveSkillList", "value", "values", default=[]) or []

        pals.append(
            {
                "instanceId": instance_id,
                "ownerUid": str(_prop(obj, "OwnerPlayerUId", "") or player_uid),
                "characterId": character_id,
                "isBoss": character_id.startswith("BOSS_"),
                "speciesId": character_id[5:] if character_id.startswith("BOSS_") else character_id,
                "nickname": str(_prop(obj, "NickName", "") or ""),
                "gender": gender,
                "level": _num(obj, "Level", 1),
                "exp": _num(obj, "Exp", 0),
                "rank": _num(obj, "Rank", 1) or 1,
                "hp": _num(obj, "Hp", 0),
                "ivs": {
                    label: _num(obj, prop, 0) for label, prop in _TALENTS if prop in obj
                },
                "passiveSkills": [str(p) for p in passives],
                "containerId": str(_slot(obj, "ContainerId", "value", "ID", "value") or ""),
                "slotIndex": int(_slot(obj, "SlotIndex", "value") or 0),
                "guildId": str(raw.get("group_id") or ""),
            }
        )

    return players, pals


# ─── Player save files ───────────────────────────────────────────


def extract_player_save(gvas: Any, uid: str) -> dict:
    """
    Read one player's .sav. These are small (~100KB), so parsing them is cheap
    and safe to do on demand.

    The container IDs matter: they tell us which Pals are in the party versus
    the palbox, which the breeding UI needs.
    """
    props = getattr(gvas, "properties", {}) or {}
    save = _v(props, "SaveData", "value", default={}) or {}

    def container(name: str) -> str:
        return str(_v(save, name, "value", "ID", "value", default="") or "")

    inventory = _v(save, "InventoryInfo", "value", default={}) or {}

    return {
        "uid": uid,
        "instanceId": str(_v(save, "IndividualId", "value", "InstanceId", "value", default="") or ""),
        "playerUid": str(_v(save, "IndividualId", "value", "PlayerUId", "value", default="") or ""),
        "technologyPoints": int(_prop(save, "TechnologyPoint", 0) or 0),
        "ancientTechnologyPoints": int(_prop(save, "bossTechnologyPoint", 0) or 0),
        "unlockedRecipes": [
            str(r) for r in (_v(save, "UnlockedRecipeTechnologyNames", "value", "values", default=[]) or [])
        ],
        "palStorageContainerId": container("PalStorageContainerId"),
        "otomoCharacterContainerId": container("OtomoCharacterContainerId"),
        "inventoryContainerIds": {
            name: str(_v(inventory, name, "value", "ID", "value", default="") or "")
            for name in (
                "CommonContainerId",
                "DropSlotContainerId",
                "EssentialContainerId",
                "WeaponLoadOutContainerId",
                "PlayerEquipArmorContainerId",
                "FoodEquipContainerId",
            )
        },
    }


# ─── Map objects (points of interest) ────────────────────

# Only categorised objects are surfaced. A save contains thousands of walls,
# roofs and pillars that would bloat the payload and tell you nothing.
# Order matters — first match wins, so the specific patterns come first.
_POI_CATEGORIES: list[tuple[str, re.Pattern]] = [
    # Naturally occurring ore, coal, sulfur and quartz nodes. These are placed by
    # the world, not by players, and there are hundreds of them — the single
    # biggest untapped layer already sitting in every save.
    ("oreNode", re.compile(r"DamagableRock|DamagableWood|MeteorDrop", re.I)),
    # World loot. Fishing junk and oil-rig crates are worth telling apart from
    # ordinary chests: one is background noise, the other marks a raid target.
    ("fishingJunk", re.compile(r"TreasureBox_FishingJunk", re.I)),
    ("oilrigChest", re.compile(r"TreasureBox_Oilrig", re.I)),
    ("chest", re.compile(r"TreasureBox|ItemChest|GuildChest", re.I)),
    ("drop", re.compile(r"CommonDropItem3D|DroppedCharacter", re.I)),

    ("palbox", re.compile(r"PalBox", re.I)),
    ("breeding", re.compile(r"MonsterFarm", re.I)),
    ("statue", re.compile(r"GoddessStatue", re.I)),
    ("crafting", re.compile(r"WeaponFactory|SphereFactory|Workbench|WorkBench|RepairBench", re.I)),
    ("production", re.compile(r"OilPump|StonePit|QuartzPit|CoalPit|CopperPit|BlastFurnace|Deforest|ElectricGenerator|Crusher|FlourMill|IceCrusher", re.I)),
    ("farm", re.compile(r"FarmBlock", re.I)),
    ("storage", re.compile(r"PalFoodBox|Refrigerator|PalMedicineBox|CoolerBox", re.I)),
    ("comfort", re.compile(r"Spa|PlayerBed|MedicalPalBed", re.I)),
    ("egg", re.compile(r"HatchingPalEgg|PalEgg", re.I)),
    ("defense", re.compile(r"DefenseWall|Turret|DefenseOther", re.I)),
]

ZERO_GUID = "00000000-0000-0000-0000-000000000000"


def _categorise(map_object_id: str) -> Optional[str]:
    for name, pattern in _POI_CATEGORIES:
        if pattern.search(map_object_id):
            return name
    return None


def extract_map_objects(gvas: Any) -> list[dict]:
    """
    Placed world objects with coordinates.

    `initital_transform_cache` (the game's own spelling) carries the world
    translation, and `base_camp_id_belong_to` attributes an object to a base —
    which is what lets chest contents be grouped per base rather than dumped in
    one undifferentiated pile.
    """
    objects: list[dict] = []

    entries = _v(_world_save_data(gvas), "MapObjectSaveData", "value", "values", default=[]) or []
    for entry in entries if isinstance(entries, list) else []:
        object_id = str(_v(entry, "MapObjectId", "value", default="") or "")
        category = _categorise(object_id)
        if not category:
            continue

        raw = _v(entry, "Model", "value", "RawData", "value")
        if not isinstance(raw, dict):
            continue

        translation = _v(raw, "initital_transform_cache", "translation", default={}) or {}
        concrete = _v(entry, "ConcreteModel", "value", "RawData", "value") or {}

        # An object belongs to a base camp, or it was placed by the world. On a
        # real save this splits roughly 1,019 base-placed to 3,604 world-placed,
        # and they want completely different map layers: one is "what my guild
        # built", the other is "what is out there to go and find".
        base_camp = str(raw.get("base_camp_id_belong_to") or "")
        world_placed = base_camp in ("", "None", ZERO_GUID)

        objects.append(
            {
                "id": str(raw.get("instance_id") or ""),
                "kind": object_id,
                "category": category,
                "x": float(translation.get("x") or 0.0),
                "y": float(translation.get("y") or 0.0),
                "z": float(translation.get("z") or 0.0),
                "baseCampId": "" if world_placed else base_camp,
                "worldPlaced": world_placed,
                "guildId": str(raw.get("group_id_belong_to") or ""),
                "buildPlayerUid": str(raw.get("build_player_uid") or ""),
                # Chest-specific extras, absent on other object types.
                "opened": concrete.get("opened"),
                "grade": concrete.get("treasure_grade_type"),
            }
        )

    return objects


# ─── Player progression ──────────────────────────────────

# RecordData flag maps we surface. Each is a list of {key, value} pairs.
_PROGRESS_FLAGS: list[tuple[str, str]] = [
    ("towerBosses", "TowerBossDefeatFlag"),
    ("fieldBosses", "NormalBossDefeatFlag"),
    ("fastTravel", "FastTravelPointUnlockFlag"),
    ("paldeck", "PaldeckUnlockFlag"),
    ("effigies", "RelicObtainForInstanceFlag"),
    ("areasFound", "FindAreaFlagMap"),
    ("dungeonsCleared", "FixedDungeonClearCount"),
]

_PROGRESS_COUNTERS: list[tuple[str, str]] = [
    ("palsCaptured", "PalCaptureCount"),
    ("speciesCaptured", "TribeCaptureCount"),
    ("itemsCrafted", "CraftItemCount"),
    ("palRankUps", "PalRankupCount"),
    ("mutations", "MutationCount"),
    ("raidBossesDefeated", "RaidBossDefeatCount"),
]


def _flag_entries(record: dict, prop: str) -> list[dict]:
    value = _v(record, prop, "value")
    return value if isinstance(value, list) else []


def extract_player_progress(gvas: Any) -> dict[str, Any]:
    """
    Progression stats from one player's RecordData.

    IMPORTANT: these flag maps only contain entries the player has *obtained* —
    verified by comparing five players on a real server, whose map sizes all
    differ. The absolute number of fast-travel points or towers in the game is
    therefore NOT recoverable from a single save. Callers should compare against
    the union across all players (see `progress_totals`) and present it as
    "known on this server", which is a floor rather than a true total.
    """
    props = getattr(gvas, "properties", {}) or {}
    save = _v(props, "SaveData", "value", default={}) or {}
    record = _v(save, "RecordData", "value", default={}) or {}

    progress: dict[str, Any] = {
        "technologyPoints": _num(save, "TechnologyPoint", 0),
        "ancientTechnologyPoints": _num(save, "bossTechnologyPoint", 0),
        "technologiesUnlocked": len(
            _v(save, "UnlockedRecipeTechnologyNames", "value", "values", default=[]) or []
        ),
    }

    for label, prop in _PROGRESS_FLAGS:
        entries = _flag_entries(record, prop)
        obtained = [e for e in entries if e.get("value") not in (None, False, 0)]
        progress[label] = {
            "obtained": len(obtained),
            "keys": [str(e.get("key")) for e in obtained],
        }

    for label, prop in _PROGRESS_COUNTERS:
        entries = _flag_entries(record, prop)
        total = 0
        for entry in entries:
            value = entry.get("value")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                total += int(value)
            elif value is True:
                total += 1
        progress[label] = {"total": total, "distinct": len(entries)}

    return progress


# Progress categories whose true total is computable from the game's own data
# tables, so they need no published estimate at all.
_EXACT_TOTALS: dict[str, str] = {
    "fastTravel": "fastTravelPoints",   # 174
    "paldeck": "paldeckForms",          # 303 — PaldeckUnlockFlag keys on forms
}


@lru_cache(maxsize=1)
def _reference_totals() -> dict[str, tuple[int, str]]:
    """
    Denominators as {label: (total, source)}.

    Two tiers. `gamedata` figures are computed from the game's own data tables
    and are exact. `reference` figures are published community counts for things
    the tables do not enumerate (tower bosses, effigies, sealed realms) and are
    only as good as their source.
    """
    totals: dict[str, tuple[int, str]] = {}

    path = os.path.join(os.path.dirname(__file__), "data", "reference_totals.json")
    try:
        with open(path) as f:
            for label, value in (json.load(f).get("totals") or {}).items():
                totals[label] = (int(value), "reference")
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not load reference totals: %s", e)

    # Exact figures win over published ones.
    try:
        import gamedata

        exact = gamedata.totals()
        for label, key in _EXACT_TOTALS.items():
            if exact.get(key):
                totals[label] = (int(exact[key]), "gamedata")
    except Exception as e:  # noqa: BLE001
        logger.warning("Game data unavailable, using published totals only: %s", e)

    return totals


def progress_totals(per_player: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """
    Denominator per category, preferring published totals over observation.

    Save files only record obtained entries, so the union across players is a
    floor. Where a published 1.0 figure exists we use it instead and mark the
    source, but never below what someone has actually obtained — a stale
    reference figure must not produce a negative "remaining".
    """
    reference = _reference_totals()
    observed: dict[str, set[str]] = {label: set() for label, _ in _PROGRESS_FLAGS}

    for player in per_player:
        for label, _prop in _PROGRESS_FLAGS:
            entry = player.get(label)
            if isinstance(entry, dict):
                observed[label].update(entry.get("keys") or [])

    totals: dict[str, dict[str, Any]] = {}
    for label, keys in observed.items():
        seen = len(keys)
        known = reference.get(label)
        if known and known[0] >= seen:
            totals[label] = {"total": known[0], "source": known[1]}
        else:
            # Either we have no figure, or ours is lower than what somebody has
            # actually obtained — in which case ours is stale, not their save.
            totals[label] = {"total": seen, "source": "discovered"}
    return totals


# ─── Item containers ─────────────────────────────────────────────


def extract_containers(gvas: Any) -> dict[str, list[dict]]:
    """
    Map container GUID -> slot contents. Only populated when the Level.sav was
    parsed with include_items=True.
    """
    containers: dict[str, list[dict]] = {}

    entries = _v(_world_save_data(gvas), "ItemContainerSaveData", "value", default=[]) or []
    for entry in entries if isinstance(entries, list) else []:
        container_id = str(_v(entry, "key", "ID", "value", default="") or "")
        if not container_id:
            continue

        slots = []
        raw_slots = _v(entry, "value", "Slots", "value", "values", default=[]) or []
        for slot in raw_slots:
            raw = _v(slot, "RawData", "value") or {}
            if not isinstance(raw, dict):
                continue
            # `count` lives at the slot root; only `static_id` is nested under
            # `item`. Reading count from inside `item` yields 0 for everything.
            item_id = str(_v(raw, "item", "static_id", default="") or "")
            count = int(raw.get("count") or 0)
            slots.append(
                {
                    "slotIndex": int(raw.get("slot_index") or 0),
                    "itemId": item_id,
                    "itemName": item_id,
                    "stackCount": count,
                    "isEmpty": not item_id or count <= 0,
                }
            )

        containers[container_id] = slots

    return containers
