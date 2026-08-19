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
from datetime import datetime, timedelta
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
    # The Pal Lab research tree's per-guild progress. Without this the blob is
    # opaque bytes and `extract_guild_research` yields nothing. Measured at
    # **+0.4s on a 5.0s parse** of the live world — the first measurement said
    # 1.5s and was warmup noise, which reversing the order exposed.
    ".worldSaveData.GuildExtraSaveDataMap.Value.Lab.RawData",
    # Who the game has ACTUALLY assigned to each job (extract_work_assignments).
    # Measured cost on refworld, interleaved against a control because the naive
    # A-then-B ordering here already produced a warmup artifact once: **+0.30s
    # median on a 3.06s parse**. Worth it — this is the only record of real
    # assignments, and inferring them is a different and weaker answer.
    ".worldSaveData.WorkSaveData",
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


def _flag(obj: dict, name: str) -> Optional[bool]:
    """
    Read a boolean property, distinguishing "false" from "not stored".

    `bool(_prop(...))` collapses the two, and that collapse reaches further than
    it looks: `charedit` refuses to write a property a Pal does not carry, so a
    flat `False` makes the editor offer a checkbox the writer will always
    reject. Absent is the common case here — `bImportedCharacter` is on 136 of
    the live world's 2,963 Pals — so it is worth spelling out.
    """
    if name not in obj:
        return None
    return bool(_prop(obj, name, False))


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


def _guild_markers(raw: dict) -> list[dict]:
    """
    The pins a guild has dropped on its map. `guild_markers` on the guild record.

    **The game's own words for this are "Guild Marker" and "Shared with Guild
    Members"** (`MAP_MARKER_HEAD_GUILD`, `MAP_MARKER_GUILD_INFO` in
    `DT_UI_Common_Text`), which is both the confirmation that the field means
    what its name says and the reason `/api/world/guildmarkers` scopes them to
    the guild rather than showing every guild's pins to everybody.

    **Positions are verified, not assumed.** On the world that first carried any,
    the three markers land 1 on Palpagos and 2 on World Tree against the
    landmass extents the cell grid gives — real world coordinates in the same
    space as everything else on the map, not map-space or normalised ones. `z`
    is always 0.0, which is consistent with a pin dropped on a flat map rather
    than at a point in the world.

    **`iconType` IS NOT RESOLVED, AND THIS IS WHERE THE SEARCH STOPPED.** The
    values seen are 0 and 6. There is no marker DataTable in either pak; the
    client ships five `MI_UI_MapMarker_*` materials (`00`, `Camp`, `FTTower`,
    `Oilrig`, `Tower`) which are the *map's own* markers and cannot be this set,
    since the index already exceeds them. The custom-pin sprites live in
    `WBP_MapMarker_Button`, a widget blueprint, whose properties are cooked
    unversioned — the same wall `elements.py` documents. So the integer travels
    as an integer and the UI draws one shape for all of them. Naming them from a
    guessed ordering is exactly the `TowerLockBarrier` mistake.
    """
    out = []
    for marker in raw.get("guild_markers") or []:
        if not isinstance(marker, dict):
            continue
        location = marker.get("icon_location")
        # **A marker with no position is dropped, not defaulted.** The first
        # version read `location.get("x") or 0.0`, so a record missing its
        # location became a pin at the world origin — a confident wrong point on
        # a map, which is the one outcome every reader here refuses. Both `x` and
        # `y` must actually be numbers.
        if not isinstance(location, dict):
            continue
        x, y = location.get("x"), location.get("y")
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            continue
        out.append({
            "id": str(marker.get("marker_id") or ""),
            "x": float(x),
            "y": float(y),
            # Unnamed on purpose — see the docstring.
            "iconType": int(marker.get("icon_type") or 0),
            "ownerUid": str(marker.get("owner_player_uid") or ""),
        })
    return out


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
                # WHO MAY OPEN THE GUILD CHEST. The dashboard has reported what
                # is IN it since guild storage shipped and never who can reach
                # it. Raw indices — `gamedata.guild_roles()` names them, and the
                # permission numbers below are deliberately left unnamed because
                # the game's enum order is not established.
                "chestAllowedRoles": [
                    int(r) for r in (raw.get("guild_chest_allowed_roles") or [])
                    if isinstance(r, int)
                ],
                "rolePermissions": [
                    {
                        "role": int(entry.get("role") or 0),
                        "permissions": [
                            int(p) for p in (entry.get("permissions") or [])
                            if isinstance(p, int)
                        ],
                    }
                    for entry in (raw.get("role_permissions") or [])
                    if isinstance(entry, dict)
                ],
                "markers": _guild_markers(raw),
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


