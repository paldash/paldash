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


# ─── Ordering ────────────────────────────────────────────

ORDERS = ("id", "category")


def _order_key(order: str):
    """
    The sort key for one item id, for the requested ordering.

    `id` is alphabetical on the internal id — stable, needs no game data, and is
    what every sort did before this existed. It is also close to useless to read:
    `Cake`, `Charcoal`, `CommonShield`, `Coal` sit together because their *ids*
    do, not because anything about them relates.

    `category` groups by the game's own `typeA` (Material, Weapon, Consume, …)
    and orders within a group by `sortId` — the field Palworld itself sorts
    inventories with, so a sorted chest matches the order the game shows in the
    player's own inventory rather than an order only this dashboard uses.

    **Pal eggs get their own group, because the game's own table does not.** All
    56 `PalEgg_*` items are `typeA: "Material"`, so grouping strictly by category
    files a Jormuntide egg between Coal and Wood — every egg scattered through the
    ore. They are the one thing in a chest that is not a commodity: each holds a
    *distinct Pal*, and a player looking for one is not looking for a material.

    Identified by `dynamic.type == "unknown"`, which is **exactly** those 56 items
    and nothing else — a property of the data rather than a hand-written id list
    or a `PalEgg_` prefix rule that a renamed asset would silently break.

    They are never merged either, but that already held for a different reason:
    each egg carries a `dynamic_id` and `_sort_container` refuses to pool those.
    This is about where they end up, not about losing one.

    **Four buckets, and the third is the subtle one.** 653 of the 2,466 items
    carry an empty `typeA` — key items, schematics — but they still carry a
    `sortId`, so they can be ordered the way the game orders them even with no
    category to group under. Lumping them in with genuinely unknown ids would
    throw that ordering away.

    Unknown ids sort last of all, alphabetically. They are modded or
    newer-than-the-bundle items, and interleaving them at `sortId` 0 would put
    them ahead of everything — the most confusing possible place for the items
    the dashboard understands least. `gamedata` lookups are case-insensitive, so
    the save's inconsistent capitalisation resolves here as it does everywhere.
    """
    if order == "id":
        return lambda item_id: (item_id,)

    import gamedata

    # Sort after every real category name, and after each other, so the buckets
    # stay in the intended order without depending on what the game happens to
    # have named a category.
    EGGS = "￿0"
    UNCATEGORISED = "￿1"
    UNKNOWN = "￿2"

    def key(item_id: str) -> tuple[str, int, str]:
        entry = gamedata.item(item_id)
        if not entry:
            return (UNKNOWN, 0, item_id)
        if (entry.get("dynamic") or {}).get("type") == "unknown":
            return (EGGS, int(entry.get("sortId") or 0), item_id)
        return (
            str(entry.get("typeA") or "") or UNCATEGORISED,
            int(entry.get("sortId") or 0),
            item_id,
        )

    return key


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
    The merge ceiling per item: the larger of what the game allows and what the
    save already contains.

    Two sources, and taking the max of them is deliberate rather than lazy:

    - **Observed** — the largest stack of that item anywhere in the world. This
      was the only source before, on the reasoning that a stack size the save
      already contains is certainly achievable.
    - **Authoritative** — `maxStack` from the bundled game database. Bundled in
      Phase 1 but deliberately left unwired, because changing the ceiling
      changes what a sort *writes*.

    Taking `max()` cannot make a sort need more slots than it already needed,
    which is the property that matters. Raising the ceiling only ever merges
    more items into fewer slots. Lowering it is what would force a container to
    grow, and that cannot happen here: when the real cap is *below* something
    the save already holds — an older cap, or a modded stack — the observed
    value wins and that stack is preserved as-is rather than being split up.

    Items absent from the database (mod content, renamed assets) simply fall
    back to observed, which is exactly the previous behaviour.
    """
    import gamedata

    limits: dict[str, int] = defaultdict(int)
    for entry in containers:
        for slot in ((entry.get("value") or {}).get("Slots") or {}).get("value", {}).get("values", []):
            raw = _slot_raw(slot)
            if raw and not _is_empty(raw):
                item = _static_id(raw)
                limits[item] = max(limits[item], _count(raw))

    for item_id in list(limits):
        authoritative = gamedata.max_stack(item_id)
        if authoritative > 0:
            limits[item_id] = max(limits[item_id], authoritative)

    return dict(limits)


# ─── The sort itself ─────────────────────────────────────


def _sort_container(
    entry: dict,
    mode: str,
    merge: bool,
    max_stacks: dict[str, int],
    # Defaults to the id ordering — the behaviour that predates `order` — so a
    # caller that has not been taught about arrangement cannot accidentally
    # rewrite a world in a new layout.
    order_key=_order_key("id"),
) -> int:
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

    # Ordering first, then largest stack first within an item. The second half is
    # not cosmetic: it puts the partial stack of a merged item at the end of its
    # run, which is where someone reaching into a chest expects the odd remainder.
    payloads.sort(
        key=lambda p: (
            order_key(_static_id({"item": p["item"], "count": p["count"]})),
            -p["count"],
        )
    )

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


def _container_id_of(entry: dict) -> str:
    return str((((entry.get("key") or {}).get("ID") or {}).get("value")) or "")


def sort_containers(
    mode: str = "stackables",
    merge: bool = True,
    base_id: Optional[str] = None,
    order: str = "id",
) -> dict[str, Any]:
    """
    Sort and optionally merge item containers.

    mode:
      "stackables" — skip anything with a dynamic_id (weapons, armour, tools)
      "all"        — also relocate durability items, carrying their links along

    order:
      "id"       — alphabetical on the internal id (what this always did)
      "category" — grouped by the game's own item category, in the game's order

    `order` is separate from `mode` because they answer different questions:
    `mode` is about what is *safe* to move and maps to a capability, `order` is
    only about what the result looks like. Folding them into one enum would have
    made "sort by category" imply permission to relocate durability items.

    base_id scopes the sort to the containers a single base owns. Everything
    else in the world is left untouched, which is what makes this usable on a
    shared server: one guild can tidy its own base without reorganising
    everyone else's chests.

    Scoping narrows what is *written*, never what is *checked*. The conservation
    fingerprint is still taken over every container in the world, so an
    out-of-scope container that changed would fail the check just as loudly as
    an in-scope one.
    """
    if mode not in ("stackables", "all"):
        raise SaveEditError(f"Unknown sort mode: {mode}")
    if order not in ORDERS:
        raise SaveEditError(f"Unknown sort order: {order}")
    order_key = _order_key(order)

    level_path = get_level_sav_path()
    if not level_path:
        raise SaveEditError("Level.sav not found")

    world_dir = os.path.dirname(level_path)

    # The safety gate comes before EVERYTHING — the palsav import included.
    # The import is harmless in itself, but its old position made "the gate
    # is reached before anything" true only on machines where palsav was
    # installed: on CI (which deliberately has no palsav) the import raised
    # first, and the test asserting the ordering failed for the wrong reason.
    safety.assert_writable()

    from palsav.core import compress_gvas_to_sav, decompress_sav_to_gvas
    from palsav.gvas import GvasFile
    from palsav.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS
    from savefiles import read_sav_bytes

    # assert_writable (again, inside the guard) + full backup; raises unless
    # provably safe — the pre-check above does not replace the guard's own.
    scope_note = f", base {base_id}" if base_id else ""
    with guarded_save_write(
        f"sort containers ({mode}, by {order}{scope_note})", world_dir
    ) as backup:
        original = read_sav_bytes(level_path)
        if original is None:
            raise SaveEditError("Could not read Level.sav")

        raw_gvas, save_type = decompress_sav_to_gvas(original)
        gvas = GvasFile.read(raw_gvas, PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES)
        containers = gvas.properties["worldSaveData"]["value"]["ItemContainerSaveData"]["value"]

        before = _totals(containers)
        max_stacks = _max_stacks(containers)

        # Resolve the scope from the same parse tree we are about to mutate, so
        # the ownership map cannot be stale relative to the containers.
        in_scope: Optional[set[str]] = None
        base_label = ""
        if base_id:
            import parser as save_parser

            ownership = save_parser.extract_container_ownership(gvas)
            in_scope = {
                cid for cid, owner in ownership.items() if owner["baseCampId"] == base_id
            }
            if not in_scope:
                raise SaveEditError(
                    f"Base {base_id} owns no item containers — nothing to sort. "
                    "(A base with only production and defences has no storage.)"
                )
            base_label = f" for base {base_id}"

        changed_slots = 0
        touched = 0
        for entry in containers:
            if in_scope is not None and _container_id_of(entry) not in in_scope:
                continue
            delta = _sort_container(entry, mode, merge, max_stacks, order_key)
            if delta:
                touched += 1
                changed_slots += delta

        if not changed_slots:
            raise SaveEditError(f"Nothing to sort{base_label} — containers are already tidy")

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
            try:
                restore_backup(backup["id"], scope="world")
            except Exception as rollback_error:  # noqa: BLE001
                raise SaveEditError(
                    f"Write verification FAILED and automatic rollback also failed "
                    f"({rollback_error}). Restore backup {backup['id']} manually. "
                    f"Original cause: {e}"
                ) from e
            raise SaveEditError(
                f"Write verification failed and the world was rolled back to backup "
                f"{backup['id']}. Nothing was lost. Cause: {e}"
            ) from e

        return {
            "ok": True,
            "mode": mode,
            "order": order,
            "merged": merge,
            "baseId": base_id or "",
            "scope": "base" if base_id else "world",
            "containersInScope": len(in_scope) if in_scope is not None else len(containers),
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
