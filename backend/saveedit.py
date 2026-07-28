"""
Save mutation: container sorting, with the guard rails that make it survivable.

Every write follows the same pipeline:

    assert writable  ->  full backup  ->  mutate in memory
    ->  conservation check  ->  atomic write  ->  RE-READ FROM DISK
    ->  conservation check again  ->  rollback on any mismatch

The invariant that makes this safe is conservation: sorting a container may
reorder and merge stacks, but the total quantity of every item in that container
must be identical afterwards. If a single count is off, the write is rejected and
the backup is restored automatically. A sort that loses items fails loudly rather
than silently eating someone's ore.

Two things are deliberately conservative:

  * Empty slots are never fabricated. Clearing a slot reuses the exact byte
    representation of an empty slot already present in the same container, so we
    never guess at what "empty" looks like to the game.
  * Items carrying a dynamic_id (weapons, armour, tools — anything with
    durability) point into DynamicItemSaveData. In `stackables` mode they are
    left exactly where they are.
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from typing import Any, Optional

import safety
from backup import guarded_save_write, restore_backup
from savefiles import atomic_write, get_level_sav_path

logger = logging.getLogger(__name__)

ZERO_GUID = "00000000-0000-0000-0000-000000000000"


class SaveEditError(Exception):
    """Raised when an edit is rejected, failed, or was rolled back."""


# ─── Slot helpers ────────────────────────────────────────


def _slot_raw(slot: dict) -> Optional[dict]:
    raw = ((slot or {}).get("RawData") or {}).get("value")
    return raw if isinstance(raw, dict) else None


def _static_id(raw: dict) -> str:
    return str(((raw.get("item") or {}).get("static_id")) or "")


def _count(raw: dict) -> int:
    try:
        return int(raw.get("count") or 0)
    except (TypeError, ValueError):
        return 0


def _has_dynamic_id(raw: dict) -> bool:
    """True for durability-bearing items linked into DynamicItemSaveData."""
    dynamic = (raw.get("item") or {}).get("dynamic_id") or {}
    local = str(dynamic.get("local_id_in_created_world") or ZERO_GUID)
    return local != ZERO_GUID


def _is_empty(raw: dict) -> bool:
    return not _static_id(raw) or _count(raw) <= 0


def _totals(containers: list) -> dict[str, dict[str, int]]:
    """container id -> {item id -> total quantity}. The conservation fingerprint."""
    result: dict[str, dict[str, int]] = {}
    for entry in containers:
        container_id = str((((entry.get("key") or {}).get("ID") or {}).get("value")) or "")
        counts: dict[str, int] = defaultdict(int)
        for slot in ((entry.get("value") or {}).get("Slots") or {}).get("value", {}).get("values", []):
            raw = _slot_raw(slot)
            if raw and not _is_empty(raw):
                counts[_static_id(raw)] += _count(raw)
        result[container_id] = dict(counts)
    return result


def _max_stacks(containers: list) -> dict[str, int]:
    """
    Largest stack observed per item across the whole world.

    Used as the merge ceiling. The game's real per-item stack limits are not in
    the save, so rather than guessing we never build a stack larger than one the
    save already contains — if 9999 Wood exists somewhere, 9999 is achievable.
    """
    limits: dict[str, int] = defaultdict(int)
    for entry in containers:
        for slot in ((entry.get("value") or {}).get("Slots") or {}).get("value", {}).get("values", []):
            raw = _slot_raw(slot)
            if raw and not _is_empty(raw):
                item = _static_id(raw)
                limits[item] = max(limits[item], _count(raw))
    return dict(limits)


# ─── The sort itself ─────────────────────────────────────


def _sort_container(entry: dict, mode: str, merge: bool, max_stacks: dict[str, int]) -> int:
    """
    Reorder one container in place. Returns the number of slots changed.

    Movable slots are permuted among the positions they already occupy, so the
    set of occupied slots never grows. Merging frees slots, and a freed slot is
    cleared by copying an empty slot that already exists in this container.
    """
    slots = ((entry.get("value") or {}).get("Slots") or {}).get("value", {}).get("values", [])
    if not slots:
        return 0

    movable_positions: list[int] = []
    payloads: list[dict] = []
    empty_template: Optional[dict] = None

    for index, slot in enumerate(slots):
        raw = _slot_raw(slot)
        if raw is None:
            continue
        if _is_empty(raw):
            if empty_template is None:
                empty_template = {
                    "item": _copy_item(raw),
                    "count": _count(raw),
                }
            continue
        if mode == "stackables" and _has_dynamic_id(raw):
            continue  # leave equipment exactly where it is
        movable_positions.append(index)
        payloads.append({"item": _copy_item(raw), "count": _count(raw)})

    if len(movable_positions) < 2 and not merge:
        return 0

    # Merge partial stacks of identical plain items.
    if merge and empty_template is not None:
        merged: list[dict] = []
        pooled: dict[str, int] = defaultdict(int)
        for payload in payloads:
            raw_like = {"item": payload["item"], "count": payload["count"]}
            if _has_dynamic_id(raw_like):
                merged.append(payload)  # never pool durability items
            else:
                pooled[_static_id(raw_like)] += payload["count"]

        for item_id, total in pooled.items():
            ceiling = max(1, max_stacks.get(item_id, total))
            template = next(
                p for p in payloads if _static_id({"item": p["item"], "count": p["count"]}) == item_id
            )
            while total > 0:
                take = min(total, ceiling)
                merged.append({"item": _copy_item({"item": template["item"]}), "count": take})
                total -= take
        payloads = merged

    if len(payloads) > len(movable_positions):
        # Merging should only ever reduce slot usage; refuse if it somehow grew.
        raise SaveEditError(
            "Sort would need more slots than are available — refusing to modify this container"
        )

    payloads.sort(key=lambda p: (_static_id({"item": p["item"], "count": p["count"]}), -p["count"]))

    changed = 0
    for position, payload in zip(movable_positions, payloads):
        raw = _slot_raw(slots[position])
        if raw is None:
            continue
        if _static_id(raw) != _static_id({"item": payload["item"]}) or _count(raw) != payload["count"]:
            changed += 1
        raw["item"] = payload["item"]
        raw["count"] = payload["count"]

    # Clear any positions freed by merging.
    for position in movable_positions[len(payloads):]:
        raw = _slot_raw(slots[position])
        if raw is None or empty_template is None:
            continue
        if not _is_empty(raw):
            changed += 1
        raw["item"] = _copy_item(empty_template)
        raw["count"] = empty_template["count"]

    return changed


def _copy_item(source: dict) -> dict:
    """Deep-ish copy of a slot's item descriptor, preserving dynamic_id links."""
    import copy

    return copy.deepcopy(source.get("item") if "item" in source else source)


