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

import copy
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
    # Condition and identity. All write into an existing shape like everything
    # else here — `_write_property` still refuses to create a property.
    "sanity": "SanityValue",
    "fullStomach": "FullStomach",
    "favoriteIndex": "FavoriteIndex",
    "skinName": "SkinName",
    "isImported": "bImportedCharacter",
}

# Fields whose fix is REMOVING the property, not writing a value.
#
# An affliction is a property that exists. Measured on the live world:
# `HungerType` is present on 97 of 2,963 Pals, `WorkerSick` on 54,
# `PhysicalHealth` on 21 — a healthy Pal does not carry the field at all. So
# there is no "healthy" enum value to write, and `_write_property` rightly
# refuses to invent a property on the 2,866 Pals that have none.
#
# Deletion is also the safe direction: the absent state is the one directly
# observed on the overwhelming majority of a real world, rather than a value
# this project guessed at. Curing is therefore always possible; *inflicting* is
# not offered, which is the right asymmetry for a dashboard whose job is to fix
# a base rather than to poison one.
PAL_CLEARABLE = {
    "workerSick": "WorkerSick",
    "physicalHealth": "PhysicalHealth",
    "hungerType": "HungerType",
}

# Schema field name -> the ArrayProperty in the save.
#
# These write a different shape from the scalars above: the values live at
# `node["value"]["values"]` and the property carries an `array_type` that must
# not change. `PassiveSkillList` is a NameProperty of bare ids; `EquipWaza` is an
# EnumProperty whose values all carry the `EPalWazaID::` prefix. The API speaks
# bare ids for both, because that is how the bundled tables are keyed, and the
# prefix is re-attached here on the way in.
PAL_LIST_PROPERTY_MAP = {
    "passiveSkills": "PassiveSkillList",
    "activeSkills": "EquipWaza",
    # The learned-move pool. Writable ONLY where it already exists, which is the
    # same rule every other property here follows — `_write_list_property`
    # refuses to create one rather than guess an `array_type`.
    #
    # This narrows an older blanket refusal rather than overturning it. The
    # reason given was that the property is absent on most Pals; that is still
    # true (present on 738 of 2,963 on the live world), but "absent on most" is
    # an argument against *creating* it, not against editing the 25% that have
    # it. Where it exists the shape is measured: array_type EnumProperty, values
    # carrying the same `EPalWazaID::` prefix `EquipWaza` uses.
    "masteredSkills": "MasteredWaza",
}

# ArrayProperties whose values are palsav `UUID` objects rather than strings.
#
# `_write_list_property` calls `str()` on everything, which here produces a tree
# that looks right and bytes that are not — the exact failure `soloexport`
# records, where an `isinstance(v, str)` test matched nothing and rewrote zero of
# 6,455 uid fields. `_write_uid_list` reconstructs the same class instead.
#
# `OldOwnerPlayerUIds` is present on 100% of Pals, so unlike every other list
# here there is no create-vs-guess problem — only the value type.
PAL_UID_LIST_PROPERTY = {
    "previousOwners": "OldOwnerPlayerUIds",
}

# Properties whose stored values carry an enum prefix the API strips.
LIST_PREFIXES = {
    "EquipWaza": editschema.WAZA_PREFIX,
    "MasteredWaza": editschema.WAZA_PREFIX,
}

# Schema field name -> an ArrayProperty of STRUCTS, which the list writer above
# cannot touch.
#
# `_write_list_property` coerces every value with `str()`. That is right for
# `EquipWaza` and wrong here: a struct written as a string still serialises and
# is silently wrong, the same family of failure as a `PassiveSkillList` rewritten
# as an EnumProperty.
#
# Writable only where the property exists, for the usual reason and one extra:
# there is no `array_type` to guess AND no struct to copy, so a Pal with no
# entries offers nothing to build a new one from.
PAL_STRUCT_MAP_PROPERTY = {
    "workRanks": "GotWorkSuitabilityAddRankList",
}

