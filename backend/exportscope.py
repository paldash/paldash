"""
What a world export would drop if it kept only some guilds — the PLAN half.

Carrying a character off a server that is shutting down leaves you with a copy
of everybody's world: their bases still standing, their Pals filed under uids
that do not exist on your install. Not corruption, and not something you can
tidy up in game either — another guild's structures are attackable but not
dismantleable, so removing three built bases by hand is an evening of hitting
walls.

**This module computes the scope and writes nothing.** `apply` is deliberately
not here. Deletion across six interlinked structures is the one thing in this
project where a half-finished implementation is worse than none, and the shape
of the risk is that a plausible-looking prune leaves a dangling id: a world that
loads today and crashes when somebody walks into the cell.

## Why the EXPORT is where this belongs

`soloexport` reads the live world and writes a **new directory**; the source is
never opened for writing. So a prune that gets it wrong produces a folder you
delete. Every other deletion idea here has to be argued against
`guarded_save_write`; this one does not, which makes it the only place in the
codebase where destructive scope filtering is cheap to get wrong.

## The join, and the field that will bite

    GroupSaveDataMap[].RawData            the guild: group_id, players[]
      <- BaseCampSaveData[].RawData.group_id_belong_to
         BaseCampSaveData[].RawData.id
      <- MapObjectSaveData[].Model.RawData.base_camp_id_belong_to
    CharacterSaveParameterMap[].group_id  every Pal, owned or not

**`group_id_belong_to` is the guild and `base_camp_id_belong_to` is the base.**
They are GUIDs sitting beside each other in the same `RawData`, and swapping
them still produces a plausible grouping — it just silently merges every base in
a guild into one. `test_base_storage.py` already pins that distinction for the
storage join; this is the same trap one structure over.

**An ownerless Pal belongs to its GUILD, not to nobody.** 159 of the reference
world's 1,905 carry no `OwnerPlayerUId` — base workers and anything in a shared
store. Filtering on owner instead of `group_id` leaves every base worker behind,
pointing at a guild that no longer exists, which is exactly the dangling
reference this module exists to make visible.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _v(node: Any, *keys: str, default: Any = None) -> Any:
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def _guid(value: Any) -> str:
    """A GUID as a comparable lowercase string. `None` and '' both become ''."""
    text = str(value or "").strip().lower()
    return "" if text.startswith("00000000-0000-0000-0000") else text


def guilds(world: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Every guild, with what it owns — the input a keep/drop choice is made from.

    Only `EPalGroupType::Guild` records carry `players`, which is how the 7
    `Organization` groups on the reference world are excluded without keying on
    the enum: the field that matters is the one that has to be there.
    """
    out: list[dict[str, Any]] = []
    for group in _v(world, "GroupSaveDataMap", "value", default=[]) or []:
        raw = _v(group, "value", "RawData", "value", default={})
        if not isinstance(raw, dict) or "players" not in raw:
            continue
        out.append({
            "guildId": _guid(raw.get("group_id")),
            "name": str(raw.get("guild_name") or ""),
            "adminUid": _guid(raw.get("admin_player_uid")),
            "playerUids": [
                _guid(p.get("player_uid")) for p in (raw.get("players") or [])
            ],
            "memberCount": len(raw.get("players") or []),
        })
    return out


def plan(world: dict[str, Any], keep_guilds: Optional[list] = None,
         keep_uid: str = "") -> dict[str, Any]:
    """
    What a prune would remove, counted per structure. **Nothing is written.**

    `keep_guilds` is guild ids; `keep_uid` names a player whose guilds are kept
    whatever else is chosen — the exporting character's own, so the common case
    ("just me") needs no guild ids at all and cannot accidentally drop itself.
    """
    keep = {_guid(g) for g in (keep_guilds or []) if _guid(g)}
    me = _guid(keep_uid)

    all_guilds = guilds(world)
    if me:
        for guild in all_guilds:
            if me == guild["adminUid"] or me in guild["playerUids"]:
                keep.add(guild["guildId"])

    known = {g["guildId"] for g in all_guilds}
    drop = {g for g in known if g not in keep}

    # Bases, by the GUILD field — not the base field beside it.
    bases_dropped, bases_kept = [], set()
    for camp in _v(world, "BaseCampSaveData", "value", default=[]) or []:
        raw = _v(camp, "value", "RawData", "value", default={})
        if not isinstance(raw, dict):
            continue
        guild_id = _guid(raw.get("group_id_belong_to"))
        base_id = _guid(raw.get("id"))
        if guild_id in drop:
            bases_dropped.append(base_id)
        else:
            bases_kept.add(base_id)

    dropped_bases = set(bases_dropped)
    objects = containers = 0
    # **`value.values`, not `value`.** `MapObjectSaveData` is an ArrayProperty
    # and its elements live one level deeper than the maps beside it —
    # `parser.extract_map_objects` reads it the same way. Reading `value`
    # returned an empty list and the plan reported **0 objects for 8 dropped
    # bases**, which looked like a world with nothing built on it rather than
    # like a bad path. A prune trusting that count would have left every
    # structure behind.
    for obj in _v(world, "MapObjectSaveData", "value", "values", default=[]) or []:
        model = _v(obj, "Model", "value", "RawData", "value", default={})
        base_id = _guid(model.get("base_camp_id_belong_to")) if isinstance(model, dict) else ""
        if base_id and base_id in dropped_bases:
            objects += 1
            modules = _v(obj, "ConcreteModel", "value", "ModuleMap", "value",
                         default=[]) or []
            for module in modules:
                # The parser reads `target_container_id` and stops there — the
                # first version here kept walking into `.value.ID.value` and got
                # None every time, reporting **0 containers for 842 objects**.
                # Same shape as the array path above: a zero that means "wrong
                # path" is indistinguishable from a zero that means "none".
                if _v(module, "value", "RawData", "value", "target_container_id"):
                    containers += 1

    # Characters by `group_id`, which is the ONLY correct filter — an ownerless
    # Pal is a base worker and still belongs to a guild.
    characters = ownerless = 0
    for entry in _v(world, "CharacterSaveParameterMap", "value", default=[]) or []:
        raw = _v(entry, "value", "RawData", "value", default={})
        if not isinstance(raw, dict):
            continue
        if _guid(raw.get("group_id")) in drop:
            characters += 1
            param = _v(raw, "object", "SaveParameter", "value", default={}) or {}
            if not _guid(_v(param, "OwnerPlayerUId", "value")):
                ownerless += 1

    players = sorted({
        uid for g in all_guilds if g["guildId"] in drop
        for uid in [*g["playerUids"], g["adminUid"]] if uid
    })

    return {
        "guilds": all_guilds,
        "keepGuildIds": sorted(keep),
        "dropGuildIds": sorted(drop),
        "removes": {
            "guilds": len(drop),
            "bases": len(bases_dropped),
            "mapObjects": objects,
            "containers": containers,
            "characters": characters,
            # Counted separately because it is the number that proves the filter
            # keyed on `group_id` rather than on ownership. A prune reporting
            # zero ownerless characters while removing bases has used the wrong
            # field and will strand every worker.
            "ownerlessCharacters": ownerless,
            "playerSaves": len(players),
        },
        "playerUids": players,
        # **THE PLAN IS ALL THERE IS.** Nothing here deletes, and a caller must
        # not present this as a completed operation.
        "applyImplemented": False,
        "note": (
            "This counts what a prune would remove. Pruning itself is not "
            "implemented: deletion across six interlinked structures risks "
            "leaving a dangling id, which produces a world that loads and "
            "fails later. The export writes a full copy today."
        ),
    }
