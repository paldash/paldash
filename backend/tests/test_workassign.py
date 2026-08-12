"""
`WorkSaveData` — the game's own record of who works where.

The integration half is in `test_workassign_world.py`; this file is the policy,
and the policy is mostly about **what must not be called a problem**.

THE POSITIVE CONTROL IS THE LOAD-BEARING TEST HERE. `mismatches` finds zero
`unsuitable` assignments on the reference world, which is a fine result and an
indistinguishable one from a checker that can never fire. So a synthetic
rank-0 assignment is constructed and must be caught.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import workassign  # noqa: E402


def _job(structure_id, assigned, base_id="base-1", needs=None):
    return {
        "workId": f"w-{structure_id}",
        "baseId": base_id,
        "structureId": structure_id,
        "structureName": structure_id,
        "defineId": f"{structure_id}_0",
        "workableType": "Progress",
        "assigned": [{"instanceId": i, "state": 3, "fixed": False} for i in assigned],
        "staleAssignments": 0,
        "fixedPositions": 1,
    }


def _pal(instance_id, species, name=""):
    return {"instanceId": instance_id, "speciesId": species,
            "nickname": name, "level": 20, "workRanks": []}


BASES = [{"id": "base-1", "name": "Base Camp 1"}]


def test_a_rank_zero_worker_is_caught():
    """
    THE POSITIVE CONTROL. `Alpaca` (Melpaca) has no Handiwork suitability, so
    putting it on a workbench is a real mismatch. Without this, "zero mismatches
    on refworld" would be unfalsifiable.
    """
    work = [_job("WorkBench", ["pal-1"])]
    pals = [_pal("pal-1", "Alpaca", "Woolly")]
    found = workassign.mismatches(workassign.summarise(work, pals, BASES))
    unsuitable = [m for m in found if m["kind"] == "unsuitable"]
    assert len(unsuitable) == 1
    assert unsuitable[0]["instanceId"] == "pal-1"
    assert unsuitable[0]["structureName"] == "WorkBench"


def test_a_capable_worker_is_not_caught():
    """The other side of the control — the check must discriminate."""
    work = [_job("WorkBench", ["pal-1"])]
    # Anubis is the game's canonical Handiwork Pal (base 6).
    pals = [_pal("pal-1", "Anubis")]
    found = workassign.mismatches(workassign.summarise(work, pals, BASES))
    assert [m for m in found if m["kind"] == "unsuitable"] == []


def test_anyone_is_not_a_work_type_and_jetragon_can_breed():
    """
    THE BUG THIS MODULE SHIPPED WITH FOR ONE COMMIT'S WORTH OF DRAFT.

    `EPalWorkSuitability::Anyone` is the game's pseudo-suitability meaning no
    suitability is required, and **0 of the 753 species carry a rank in it**.
    Reading it as an ordinary work type made every Pal rank 0, which flagged all
    seven Pals on refworld's Breeding Farms — Jetragon among them — as unfit.
    """
    work = [_job("BreedFarm", ["pal-1", "pal-2"])]
    pals = [_pal("pal-1", "JetDragon"), _pal("pal-2", "Manticore")]
    summary = workassign.summarise(work, pals, BASES)
    job = summary["bases"][0]["jobs"][0]

    assert workassign.ANY_PAL in job["needs"], "the requirement is still reported"
    assert job["anyPalQualifies"] is True
    # ...and it is not turned into a suitability the Pal fails.
    assert job["assigned"][0]["workLevels"] == {}
    assert [m for m in workassign.mismatches(summary) if m["kind"] == "unsuitable"] == []


def test_an_unknown_character_is_skipped_not_flagged():
    """
    99 of the reference world's 1,905 characters are NPCs with no work table at
    all. `palcheck` treats an unrecognised species as an advisory and never as
    cheating; the same restraint applies here, or a merchant standing anywhere
    becomes "assigned to a job it cannot do".
    """
    work = [_job("WorkBench", ["pal-1"])]
    pals = [_pal("pal-1", "NoSuchSpeciesAnywhere")]
    summary = workassign.summarise(work, pals, BASES)
    assert summary["bases"][0]["jobs"][0]["assigned"][0]["workLevels"] == {
        "Handcraft": None
    }
    assert [m for m in workassign.mismatches(summary) if m["kind"] == "unsuitable"] == []


def test_a_pal_good_at_one_of_three_jobs_is_not_a_mismatch():
    """
    A farm plot needs Seeding, Watering and Collection. A Pal that can water is
    doing useful work there; requiring all three would flag most of a real base.
    """
    work = [_job("FarmBlockV2_Berries", ["pal-1"])]
    # Nyafia: Collection 4, and the game leaves Seeding/Watering unset. That
    # `None`-for-absent shape is why `_work_level` distinguishes "no table at
    # all" (None) from "a table with no rank in this work" (0) — an earlier
    # fixture guessed a species id that does not exist and every level came
    # back None, which the guard below caught.
    pals = [_pal("pal-1", "BadCatGirl")]
    summary = workassign.summarise(work, pals, BASES)
    levels = summary["bases"][0]["jobs"][0]["assigned"][0]["workLevels"]
    assert set(levels) == {"Seeding", "Watering", "Collection"}
    assert any(levels.values()), "fixture must have at least one usable rank"
    assert [m for m in workassign.mismatches(summary) if m["kind"] == "unsuitable"] == []


def test_a_structure_with_no_row_is_not_a_mismatch():
    """
    Chests, beds, the palbox and the spa are in no `DT_MapObjectAssignData` row.
    That is the table saying nobody is ever assigned to them, not a gap —
    `gamedata.work_assign` documents the same thing.
    """
    work = [_job("ItemChest", [])]
    summary = workassign.summarise(work, [], BASES)
    assert summary["bases"][0]["jobs"][0]["needs"] == []
    assert workassign.mismatches(summary) == []


def test_an_empty_job_is_reported():
    work = [_job("WorkBench", [])]
    found = workassign.mismatches(workassign.summarise(work, [], BASES))
    assert [m["kind"] for m in found] == ["empty"]
    assert found[0]["structureName"] == "WorkBench"


def test_a_base_with_no_jobs_still_appears():
    """
    "Nobody is working here" is an answer. Dropping the row makes it
    indistinguishable from a base this failed to read — the `.catch(() => [])`
    lesson, which this project has now recorded three times.
    """
    summary = workassign.summarise([], [], BASES)
    assert len(summary["bases"]) == 1
    assert summary["bases"][0]["jobs"] == []
    assert summary["totalAssigned"] == 0


def test_a_pal_outside_scope_is_counted_but_not_named():
    """
    Privacy is decided before this runs. A job whose worker the caller may not
    see must not read as empty — that would be a base looking under-staffed
    because of somebody else's privacy setting.
    """
    work = [_job("WorkBench", ["pal-hidden"])]
    summary = workassign.summarise(work, [], BASES)
    job = summary["bases"][0]["jobs"][0]
    assert job["assigned"] == []
    # The world-level total still counts it, which is what says the slot is full.
    assert summary["totalAssigned"] == 1


def test_an_unbased_job_does_not_land_under_a_base():
    """
    refworld has exactly one: a `RepairBuildObject_0` on a world-placed chest,
    carrying the all-zero base GUID. It belongs to no base and must not be
    filed under one.
    """
    work = [_job("ItemChest_03", [], base_id="")]
    summary = workassign.summarise(work, [], BASES)
    assert summary["bases"][0]["jobs"] == []
    assert len(summary["unbased"]) == 1


def test_the_state_integer_is_never_named():
    """
    The game names these nowhere this project can read, and inventing a legend
    from an observed distribution is the `icon_type` mistake. The payload says
    so, because the client is the thing about to draw one.
    """
    summary = workassign.summarise([_job("WorkBench", ["pal-1"])],
                                   [_pal("pal-1", "Anubis")], BASES)
    assert summary["stateIsUnnamed"] is True
    assert summary["bases"][0]["jobs"][0]["assigned"][0]["state"] == 3


def test_wandering_jobs_are_marked():
    """
    `fixedPositions` is 0 on a Ranch holding four Pals, because those jobs have
    no standing positions at all. Without the flag that reads as a capacity of
    zero — which is what the first draft of the extractor called it.
    """
    job = _job("MonsterFarm", ["pal-1"])
    job["workableType"] = "MonsterFarm"
    job["fixedPositions"] = 0
    summary = workassign.summarise([job], [_pal("pal-1", "Alpaca")], BASES)
    assert summary["bases"][0]["jobs"][0]["wanders"] is True


@pytest.mark.parametrize("work_type", sorted(workassign.WANDERING_TYPES))
def test_every_wandering_type_is_recognised(work_type):
    job = _job("MonsterFarm", [])
    job["workableType"] = work_type
    summary = workassign.summarise([job], [], BASES)
    assert summary["bases"][0]["jobs"][0]["wanders"] is True