# Fields the editor refuses to touch even though the schema can describe them.
# Species and gender rewrite what a Pal *is*, which cascades into the Paldeck,
# breeding eligibility and the palbox UI. Out of scope until there is a reason.
#
# `MasteredWaza` used to be refused outright here, on the grounds that it is
# absent on most Pals. It is now editable WHERE PRESENT (738 of the live world's
# 2,963), by the ordinary rule that governs every property in this module: write
# into an existing shape, never create one. "Absent on most" was always an
# argument against creating it, not against editing the ones that have it.
PAL_READ_ONLY = ("speciesId", "gender")

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


def _read_list(obj: dict, prop: str) -> list[str]:
    """An ArrayProperty's values, with any enum prefix stripped."""
    values = _v(obj, prop, "value", "values", default=[]) or []
    prefix = LIST_PREFIXES.get(prop, "")
    out = []
    for value in values:
        text = str(value)
        out.append(text[len(prefix):] if prefix and text.startswith(prefix) else text)
    return out


def read_pal(obj: dict) -> dict:
    """The editable view of one Pal, in schema field names."""
    ivs = {}
    for field, prop in PAL_PROPERTY_MAP.items():
        if field.startswith("ivs.") and prop in obj:
            ivs[field.split(".", 1)[1]] = _num(obj, prop, 0)

    view = {
        "nickname": str(_prop(obj, "NickName", "") or ""),
        "level": _num(obj, "Level", 1),
        "exp": _num(obj, "Exp", 0),
        "rank": _num(obj, "Rank", 1) or 1,
        "ivs": ivs,
    }
    # Condition and identity, present only where the save carries them. Same
    # rule as the lists below: a property this Pal does not have cannot be
    # written, so it must not appear editable.
    for field, prop in PAL_PROPERTY_MAP.items():
        if field in ("nickname", "level", "exp", "rank") or field.startswith("ivs."):
            continue
        if prop in obj:
            value = _prop(obj, prop, None)
            view[field] = value.get("value") if isinstance(value, dict) else value
    # An affliction reads as its bare enum name, or is absent entirely — which
    # is what healthy looks like in the save.
    for field, prop in PAL_CLEARABLE.items():
        if prop in obj:
            view[field] = str(_v(obj, prop, "value", "value") or "").split("::")[-1]
    # Absent means absent, matching the scalar rule: a list this save does not
    # carry cannot be written, so it must not appear editable.
    for field, prop in {**PAL_LIST_PROPERTY_MAP, **PAL_UID_LIST_PROPERTY}.items():
        if prop in obj:
            view[field] = _read_list(obj, prop)
    # Work ranks read as `{workType: rank}` — the same shape the parser reports
    # and the same shape the writer takes, so a round trip changes nothing.
    for field, prop in PAL_STRUCT_MAP_PROPERTY.items():
        if prop in obj:
            view[field] = {
                _struct_entry_work_type(e): _num(e, "Rank", 0)
                for e in (_v(obj, prop, "value", "values") or [])
                if isinstance(e, dict)
            }
    return view


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


def _write_list_property(obj: dict, prop: str, values: list) -> None:
    """
    Replace an ArrayProperty's values in place.

    Same principle as `_write_property`: write into the shape that is already
    there. `array_type` is left exactly as found — a `PassiveSkillList` rewritten
    as an EnumProperty, or an `EquipWaza` as a NameProperty, still serialises and
    is still wrong.
    """
    if prop not in obj:
        raise EditError(
            f"This save has no {prop!r} stored, so there is no shape to write into. "
            "Creating the property would mean guessing its array type, which is how "
            "a save stops loading."
        )

    node = obj[prop]
    container = node.get("value") if isinstance(node, dict) else None
    if not isinstance(container, dict) or "values" not in container:
        raise EditError(f"{prop!r} is not in the expected array-property shape")

    prefix = LIST_PREFIXES.get(prop, "")
    container["values"] = [
        f"{prefix}{v}" if prefix and not str(v).startswith(prefix) else str(v)
        for v in values
    ]


