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
import os
from typing import Any, Optional

import savefiles

logger = logging.getLogger(__name__)


class ExportScopeError(Exception):
    """
    Raised when a prune cannot be completed cleanly.

    **Every raise here means the caller writes the UNPRUNED copy.** That is the
    whole safety argument: the export's defence is that a bad result is a folder
    you delete, and it only holds if the bad result is *whole*. A half-pruned
    world loads today and fails when somebody walks into the cell.
    """


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


def load_world(world_dir: Optional[str] = None) -> dict[str, Any]:
    """
    The raw `worldSaveData` tree, for callers that only have a directory.

    **This is a full `Level.sav` parse**, which is the heaviest thing this
    dashboard can do to a machine also running a game server. It is acceptable
    here only because the export preview is a deliberate, rare, operator-gated
    click, and because `apply_export` pays the same cost moments later anyway.

    The parsed cache cannot serve this: `savecache` holds extracted *sections*,
    and every join below reads raw GVAS fields (`group_id_belong_to`,
    `target_container_id`) that the extraction deliberately does not carry.
    Deriving the plan from the sections would be a second, weaker source of
    truth for a destructive operation.
    """
    import soloexport  # local: soloexport imports this module.

    root = world_dir or savefiles.get_default_world_dir()
    if not root:
        raise ExportScopeError("No world directory configured")
    gvas, _ = soloexport._load(os.path.join(root, "Level.sav"))
    return soloexport._world_save_data(gvas)


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
        # `plan` still writes nothing — `apply` is the half that does, and it
        # only ever touches the in-memory tree of a COPY.
        "applyImplemented": True,
        "note": (
            "This counts what a prune would remove. `apply` performs it on the "
            "exported copy and refuses outright if any surviving structure "
            "still points at a removed id, in which case the unpruned copy is "
            "written instead — a world missing half a reference loads and "
            "fails later, so a partial prune is never an outcome."
        ),
    }


def _dropped_ids(world: dict[str, Any], drop: set[str]) -> dict[str, set[str]]:
    """
    Every id that must go, gathered before anything is removed.

    Collected in one pass so the removal below is a pure filter — nothing is
    decided while the tree is being mutated, which is how an ordering bug turns
    into a partial prune.

    **A container is dropped only if NOTHING kept still references it.** A
    palbox belongs to a player, not to a base, and an object in a surviving
    guild can legitimately point at the same container id. Subtracting the kept
    references is what stops a prune from taking a container out from under a
    player it was told to keep.
    """
    bases_drop, bases_keep = set(), set()
    for camp in _v(world, "BaseCampSaveData", "value", default=[]) or []:
        raw = _v(camp, "value", "RawData", "value", default={})
        if not isinstance(raw, dict):
            continue
        base_id = _guid(raw.get("id"))
        if _guid(raw.get("group_id_belong_to")) in drop:
            bases_drop.add(base_id)
        else:
            bases_keep.add(base_id)

    containers_drop, containers_keep = set(), set()
    objects_drop = set()
    for index, obj in enumerate(
        _v(world, "MapObjectSaveData", "value", "values", default=[]) or []
    ):
        model = _v(obj, "Model", "value", "RawData", "value", default={})
        base_id = _guid(model.get("base_camp_id_belong_to")) if isinstance(model, dict) else ""
        going = bool(base_id) and base_id in bases_drop
        if going:
            objects_drop.add(index)
        bucket = containers_drop if going else containers_keep
        for module in _v(obj, "ConcreteModel", "value", "ModuleMap", "value",
                         default=[]) or []:
            # `target_container_id` is where the walk stops — see `plan`.
            target = _guid(_v(module, "value", "RawData", "value", "target_container_id"))
            if target:
                bucket.add(target)

    characters_drop = set()
    for entry in _v(world, "CharacterSaveParameterMap", "value", default=[]) or []:
        raw = _v(entry, "value", "RawData", "value", default={})
        if isinstance(raw, dict) and _guid(raw.get("group_id")) in drop:
            characters_drop.add(_guid(_v(entry, "key", "InstanceId", "value")))

    # Container ids a KEPT player still points at — palbox and party live on the
    # player save, not on any map object, so they never appear in the loop above.
    for entry in _v(world, "CharacterSaveParameterMap", "value", default=[]) or []:
        raw = _v(entry, "value", "RawData", "value", default={})
        if not isinstance(raw, dict) or _guid(raw.get("group_id")) in drop:
            continue
        param = _v(raw, "object", "SaveParameter", "value", default={}) or {}
        for field in ("SlotId",):
            slot = _v(param, field, "value", default={}) or {}
            cid = _guid(_v(slot, "ContainerId", "value", "ID", "value"))
            if cid:
                containers_keep.add(cid)

    return {
        "bases": bases_drop,
        "objectIndices": objects_drop,
        # The subtraction is the safety property, not an optimisation.
        "containers": containers_drop - containers_keep,
        "characters": characters_drop,
        "guilds": set(drop),
    }


