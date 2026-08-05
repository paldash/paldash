"""
Ownership history — `OldOwnerPlayerUIds`, present on 100% of Pals.

TWO THINGS MAKE THIS DIFFERENT from every other list field here.

**The values are palsav `UUID` objects, not strings.** `_write_list_property`
coerces everything with `str()`, which would produce a tree that reads back
correctly and an encoder that emits wrong bytes — the same trap `soloexport`
records, where an `isinstance(v, str)` test matched nothing and rewrote zero of
6,455 uid fields. `_write_uid_list` reconstructs the class instead.

**Validation is on shape, never against the roster.** A Pal traded in from
another server legitimately names a uid nobody here has seen, and the main reason
to edit this at all is after a `soloexport` uid remap leaves entries pointing at
a player who no longer exists anywhere.
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

PROP = "OldOwnerPlayerUIds"
A = "11a11a01-0000-0000-0000-000000000000"
B = "22b22b02-0000-0000-0000-000000000000"


def _uuid(text: str):
    from palsav.archive import UUID as PalUUID

    return PalUUID.from_str(text)


def _obj(owners=None):
    obj = {
        "NickName": {"value": "Woolly"},
        "Level": {"value": {"value": 10}},
        "Exp": {"value": 100},
        "Rank": {"value": {"value": 1}},
    }
    if owners is not None:
        obj[PROP] = {"array_type": "StructProperty", "value": {"values": owners}}
    return obj


def _values(obj: dict) -> list:
    return obj[PROP]["value"]["values"]


# ─── The value type, which is the whole point ────────────────────


def test_written_owners_keep_the_uuid_class_rather_than_becoming_strings():
    """
    A `str` here serialises into wrong bytes. The failure is invisible in the
    tree — this is the only place it can be caught.
    """
    from palsav.archive import UUID as PalUUID

    obj = _obj([_uuid(A)])
    charedit._apply_pal_change(obj, {"field": "previousOwners", "after": [A, B]})
    assert all(isinstance(v, PalUUID) for v in _values(obj))
    assert [str(v) for v in _values(obj)] == [A, B]


def test_a_save_storing_plain_strings_keeps_storing_plain_strings():
    """The class comes from what is already there, not from this file."""
    obj = _obj([A])
    charedit._apply_pal_change(obj, {"field": "previousOwners", "after": [B]})
    assert _values(obj) == [B]


def test_reading_gives_plain_strings_because_that_is_what_the_api_speaks():
    assert charedit.read_pal(_obj([_uuid(A)]))["previousOwners"] == [A]


# ─── Editing ─────────────────────────────────────────────────────


def test_history_can_be_cleared_entirely():
    obj = _obj([_uuid(A), _uuid(B)])
    charedit._apply_pal_change(obj, {"field": "previousOwners", "after": []})
    assert _values(obj) == []


def test_the_plan_shows_both_sides_so_the_audit_log_records_them():
    obj = _obj([_uuid(A)])
    plan = charedit.plan_pal_edit(copy.deepcopy(obj), {"previousOwners": [A, B]})
    assert plan["ok"], plan["problems"]
    assert plan["changes"][0]["before"] == [A]
    assert plan["changes"][0]["after"] == [A, B]


def test_an_unchanged_history_plans_as_no_change():
    plan = charedit.plan_pal_edit(_obj([_uuid(A)]), {"previousOwners": [A]})
    assert plan["ok"] and plan["changes"] == []


# ─── Validation ──────────────────────────────────────────────────


def test_a_full_entropy_guid_is_rejected():
    """
    A player uid is a Steam ID32 followed by zeros. Bases, guilds and character
    instances use full-entropy GUIDs, and filing one of those as an owner names
    a thing that is not a player.
    """
    problem = editschema.PAL_FIELDS["previousOwners"].check(
        ["3f2a91cc-77bd-4e1a-9f00-112233445566"]
    )
    assert problem is not None and "player uid" in problem


def test_duplicates_are_rejected():
    assert editschema.PAL_FIELDS["previousOwners"].check([A, A]) is not None


def test_a_uid_this_server_has_never_seen_is_accepted():
    """
    Validated on shape, never against the roster — a Pal traded in from another
    server names a stranger, and the remap case names someone who is now gone.
    """
    assert editschema.PAL_FIELDS["previousOwners"].check([
        "deadbeef-0000-0000-0000-000000000000"
    ]) is None


def test_an_empty_history_is_valid():
    assert editschema.PAL_FIELDS["previousOwners"].check([]) is None


def test_an_absent_property_is_still_refused():
    """
    It is present on 100% of Pals, so this cannot happen on a real world — but
    the rule that a writer never invents a property does not get an exception
    for a field that is merely usually there.
    """
    with pytest.raises(charedit.EditError, match=f"no '{PROP}'"):
        charedit._apply_pal_change(_obj(), {"field": "previousOwners", "after": [A]})