def _write_uid_list(obj: dict, prop: str, uids: list) -> None:
    """
    Replace an ArrayProperty of GUIDs, preserving the element *type*.

    palsav decodes a GUID as its own `UUID` class, not as `str`. Writing strings
    into `OldOwnerPlayerUIds` yields a tree that reads back correctly and an
    encoder that either raises or emits wrong bytes — the same trap
    `soloexport._write_uid` exists for, one container deeper.

    The class is taken from what is already in the list where possible, so a
    save that stores these as plain strings keeps storing them as plain strings.
    """
    from palsav.archive import UUID as PalUUID

    if prop not in obj:
        raise EditError(
            f"This Pal has no {prop!r} stored, so there is no shape to write into."
        )

    node = obj[prop]
    container = node.get("value") if isinstance(node, dict) else None
    if not isinstance(container, dict) or "values" not in container:
        raise EditError(f"{prop!r} is not in the expected array-property shape")

    existing = container.get("values") or []
    # An empty list carries no example, so fall back to the class palsav uses —
    # which is what every non-empty case on every world examined has held.
    stores_strings = bool(existing) and isinstance(existing[0], str)

    container["values"] = [
        str(u) if stores_strings else PalUUID.from_str(str(u)) for u in uids
    ]


def _struct_entry_work_type(entry: dict) -> str:
    """The bare work-suitability id an entry names, e.g. `Handcraft`."""
    return str(_v(entry, "WorkSuitability", "value", "value") or "").split("::")[-1]


def find_work_rank_donor(gvas: Any, prop: str) -> Optional[dict]:
    """
    Any Pal in this save that carries `GotWorkSuitabilityAddRankList`, or None.

    **The old rule demanded the template come from the SAME Pal, and that was
    stricter than the reason for it.** The reason — never construct a shape — is
    right and unchanged. But dumped from a real world, nothing in this node is
    Pal-specific:

        array_type: "StructProperty"
        type_name:  "PalWorkSuitabilityInfo"
        id:         "00000000-0000-0000-0000-000000000000"   <- all zeros
        values:     [{WorkSuitability: EnumProperty, Rank: IntProperty}]

    No `CustomVersionData`, no instance guid, no `permission_tribe_id` — none of
    what makes `palclone` demand a same-species template. Two Pals' entries
    differ only in the enum value and the integer, both of which are overwritten.
    So the donor may be any Pal in the same save, which is the difference between
    "you can only edit a Pal that already has one" and "you can edit any Pal, as
    long as somebody on this server has ever spent a handbook".

    Returns the whole property node, deep-copied by the caller. Scans in save
    order and stops at the first hit — on the live world 738 of 2,963 Pals carry
    it, so this is a short walk in practice and it runs once per apply.
    """
    for entry in _character_entries(gvas):
        obj = _save_parameter(entry)
        if not isinstance(obj, dict) or prop not in obj:
            continue
        container = _v(obj, prop, "value")
        if not isinstance(container, dict):
            continue
        values = [e for e in (container.get("values") or []) if isinstance(e, dict)]
        if values:
            return obj[prop]
    return None


