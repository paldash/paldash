"""
Container sorting.

The invariant: sorting may reorder and merge stacks, but the total quantity of
every item in a container must be identical afterwards. These tests build
synthetic containers in the shape palsav produces, so the algorithm can be
checked exhaustively without parsing a 55 MB world.
"""

from __future__ import annotations

import pytest

import saveedit
from saveedit import (
    ZERO_GUID,
    SaveEditError,
    _assert_conserved,
    _has_dynamic_id,
    _is_empty,
    _max_stacks,
    _sort_container,
    _totals,
)


# ─── Builders matching the parsed save shape ─────────────────────


def slot(static_id: str = "", count: int = 0, dynamic: str | None = None) -> dict:
    return {
        "RawData": {
            "value": {
                "item": {
                    "static_id": static_id,
                    "dynamic_id": {
                        "created_world_id": ZERO_GUID,
                        "local_id_in_created_world": dynamic or ZERO_GUID,
                    },
                },
                "count": count,
            }
        }
    }


def container(container_id: str, slots: list[dict]) -> dict:
    return {
        "key": {"ID": {"value": container_id}},
        "value": {"Slots": {"value": {"values": slots}}},
    }


def counts_of(entry: dict) -> list[tuple[str, int]]:
    """Occupied (item, count) pairs in slot order."""
    out = []
    for s in entry["value"]["Slots"]["value"]["values"]:
        raw = s["RawData"]["value"]
        if not _is_empty(raw):
            out.append((raw["item"]["static_id"], raw["count"]))
    return out


# ─── Slot predicates ─────────────────────────────────────────────


def test_empty_slot_detection():
    assert _is_empty(slot()["RawData"]["value"]) is True
    assert _is_empty(slot("Wood", 0)["RawData"]["value"]) is True
    assert _is_empty(slot("", 5)["RawData"]["value"]) is True
    assert _is_empty(slot("Wood", 5)["RawData"]["value"]) is False


def test_dynamic_id_detection():
    plain = slot("Wood", 5)["RawData"]["value"]
    weapon = slot("Sword", 1, dynamic="abc-123")["RawData"]["value"]
    assert _has_dynamic_id(plain) is False
    assert _has_dynamic_id(weapon) is True


def test_totals_are_per_container():
    entries = [
        container("c1", [slot("Wood", 10), slot("Wood", 5), slot("Stone", 3)]),
        container("c2", [slot("Wood", 1)]),
    ]
    assert _totals(entries) == {"c1": {"Wood": 15, "Stone": 3}, "c2": {"Wood": 1}}


def test_totals_ignore_empty_slots():
    entries = [container("c1", [slot("Wood", 10), slot(), slot("Wood", 0)])]
    assert _totals(entries) == {"c1": {"Wood": 10}}


def test_max_stacks_is_the_largest_observed():
    entries = [
        container("c1", [slot("Wood", 50), slot("Wood", 9999)]),
        container("c2", [slot("Wood", 100), slot("Stone", 7)]),
    ]
    assert _max_stacks(entries) == {"Wood": 9999, "Stone": 7}


# ─── Sorting ─────────────────────────────────────────────────────


def test_sort_orders_by_item_id():
    entry = container("c", [slot("Stone", 1), slot("Wood", 1), slot("Ore", 1)])
    _sort_container(entry, "stackables", merge=False, max_stacks={})
    assert [i for i, _ in counts_of(entry)] == ["Ore", "Stone", "Wood"]


def test_sort_conserves_totals():
    entry = container("c", [slot("Wood", 7), slot("Stone", 3), slot("Wood", 11)])
    before = _totals([entry])
    _sort_container(entry, "stackables", merge=True, max_stacks={"Wood": 9999, "Stone": 9999})
    _assert_conserved(before, _totals([entry]), "test")


def test_merge_combines_partial_stacks():
    entry = container("c", [slot("Wood", 7), slot("Wood", 11), slot()])
    _sort_container(entry, "stackables", merge=True, max_stacks={"Wood": 9999})
    assert counts_of(entry) == [("Wood", 18)]


def test_merge_respects_the_observed_stack_ceiling():
    """
    Never build a stack larger than one the save already contains — the game's
    real limits are not in the save, so an observed maximum is the safe ceiling.
    """
    entry = container("c", [slot("Wood", 40), slot("Wood", 40), slot("Wood", 40), slot()])
    _sort_container(entry, "stackables", merge=True, max_stacks={"Wood": 50})
    stacks = counts_of(entry)
    assert sum(c for _, c in stacks) == 120
    assert all(c <= 50 for _, c in stacks)
    assert stacks == [("Wood", 50), ("Wood", 50), ("Wood", 20)]


def test_merge_frees_slots():
    entry = container("c", [slot("Wood", 1), slot("Wood", 1), slot("Wood", 1), slot()])
    _sort_container(entry, "stackables", merge=True, max_stacks={"Wood": 9999})
    slots = entry["value"]["Slots"]["value"]["values"]
    occupied = [s for s in slots if not _is_empty(s["RawData"]["value"])]
    assert len(occupied) == 1
    assert len(slots) == 4, "slot count must never change"


