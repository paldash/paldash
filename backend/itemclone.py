"""
Create equipment and eggs — the only code here that adds a `DynamicItemSaveData`
record.

A SEPARATE MODULE, FOR THE REASON `palclone.py` IS
--------------------------------------------------
`saveimport` deliberately writes plain stackable items only and zeroes every
`dynamic_id`; `dynamicitem` deliberately only *edits* records it found. Adding a
record is a third thing, and it changes the **shape** of the save rather than
values in it. Keeping it in its own file is what stops the risky code from being
one typo away from the safe code.

WHY THIS WAS REFUSED, AND WHY IT NO LONGER IS
----------------------------------------------
`can_create()` used to refuse, because on `refworld` a local id maps to sixteen
byte-identical records on 2,022 of 2,052 ids — and appending one where the game
wants sixteen leaves a half-registered item.

That was a fact about one file, not about the format. The same world's own server
backups — nine snapshots across a week — are `{1: n}` throughout, and `refworld`
sits at the same moment as a backup with 2,051 ids to its 2,052. It is a
processed copy and something multiplied its records.

**The count is 1, and it was observed rather than inferred**: diffing those
backups shows 2,017 new ids appearing across a week and another 245 into the live
world, every one of them a single record — eggs, armour and weapons alike. See
`scripts/diff-dynamic-items.py` and AGENTS.md.

WHAT IS COPIED, AND WHAT IS NEVER CONSTRUCTED
----------------------------------------------
Records are **deep-copied from an existing record of the same type**, exactly as
`palclone` copies a character. `CustomVersionData` has three distinct values on
one world and there is no way to know which a given save wants; `leading_bytes`,
`trailing_bytes` and a weapon's `unknown_bytes` are opaque. So no record is ever
built from scratch, and a type with no template in the world is a refusal naming
the type rather than a guess.

**That paragraph is measured on WEAPONS AND ARMOUR**, and applying it to eggs
unexamined is what produced the retraction below. An egg record has no
`CustomVersionData` and no `unknown_bytes`, and its two byte fields hold one
distinct value each across three worlds. Copying is still how an egg is made —
there is no reason to hand-build a struct when a template is free — but it
supplies *shape only*, and every field that means anything is written.

A NEW ITEM IS TWO THINGS THAT MUST AGREE
-----------------------------------------
Like a Pal, and for the same reason. The record in `DynamicItemSaveData` carries
the durability; the container slot's `item.dynamic_id.local_id_in_created_world`
points at it. Write one without the other and you get an item the game cannot
resolve, or a record nothing references.

`created_world_id` is the zero GUID on all 324 records of the live world, so it
is copied from the template rather than invented — but it is not a value this
module chooses.

EGGS: "AN EGG NEEDS A TEMPLATE OF THE SAME ITEM" WAS WRONG
-----------------------------------------------------------
This module used to refuse unless it found a record for the *same egg item*,
reasoning that `character_id` decides what hatches, the catalogue does not know
it, and cloning a `PalEgg_Dark_01` for a `PalEgg_Fire_01` request would give a
fire egg that hatches a dark Pal.

**The premise is right. The conclusion was backwards, in both directions.**

An egg record is six fields, and none of them is opaque:

    type            "egg"
    id              { static_id, local_id_in_created_world, created_world_id }
    character_id    what hatches
    object          usually empty; a whole embedded Pal when not
    leading_bytes   ONE distinct value across three worlds: 4 zero bytes
    trailing_bytes  ONE distinct value across three worlds: 28 zero bytes

Measured on refworld (30,866 eggs), the live world (180) and a 07-22 backup
(531): one keyset, one leading value, one trailing value, all zeros. So a
template supplies nothing an egg needs beyond the struct's shape.

And the old rule did not deliver what it promised. **One egg item hatches many
species** — `PalEgg_Dark_03` covers 18 on a single world, 41 items over 253
distinct (item, species) pairs — so a same-item template handed back whichever
of the eighteen that record happened to hold. It refused the case it could get
exactly right and allowed the case it got by luck.

`character_id` and `id.static_id` are now **written**, not inherited, and the
post-write verification re-reads the record and refuses if the species that came
back is not the species asked for. That is the direct check the old rule was a
proxy for.

**The `object` half of the rule stands unchanged.** An egg carrying an embedded
Pal is never a template: copying one duplicates a character wholesale — skills,
IVs, identity — which is `palclone`'s job and not something to do while adding an
item to a chest. A world with no empty-object egg at all is still a refusal.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import uuid
from typing import Any, Optional

import gamedata
import saveedit
from parser import ZERO_GUID

logger = logging.getLogger(__name__)


class ItemCloneError(Exception):
    """A creation that cannot be performed."""


# One request at a time, for the reason `palclone` takes one Pal: the
# verification below is written for a single append (arrays grew by exactly one),
# and a batch reusing it would be checking the wrong invariant.
MAX_STACK_DEFAULT = 1


def _containers(gvas: Any) -> list:
    world = gvas.properties["worldSaveData"]["value"]
    node = world.get("ItemContainerSaveData") or {}
    value = node.get("value")
    return value if isinstance(value, list) else []


def _container_id(entry: dict) -> str:
    return str(((entry.get("key") or {}).get("ID") or {}).get("value") or "").lower()


def _slots(entry: dict) -> list:
    return (((entry.get("value") or {}).get("Slots") or {}).get("value") or {}).get("values") or []


def _slot_num(entry: dict) -> int:
    try:
        return int(((entry.get("value") or {}).get("SlotNum") or {}).get("value") or 0)
    except (TypeError, ValueError):
        return 0


def _slot_index(raw: dict) -> int:
    """
    A slot's index, correctly for slot **0**.

    `raw.get("slot_index", -1) or -1` is the obvious spelling and it is wrong:
    0 is falsy, so the first slot in every container reads as -1. That turns
    "is slot 0 free?" into yes on a container whose slot 0 is full, and would
    have made the write append a SECOND entry for index 0 rather than find the
    existing one. All 18,728 slots on the reference world carry the field, so
    the default is a guard rather than a code path.
    """
    value = raw.get("slot_index")
    try:
        return int(value) if value is not None else -1
    except (TypeError, ValueError):
        return -1


def _dynamic_records(gvas: Any) -> list:
    import dynamicitem

    return dynamicitem._records(gvas.properties["worldSaveData"]["value"])


def _refuse(message: str) -> dict:
    return {"ok": False, "problems": [message], "planHash": ""}


#: The catalogue and the save disagree on what to call an egg, and both are
#: internally consistent. `gamedata` says `dynamic.type == "unknown"` — which is
#: exactly the 56 `PalEgg_*` items and nothing else, the same property
#: `saveedit`'s category sort keys its egg bucket on — while the save's own
#: record says `type: "egg"`. Translating here rather than "fixing" either side
#: keeps both readable against their own source.
_CATALOGUE_TO_RECORD = {"weapon": "weapon", "armor": "armor", "unknown": "egg"}


def _item_kind(static_id: str) -> tuple[str, dict]:
    """
    `(record type, catalogue entry)` for an item, or `("", entry)` if it has none.

    Resolved through `gamedata` so the case-insensitive lookup applies — the
    upstream ids are inconsistently capitalised and an exact match silently loses
    entries.
    """
    entry = gamedata.item(static_id) or {}
    catalogue = str((entry.get("dynamic") or {}).get("type") or "")
    return _CATALOGUE_TO_RECORD.get(catalogue, ""), entry


def _records_by_item(gvas: Any) -> dict[str, str]:
    """
    `local_id -> the item id of the slot pointing at it`, lowercased.

    A record does not name its own item, so the only way to ask "is this a
    record for a Katana" is to walk the containers and see what references it.
    """
    out: dict[str, str] = {}
    for entry in _containers(gvas):
        for slot in _slots(entry):
            raw = saveedit._slot_raw(slot)
            if raw is None:
                continue
            dynamic = (raw.get("item") or {}).get("dynamic_id") or {}
            local = str(dynamic.get("local_id_in_created_world") or "").lower()
            if local and local != ZERO_GUID:
                out[local] = saveedit._static_id(raw).lower()
    return out


def _find_template(gvas: Any, kind: str, static_id: str) -> Optional[dict]:
    """
    A record to deep-copy: same record type, preferring the same item.

    **THE SAME-ITEM RULE FOR EGGS WAS RETRACTED, AND IT WAS STRICTER THAN THE
    DATA.** This function used to demand a template of the *same egg item*, on
    the grounds that `character_id` decides what hatches and the catalogue does
    not know it — so cloning a `PalEgg_Dark_01` record for a `PalEgg_Fire_01`
    request would give a fire egg that hatches a dark Pal.

    The premise is right and the conclusion was backwards. Measured across three
    worlds (refworld 30,866 eggs, the live world 180, a 07-22 backup 531), an egg
    record is six fields and **nothing in it is opaque**:

        type            "egg"
        id              { static_id, local_id_in_created_world, created_world_id }
        character_id    what hatches
        object          usually empty; a whole embedded Pal when not
        leading_bytes   ONE distinct value on all three worlds: 4 zero bytes
        trailing_bytes  ONE distinct value on all three worlds: 28 zero bytes

    No `CustomVersionData`, no `unknown_bytes`. The module docstring's reason for
    deep-copying — those fields carry values only this save knows — is measured
    on **weapons and armour** and was carried across to eggs without being
    re-checked against one.

    And the old rule did not preserve the guarantee it claimed. One egg item
    hatches many species: `PalEgg_Dark_03` covers **18** on one world, 41 items
    over 253 distinct (item, species) pairs. So a same-item template supplied an
    *arbitrary* one of the eighteen — the rule refused the case it could get
    exactly right and permitted the case it got by luck. `character_id` is now
    written explicitly (see `plan_item_create`'s `hatches`), which is what makes
    any egg record an adequate source of shape.

    **An egg with a non-empty `object` is still never a template**, and that half
    stands: copying one duplicates a whole Pal — skills, IVs, identity — which is
    `palclone`'s job and not something to do while adding an item to a chest.
    """
    import dynamicitem

    same_type = [
        r for r in _dynamic_records(gvas)
        if str(dynamicitem._raw(r).get("type") or "") == kind
    ]
    if kind == "egg":
        # Shape only, so any egg will do — but never one carrying a Pal.
        same_type = [r for r in same_type if not (dynamicitem._raw(r).get("object") or {})]
    if not same_type:
        return None

    # Still *prefer* the same item where one exists: it costs nothing and keeps
    # the copied `id.static_id` right before it is overwritten anyway.
    by_local = _records_by_item(gvas)
    wanted = static_id.lower()
    exact = [r for r in same_type if by_local.get(dynamicitem._local_id(r)) == wanted]

    return (exact or same_type)[0]


def _canonical_character(species_id: str) -> str:
    """
    The catalogue's own spelling of a character id, prefixes intact.

    `gamedata.character()` resolves case-insensitively but hands back the entry
    rather than the key, and the key is what a save stores. Prefixes are kept:
    `BOSS_AmaterasuWolf` is a real value in a real egg record.
    """
    wanted = str(species_id or "").lower()
    for section in ("pals", "npcs"):
        for key in gamedata.load().get(section, {}):
            if key.lower() == wanted:
                return key
    return str(species_id or "")


def plan_item_create(
    gvas: Any,
    container_id: str,
    slot_index: int,
    static_id: str,
    count: int = 1,
    durability: Optional[float] = None,
    hatches: Optional[str] = None,
) -> dict:
    """
    What creating one item would do. Pure — reads the tree, writes nothing.

    Refuses rather than improvises on every branch: an unknown item, an item that
    carries no durability record (those go through `saveimport`, which is the
    safer path and should stay the default), a slot that is out of range or
    already occupied, or a type with no template to copy.

    **`hatches` is the species an egg produces, and it is a real choice.** The
    egg *item* does not determine it — `PalEgg_Dark_03` covers 18 species on one
    world — so inheriting it from whichever template was picked means handing
    back an arbitrary one. Omitting it keeps that old behaviour rather than
    breaking callers, but the value is always reported in `hatchesInto` and
    `hatchesFromTemplate` says which of the two happened.
    """
    import dynamicitem

    container_id = str(container_id or "").lower()
    kind, entry = _item_kind(static_id)

    if not entry:
        return _refuse(
            f"'{static_id}' is not an item in the game's catalogue. Search by id or "
            f"by display name — the catalogue speaks both."
        )
    if not kind:
        return _refuse(
            f"{entry.get('name') or static_id} carries no durability record, so it "
            f"does not need this path — add it through the slot editor, which is "
            f"the safer of the two."
        )
    if kind not in dynamicitem.DYNAMIC_TYPES:
        return _refuse(f"Unsupported record type '{kind}'.")

    target = next((c for c in _containers(gvas) if _container_id(c) == container_id), None)
    if target is None:
        return _refuse(f"No container with id {container_id} in this world.")

    capacity = _slot_num(target)
    if slot_index < 0 or (capacity and slot_index >= capacity):
        return _refuse(
            f"Slot {slot_index} is outside this container, which holds {capacity}."
        )

    # `SlotNum` is the capacity; the array holds only OCCUPIED slots, so an index
    # that is absent is a free slot rather than an error. See AGENTS.md.
    occupied = {}
    for slot in _slots(target):
        raw = saveedit._slot_raw(slot)
        if raw is None:
            continue
        occupied[_slot_index(raw)] = raw

    existing = occupied.get(slot_index)
    if existing is not None and not saveedit._is_empty(existing):
        return _refuse(
            f"Slot {slot_index} already holds "
            f"{gamedata.item_name(saveedit._static_id(existing))}. Clear it first — "
            f"overwriting it would orphan whatever record it points at."
        )

    template = _find_template(gvas, kind, static_id)
    if template is None:
        if kind == "egg":
            return _refuse(
                "This world holds no egg record with an empty contents field to "
                "copy the shape from. Any egg will do — the species is set "
                "explicitly rather than inherited — but one that already has a "
                "Pal inside it is never copied, because that would duplicate the "
                "Pal. Obtain any ordinary egg first."
            )
        return _refuse(
            f"This world contains no {kind} record to copy the shape from. The right "
            f"values for CustomVersionData and the opaque byte fields are whatever "
            f"this save already uses, so there is nothing to derive them from yet — "
            f"obtain one {kind} in game and try again."
        )

    max_durability = dynamicitem._max_durability(static_id)
    if durability is None:
        durability = max_durability
    if kind in dynamicitem.EDITABLE_TYPES:
        if durability < 0 or durability > dynamicitem.MAX_DURABILITY:
            return _refuse(
                f"Durability must be between 0 and {dynamicitem.MAX_DURABILITY:,.0f}."
            )

    if count < 1:
        return _refuse("Count must be at least 1.")
    if count != 1:
        # Each durability item is individually tracked, so "five swords" is five
        # records and five slots, not a stack of five. Refusing is clearer than
        # silently creating one.
        return _refuse(
            "Equipment and eggs are tracked individually, so they do not stack — "
            "create them one slot at a time."
        )

    template_raw = dynamicitem._raw(template)

    # What this egg will hatch. Validated against the character tables, which is
    # the only check available — the game ships no item->species mapping, and
    # every one of the 103 species seen on a real world's eggs resolves there.
    hatches_into = ""
    from_template = False
    if kind == "egg":
        wanted_species = str(hatches or "").strip()
        if wanted_species:
            resolved = gamedata.character(wanted_species)
            if resolved is None:
                return _refuse(
                    f"'{wanted_species}' is not a character in the game's data, so "
                    f"an egg naming it would hatch nothing. Search by species id or "
                    f"by display name."
                )
            # Canonical spelling, because the catalogue is inconsistently
            # capitalised (`Sheepball` in a save, `SheepBall` in the tables).
            #
            # **NOT `normalise_species`**, which strips the `BOSS_` prefix — an
            # alpha is a real thing for an egg to hatch, and `BOSS_AmaterasuWolf`
            # appears verbatim in real egg records. Stripping it would quietly
            # turn a request for an alpha into an ordinary Pal.
            hatches_into = _canonical_character(wanted_species)
        else:
            hatches_into = str(template_raw.get("character_id") or "")
            from_template = True

    plan = {
        "ok": True,
        "problems": [],
        "containerId": container_id,
        "slotIndex": slot_index,
        "staticId": static_id,
        "itemName": entry.get("name") or static_id,
        "icon": entry.get("icon") or "",
        "type": kind,
        "count": count,
        "durability": float(durability),
        "maxDurability": max_durability,
        # WHAT THIS EGG WILL HATCH, and now a choice rather than an accident.
        # The item does not determine it, so a caller that does not say gets the
        # template's value and is told so — `hatchesFromTemplate` is the flag
        # that stops an arbitrary species reading as a decided one.
        "hatchesInto": hatches_into,
        "hatchesFromTemplate": from_template,
        "hatchesName": gamedata.character_name(hatches_into) if hatches_into else "",
        "templateLocalId": dynamicitem._local_id(template),
        "recordsBefore": len(_dynamic_records(gvas)),
    }
    plan["planHash"] = hashlib.sha256(
        json.dumps(
            {k: plan[k] for k in
             ("containerId", "slotIndex", "staticId", "type", "count",
              "durability", "hatchesInto", "recordsBefore")},
            sort_keys=True,
        ).encode()
    ).hexdigest()[:16]
    return plan


def _new_record(template: dict, local_id: str, kind: str, plan: dict) -> dict:
    """
    A deep copy of `template` with a fresh id and the planned values.

    Deep-copied rather than constructed, for the reason `palclone._new_slot` is:
    `CustomVersionData` and the opaque byte fields carry values this save chose,
    and inventing them produces a tree that looks right and bytes that are not.
    """
    import dynamicitem

    record = copy.deepcopy(template)
    raw = dynamicitem._raw(record)

    # The id is a dict of two GUIDs. `created_world_id` is copied, never chosen —
    # it is the zero GUID on every record of the live world, but that is an
    # observation about this save rather than a rule this module should encode.
    ident = raw.setdefault("id", {})
    ident["local_id_in_created_world"] = _as_uuid(template, local_id)

    if kind in dynamicitem.EDITABLE_TYPES:
        raw["durability"] = float(plan["durability"])
    if kind == "egg":
        # **Written explicitly, not inherited.** The template supplies shape
        # only — its two byte fields are constant zeros on every world measured —
        # so the one field that decides what this egg becomes is set here.
        # `id.static_id` too: the record carries which item it is, and a template
        # of a different egg would otherwise leave the old id behind.
        raw["character_id"] = plan["hatchesInto"]
        ident["static_id"] = plan["staticId"]
    if kind == "weapon":
        raw["remaining_bullets"] = 0
        # A fresh weapon carries no passives. The template's would otherwise be
        # a silent gift of whatever the copied item happened to have.
        if isinstance(raw.get("passive_skill_list"), list):
            raw["passive_skill_list"] = []
    return record


def _as_uuid(template: dict, value: str):
    """
    `value` as whatever type this save stores GUIDs in.

    palsav decodes GUIDs as its own `UUID` class, not `str` — the same trap
    `soloexport._write_uid` documents. Writing a `str` where the encoder expects
    a UUID produces a tree that looks right and bytes that are not, so the type
    is taken from the record being copied rather than assumed.

    **`UUID(...)` is not the constructor to reach for**, and this cost a
    debugging round: `palsav.archive.UUID.__init__` takes **raw bytes**, in a
    swizzled order, and parsing a dashed string is the separate `from_str`.
    Passing the string to the constructor stores it verbatim and the failure
    surfaces much later, inside the encoder, as "a bytes-like object is
    required, not 'str'".
    """
    import dynamicitem
    from palsav.archive import UUID as PalUUID

    existing = (dynamicitem._raw(template).get("id") or {}).get("local_id_in_created_world")
    if existing is None or isinstance(existing, str):
        return value
    return PalUUID.from_str(value)


def _write_slot(raw: dict, static_id: str, count: int, local_id, created_world) -> None:
    """Point a container slot at the new record."""
    item = raw.setdefault("item", {})
    item["static_id"] = static_id
    dynamic = item.setdefault("dynamic_id", {})
    dynamic["created_world_id"] = created_world
    dynamic["local_id_in_created_world"] = local_id
    raw["count"] = count


def apply_item_create(
    container_id: str,
    slot_index: int,
    static_id: str,
    count: int = 1,
    durability: Optional[float] = None,
    expected_plan_hash: Optional[str] = None,
    hatches: Optional[str] = None,
) -> dict:
    """
    Create the item. The only function here that writes.

    Same order as every other write in this project — guard and back up, re-plan
    against the **live** tree, refuse a stale plan, write, re-read from disk and
    verify, roll back on any mismatch.

    The verification is the strict one, because this changes the save's shape:

    - `DynamicItemSaveData` must have grown by exactly **one**
    - the new local id must resolve to exactly **one** record
    - the target slot must point at that id, with the right item and count
    - **no other container may have changed length**, which catches a write that
      landed somewhere it was not asked to
    """
    import dynamicitem
    from backup import guarded_save_write, restore_backup
    from palsav.core import compress_gvas_to_sav, decompress_sav_to_gvas
    from palsav.gvas import GvasFile
    from palsav.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS
    from parser import _custom_properties
    from savefiles import atomic_write, get_level_sav_path, read_sav_bytes

    level_path = get_level_sav_path()
    if not level_path:
        raise ItemCloneError("Level.sav not found")

    world_dir = os.path.dirname(level_path)
    props = {**PALWORLD_CUSTOM_PROPERTIES, **_custom_properties(include_items=True)}

    with guarded_save_write(f"create {static_id} in {container_id}", world_dir) as backup:
        original = read_sav_bytes(level_path)
        if original is None:
            raise ItemCloneError("Could not read Level.sav")

        raw_gvas, save_type = decompress_sav_to_gvas(original)
        gvas = GvasFile.read(raw_gvas, PALWORLD_TYPE_HINTS, props)

        plan = plan_item_create(
            gvas, container_id, slot_index, static_id, count, durability, hatches
        )
        if not plan["ok"]:
            raise ItemCloneError("; ".join(plan["problems"][:3]))
        if expected_plan_hash and plan["planHash"] != expected_plan_hash:
            raise ItemCloneError(
                "The world changed since this was previewed — the plan no longer "
                "matches what you approved. Preview it again."
            )

        kind = plan["type"]
        template = _find_template(gvas, kind, static_id)
        if template is None:
            raise ItemCloneError("The template record vanished before the write.")

        records = _dynamic_records(gvas)
        records_before = len(records)
        lengths_before = {_container_id(c): len(_slots(c)) for c in _containers(gvas)}

        target = next(c for c in _containers(gvas) if _container_id(c) == container_id.lower())
        slots = _slots(target)
        if not slots:
            raise ItemCloneError(
                "That container has no existing slot to copy the entry shape from. "
                "Building one from scratch would mean guessing fields this save "
                "already knows the right values for."
            )

        new_local = str(uuid.uuid4())
        template_raw = dynamicitem._raw(template)
        created_world = (template_raw.get("id") or {}).get("created_world_id")

        record = _new_record(template, new_local, kind, plan)
        records.append(record)

        # The slot half. An index the array does not carry is a FREE slot, not a
        # missing one — `SlotNum` is the capacity and only occupied slots are
        # stored — so this appends an entry when there is nothing to overwrite,
        # copying an existing slot's shape for the same reason records are copied.
        existing_slot = next(
            (s for s in slots
             if saveedit._slot_raw(s) is not None
             and _slot_index(saveedit._slot_raw(s)) == slot_index),
            None,
        )
        if existing_slot is None:
            existing_slot = copy.deepcopy(slots[0])
            saveedit._slot_raw(existing_slot)["slot_index"] = slot_index
            slots.append(existing_slot)

        _write_slot(
            saveedit._slot_raw(existing_slot),
            static_id,
            count,
            _as_uuid(template, new_local),
            created_world,
        )

        encoded = compress_gvas_to_sav(gvas.write(PALWORLD_CUSTOM_PROPERTIES), save_type)
        atomic_write(level_path, encoded)
        logger.info("Created %s (%s) in %s slot %d", static_id, kind, container_id, slot_index)

        try:
            verify = GvasFile.read(
                decompress_sav_to_gvas(read_sav_bytes(level_path))[0],
                PALWORLD_TYPE_HINTS,
                props,
            )
            verify_records = _dynamic_records(verify)
            if len(verify_records) != records_before + 1:
                raise ItemCloneError(
                    f"DynamicItemSaveData has {len(verify_records)} records, expected "
                    f"{records_before + 1}"
                )

            index = dynamicitem.index_by_local_id(
                verify.properties["worldSaveData"]["value"]
            )
            copies = index.get(new_local.lower()) or []
            if len(copies) != 1:
                raise ItemCloneError(
                    f"The new record resolves to {len(copies)} copies, expected 1"
                )

            lengths_after = {_container_id(c): len(_slots(c)) for c in _containers(verify)}
            for cid, before in lengths_before.items():
                after = lengths_after.get(cid)
                allowed = (before, before + 1) if cid == container_id.lower() else (before,)
                if after not in allowed:
                    raise ItemCloneError(
                        f"Container {cid} has {after} slots, expected one of "
                        f"{allowed} — the write landed outside its scope"
                    )

            written = next(
                c for c in _containers(verify) if _container_id(c) == container_id.lower()
            )
            slot_raw = next(
                (saveedit._slot_raw(s) for s in _slots(written)
                 if saveedit._slot_raw(s) is not None
                 and _slot_index(saveedit._slot_raw(s)) == slot_index),
                None,
            )
            if slot_raw is None:
                raise ItemCloneError(f"Slot {slot_index} is not in the container after writing")
            if saveedit._static_id(slot_raw) != static_id:
                raise ItemCloneError(
                    f"Slot {slot_index} holds '{saveedit._static_id(slot_raw)}', "
                    f"expected '{static_id}'"
                )
            written_local = str(
                ((slot_raw.get("item") or {}).get("dynamic_id") or {})
                .get("local_id_in_created_world") or ""
            ).lower()
            if written_local != new_local.lower():
                raise ItemCloneError(
                    "The slot does not point at the new record — the item would be "
                    "half-registered"
                )

            # **THE EGG'S SPECIES, RE-READ FROM DISK.** This is the check the old
            # same-item rule was standing in for, and it is the direct one: the
            # record that came back must name the Pal that was asked for. A
            # `character_id` written as the wrong type, or dropped by the encoder,
            # produces an egg that hatches something else — and nobody finds out
            # until it hatches, which is exactly the failure the retracted rule
            # was worried about. Now it cannot leave this function.
            if kind == "egg":
                written_species = str(
                    dynamicitem._raw(copies[0]).get("character_id") or ""
                )
                if written_species != plan["hatchesInto"]:
                    raise ItemCloneError(
                        f"The new egg record hatches '{written_species}', not "
                        f"'{plan['hatchesInto']}' as planned"
                    )
        except Exception:
            logger.exception("Verification failed after creating %s; rolling back", static_id)
            restore_backup(backup["id"], reason="item creation verification failed")
            raise

    return {
        "ok": True,
        "localId": new_local,
        "containerId": container_id,
        "slotIndex": slot_index,
        "staticId": static_id,
        "itemName": plan["itemName"],
        "type": kind,
        "durability": plan["durability"],
        "hatchesInto": plan["hatchesInto"],
        "backupId": backup["id"],
    }
