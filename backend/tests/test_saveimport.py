"""
Import validation and dry-run planning (Phase 6, import half).

Nothing here writes. These tests cover the gate that decides whether a write is
even allowed to be attempted — which is where an import goes wrong, long before
any bytes reach a save file.
"""

from __future__ import annotations

import pytest

import saveexport
import saveimport


def document(slots, container_id="c1", kind="container"):
    payload = {"containerId": container_id, "owner": None, "slots": slots}
    return saveexport.envelope(kind, payload, "WORLDGUID")


def slot(index, item="Wood", count=10, empty=False):
    return {"slotIndex": index, "itemId": "" if empty else item,
            "stackCount": 0 if empty else count, "isEmpty": empty}


CURRENT = [slot(0, "Wood", 100), slot(1, "Stone", 50), slot(2, empty=True)]


# ─── Durability items ────────────────────────────────────────────


def test_a_slot_holding_a_durability_item_blocks_the_import():
    """
    Equipment and eggs have their own DynamicItemSaveData record. Writing over
    one orphans that record, and a replacement cannot be fabricated — so the
    whole import is refused rather than partially applied.
    """
    current = [dict(slot(0, "Shield_03", 1), hasDynamicId=True)]
    doc = document([slot(0, "Wood", 10)])

    plan = saveimport.plan_container_import(doc, current)

    assert plan["ok"] is False
    assert plan["changes"] == []
    assert "orphan" in plan["problems"][0]["problem"]


def test_an_untouched_durability_slot_does_not_block_anything():
    """Only slots the import would actually change are checked."""
    current = [
        dict(slot(0, "Shield_03", 1), hasDynamicId=True),
        slot(1, "Wood", 10),
    ]
    doc = document([slot(0, "Shield_03", 1), slot(1, "Wood", 25)])

    plan = saveimport.plan_container_import(doc, current)

    assert plan["ok"] is True
    assert plan["slotsChanged"] == 1
    assert plan["changes"][0]["slotIndex"] == 1


# ─── Payload validation ──────────────────────────────────────────


def test_a_clean_payload_validates():
    report = saveimport.validate_container_payload({"slots": [slot(0, "Wood", 10)]}, capacity=3)
    assert report["ok"], report["problems"]
    assert report["slots"][0]["stackCount"] == 10


def test_unknown_item_ids_are_rejected():
    """Strict on purpose — an item the game does not know is a crash."""
    report = saveimport.validate_container_payload({"slots": [slot(0, "NotARealItem", 1)]}, capacity=3)

    assert not report["ok"]
    assert "Unknown item id" in report["problems"][0]["problem"]


def test_counts_beyond_the_real_stack_limit_are_rejected():
    report = saveimport.validate_container_payload({"slots": [slot(0, "Wood", 999999)]}, capacity=3)

    assert not report["ok"]
    assert "stack limit" in report["problems"][0]["problem"]


@pytest.mark.parametrize("bad", [0, -5, 1.5, "10", True, None])
def test_non_positive_or_non_integer_counts_are_rejected(bad):
    payload = {"slots": [{"slotIndex": 0, "itemId": "Wood", "stackCount": bad}]}
    assert not saveimport.validate_container_payload(payload, capacity=3)["ok"]


@pytest.mark.parametrize("bad", [-1, "0", 1.5, True, None])
def test_bad_slot_indexes_are_rejected(bad):
    payload = {"slots": [{"slotIndex": bad, "itemId": "Wood", "stackCount": 1}]}
    assert not saveimport.validate_container_payload(payload, capacity=3)["ok"]


def test_duplicate_slot_indexes_are_rejected():
    payload = {"slots": [slot(0, "Wood", 1), slot(0, "Stone", 1)]}
    report = saveimport.validate_container_payload(payload, capacity=3)

    assert not report["ok"]
    assert "Duplicate" in report["problems"][0]["problem"]


def test_slots_beyond_the_target_capacity_are_rejected():
    """A 40-slot chest's contents must not be poured into a 3-slot box."""
    report = saveimport.validate_container_payload({"slots": [slot(7, "Wood", 1)]}, capacity=3)

    assert not report["ok"]
    assert "outside the target container" in report["problems"][0]["problem"]


def test_an_absurd_slot_count_is_refused_before_validation():
    payload = {"slots": [slot(i, "Wood", 1) for i in range(saveimport.MAX_SLOTS + 1)]}
    report = saveimport.validate_container_payload(payload)

    assert not report["ok"]
    assert "maximum" in report["problems"][0]["problem"]


def test_empty_slots_are_allowed():
    report = saveimport.validate_container_payload({"slots": [slot(0, empty=True)]}, capacity=3)

    assert report["ok"]
    assert report["slots"][0] == {"slotIndex": 0, "itemId": "", "stackCount": 0}


def test_junk_payloads_do_not_raise():
    for junk in [None, [], "text", 42, {}, {"slots": "not a list"}]:
        report = saveimport.validate_container_payload(junk)
        assert report["ok"] is False
        assert report["problems"]


# ─── Document gating ─────────────────────────────────────────────


def test_a_tampered_document_is_refused_before_planning():
    doc = document([slot(0, "Wood", 10)])
    doc["payload"]["slots"][0]["stackCount"] = 9999

    with pytest.raises(saveimport.ImportError_, match="Checksum mismatch"):
        saveimport.plan_container_import(doc, CURRENT)