# ─── Public entry point ──────────────────────────────────


def sort_containers(mode: str = "stackables", merge: bool = True) -> dict[str, Any]:
    """
    Sort and optionally merge every item container in the world.

    mode:
      "stackables" — skip anything with a dynamic_id (weapons, armour, tools)
      "all"        — also relocate durability items, carrying their links along
    """
    if mode not in ("stackables", "all"):
        raise SaveEditError(f"Unknown sort mode: {mode}")

    level_path = get_level_sav_path()
    if not level_path:
        raise SaveEditError("Level.sav not found")

    world_dir = os.path.dirname(level_path)

    from palsav.core import compress_gvas_to_sav, decompress_sav_to_gvas
    from palsav.gvas import GvasFile
    from palsav.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS
    from savefiles import read_sav_bytes

    # assert_writable + full backup; raises unless provably safe.
    with guarded_save_write(f"sort containers ({mode})", world_dir) as backup:
        original = read_sav_bytes(level_path)
        if original is None:
            raise SaveEditError("Could not read Level.sav")

        raw_gvas, save_type = decompress_sav_to_gvas(original)
        gvas = GvasFile.read(raw_gvas, PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES)
        containers = gvas.properties["worldSaveData"]["value"]["ItemContainerSaveData"]["value"]

        before = _totals(containers)
        max_stacks = _max_stacks(containers)

        changed_slots = 0
        touched = 0
        for entry in containers:
            delta = _sort_container(entry, mode, merge, max_stacks)
            if delta:
                touched += 1
                changed_slots += delta

        if not changed_slots:
            raise SaveEditError("Nothing to sort — containers are already tidy")

        # Conservation, in memory.
        after = _totals(containers)
        _assert_conserved(before, after, "in memory")

        encoded = compress_gvas_to_sav(gvas.write(PALWORLD_CUSTOM_PROPERTIES), save_type)
        atomic_write(level_path, encoded)
        logger.info("Wrote sorted Level.sav (%d slots across %d containers)", changed_slots, touched)

        # Conservation, re-read from disk. This is the check that catches an
        # encoder bug rather than trusting the in-memory tree.
        try:
            verify_bytes = read_sav_bytes(level_path)
            verify_gvas = GvasFile.read(
                decompress_sav_to_gvas(verify_bytes)[0],
                PALWORLD_TYPE_HINTS,
                PALWORLD_CUSTOM_PROPERTIES,
            )
            reread = _totals(
                verify_gvas.properties["worldSaveData"]["value"]["ItemContainerSaveData"]["value"]
            )
            _assert_conserved(before, reread, "after re-reading from disk")
        except Exception as e:  # noqa: BLE001
            logger.error("Verification failed, rolling back: %s", e)
            if restore_backup(backup["id"]):
                raise SaveEditError(
                    f"Write verification failed and the world was rolled back to backup "
                    f"{backup['id']}. Nothing was lost. Cause: {e}"
                ) from e
            raise SaveEditError(
                f"Write verification FAILED and automatic rollback also failed. "
                f"Restore backup {backup['id']} manually. Cause: {e}"
            ) from e

        return {
            "ok": True,
            "mode": mode,
            "merged": merge,
            "containersTouched": touched,
            "slotsChanged": changed_slots,
            "backupId": backup["id"],
            "verified": True,
        }


def _assert_conserved(before: dict, after: dict, stage: str) -> None:
    """Every item total in every container must be unchanged."""
    for container_id, expected in before.items():
        actual = after.get(container_id, {})
        if expected != actual:
            missing = {
                k: (expected.get(k, 0), actual.get(k, 0))
                for k in set(expected) | set(actual)
                if expected.get(k, 0) != actual.get(k, 0)
            }
            raise SaveEditError(
                f"Item conservation check failed {stage} in container {container_id}: {missing}"
            )
