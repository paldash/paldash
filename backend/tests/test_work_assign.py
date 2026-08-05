"""
Which work each structure needs.

THE TEST THAT MATTERS IS `test_the_structures_absent_are_exactly_the_unworkable_ones`.
Everything else here checks that the bundle says what the extractor intended,
which is a tautology dressed as a test — the extractor wrote it. The refworld
check is different in kind: it compares the table against a world this code has
never read, and the thing it asserts (that the 19 unlisted structures are chests,
beds, the palbox, the spa, walls and food boxes) is not something a bad
extraction could arrange.

This mapping was twice documented as absent from every game file. It was in
`DT_MapObjectAssignData` the whole time, a sibling of the table both refusals
checked. `docs/GAMEDATA-SOURCES.md` exists because of that.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gamedata  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh():
    gamedata._reset_cache()
    yield
    gamedata._reset_cache()


# ─── The bundle loaded at all ────────────────────────────


def test_the_bundle_is_present():
    assert gamedata.work_assign_available() is True


def test_a_missing_bundle_is_distinguishable_from_an_unlisted_structure(monkeypatch):
    """
    "This structure needs no work" and "the bundle failed to load" must not
    share a representation — the `.catch(() => [])` lesson.
    """
    monkeypatch.setattr(gamedata, "WORK_ASSIGN_PATH", "/nonexistent/work.json.gz")
    gamedata._reset_cache()
    assert gamedata.work_assign("CopperPit") is None
    assert gamedata.work_assign_available() is False


# ─── What a structure needs ──────────────────────────────


def test_a_quarry_needs_mining():
    entry = gamedata.work_assign("CopperPit")
    assert entry is not None
    assert [s["work"] for s in entry["slots"]] == ["Mining"]
    assert entry["baseWorkerWorkable"] is True


def test_the_minimum_rank_is_carried_and_is_not_always_one():
    """
    `WorkSuitabilityRank` is what turns "who is best at mining" into "who can
    work THIS node". A rank-1 miner cannot touch a rank-3 rock.
    """
    hard = gamedata.work_assign("DamagableRock0004")
    assert hard is not None
    assert hard["slots"][0]["requiredRank"] == 3

    easy = gamedata.work_assign("DamagableRock0009")
    assert easy["slots"][0]["requiredRank"] == 1


def test_a_farm_plot_needs_three_different_kinds_of_work():
    """
    The trap this bundle is shaped around: a structure can have several slots,
    and reading only the first looks complete while answering a third of the
    question.
    """
    entry = gamedata.work_assign("FarmBlockV2_wheet")
    assert entry is not None
    assert {s["work"] for s in entry["slots"]} == {"Seeding", "Watering", "Collection"}


def test_ancient_benches_need_two_work_types_at_once():
    """`MultiWorkSuitability1` — three structures in the game use it."""
    entry = gamedata.work_assign("AncientBlastFurnace")
    assert entry is not None
    assert {s["work"] for s in entry["slots"]} == {"EmitFlame", "Cool"}


def test_lookup_is_case_insensitive_because_the_save_disagrees_with_the_table():
    """
    The save spells it `Workbench`; the table says `WorkBench`. An exact match
    silently loses a real structure, which is the same reason every other
    `gamedata` lookup folds case.
    """
    assert gamedata.work_assign("Workbench") is not None
    assert gamedata.work_assign("WORKBENCH") is not None
    assert gamedata.work_assign("WorkBench") is not None


def test_the_breeding_farm_takes_any_pal_and_drains_no_sanity():
    entry = gamedata.work_assign("BreedFarm")
    assert entry["slots"][0]["work"] == "Anyone"
    assert entry["slots"][0]["sanityPerTick"] == 0.0


def test_mining_drains_sanity_and_that_number_is_negative():
    slot = gamedata.work_assign("CopperPit")["slots"][0]
    assert slot["sanityPerTick"] < 0


def test_worker_max_zero_is_flagged_rather_than_read_as_a_capacity():
    """
    178 of 271 rows carry `WorkerMaxNum: 0`, including quarries that obviously
    take workers. Rendering that as "0/0 assigned" would be confidently wrong,
    so the unset case is marked rather than normalised away.
    """
    quarry = gamedata.work_assign("StonePit")["slots"][0]
    assert quarry["workerMax"] == 0
    assert quarry["workerMaxIsUnset"] is True

    generator = gamedata.work_assign("ElectricGenerator")["slots"][0]
    assert generator["workerMax"] == 1
    assert generator["workerMaxIsUnset"] is False


def test_a_structure_nobody_works_is_not_in_the_table():
    for unworkable in ("ItemChest_02", "PalBoxV2", "GuildChest", "DefenseWall_Wood"):
        assert gamedata.work_assign(unworkable) is None


# ─── The check the extractor cannot fake ─────────────────


@pytest.mark.integration
def test_the_structures_absent_are_exactly_the_unworkable_ones(level_sav, palsav_available):
    """
    Read the reference world, list every base-placed structure kind, and split it
    on whether the table knows it.

    The assertion is not "coverage is high" — it is that **everything the table
    omits is something no Pal is ever assigned to**. A mis-parsed table would put
    a quarry or a furnace on the wrong side of that line. Measured: 63 kinds, 44
    in the table, 19 out.
    """
    from parser import extract_map_objects, load_gvas

    gvas = load_gvas(level_sav)
    kinds = {o["kind"] for o in extract_map_objects(gvas) if o["baseCampId"]}

    listed = {k for k in kinds if gamedata.work_assign(k)}
    absent = kinds - listed

    assert listed, "no base structure resolved — the bundle or the join is broken"

    # Everything absent must be storage, comfort, defence or the palbox. These
    # are the categories a Pal is never assigned to work at.
    import re
    allowed_absent = re.compile(
        r"Chest|PalBox|Bed|Spa|Wall|FoodBox|MedicineBox|Statue|DropItem|PalEgg",
        re.I,
    )
    surprises = sorted(k for k in absent if not allowed_absent.search(k))
    assert surprises == [], (
        f"structures missing from DT_MapObjectAssignData that look workable: "
        f"{surprises}"
    )

    # And the converse: nothing listed should be a chest or a bed.
    misfiled = sorted(k for k in listed if re.search(r"^ItemChest|^PalBoxV2", k))
    assert misfiled == []
