"""
Pal condition: sickness, hunger, injury, sanity — and why curing is a deletion.

AN AFFLICTION IS A PROPERTY THAT EXISTS. Measured on the live world:
`HungerType` is present on 97 of 2,963 Pals, `WorkerSick` on 54,
`PhysicalHealth` on 21. A healthy Pal does not carry the field at all, so there
is no healthy value to write and `_write_property` rightly refuses to invent the
property on the 2,866 that have none.
"""

from __future__ import annotations

import os
import sys

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

import charedit       # noqa: E402
import editschema     # noqa: E402


def _obj(**extra):
    obj = {
        "NickName": {"value": "Woolly"},
        "Level": {"value": {"value": 10}},
        "Exp": {"value": 100},
        "Rank": {"value": {"value": 1}},
    }
    obj.update(extra)
    return obj


def _enum(kind, value):
    return {"value": {"type": kind, "value": f"{kind}::{value}"}}


# ─── Curing ──────────────────────────────────────────────────────


def test_curing_sickness_removes_the_property():
    obj = _obj(WorkerSick=_enum("EPalBaseCampWorkerSickType", "DepressionSprain"))
    charedit._apply_pal_change(obj, {"field": "workerSick", "after": None})
    assert "WorkerSick" not in obj


def test_curing_a_healthy_pal_is_a_no_op_not_an_error():
    """A bulk cure across a base must not fail on the healthy members of it."""
    obj = _obj()
    charedit._apply_pal_change(obj, {"field": "workerSick", "after": None})
    assert "WorkerSick" not in obj


def test_every_affliction_is_clearable():
    obj = _obj(
        WorkerSick=_enum("EPalBaseCampWorkerSickType", "Weakness"),
        PhysicalHealth=_enum("EPalStatusPhysicalHealthType", "Severe"),
        HungerType=_enum("EPalStatusHungerType", "Starvation"),
    )
    for field in charedit.PAL_CLEARABLE:
        charedit._apply_pal_change(obj, {"field": field, "after": None})
    assert not (set(charedit.PAL_CLEARABLE.values()) & set(obj))


def test_a_cure_survives_the_PLANNER_and_not_only_the_writer():
    """
    The writer handled `clear` from the day it was written and the planner did
    not, so every cure was rejected as "not a writable Pal field" before any of
    the tested code ran. Unit-testing `_apply_pal_change` directly is what hid
    it: nothing exercised the route an actual request takes.
    """
    plan = charedit.plan_pal_edit(
        _obj(WorkerSick=_enum("EPalBaseCampWorkerSickType", "DepressionSprain")),
        {"workerSick": None},
    )
    assert plan["ok"], plan["problems"]
    assert [c["field"] for c in plan["changes"]] == ["workerSick"]
    assert plan["changes"][0]["before"] == "DepressionSprain"
    assert plan["changes"][0]["after"] is None


def test_curing_a_healthy_pal_PLANS_as_no_change_rather_than_refusing():
    """
    The absent property is the target state, so the missing-property refusal
    that protects every other field has to invert here — otherwise "cure this
    base" fails on exactly its healthy members.
    """
    plan = charedit.plan_pal_edit(_obj(), {"workerSick": None})
    assert plan["ok"], plan["problems"]
    assert plan["changes"] == []


def test_an_affliction_cannot_be_INFLICTED_through_the_schema():
    """
    The asymmetry is deliberate. A dashboard's job here is to fix a base, and
    there is no verified value to write anyway.
    """
    assert editschema.PAL_FIELDS["workerSick"].check(None) is None
    assert editschema.PAL_FIELDS["workerSick"].check("Fracture") is not None


# ─── Scalars ─────────────────────────────────────────────────────


def test_sanity_writes_into_a_float_property():
    obj = _obj(SanityValue={"value": 12.5, "type": "FloatProperty"})
    charedit._apply_pal_change(obj, {"field": "sanity", "after": 100.0})
    assert obj["SanityValue"]["value"] == 100.0


def test_sanity_is_bounded_at_a_hundred():
    assert editschema.PAL_FIELDS["sanity"].check(100) is None
    assert editschema.PAL_FIELDS["sanity"].check(101) is not None
    assert editschema.PAL_FIELDS["sanity"].check(-1) is not None


def test_fullness_has_no_maximum_because_the_ceiling_is_not_stored():
    """
    Per species and per level, ranging 150 to 620 on the live world and written
    down nowhere in the save. The game clamps an overshoot itself.
    """
    assert editschema.PAL_FIELDS["fullStomach"].maximum is None
    assert editschema.PAL_FIELDS["fullStomach"].check(9999) is None


def test_an_absent_property_is_refused_rather_than_created():
    """The rule the whole module rests on, restated for the new fields."""
    with pytest.raises(charedit.EditError, match="no 'SanityValue'"):
        charedit._apply_pal_change(_obj(), {"field": "sanity", "after": 50.0})


def test_the_imported_flag_is_a_bool_not_an_int():
    assert editschema.PAL_FIELDS["isImported"].check(True) is None
    assert editschema.PAL_FIELDS["isImported"].check(1) is not None


# ─── The learned-move pool ───────────────────────────────────────


def test_learned_moves_write_where_the_property_exists():
    obj = _obj(MasteredWaza={
        "array_type": "EnumProperty",
        "value": {"values": ["EPalWazaID::SelfDestruct"]},
    })
    charedit._apply_pal_change(
        obj, {"field": "masteredSkills", "after": ["AirBlade", "SelfDestruct"]}
    )
    values = obj["MasteredWaza"]["value"]["values"]
    assert values == ["EPalWazaID::AirBlade", "EPalWazaID::SelfDestruct"]
    assert obj["MasteredWaza"]["array_type"] == "EnumProperty", "array_type must survive"


def test_learned_moves_are_still_refused_where_the_property_is_absent():
    """
    This is the half of the old blanket refusal that still stands: "absent on
    most Pals" argues against CREATING the property, not against editing the
    ones that have it.
    """
    with pytest.raises(charedit.EditError, match="no 'MasteredWaza'"):
        charedit._apply_pal_change(
            _obj(), {"field": "masteredSkills", "after": ["AirBlade"]}
        )


def test_an_unknown_learned_move_is_rejected():
    assert editschema.PAL_FIELDS["masteredSkills"].check(["NotAMove"]) is not None
    assert editschema.PAL_FIELDS["masteredSkills"].check(["AirBlade"]) is None


def test_the_editable_view_hides_what_this_pal_does_not_have():
    """An unwritable field must not render as an editable one."""
    view = charedit.read_pal(_obj())
    for absent in ("sanity", "workerSick", "masteredSkills", "skinName"):
        assert absent not in view

    rich = charedit.read_pal(_obj(
        SanityValue={"value": 42.0},
        WorkerSick=_enum("EPalBaseCampWorkerSickType", "Bulimia"),
    ))
    assert rich["sanity"] == 42.0
    assert rich["workerSick"] == "Bulimia"