#: 0001-01-01, the .NET DateTime epoch. Ticks are 100-nanosecond intervals from
#: there, which is what `OwnedTime` counts — see `obtainedAt`.
_DOTNET_EPOCH = datetime(1, 1, 1)


def _dotnet_ticks(ticks: int) -> Optional[str]:
    """
    A .NET tick count as an ISO timestamp, or `None`.

    **No timezone is asserted.** .NET carries a `DateTimeKind` alongside the
    ticks and this save format drops it, so labelling the result `Z` would be a
    claim the data does not support. A bare ISO string says "this instant, as the
    server recorded it", which is what is actually known.

    Guarded because a garbage tick count would otherwise raise out of a parse
    that has 1,905 other Pals to finish: anything outside a plausible range comes
    back `None` rather than a date in the year 3000.
    """
    if not ticks or ticks <= 0:
        return None
    try:
        moment = _DOTNET_EPOCH + timedelta(microseconds=ticks // 10)
    except (OverflowError, ValueError):
        return None
    if not 2020 <= moment.year <= 2100:
        return None
    return moment.isoformat(sep=" ", timespec="seconds")


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
            # `None` rather than `False`/`0` when the property is absent, and the
            # distinction is load-bearing rather than tidiness. `charedit`
            # refuses to write a property this Pal does not carry, so an editor
            # seeded from a flat `False` renders a checkbox that can only ever be
            # rejected — the exact dead end the field list is filtered to avoid.
            # Present on 136 and 102 of the live world's 2,963 Pals respectively,
            # so absent is the ordinary case, not the edge one.
            "isImported": _flag(obj, "bImportedCharacter"),
            "isAwakened": _flag(obj, "bIsAwakening"),
            "favoriteIndex": _num(obj, "FavoriteIndex", 0)
            if "FavoriteIndex" in obj else None,
            # Every previous owner, oldest first. Present on 100% of Pals and
            # the only record of a trade there is.
            "previousOwners": [
                str(u) for u in (_v(obj, "OldOwnerPlayerUIds", "value", "values") or [])
            ],
            # Who last renamed it. `previousOwners` says a Pal changed hands and
            # this says who touched its name, which is the other half of "where
            # did this come from" — 2,967 of 2,968 carry it.
            "lastRenamedBy": str(_prop(obj, "LastNickNameModifierPlayerUid", "") or "")
            or None,
            # WHEN THIS PAL WAS OBTAINED — and the field name misleads, so this
            # was checked rather than assumed. `OwnedTime` reads like a duration
            # ("how long owned") and is an absolute **.NET DateTime tick count**:
            # 100-nanosecond intervals since 0001-01-01. The reference world's
            # values decode to 2024-04-13 through 2026-07-28, which is the real
            # lifespan of that save; as a duration they would be 2,000 years.
            #
            # So the conversion is exact and needs nothing from the server: it is
            # wall-clock time, not game time, so `DayTimeSpeedRate` and friends
            # do not enter. **No timezone is claimed** — .NET stores a kind flag
            # this format drops, so the ISO string carries no offset rather than
            # asserting UTC.
            "obtainedAt": _dotnet_ticks(_num(obj, "OwnedTime", 0)),
            "obtainedAtTicks": _num(obj, "OwnedTime", 0) or None,
            # Seconds of trust accrued at a base, on 1,347. Distinct from
            # `friendshipPoint`, which is the heart meter itself: this is the
            # accrual clock behind it, and `FriendshipPoint_AutoIncrementRequire`
            # `Sanity` = 50 is the threshold that gates it.
            "basecampTrustSeconds": _num(obj, "FriendshipBasecampSec", 0) or None,
            # A named story encounter rather than an ordinary spawn — 5 on the
            # reference world. `gamedata.guild_roles`-style naming is not
            # attempted: the id is the game's and resolving it needs
            # `DT_UniqueNPC`, which `extract-npcs.py` already bundles.
            "uniqueNpcId": str(_prop(obj, "UniqueNPCID", "") or "") or None,
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
            # `None` when the property is absent, not `{}` — the same distinction
            # `_flag` draws and for the same reason. `charedit` refuses to write
            # a property a Pal does not carry (there is no struct to copy), so an
            # editor that cannot tell "no bought ranks" from "no property" offers
            # a control the writer will always reject.
            "workRanks": {
                str(_v(e, "WorkSuitability", "value", "value") or "").split("::")[-1]:
                    _num(e, "Rank", 0)
                for e in (_v(obj, "GotWorkSuitabilityAddRankList", "value", "values") or [])
                if isinstance(e, dict)
            } if "GotWorkSuitabilityAddRankList" in obj else None,
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
    # `breeding` used to match MonsterFarm, which is the **Ranch** — the pasture
    # that produces wool and eggs. The Breeding Farm is `BreedFarm`, and it
    # matched nothing at all, so all five on the reference world were dropped by
    # `_categorise` and never appeared on the map. One category was pointing at
    # the wrong structure while the structure it was named for was invisible.
    #
    # `structure_name` had the answer the whole time: MonsterFarm -> "Ranch",
    # BreedFarm -> "Breeding Farm". A category whose name disagrees with what the
    # game calls the thing is worth checking rather than trusting.
    ("breeding", re.compile(r"BreedFarm", re.I)),
    ("ranch", re.compile(r"MonsterFarm", re.I)),
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

    A thin projection of `_base_worker_join`, which also yields each base's
    worker *capacity*. One join, two views: the offset read below is the risky
    part of this module and must not exist in two places.
    """
    return _base_worker_join(gvas)[0]


def _base_worker_join(gvas: Any) -> tuple[dict[str, str], dict[str, int]]:
    """
    `({container_id: base_id}, {base_id: slot capacity})` in one pass.

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

    # Capacity comes along for the ride, because the same join already has it and
    # deriving it separately would mean reading the offset twice. `SlotNum` is the
    # *real* per-base worker cap: the game has already applied whatever this
    # server's `BaseCampWorkerMaxNum` and the base's own level allow, so it beats
    # any figure computed from a setting. See `extract_base_worker_capacity`.
    known: dict[str, int] = {}
    for entry in _v(world, "CharacterContainerSaveData", "value", default=[]) or []:
        container_id = str(_v(entry, "key", "ID", "value") or "").lower()
        if container_id:
            known[container_id] = _num(entry.get("value") or {}, "SlotNum", 0)

    def _guid_at(blob: bytes, offset: int) -> str:
        a, b, c, d = struct.unpack_from("<4I", blob, offset)
        return "%08x-%04x-%04x-%04x-%04x%08x" % (
            a, b >> 16, b & 0xFFFF, c >> 16, c & 0xFFFF, d
        )

    workers: dict[str, str] = {}
    capacities: dict[str, int] = {}
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
            capacities[base_id] = known[container_id]

    if camps and not workers:
        logger.warning(
            "No base worker containers resolved across %d bases — the "
            "WorkerDirector layout may have changed; per-base Pal counts will "
            "be unavailable",
            len(camps),
        )
    return workers, capacities


def extract_base_worker_capacity(gvas: Any) -> dict[str, int]:
    """
    `{base_camp_id: worker slots}` — the denominator `palCount` never had.

    **This is the game's own answer for THIS base, not a figure derived from a
    setting.** `SlotNum` on the base's worker container is what the game
    allocated after applying this server's `BaseCampWorkerMaxNum` and the base's
    own level, so it needs reconciling with neither. `gamedata.server_limit`
    still says what the operator configured, but that is *context*: a
    server-wide setting cannot tell you what one base can hold.

    The measured spread is why this is read rather than computed. Refworld's
    eleven bases are 20/16/13/8, the live world has 25s, and a 07-22 snapshot
    has 10s and 14s. Only *neither-960-nor-5* generalises, which is the property
    `extract_base_workers` already checks — and this shares that check by
    sharing its implementation rather than repeating the offset read.

    A base whose container did not resolve is **absent, never 0**. The caller
    has to be able to tell "no cap known" from "a cap of zero", and a base with
    a zero denominator renders as infinitely full.
    """
    return _base_worker_join(gvas)[1]


def extract_guild_research(gvas: Any) -> dict[str, dict]:
    """
    `{guild_id: {currentResearchId, progress: {research_id: work_amount}}}`.

    The Pal Lab tree, which is **guild-wide and permanent** — the one base
    upgrade that explains why two identical Pals produce differently on two
    different servers.

    `Lab.RawData` decodes to named fields rather than needing a byte offset, so
    this is the `extract_pal_storage` shape rather than the `WorkerDirector`
    one: where the game gives a name, take the name.

    **Every guild carries all 168 rows, including the ones it has not started.**
    On the live world one guild's rows are all `0.0`. So a row's presence says
    nothing; only `work_amount` does, and completion is a comparison against
    `RequiredWorkAmount` from `lab_research.json.gz` — which is why this returns
    raw amounts and lets `labresearch` do the join rather than deciding here.

    **`current_research_id` is the STRING "None" when idle**, not null. Treating
    it as a real id would put a node called "None" on screen.
    """
    world = _world_save_data(gvas)
    out: dict[str, dict] = {}

    guilds = _v(world, "GuildExtraSaveDataMap", "value", default=[]) or []
    for entry in guilds if isinstance(guilds, list) else []:
        guild_id = str(_v(entry, "key", "value") or entry.get("key") or "")
        lab = _v(entry, "value", "Lab", "value", "RawData", "value")
        if not guild_id or not isinstance(lab, dict):
            continue

        rows = lab.get("research_info")
        if not isinstance(rows, list):
            # Opaque bytes: the custom property was not requested. Yield nothing
            # rather than an empty tree, which would read as "researched none".
            continue

        progress: dict[str, float] = {}
        for row in rows:
            research_id = str((row or {}).get("research_id") or "")
            if research_id:
                progress[research_id] = float((row or {}).get("work_amount") or 0.0)

        current = str(lab.get("current_research_id") or "")
        out[guild_id] = {
            "currentResearchId": "" if current in ("", "None") else current,
            "progress": progress,
        }

    if guilds and not out:
        logger.warning(
            "No guild research decoded across %d guilds — the Lab.RawData custom "
            "property may not have been requested; research progress unavailable",
            len(guilds),
        )
    return out


def extract_guild_storage(gvas: Any) -> dict[str, str]:
    """
    `{guild_id: item_container_id}` — the Guild Chest.

    **The Guild Chest is not a base container and never was.** Every other
    storage structure in the game hangs an `ItemContainer` module off its
    `MapObjectSaveData` entry, which is how `extract_container_ownership` finds
    chests, feed boxes and breeding farms. `GuildChest` does not: all eight on
    the reference world carry `GuildSecurity` and nothing else, so a per-base
    walk finds them placed on the map and holding nothing.

    Their contents live one level up, in `GuildExtraSaveDataMap`, because the
    chest is **shared by the whole guild** rather than owned by the base it
    stands in. Eight placed chests on the reference world, five guilds, five
    containers — the count difference is the point: two chests in the same guild
    are two doors into one 54-slot box.

    `GuildItemStorage.RawData` is an opaque 20-byte ByteProperty: the container
    GUID at offset 0 and four trailing bytes. That is the same measured-offset
    read `extract_base_workers` performs, and it carries the same obligation —
    the decoded id **must resolve to a real `ItemContainerSaveData` entry** or it
    is dropped. A layout change therefore yields nothing rather than a confident
    wrong answer about what a guild is holding. Measured: **5 of 5** guilds
    resolve, to containers of 54 slots.

    GUID byte order is `u32le`, as in `extract_base_workers`.
    """
    import struct

    world = _world_save_data(gvas)

    known: set[str] = set()
    for entry in _v(world, "ItemContainerSaveData", "value", default=[]) or []:
        container_id = str(_v(entry, "key", "ID", "value") or "").lower()
        if container_id:
            known.add(container_id)

    def _guid_at(blob: bytes, offset: int) -> str:
        a, b, c, d = struct.unpack_from("<4I", blob, offset)
        return "%08x-%04x-%04x-%04x-%04x%08x" % (
            a, b >> 16, b & 0xFFFF, c >> 16, c & 0xFFFF, d
        )

    storage: dict[str, str] = {}
    guilds = _v(world, "GuildExtraSaveDataMap", "value", default=[]) or []
    for entry in guilds if isinstance(guilds, list) else []:
        guild_id = str(_v(entry, "key", "value") or entry.get("key") or "")
        blob = _v(entry, "value", "GuildItemStorage", "value", "RawData", "value", "values")
        if not isinstance(blob, (bytes, bytearray)) or len(blob) < 16:
            continue
        container_id = _guid_at(bytes(blob), 0)
        if guild_id and container_id in known:
            storage[guild_id] = container_id

    if guilds and not storage:
        logger.warning(
            "No guild chest containers resolved across %d guilds — the "
            "GuildItemStorage layout may have changed; guild chest contents "
            "will be unavailable",
            len(guilds),
        )
    return storage


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


# `WorkAssignMap` states, from the save. Only two occur on the reference world
# (3 on 53 assignments, 2 on 7) and the game does not name them anywhere this
# project can read, so **the integer travels as an integer**. Naming them from
# their distribution would be the `icon_type` mistake: inventing a legend from a
# guessed ordering. The UI says the game does not name them.

def _plain_int(value: Any, default: int = 0) -> int:
    """
    An already-unwrapped scalar out of a decoded `RawData` block.

    Distinct from `_num`, which digs a value out of a *tagged* GVAS property and
    has to know that a ByteProperty nests one level deeper than an Int.
    `WorkSaveData`'s RawData is a plain dict of Python scalars, so the two must
    not be confused — `_num` on one of these finds nothing and returns 0, which
    reads as a real state rather than a wrong reader.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _plain_float(value: Any, default: float = 0.0) -> float:
    """The float half of `_plain_int`; same distinction, same reason."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def extract_world_clock(gvas: Any) -> dict[str, int]:
    """
    `GameTimeSaveData` — how much time has passed in the world, and how long the
    server has been up.

    **Both are elapsed durations counted from zero, not .NET timestamps.**
    `OwnedTime` on a Pal is the opposite trap (a timestamp whose name reads as a
    duration); this is a duration in the same tick unit, so the two must not be
    handled by the same helper.

    Interpretation, verification and the one thing that is not established live
    in `backend/worldclock.py`. This only reads.
    """
    world = _world_save_data(gvas)
    clock = _v(world, "GameTimeSaveData", "value")
    if not isinstance(clock, dict):
        return {}

    out: dict[str, int] = {}
    for label, prop in (("gameTicks", "GameDateTimeTicks"),
                        ("realTicks", "RealDateTimeTicks")):
        value = _v(clock, prop, "value")
        if isinstance(value, int) and not isinstance(value, bool):
            out[label] = int(value)
    return out


# .NET DateTime.MaxValue.Ticks — a respawn timer holding it means "never
# respawns", and taken as a duration it is 87 million game-hours wearing a
# number (#86's lesson). The other two sentinels: -1 idle, 0 never written.
_TICKS_NEVER = 3155378975999999999


def extract_respawn_state(gvas: Any) -> dict[str, Any]:
    """
    Which gatherable spawners are respawning, from
    `MapObjectSpawnerInStageSaveData` (#141) — the save's largest structure,
    unread until the pak side could name a position for its keys.

    Only PENDING timers travel: a slot at -1 has its object standing, 0 was
    never written, DateTime.MaxValue never respawns, and a timer already
    behind `GameDateTimeTicks` respawns the moment a player streams the area
    in — thousands of those on a lived-in world, and a map of them would be
    noise. The counts keep all four states visible so "few pins" is
    distinguishable from "nothing read".

    **Every stage is walked, never `[0]`** — the outer map is keyed by stage
    and dungeon stages ride beside the overworld (the `base_camp_level`
    mistake, pinned in AGENTS.md). Non-overworld spawners have no world
    position, so they are counted and not listed.
    """
    world = _world_save_data(gvas)
    node = _v(world, "MapObjectSpawnerInStageSaveData", "value")
    if not isinstance(node, list):
        return {}

    clock = _v(world, "GameTimeSaveData", "value", "GameDateTimeTicks", "value")
    now = int(clock) if isinstance(clock, int) and not isinstance(clock, bool) else None

    pending: list[dict[str, Any]] = []
    counts = {"idle": 0, "neverWritten": 0, "neverRespawns": 0, "due": 0,
              "pending": 0, "otherStages": 0}
    for stage in node:
        internal = str(_v(stage, "key", "InternalId", "value") or "")
        overworld = internal.strip("0-") == ""
        spawners = _v(stage, "value", "SpawnerDataMapByLevelObjectInstanceId",
                      "value", default=[]) or []
        for spawner in spawners:
            spawner_id = str(spawner.get("key") or "").lower().replace("-", "")
            for slot in _v(spawner, "value", "ItemMap", "value", default=[]) or []:
                ticks = _v(slot, "value", "NextLotteryGameTime", "value")
                if not isinstance(ticks, int) or isinstance(ticks, bool):
                    continue
                if ticks == -1:
                    counts["idle"] += 1
                elif ticks == 0:
                    counts["neverWritten"] += 1
                elif ticks >= _TICKS_NEVER:
                    counts["neverRespawns"] += 1
                elif now is not None and ticks <= now:
                    counts["due"] += 1
                else:
                    if overworld:
                        counts["pending"] += 1
                        pending.append({"id": spawner_id, "readyTicks": ticks})
                    else:
                        counts["otherStages"] += 1

    return {
        # "As of this parse": game time only advances while the server runs,
        # so a remaining duration computed later against wall clocks would be
        # a guess. The clock ships and the caller subtracts.
        "clockTicks": now,
        "pending": pending,
        "counts": counts,
    }


def extract_work_assignments(gvas: Any) -> list[dict]:
    """
    Who the game has **actually** assigned to each job — `WorkSaveData`.

    This is a different question from `baseassign.py`, which infers who *ought*
    to work where by reading a base's structures out of `DT_MapObjectAssignData`
    and ranking candidates. This is the save's own record, and it is the one an
    operator asks first: not "who should mine" but "who IS mining".

    Nothing read it until now. 160 entries on the reference world.

    ## No byte offsets — the game names every field

    `.worldSaveData.WorkSaveData` is a palsav custom property, so `RawData`
    decodes to named fields. Same rule as `extract_pal_storage`: where the game
    gives a name, take the name. The join is

        WorkSaveData[].RawData.owner_map_object_model_id
          -> MapObjectSaveData[].Model.RawData.instance_id     (the structure)
        WorkSaveData[].WorkAssignMap[].value.RawData
          .assigned_individual_id.instance_id
          -> CharacterSaveParameterMap[].key.InstanceId        (the Pal)

    **Both were verified before anything was built on them.** The structure join
    is **160 of 160**, and so is the parallel one through
    `owner_map_object_concrete_model_id` -> `concrete_model_instance_id`.

    **`MapObjectId` is a NAME, not a GUID** (`DamagableRock0002`), so joining on
    it resolves 0 of 160 and looks like the field is wrong. The GUID is one
    level down in `Model.RawData`.

    ## The work type is reached two independent ways and they never disagree

    A row carries `assign_define_data_id` (`MonsterFarm_0`), whose stem joins
    `DT_MapObjectAssignData` directly — and the structure reached through the
    map-object join keys the *same* table. Measured: 158/160 and 159/160
    resolve, and **on all 160 the two routes give the same work**. That
    agreement is the check that the record means what it looks like; a
    misaligned read does not produce two keys landing on one answer.

    The structure route is used, because it is the one `baseassign` already
    speaks and comparing the two is the point of having this at all.

    ## Three things that are real states rather than failures

    - **A stale assignment.** One of the 60 assignments names an instance id
      that is in no `CharacterSaveParameterMap` entry — the Pal is gone and the
      Ranch slot still points at it. It is **dropped from `assigned` and
      counted in `staleAssignments`**, because a slot that looks occupied and
      is not is worth telling somebody about, and silently dropping it would
      make the base read as merely under-staffed.
    - **A job with no base.** `base_camp_id_belong_to` resolves for 159 of 160;
      the exception is a `RepairBuildObject_0` on a world-placed chest, with the
      all-zero GUID. That is a repair job outside any base, so `baseId` is
      empty rather than wrong.
    - **`player_uid` is not usable as a player.** Two assignments carry
      `00000000-…-0001`, which is neither the zero sentinel nor a Steam ID32
      followed by zeros — the shape AGENTS.md pins as a real player uid. Both
      resolve to ordinary Pals (a Manticore and a Sheepball), so the field is
      **not read**: `instance_id` is the key that resolves and the one used.

    Verification is the obligation every reader here carries: an id that does
    not resolve is dropped, so a layout change yields nothing rather than a
    confident wrong answer about which Pal is doing which job.
    """
    import gamedata

    world = _world_save_data(gvas)

    characters: set[str] = set()
    for entry in _v(world, "CharacterSaveParameterMap", "value", default=[]) or []:
        instance_id = str(_v(entry, "key", "InstanceId", "value") or "").lower()
        if instance_id:
            characters.add(instance_id)

    # Structure GUID -> its MapObjectId, which is what `work_assign` keys on.
    structures: dict[str, str] = {}
    objects = _v(world, "MapObjectSaveData", "value", "values", default=[]) or []
    for entry in objects if isinstance(objects, list) else []:
        instance_id = str(
            _v(entry, "Model", "value", "RawData", "value", "instance_id") or ""
        ).lower()
        if instance_id:
            structures[instance_id] = str(_v(entry, "MapObjectId", "value") or "")

    out: list[dict] = []
    entries = _v(world, "WorkSaveData", "value", "values", default=[]) or []
    for entry in entries if isinstance(entries, list) else []:
        raw = _v(entry, "RawData", "value")
        raw = raw if isinstance(raw, dict) else {}

        owner = str(raw.get("owner_map_object_model_id") or "").lower()
        structure_id = structures.get(owner)
        if structure_id is None:
            # No placed object for this job. Never observed; dropped rather
            # than reported against a structure we cannot name.
            continue

        assigned: list[dict] = []
        stale = 0
        for slot in _v(entry, "WorkAssignMap", "value", default=[]) or []:
            detail = _v(slot, "value", "RawData", "value")
            detail = detail if isinstance(detail, dict) else {}
            individual = detail.get("assigned_individual_id") or {}
            instance_id = str(individual.get("instance_id") or "").lower()
            if not instance_id or instance_id == ZERO_GUID:
                continue
            if instance_id not in characters:
                stale += 1
                continue
            assigned.append({
                "instanceId": instance_id,
                # The game's own integer. Not named — see the note above.
                "state": _plain_int(detail.get("state")),
                "fixed": bool(detail.get("fixed")),
            })

        base_camp = str(raw.get("base_camp_id_belong_to") or "")
        locations = raw.get("assign_locations")
        out.append({
            "workId": str(raw.get("id") or ""),
            "baseId": "" if base_camp in ("", "None", ZERO_GUID) else base_camp,
            "structureId": structure_id,
            "structureName": gamedata.structure_name(structure_id),
            # Kept beside the structure because it is the game's own label for
            # the job and disambiguates two of the same structure at one base.
            "defineId": str(raw.get("assign_define_data_id") or ""),
            "workableType": str(
                _v(entry, "WorkableType", "value", "value") or ""
            ).rsplit("::", 1)[-1],
            "assigned": assigned,
            "staleAssignments": stale,
            # **NOT A CAPACITY, AND THE FIRST VERSION OF THIS SAID IT WAS.**
            # `assign_locations` is a list of fixed standing positions, each
            # with a facing direction — where a Pal plants itself at a
            # workbench. Jobs the Pal wanders (`MonsterFarm`,
            # `OnlyJoinAndWalkAround`) have **none at all**, so a Ranch holding
            # two Pals reads 0. Measured: **20 of the 160 rows have more Pals
            # assigned than positions**, which is what refuted it.
            #
            # Named for what it is. `DT_MapObjectAssignData`'s `workerMax` is
            # the capacity-shaped figure, and `baseassign` already notes it is
            # UNSET (0) on 178 of 271 rows.
            "fixedPositions": len(locations) if isinstance(locations, list) else 0,
            "state": _plain_int(raw.get("current_state")),
            "workAmount": _plain_float(raw.get("current_work_amount")),
            "requiredWorkAmount": _plain_float(raw.get("required_work_amount")),
        })

    return out


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
    # "Show me this Pal" requests, keyed by the RequestIDs in `DA_PalDisplay`.
    # The binary calls the runtime state `Local_PalDisplayNPCDataTableProgress`
    # and `Local_` reads as client-side — it is in the save, in exactly the
    # `[{key, value}]` shape every other flag here uses.
    ("palDisplay", "PalDisplayNPCDataTableProgress"),
]

