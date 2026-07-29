"""
Pal duplication (Phase 7).

Deliberately its own module, for the same reason `saveimport` is separate from
`saveexport`: this is the only code in the project that **creates** save records
rather than overwriting fields that already exist. Everything else writes into a
shape the game itself produced. This invents one, so it should be readable in
full by anyone reviewing what can add to a world.

WHAT A PAL ACTUALLY IS
----------------------
Two records, in two different arrays, that must agree:

    CharacterSaveParameterMap[]
      .key.InstanceId                     <- the identity
      .value.RawData.value.object.SaveParameter.value
                                          <- the Pal: species, level, IVs, skills
      .value.RawData.value.group_id       <- the guild
      SaveParameter.SlotId.value
        .ContainerId.value.ID.value       <- which palbox/party
        .SlotIndex.value                  <- where in it

    CharacterContainerSaveData[]
      .key.ID.value                       == that ContainerId
      .value.SlotNum                      <- capacity
      .value.Slots.value.values[]
        .SlotIndex.value
        .RawData.value.instance_id        == that InstanceId

A clone is a deep copy of the first with a fresh `InstanceId`, plus a new slot
entry in the second pointing at it. Miss either half and the Pal is a ghost: in
the file, not in anyone's palbox, or occupying a slot that resolves to nothing.

THERE ARE NO EMPTY SLOTS TO FILL
--------------------------------
This is the finding that shaped the implementation. Across the reference world's
23 character containers there are **1,905 slot entries and 1,905 Pals — zero
empty slots**. `SlotNum` is the *capacity* (960 for a palbox) while the array
holds only occupied slots, so free space is `SlotNum - len(slots)`.

So adding a Pal means **appending** a slot, not filling one. That is a heavier
change than anything else in this project, and it is why the verification below
counts records rather than just comparing values.

WHAT THIS REFUSES
-----------------
- Cloning a **player** character. Two characters sharing a PlayerUId is not a
  situation the game is designed for.
- Cloning into a container with no free capacity.
- Any target the caller did not name explicitly. There is no "find a slot
  anywhere" mode, because silently putting a Pal in someone else's palbox is a
  worse outcome than an error.
"""

from __future__ import annotations

import copy
import logging
import os
import uuid
from typing import Any, Optional

import charedit
import gamedata
import saveexport
from parser import ZERO_GUID, _prop, _v

logger = logging.getLogger(__name__)

# One request cannot create more than this. A clone is the most expensive write
# here — it grows two arrays — and a runaway loop with the server down is its
# own kind of outage.
MAX_CLONES = 50


class CloneError(Exception):
    """Raised when a clone cannot be planned or applied."""


def _containers(gvas: Any) -> list:
    from parser import _world_save_data

    return _v(_world_save_data(gvas), "CharacterContainerSaveData", "value", default=[]) or []


def _container_id(entry: dict) -> str:
    return str(_v(entry, "key", "ID", "value", default="") or "")


def _container_slots(entry: dict) -> list:
    return _v(entry, "value", "Slots", "value", "values", default=[]) or []


def _slot_instance(slot: dict) -> str:
    return str(_v(slot, "RawData", "value", "instance_id", default="") or "")


def _slot_index(slot: dict) -> int:
    return int(_v(slot, "SlotIndex", "value", default=0) or 0)


def describe_containers(gvas: Any) -> list[dict]:
    """
    Every character container with its capacity and free space. Read-only.

    `free` is `SlotNum - len(slots)` rather than a count of empty entries,
    because there are no empty entries — see the module docstring.
    """
    out = []
    for entry in _containers(gvas):
        slots = _container_slots(entry)
        capacity = int(_v(entry, "value", "SlotNum", "value", default=0) or 0)
        out.append({
            "containerId": _container_id(entry),
            "capacity": capacity,
            "used": len(slots),
            "free": max(0, capacity - len(slots)),
        })
    return out


def _pal_summary(obj: dict) -> dict:
    character_id = str(_prop(obj, "CharacterID", "") or "")
    return {
        "speciesId": character_id,
        "speciesName": gamedata.character_name(character_id),
        "nickname": str(_prop(obj, "NickName", "") or ""),
        **charedit.read_pal(obj),
    }