def test_freed_slots_reuse_an_existing_empty_representation():
    """
    Empty slots are never fabricated — a cleared slot copies an empty slot that
    already exists in the same container, so we never guess what 'empty' means.
    """
    entry = container("c", [slot("Wood", 1), slot("Wood", 1), slot()])
    template = dict(entry["value"]["Slots"]["value"]["values"][2]["RawData"]["value"]["item"])
    _sort_container(entry, "stackables", merge=True, max_stacks={"Wood": 9999})

    slots = entry["value"]["Slots"]["value"]["values"]
    cleared = [s["RawData"]["value"] for s in slots if _is_empty(s["RawData"]["value"])]
    assert cleared
    for raw in cleared:
        assert raw["item"]["static_id"] == template["static_id"]
        assert raw["count"] == 0


def test_container_with_no_empty_slot_is_not_merged():
    """Without an empty template there is no safe way to clear a freed slot."""
    entry = container("c", [slot("Wood", 1), slot("Wood", 1)])
    before = _totals([entry])
    _sort_container(entry, "stackables", merge=True, max_stacks={"Wood": 9999})
    _assert_conserved(before, _totals([entry]), "test")
    assert counts_of(entry) == [("Wood", 1), ("Wood", 1)]


# ─── Mode differences ────────────────────────────────────────────


def test_stackables_mode_never_moves_equipment():
    entry = container(
        "c",
        [slot("Wood", 5), slot("Sword", 1, dynamic="dur-1"), slot("Stone", 2)],
    )
    _sort_container(entry, "stackables", merge=True, max_stacks={})
    slots = entry["value"]["Slots"]["value"]["values"]
    middle = slots[1]["RawData"]["value"]
    assert middle["item"]["static_id"] == "Sword"
    assert middle["item"]["dynamic_id"]["local_id_in_created_world"] == "dur-1"


def test_all_mode_moves_equipment_and_keeps_its_link():
    entry = container(
        "c",
        [slot("Wood", 5), slot("Axe", 1, dynamic="dur-9"), slot("Ore", 2)],
    )
    _sort_container(entry, "all", merge=True, max_stacks={"Wood": 99, "Ore": 99})

    ids = [i for i, _ in counts_of(entry)]
    assert ids == sorted(ids), "equipment participates in the ordering"

    links = [
        s["RawData"]["value"]["item"]["dynamic_id"]["local_id_in_created_world"]
        for s in entry["value"]["Slots"]["value"]["values"]
    ]
    assert "dur-9" in links, "the durability link must survive relocation"


def test_equipment_is_never_pooled_even_in_all_mode():
    """Two swords must stay two rows — merging them would orphan a durability record."""
    entry = container(
        "c",
        [slot("Sword", 1, dynamic="a"), slot("Sword", 1, dynamic="b"), slot()],
    )
    _sort_container(entry, "all", merge=True, max_stacks={"Sword": 99})
    assert counts_of(entry) == [("Sword", 1), ("Sword", 1)]

    links = {
        s["RawData"]["value"]["item"]["dynamic_id"]["local_id_in_created_world"]
        for s in entry["value"]["Slots"]["value"]["values"]
    }
    assert {"a", "b"} <= links


# ─── Conservation checking ───────────────────────────────────────


def test_assert_conserved_passes_when_equal():
    before = {"c1": {"Wood": 10}}
    _assert_conserved(before, {"c1": {"Wood": 10}}, "test")


def test_assert_conserved_catches_loss():
    with pytest.raises(SaveEditError, match="conservation check failed"):
        _assert_conserved({"c1": {"Wood": 10}}, {"c1": {"Wood": 9}}, "test")


def test_assert_conserved_catches_duplication():
    with pytest.raises(SaveEditError, match="conservation check failed"):
        _assert_conserved({"c1": {"Wood": 10}}, {"c1": {"Wood": 11}}, "test")


def test_assert_conserved_catches_a_vanished_container():
    with pytest.raises(SaveEditError, match="conservation check failed"):
        _assert_conserved({"c1": {"Wood": 1}}, {}, "test")


def test_assert_conserved_reports_the_offending_item():
    with pytest.raises(SaveEditError) as excinfo:
        _assert_conserved({"c1": {"Wood": 10, "Ore": 5}}, {"c1": {"Wood": 10, "Ore": 4}}, "test")
    assert "Ore" in str(excinfo.value)
    assert "Wood" not in str(excinfo.value), "only the mismatched item should be reported"


# ─── Entry-point guards ──────────────────────────────────────────


def test_unknown_mode_is_rejected():
    with pytest.raises(SaveEditError, match="Unknown sort mode"):
        saveedit.sort_containers(mode="destroy-everything")


def test_sort_refuses_when_server_may_be_running(monkeypatch, tmp_path):
    """The safety gate must be reached before anything is read or written."""
    import safety

    monkeypatch.setattr(
        safety, "_probe_rest_api", lambda: safety.Signal("rest_api", "running", "t")
    )
    monkeypatch.setattr(saveedit, "get_level_sav_path", lambda: str(tmp_path / "Level.sav"))

    with pytest.raises(safety.ServerRunningError):
        saveedit.sort_containers(mode="stackables")
