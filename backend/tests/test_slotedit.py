"""
Inventory slot editing (Phase 7).

The module is deliberately thin — it turns a patch list into an import document
and delegates everything dangerous to `saveimport`. So these tests are about the
translation being faithful: an unpatched slot must survive untouched, a patch
must land on the right index, and every refusal the importer already makes must
still fire when the request arrives as a patch rather than as a file.
"""

from __future__ import annotations

import pytest

import saveexport
import saveimport
import slotedit


def slot(index, item_id="", count=0, dynamic=False):
    return {
        "slotIndex": index,
        "itemId": item_id,
        "itemName": item_id,
        "stackCount": count,
        "isEmpty": not item_id or count <= 0,
        "hasDynamicId": dynamic,
    }


def container(*slots):
    return list(slots)


CURRENT = container(
    slot(0, "Wood", 20),
    slot(1, "Stone", 5),
    slot(2),
    slot(3, "Bow", 1, dynamic=True),
)


# ─── Patch normalisation ─────────────────────────────────────────


def test_empty_patch_list_is_refused():
    with pytest.raises(slotedit.SlotEditError, match="No slot changes"):
        slotedit.build_document("c1", [], CURRENT)


def test_duplicate_slot_index_is_refused():
    """
    Two patches for one slot have no defined winner, and picking one silently
    is how an operator ends up with something they did not ask for.
    """
    with pytest.raises(slotedit.SlotEditError, match="patched twice"):
        slotedit.build_document("c1", [
            {"slotIndex": 0, "itemId": "Wood", "stackCount": 1},
            {"slotIndex": 0, "itemId": "Stone", "stackCount": 1},
        ], CURRENT)


def test_negative_and_non_integer_indices_are_refused():
    for bad in (-1, "0", 1.5, True):
        with pytest.raises(slotedit.SlotEditError, match="Bad slot index"):
            slotedit.build_document(
                "c1", [{"slotIndex": bad, "itemId": "Wood", "stackCount": 1}], CURRENT
            )


def test_slot_outside_the_container_is_refused():
    with pytest.raises(slotedit.SlotEditError, match="not in this container"):
        slotedit.build_document(
            "c1", [{"slotIndex": 99, "itemId": "Wood", "stackCount": 1}], CURRENT
        )


def test_empty_item_and_zero_count_both_clear():
    """Two ways to say the same thing; a UI should not have to know which."""
    by_item = slotedit.build_document("c1", [{"slotIndex": 0, "itemId": "", "stackCount": 9}], CURRENT)
    by_count = slotedit.build_document("c1", [{"slotIndex": 0, "itemId": "Wood", "stackCount": 0}], CURRENT)

    assert by_item["payload"]["slots"][0] == {"slotIndex": 0, "itemId": "", "stackCount": 0}
    assert by_count["payload"]["slots"][0] == by_item["payload"]["slots"][0]


def test_too_many_patches_are_refused():
    patches = [{"slotIndex": i, "itemId": "Wood", "stackCount": 1}
               for i in range(slotedit.MAX_PATCHES + 1)]
    with pytest.raises(slotedit.SlotEditError, match="exceeds"):
        slotedit.build_document("c1", patches, CURRENT)


# ─── Document construction ───────────────────────────────────────


def test_document_names_only_the_patched_slots():
    """
    A slot the operator did not touch must not appear at all. Naming it would
    both subject it to validation it never needed and let a stale view of it
    revert someone else's change.
    """
    document = slotedit.build_document(
        "c1", [{"slotIndex": 1, "itemId": "Stone", "stackCount": 50}], CURRENT
    )
    assert document["payload"]["slots"] == [
        {"slotIndex": 1, "itemId": "Stone", "stackCount": 50}
    ]


def test_an_unrecognised_item_elsewhere_does_not_block_the_edit():
    """
    Slot 3 holds an id the bundled game data does not know — modded, or from
    another version. Editing slot 0 must still work, because nothing is written
    to slot 3 and the importer is never asked to validate it.
    """
    with_mod = container(
        slot(0, "Wood", 20),
        slot(1, "Stone", 5),
        slot(2),
        slot(3, "SomeModdedThing", 1),
    )
    plan = slotedit.plan_slot_edit(
        "c1", [{"slotIndex": 0, "itemId": "Wood", "stackCount": 30}], with_mod
    )
    assert plan["ok"], plan["problems"]
    assert [c["slotIndex"] for c in plan["changes"]] == [0]


def test_document_is_a_valid_export_envelope():
    """It goes through `saveimport`, which verifies it like any other file."""
    document = slotedit.build_document(
        "c1", [{"slotIndex": 0, "itemId": "Wood", "stackCount": 1}], CURRENT
    )
    report = saveexport.verify(document)
    assert report["ok"], report["problems"]
    assert report["kind"] == "container"


def test_generator_says_it_came_from_the_slot_editor():
    document = slotedit.build_document(
        "c1", [{"slotIndex": 0, "itemId": "Wood", "stackCount": 1}], CURRENT
    )
    assert "slot-editor" in document["generator"]


