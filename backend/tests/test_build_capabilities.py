"""
What a structure contributes to base output.

`DA_PalBuildObjectCapabilityData` was found by **class**, not by name — it is a
`PalBuildObjectCapabilityDataAsset`, so no DataTable sweep saw it and the
prefix-based asset census excluded it too. That was the fourth time enumerating
by naming convention cost a whole category.

These assert against the **shipped bundle** rather than the extractor, because a
test of the generator passes happily beside a stale `.json.gz` — the lesson
`test_gametext.py` records after two placeholder names shipped for months.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gamedata  # noqa: E402


def test_the_bundle_is_present_and_has_every_structure():
    data = gamedata.build_capabilities()
    assert data, "build_capabilities.json.gz is missing from the shipped bundle"
    assert len(data["structures"]) == 48


def test_the_blast_furnace_ladder_is_the_reason_this_exists():
    """
    An operator on a tier-1 furnace runs at a ninth of the Ancient one. This is
    the spread the Bases tab had no way to show, so it is worth pinning by value
    rather than by shape.
    """
    rate = "WorkSpeedAdditionalRate"
    ladder = [
        ("BlastFurnace", 1.0),
        ("BlastFurnace2", 1.5),
        ("BlastFurnace3", 3.0),
        ("BlastFurnace4", 4.5),
        ("AncientBlastFurnace", 11.0),
    ]
    for structure, expected in ladder:
        assert gamedata.structure_capability(structure)[rate] == expected


def test_lookup_is_case_insensitive_like_every_other_id_here():
    """
    The save and the game's own tables disagree about capitalisation on real
    ids — measured on four of refworld's 645 item ids — so an exact match is how
    live data silently reads as unknown.
    """
    assert (gamedata.structure_capability("blastfurnace4")
            == gamedata.structure_capability("BlastFurnace4"))


def test_a_structure_with_no_capability_is_empty_not_an_error():
    """
    Only 48 of the game's ~1,000 build objects carry one, so absence is the
    ordinary case. It must not read as a lookup failure.
    """
    assert gamedata.structure_capability("GuildChest") == {}
    assert gamedata.structure_capability("") == {}
    assert gamedata.structure_capability("NoSuchStructureExists") == {}


def test_the_generator_range_spans_two_orders_of_magnitude():
    cap = "GenerateEnergyRateByWorker"
    assert gamedata.structure_capability("ManualElectricGenerator")[cap] == 0.2
    assert gamedata.structure_capability("AncientElectricGenerator")[cap] == 20.0


def test_it_refuses_to_claim_how_this_composes_with_a_work_rank():
    """
    THE REFUSAL. The structure's rate and the Pal's work-rank speed are two
    numbers from two files, and nothing states whether they multiply, add or
    gate each other. `buildplanner` and `palresist` hold the same line with
    `stackingKnown`.

    Asserted as a flag the client receives, not only as a docstring, because the
    client is the thing about to render a combined figure.
    """
    data = gamedata.build_capabilities()
    assert data["composesWithWorkRank"] is False


def test_it_says_nothing_about_what_a_container_accepts():
    """
    The class name reads like it might, and that reading is what
    `basesupply.py`'s refusal has been waiting on. It does not: there is no Feed
    Box row and no item-filter capability anywhere in the asset.

    Pinned as an absence so that a future bundle which *does* gain one cannot
    quietly reinstate the mechanic claim without someone deciding to.
    """
    structures = gamedata.build_capabilities()["structures"]
    assert not any("FoodBox" in s or "Chest" in s for s in structures)

    every_capability = {c for caps in structures.values() for c in caps}
    for token in ("Item", "Accept", "Filter", "Category", "Allow"):
        assert not any(token in c for c in every_capability), (
            f"a capability naming {token!r} appeared — check whether the Feed "
            "Box refusal in basesupply.py can now be narrowed, deliberately"
        )
