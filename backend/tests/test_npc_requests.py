"""
The two NPC request chains, and the fact that only one of them is trackable.

Both come from DataAssets found by the class census. The interesting property is
not the data — it is that the two halves have different epistemic status and the
payload has to say so, because a panel that implies completion it cannot see is
worse than one that admits it only lists what exists.

Asserted against the **shipped bundle**, not the extractor.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gamedata  # noqa: E402
import progresscheck  # noqa: E402


def test_both_chains_are_bundled():
    data = gamedata.npc_requests()
    assert data, "npc_requests.json.gz is missing from the shipped bundle"
    assert len(data["palDisplay"]["requests"]) == 54
    assert len(data["itemRequest"]["requests"]) == 11


def test_only_the_pal_display_half_claims_to_be_tracked():
    """
    THE DISTINCTION THIS BUNDLE EXISTS TO CARRY.

    `PalDisplayNPCDataTableProgress` is in the save, keyed by these RequestIDs.
    No progress field for item requests has been observed on any player, so that
    half must never be rendered as a checklist.
    """
    data = gamedata.npc_requests()
    assert data["palDisplay"]["tracked"] is True
    assert data["palDisplay"]["savedFlag"] == "PalDisplayNPCDataTableProgress"

    assert data["itemRequest"]["tracked"] is False
    assert data["itemRequest"]["savedFlag"] is None
    assert "lists what exists" in data["itemRequest"]["note"]


def test_a_request_names_a_species_a_reward_and_an_area():
    entry = gamedata.npc_requests()["palDisplay"]["requests"]["Area_A1_1"]
    assert entry["speciesId"] == "Carbunclo"
    assert entry["category"] == "Area_A1"
    assert {"itemId": "PalSphere", "count": 10} in entry["rewards"]


def test_the_checklist_labels_the_species_not_the_request_id():
    """
    `Area_F1_1` is a join key, not an answer. A player recognises "Carbunclo".
    """
    result = progresscheck.pal_display(["Area_A1_1"])
    assert result["of"] == 54
    assert result["obtained"] == 1

    listed = result["missing"]
    assert listed, "54 requests and one obtained should leave something missing"
    for entry in listed:
        assert entry["name"], entry
        # The raw id is still carried — it is what the save keys on — but it is
        # never the display name.
        assert entry["name"] != entry["id"]


def test_an_unknown_key_counts_but_is_reported_separately():
    """
    `_checklist`'s documented contract, and it is a deliberate choice rather
    than an oversight: a key the bundle does not list still counts toward
    `obtained`, because the player has plainly done the thing and the honest
    reading is that this bundle does not enumerate it — not that the save is
    wrong.

    (My first version of this test asserted the opposite, from an assumption
    rather than from reading the helper. The denominator is what must not move.)

    What must hold is that the unknown key is **never silently folded in**: it
    appears in `unlisted` so a caller can see the count exceeds the catalogue.
    """
    result = progresscheck.pal_display(["Area_A1_1", "Area_ZZ_9"])
    assert result["of"] == 54, "the denominator comes from the game's data"
    assert result["obtained"] == 2
    assert result["unlisted"] == ["Area_ZZ_9"]
    assert not any(e["id"] == "Area_ZZ_9" for e in result["have"])


def test_the_item_half_is_absent_from_the_progress_checklists():
    """
    Pinned as an absence, because adding it is the tempting next commit and the
    save cannot support it. `describe()` must not grow an `itemRequest` key
    until somebody observes the progress field on a real player.
    """
    detail = progresscheck.describe({})
    assert "palDisplay" in detail
    assert "itemRequest" not in detail, (
        "item-request progress is not recorded in any save examined — adding a "
        "checklist for it would show every player at 0 of 11 forever"
    )
