"""
Durability records — reading and repairing equipment.

WHAT A DYNAMIC ITEM IS
----------------------
Most items are just `(static_id, count)` in a container slot. Equipment is not:
a weapon or armour piece carries a **per-instance record** in
`DynamicItemSaveData`, and the slot points at it by
`dynamic_id.local_id_in_created_world`. Durability lives on that record, not in
the slot.

Measured on the reference world: **32,446 records — 814 weapon, 766 armor,
30,866 egg.**

THREE SHAPES, NOT ONE
---------------------
They are not variations on a theme, and this is why the code below refuses to
build one from scratch:

    armor   type, id, durability, leading_bytes, trailing_bytes
    weapon  … plus remaining_bullets, passive_skill_list, unknown_bytes
    egg     type, id, leading_bytes, trailing_bytes, character_id, **object**

An egg's `object` is a whole embedded Pal. So "egg editing" is not durability
editing wearing a hat — creating one means creating a character, which is
`palclone`'s problem, not this module's. Eggs are read here and refused for
writing, deliberately and with that reason given.

Even within `weapon` the shape is not constant: 813 of 814 records carry
`unknown_bytes` and one does not, and there are two distinct `CustomVersionData`
blobs. A fabricated record would have to guess which variant this save uses.

REPAIR YES, CREATE NO — AND THE REASON IS NOT THE SHAPE
-------------------------------------------------------
Deep-copying a record of the right type solves the shape problem, the way
`palclone` does for Pals. What stopped creation is the **copy count**: one local
id maps to 16 identical records (see `index_by_local_id`), and nothing here
explains that. Appending one where sixteen are expected leaves a half-registered
item. `can_create()` returns that refusal in full.

Editing an existing record is unaffected, because every copy is found and every
copy is written.

This module does not write. It produces records and edits for `saveimport` to
apply inside `guarded_save_write`, the same way `slotedit` and `palcheck` do.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import gamedata

logger = logging.getLogger(__name__)

# The types the game gives a per-instance record. Matches what PST gates on.
DYNAMIC_TYPES = ("weapon", "armor", "egg")

# Editable through this module. `egg` is excluded because its record embeds a
# whole Pal — see the module docstring.
EDITABLE_TYPES = ("weapon", "armor")

# Durability is a float and the game clamps it itself, but a negative or absurd
# value is worth refusing before it reaches the save rather than after.
MAX_DURABILITY = 100_000.0


class DynamicItemError(Exception):
    """A durability edit that cannot be applied."""


def _records(world: dict) -> list[dict]:
    """The `DynamicItemSaveData` array, or an empty list."""
    node = world.get("DynamicItemSaveData")
    if not isinstance(node, dict):
        return []
    value = node.get("value")
    if isinstance(value, dict):
        value = value.get("values")
    return value if isinstance(value, list) else []


def _raw(record: dict) -> dict:
    return ((record.get("RawData") or {}).get("value")) or {}


def _local_id(record: dict) -> str:
    return str((_raw(record).get("id") or {}).get("local_id_in_created_world") or "").lower()


def index_by_local_id(world: dict) -> dict[str, list[dict]]:
    """
    `local_id_in_created_world` -> **every** record carrying it.

    A LIST, NOT A RECORD, AND THAT IS THE WHOLE POINT
    -------------------------------------------------
    A local id does not identify one record. Measured on the reference world:
    **32,446 records but only 2,052 distinct ids** — 2,022 ids appear exactly
    **16** times, twelve appear 6 times, one appears 5, and seventeen appear
    once. Every copy of a given id is byte-for-byte identical.

    30,866 of those records are eggs, for a world with nowhere near that many
    eggs, so this looks like the save accumulating orphaned duplicates rather
    than the game needing sixteen of everything.

    The first version of this module keyed one id to one record, and its own test
    caught the consequence immediately: `plan_durability` read one copy,
    `apply_durability` looked the id up again and mutated a *different* copy, and
    the value appeared not to change. Silently editing 1 of 16 identical records
    is worse than that — the game may read any of them.

    So every copy is returned and every copy is written. Lowercased for the same
    normalisation reason `privacy.normalise_uid` exists.
    """
    out: dict[str, list[dict]] = {}
    for record in _records(world):
        key = _local_id(record)
        if key:
            out.setdefault(key, []).append(record)
    return out


def _max_durability(static_id: str) -> float:
    """
    The item's factory-fresh durability, or 0.0 when the data does not say.

    Read through `gamedata` rather than indexed directly, so the case-insensitive
    lookup applies — the upstream ids are inconsistently capitalised and an exact
    match silently loses entries.
    """
    entry = gamedata.item(static_id) or {}
    try:
        return float((entry.get("dynamic") or {}).get("durability") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def describe(record: dict) -> dict[str, Any]:
    """One record, flattened for the API. Never returns the embedded egg Pal."""
    raw = _raw(record)
    ident = raw.get("id") or {}
    kind = str(raw.get("type") or "")
    out: dict[str, Any] = {
        "localId": str(ident.get("local_id_in_created_world") or ""),
        "staticId": str(ident.get("static_id") or ""),
        "type": kind,
        "editable": kind in EDITABLE_TYPES,
    }
    if kind in ("weapon", "armor"):
        out["durability"] = float(raw.get("durability") or 0.0)
        # What "full" means for this item, from the bundled game data.
        #
        # The record holds only the *current* value, so without this the editor
        # asks for a bare number with nothing to measure it against — 1,045 is
        # either nearly new or nearly broken depending on an item the operator is
        # expected to already know. Present for 669 of the 948 items with a
        # dynamic record; the rest are accessories, which genuinely do not wear
        # out, so 0 there is the answer and not a gap.
        out["maxDurability"] = _max_durability(out["staticId"])
        out["name"] = gamedata.item_name(out["staticId"])
        out["icon"] = (gamedata.item(out["staticId"]) or {}).get("icon", "")
    if kind == "weapon":
        out["remainingBullets"] = int(raw.get("remaining_bullets") or 0)
        out["passiveSkills"] = [str(p) for p in (raw.get("passive_skill_list") or [])]
    if kind == "egg":
        # The character itself is deliberately not surfaced: it is a full Pal
        # record, and anything that wants one should go through the Pal editor
        # rather than reach into an egg.
        out["characterId"] = str(raw.get("character_id") or "")
        out["reason"] = (
            "An egg's record embeds a whole Pal, so it is read-only here — "
            "editing what hatches is a character edit, not a durability one."
        )
    return out


def plan_durability(
    world: dict, local_id: str, durability: Optional[float] = None,
    remaining_bullets: Optional[int] = None,
) -> dict[str, Any]:
    """
    Validate a durability/ammo change against the live tree. Writes nothing.

    Returns the before/after so a caller can show it and a verifier can check it.
    """
    copies = index_by_local_id(world).get(str(local_id).lower()) or []
    if not copies:
        raise DynamicItemError(
            f"No durability record {local_id} in this world. It may belong to an "
            "item that was destroyed since the page loaded."
        )

    record = copies[0]
    raw = _raw(record)
    kind = str(raw.get("type") or "")
    if kind not in EDITABLE_TYPES:
        raise DynamicItemError(
            f"A '{kind}' record is not editable here. "
            + (describe(record).get("reason") or "")
        )

    before = {"durability": float(raw.get("durability") or 0.0)}
    after = dict(before)

    if durability is not None:
        value = float(durability)
        if value < 0 or value > MAX_DURABILITY:
            raise DynamicItemError(
                f"Durability must be between 0 and {MAX_DURABILITY:,.0f}."
            )
        after["durability"] = value

    if remaining_bullets is not None:
        if kind != "weapon":
            raise DynamicItemError("Only a weapon record carries remaining bullets.")
        count = int(remaining_bullets)
        if count < 0:
            raise DynamicItemError("Remaining bullets cannot be negative.")
        before["remainingBullets"] = int(raw.get("remaining_bullets") or 0)
        after["remainingBullets"] = count

    return {
        "localId": str(local_id),
        "staticId": str((raw.get("id") or {}).get("static_id") or ""),
        "type": kind,
        "before": before,
        "after": after,
        "changed": before != after,
        # Surfaced so the caller can show it and the verifier can require it.
        # A partial write across these is the failure mode this module exists to
        # avoid.
        "copies": len(copies),
    }


def apply_durability(world: dict, plan: dict[str, Any]) -> None:
    """
    Write one planned change into the tree in place.

    Deliberately takes a *plan* rather than raw values: the validation lives in
    `plan_durability`, and a second entry point that skipped it is how an
    unchecked value reaches a save.
    """
    copies = index_by_local_id(world).get(str(plan["localId"]).lower()) or []
    if not copies:
        raise DynamicItemError(f"Record {plan['localId']} vanished before the write.")
    if len(copies) != plan.get("copies", len(copies)):
        raise DynamicItemError(
            f"Record {plan['localId']} had {plan.get('copies')} copies when planned "
            f"and {len(copies)} now — the world moved. Re-plan rather than write."
        )

    # Every copy, not the first. See `index_by_local_id`.
    after = plan["after"]
    for record in copies:
        raw = _raw(record)
        if "durability" in after:
            raw["durability"] = float(after["durability"])
        if "remainingBullets" in after:
            raw["remaining_bullets"] = int(after["remainingBullets"])


def can_create() -> tuple[bool, str]:
    """
    Whether this module will create a new durability record. It will not.

    NOT "cannot be done" — "not yet understood well enough to do safely"
    ----------------------------------------------------------------------
    Deep-copying an existing record of the right type solves the *shape* problem
    (see the module docstring), and that much was built and worked. What stopped
    it is what the copy count turned up: a local id maps to **16 identical
    records**, not one, on 2,022 of the reference world's 2,052 ids — while 17
    ids appear exactly once and thirteen others appear 5 or 6 times.

    Nothing here explains that distribution. Appending one record where the game
    expects sixteen, or sixteen where it expects one, produces an item that is
    half-registered — and a half-registered item is exactly the failure this
    project refuses to risk with a guess.

    Editing an *existing* record does not have that problem: every copy is found
    and every copy is written, so whatever the count means, they stay consistent.
    That is why repair works and creation does not.

    PST fabricates records (`generate_dynamic_item_uuid`) and so is more capable
    here. Reproducing that means first understanding the 16× pattern, most likely
    by making one weapon in game and diffing the save before and after.
    """
    return False, (
        "Creating equipment is not supported: a durability record appears 16 "
        "times in the save and nothing yet explains that, so a new one cannot be "
        "written without guessing how many copies the game expects. Existing "
        "equipment can be repaired."
    )


def count(world: dict) -> int:
    """How many records exist. Used by verification to prove nothing else moved."""
    return len(_records(world))
