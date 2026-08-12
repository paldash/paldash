"""
`extract_work_assignments` against a real world.

Every join in this reader was verified before anything was built on it, and
these are those verifications, kept so a game update that moves a field fails
here rather than producing a confident wrong answer about which Pal is mining.

The figures are refworld's. What generalises is stated per test; a bare count is
a regression signal, not a rule — the lesson `verify-figures.py` exists for.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytestmark = [pytest.mark.integration, pytest.mark.slow]


@pytest.fixture(scope="module")
def world(request):
    """Parsed once — this costs a full 55 MB world parse."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    level = os.path.join(root, "refworld", "Level.sav")
    if not os.path.exists(level):
        pytest.skip("refworld/ not present — integration test skipped")
    try:
        import palsav  # noqa: F401
    except ImportError:
        pytest.skip("palsav not installed")
    import parser as pparser

    gvas = pparser.load_gvas(level)
    if gvas is None:
        pytest.skip("could not parse refworld")
    return gvas


@pytest.fixture(scope="module")
def jobs(world):
    import parser as pparser
    return pparser.extract_work_assignments(world)


def test_every_job_resolves_to_a_placed_structure(jobs, world):
    """
    `owner_map_object_model_id` -> `MapObjectSaveData[].Model.RawData
    .instance_id`, **160 of 160**.

    The extractor drops a job whose structure it cannot find, so the check that
    matters is that nothing was dropped: the count here must equal the number of
    entries in `WorkSaveData` itself.
    """
    import parser as pparser

    world_data = pparser._world_save_data(world)
    total = len(pparser._v(world_data, "WorkSaveData", "value", "values", default=[]))
    assert total == 160
    assert len(jobs) == total, "a job was dropped — the structure join has moved"
    assert all(j["structureId"] for j in jobs)


def test_map_object_id_is_a_name_not_a_guid(world):
    """
    THE TRAP. Joining on `MapObjectId` resolves **0 of 160** and reads as the
    field being wrong; it is a name like `DamagableRock0002`, and the GUID lives
    one level down in `Model.RawData`.
    """
    import parser as pparser

    world_data = pparser._world_save_data(world)
    objects = pparser._v(world_data, "MapObjectSaveData", "value", "values", default=[])
    names = {str(pparser._v(o, "MapObjectId", "value") or "") for o in objects}
    assert "DamagableRock0002" in names
    # A GUID has four dashes; these plainly do not.
    assert not any(n.count("-") == 4 for n in names)


def test_the_work_type_is_reached_two_ways_and_they_agree(jobs):
    """
    THE VERIFICATION THAT THE RECORD MEANS WHAT IT LOOKS LIKE.

    `assign_define_data_id`'s stem (`MonsterFarm_0` -> `MonsterFarm`) keys
    `DT_MapObjectAssignData` directly, and so does the structure reached by
    walking to the placed object. Two independent keys, and on every row where
    both resolve they give the **same** work. A misaligned read does not produce
    that.
    """
    import re

    import gamedata

    both = disagreed = 0
    for job in jobs:
        stem = re.sub(r"_\d+$", "", str(job.get("defineId") or ""))
        by_define = gamedata.work_assign(stem)
        by_structure = gamedata.work_assign(str(job.get("structureId") or ""))
        if by_define and by_structure:
            both += 1
            if by_define != by_structure:
                disagreed += 1
    assert both >= 150, "the two routes stopped resolving together"
    assert disagreed == 0


def test_an_assigned_pal_always_resolves_to_a_character(jobs, world):
    """
    An instance id that is in no `CharacterSaveParameterMap` entry is **dropped
    and counted**, never reported as a worker. So everything in `assigned` must
    resolve, by construction — this asserts the construction holds.
    """
    import parser as pparser

    world_data = pparser._world_save_data(world)
    known = {
        str(pparser._v(e, "key", "InstanceId", "value") or "").lower()
        for e in pparser._v(world_data, "CharacterSaveParameterMap", "value", default=[])
    }
    assigned = [a["instanceId"] for j in jobs for a in j["assigned"]]
    assert assigned, "the world has assignments to check"
    assert all(a in known for a in assigned)


def test_the_stale_assignment_is_counted_rather_than_hidden(jobs):
    """
    One of refworld's 60 assignments names a Pal that no longer exists — a Ranch
    slot pointing at something gone. It is a **real state**, so it is counted:
    dropping it silently would make the base read as merely under-staffed.
    """
    stale = sum(j["staleAssignments"] for j in jobs)
    assert stale == 1
    named = sum(len(j["assigned"]) for j in jobs)
    assert named == 59
    assert named + stale == 60


def test_the_job_outside_any_base_keeps_an_empty_base_id(jobs):
    """
    `base_camp_id_belong_to` resolves for 159 of 160. The exception is a
    `RepairBuildObject_0` on a world-placed chest carrying the all-zero GUID —
    a repair job outside any base, not a broken join. The zero must not travel
    as if it were a base id.
    """
    unbased = [j for j in jobs if not j["baseId"]]
    assert len(unbased) == 1
    assert unbased[0]["workableType"] == "Repair"
    assert all(not j["baseId"].startswith("00000000-0000") for j in jobs)


def test_fixed_positions_is_not_a_capacity(jobs):
    """
    THE CLAIM THE FIRST DRAFT MADE AND THE DATA REFUTED.

    `assign_locations` was documented as the worker capacity. **20 of the 160
    rows have more Pals assigned than positions** — the wandering job types have
    none at all, so a Ranch holding two reads 0.
    """
    over = [j for j in jobs if len(j["assigned"]) > j["fixedPositions"]]
    assert len(over) == 20
    wandering = [j for j in jobs
                 if j["workableType"] in ("MonsterFarm", "OnlyJoinAndWalkAround")]
    assert wandering
    assert all(j["fixedPositions"] == 0 for j in wandering)


def test_the_workable_types_are_the_six_the_world_has(jobs):
    """
    A regression signal rather than a rule — another world may have others. What
    would be wrong is an empty string, which means the enum prefix strip failed.
    """
    import collections

    seen = collections.Counter(j["workableType"] for j in jobs)
    assert "" not in seen
    assert set(seen) == {
        "Progress", "OnlyJoin", "OnlyJoinAndWalkAround",
        "MonsterFarm", "Booth", "Repair",
    }
    assert seen["Progress"] == 120


def test_no_pal_on_this_world_is_assigned_to_a_job_it_cannot_do(jobs, world):
    """
    The measured result, and it is a **negative**: zero `unsuitable`.

    Worth pinning because it says the reader agrees with the game rather than
    finding phantom problems — an early draft reported seven, all of them Pals
    on a Breeding Farm, because it read `Anyone` as a work type. The positive
    control that this check *can* fire is in `test_workassign.py`; without that
    counterpart this assertion would be worthless.
    """
    import parser as pparser
    import workassign

    _, pals = pparser.extract_characters(world)
    bases = pparser.extract_base_camps(world)
    summary = workassign.summarise(jobs, pals, bases)
    found = workassign.mismatches(summary)

    assert [m for m in found if m["kind"] == "unsuitable"] == []
    # ...and the summary did see real work, so this is not vacuous.
    assert summary["totalAssigned"] == 59
    assert sum(len(b["jobs"]) for b in summary["bases"]) == 159
