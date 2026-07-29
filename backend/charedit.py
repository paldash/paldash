"""
Character editor — Pals and players (Phase 7).

Writes validated field changes into the save. Validation lives in `editschema`;
this module is the write path and nothing else, following the same split as
export/import.

A Pal lives entirely in `Level.sav`. A **player does not**: their name, level
and EXP are in `Level.sav` while their technology points are in
`Players/<UID>.sav`, so a player edit spans two files that cannot be written
atomically together. Both are verified after writing and any mismatch rolls back
the whole world, which is coherent because the pre-edit backup covers
`Players/` too.

THE SHAPE PROBLEM
-----------------
Reading a property is forgiving; writing is not. Palworld 1.0 stores `Level` and
`Talent_*` as **ByteProperty**, which nests one level deeper than Int:

    Level:      {'value': {'type': 'None', 'value': 24}}   <- ByteProperty
    Exp:        {'value': 1234567}                          <- IntProperty

`parser._num` reads both. Writing to the wrong depth produces a file that still
serialises and still loads, with the edit silently ignored — the worst kind of
failure, because it looks like it worked. `_write_property` therefore writes
**into the existing shape** rather than constructing one, and refuses outright
when the property is absent, because inventing a property means guessing its
type tag.

That refusal is deliberate: a Pal with no `Talent_HP` in its save has never had
that IV rolled, and fabricating one is a change to game state we cannot verify.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import editschema
import saveexport
from parser import _num, _prop, _v

logger = logging.getLogger(__name__)

# Schema field name -> the property name in the save.
PAL_PROPERTY_MAP = {
    "nickname": "NickName",
    "level": "Level",
    "exp": "Exp",
    "rank": "Rank",
    "ivs.hp": "Talent_HP",
    "ivs.shot": "Talent_Shot",
    "ivs.defense": "Talent_Defense",
}

# Fields the editor refuses to touch even though the schema can describe them.
# Species and gender rewrite what a Pal *is*, which cascades into the Paldeck,
# breeding eligibility and the palbox UI. Out of scope until there is a reason.
PAL_READ_ONLY = ("speciesId", "gender", "passiveSkills")

# A player is stored across TWO files, which is the whole complication:
#
#   Level.sav          — the character: NickName, Level, Exp
#   Players/<UID>.sav  — the account: technology points
#
# One `guarded_save_write` covers both, because `collect_world_files` walks the
# entire world directory including `Players/`, so the backup and any rollback
# are consistent across the pair. The writes themselves cannot be atomic
# together, so both are verified and any failure rolls back the whole world.
PLAYER_CHARACTER_MAP = {
    "nickname": "NickName",
    "level": "Level",
    "exp": "Exp",
}

# `bossTechnologyPoint` really is the ancient-technology counter — the naming is
# the game's, not ours. `TechnologyPoint` is **absent** on players who have
# never banked an unspent point (1 of the 5 in the reference world), and an
# absent property cannot be written without guessing its type.
PLAYER_SAVE_MAP = {
    "technologyPoints": "TechnologyPoint",
    "ancientTechnologyPoints": "bossTechnologyPoint",
}

PLAYER_PROPERTY_MAP = {**PLAYER_CHARACTER_MAP, **PLAYER_SAVE_MAP}


class EditError(Exception):
    """Raised when an edit cannot be planned or applied."""


def _character_entries(gvas: Any) -> list:
    from parser import _world_save_data

    return _v(_world_save_data(gvas), "CharacterSaveParameterMap", "value", default=[]) or []


def _save_parameter(entry: dict) -> Optional[dict]:
    obj = _v(entry, "value", "RawData", "value", "object", "SaveParameter", "value")
    return obj if isinstance(obj, dict) else None


def read_pal(obj: dict) -> dict:
    """The editable view of one Pal, in schema field names."""
    ivs = {}
    for field, prop in PAL_PROPERTY_MAP.items():
        if field.startswith("ivs.") and prop in obj:
            ivs[field.split(".", 1)[1]] = _num(obj, prop, 0)

    return {
        "nickname": str(_prop(obj, "NickName", "") or ""),
        "level": _num(obj, "Level", 1),
        "exp": _num(obj, "Exp", 0),
        "rank": _num(obj, "Rank", 1) or 1,
        "ivs": ivs,
    }


def _write_property(obj: dict, prop: str, value: Any) -> None:
    """
    Write into the property's existing shape.

    ByteProperty nests one deeper than Int. Rather than deciding which this is,
    look at what is already there — the save itself is the authority on its own
    encoding.
    """
    if prop not in obj:
        raise EditError(
            f"This save has no {prop!r} stored, so there is no shape to write into. "
            "Creating the property would mean guessing its type, which is how a save "
            "stops loading."
        )

    node = obj[prop]
    if not isinstance(node, dict) or "value" not in node:
        raise EditError(f"{prop!r} is not in the expected property shape")

    inner = node["value"]
    if isinstance(inner, dict) and "value" in inner:
        inner["value"] = value      # ByteProperty / EnumProperty
    else:
        node["value"] = value       # IntProperty / StrProperty


def plan_pal_edit(obj: dict, changes: dict) -> dict:
    """
    Validate and diff a proposed Pal edit. Pure — no writes.

    Returns the same shape as the import planner, including a `planHash` so an
    apply can refuse if the world moved after the operator approved the preview.
    """
    current = read_pal(obj)

    rejected = [f for f in changes if f in PAL_READ_ONLY]
    if rejected:
        return {
            "ok": False,
            "problems": [{
                "field": f,
                "problem": f"{f} cannot be edited — it changes what the Pal is, "
                           "which cascades into the Paldeck and breeding.",
            } for f in rejected],
            "changes": [], "planHash": "",
        }

    unmapped = [f for f in changes if f not in PAL_PROPERTY_MAP]
    if unmapped:
        return {
            "ok": False,
            "problems": [{"field": f, "problem": f"{f} is not a writable Pal field"}
                         for f in unmapped],
            "changes": [], "planHash": "",
        }

    report = editschema.validate("pal", changes, current=current)
    if not report["ok"]:
        return {"ok": False, "problems": report["problems"], "changes": [], "planHash": ""}

    diff = editschema.diff("pal", report["changes"], current)
    plan = {
        "ok": True,
        "problems": [],
        "changes": diff,
        "fieldsChanged": len(diff),
        "current": current,
        "crossFieldChecked": report["crossFieldChecked"],
    }
    plan["planHash"] = saveexport.checksum(diff)
    return plan


def read_player(char_obj: dict, player_save: Optional[dict] = None) -> dict:
    """
    The editable view of a player, merged across both files.

    `player_save` is `SaveData.value` from `Players/<UID>.sav`. Fields it does
    not carry are simply absent from the view rather than defaulted to 0 — the
    difference between "nought unspent points" and "this save has never had that
    property" matters, because only one of them can be written.
    """
    view: dict[str, Any] = {
        "nickname": str(_prop(char_obj, "NickName", "") or ""),
        "level": _num(char_obj, "Level", 1),
        "exp": _num(char_obj, "Exp", 0),
    }
    for field, prop in PLAYER_SAVE_MAP.items():
        if player_save is not None and prop in player_save:
            view[field] = _num(player_save, prop, 0)
    return view


def plan_player_edit(
    char_obj: dict, changes: dict, player_save: Optional[dict] = None
) -> dict:
    """Validate and diff a proposed player edit. Pure — no writes."""
    current = read_player(char_obj, player_save)

    unmapped = [f for f in changes if f not in PLAYER_PROPERTY_MAP]
    if unmapped:
        return {
            "ok": False,
            "problems": [{"field": f, "problem": f"{f} is not a writable player field"}
                         for f in unmapped],
            "changes": [], "planHash": "",
        }

    # Refuse fields whose property this particular save does not carry, before
    # the write path discovers it and has to roll back.
    missing = [
        f for f in changes
        if f in PLAYER_SAVE_MAP and (player_save is None or PLAYER_SAVE_MAP[f] not in player_save)
    ]
    if missing:
        return {
            "ok": False,
            "problems": [{
                "field": f,
                "problem": f"This player's save has no {PLAYER_SAVE_MAP[f]!r} stored, so there "
                           "is no shape to write into. It appears once they have banked "
                           "points of that kind in game.",
            } for f in missing],
            "changes": [], "planHash": "",
        }

    report = editschema.validate("player", changes, current=current)
    if not report["ok"]:
        return {"ok": False, "problems": report["problems"], "changes": [], "planHash": ""}

    diff = editschema.diff("player", report["changes"], current)
    plan = {
        "ok": True,
        "problems": [],
        "changes": diff,
        "fieldsChanged": len(diff),
        "current": current,
        "crossFieldChecked": report["crossFieldChecked"],
        # Which files this edit would touch, so the UI can say so.
        "touchesPlayerSave": any(c["field"] in PLAYER_SAVE_MAP for c in diff),
        "touchesLevelSav": any(c["field"] in PLAYER_CHARACTER_MAP for c in diff),
    }
    plan["planHash"] = saveexport.checksum(diff)
    return plan


def apply_pal_edit(
    instance_id: str,
    changes: dict,
    expected_plan_hash: Optional[str] = None,
) -> dict:
    """
    Apply a validated Pal edit.

    Same order as every other write in this project: guard and back up, re-plan
    against the live tree, refuse a stale plan, write, re-read from disk and
    verify, roll back on any mismatch.
    """
    from backup import guarded_save_write, restore_backup
    from palsav.core import compress_gvas_to_sav, decompress_sav_to_gvas
    from palsav.gvas import GvasFile
    from palsav.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS
    from savefiles import atomic_write, get_level_sav_path, read_sav_bytes

    if not instance_id:
        raise EditError("No Pal instance id given")

    level_path = get_level_sav_path()
    if not level_path:
        raise EditError("Level.sav not found")

    world_dir = os.path.dirname(level_path)

    with guarded_save_write(f"edit Pal {instance_id}", world_dir) as backup:
        original = read_sav_bytes(level_path)
        if original is None:
            raise EditError("Could not read Level.sav")

        raw_gvas, save_type = decompress_sav_to_gvas(original)
        gvas = GvasFile.read(raw_gvas, PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES)

        target = None
        for entry in _character_entries(gvas):
            key = entry.get("key") if isinstance(entry, dict) else None
            if str(_v(key, "InstanceId", "value", default="") or "") == instance_id:
                target = entry
                break
        if target is None:
            raise EditError(f"No Pal with instance id {instance_id} in this world")

        obj = _save_parameter(target)
        if obj is None:
            raise EditError("That character has no SaveParameter to edit")
        if _prop(obj, "IsPlayer", False) is True:
            raise EditError(
                "That instance is a player character. Player editing is not implemented."
            )

        plan = plan_pal_edit(obj, changes)
        if not plan["ok"]:
            raise EditError("; ".join(p["problem"] for p in plan["problems"][:5]))
        if expected_plan_hash and plan["planHash"] != expected_plan_hash:
            raise EditError(
                "This Pal changed since the edit was previewed — the plan no longer "
                "matches what you approved. Preview it again."
            )

        for change in plan["changes"]:
            _write_property(obj, PAL_PROPERTY_MAP[change["field"]], change["after"])

        encoded = compress_gvas_to_sav(gvas.write(PALWORLD_CUSTOM_PROPERTIES), save_type)
        atomic_write(level_path, encoded)
        logger.info("Edited Pal %s (%d fields)", instance_id, len(plan["changes"]))

        try:
            verify_gvas = GvasFile.read(
                decompress_sav_to_gvas(read_sav_bytes(level_path))[0],
                PALWORLD_TYPE_HINTS,
                PALWORLD_CUSTOM_PROPERTIES,
            )
            written = None
            for entry in _character_entries(verify_gvas):
                key = entry.get("key") if isinstance(entry, dict) else None
                if str(_v(key, "InstanceId", "value", default="") or "") == instance_id:
                    written = _save_parameter(entry)
                    break
            if written is None:
                raise EditError("The Pal vanished from the written file")

            after = editschema._flatten(read_pal(written))
            for change in plan["changes"]:
                actual = after.get(change["field"])
                if actual != change["after"]:
                    raise EditError(
                        f"{change['field']} reads back as {actual!r} rather than "
                        f"{change['after']!r} — the write did not take"
                    )
        except Exception as e:  # noqa: BLE001
            logger.error("Pal edit verification failed, rolling back: %s", e)
            try:
                restore_backup(backup["id"], scope="world")
            except Exception as rollback_error:  # noqa: BLE001
                raise EditError(
                    f"Edit verification FAILED and automatic rollback also failed "
                    f"({rollback_error}). Restore backup {backup['id']} manually. "
                    f"Original cause: {e}"
                ) from e
            raise EditError(
                f"Edit verification failed and the world was rolled back to backup "
                f"{backup['id']}. Nothing was lost. Cause: {e}"
            ) from e

        return {
            "ok": True,
            "applied": True,
            "instanceId": instance_id,
            "fieldsChanged": len(plan["changes"]),
            "changes": plan["changes"],
            "backupId": backup["id"],
            "planHash": plan["planHash"],
            "verified": True,
        }


def apply_player_edit(
    uid: str,
    changes: dict,
    expected_plan_hash: Optional[str] = None,
) -> dict:
    """
    Apply a validated player edit across both files.

    The extra hazard over a Pal edit is that two files must change together and
    cannot be written atomically. Both are written, then both are re-read and
    verified, and *any* mismatch restores the whole world — which is coherent
    because `collect_world_files` walks `Players/` too, so the pre-edit backup
    covers the pair.
    """
    from backup import guarded_save_write, restore_backup
    from palsav.core import compress_gvas_to_sav, decompress_sav_to_gvas
    from palsav.gvas import GvasFile
    from palsav.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS
    from savefiles import atomic_write, get_level_sav_path, get_player_sav_path, read_sav_bytes

    if not uid:
        raise EditError("No player uid given")

    level_path = get_level_sav_path()
    if not level_path:
        raise EditError("Level.sav not found")

    world_dir = os.path.dirname(level_path)
    player_path = get_player_sav_path(uid, world_dir)

    def read_tree(path: str):
        raw = read_sav_bytes(path)
        if raw is None:
            raise EditError(f"Could not read {os.path.basename(path)}")
        decoded, save_type = decompress_sav_to_gvas(raw)
        return GvasFile.read(decoded, PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES), save_type

    with guarded_save_write(f"edit player {uid}", world_dir) as backup:
        level_tree, level_type = read_tree(level_path)

        key_uid = uid.replace("-", "").lower()
        char_obj = None
        for entry in _character_entries(level_tree):
            key = entry.get("key") if isinstance(entry, dict) else None
            entry_uid = str(_v(key, "PlayerUId", "value", default="") or "")
            if entry_uid.replace("-", "").lower() != key_uid:
                continue
            obj = _save_parameter(entry)
            if obj is not None and _prop(obj, "IsPlayer", False) is True:
                char_obj = obj
                break
        if char_obj is None:
            raise EditError(f"No player character with uid {uid} in this world")

        player_tree = player_save = None
        player_type = None
        if player_path and os.path.exists(player_path):
            player_tree, player_type = read_tree(player_path)
            player_save = _v(getattr(player_tree, "properties", {}), "SaveData", "value") or {}

        plan = plan_player_edit(char_obj, changes, player_save)
        if not plan["ok"]:
            raise EditError("; ".join(p["problem"] for p in plan["problems"][:5]))
        if expected_plan_hash and plan["planHash"] != expected_plan_hash:
            raise EditError(
                "This player changed since the edit was previewed — the plan no longer "
                "matches what you approved. Preview it again."
            )
        if not plan["changes"]:
            raise EditError("Nothing to change — the player already has those values")

        for change in plan["changes"]:
            field = change["field"]
            if field in PLAYER_CHARACTER_MAP:
                _write_property(char_obj, PLAYER_CHARACTER_MAP[field], change["after"])
            else:
                _write_property(player_save, PLAYER_SAVE_MAP[field], change["after"])

        written_files = []
        if plan["touchesLevelSav"]:
            atomic_write(
                level_path,
                compress_gvas_to_sav(level_tree.write(PALWORLD_CUSTOM_PROPERTIES), level_type),
            )
            written_files.append(os.path.basename(level_path))
        if plan["touchesPlayerSave"] and player_tree is not None:
            atomic_write(
                player_path,
                compress_gvas_to_sav(player_tree.write(PALWORLD_CUSTOM_PROPERTIES), player_type),
            )
            written_files.append(os.path.basename(player_path))

        logger.info("Edited player %s (%d fields across %s)", uid, len(plan["changes"]),
                    ", ".join(written_files))

        try:
            verify_level, _ = read_tree(level_path)
            verify_char = None
            for entry in _character_entries(verify_level):
                key = entry.get("key") if isinstance(entry, dict) else None
                entry_uid = str(_v(key, "PlayerUId", "value", default="") or "")
                if entry_uid.replace("-", "").lower() == key_uid:
                    obj = _save_parameter(entry)
                    if obj is not None and _prop(obj, "IsPlayer", False) is True:
                        verify_char = obj
                        break
            if verify_char is None:
                raise EditError("The player character vanished from the written file")

            verify_save = None
            if plan["touchesPlayerSave"] and player_path:
                verify_tree, _ = read_tree(player_path)
                verify_save = _v(getattr(verify_tree, "properties", {}), "SaveData", "value") or {}

            after = editschema._flatten(read_player(verify_char, verify_save))
            for change in plan["changes"]:
                actual = after.get(change["field"])
                if actual != change["after"]:
                    raise EditError(
                        f"{change['field']} reads back as {actual!r} rather than "
                        f"{change['after']!r} — the write did not take"
                    )
        except Exception as e:  # noqa: BLE001
            logger.error("Player edit verification failed, rolling back: %s", e)
            try:
                restore_backup(backup["id"], scope="world")
            except Exception as rollback_error:  # noqa: BLE001
                raise EditError(
                    f"Edit verification FAILED and automatic rollback also failed "
                    f"({rollback_error}). Restore backup {backup['id']} manually. "
                    f"Original cause: {e}"
                ) from e
            raise EditError(
                f"Edit verification failed and the world was rolled back to backup "
                f"{backup['id']}. Nothing was lost. Cause: {e}"
            ) from e

        return {
            "ok": True,
            "applied": True,
            "uid": uid,
            "fieldsChanged": len(plan["changes"]),
            "changes": plan["changes"],
            "filesWritten": written_files,
            "backupId": backup["id"],
            "planHash": plan["planHash"],
            "verified": True,
        }
