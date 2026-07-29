"""
Inventory slot editing (Phase 7).

Setting one chest slot to "Wood x50" is, mechanically, an import of a container
whose contents differ from the current one by exactly that slot. So this module
**does not open a second write path into containers**. It turns a patch list
into the absolute container state the operator wants, wraps it in a normal
export envelope, and hands it to `saveimport`.

That reuse is the point rather than a shortcut. Every guarantee the importer
already earned applies unchanged and without a second implementation to keep in
step:

- unknown item ids refused, stack counts bounded by the real per-item ceiling
- slots holding a `dynamic_id` refused, because overwriting one orphans its
  `DynamicItemSaveData` record
- `guarded_save_write`: server provably stopped, verified backup, re-check
- after writing, the target container must match the plan **and every other
  container in the world must be unchanged**, or the world rolls back

THE DOCUMENT CARRIES ONLY THE PATCHED SLOTS
-------------------------------------------
`plan_container_import` leaves any slot index the document does not mention
exactly as it found it, so a partial document is a first-class thing rather than
a truncated one. Sending only the patched slots is better than sending the whole
container in two ways that matter:

- **An untouched slot cannot block the edit.** A whole-container document is
  validated in full, so one modded or unrecognised item elsewhere in the chest
  would refuse an edit that never went near it.
- **An untouched slot cannot be reverted.** Nothing is written for a slot the
  document does not name, so a stale view of the rest of the container has no
  way to undo someone else's change.

Staleness on the *patched* slot is still caught: the plan diffs against the live
tree, so a slot that moved produces a different `before`, a different `planHash`,
and a refused apply.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import gamedata
import saveexport
import saveimport

logger = logging.getLogger(__name__)

# One request cannot patch more slots than the largest legitimate container.
MAX_PATCHES = saveimport.MAX_SLOTS


class SlotEditError(Exception):
    """Raised when a slot patch cannot be turned into a plan."""


def _clean_patches(patches: Any) -> list[dict]:
    """
    Normalise the incoming patch list, or raise.

    Shape per patch: `{"slotIndex": int, "itemId": str, "stackCount": int}`.
    An empty `itemId` (or a count of 0) clears the slot — the two are the same
    operation, and accepting either avoids a UI having to know which.
    """
    if not isinstance(patches, list) or not patches:
        raise SlotEditError("No slot changes supplied")
    if len(patches) > MAX_PATCHES:
        raise SlotEditError(f"{len(patches)} patches exceeds the {MAX_PATCHES} maximum")

    seen: set[int] = set()
    cleaned: list[dict] = []
    for patch in patches:
        if not isinstance(patch, dict):
            raise SlotEditError("Each slot change must be an object")

        index = patch.get("slotIndex")
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise SlotEditError(f"Bad slot index {index!r} — must be a non-negative integer")
        if index in seen:
            raise SlotEditError(f"Slot {index} is patched twice in one request")
        seen.add(index)

        item_id = patch.get("itemId") or ""
        count = patch.get("stackCount", 0)
        if not isinstance(item_id, str):
            raise SlotEditError(f"Slot {index}: item id must be text")
        if not isinstance(count, int) or isinstance(count, bool):
            raise SlotEditError(f"Slot {index}: stack count must be a whole number")

        if not item_id or count <= 0:
            cleaned.append({"slotIndex": index, "itemId": "", "stackCount": 0})
        else:
            cleaned.append({"slotIndex": index, "itemId": item_id, "stackCount": count})

    return cleaned


def build_document(container_id: str, patches: Any, current_slots: list[dict]) -> dict:
    """
    The patched slots, as an import document. Slots not named are not included.

    `current_slots` is used only to bound the indices — the importer is given
    the same list and does the diffing itself.
    """
    if not container_id:
        raise SlotEditError("No container named")

    slots = _clean_patches(patches)

    known = {int(s.get("slotIndex", i) or 0) for i, s in enumerate(current_slots)}
    outside = [p["slotIndex"] for p in slots if p["slotIndex"] not in known]
    if outside:
        raise SlotEditError(
            f"Slot {outside[0]} is not in this container, which has "
            f"{len(current_slots)} slots"
        )

    return saveexport.envelope(
        "container",
        {"containerId": container_id, "owner": None, "slots": slots},
        # No world guid: this document is synthesised from the live world rather
        # than carried in from another one, and `verify` does not require it.
        generator="palworld-dashboard/slot-editor",
    )


def plan_slot_edit(container_id: str, patches: Any, current_slots: list[dict]) -> dict:
    """
    Dry-run a slot edit. Pure — no writes, no I/O.

    Returns the importer's plan verbatim (including its `planHash`), plus the
    patch list echoed back so a UI can show what was asked for alongside what it
    would actually do.
    """
    document = build_document(container_id, patches, current_slots)
    plan = saveimport.plan_container_import(document, current_slots)
    return {
        **plan,
        "containerId": container_id,
        "requested": document["payload"]["slots"],
        "applied": False,
    }


def apply_slot_edit(
    container_id: str,
    patches: Any,
    current_slots: list[dict],
    expected_plan_hash: Optional[str] = None,
) -> dict:
    """
    Apply a previewed slot edit.

    `current_slots` must be the same slots the preview was built from — they
    become the unpatched half of the document, and the `planHash` check inside
    the importer is what catches the case where the world has moved since.
    """
    document = build_document(container_id, patches, current_slots)
    result = saveimport.apply_container_import(document, expected_plan_hash=expected_plan_hash)
    logger.info("Slot edit applied to container %s", container_id)
    return result


def summarise(plan: dict) -> str:
    """One line an operator can sanity-check before confirming."""
    if not plan.get("ok"):
        return f"{len(plan.get('problems', []))} problem(s); nothing would be applied"
    if not plan.get("changes"):
        return "No changes — those slots already hold exactly that"

    parts = []
    for change in plan["changes"][:4]:
        after = change["after"]
        before = change["before"]
        if not after["itemId"]:
            parts.append(f"slot {change['slotIndex']}: clear {before['itemName'] or 'item'}")
        else:
            name = after["itemName"] or gamedata.item_name(after["itemId"])
            parts.append(f"slot {change['slotIndex']}: {name} ×{after['stackCount']}")

    more = len(plan["changes"]) - len(parts)
    return "; ".join(parts) + (f"; and {more} more" if more > 0 else "")
