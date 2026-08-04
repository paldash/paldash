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


# An unnamed base keeps the game's own placeholder rather than an empty string:
# 新規生成拠点テンプレート名0(仮) — "newly generated base template name 0
# (provisional)". Every base on the reference world carries one, so passing it
# through leaves eleven identically-named bases in the UI. The trailing digits
# are the only thing distinguishing them, and they are not stable base numbers,
# so we fall back to positional naming instead.
_PLACEHOLDER_BASE_NAME = re.compile(r"新規生成拠点テンプレート名|NewlyCreatedBaseCamp|BaseCampTemplateName")


def _base_name(raw_name: str, index: int) -> tuple[str, bool]:
    """(display name, whether the player actually named it)."""
    name = (raw_name or "").strip()
    if not name or _PLACEHOLDER_BASE_NAME.search(name):
        return f"Base Camp {index + 1}", False
    return name, True


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
        name, player_named = _base_name(str(raw.get("name") or ""), i)

        bases.append(
            {
                "id": str(raw.get("id") or _v(entry, "key") or f"base_{i}"),
                "name": name,
                "playerNamed": player_named,
                "guildId": guild_id,
                "guildName": guild_names.get(guild_id, "Unknown Guild"),
                "x": float(translation.get("x") or 0.0),
                "y": float(translation.get("y") or 0.0),
                "z": float(translation.get("z") or 0.0),
                "radius": float(raw.get("area_range") or 0.0),
                "state": raw.get("state"),
                "guildPalCount": 0,
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

# Pal Soul upgrades — the statue ones, separate from the condenser `Rank`.
#
# **`Rank_Defence` is spelled the British way, and only that one.** `Rank_Attack`
# and `Rank_HP` are not, and `Talent_Defense` beside it is American. Reading
# `Rank_Defense` finds nothing and yields a silent zero: the defence souls a
# player actually spent simply do not appear in the stat, with no error to
# follow. Measured on the reference world — 11 Pals carry `Rank_Defence`, none
# carry `Rank_Defense`.
#
# All four are absent on a Pal with no souls invested, which reads as 0.
_SOUL_RANKS = (
    ("hp", "Rank_HP"),
    ("attack", "Rank_Attack"),
    ("defense", "Rank_Defence"),
    ("craftSpeed", "Rank_CraftSpeed"),
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

        pal = _pal_record(obj, instance_id, player_uid, str(raw.get("group_id") or ""))
        if pal is not None:
            pals.append(pal)

    return players, pals


def _pal_record(
    obj: dict, instance_id: str, player_uid: str, guild_id: str
) -> Optional[dict]:
    """
    One Pal, from its `SaveParameter`.

    Split out of `extract_characters` because the same struct appears in a
    second place the parser now reads: a player's Dimensional Pal Storage lives
    in its own file (`<UID>_dps.sav`) rather than in `CharacterSaveParameterMap`,
    and a Pal in one was invisible to every count in this dashboard. Two readers
    for one struct is how the two drift, and the fields here feed breeding, the
    editor and `palstats` alike.
    """
    character_id = str(_prop(obj, "CharacterID", "") or "")
    if not character_id:
        return None

    gender_raw = _enum(obj, "Gender")
    gender = "Female" if gender_raw.endswith("Female") else "Male" if gender_raw else "Unknown"

    passives = _v(obj, "PassiveSkillList", "value", "values", default=[]) or []
    # `EquipWaza` is an EnumProperty whose values all carry an `EPalWazaID::`
    # prefix; the bundled activeSkills table is keyed without it. Strip here
    # so everything downstream — names, the editor, validation — speaks one
    # language. `MasteredWaza` is deliberately not read: it is absent on
    # 1,563 of the reference world's 1,905 Pals.
    equipped = _v(obj, "EquipWaza", "value", "values", default=[]) or []

    return (
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
            # Everything below feeds `palstats.py`. Read here rather than
            # re-parsed there, because the whole world is already open.
            "soulRanks": {
                label: _num(obj, prop, 0) for label, prop in _SOUL_RANKS if prop in obj
            },
            # Trust, which the game shows as the heart meter. Absent on a Pal
            # nobody has bonded with, which is genuinely zero rather than
            # unknown.
            "friendshipPoint": _num(obj, "FriendshipPoint", 0),
            # The gold "lucky" variant. Distinct from `isBoss` (the alpha
            # forms): a lucky Pal keeps its ordinary species id, so this flag
            # is the only thing that says so.
            "isLucky": bool(_prop(obj, "IsRarePal", False)),
            "passiveSkills": [str(p) for p in passives],
            "activeSkills": [
                str(w).split("::", 1)[-1] for w in equipped
            ] if "EquipWaza" in obj else None,
            "containerId": str(_slot(obj, "ContainerId", "value", "ID", "value") or ""),
            "slotIndex": int(_slot(obj, "SlotIndex", "value") or 0),
            "guildId": guild_id,
            # ── Condition ────────────────────────────────────────
            #
            # AN AFFLICTION IS A PROPERTY THAT EXISTS. Measured on the live
            # world: `HungerType` is present on 97 of 2,963 Pals, `WorkerSick`
            # on 54, `PhysicalHealth` on 21 — a healthy Pal does not carry the
            # field at all. So `None` here means healthy, and it is the observed
            # state of the overwhelming majority rather than a default this code
            # invented. It is also why curing one is a *deletion*; see
            # `charedit.PAL_CLEARABLE`.
            "sanity": _prop(obj, "SanityValue", None),
            "fullStomach": _prop(obj, "FullStomach", None),
            "hungerType": _enum(obj, "HungerType").split("::")[-1] or None,
            "workerSick": _enum(obj, "WorkerSick").split("::")[-1] or None,
            "physicalHealth": _enum(obj, "PhysicalHealth").split("::")[-1] or None,
            "currentWork": _enum(obj, "CurrentWorkSuitability").split("::")[-1] or None,
            # ── Identity and history ─────────────────────────────
            "skinName": str(_prop(obj, "SkinName", "") or "") or None,
            "isImported": bool(_prop(obj, "bImportedCharacter", False)),
            "isAwakened": bool(_prop(obj, "bIsAwakening", False)),
            "favoriteIndex": _num(obj, "FavoriteIndex", 0),
            # Every previous owner, oldest first. Present on 100% of Pals and
            # the only record of a trade there is.
            "previousOwners": [
                str(u) for u in (_v(obj, "OldOwnerPlayerUIds", "value", "values") or [])
            ],
            # The learned-move pool, as bare ids like `activeSkills`. Absent on
            # 75% of Pals, which is why it is readable everywhere and writable
            # only where it already exists.
            "masteredSkills": [
                str(w).split("::", 1)[-1]
                for w in (_v(obj, "MasteredWaza", "value", "values") or [])
            ] if "MasteredWaza" in obj else None,
            # Work-suitability ranks the player bought with Pal Souls, and the
            # work types they switched OFF for this Pal. Both are per-Pal
            # decisions no species table can supply.
            "workRanks": {
                str(_v(e, "WorkSuitability", "value", "value") or "").split("::")[-1]:
                    _num(e, "Rank", 0)
                for e in (_v(obj, "GotWorkSuitabilityAddRankList", "value", "values") or [])
                if isinstance(e, dict)
            },
            "workDisabled": [
                str(w).split("::")[-1]
                for w in (_v(obj, "WorkSuitabilityOptionInfo", "value",
                             "OffWorkSuitabilityList", "value", "values") or [])
            ],
        }
    )

    return players, pals


def extract_dimension_storage(gvas: Any, owner_uid: str) -> list[dict]:
    """
    Pals in one player's Dimensional Pal Storage — a **separate save file**.

    This is the fourth place a Pal can be and the only one that is not in
    `Level.sav` at all. It lives in `Players/<UID>_dps.sav`, whose single
    property is a `SaveParameterArray` of `PalDimensionPalStorageSaveParameter`
    — 9,600 slots, each an ordinary `SaveParameter` plus its own `InstanceId`.
    Nothing in this project had ever opened that file.

    So a Pal moved into Dimensional Pal Storage did not merely land in an
    unrecognised container: it **vanished from every count in the dashboard**.
    The report that found this was a player being shown breeding routes to a
    Lamball while six of their own sat in storage — and the planner was working
    correctly on the data it had, which is why it took a file listing rather
    than a code read to spot.

    Measured on the live world: two of five players have the file, holding 53
    and 2 Pals against 9,600 slots. **Empty slots are the overwhelming majority
    and they are not absent** — every slot is materialised, with an empty
    `SaveParameter`, so the occupied test is a real `CharacterID` rather than a
    length check.

    `containerId` is deliberately left empty. These Pals are not in
    `CharacterContainerSaveData`, and inventing an id would let anything that
    joins on one — base attribution, the slot editor, `palclone` — believe it
    could address them.
    """
    slots = _v(getattr(gvas, "properties", {}) or {},
               "SaveParameterArray", "value", "values", default=[]) or []

    pals: list[dict] = []
    for slot in slots if isinstance(slots, list) else []:
        obj = _v(slot, "SaveParameter", "value")
        if not isinstance(obj, dict) or not obj:
            continue

        # AN EMPTY SLOT IS NOT AN ABSENT SLOT, AND IT IS NOT BLANK EITHER.
        # All 9,600 are materialised, and a free one carries the *string*
        # "None" as its CharacterID — so a plain truthiness test on the id
        # accepts every one of them. The first version of this returned 9,600
        # Pals per player, which is not a subtle failure but would have become
        # one the moment anything summed it.
        species = str(_prop(obj, "CharacterID", "") or "")
        if not species or species == "None":
            continue

        instance_id = str(_v(slot, "InstanceId", "value", default="") or "")
        pal = _pal_record(obj, instance_id, owner_uid, "")
        if pal is None:
            continue

        # The file is per player, so its contents belong to that player even
        # where the record's own OwnerPlayerUId is blank — which it is for a Pal
        # that was placed there straight from a capture.
        if not str(pal.get("ownerUid") or "").strip("0-"):
            pal["ownerUid"] = owner_uid
        pal["location"] = "dimension"
        # Named as well as typed, so the breeding planner's "where is this
        # parent" note reads "in Dimensional Pal Storage" rather than "in
        # dimension". Same field the guild-built stores use.
        pal["storageKind"] = "Dimensional Pal Storage"
        pal["containerId"] = ""
        pals.append(pal)

    return pals


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

    def _platform(node: dict) -> str:
        """
        `EPalPlayerPlatform::Steam` -> `Steam`, or "" when the field is absent.

        Empty rather than a default of "Steam": an older save that predates the
        field would otherwise assert a platform nobody checked, which is the kind
        of confident-but-unverified claim this project keeps having to correct.
        """
        raw = str(_v(node, "PlayerPlatform", "value", "value", default="") or "")
        return raw.split("::")[-1] if "::" in raw else raw

    def container(name: str) -> str:
        return str(_v(save, name, "value", "ID", "value", default="") or "")

    inventory = _v(save, "InventoryInfo", "value", default={}) or {}

    return {
        "uid": uid,
        "instanceId": str(_v(save, "IndividualId", "value", "InstanceId", "value", default="") or ""),
        "playerUid": str(_v(save, "IndividualId", "value", "PlayerUId", "value", default="") or ""),
        "technologyPoints": int(_prop(save, "TechnologyPoint", 0) or 0),
        "ancientTechnologyPoints": int(_prop(save, "bossTechnologyPoint", 0) or 0),
        # Which store the player came from. The game's own enum is
        # `EPalPlayerPlatform::{Steam,Xbox,PS5,Mac,None}` — read out of the server
        # binary, not guessed — so a save can legitimately hold console players.
        #
        # Surfaced because this project has only ever been run against Steam
        # accounts, and everything downstream treats a uid as opaque on the
        # assumption that holds for other platforms too. If a console player ever
        # appears, this field is what makes that visible rather than something to
        # deduce from a uid that looks unusual. See docs/CROSSPLAY.md.
        "platform": _platform(save),
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


# ─── Container ownership ─────────────────────────────────

# The module on a placed object that points at its storage.
_ITEM_CONTAINER_MODULE = "ItemContainer"


def _target_container_id(entry: dict) -> str:
    """The container a placed object stores into, or "" if it has none."""
    modules = _v(entry, "ConcreteModel", "value", "ModuleMap", "value", default=[]) or []
    for module in modules if isinstance(modules, list) else []:
        if _ITEM_CONTAINER_MODULE not in str(module.get("key") or ""):
            continue
        target = _v(module, "value", "RawData", "value", "target_container_id")
        return str(target or "")
    return ""


def extract_container_ownership(gvas: Any) -> dict[str, dict]:
    """
    container GUID -> the object and base that owns it.

    This is the join that makes per-base inventory possible, and it is exact
    rather than spatial: the game itself records which base a placed object
    belongs to, so there is no radius guessing.

        BaseCamp.id
          <- Model.RawData.base_camp_id_belong_to
             MapObjectSaveData entry
          -> ConcreteModel.ModuleMap[ItemContainer].RawData.target_container_id
          -> ItemContainerSaveData key

    Do NOT substitute `group_id_belong_to` for the base id. It is the *guild*,
    and on the reference world none of its six values match a base camp id — a
    naive swap silently collapses every base in a guild into one pile.

    Measured on the reference world: 3,370 objects carry a container id, 3 of
    them dangle (the container is already gone), 262 attribute to the 11 real
    bases and 3,105 are world-placed chests and drops. No container is ever
    referenced by two objects.
    """
    import gamedata

    ownership: dict[str, dict] = {}

    entries = _v(_world_save_data(gvas), "MapObjectSaveData", "value", "values", default=[]) or []
    for entry in entries if isinstance(entries, list) else []:
        container_id = _target_container_id(entry)
        if not container_id:
            continue

        raw = _v(entry, "Model", "value", "RawData", "value")
        if not isinstance(raw, dict):
            continue

        base_camp = str(raw.get("base_camp_id_belong_to") or "")
        world_placed = base_camp in ("", "None", ZERO_GUID)
        kind = str(_v(entry, "MapObjectId", "value", default="") or "")

        ownership[container_id] = {
            "objectId": str(raw.get("instance_id") or ""),
            "kind": kind,
            # The bundled database already names these properly — ItemChest_02
            # is "Metal Chest", PalFoodBox is "Feed Box". Unknown ids fall back
            # to humanize() rather than failing.
            "kindName": gamedata.structure_name(kind),
            "category": _categorise(kind),
            "baseCampId": "" if world_placed else base_camp,
            "guildId": str(raw.get("group_id_belong_to") or ""),
            "builderUid": str(raw.get("build_player_uid") or ""),
            "worldPlaced": world_placed,
        }

    return ownership


# Byte offset of the worker container GUID inside `WorkerDirector.RawData`.
# Measured across all 11 reference-world bases; the blob is 118 bytes and the
# base camp's own id sits at 0.
_WORKER_GUID_OFFSET = 98


def extract_base_workers(gvas: Any) -> dict[str, str]:
    """
    `{character_container_id: base_camp_id}` — which base each Pal works at.

    This was previously documented here as impossible. It is not: every base's
    `WorkerDirector` names the character container holding its workers, and the
    join is exact rather than spatial. Measured on the reference world: **11 of
    11 bases resolve, one container each**, and those eleven are exactly the
    20/16/13/8-slot containers that are neither a palbox (960) nor a party (5).

    **`WorkerDirector.RawData` is an opaque ByteProperty, so this reads it by
    offset.** The blob is 118 bytes with a fixed layout — the base camp id at 0,
    the worker container id at 98. Those offsets are *measured*, not looked up,
    which is the same footing `scripts/upackage.py` stands on and carries the
    same obligation: verify, do not assume. So the decoded id must resolve to a
    real entry in `CharacterContainerSaveData`, and a blob of the wrong length is
    skipped. A game update that changes the layout therefore yields **nothing**,
    which degrades per-base counts to the guild figure they came from — never a
    confident wrong answer about whose Pal is where.

    GUID byte order is `u32le`, the same convention `extract-effigies.py`
    documents: four little-endian uint32s printed big-endian.
    """
    import struct

    world = _world_save_data(gvas)

    known: set[str] = set()
    for entry in _v(world, "CharacterContainerSaveData", "value", default=[]) or []:
        container_id = str(_v(entry, "key", "ID", "value") or "").lower()
        if container_id:
            known.add(container_id)

    def _guid_at(blob: bytes, offset: int) -> str:
        a, b, c, d = struct.unpack_from("<4I", blob, offset)
        return "%08x-%04x-%04x-%04x-%04x%08x" % (
            a, b >> 16, b & 0xFFFF, c >> 16, c & 0xFFFF, d
        )

    workers: dict[str, str] = {}
    camps = _v(world, "BaseCampSaveData", "value", default=[]) or []
    for entry in camps if isinstance(camps, list) else []:
        raw = _v(entry, "value", "RawData", "value")
        if not isinstance(raw, dict):
            continue
        base_id = str(raw.get("id") or "")
        blob = _v(entry, "value", "WorkerDirector", "value", "RawData", "value", "values")
        if not isinstance(blob, (bytes, bytearray)) or len(blob) < _WORKER_GUID_OFFSET + 16:
            continue
        container_id = _guid_at(bytes(blob), _WORKER_GUID_OFFSET)
        # The verification, and the whole reason reading at an offset is
        # acceptable here: a wrong offset produces a GUID that resolves to
        # nothing, so it is dropped rather than attributed.
        if base_id and container_id in known:
            workers[container_id] = base_id

    if camps and not workers:
        logger.warning(
            "No base worker containers resolved across %d bases — the "
            "WorkerDirector layout may have changed; per-base Pal counts will "
            "be unavailable",
            len(camps),
        )
    return workers


_CHARACTER_CONTAINER_MODULE = "CharacterContainer"


def extract_pal_storage(gvas: Any) -> dict[str, dict]:
    """
    `{character_container_id: {kind, kindName, baseCampId, guildId}}` for Pals
    held by a *placed structure* rather than by a player or a base's workforce.

    This closes the gap that produced `location: "other"`. A Pal sits in one of
    four kinds of place, and only three were recognised: a player's palbox, their
    party, or a base's worker container. The fourth is a structure the guild
    built that stores Pals — and the save records it plainly, through the same
    module map `extract_container_ownership` already walks for chests:

        MapObjectSaveData[]
          -> ConcreteModel.ModuleMap["…::CharacterContainer"]
             .RawData.target_container_id
          -> CharacterContainerSaveData[].key.ID

    **No byte offsets here.** `extract_base_workers` reads `WorkerDirector` at a
    measured offset because that blob is opaque; this module's `RawData` decodes
    to a named `target_container_id` field, so the same fact is available without
    the assumption. Where the game gives a name, take the name.

    Measured on the reference world: **2 of 2** `CharacterContainer` modules
    resolve, both `PalBooth` — the game's own tables call it "Flea Market
    (Pals)" — each with its own five-slot container, and both attribute to a
    real base. Those two are exactly the pair this file used to call "orphaned
    containers with no live owner". They were never orphans; nothing had looked
    at the module map for them.

    **The reference world cannot exercise the case that matters most**, and that
    is worth stating rather than hiding. It has one `DimensionPalStorage` and
    three `GlobalPalStorage` objects, all with an *empty* `ModuleMap` — nobody
    stored a Pal in them, so the game has not created their containers yet. A
    world where someone has will resolve them here automatically: the join keys
    on the module type, not on a list of structure names, so a Pal-holding
    structure this code has never heard of still classifies.

    Verification is the same obligation `extract_base_workers` carries: an id
    that does not resolve in `CharacterContainerSaveData` is dropped, so a
    changed layout yields nothing rather than a confident wrong answer.
    """
    import gamedata

    world = _world_save_data(gvas)

    known: set[str] = set()
    for entry in _v(world, "CharacterContainerSaveData", "value", default=[]) or []:
        container_id = str(_v(entry, "key", "ID", "value") or "").lower()
        if container_id:
            known.add(container_id)

    storage: dict[str, dict] = {}
    entries = _v(world, "MapObjectSaveData", "value", "values", default=[]) or []
    for entry in entries if isinstance(entries, list) else []:
        modules = _v(entry, "ConcreteModel", "value", "ModuleMap", "value", default=[]) or []
        for module in modules if isinstance(modules, list) else []:
            if _CHARACTER_CONTAINER_MODULE not in str(module.get("key") or ""):
                continue
            container_id = str(
                _v(module, "value", "RawData", "value", "target_container_id") or ""
            ).lower()
            if not container_id or container_id not in known:
                continue

            raw = _v(entry, "Model", "value", "RawData", "value")
            raw = raw if isinstance(raw, dict) else {}
            base_camp = str(raw.get("base_camp_id_belong_to") or "")
            kind = str(_v(entry, "MapObjectId", "value", default="") or "")

            storage[container_id] = {
                "kind": kind,
                "kindName": gamedata.structure_name(kind),
                "baseCampId": "" if base_camp in ("", "None", ZERO_GUID) else base_camp,
                # The guild, not the base — a Pal in a shared store belongs to
                # everyone in the guild, which is what makes it breedable for a
                # member who is not the one who caught it.
                "guildId": str(raw.get("group_id_belong_to") or ""),
            }

    return storage


def summarise_base_storage(
    containers: dict[str, list[dict]],
    ownership: dict[str, dict],
    bases: list[dict],
) -> list[dict]:
    """
    Per-base storage: which containers a base owns, how full they are, and what
    is in them.

    Bases with no storage still come back (with zeroes) — "this base has no
    chests" is a real answer and silently dropping it looks like a bug.
    """
    from collections import defaultdict

    by_base: dict[str, list[str]] = defaultdict(list)
    for container_id, owner in ownership.items():
        if owner["baseCampId"] and container_id in containers:
            by_base[owner["baseCampId"]].append(container_id)

    summaries: list[dict] = []
    for base in bases:
        base_id = base.get("id", "")
        container_ids = by_base.get(base_id, [])

        totals: dict[str, int] = defaultdict(int)
        used_slots = 0
        total_slots = 0
        breakdown: list[dict] = []

        for container_id in container_ids:
            slots = containers.get(container_id, [])
            occupied = [s for s in slots if not s["isEmpty"]]
            used_slots += len(occupied)
            total_slots += len(slots)
            for slot in occupied:
                totals[slot["itemId"]] += slot["stackCount"]

            owner = ownership[container_id]
            breakdown.append(
                {
                    "containerId": container_id,
                    "kind": owner["kind"],
                    "kindName": owner["kindName"],
                    "category": owner["category"],
                    "usedSlots": len(occupied),
                    "totalSlots": len(slots),
                    "itemCount": sum(s["stackCount"] for s in occupied),
                }
            )

        breakdown.sort(key=lambda c: (-c["itemCount"], c["kindName"]))

        summaries.append(
            {
                "baseId": base_id,
                "baseName": base.get("name", ""),
                "guildId": base.get("guildId", ""),
                "guildName": base.get("guildName", ""),
                "containerCount": len(container_ids),
                "usedSlots": used_slots,
                "totalSlots": total_slots,
                "fillPercent": round(100 * used_slots / total_slots, 1) if total_slots else 0.0,
                "itemCount": sum(totals.values()),
                "uniqueItems": len(totals),
                "items": _rank_items(totals),
                "containers": breakdown,
            }
        )

    summaries.sort(key=lambda b: -b["itemCount"])
    return summaries


def _rank_items(totals: dict[str, int]) -> list[dict]:
    """Item totals as a sorted, name-resolved list."""
    import gamedata

    return [
        {"itemId": item_id, "itemName": gamedata.item_name(item_id), "count": count}
        for item_id, count in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


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
            # A non-zero local id means this item has its own record in
            # DynamicItemSaveData — durability, eggs, anything individually
            # tracked. Overwriting such a slot orphans that record, and a new
            # one cannot be fabricated, so the importer refuses to touch them.
            local_id = str(
                _v(raw, "item", "dynamic_id", "local_id_in_created_world", default="") or ""
            )
            slots.append(
                {
                    "slotIndex": int(raw.get("slot_index") or 0),
                    "itemId": item_id,
                    "itemName": item_id,
                    "stackCount": count,
                    "isEmpty": not item_id or count <= 0,
                    "hasDynamicId": bool(local_id) and local_id != ZERO_GUID,
                }
            )

        # THE SAVE STORES ONLY OCCUPIED SLOTS; `SlotNum` IS THE REAL CAPACITY.
        #
        # Same shape as `CharacterContainerSaveData`, where "free space is
        # SlotNum - len(slots)" is already documented — item containers do it too
        # and this code did not account for it. Three consequences, all of which
        # showed up in use:
        #
        #   * the inventory editor had no empty rows to write into, so adding an
        #     item meant overwriting one that was already there;
        #   * `totalSlots` was the occupied count, so `fillPercent` was ~100% for
        #     every base on the reference world and the "90%+ full" warning fired
        #     permanently and meant nothing;
        #   * stored `slot_index` values are sparse, so a UI numbering rows by
        #     position disagreed with the indices the writer uses — which is where
        #     the out-of-range errors came from.
        #
        # Padding here rather than in each consumer, because the slots genuinely
        # exist in the container; the save just declines to write empty ones.
        capacity = _num(entry.get("value") or {}, "SlotNum", 0)
        present = {s["slotIndex"] for s in slots}
        for index in range(capacity):
            if index not in present:
                slots.append({
                    "slotIndex": index,
                    "itemId": "",
                    "itemName": "",
                    "stackCount": 0,
                    "isEmpty": True,
                    "hasDynamicId": False,
                })
        slots.sort(key=lambda s: s["slotIndex"])

        containers[container_id] = slots

    return containers
