"""
Pal editor (Phase 7).

The interesting failure here is not a crash — it is a write that serialises
fine, loads fine, and silently did nothing because it went in at the wrong
nesting depth. Most of these tests are about that.
"""

from __future__ import annotations

import pytest

import charedit


def byte_prop(value):
    """Level and Talent_* are ByteProperty: one level deeper than Int."""
    return {"value": {"type": "None", "value": value}}


def int_prop(value):
    return {"value": value}


def pal_object(level=10, exp=None, rank=1, hp=50, shot=60, defense=70, nickname="Fluffy"):
    import gamedata

    if exp is None:
        exp = int(gamedata.load()["palExpTable"][str(level)]["PalTotalEXP"])

    return {
        "NickName": int_prop(nickname),
        "Level": byte_prop(level),
        "Exp": int_prop(exp),
        "Rank": byte_prop(rank),
        "Talent_HP": byte_prop(hp),
        "Talent_Shot": byte_prop(shot),
        "Talent_Defense": byte_prop(defense),
    }


# ─── Reading ─────────────────────────────────────────────────────


def test_reads_both_property_shapes():
    obj = pal_object(level=25, rank=3, hp=90)
    view = charedit.read_pal(obj)

    assert view["level"] == 25       # ByteProperty, nested
    assert view["rank"] == 3
    assert view["ivs"]["hp"] == 90
    assert view["nickname"] == "Fluffy"


def test_absent_ivs_are_simply_absent():
    obj = pal_object()
    del obj["Talent_Shot"]
    assert "shot" not in charedit.read_pal(obj)["ivs"]


# ─── Writing into the right shape ────────────────────────────────


def test_writing_a_byte_property_goes_one_level_deeper():
    """
    The bug this guards: writing to `node['value']` on a ByteProperty replaces
    the inner dict with a bare int. It still serialises, still loads, and the
    edit is silently ignored.
    """
    obj = pal_object(level=10)
    charedit._write_property(obj, "Level", 42)

    assert obj["Level"] == {"value": {"type": "None", "value": 42}}
    assert charedit.read_pal(obj)["level"] == 42


def test_writing_an_int_property_stays_at_the_top_level():
    obj = pal_object()
    charedit._write_property(obj, "Exp", 999)

    assert obj["Exp"] == {"value": 999}


def test_writing_an_absent_property_is_refused():
    """Inventing a property means guessing its type tag."""
    obj = pal_object()
    del obj["Talent_HP"]

    with pytest.raises(charedit.EditError, match="no 'Talent_HP' stored"):
        charedit._write_property(obj, "Talent_HP", 100)


# ─── Planning ────────────────────────────────────────────────────


def test_a_valid_edit_plans_cleanly():
    obj = pal_object(rank=1, hp=50)
    plan = charedit.plan_pal_edit(obj, {"rank": 4, "ivs.hp": 95})

    assert plan["ok"], plan["problems"]
    assert plan["fieldsChanged"] == 2
    assert plan["crossFieldChecked"] is True
    assert plan["planHash"]


def test_unchanged_fields_are_not_in_the_plan():
    obj = pal_object(rank=3)
    plan = charedit.plan_pal_edit(obj, {"rank": 3})

    assert plan["ok"]
    assert plan["fieldsChanged"] == 0


def test_out_of_range_values_are_refused():
    obj = pal_object()
    assert not charedit.plan_pal_edit(obj, {"rank": 9})["ok"]
    assert not charedit.plan_pal_edit(obj, {"ivs.hp": 500})["ok"]
    assert not charedit.plan_pal_edit(obj, {"level": 81})["ok"]


def test_level_without_matching_exp_is_refused():
    """The cross-field rule, reached through the editor rather than directly."""
    obj = pal_object(level=1, exp=0)
    plan = charedit.plan_pal_edit(obj, {"level": 50})

    assert not plan["ok"]
    assert any("recalculate" in p["problem"] for p in plan["problems"])


def test_level_with_matching_exp_is_accepted():
    import gamedata

    obj = pal_object(level=1, exp=0)
    target = int(gamedata.load()["palExpTable"]["50"]["PalTotalEXP"])
    plan = charedit.plan_pal_edit(obj, {"level": 50, "exp": target})

    assert plan["ok"], plan["problems"]


@pytest.mark.parametrize("field", charedit.PAL_READ_ONLY)
def test_identity_fields_are_refused(field):
    """Species, gender and passives change what the Pal *is*."""
    plan = charedit.plan_pal_edit(pal_object(), {field: "anything"})

    assert not plan["ok"]
    assert plan["changes"] == []


def test_melee_iv_is_not_writable():
    """It is not a 1.0 field; the property map must not carry it."""
    assert "ivs.melee" not in charedit.PAL_PROPERTY_MAP
    assert not charedit.plan_pal_edit(pal_object(), {"ivs.melee": 50})["ok"]


def test_unknown_fields_are_refused():
    assert not charedit.plan_pal_edit(pal_object(), {"isShiny": True})["ok"]


def test_the_plan_hash_tracks_the_effect():
    obj = pal_object(rank=1)
    first = charedit.plan_pal_edit(obj, {"rank": 4})
    same = charedit.plan_pal_edit(obj, {"rank": 4})
    other = charedit.plan_pal_edit(obj, {"rank": 5})

    assert first["planHash"] == same["planHash"]
    assert first["planHash"] != other["planHash"]


def test_the_plan_shows_before_and_after():
    obj = pal_object(rank=1)
    change = charedit.plan_pal_edit(obj, {"rank": 4})["changes"][0]

    assert change["before"] == 1
    assert change["after"] == 4
    assert change["label"] == "Condenser rank"


def test_applying_a_plan_by_hand_round_trips():
    """Plan, write, read back — the whole loop without touching a save file."""
    obj = pal_object(level=10, rank=1, hp=50)
    plan = charedit.plan_pal_edit(obj, {"rank": 5, "ivs.hp": 100})

    for change in plan["changes"]:
        charedit._write_property(obj, charedit.PAL_PROPERTY_MAP[change["field"]], change["after"])

    view = charedit.read_pal(obj)
    assert view["rank"] == 5
    assert view["ivs"]["hp"] == 100
    assert view["level"] == 10, "an unrelated field was disturbed"
