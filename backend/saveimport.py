"""
Save imports — the dangerous half of Phase 6.

Deliberately a separate module from `saveexport.py`. Export has no write path at
all; keeping them apart means the risky code is never one typo away from the
safe code, and this file can be read in full by anyone reviewing what can
modify a world.

THE RULES, IN ORDER
-------------------
1. **Nothing is applied without a dry run first.** `preview()` is pure: it takes
   a document and the current world state and returns exactly what would change.
   It cannot write. `apply()` re-runs the same planner and refuses if the plan
   has changed since the preview the caller was shown.
2. **A plan is only applied through `guarded_save_write`**, which re-checks that
   the server is provably stopped, takes a verified backup, and re-checks again.
3. **Conservation does not apply here.** A sort must conserve every item; an
   import deliberately does not. So the safety net is different: a *typed,
   bounded* change set, an explicit diff the operator approved, and the backup.
4. **Only containers, for now.** Player fields, Pal stats and technology points
   need the per-field validation schema that is Phase 7. Importing them is
   refused rather than half-validated — a world the game will not load is the
   failure mode this whole project exists to avoid.

WHAT "VALIDATED" MEANS HERE
---------------------------
Every slot in an incoming container is checked for: a known item id, a positive
integer count within the item's real stack ceiling, a slot index inside the
target container, and no duplicate slot indices. Unknown item ids are rejected
outright — an item the game does not know is a guaranteed crash, and "it might
be modded" is not worth a corrupted world.
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Optional

import gamedata
import saveexport

logger = logging.getLogger(__name__)

# Import targets that are actually implemented. Everything else is refused with
# a reason rather than silently ignored.
SUPPORTED_KINDS = ("container",)

# A single container cannot legitimately be bigger than this. Guards against a
# document that claims 10 million slots and eats memory before validation.
MAX_SLOTS = 512


class ImportError_(Exception):
    """Raised when a document cannot be imported. Named to avoid the builtin."""


class ImportRefused(ImportError_):
    """The document is well-formed but this build will not apply it."""


def _problem(slot_index: Any, message: str) -> dict:
    return {"slotIndex": slot_index, "problem": message}


def validate_container_payload(payload: Any, capacity: Optional[int] = None) -> dict:
    """
    Check an incoming container payload. Pure; returns a report.

    `capacity` is the target container's real slot count when known — an import
    that would overflow the destination is rejected before anything is planned.
    """
    problems: list[dict] = []

    if not isinstance(payload, dict):
        return {"ok": False, "problems": [_problem(None, "Payload is not an object")], "slots": []}

    slots = payload.get("slots")
    if not isinstance(slots, list):
        return {"ok": False, "problems": [_problem(None, "Payload has no slot list")], "slots": []}

    if len(slots) > MAX_SLOTS:
        return {
            "ok": False,
            "problems": [_problem(None, f"{len(slots)} slots exceeds the {MAX_SLOTS} maximum")],
            "slots": [],
        }

    seen: set[int] = set()
    cleaned: list[dict] = []

    for entry in slots:
        if not isinstance(entry, dict):
            problems.append(_problem(None, "Slot is not an object"))
            continue

        index = entry.get("slotIndex")
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            problems.append(_problem(index, "Slot index must be a non-negative integer"))
            continue
        if index in seen:
            problems.append(_problem(index, "Duplicate slot index"))
            continue
        seen.add(index)

        if capacity is not None and index >= capacity:
            problems.append(
                _problem(index, f"Slot index is outside the target container ({capacity} slots)")
            )
            continue

        item_id = entry.get("itemId") or ""
        count = entry.get("stackCount", 0)

        # An empty slot is legitimate and carries no item.
        if not item_id or entry.get("isEmpty"):
            cleaned.append({"slotIndex": index, "itemId": "", "stackCount": 0})
            continue

        if not isinstance(item_id, str):
            problems.append(_problem(index, "Item id must be a string"))
            continue
        if not gamedata.item(item_id):
            # Deliberately strict. See the module docstring.
            problems.append(_problem(index, f"Unknown item id {item_id!r}"))
            continue

        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            problems.append(_problem(index, "Stack count must be a positive integer"))
            continue

        ceiling = gamedata.max_stack(item_id)
        if ceiling and count > ceiling:
            problems.append(
                _problem(index, f"{count} exceeds the stack limit for {item_id} ({ceiling})")
            )
            continue

        cleaned.append({"slotIndex": index, "itemId": item_id, "stackCount": count})

    return {"ok": not problems, "problems": problems, "slots": cleaned}


def plan_container_import(document: dict, current_slots: list[dict]) -> dict:
    """
    Work out what applying this document would do. Pure — no writes, no I/O.

    Returns a plan the operator can read and approve, plus a `planHash` that
    `apply` uses to confirm the world has not moved underneath them.
    """
    report = saveexport.verify(document)
    if not report["ok"]:
        raise ImportError_("; ".join(report["problems"]))

    kind = report["kind"]
    if kind not in SUPPORTED_KINDS:
        extra = (
            " Pal documents ('pal' and 'player' exports) are imported by `palimport`, "
            "which routes them to the Pal editor rather than through this module."
            if kind in ("pal", "player") else
            " Player fields and technology points still need per-field validation, and "
            "importing them unvalidated is how a world stops loading."
        )
        raise ImportRefused(
            f"This module imports {', '.join(SUPPORTED_KINDS)} exports, not {kind!r}." + extra
        )

    payload = document["payload"]
    capacity = len(current_slots)
    validation = validate_container_payload(payload, capacity=capacity)
    if not validation["ok"]:
        return {
            "ok": False,
            "containerId": payload.get("containerId", ""),
            "problems": validation["problems"],
            "changes": [],
            "planHash": "",
        }

    incoming = {s["slotIndex"]: s for s in validation["slots"]}
    existing = {
        int(s.get("slotIndex", i)): s for i, s in enumerate(current_slots)
    }

    changes: list[dict] = []
    blocked: list[dict] = []
    for index in sorted(set(incoming) | set(existing)):
        before = existing.get(index)
        after = incoming.get(index)

        before_item = "" if not before or before.get("isEmpty") else (before.get("itemId") or "")
        before_count = 0 if not before or before.get("isEmpty") else int(before.get("stackCount") or 0)
        after_item = after["itemId"] if after else before_item
        after_count = after["stackCount"] if after else before_count

        if (before_item, before_count) == (after_item, after_count):
            continue

        # Equipment, eggs, anything with its own DynamicItemSaveData record.
        # Writing over one orphans that record and a replacement cannot be
        # fabricated, so the whole import is refused rather than partially
        # applied — the same conservative line the "stackables" sort takes.
        if before and before.get("hasDynamicId"):
            blocked.append(_problem(
                index,
                f"Slot holds {before_item or 'an item'} with durability or its own "
                "record. Importing over it would orphan that record.",
            ))
            continue

        changes.append({
            "slotIndex": index,
            "before": {"itemId": before_item, "itemName": gamedata.item_name(before_item) if before_item else "",
                       "stackCount": before_count},
            "after": {"itemId": after_item, "itemName": gamedata.item_name(after_item) if after_item else "",
                      "stackCount": after_count},
            "action": _describe(before_item, before_count, after_item, after_count),
        })

    if blocked:
        return {
            "ok": False,
            "containerId": payload.get("containerId", ""),
            "problems": blocked,
            "changes": [],
            "planHash": "",
        }

    plan = {
        "ok": True,
        "containerId": payload.get("containerId", ""),
        "problems": [],
        "changes": changes,
        "slotsChanged": len(changes),
        "itemsBefore": sum(
            int(s.get("stackCount") or 0) for s in current_slots if not s.get("isEmpty")
        ),
        # Derived from the diff rather than by summing the document, because a
        # document may legitimately describe only *part* of a container — the
        # slot editor sends exactly the slots being changed, and the loop above
        # leaves any index it does not mention alone. Summing the document would
        # then report the container as holding only the patched slots. For a
        # whole-container import the two are identical, since the changes cover
        # every slot that differs.
        "itemsAfter": sum(
            int(s.get("stackCount") or 0) for s in current_slots if not s.get("isEmpty")
        ) + sum(c["after"]["stackCount"] - c["before"]["stackCount"] for c in changes),
        "sourceWorldGuid": report["worldGuid"],
        "exportedAt": report["exportedAt"],
    }
    plan["planHash"] = saveexport.checksum(plan["changes"])
    return plan


def _describe(before_item: str, before_count: int, after_item: str, after_count: int) -> str:
    if not before_item and after_item:
        return "add"
    if before_item and not after_item:
        return "clear"
    if before_item != after_item:
        return "replace"
    return "increase" if after_count > before_count else "decrease"


# ─── The write path ──────────────────────────────────────


def _live_slots(entry: dict) -> list[dict]:
    """
    The container's slots as the planner sees them, read from the live tree.

    **PADDED TO `SlotNum`, and the absence of that was a second blocker on
    writing to an empty slot.** The save stores only occupied slots, so this
    returned three rows for a chest with three items in it — and
    `plan_container_import` takes `capacity = len(current_slots)`, which then
    rejected any index at or above three as "outside the target container".

    `parser.extract_containers` has padded the READ side since the empty-row
    work, so the editor showed free slots the writer then refused. Two views of
    one container disagreeing about how big it is, which is the shape of bug
    this file already documents for `SlotNum` versus the slot array.

    Padding here rather than at the call site because every caller wants the
    same answer, and the capacity check is derived from the length.
    """
    import saveedit

    slots = []
    raw_slots = ((entry.get("value") or {}).get("Slots") or {}).get("value", {}).get("values", [])
    for position, slot in enumerate(raw_slots):
        raw = saveedit._slot_raw(slot)
        if raw is None:
            continue
        item_id = saveedit._static_id(raw)
        count = saveedit._count(raw)
        slots.append({
            "slotIndex": int(raw.get("slot_index", position) or 0),
            "itemId": item_id,
            "stackCount": count,
            "isEmpty": not item_id or count <= 0,
            "hasDynamicId": saveedit._has_dynamic_id(raw),
        })

    # `SlotNum` is the real capacity; the array is only what the game bothered
    # to write. A padded row is a slot that genuinely exists and is free.
    from parser import _num

    capacity = _num(entry.get("value") or {}, "SlotNum", 0)
    present = {s["slotIndex"] for s in slots}
    for index in range(capacity):
        if index not in present:
            slots.append({
                "slotIndex": index,
                "itemId": "",
                "stackCount": 0,
                "isEmpty": True,
                "hasDynamicId": False,
            })
    slots.sort(key=lambda s: s["slotIndex"])
    return slots


def _write_slot(raw: dict, item_id: str, count: int) -> None:
    """
    Set one slot to a plain item, or clear it.

    An empty slot on disk is `static_id: ""`, `count: 0` and a zeroed
    dynamic_id — verified against the reference world rather than assumed. The
    dynamic_id is always zeroed here because this only ever writes plain
    stackable items; anything individually tracked is refused during planning.
    """
    from parser import ZERO_GUID

    item = raw.setdefault("item", {})
    item["static_id"] = item_id
    dynamic = item.setdefault("dynamic_id", {})
    dynamic["created_world_id"] = ZERO_GUID
    dynamic["local_id_in_created_world"] = ZERO_GUID
    raw["count"] = count


def apply_container_import(
    document: dict,
    expected_plan_hash: Optional[str] = None,
) -> dict:
    """
    Apply a container import. The only function here that writes.

    Order matters and is not negotiable:

    1. `guarded_save_write` proves the server is stopped and takes a verified
       backup before we are allowed to touch anything.
    2. The plan is recomputed against the **live** tree, never against the parse
       cache, which may be minutes stale.
    3. If `expected_plan_hash` does not match that fresh plan, the world moved
       after the operator approved the preview and the import is refused.
    4. After writing, the file is re-read from disk and checked: the target
       container must match the plan exactly, and every other container in the
       world must be byte-for-byte what it was. Anything else rolls back.

    Conservation deliberately does not apply — an import changes totals on
    purpose — so step 4 is the substitute, and it is stricter about scope than
    the sort's check is.
    """
    import os

    import saveedit
    from backup import guarded_save_write, restore_backup
    from savefiles import atomic_write, get_level_sav_path

    report = saveexport.verify(document)
    if not report["ok"]:
        raise ImportError_("; ".join(report["problems"]))
    if report["kind"] not in SUPPORTED_KINDS:
        raise ImportRefused(f"Importing a {report['kind']!r} export is not implemented.")

    container_id = (document.get("payload") or {}).get("containerId") or ""
    if not container_id:
        raise ImportError_("Document does not name a container")

    level_path = get_level_sav_path()
    if not level_path:
        raise ImportError_("Level.sav not found")

    from palsav.core import compress_gvas_to_sav, decompress_sav_to_gvas
    from palsav.gvas import GvasFile
    from palsav.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS
    from savefiles import read_sav_bytes

    world_dir = os.path.dirname(level_path)

    with guarded_save_write(f"import container {container_id}", world_dir) as backup:
        original = read_sav_bytes(level_path)
        if original is None:
            raise ImportError_("Could not read Level.sav")

        raw_gvas, save_type = decompress_sav_to_gvas(original)
        gvas = GvasFile.read(raw_gvas, PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES)
        containers = gvas.properties["worldSaveData"]["value"]["ItemContainerSaveData"]["value"]

        target = next(
            (e for e in containers if saveedit._container_id_of(e) == container_id), None
        )
        if target is None:
            raise ImportError_(f"Container {container_id} does not exist in this world")

        # Fingerprint every *other* container so we can prove we left them alone.
        others_before = {
            saveedit._container_id_of(e): _live_slots(e)
            for e in containers
            if saveedit._container_id_of(e) != container_id
        }

        plan = plan_container_import(document, _live_slots(target))
        if not plan["ok"]:
            raise ImportError_(
                "; ".join(p["problem"] for p in plan["problems"][:5])
            )
        if expected_plan_hash and plan["planHash"] != expected_plan_hash:
            raise ImportError_(
                "The world changed since this import was previewed — the plan no longer "
                "matches what you approved. Preview it again and re-check the changes."
            )
        if not plan["changes"]:
            raise ImportError_("Nothing to import — the container already matches this document")

        by_index = {c["slotIndex"]: c for c in plan["changes"]}
        raw_slots = ((target.get("value") or {}).get("Slots") or {}).get("value", {}).get("values", [])

        # **THE SAVE STORES ONLY OCCUPIED SLOTS, SO AN EMPTY ONE HAS NOTHING TO
        # WRITE INTO.** `extract_containers` pads the read side up to `SlotNum`
        # — which is right, the slots genuinely exist — and the planner happily
        # plans a change for a padded index. This loop then walked the raw array
        # looking for it, never found it, and the count check below refused the
        # whole import. Reported as "I can see the empty spots but cannot write
        # to them", and the refusal was correct about the array while being
        # wrong about the container.
        #
        # Writing into a free slot therefore means APPENDING an entry, which is
        # the same thing `palclone` does for a Pal and `itemclone` for a
        # durability record — and it follows the same rule: the entry is a
        # **deep copy of one the save already has**, never constructed. A slot
        # carries `CustomVersionData` and other opaque metadata whose right
        # values are whatever this save uses, so a hand-built one is a guess.
        by_position: dict[int, dict] = {}
        for position, slot in enumerate(raw_slots):
            raw = saveedit._slot_raw(slot)
            if raw is not None:
                by_position[int(raw.get("slot_index", position) or 0)] = raw

        applied = 0
        appended = 0
        for change in plan["changes"]:
            index = int(change["slotIndex"])
            raw = by_position.get(index)

            if raw is None:
                if not change["after"]["itemId"]:
                    # Clearing a slot the save never wrote is already true.
                    # Counting it keeps the completeness check meaningful
                    # instead of refusing a no-op.
                    applied += 1
                    continue
                if not raw_slots:
                    raise ImportError_(
                        f"Slot {index} is empty and this container has no slot to "
                        "copy a shape from, so a new entry cannot be created. Put "
                        "one item in the container by hand first."
                    )
                entry = copy.deepcopy(raw_slots[0])
                raw = saveedit._slot_raw(entry)
                if raw is None:
                    raise ImportError_(
                        f"Slot {index}: the template slot did not decode, so no "
                        "entry can be appended."
                    )
                raw["slot_index"] = index
                raw_slots.append(entry)
                by_position[index] = raw
                appended += 1

            _write_slot(raw, change["after"]["itemId"], change["after"]["stackCount"])
            applied += 1

        if applied != len(plan["changes"]):
            raise ImportError_(
                f"Planned {len(plan['changes'])} slot changes but only {applied} slots were "
                "found in the container — refusing to write a partial import"
            )
        if appended:
            logger.info(
                "Appended %d new slot entries to container %s", appended, container_id
            )

        encoded = compress_gvas_to_sav(gvas.write(PALWORLD_CUSTOM_PROPERTIES), save_type)
        atomic_write(level_path, encoded)
        logger.info("Imported %d slots into container %s", applied, container_id)

        try:
            verify_gvas = GvasFile.read(
                decompress_sav_to_gvas(read_sav_bytes(level_path))[0],
                PALWORLD_TYPE_HINTS,
                PALWORLD_CUSTOM_PROPERTIES,
            )
            reread = verify_gvas.properties["worldSaveData"]["value"]["ItemContainerSaveData"]["value"]

            written = next(
                (e for e in reread if saveedit._container_id_of(e) == container_id), None
            )
            if written is None:
                raise ImportError_("The container vanished from the written file")

            after = {s["slotIndex"]: s for s in _live_slots(written)}
            for change in plan["changes"]:
                actual = after.get(change["slotIndex"])
                expected_item = change["after"]["itemId"]
                expected_count = change["after"]["stackCount"]
                if actual is None:
                    raise ImportError_(f"Slot {change['slotIndex']} is missing after the write")
                if (actual["itemId"], actual["stackCount"]) != (expected_item, expected_count):
                    raise ImportError_(
                        f"Slot {change['slotIndex']} reads back as "
                        f"{actual['itemId']!r}x{actual['stackCount']} rather than "
                        f"{expected_item!r}x{expected_count}"
                    )

            others_after = {
                saveedit._container_id_of(e): _live_slots(e)
                for e in reread
                if saveedit._container_id_of(e) != container_id
            }
            if others_after != others_before:
                changed = [
                    cid for cid, slots in others_after.items()
                    if others_before.get(cid) != slots
                ]
                raise ImportError_(
                    f"The import modified {len(changed)} container(s) outside its scope: "
                    f"{', '.join(changed[:3])}"
                )
        except Exception as e:  # noqa: BLE001
            logger.error("Import verification failed, rolling back: %s", e)
            try:
                restore_backup(backup["id"], scope="world")
            except Exception as rollback_error:  # noqa: BLE001
                raise ImportError_(
                    f"Import verification FAILED and automatic rollback also failed "
                    f"({rollback_error}). Restore backup {backup['id']} manually. "
                    f"Original cause: {e}"
                ) from e
            raise ImportError_(
                f"Import verification failed and the world was rolled back to backup "
                f"{backup['id']}. Nothing was lost. Cause: {e}"
            ) from e

        return {
            "ok": True,
            "applied": True,
            "containerId": container_id,
            "slotsChanged": len(plan["changes"]),
            "itemsBefore": plan["itemsBefore"],
            "itemsAfter": plan["itemsAfter"],
            "backupId": backup["id"],
            "planHash": plan["planHash"],
            "verified": True,
        }


def summarise(plan: dict) -> str:
    """One line an operator can sanity-check before confirming."""
    if not plan.get("ok"):
        return f"{len(plan.get('problems', []))} problem(s); nothing would be applied"

    counts: dict[str, int] = {}
    for change in plan["changes"]:
        counts[change["action"]] = counts.get(change["action"], 0) + 1
    if not counts:
        return "No changes — the container already matches this document"

    parts = ", ".join(f"{n} {action}" for action, n in sorted(counts.items()))
    delta = plan["itemsAfter"] - plan["itemsBefore"]
    return f"{plan['slotsChanged']} slots ({parts}); item total {delta:+,}"