def _write_work_ranks(
    obj: dict, prop: str, ranks: dict, donor: Optional[dict] = None
) -> None:
    """
    Rewrite `GotWorkSuitabilityAddRankList` — the work ranks bought with Pal Souls.

    An ArrayProperty of `{WorkSuitability: EnumProperty, Rank: IntProperty}`, and
    the reason it needs its own writer is that `_write_list_property` calls
    `str()` on every value. A struct stringified still serialises and is silently
    wrong.

    TWO THINGS THIS DOES NOT DO, each measured rather than assumed. Across
    refworld, the live world and a 07-29 snapshot, 39 Pals carry the property:

    - **It does not construct an entry.** A new work type deep-copies an existing
      one and overwrites its two fields, which is `palclone`'s rule: the right
      `CustomVersionData` and struct metadata are whatever this save already uses.
    - **It does not invent the enum prefix.** That is taken from the template's
      own value string, so a game update that renames `EPalWorkSuitability::`
      carries through instead of producing entries the game ignores.

    **THE THIRD USED TO BE "IT DOES NOT CREATE IT", AND THAT WAS STRICTER THAN
    ITS OWN REASON.** The reason is *never construct a shape*, and it is intact —
    what changed is where the shape may come from. A work-rank node carries no
    `CustomVersionData`, no instance guid and an all-zero `id`; two Pals' entries
    differ only in the enum and the integer, and both of those get overwritten.
    So any Pal in the same save is as good a template as this one, and the rule
    the old refusal actually enforced was "you may only edit a Pal that already
    has a rank" — which is not a safety property, just a smaller feature. See
    `find_work_rank_donor`.

    That correction settles the empty-array case the same way, and it has to:
    an *absent* property carries strictly less information than a present but
    empty one, so refusing the second while accepting the first is backwards.

    Every one of those 39 Pals carries **exactly one entry**, so a multi-entry
    list is plausible but unobserved. Adding a second type is allowed — the array
    length is not the risky part, the struct shape is, and that is copied — but
    it is worth knowing that it is untested against the game.
    """
    if prop not in obj:
        if donor is None:
            raise EditError(
                f"No Pal on this server carries {prop!r}, so there is no array "
                "type or struct shape to copy. It appears the first time anyone "
                "spends a work handbook — do that on any Pal and this becomes "
                "editable for all of them."
            )
        obj[prop] = copy.deepcopy(donor)

    node = obj[prop]
    container = node.get("value") if isinstance(node, dict) else None
    if not isinstance(container, dict) or "values" not in container:
        raise EditError(f"{prop!r} is not in the expected array-property shape")

    entries = [e for e in (container.get("values") or []) if isinstance(e, dict)]
    if not entries and donor is not None:
        # Present but empty: the `array_type` here is already right, and the
        # donor supplies the one thing missing. Take only its entries so this
        # Pal's own array metadata is preserved rather than replaced.
        donated = ((donor.get("value") or {}).get("values") or []) \
            if isinstance(donor, dict) else []
        entries = [copy.deepcopy(e) for e in donated if isinstance(e, dict)]
    if not entries:
        raise EditError(
            f"{prop!r} is present but empty and no Pal on this server has an "
            "entry to copy the struct shape from. It appears the first time "
            "anyone spends a work handbook."
        )

    # The prefix as this save spells it, not as this file remembers it.
    sample = str(_v(entries[0], "WorkSuitability", "value", "value") or "")
    prefix = f"{sample.rsplit('::', 1)[0]}::" if "::" in sample else ""

    by_type = {_struct_entry_work_type(e): e for e in entries}
    template = entries[0]

    out = []
    for work_type, rank in ranks.items():
        entry = by_type.get(work_type)
        if entry is None:
            entry = copy.deepcopy(template)
            _write_property(entry, "WorkSuitability", f"{prefix}{work_type}")
        _write_property(entry, "Rank", int(rank))
        out.append(entry)

    # A work type left out of the request is dropped. That is a deletion, which
    # is the safe direction here for the same reason curing is: the absent state
    # is what 2,924 of the live world's 2,963 Pals already look like.
    container["values"] = out


def _clear_property(obj: dict, prop: str) -> None:
    """
    Remove an affliction property, which is what curing one is.

    Deleting is safe here in a way that writing would not be: the absent state
    is directly observed on 2,866 of the live world's 2,963 Pals, so this
    produces a record identical in shape to a Pal that was never afflicted.

    Idempotent on purpose. "Cure this Pal" on a healthy Pal is a no-op rather
    than an error, because a bulk cure across a base must not fail on the
    healthy members of it.
    """
    obj.pop(prop, None)


