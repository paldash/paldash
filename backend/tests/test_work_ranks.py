"""
Work-suitability ranks: the ranks bought with Pal Souls.

WHY THERE IS NO MAXIMUM. Measured across three real worlds — refworld, the live
world and a 07-29 snapshot — **39 Pals carry `GotWorkSuitabilityAddRankList`**
and their ranks run `{1: 30, 2: 4, 3: 4, 6: 1}`. Six is the highest anyone has
reached, which is not a cap. The game ships no cap either:
`DT_GainWorkSuitabilityRankItem` decodes cleanly out of the server pak and holds
one ticket item per work type with **no rank column**, and no other DataTable
carries one. So the ceiling is not asserted, exactly as `fullStomach`'s is not.

Rank 0 appears on none of the 39, so a zero is `parser._num`'s default rather
than a value the game stores — which is why the *minimum* is real.

All 39 carry exactly **one** entry, so a multi-entry list is plausible and
unobserved. Adding is allowed (the struct shape is copied, and array length is
not the risky part) and the fact is recorded rather than hidden.
"""

from __future__ import annotations

import copy
import os
import sys

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

import charedit       # noqa: E402
import editschema     # noqa: E402

PROP = "GotWorkSuitabilityAddRankList"


def _entry(work_type: str, rank: int) -> dict:
    return {
        "WorkSuitability": {
            "value": {
                "type": "EPalWorkSuitability",
                "value": f"EPalWorkSuitability::{work_type}",
            }
        },
        "Rank": {"value": rank},
    }


def _obj(entries=None, **extra):
    obj = {
        "NickName": {"value": "Woolly"},
        "Level": {"value": {"value": 10}},
        "Exp": {"value": 100},
        "Rank": {"value": {"value": 1}},
    }
    if entries is not None:
        obj[PROP] = {"array_type": "StructProperty", "value": {"values": entries}}
    obj.update(extra)
    return obj


def _ranks(obj: dict) -> dict:
    return {
        charedit._struct_entry_work_type(e): e["Rank"]["value"]
        for e in obj[PROP]["value"]["values"]
    }


# ─── Reading ─────────────────────────────────────────────────────


def test_read_pal_reports_the_same_shape_the_writer_takes():
    """A round trip must not change anything, so read and write agree on shape."""
    view = charedit.read_pal(_obj([_entry("Handcraft", 2)]))
    assert view["workRanks"] == {"Handcraft": 2}


def test_a_pal_with_no_bought_ranks_does_not_offer_the_field():
    assert "workRanks" not in charedit.read_pal(_obj())


# ─── Writing ─────────────────────────────────────────────────────


def test_raising_an_existing_rank_writes_into_the_existing_struct():
    obj = _obj([_entry("Handcraft", 1)])
    charedit._apply_pal_change(obj, {"field": "workRanks", "after": {"Handcraft": 3}})
    assert _ranks(obj) == {"Handcraft": 3}


def test_array_type_survives_untouched():
    """
    Same rule the list writer follows. A `StructProperty` array rewritten as
    anything else still serialises and is silently wrong.
    """
    obj = _obj([_entry("Handcraft", 1)])
    charedit._apply_pal_change(obj, {"field": "workRanks", "after": {"Mining": 2}})
    assert obj[PROP]["array_type"] == "StructProperty"


def test_a_new_work_type_is_deep_copied_from_an_existing_entry():
    """
    Never constructed. The struct's metadata is whatever this save uses, which
    is `palclone`'s rule and for the same reason.
    """
    obj = _obj([_entry("Handcraft", 1)])
    charedit._apply_pal_change(
        obj, {"field": "workRanks", "after": {"Handcraft": 1, "Mining": 2}}
    )
    assert _ranks(obj) == {"Handcraft": 1, "Mining": 2}


def test_the_enum_prefix_comes_from_the_save_not_from_this_file():
    """
    A game update that renames the enum carries through, instead of producing
    entries the game silently ignores.
    """
    obj = _obj([{
        "WorkSuitability": {"value": {"type": "X", "value": "EPalSomethingElse::Handcraft"}},
        "Rank": {"value": 1},
    }])
    charedit._apply_pal_change(obj, {"field": "workRanks", "after": {"Mining": 1}})
    written = obj[PROP]["value"]["values"][0]["WorkSuitability"]["value"]["value"]
    assert written == "EPalSomethingElse::Mining"


def test_omitting_a_work_type_drops_its_bought_rank():
    """A deletion, which is the safe direction — an absent entry is the norm."""
    obj = _obj([_entry("Handcraft", 1), _entry("Mining", 2)])
    charedit._apply_pal_change(obj, {"field": "workRanks", "after": {"Mining": 2}})
    assert _ranks(obj) == {"Mining": 2}


