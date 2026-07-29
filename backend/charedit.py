"""
Character editor — Pals first (Phase 7).

Writes validated field changes into `CharacterSaveParameterMap`. Validation
lives in `editschema`; this module is the write path and nothing else, following
the same split as export/import.

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
            f"This Pal has no {prop!r} stored, so there is no shape to write into. "
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