def _apply_pal_change(obj: dict, change: dict, donor: Optional[dict] = None) -> None:
    """
    Write one planned Pal change into the save tree.

    Both the single and the batch writer go through here so the scalar/list
    routing exists once — a batch that forgot lists would silently skip every
    skill edit in it.
    """
    field = change["field"]
    if field in PAL_CLEARABLE:
        _clear_property(obj, PAL_CLEARABLE[field])
    elif field in PAL_LIST_PROPERTY_MAP:
        _write_list_property(obj, PAL_LIST_PROPERTY_MAP[field], change["after"])
    elif field in PAL_UID_LIST_PROPERTY:
        _write_uid_list(obj, PAL_UID_LIST_PROPERTY[field], change["after"] or [])
    elif field in PAL_STRUCT_MAP_PROPERTY:
        _write_work_ranks(
            obj, PAL_STRUCT_MAP_PROPERTY[field], change["after"] or {}, donor
        )
    else:
        _write_property(obj, PAL_PROPERTY_MAP[field], change["after"])


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

    writable = {
        **PAL_PROPERTY_MAP, **PAL_LIST_PROPERTY_MAP,
        **PAL_CLEARABLE, **PAL_STRUCT_MAP_PROPERTY, **PAL_UID_LIST_PROPERTY,
    }
    unmapped = [f for f in changes if f not in writable]
    if unmapped:
        return {
            "ok": False,
            "problems": [{"field": f, "problem": f"{f} is not a writable Pal field"}
                         for f in unmapped],
            "changes": [], "planHash": "",
        }

    # Refuse fields whose property this particular Pal does not carry, the same
    # way `plan_player_edit` does. `_write_property` refuses to invent one — an
    # absent `Rank` on a never-condensed Pal reads as 1 through `_num`'s default,
    # so nothing upstream notices until the write is already inside
    # `guarded_save_write`. Catching it here means no pointless backup and, for a
    # batch, no discovering it 140 Pals in.
    #
    # A `clear` field is the exception, and it inverts the rule: the absent
    # property IS the target state, so "this Pal has no WorkerSick" is success
    # rather than an obstacle. Refusing here would make "cure every sick Pal at
    # this base" fail on precisely the healthy ones — the selection nobody
    # curates by hand.
    missing = [
        f for f in changes if f not in PAL_CLEARABLE and writable[f] not in obj
    ]
    if missing:
        return {
            "ok": False,
            "problems": [{
                "field": f,
                "problem": f"This Pal has no {writable[f]!r} stored, so there is no "
                           "shape to write into. It appears once the game itself sets the "
                           "value — creating it would mean guessing its type.",
            } for f in missing],
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


# ─── Bulk Pal editing ────────────────────────────────────
#
# One change set, many Pals, one backup, all-or-nothing.
#
# The batch is atomic on purpose. A partial bulk edit — 140 of 200 Pals moved,
# the rest not — is materially worse than no edit at all, because there is no
# record of where it stopped and no way to resume it. So every Pal is planned
# and validated *before* anything is written, and a single failure refuses the
# whole batch.

# A ceiling on one request. The reference world has 1,905 Pals; anything past
# this is a mistake or an attempt to make one write take minutes with the server
# down, which is its own hazard.
MAX_BULK = 2000


def plan_pal_batch(subjects: list[tuple[str, dict, dict]]) -> dict:
    """
    Validate a **per-Pal** change set against many Pals. Pure — no writes.

    `subjects` is `[(instance_id, save_parameter_object, changes), ...]`.

    This is the core both batch callers share. A bulk edit sends the same
    `changes` for every Pal; a repair sends a different one per Pal, because
    clamping an out-of-range value depends on what that Pal's value is. Keeping
    the per-Pal form as the primitive means there is one batch write path rather
    than two that have to stay in step.
    """
    if not subjects:
        return {
            "ok": False,
            "problems": [{"instanceId": None, "field": None,
                          "problem": "No Pals selected"}],
            "pals": [], "planHash": "",
        }
    if len(subjects) > MAX_BULK:
        return {
            "ok": False,
            "problems": [{"instanceId": None, "field": None,
                          "problem": f"{len(subjects)} Pals exceeds the {MAX_BULK} maximum "
                                     "for one batch"}],
            "pals": [], "planHash": "",
        }

    problems: list[dict] = []
    planned: list[dict] = []
    unchanged: list[str] = []

    for instance_id, obj, per_pal in subjects:
        plan = plan_pal_edit(obj, per_pal)
        if not plan["ok"]:
            problems.extend(
                {"instanceId": instance_id, **p} for p in plan["problems"]
            )
            continue

        if not plan["changes"]:
            # Already at the target values. Not a failure — a bulk edit over a
            # mixed selection will always contain some of these.
            unchanged.append(instance_id)
            continue

        planned.append({
            "instanceId": instance_id,
            "nickname": plan["current"]["nickname"],
            "changes": plan["changes"],
        })

    if problems:
        return {"ok": False, "problems": problems, "pals": [], "planHash": ""}

    result = {
        "ok": True,
        "problems": [],
        "pals": planned,
        "palsChanged": len(planned),
        "palsUnchanged": len(unchanged),
        "unchanged": unchanged,
        "fieldsChanged": sum(len(p["changes"]) for p in planned),
    }
    # The hash covers every Pal's diff, so a single Pal moving underneath the
    # operator invalidates the batch. That is the intent: they approved a
    # specific set of before/after pairs, not a filter.
    result["planHash"] = saveexport.checksum(result["pals"])
    return result


def spread_changes(
    instance_ids: list[str], changes: dict, auto_exp: bool = False
) -> dict[str, dict]:
    """
    One change set, repeated per Pal — the bulk-edit shape, as a per-Pal map.

    `auto_exp` is what makes a bulk *level* change work at all. The game derives
    level from total EXP on load, so setting level without EXP is silently
    undone the next time the world loads. With `auto_exp`, EXP moves to the
    minimum for the new level. It is ignored when the caller supplied `exp`
    themselves — an explicit value is never second-guessed.
    """
    per_pal = dict(changes)
    if auto_exp and "level" in changes and "exp" not in changes:
        per_pal["exp"] = editschema.exp_for_level("pal", changes["level"])
    return {i: dict(per_pal) for i in instance_ids}


def apply_pal_batch(
    edits: dict[str, dict],
    label: str = "bulk edit",
    expected_plan_hash: Optional[str] = None,
) -> dict:
    """
    Apply per-Pal change sets to many Pals in a single guarded write.

    One backup, one re-read, one verification pass covering every Pal. Any
    failure — a Pal that moved, a value that did not read back — rolls the whole
    world back, so the batch cannot land half-applied.
    """
    from backup import guarded_save_write, restore_backup
    from palsav.core import compress_gvas_to_sav, decompress_sav_to_gvas
    from palsav.gvas import GvasFile
    from palsav.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS
    from savefiles import atomic_write, get_level_sav_path, read_sav_bytes

    wanted = [i for i in dict.fromkeys(edits or {}) if i]
    if not wanted:
        raise EditError("No Pals selected")

    level_path = get_level_sav_path()
    if not level_path:
        raise EditError("Level.sav not found")

    world_dir = os.path.dirname(level_path)

    with guarded_save_write(f"{label}: {len(wanted)} Pals", world_dir) as backup:
        original = read_sav_bytes(level_path)
        if original is None:
            raise EditError("Could not read Level.sav")

        raw_gvas, save_type = decompress_sav_to_gvas(original)
        gvas = GvasFile.read(raw_gvas, PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES)

        found = _index_pals(gvas, set(wanted))
        missing = [i for i in wanted if i not in found]
        if missing:
            raise EditError(
                f"{len(missing)} of the selected Pals are not in this world "
                f"(first: {missing[0]}). Refusing to apply a partial batch."
            )

        subjects = [(i, found[i], edits[i]) for i in wanted]
        plan = plan_pal_batch(subjects)
        if not plan["ok"]:
            first = plan["problems"][0]
            raise EditError(
                f"{len(plan['problems'])} problem(s), nothing applied. First: "
                f"{first['problem']}"
                + (f" (Pal {first['instanceId']})" if first.get("instanceId") else "")
            )
        if expected_plan_hash and plan["planHash"] != expected_plan_hash:
            raise EditError(
                "One or more of these Pals changed since the batch was previewed — the "
                "plan no longer matches what you approved. Preview it again."
            )
        if not plan["pals"]:
            raise EditError("Nothing to change — every selected Pal already has those values")

        # Scanned once for the whole batch rather than per Pal: the shape is
        # save-wide, and a 200-Pal bulk edit would otherwise walk 2,963
        # characters 200 times. Looked up unconditionally — cheap, and making it
        # conditional on "does any change touch work ranks" is a second place to
        # get the field list wrong.
        donor = find_work_rank_donor(gvas, PAL_STRUCT_MAP_PROPERTY["workRanks"])
        for entry in plan["pals"]:
            obj = found[entry["instanceId"]]
            for change in entry["changes"]:
                _apply_pal_change(obj, change, donor)

        encoded = compress_gvas_to_sav(gvas.write(PALWORLD_CUSTOM_PROPERTIES), save_type)
        atomic_write(level_path, encoded)
        logger.info(
            "%s: %d Pals, %d fields", label, len(plan["pals"]), plan["fieldsChanged"]
        )

        try:
            verify_gvas = GvasFile.read(
                decompress_sav_to_gvas(read_sav_bytes(level_path))[0],
                PALWORLD_TYPE_HINTS,
                PALWORLD_CUSTOM_PROPERTIES,
            )
            written = _index_pals(verify_gvas, {e["instanceId"] for e in plan["pals"]})

            for entry in plan["pals"]:
                obj = written.get(entry["instanceId"])
                if obj is None:
                    raise EditError(f"Pal {entry['instanceId']} vanished from the written file")
                after = editschema._flatten(read_pal(obj))
                for change in entry["changes"]:
                    actual = after.get(change["field"])
                    if actual != change["after"]:
                        raise EditError(
                            f"Pal {entry['instanceId']}: {change['field']} reads back as "
                            f"{actual!r} rather than {change['after']!r} — the write did not take"
                        )
        except Exception as e:  # noqa: BLE001
            logger.error("%s verification failed, rolling back: %s", label, e)
            try:
                restore_backup(backup["id"], scope="world")
            except Exception as rollback_error:  # noqa: BLE001
                raise EditError(
                    f"Verification FAILED and automatic rollback also failed "
                    f"({rollback_error}). Restore backup {backup['id']} manually. "
                    f"Original cause: {e}"
                ) from e
            raise EditError(
                f"Verification failed and the world was rolled back to backup "
                f"{backup['id']}. Nothing was lost. Cause: {e}"
            ) from e

        return {
            "ok": True,
            "applied": True,
            "palsChanged": len(plan["pals"]),
            "palsUnchanged": plan["palsUnchanged"],
            "fieldsChanged": plan["fieldsChanged"],
            "pals": plan["pals"],
            "backupId": backup["id"],
            "planHash": plan["planHash"],
            "verified": True,
        }


def _index_pals(gvas: Any, wanted: set[str]) -> dict[str, dict]:
    """
    Instance id -> SaveParameter, for the requested Pals only.

    One walk of `CharacterSaveParameterMap` rather than one per Pal: on the
    reference world that map holds 1,905 entries, so the per-Pal scan a bulk
    edit would otherwise do is quadratic.
    """
    out: dict[str, dict] = {}
    for entry in _character_entries(gvas):
        key = entry.get("key") if isinstance(entry, dict) else None
        instance_id = str(_v(key, "InstanceId", "value", default="") or "")
        if instance_id not in wanted or instance_id in out:
            continue
        obj = _save_parameter(entry)
        if obj is None:
            continue
        if _prop(obj, "IsPlayer", False) is True:
            raise EditError(
                f"{instance_id} is a player character, not a Pal. Use the player editor."
            )
        out[instance_id] = obj
    return out


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

        donor = find_work_rank_donor(gvas, PAL_STRUCT_MAP_PROPERTY["workRanks"])
        for change in plan["changes"]:
            _apply_pal_change(obj, change, donor)

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