def test_unsupported_kinds_are_refused_with_a_reason():
    doc = saveexport.envelope("player", {"player": {}, "pals": []}, "W")

    with pytest.raises(saveimport.ImportRefused, match="not implemented"):
        saveimport.plan_container_import(doc, CURRENT)


def test_every_unsupported_kind_is_refused():
    for kind in ("world", "player", "guild", "base"):
        doc = saveexport.envelope(kind, {"anything": True}, "W")
        with pytest.raises(saveimport.ImportRefused):
            saveimport.plan_container_import(doc, CURRENT)


# ─── Planning ────────────────────────────────────────────────────


def test_the_plan_reports_exactly_what_changes():
    doc = document([slot(0, "Wood", 100), slot(1, "Stone", 75), slot(2, empty=True)])
    plan = saveimport.plan_container_import(doc, CURRENT)

    assert plan["ok"]
    # Slot 0 is unchanged and must not appear; only slot 1 moved.
    assert plan["slotsChanged"] == 1
    assert plan["changes"][0]["slotIndex"] == 1
    assert plan["changes"][0]["before"]["stackCount"] == 50
    assert plan["changes"][0]["after"]["stackCount"] == 75
    assert plan["changes"][0]["action"] == "increase"


def test_an_identical_document_plans_no_changes():
    doc = document([slot(0, "Wood", 100), slot(1, "Stone", 50), slot(2, empty=True)])
    plan = saveimport.plan_container_import(doc, CURRENT)

    assert plan["ok"]
    assert plan["slotsChanged"] == 0
    assert "No changes" in saveimport.summarise(plan)


def test_the_plan_names_the_actions():
    doc = document([slot(0, empty=True), slot(1, "Wood", 50), slot(2, "Stone", 5)])
    actions = {c["slotIndex"]: c["action"] for c in
               saveimport.plan_container_import(doc, CURRENT)["changes"]}

    assert actions[0] == "clear"       # Wood 100 -> empty
    assert actions[1] == "replace"     # Stone -> Wood
    assert actions[2] == "add"         # empty -> Stone


def test_the_plan_resolves_friendly_names():
    doc = document([slot(0, "Wood", 100), slot(1, "Stone", 50), slot(2, "AIcore", 1)])
    change = saveimport.plan_container_import(doc, CURRENT)["changes"][0]

    assert change["after"]["itemName"]
    assert change["after"]["itemName"] != change["after"]["itemId"]


def test_the_plan_reports_the_item_total_delta():
    doc = document([slot(0, "Wood", 200), slot(1, "Stone", 50), slot(2, empty=True)])
    plan = saveimport.plan_container_import(doc, CURRENT)

    assert plan["itemsBefore"] == 150
    assert plan["itemsAfter"] == 250
    assert "+100" in saveimport.summarise(plan)


def test_a_plan_with_problems_is_not_ok_and_changes_nothing():
    doc = document([slot(0, "NotARealItem", 1)])
    plan = saveimport.plan_container_import(doc, CURRENT)

    assert plan["ok"] is False
    assert plan["changes"] == []
    assert plan["planHash"] == ""


def test_the_plan_hash_is_stable_and_content_dependent():
    """
    `apply` compares this against the plan the operator approved, so it must
    change whenever the effect changes and not otherwise.
    """
    doc = document([slot(0, "Wood", 100), slot(1, "Stone", 75), slot(2, empty=True)])
    first = saveimport.plan_container_import(doc, CURRENT)
    again = saveimport.plan_container_import(doc, CURRENT)
    assert first["planHash"] == again["planHash"]

    other = document([slot(0, "Wood", 100), slot(1, "Stone", 76), slot(2, empty=True)])
    assert saveimport.plan_container_import(other, CURRENT)["planHash"] != first["planHash"]


def test_the_plan_hash_changes_when_the_world_moves():
    """The same document against a changed container is a different plan."""
    doc = document([slot(0, "Wood", 100), slot(1, "Stone", 75), slot(2, empty=True)])
    before = saveimport.plan_container_import(doc, CURRENT)

    moved = [slot(0, "Wood", 100), slot(1, "Stone", 60), slot(2, empty=True)]
    assert saveimport.plan_container_import(doc, moved)["planHash"] != before["planHash"]


def test_the_source_world_is_carried_into_the_plan():
    """Importing another server's container should be visible, not silent."""
    plan = saveimport.plan_container_import(document([slot(0, "Wood", 1)]), CURRENT)
    assert plan["sourceWorldGuid"] == "WORLDGUID"


# ─── Round trip ──────────────────────────────────────────────────


def test_an_export_of_the_current_state_imports_as_a_no_op():
    """The strongest end-to-end statement: export then import changes nothing."""
    sections = {
        "containers": {"c1": CURRENT},
        "containerOwnership": {"c1": {"baseCampId": "b1"}},
        "worldGuid": "WORLDGUID",
    }
    exported = saveexport.export_container(sections, "c1")
    plan = saveimport.plan_container_import(exported, CURRENT)

    assert plan["ok"]
    assert plan["slotsChanged"] == 0
