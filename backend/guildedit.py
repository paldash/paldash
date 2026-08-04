"""
Move a player between guilds.

Guild membership is not one field. A player belongs to a guild through **four**
places that must agree, and a move that updates three of them leaves a world the
game reads inconsistently:

  1. `GroupSaveDataMap[guild].RawData.players[]` — the member list
  2. `RawData.admin_player_uid` — who leads it
  3. `CharacterSaveParameterMap[].RawData.group_id` on the player's character
     **and every Pal they own**
  4. `RawData.individual_character_handle_ids[]` — the guild's index of those
     same characters, which has to be maintained on both sides

Everything here is one all-or-nothing write through `guarded_save_write`, for the
same reason `charedit.apply_pal_batch` is: half a guild move, with nothing
recording where it stopped, is worse than a refusal.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
**It never deletes a base camp.** The reference implementation
(`PalWorldSaveTools`' `move_player_to_guild`) removes the origin guild and calls
`delete_base_camp` on everything it owned once the last member leaves. On the
reference world that would destroy three fully built bases and their contents to
carry out a request that said nothing about bases.

That case is not rare — it is the *main* one. All five guilds on the reference
world have exactly one member, so "move this player to their friend's guild"
empties the origin guild every time. So it is handled rather than avoided:

  * by default a move that would empty the origin guild is **refused**, naming
    how many bases and deployed Pals would be orphaned;
  * `transfer_bases=True` moves those bases to the target guild as part of the
    same write and then removes the emptied guild. Nothing is deleted — the
    player's bases arrive with the player, which is what an operator asking to
    move someone actually means.

**It does not write the player's `.sav`.** PST sets `SaveData.GroupId` there.
That key does not exist on a Palworld 1.0 player save — checked against the
reference world, where the 16 `SaveData` keys do not include it — so writing it
would be *creating* a property the game does not currently store, on the same
reasoning that keeps `MasteredWaza` uneditable. Membership lives in
`GroupSaveDataMap` and in each character's `group_id`, which is what every
character on the reference world already agrees with: the five guild ids account
for all 1,910 of them.

WHICH CHARACTERS MOVE
---------------------
The player's own character, plus every Pal carrying their `OwnerPlayerUId`.

**170 of the reference world's 1,910 characters have no `OwnerPlayerUId`** — they
are the base-deployed workers, and they belong to a *base* rather than to a
person. They move only when their base does, which is why they are counted in the
refusal message and moved under `transfer_bases`. Treating them as unowned and
leaving them behind would strand a base's whole workforce in a guild that no
longer owns the base.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any, Optional

from backup import guarded_save_write, restore_backup
from savefiles import atomic_write, get_level_sav_path

logger = logging.getLogger(__name__)

GUILD_TYPES = ("EPalGroupType::Guild", "EPalGroupType::IndependentGuild")

# Guild roles as the save stores them. 1 is the leader; a joining member gets 3.
ROLE_ADMIN = 1
ROLE_MEMBER = 3


class GuildEditError(Exception):
    """A guild move was rejected, failed, or was rolled back."""


def _nu(value: Any) -> str:
    """Normalise a uid for comparison. Both dialects appear in one file."""
    return str(value or "").replace("-", "").lower()


# ─── Reading the tree ────────────────────────────────────


def _world(gvas: Any) -> dict:
    return gvas.properties["worldSaveData"]["value"]


def _guild_entries(gvas: Any) -> list[dict]:
    groups = _world(gvas).get("GroupSaveDataMap", {}).get("value", []) or []
    out = []
    for entry in groups:
        try:
            if entry["value"]["GroupType"]["value"]["value"] in GUILD_TYPES:
                out.append(entry)
        except (KeyError, TypeError):
            continue
    return out


def _raw(entry: dict) -> dict:
    return entry["value"]["RawData"]["value"]


def _members(entry: dict) -> list[dict]:
    return _raw(entry).get("players") or []


def _guild_label(entry: dict) -> str:
    raw = _raw(entry)
    return str(raw.get("guild_name") or raw.get("group_name") or "Unnamed Guild")


def _find_guild(gvas: Any, guild_id: str) -> Optional[dict]:
    wanted = _nu(guild_id)
    for entry in _guild_entries(gvas):
        if _nu(_raw(entry).get("group_id")) == wanted or _nu(entry.get("key")) == wanted:
            return entry
    return None


def _find_member(gvas: Any, player_uid: str) -> tuple[Optional[dict], Optional[dict]]:
    """(guild entry, member record) for whichever guild currently holds them."""
    wanted = _nu(player_uid)
    for entry in _guild_entries(gvas):
        for member in _members(entry):
            if _nu(member.get("player_uid")) == wanted:
                return entry, member
    return None, None


def _save_parameter(character: dict) -> Optional[dict]:
    try:
        return character["value"]["RawData"]["value"]["object"]["SaveParameter"]["value"]
    except (KeyError, TypeError):
        return None


def _owned_characters(gvas: Any, player_uid: str) -> list[dict]:
    """
    The player's own character plus every Pal that names them as owner.

    Base-deployed Pals are **not** here: they carry no `OwnerPlayerUId` at all and
    belong to the base. `_base_characters` collects those separately so the two
    can be moved under different conditions.
    """
    wanted = _nu(player_uid)
    found = []
    for character in _world(gvas).get("CharacterSaveParameterMap", {}).get("value", []):
        parameter = _save_parameter(character)
        if parameter is None:
            continue
        if _nu(((parameter.get("OwnerPlayerUId") or {}).get("value"))) == wanted:
            found.append(character)
            continue
        # The player themselves. `IsPlayer` alone is not enough — every player in
        # the world has it — so the key's PlayerUId is what identifies this one.
        is_player = bool((parameter.get("IsPlayer") or {}).get("value"))
        if is_player and _nu((character.get("key") or {}).get("PlayerUId", {}).get("value")) == wanted:
            found.append(character)
    return found


def _characters_in_guild(gvas: Any, guild_id: str) -> list[dict]:
    wanted = _nu(guild_id)
    return [
        c for c in _world(gvas).get("CharacterSaveParameterMap", {}).get("value", [])
        if _nu(c["value"]["RawData"]["value"].get("group_id")) == wanted
    ]


def _instance_id(character: dict) -> str:
    return str((character.get("key") or {}).get("InstanceId", {}).get("value") or "")


def _base_entries(gvas: Any, guild_id: str) -> list[dict]:
    """Base camps whose `group_id_belong_to` names this guild."""
    wanted = _nu(guild_id)
    out = []
    for base in _world(gvas).get("BaseCampSaveData", {}).get("value", []) or []:
        try:
            raw = base["value"]["RawData"]["value"]
        except (KeyError, TypeError):
            continue
        # `group_id_belong_to` is the GUILD. `id` beside it is the base's own.
        # Both are GUIDs in the same dict and substituting one for the other
        # still produces a plausible-looking grouping — see AGENTS.md.
        if _nu(raw.get("group_id_belong_to")) == wanted:
            out.append(base)
    return out


# ─── Planning ────────────────────────────────────────────


def _plan_hash(payload: dict[str, Any]) -> str:
    """
    Fingerprint of the world state a plan was computed against.

    The apply re-plans from the live tree and refuses if this no longer matches,
    which is what stops a world that moved between preview and apply from being
    written blind — the same guarantee `saveimport.apply_container_import` gives.
    """
    material = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _build_plan(gvas: Any, player_uid: str, target_guild_id: str,
                transfer_bases: bool) -> dict[str, Any]:
    problems: list[str] = []
    warnings: list[str] = []

    origin, member = _find_member(gvas, player_uid)
    target = _find_guild(gvas, target_guild_id)

    if member is None:
        problems.append(
            f"No guild in this world lists player {player_uid}. A player with no "
            "guild has nothing to move from."
        )
    if target is None:
        problems.append(f"No guild with id {target_guild_id} in this world.")
    if problems:
        return {"ok": False, "problems": problems, "warnings": warnings, "planHash": ""}

    assert origin is not None and target is not None  # narrowed by the checks above

    if origin is target:
        return {
            "ok": False,
            "problems": [f"That player is already in {_guild_label(target)}."],
            "warnings": warnings,
            "planHash": "",
        }

    origin_id = str(_raw(origin).get("group_id") or "")
    target_id = str(_raw(target).get("group_id") or "")

    moving = _owned_characters(gvas, player_uid)
    remaining_members = [
        m for m in _members(origin) if _nu(m.get("player_uid")) != _nu(player_uid)
    ]
    origin_bases = _base_entries(gvas, origin_id)
    # Characters left behind in the origin guild once the player's own are gone.
    # On a solo guild these are the base workers, which is the number that makes
    # the refusal below concrete rather than abstract.
    moving_ids = {_instance_id(c) for c in moving}
    stranded = [
        c for c in _characters_in_guild(gvas, origin_id)
        if _instance_id(c) not in moving_ids
    ]

    empties = not remaining_members
    if empties and not transfer_bases:
        problems.append(
            f"{_guild_label(origin)} has no other members, so moving this player "
            f"would leave {len(origin_bases)} base(s) and {len(stranded)} "
            "base-deployed Pal(s) in a guild with nobody in it. Re-run with "
            "'transfer bases' to bring them along to "
            f"{_guild_label(target)}, or move another player into "
            f"{_guild_label(origin)} first."
        )

    if empties and transfer_bases:
        after = len(_base_entries(gvas, target_id)) + len(origin_bases)
        if after > 4:
            warnings.append(
                f"{_guild_label(target)} will own {after} bases afterwards. "
                "Palworld's own limit is usually 3-4, so the game may not let "
                "anyone build another until some are dismantled. Nothing is lost "
                "either way — this is a limit on building, not on keeping."
            )

    payload = {
        "playerUid": _nu(player_uid),
        "originGuildId": _nu(origin_id),
        "targetGuildId": _nu(target_id),
        "transferBases": bool(transfer_bases and empties),
        "movingInstanceIds": sorted(moving_ids),
        "originMembers": len(_members(origin)),
        "targetMembers": len(_members(target)),
        "originBases": sorted(_nu(_base_id(b)) for b in origin_bases),
        "strandedInstanceIds": sorted(_instance_id(c) for c in stranded),
    }

    name = str((member.get("player_info") or {}).get("player_name") or player_uid)
    return {
        "ok": not problems,
        "problems": problems,
        "warnings": warnings,
        "playerUid": str(player_uid),
        "playerName": name,
        "origin": {
            "id": origin_id,
            "name": _guild_label(origin),
            "members": len(_members(origin)),
            "bases": len(origin_bases),
            "isAdmin": _nu(_raw(origin).get("admin_player_uid")) == _nu(player_uid),
        },
        "target": {
            "id": target_id,
            "name": _guild_label(target),
            "members": len(_members(target)),
            "bases": len(_base_entries(gvas, target_id)),
        },
        # What actually changes, so a confirmation dialog can be specific rather
        # than asking someone to trust a verb.
        "movesCharacters": len(moving),
        "movesBases": len(origin_bases) if payload["transferBases"] else 0,
        "movesBaseWorkers": len(stranded) if payload["transferBases"] else 0,
        "removesOriginGuild": bool(payload["transferBases"]),
        "newLeaderOfOrigin": (
            str((remaining_members[0].get("player_info") or {}).get("player_name") or "")
            if remaining_members
            and _nu(_raw(origin).get("admin_player_uid")) == _nu(player_uid)
            else ""
        ),
        "planHash": _plan_hash(payload),
        "_payload": payload,
    }


def _base_id(base: dict) -> str:
    try:
        return str(base["value"]["RawData"]["value"].get("id") or "")
    except (KeyError, TypeError):
        return ""


def plan_guild_move(player_uid: str, target_guild_id: str,
                    transfer_bases: bool = False) -> dict[str, Any]:
    """Preview a move. Reads `Level.sav`; writes nothing."""
    from parser import load_gvas

    level_path = get_level_sav_path()
    if not level_path:
        raise GuildEditError("Level.sav not found")

    gvas = load_gvas(level_path)
    if gvas is None:
        raise GuildEditError("Could not parse Level.sav")

    plan = _build_plan(gvas, player_uid, target_guild_id, transfer_bases)
    plan.pop("_payload", None)
    return plan


# ─── Applying ────────────────────────────────────────────


def _handle_ids(raw: dict) -> list:
    handles = raw.get("individual_character_handle_ids")
    if not isinstance(handles, list):
        handles = []
        raw["individual_character_handle_ids"] = handles
    return handles


def _move_handles(origin_raw: dict, target_raw: dict, instance_ids: set[str]) -> None:
    """
    Move a guild's index entries for the given characters.

    The handle carries a `guid` alongside the instance id, so entries are
    **relocated rather than rebuilt** — reconstructing one means inventing that
    guid, and the origin already holds the right value.
    """
    origin_handles = _handle_ids(origin_raw)
    target_handles = _handle_ids(target_raw)

    taken = [h for h in origin_handles if str(h.get("instance_id", "")) in instance_ids]
    origin_raw["individual_character_handle_ids"] = [
        h for h in origin_handles if str(h.get("instance_id", "")) not in instance_ids
    ]

    present = {str(h.get("instance_id", "")) for h in target_handles}
    for handle in taken:
        if str(handle.get("instance_id", "")) not in present:
            target_handles.append(handle)
            present.add(str(handle.get("instance_id", "")))


def apply_guild_move(player_uid: str, target_guild_id: str,
                     transfer_bases: bool = False,
                     plan_hash: str = "") -> dict[str, Any]:
    """
    Move the player, all at once or not at all.

    Re-plans against the tree opened **inside** the write guard, never against
    the preview's copy — the same rule `palimport` follows. A `plan_hash` that no
    longer matches is a refusal, because the world moved between preview and
    apply and the operator was shown something that is no longer true.
    """
    from palsav.core import compress_gvas_to_sav, decompress_sav_to_gvas
    from palsav.gvas import GvasFile
    from palsav.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS
    from savefiles import read_sav_bytes

    level_path = get_level_sav_path()
    if not level_path:
        raise GuildEditError("Level.sav not found")
    world_dir = os.path.dirname(level_path)

    with guarded_save_write(f"move player {player_uid} to guild", world_dir) as backup:
        original = read_sav_bytes(level_path)
        if original is None:
            raise GuildEditError("Could not read Level.sav")

        raw_gvas, save_type = decompress_sav_to_gvas(original)
        gvas = GvasFile.read(raw_gvas, PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES)

        plan = _build_plan(gvas, player_uid, target_guild_id, transfer_bases)
        if not plan["ok"]:
            raise GuildEditError("; ".join(plan["problems"]))
        if plan_hash and plan_hash != plan["planHash"]:
            raise GuildEditError(
                "The world changed since this move was previewed. Preview it "
                "again — applying a stale plan would act on a guild that is no "
                "longer shaped the way you were shown."
            )

        payload = plan["_payload"]
        origin = _find_guild(gvas, payload["originGuildId"])
        target = _find_guild(gvas, payload["targetGuildId"])
        assert origin is not None and target is not None
        origin_raw, target_raw = _raw(origin), _raw(target)

        before = {
            "guilds": len(_guild_entries(gvas)),
            "targetCharacters": len(_characters_in_guild(gvas, payload["targetGuildId"])),
            "otherMembers": {
                _nu(_raw(e).get("group_id")): len(_members(e))
                for e in _guild_entries(gvas)
                if _nu(_raw(e).get("group_id")) not in
                (payload["originGuildId"], payload["targetGuildId"])
            },
        }

        # ── 1. Member lists ──
        member = next(
            m for m in _members(origin)
            if _nu(m.get("player_uid")) == payload["playerUid"]
        )
        origin_raw["players"] = [
            m for m in _members(origin)
            if _nu(m.get("player_uid")) != payload["playerUid"]
        ]

        target_members = target_raw.setdefault("players", [])
        member["role"] = ROLE_MEMBER
        if not target_members:
            # An empty target has no leader to defer to.
            member["role"] = ROLE_ADMIN
            target_raw["admin_player_uid"] = member["player_uid"]
        target_members.append(member)

        # A guild whose admin just left needs a new one, or nobody can manage it.
        if origin_raw["players"] and _nu(origin_raw.get("admin_player_uid")) == payload["playerUid"]:
            origin_raw["admin_player_uid"] = origin_raw["players"][0]["player_uid"]
            origin_raw["players"][0]["role"] = ROLE_ADMIN

        # ── 2. Characters ──
        new_group_id = target_raw["group_id"]
        moving_ids = set(payload["movingInstanceIds"])
        if payload["transferBases"]:
            moving_ids |= set(payload["strandedInstanceIds"])

        moved = 0
        for character in _world(gvas).get("CharacterSaveParameterMap", {}).get("value", []):
            if _instance_id(character) not in moving_ids:
                continue
            character["value"]["RawData"]["value"]["group_id"] = new_group_id
            parameter = _save_parameter(character)
            if parameter is not None:
                # Expeditions are guild-scoped; an assignment that survives the
                # move points at a map object the new guild does not own.
                parameter.pop("MapObjectConcreteInstanceIdAssignedToExpedition", None)
            moved += 1

        # ── 3. Handle index, both sides ──
        _move_handles(origin_raw, target_raw, moving_ids)

        # ── 4. Bases, only when asked ──
        moved_bases = 0
        if payload["transferBases"]:
            target_base_ids = target_raw.setdefault("base_ids", [])
            origin_base_ids = list(origin_raw.get("base_ids") or [])
            for base in _base_entries(gvas, payload["originGuildId"]):
                base["value"]["RawData"]["value"]["group_id_belong_to"] = new_group_id
                moved_bases += 1
            for base_id in origin_base_ids:
                if base_id not in target_base_ids:
                    target_base_ids.append(base_id)
            origin_raw["base_ids"] = []

            # The emptied guild goes only after everything it owned has been
            # re-homed, so a failure anywhere above leaves it holding its bases.
            groups = _world(gvas)["GroupSaveDataMap"]["value"]
            groups.remove(origin)

        # ── 5. Verify in memory ──
        _verify(gvas, payload, before, moved, expect_origin_gone=payload["transferBases"])

        encoded = compress_gvas_to_sav(gvas.write(PALWORLD_CUSTOM_PROPERTIES), save_type)
        atomic_write(level_path, encoded)
        logger.info(
            "Moved %s to guild %s (%d characters, %d bases)",
            player_uid, payload["targetGuildId"], moved, moved_bases,
        )

        # ── 6. Verify again, from disk ──
        #
        # The check that catches an encoder fault rather than trusting the tree
        # we just built. A guild move touches four structures; a write that
        # serialises three of them is exactly what this is looking for.
        try:
            verify_bytes = read_sav_bytes(level_path)
            verify_gvas = GvasFile.read(
                decompress_sav_to_gvas(verify_bytes)[0],
                PALWORLD_TYPE_HINTS,
                PALWORLD_CUSTOM_PROPERTIES,
            )
            _verify(verify_gvas, payload, before, moved,
                    expect_origin_gone=payload["transferBases"])
        except Exception as e:  # noqa: BLE001
            logger.error("Guild move verification failed, rolling back: %s", e)
            try:
                restore_backup(backup["id"], scope="world")
            except Exception as rollback_error:  # noqa: BLE001
                raise GuildEditError(
                    f"Write verification FAILED and automatic rollback also failed "
                    f"({rollback_error}). Restore backup {backup['id']} manually. "
                    f"Original cause: {e}"
                ) from e
            raise GuildEditError(
                f"Write verification failed and the world was rolled back to backup "
                f"{backup['id']}. Nothing was lost. Cause: {e}"
            ) from e

        return {
            "ok": True,
            "playerUid": str(player_uid),
            "playerName": plan["playerName"],
            "fromGuild": plan["origin"]["name"],
            "toGuild": plan["target"]["name"],
            "charactersMoved": moved,
            "basesMoved": moved_bases,
            "originGuildRemoved": payload["transferBases"],
            "backupId": backup["id"],
            "verified": True,
        }


def _verify(gvas: Any, payload: dict, before: dict, moved: int,
            *, expect_origin_gone: bool) -> None:
    """
    Every invariant a correct move has to satisfy. Raises on the first failure.

    Counts rather than value comparisons, like `palclone`'s verification: the
    question is whether the four structures agree, and a count that is off says
    so without needing to know what any individual record should contain.
    """
    target_id = payload["targetGuildId"]
    origin_id = payload["originGuildId"]

    target = _find_guild(gvas, target_id)
    if target is None:
        raise GuildEditError("The target guild is missing after the write")

    # The player is in the target, and in nothing else.
    holding = [
        _nu(_raw(e).get("group_id")) for e in _guild_entries(gvas)
        for m in _members(e) if _nu(m.get("player_uid")) == payload["playerUid"]
    ]
    if holding != [target_id]:
        raise GuildEditError(
            f"Player should be in exactly the target guild, found in {holding or 'none'}"
        )

    origin = _find_guild(gvas, origin_id)
    if expect_origin_gone:
        if origin is not None:
            raise GuildEditError("The emptied origin guild was not removed")
        if _base_entries(gvas, origin_id):
            raise GuildEditError("Bases still point at the removed origin guild")
    else:
        if origin is None:
            raise GuildEditError("The origin guild vanished; it should have been kept")
        if len(_members(origin)) != payload["originMembers"] - 1:
            raise GuildEditError("The origin guild's member count is wrong")
        # A guild that kept members must still have a leader who is in it.
        admins = {_nu(m.get("player_uid")) for m in _members(origin)}
        if _nu(_raw(origin).get("admin_player_uid")) not in admins:
            raise GuildEditError("The origin guild's leader is not one of its members")

    if len(_members(target)) != payload["targetMembers"] + 1:
        raise GuildEditError("The target guild's member count is wrong")

    # Characters actually carry the new guild id.
    after = len(_characters_in_guild(gvas, target_id))
    if after != before["targetCharacters"] + moved:
        raise GuildEditError(
            f"Expected {before['targetCharacters'] + moved} characters in the target "
            f"guild, found {after}"
        )
    if _characters_in_guild(gvas, origin_id) and expect_origin_gone:
        raise GuildEditError("Characters still point at the removed origin guild")

    # And the handle index agrees with them, on both sides. A move that updates
    # `group_id` but not this leaves the guild listing characters it does not own.
    handles = {str(h.get("instance_id", "")) for h in _handle_ids(_raw(target))}
    missing = [i for i in payload["movingInstanceIds"] if i not in handles]
    if missing:
        raise GuildEditError(
            f"{len(missing)} moved character(s) are missing from the target guild's "
            "handle index"
        )

    # Nothing happened to anyone else.
    for entry in _guild_entries(gvas):
        gid = _nu(_raw(entry).get("group_id"))
        if gid in (origin_id, target_id):
            continue
        if len(_members(entry)) != before["otherMembers"].get(gid):
            raise GuildEditError(f"Guild {gid} changed membership and should not have")

    expected_guilds = before["guilds"] - (1 if expect_origin_gone else 0)
    if len(_guild_entries(gvas)) != expected_guilds:
        raise GuildEditError("The number of guilds changed unexpectedly")