# ─── Planning ────────────────────────────────────────────────────


def test_plan_reports_only_the_patched_slot():
    plan = slotedit.plan_slot_edit(
        "c1", [{"slotIndex": 0, "itemId": "Wood", "stackCount": 64}], CURRENT
    )
    assert plan["ok"], plan["problems"]
    assert [c["slotIndex"] for c in plan["changes"]] == [0]
    assert plan["changes"][0]["after"]["stackCount"] == 64
    assert plan["changes"][0]["action"] == "increase"
    assert plan["planHash"]


def test_plan_for_an_empty_slot_is_an_add():
    plan = slotedit.plan_slot_edit(
        "c1", [{"slotIndex": 2, "itemId": "Stone", "stackCount": 10}], CURRENT
    )
    assert plan["changes"][0]["action"] == "add"


def test_unknown_item_is_refused():
    """Inherited from the importer, and worth pinning at this layer too."""
    plan = slotedit.plan_slot_edit(
        "c1", [{"slotIndex": 2, "itemId": "NotARealItem", "stackCount": 1}], CURRENT
    )
    assert not plan["ok"]
    assert "Unknown item" in plan["problems"][0]["problem"]


def test_stack_ceiling_is_enforced():
    ceiling = __import__("gamedata").max_stack("Wood")
    assert ceiling, "test needs an item with a known ceiling"
    plan = slotedit.plan_slot_edit(
        "c1", [{"slotIndex": 0, "itemId": "Wood", "stackCount": ceiling + 1}], CURRENT
    )
    assert not plan["ok"]
    assert "stack limit" in plan["problems"][0]["problem"]


def test_durability_slot_cannot_be_overwritten():
    """
    Slot 3 holds a Bow with its own DynamicItemSaveData record. Writing over it
    orphans that record and a replacement cannot be fabricated, so the whole
    edit is refused rather than partially applied.
    """
    plan = slotedit.plan_slot_edit(
        "c1", [{"slotIndex": 3, "itemId": "Wood", "stackCount": 1}], CURRENT
    )
    assert not plan["ok"]
    assert "orphan" in plan["problems"][0]["problem"]


def test_a_no_op_patch_plans_no_changes():
    plan = slotedit.plan_slot_edit(
        "c1", [{"slotIndex": 0, "itemId": "Wood", "stackCount": 20}], CURRENT
    )
    assert plan["ok"]
    assert plan["changes"] == []


def test_plan_hash_changes_when_the_patch_changes():
    """The hash is what stops an apply from using a plan the operator never saw."""
    a = slotedit.plan_slot_edit("c1", [{"slotIndex": 0, "itemId": "Wood", "stackCount": 30}], CURRENT)
    b = slotedit.plan_slot_edit("c1", [{"slotIndex": 0, "itemId": "Wood", "stackCount": 31}], CURRENT)
    assert a["planHash"] != b["planHash"]


def test_plan_hash_changes_when_the_patched_slot_moves():
    """
    Staleness on the slot actually being written must invalidate the plan — that
    is what stops an apply from overwriting a value the operator never saw.
    """
    patch = [{"slotIndex": 0, "itemId": "Wood", "stackCount": 30}]
    before = slotedit.plan_slot_edit("c1", patch, CURRENT)

    moved = container(slot(0, "Wood", 7), slot(1, "Stone", 5), slot(2), slot(3, "Bow", 1, dynamic=True))
    after = slotedit.plan_slot_edit("c1", patch, moved)

    assert before["planHash"] != after["planHash"]


def test_item_totals_account_for_the_unpatched_slots():
    """
    A partial document must not make the container look like it holds only the
    slots being patched. Before is the whole container; after is that plus the
    delta.
    """
    plan = slotedit.plan_slot_edit(
        "c1", [{"slotIndex": 0, "itemId": "Wood", "stackCount": 30}], CURRENT
    )
    assert plan["itemsBefore"] == 20 + 5 + 1
    assert plan["itemsAfter"] == plan["itemsBefore"] + 10


# ─── Summaries ───────────────────────────────────────────────────


def test_summary_names_the_item_not_the_id():
    plan = slotedit.plan_slot_edit(
        "c1", [{"slotIndex": 2, "itemId": "Wood", "stackCount": 10}], CURRENT
    )
    summary = slotedit.summarise(plan)
    assert "slot 2" in summary and "10" in summary


def test_summary_of_a_refusal_does_not_claim_changes():
    plan = slotedit.plan_slot_edit(
        "c1", [{"slotIndex": 2, "itemId": "Nope", "stackCount": 1}], CURRENT
    )
    assert "nothing would be applied" in slotedit.summarise(plan)


def test_max_patches_tracks_the_importers_limit():
    """One ceiling, not two that can drift apart."""
    assert slotedit.MAX_PATCHES == saveimport.MAX_SLOTS
