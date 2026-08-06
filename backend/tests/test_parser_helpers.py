"""
Property-shape helpers.

Palworld 1.0 changed field shapes in ways that silently produced zeros and empty
lists rather than errors, which is the worst kind of breakage. Each test here
pins one of those shapes.
"""

from __future__ import annotations

import pytest

from parser import _enum, _num, _prop, _slot, _v


# ─── _v: safe nested walk ────────────────────────────────────────


def test_v_walks_nested_values():
    assert _v({"a": {"b": {"c": 1}}}, "a", "b", "c") == 1


def test_v_returns_default_on_missing_key():
    assert _v({"a": {}}, "a", "b", default="fallback") == "fallback"


def test_v_returns_default_on_non_dict():
    assert _v({"a": 5}, "a", "b", default=None) is None
    assert _v(None, "a", default=0) == 0


def test_v_treats_none_as_missing():
    assert _v({"a": None}, "a", default="d") == "d"


# ─── _num: Int vs Byte property nesting ──────────────────────────


def test_num_reads_int_property():
    """IntProperty: {'value': 42}"""
    assert _num({"Level": {"value": 42}}, "Level") == 42


def test_num_reads_byte_property():
    """
    ByteProperty nests one level deeper:
    {'value': {'type': 'None', 'value': 24}}

    Palworld 1.0 moved Level and every Talent_* IV to this shape. Reading it as
    an IntProperty yields the inner dict, int() raises, and the field silently
    became 0.
    """
    obj = {"Talent_HP": {"value": {"type": "None", "value": 24}}}
    assert _num(obj, "Talent_HP") == 24


def test_num_handles_float_values():
    assert _num({"Exp": {"value": 12.9}}, "Exp") == 12


def test_num_handles_bool_values():
    assert _num({"Flag": {"value": True}}, "Flag") == 1
    assert _num({"Flag": {"value": False}}, "Flag") == 0


def test_num_missing_field_returns_default():
    assert _num({}, "Nope") == 0
    assert _num({}, "Nope", default=-1) == -1


def test_num_unparseable_value_returns_default():
    """A string where a number belongs must not raise."""
    assert _num({"Level": {"value": "twenty"}}, "Level", default=7) == 7
    assert _num({"Level": {"value": {"value": None}}}, "Level", default=3) == 3


# ─── _slot: SlotId vs SlotID ─────────────────────────────────────


def test_slot_reads_modern_slot_id():
    """1.0 spells it SlotId."""
    obj = {"SlotId": {"value": {"ContainerId": {"value": {"ID": "abc"}}}}}
    assert _slot(obj, "ContainerId", "value", "ID") == "abc"


def test_slot_reads_legacy_slot_id_uppercase():
    """Pre-1.0 spelled it SlotID. Both must work."""
    obj = {"SlotID": {"value": {"ContainerId": {"value": {"ID": "xyz"}}}}}
    assert _slot(obj, "ContainerId", "value", "ID") == "xyz"


def test_slot_prefers_modern_spelling_when_both_present():
    obj = {
        "SlotId": {"value": {"Index": 1}},
        "SlotID": {"value": {"Index": 99}},
    }
    assert _slot(obj, "Index") == 1


def test_slot_missing_returns_none():
    assert _slot({}, "ContainerId") is None


# ─── _enum ───────────────────────────────────────────────────────


def test_enum_reads_double_nested_value():
    obj = {"Gender": {"value": {"value": "EPalGenderType::Female"}}}
    assert _enum(obj, "Gender") == "EPalGenderType::Female"


def test_enum_missing_returns_default():
    assert _enum({}, "Gender", default="unknown") == "unknown"


def test_enum_non_string_returns_default():
    assert _enum({"Gender": {"value": {"value": 3}}}, "Gender", default="x") == "x"


# ─── _prop ───────────────────────────────────────────────────────


def test_prop_reads_plain_value():
    assert _prop({"Name": {"value": "Anubis"}}, "Name") == "Anubis"


def test_prop_missing_returns_default():
    assert _prop({}, "Name", default="") == ""


# ─── OwnedTime is a timestamp, not a duration ────────────


def test_owned_time_decodes_as_a_date_not_a_duration():
    """
    **The field name misleads and this is what catches it.** `OwnedTime` reads
    like "how long owned" and is an absolute .NET DateTime tick count — 100ns
    intervals since 0001-01-01. Read as a duration, the reference world's values
    are roughly two thousand years; read as a timestamp they are 2024-2026,
    which is that save's actual lifespan.
    """
    from parser import _dotnet_ticks

    assert _dotnet_ticks(638486453957560000) == "2024-04-13 22:49:55"
    assert _dotnet_ticks(639208456013490000) == "2026-07-28 14:26:41"


def test_an_implausible_tick_count_is_dropped_not_rendered():
    """
    A garbage value must not become a date in the year 3000 on somebody's Pal
    list, and must not raise out of a parse with 1,904 other Pals to finish.
    """
    from parser import _dotnet_ticks

    assert _dotnet_ticks(0) is None
    assert _dotnet_ticks(-1) is None
    assert _dotnet_ticks(1) is None                  # year 1
    assert _dotnet_ticks(10**20) is None             # far future
    assert _dotnet_ticks(2**70) is None              # overflows outright


def test_no_timezone_is_asserted():
    """
    .NET stores a `DateTimeKind` beside the ticks and this save format drops it,
    so a trailing `Z` or an offset would be a claim the data does not support.
    """
    from parser import _dotnet_ticks

    stamp = _dotnet_ticks(639208456013490000)
    assert stamp and not stamp.endswith("Z")
    assert "+" not in stamp