def plan_clone(
    gvas: Any,
    instance_id: str,
    container_id: str,
    count: int = 1,
    changes: Optional[dict] = None,
) -> dict:
    """
    Work out what cloning would do. Pure — no writes, no I/O.

    `changes` is an optional edit applied to each clone (a nickname, say), and it
    goes through exactly the same `editschema` validation a normal edit does. A
    clone is not a way around the bounds.
    """
    if not instance_id:
        return _refuse("No Pal named")
    if not container_id:
        return _refuse("No destination container named")
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        return _refuse("Count must be a positive whole number")
    if count > MAX_CLONES:
        return _refuse(f"{count} exceeds the {MAX_CLONES} maximum for one request")

    source = None
    for entry in charedit._character_entries(gvas):
        key = entry.get("key") if isinstance(entry, dict) else None
        if str(_v(key, "InstanceId", "value", default="") or "") == instance_id:
            source = entry
            break
    if source is None:
        return _refuse(f"No character with instance id {instance_id} in this world")

    obj = charedit._save_parameter(source)
    if obj is None:
        return _refuse("That character has no SaveParameter to copy")
    if _prop(obj, "IsPlayer", False) is True:
        return _refuse(
            "That is a player character. Cloning a player would put two characters on "
            "one account, which is not a state the game is built for."
        )

    target = next((c for c in _containers(gvas) if _container_id(c) == container_id), None)
    if target is None:
        return _refuse(f"No character container {container_id} in this world")

    slots = _container_slots(target)
    capacity = int(_v(target, "value", "SlotNum", "value", default=0) or 0)
    free = max(0, capacity - len(slots))
    if count > free:
        return _refuse(
            f"That container has {free} free slot(s) of {capacity}; {count} clone(s) "
            "would overflow it."
        )

    # A clone-time edit is validated exactly like any other edit.
    edit_plan = None
    if changes:
        edit_plan = charedit.plan_pal_edit(obj, changes)
        if not edit_plan["ok"]:
            return {
                "ok": False,
                "problems": edit_plan["problems"],
                "planHash": "",
            }

    # Slot indices continue from the current length. They are contiguous 0..n-1
    # on every container in the reference world.
    next_index = max((_slot_index(s) for s in slots), default=-1) + 1

    plan = {
        "ok": True,
        "problems": [],
        "instanceId": instance_id,
        "containerId": container_id,
        "count": count,
        "source": _pal_summary(obj),
        "slotIndices": list(range(next_index, next_index + count)),
        "capacity": capacity,
        "usedBefore": len(slots),
        "freeAfter": free - count,
        "changes": edit_plan["changes"] if edit_plan else [],
    }
    plan["planHash"] = saveexport.checksum({
        "source": instance_id,
        "container": container_id,
        "slots": plan["slotIndices"],
        "changes": plan["changes"],
    })
    return plan


def _refuse(message: str) -> dict:
    return {"ok": False, "problems": [{"field": None, "problem": message}], "planHash": ""}


def _new_slot(template: dict, instance_id: str, slot_index: int) -> dict:
    """
    A container slot entry for a new Pal, copied from an existing one.

    Copying rather than constructing is the point: the entry carries
    `CustomVersionData` and a `permission_tribe_id` whose correct values are
    whatever this save already uses. Building one from scratch would mean
    guessing all of that.
    """
    slot = copy.deepcopy(template)
    slot["SlotIndex"]["value"] = slot_index

    raw = _v(slot, "RawData", "value")
    if not isinstance(raw, dict) or "instance_id" not in raw:
        raise CloneError(
            "Character container slots are not decoded in this parse — the clone "
            "would have to write raw bytes, which it refuses to do."
        )
    raw["instance_id"] = instance_id
    # The slot's own player_uid is zero on every occupied slot in the reference
    # world; ownership lives on the Pal's OwnerPlayerUId, not here.
    raw["player_uid"] = ZERO_GUID
    return slot


def _new_character(template: dict, instance_id: str, container_id: str, slot_index: int) -> dict:
    """A CharacterSaveParameterMap entry for the clone."""
    entry = copy.deepcopy(template)

    key = entry.get("key")
    if not isinstance(key, dict) or "InstanceId" not in key:
        raise CloneError("Source character has no InstanceId to replace")
    key["InstanceId"]["value"] = instance_id

    obj = charedit._save_parameter(entry)
    if obj is None:
        raise CloneError("Copied character lost its SaveParameter")

    slot_id = _v(obj, "SlotId", "value")
    if not isinstance(slot_id, dict):
        raise CloneError("Source Pal has no SlotId, so the clone has nowhere to live")
    _v(slot_id, "ContainerId", "value", "ID")["value"] = container_id
    slot_id["SlotIndex"]["value"] = slot_index

    return entry