def _filter_map(world: dict[str, Any], key: str, path: tuple[str, ...],
                drop: set[str]) -> int:
    """Drop entries of a keyed map whose id at `path` is in `drop`. Returns the count."""
    entries = _v(world, key, "value", default=None)
    if not isinstance(entries, list):
        return 0
    keep = [e for e in entries if _guid(_v(e, *path)) not in drop]
    removed = len(entries) - len(keep)
    world[key]["value"] = keep
    return removed


def apply(world: dict[str, Any], keep_guilds: Optional[list] = None,
          keep_uid: str = "") -> dict[str, Any]:
    """
    Prune the world tree **in memory**, or raise and change nothing that matters.

    Operates on the tree of a COPY — `soloexport` loads the source, remaps, and
    writes elsewhere — so the source world is never at risk. That is why this is
    the one destructive operation here that does not go through
    `guarded_save_write`.

    Order is the order in the task, and it matters: references are removed
    before the things they point at, so a failure part-way leaves the world
    still owning its objects rather than owning nothing.

    Raises `ExportScopeError` if any surviving structure still points at a
    dropped id. The caller must then write the unpruned copy.
    """
    scope = plan(world, keep_guilds, keep_uid)
    drop = set(scope["dropGuildIds"])
    if not drop:
        return {"pruned": False, "reason": "nothing selected for removal",
                "removed": {k: 0 for k in scope["removes"]}}

    ids = _dropped_ids(world, drop)
    removed: dict[str, int] = {}

    # 1. Map objects — the references, before the bases they point at.
    objects = _v(world, "MapObjectSaveData", "value", "values", default=None)
    if isinstance(objects, list):
        keep_objects = [o for i, o in enumerate(objects) if i not in ids["objectIndices"]]
        removed["mapObjects"] = len(objects) - len(keep_objects)
        world["MapObjectSaveData"]["value"]["values"] = keep_objects
    else:
        removed["mapObjects"] = 0

    # 2. The containers those objects owned, minus anything kept still using one.
    # **`key.ID.value`, not `key.ID`.** The key is a wrapped GVAS Guid, so
    # stopping one level short hands `_guid` a dict — which stringifies to
    # something unique per entry, matches nothing, and removed **0 containers
    # for 199 planned**. Third instance of the zero-that-means-wrong-path trap
    # this module already records twice.
    removed["itemContainers"] = _filter_map(
        world, "ItemContainerSaveData", ("key", "ID", "value"), ids["containers"])
    removed["characterContainers"] = _filter_map(
        world, "CharacterContainerSaveData", ("key", "ID", "value"), ids["containers"])
    removed["containers"] = removed["itemContainers"] + removed["characterContainers"]
    # **Fewer entries removed than ids selected is EXPECTED, and saying so is
    # the point.** On refworld the prune selects 199 container ids and removes
    # 196: three of them resolve to no entry in either map. Those are the same
    # three dangling references AGENTS.md already records for this world
    # ("3,370 objects carry a container id, 3 dangle") — a property of the save,
    # not of this prune.
    #
    # Reported rather than smoothed over, because an unexplained gap between a
    # plan and an apply is indistinguishable from a filter that missed
    # something, which is exactly the bug this module is most at risk of.
    removed["containerIdsSelected"] = len(ids["containers"])
    removed["containerIdsDangling"] = len(ids["containers"]) - removed["containers"]

    # 3. Characters, by `group_id` — never by owner. See the module docstring.
    # The same wrapping. This one HAPPENED to work at `key.InstanceId` because
    # both sides computed the identical wrong value and so agreed with each
    # other — a filter that is consistent with itself and means nothing.
    removed["characters"] = _filter_map(
        world, "CharacterSaveParameterMap", ("key", "InstanceId", "value"),
        ids["characters"])

    # 4. Bases.
    removed["bases"] = _filter_map(
        world, "BaseCampSaveData", ("value", "RawData", "value", "group_id_belong_to"),
        drop)

    # 5. Guilds LAST, so a failure above leaves them still owning what they own.
    removed["guilds"] = _filter_map(
        world, "GroupSaveDataMap", ("value", "RawData", "value", "group_id"), drop)

    dangling = verify(world, ids)
    if dangling:
        raise ExportScopeError(
            "Prune left "
            + ", ".join(f"{n} {what}" for what, n in sorted(dangling.items()))
            + ". Refusing — the unpruned copy is written instead, because a "
              "world missing half a reference loads and fails later."
        )

    return {"pruned": True, "removed": removed, "playerUids": scope["playerUids"],
            "dropGuildIds": sorted(drop)}