_PROGRESS_COUNTERS: list[tuple[str, str]] = [
    ("palsCaptured", "PalCaptureCount"),
    ("speciesCaptured", "TribeCaptureCount"),
    ("itemsCrafted", "CraftItemCount"),
    ("palRankUps", "PalRankupCount"),
    ("mutations", "MutationCount"),
    ("raidBossesDefeated", "RaidBossDefeatCount"),
    # The #117/#138 stragglers. Scalars and maps both go through the loop's
    # shape handling; ABSENT is a real state distinct from zero (refworld
    # players carry no TowerBossDefeatCount at all — the trap the boss-counter
    # investigation recorded), so the loop must keep skipping absent keys and
    # the UI must render only what is present.
    ("towerBossDefeats", "TowerBossDefeatCount"),
    ("campsConquered", "CampConqueredCount"),
    ("oilrigsCleared", "OilrigClearCount"),
    ("npcTalks", "NPCTalkCountMap"),
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

    # **THE FLAT EFFIGY FLAG LIST IS A LEGACY FIELD AND THE GAME ABANDONED IT.**
    # `RelicObtainForInstanceFlag` is what `_PROGRESS_FLAGS` reads above, and on
    # every one of five real players it holds a fraction of the truth:
    #
    #     player     flat   ByType
    #     11A11A01     39      103
    #     55E55E05     44       98
    #     44D44D04     21       64
    #     22B22B02     11       24
    #
    # `bCaptureCompletionRelicFixupDone` is True on all of them — the game's own
    # marker that it migrated relic data — after which the flat list stopped
    # being written. So the count was **frozen, not stale**, which is why an
    # operator re-parsing changed nothing and why effigies they had already
    # collected kept showing as available on the map.
    #
    # `RelicObtainForInstanceFlagByType` is the record now: one row per
    # `EPalRelicType`, each with a `Flags` map of instance GUID -> bool. The
    # GUIDs are the same undashed uppercase hex `effigies.json.gz` carries — the
    # existing join was verified 39 of 39 with zero unmatched, so only the
    # source was ever wrong.
    #
    # The union is taken rather than the replacement, and the flat list is still
    # merged in: a save from before the fixup has only the old field, and
    # branching on `bCaptureCompletionRelicFixupDone` would still leave the
    # pre-migration entries out for no reason. A GUID in either place was
    # obtained.
    by_type = _v(record, "RelicObtainForInstanceFlagByType", "value")
    rows = by_type.get("values") if isinstance(by_type, dict) else None
    if isinstance(rows, list):
        found: set[str] = {str(k) for k in (progress.get("effigies") or {}).get("keys") or []}
        for row in rows:
            flags = _v(row, "Flags", "value")
            for entry in flags if isinstance(flags, list) else []:
                if entry.get("value") is True and entry.get("key"):
                    found.add(str(entry.get("key")))
        progress["effigies"] = {
            "obtained": len(found),
            "keys": sorted(found),
            # So a caller can tell a migrated save from an old one rather than
            # inferring it from the count.
            "fixupDone": bool(_v(record, "bCaptureCompletionRelicFixupDone", "value")),
        }

    for label, prop in _PROGRESS_COUNTERS:
        raw = _v(record, prop, "value")

        # ABSENT IS NOT ZERO. `TowerBossDefeatCount` is missing outright on
        # every refworld player — the game only writes a counter once it has
        # something to count — and emitting `{total: 0}` for a missing key is
        # how a first pass "refuted" the boss counters. Skip it: the key stays
        # out of the payload and the UI renders nothing rather than a 0.
        if raw is None:
            continue

        # **`TribeCaptureCount` IS A PLAIN INT AND THIS READ IT AS A MAP**, so
        # `speciesCaptured` has been `{total: 0, distinct: 0}` on every player
        # since the field was added — against real values of 210, 149, 128, 109
        # and 8 on the reference world. `_flag_entries` returns `[]` for
        # anything that is not a list, which is right for a missing key and
        # silently wrong for a scalar.
        #
        # Same family as the effigy count above: a field that reads as zero
        # looks like a player who has done nothing, not like a reader pointed at
        # the wrong shape. Nothing rendered it, which is why it survived.
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            # A scalar carries no per-key breakdown, so `distinct` is None
            # rather than a number copied from `total` — those are different
            # facts and one of them is not available here.
            progress[label] = {"total": int(raw), "distinct": None}
            continue

        entries = raw if isinstance(raw, list) else []
        total = 0
        for entry in entries:
            value = entry.get("value")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                total += int(value)
            elif value is True:
                total += 1
        progress[label] = {"total": total, "distinct": len(entries)}

    # Which milestone rewards this player has actually collected from the NPC.
    # Pocketpair's typo, kept: the save key and the DataTable are both
    # `Achivement`. See `backend/achievements.py` for why this is a join rather
    # than an inference — the save names the exact row.
    claimed = _flag_entries(record, "NPCAchivementRewardFlag")
    progress["achievementsClaimed"] = sorted(
        str(e.get("key")) for e in claimed
        if e.get("value") not in (None, False, 0)
    )

    # Relics SPENT per statue line, which is what says what the effigies a
    # player collected actually bought them. `gamedata.relic_rank` turns each
    # figure into a rank, its cumulative effect and the cost of the next one.
    #
    # **`RelicPossessNum` — the scalar beside this — is NOT the total and is not
    # read.** It equals the `CapturePower` figure on both reference-world
    # players, which is what the field would be if it were the pre-1.0 record
    # from when effigies raised Capture Power alone. Summing it, or using it as
    # a denominator, would double-count one line and miss twelve.
    spent: dict[str, int] = {}
    for entry in _flag_entries(record, "RelicPossessNumMap"):
        kind = str(entry.get("key") or "")
        kind = kind.rsplit("::", 1)[-1] if "::" in kind else kind
        value = entry.get("value")
        # A line at 0 is carried: "you have spent nothing here" is a real and
        # useful answer, and dropping it would make an untouched line
        # indistinguishable from one this parser could not read.
        if kind and isinstance(value, (int, float)) and not isinstance(value, bool):
            spent[kind] = int(value)
    progress["relicsSpent"] = spent

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