def test_an_empty_map_clears_every_bought_rank():
    obj = _obj([_entry("Handcraft", 1)])
    charedit._apply_pal_change(obj, {"field": "workRanks", "after": {}})
    assert obj[PROP]["value"]["values"] == []


# ─── Refusals ────────────────────────────────────────────────────


def test_an_absent_property_with_NO_DONOR_ANYWHERE_is_refused():
    """
    The refusal narrowed and the reason did not. Creating the shape is still off
    the table; what changed is that the shape may be borrowed from any Pal in the
    same save, so this is now the genuine "nothing to copy" case — no such node
    exists on the whole server.

    The message has to be actionable, because the operator's move is to spend one
    handbook on any Pal rather than to give up.
    """
    with pytest.raises(charedit.EditError, match="No Pal on this server") as excinfo:
        charedit._apply_pal_change(
            _obj(), {"field": "workRanks", "after": {"Handcraft": 1}}
        )
    assert "handbook" in str(excinfo.value)


def test_a_present_but_empty_list_is_refused_ONLY_WITHOUT_A_DONOR():
    """
    Present is not the same as usable — an empty array has an `array_type` and no
    template. But an *absent* property carries strictly less information than an
    empty one, so refusing this while accepting that would be backwards.
    """
    with pytest.raises(charedit.EditError, match="no Pal on this server"):
        charedit._apply_pal_change(
            _obj([]), {"field": "workRanks", "after": {"Handcraft": 1}}
        )


def test_an_empty_list_takes_the_struct_from_a_donor_and_keeps_its_own_metadata():
    """
    The array metadata here is already this Pal's own and correct; only the
    struct is missing. Replacing the whole node would throw away a right answer
    to import a duplicate of it.
    """
    obj = _obj([])
    obj[PROP]["array_type"] = "StructProperty"
    obj[PROP]["id"] = "11111111-1111-1111-1111-111111111111"

    donor = _obj([_entry("Mining", 2)])[PROP]
    donor["id"] = "22222222-2222-2222-2222-222222222222"

    charedit._write_work_ranks(obj, PROP, {"Handcraft": 4}, donor)

    assert _ranks(obj) == {"Handcraft": 4}
    assert obj[PROP]["id"] == "11111111-1111-1111-1111-111111111111"
    # And the donor is untouched — a shallow copy here would edit a Pal the
    # operator never named.
    assert donor["value"]["values"][0]["Rank"]["value"] == 2


def test_an_unknown_work_type_is_rejected():
    assert editschema.PAL_FIELDS["workRanks"].check({"Nonsense": 1}) is not None


def test_every_bundled_work_type_is_accepted():
    """The 13 come from the bundled table, not from a list written here."""
    known = editschema.work_suitabilities()
    assert len(known) == 13
    for work_type in known:
        assert editschema.PAL_FIELDS["workRanks"].check({work_type: 1}) is None


def test_rank_zero_is_rejected_because_the_game_never_stores_one():
    problem = editschema.PAL_FIELDS["workRanks"].check({"Handcraft": 0})
    assert problem is not None and "at least 1" in problem


def test_no_maximum_is_asserted_since_none_exists_in_the_data():
    """
    Six is the highest observed across three worlds. It is not a cap, and the
    game ships no table carrying one — so a rank above it must not be refused.
    """
    assert editschema.PAL_FIELDS["workRanks"].check({"Handcraft": 7}) is None
    assert editschema.PAL_FIELDS["workRanks"].maximum is None


# ─── Planning ────────────────────────────────────────────────────


def test_a_map_field_is_not_flattened_into_dotted_keys():
    """
    `_flatten` expands `{"ivs": {...}}` into `ivs.hp`, and expanded `workRanks`
    the same way — so the diff read `before: None`, and since None never equals
    the requested map, an edit that changed nothing planned as a change.
    """
    obj = _obj([_entry("Handcraft", 1)])
    plan = charedit.plan_pal_edit(copy.deepcopy(obj), {"workRanks": {"Handcraft": 3}})
    assert plan["ok"], plan["problems"]
    assert plan["changes"][0]["before"] == {"Handcraft": 1}

    unchanged = charedit.plan_pal_edit(copy.deepcopy(obj), {"workRanks": {"Handcraft": 1}})
    assert unchanged["ok"] and unchanged["changes"] == []


def test_ivs_are_still_flattened():
    """The fix must not break the grouping it was carving an exception out of."""
    obj = _obj([_entry("Handcraft", 1)], Talent_HP={"value": {"value": 50}})
    plan = charedit.plan_pal_edit(obj, {"ivs.hp": 60})
    assert plan["ok"], plan["problems"]
    assert plan["changes"][0]["before"] == 50