def verify(world: dict[str, Any], ids: dict[str, set[str]]) -> dict[str, int]:
    """
    Surviving references to anything dropped. **Empty means clean.**

    Re-derived from the pruned tree rather than counted during removal: a
    removal loop that miscounts is exactly the bug this has to catch, so
    checking its own arithmetic would prove nothing.
    """
    bad: dict[str, int] = {}

    def note(what: str) -> None:
        bad[what] = bad.get(what, 0) + 1

    for obj in _v(world, "MapObjectSaveData", "value", "values", default=[]) or []:
        model = _v(obj, "Model", "value", "RawData", "value", default={})
        if not isinstance(model, dict):
            continue
        if _guid(model.get("base_camp_id_belong_to")) in ids["bases"]:
            note("map objects pointing at a removed base")
        if _guid(model.get("group_id_belong_to")) in ids["guilds"]:
            note("map objects pointing at a removed guild")

    for entry in _v(world, "CharacterSaveParameterMap", "value", default=[]) or []:
        raw = _v(entry, "value", "RawData", "value", default={})
        if isinstance(raw, dict) and _guid(raw.get("group_id")) in ids["guilds"]:
            note("characters pointing at a removed guild")

    for camp in _v(world, "BaseCampSaveData", "value", default=[]) or []:
        raw = _v(camp, "value", "RawData", "value", default={})
        if isinstance(raw, dict) and _guid(raw.get("group_id_belong_to")) in ids["guilds"]:
            note("bases pointing at a removed guild")

    # A guild that still lists a character we removed is the mirror of the
    # check above, and the one a naive prune fails: the guild survives, so
    # nothing about it looks wrong until the game reads its member list.
    for group in _v(world, "GroupSaveDataMap", "value", default=[]) or []:
        raw = _v(group, "value", "RawData", "value", default={})
        if not isinstance(raw, dict):
            continue
        for handle in raw.get("individual_character_handle_ids") or []:
            if _guid(_v(handle, "instance_id")) in ids["characters"]:
                note("guild member handles pointing at a removed character")

    return bad