def apply_clone(
    instance_id: str,
    container_id: str,
    count: int = 1,
    changes: Optional[dict] = None,
    expected_plan_hash: Optional[str] = None,
) -> dict:
    """
    Create clones. The only function here that writes.

    Same order as every other write in this project — guard and back up, re-plan
    against the live tree, refuse a stale plan, write, re-read from disk and
    verify, roll back on any mismatch.

    The verification is stricter than an edit's, because this changes the *shape*
    of the save rather than values in it. After writing:

    - the character map must have grown by exactly `count`
    - the target container must have grown by exactly `count`
    - every new instance id must appear once in each, and resolve to the right slot
    - **no other container may have changed length**, which is what catches a
      clone that landed somewhere it was not asked to
    """
    from backup import guarded_save_write, restore_backup
    from palsav.core import compress_gvas_to_sav, decompress_sav_to_gvas
    from palsav.gvas import GvasFile
    from palsav.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS
    from parser import _custom_properties
    from savefiles import atomic_write, get_level_sav_path, read_sav_bytes

    level_path = get_level_sav_path()
    if not level_path:
        raise CloneError("Level.sav not found")

    world_dir = os.path.dirname(level_path)

    # Character-container slots are only decoded with the item property set, and
    # without them there is nothing to append to.
    props = {**PALWORLD_CUSTOM_PROPERTIES, **_custom_properties(include_items=True)}

    with guarded_save_write(f"clone Pal {instance_id} x{count}", world_dir) as backup:
        original = read_sav_bytes(level_path)
        if original is None:
            raise CloneError("Could not read Level.sav")

        raw_gvas, save_type = decompress_sav_to_gvas(original)
        gvas = GvasFile.read(raw_gvas, PALWORLD_TYPE_HINTS, props)

        plan = plan_clone(gvas, instance_id, container_id, count, changes)
        if not plan["ok"]:
            raise CloneError("; ".join(p["problem"] for p in plan["problems"][:5]))
        if expected_plan_hash and plan["planHash"] != expected_plan_hash:
            raise CloneError(
                "The world changed since this clone was previewed — the plan no longer "
                "matches what you approved. Preview it again."
            )

        chars = charedit._character_entries(gvas)
        source = next(
            e for e in chars
            if str(_v(e.get("key"), "InstanceId", "value", default="") or "") == instance_id
        )
        target = next(c for c in _containers(gvas) if _container_id(c) == container_id)
        slots = _container_slots(target)
        if not slots:
            raise CloneError(
                "That container has no existing slot to copy the entry shape from. "
                "Building one from scratch would mean guessing fields this save "
                "already knows the right values for."
            )

        # Fingerprint every container's length so we can prove we grew one.
        lengths_before = {
            _container_id(c): len(_container_slots(c)) for c in _containers(gvas)
        }
        chars_before = len(chars)

        new_ids: list[str] = []
        for slot_index in plan["slotIndices"]:
            new_id = str(uuid.uuid4())
            new_ids.append(new_id)

            entry = _new_character(source, new_id, container_id, slot_index)
            if plan["changes"]:
                obj = charedit._save_parameter(entry)
                for change in plan["changes"]:
                    charedit._apply_pal_change(obj, change)
            chars.append(entry)
            slots.append(_new_slot(slots[0], new_id, slot_index))

        encoded = compress_gvas_to_sav(gvas.write(PALWORLD_CUSTOM_PROPERTIES), save_type)
        atomic_write(level_path, encoded)
        logger.info("Cloned Pal %s x%d into %s", instance_id, count, container_id)

        try:
            verify = GvasFile.read(
                decompress_sav_to_gvas(read_sav_bytes(level_path))[0],
                PALWORLD_TYPE_HINTS,
                props,
            )
            verify_chars = charedit._character_entries(verify)
            if len(verify_chars) != chars_before + count:
                raise CloneError(
                    f"Character map has {len(verify_chars)} entries, expected "
                    f"{chars_before + count}"
                )

            lengths_after = {
                _container_id(c): len(_container_slots(c)) for c in _containers(verify)
            }
            for cid, before in lengths_before.items():
                expected = before + count if cid == container_id else before
                if lengths_after.get(cid) != expected:
                    raise CloneError(
                        f"Container {cid} has {lengths_after.get(cid)} slots, expected "
                        f"{expected} — the clone landed outside its scope"
                    )

            by_instance = {
                str(_v(e.get("key"), "InstanceId", "value", default="") or ""): e
                for e in verify_chars
            }
            written_container = next(
                c for c in _containers(verify) if _container_id(c) == container_id
            )
            slot_by_instance = {
                _slot_instance(s): _slot_index(s) for s in _container_slots(written_container)
            }

            for new_id, slot_index in zip(new_ids, plan["slotIndices"]):
                if new_id not in by_instance:
                    raise CloneError(f"Clone {new_id} is missing from the character map")
                if slot_by_instance.get(new_id) != slot_index:
                    raise CloneError(
                        f"Clone {new_id} is not in slot {slot_index} of its container"
                    )
                obj = charedit._save_parameter(by_instance[new_id])
                stored = str(_v(obj, "SlotId", "value", "ContainerId", "value", "ID", "value") or "")
                if stored != container_id:
                    raise CloneError(
                        f"Clone {new_id} points at container {stored}, not {container_id}"
                    )
        except Exception as e:  # noqa: BLE001
            logger.error("Clone verification failed, rolling back: %s", e)
            try:
                restore_backup(backup["id"], scope="world")
            except Exception as rollback_error:  # noqa: BLE001
                raise CloneError(
                    f"Clone verification FAILED and automatic rollback also failed "
                    f"({rollback_error}). Restore backup {backup['id']} manually. "
                    f"Original cause: {e}"
                ) from e
            raise CloneError(
                f"Clone verification failed and the world was rolled back to backup "
                f"{backup['id']}. Nothing was lost. Cause: {e}"
            ) from e

        return {
            "ok": True,
            "applied": True,
            "sourceInstanceId": instance_id,
            "containerId": container_id,
            "count": count,
            "newInstanceIds": new_ids,
            "slotIndices": plan["slotIndices"],
            "backupId": backup["id"],
            "planHash": plan["planHash"],
            "verified": True,
        }
