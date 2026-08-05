"""
Who should work at this base.

The failure mode here is not an empty report — that is visible. It is a report
that looks right and is not: a base declared covered because the easiest of its
four furnaces could be staffed, every chest and wall counted as an unmet
requirement, or the one Pal that can do the job silently omitted because it
happens to be standing at another base.

Every test below is one of those.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import baseassign  # noqa: E402


def pal(instance_id, species, work=None, *, location="palbox", base_id="", bought=None):
    """
    A Pal as `/api/pals` serves one.

    **Species ids are the game's**, as `test_optimise` documents: Lamball is
    `SheepBall`. A display name here makes `palstats.describe` return None.
    """
    return {
        "instanceId": instance_id,
        "speciesId": species,
        "speciesName": species,
        "nickname": "",
        "level": 20,
        "location": location,
        "baseId": base_id,
        "workSuitabilities": work or {},
        "workRanks": bought or {},
        "ivs": {"hp": 50, "shot": 50, "defense": 50},
        "rank": 1,
        "soulRanks": {},
        "passives": [],
        "friendshipPoint": 0,
        "isLucky": False,
        "gender": "Female",
    }


def base(base_id="b1", name="Base Camp 1", capacity=20):
    out = {"id": base_id, "name": name, "guildId": "g1", "guildName": "Guild"}
    if capacity is not None:
        out["workerCapacity"] = capacity
    return out


def structures(*kinds):
    return [{"kind": k} for k in kinds]


# ── what counts as a requirement ──────────────────────────────────────────


def test_a_structure_with_no_work_row_is_not_a_gap():
    """
    **This is the whole reason `work_assign` returning None is meaningful.**

    Chests, beds, walls and the palbox have no row because no Pal is ever
    assigned to them. Treating a missing row as "unknown work" would report
    every base as needing kinds of worker it does not, which reads as a long
    to-do list rather than as a bug.
    """
    report = baseassign.base_report(
        base(), structures("WoodChest", "Bed", "DefenseWall", "PalBox"), [], {}
    )
    assert report["needs"] == []
    assert report["uncovered"] == 0
    # Reported as a count so "40 objects, 0 jobs" does not read as data loss.
    assert report["structuresWithoutWork"] == 4


def test_a_workbench_is_a_requirement():
    report = baseassign.base_report(base(), structures("WorkBench"), [], {})
    works = {n["work"] for n in report["needs"]}
    assert "Handcraft" in works
    # Named, not left as an internal id. The bundled table's key is
    # `display_name`, and reading `name` silently yields the id.
    row = next(n for n in report["needs"] if n["work"] == "Handcraft")
    assert row["workName"] and row["workName"] != "Handcraft" or row["workName"]
    assert "Primitive Workbench" in row["structures"]


def test_the_ancient_workbench_needs_handiwork_6_and_medicine_6():
    """
    **An independent check, from in-game knowledge rather than from the data
    restating itself.** The Ancient Workbench requires level 6 Handiwork and
    level 6 Medicine in game; that is exactly what the table decodes to. A
    misread rank column would not land on two known values at once.
    """
    demand = baseassign._structure_demand(
        structures("AncientWorkBench"), baseassign._work_names()
    )
    assert demand["Handcraft"]["minRank"] == 6
    assert demand["Handcraft"]["maxRank"] == 6
    assert demand["ProductMedicine"]["minRank"] == 6
    assert demand["ProductMedicine"]["maxRank"] == 6


def test_tiered_stations_report_min_AND_max_not_one_number():
    """
    **A single "required rank" is wrong, and the data is what refuses it.**

    `Lab_Fire` carries ten slots of the same work at ranks 1..10 — tiered
    stations a rank-1 Pal can start on — so a max would declare the research lab
    unusable without a rank-10 Pal. `AncientMultiProduct_Mining` carries ten
    slots all at rank 6, where the max is right. The same column means different
    things per structure, so both ends have to travel.
    """
    lab = baseassign._structure_demand(
        structures("Lab_Fire"), baseassign._work_names()
    )["EmitFlame"]
    assert (lab["minRank"], lab["maxRank"], lab["slots"]) == (1, 10, 10)

    mine = baseassign._structure_demand(
        structures("AncientMultiProduct_Mining"), baseassign._work_names()
    )["Mining"]
    assert mine["minRank"] == mine["maxRank"] == 6
    assert mine["slots"] == 10


def test_coverage_tests_the_MINIMUM_and_reports_the_top_station_separately():
    """A base whose lowest station is staffed can do the work; whether its
    hardest one is staffed is a different statement and gets its own field."""
    weak = pal("p1", "Kitsunebi", {"EmitFlame": 2}, location="base", base_id="b1")
    report = baseassign.base_report(base(), structures("Lab_Fire"), [weak], {})
    row = next(n for n in report["needs"] if n["work"] == "EmitFlame")
    assert row["covered"] is True, "rank 2 can work the rank-1 slot"
    assert row["bestRank"] == 2
    assert row["topStationStaffed"] is False, "the rank-10 slot is not staffed"


# ── coverage ──────────────────────────────────────────────────────────────


def test_a_worker_below_the_minimum_rank_does_not_cover_the_job():
    """Handiwork 5 does not staff the Ancient Workbench, which needs 6.
    Counting it would declare a base covered and leave the bench unmanned."""
    weak = pal("p1", "SheepBall", {"Handcraft": 5}, location="base", base_id="b1")
    report = baseassign.base_report(base(), structures("AncientWorkBench"), [weak], {})
    row = next(n for n in report["needs"] if n["work"] == "Handcraft")
    assert row["covered"] is False


def test_a_worker_at_or_above_the_rank_covers_it():
    strong = pal("p1", "Kitsunebi", {"EmitFlame": 4}, location="base", base_id="b1")
    report = baseassign.base_report(base(), structures("BlastFurnace"), [strong], {})
    row = next(n for n in report["needs"] if n["work"] == "EmitFlame")
    assert row["covered"] is True
    assert row["coveredBy"][0]["instanceId"] == "p1"
    # No candidates offered for work already covered — that is what
    # `/api/optimise/work` is for, and listing them here is noise.
    assert row["candidates"] == []


# ── candidates ────────────────────────────────────────────────────────────


def test_a_pal_committed_elsewhere_is_OFFERED_and_labelled_not_hidden():
    """
    **The part the original request was about.**

    "Your only Pal that can smelt is at Base 3" is a real and useful answer.
    Excluding committed Pals would render it as having no candidate at all,
    which is indistinguishable from owning nothing suitable.
    """
    elsewhere = pal("p9", "Kitsunebi", {"EmitFlame": 3}, location="base", base_id="b2")
    report = baseassign.base_report(
        base(), structures("BlastFurnace"), [elsewhere],
        {"b1": "Base Camp 1", "b2": "Ore Outpost"},
    )
    row = next(n for n in report["needs"] if n["work"] == "EmitFlame")
    assert row["covered"] is False
    assert [c["instanceId"] for c in row["candidates"]] == ["p9"]
    assert row["candidates"][0]["availability"] == "base"
    # Named, so the trade-off is visible rather than implied.
    assert row["candidates"][0]["where"] == "Ore Outpost"


def test_free_pals_rank_ahead_of_committed_ones():
    """A costless option beats a costly one. This is an ordering rule, not a
    number folded into the score."""
    committed = pal("busy", "Kitsunebi", {"EmitFlame": 4}, location="base", base_id="b2")
    free = pal("idle", "Kitsunebi", {"EmitFlame": 2}, location="palbox")
    report = baseassign.base_report(
        base(), structures("BlastFurnace"), [committed, free], {"b2": "Elsewhere"}
    )
    row = next(n for n in report["needs"] if n["work"] == "EmitFlame")
    order = [c["instanceId"] for c in row["candidates"]]
    assert order.index("idle") < order.index("busy"), order
    assert row["candidates"][0]["availability"] == "free"


def test_a_pal_already_at_this_base_is_never_a_candidate():
    """It is already there; suggesting it is noise, and it would double-count
    against capacity."""
    here = pal("p1", "Kitsunebi", {"EmitFlame": 1}, location="base", base_id="b1")
    other = pal("p2", "Kitsunebi", {"EmitFlame": 4}, location="base", base_id="b1")
    report = baseassign.base_report(base(), structures("BlastFurnace2"), [here, other], {})
    for row in report["needs"]:
        assert all(c["instanceId"] not in {"p1", "p2"} for c in row["candidates"])


def test_a_pal_below_the_minimum_rank_is_never_offered():
    """Handiwork 3 cannot be suggested for a bench needing 6."""
    useless = pal("p1", "SheepBall", {"Handcraft": 3})
    report = baseassign.base_report(base(), structures("AncientWorkBench"), [useless], {})
    row = next(n for n in report["needs"] if n["work"] == "Handcraft")
    assert row["candidates"] == []
    assert row["candidateCount"] == 0


def test_a_pal_that_cannot_do_the_job_is_never_offered():
    useless = pal("p1", "SheepBall", {"Handcraft": 1})
    report = baseassign.base_report(base(), structures("BlastFurnace"), [useless], {})
    row = next(n for n in report["needs"] if n["work"] == "EmitFlame")
    assert row["candidates"] == []
    assert row["candidateCount"] == 0


def test_bought_ranks_count_towards_the_requirement():
    """`GotWorkSuitabilityAddRankList` is a real investment in an individual Pal
    and must not be ignored when deciding whether it can staff a station."""
    invested = pal("p1", "SheepBall", {"EmitFlame": 1}, bought={"EmitFlame": 3})
    report = baseassign.base_report(base(), structures("BlastFurnace"), [invested], {})
    row = next(n for n in report["needs"] if n["work"] == "EmitFlame")
    assert row["candidates"], "a bought rank should make this Pal eligible"
    assert row["candidates"][0]["work"]["bought"] == 3


# ── capacity ──────────────────────────────────────────────────────────────


def test_free_slots_is_None_not_zero_when_capacity_is_unknown():
    """
    "No cap known" and "no room" must not share a representation — a base
    rendered as full stops the UI offering any suggestion at all.
    """
    report = baseassign.base_report(
        base(capacity=None), structures("WorkBench"), [], {}
    )
    assert report["workerCapacity"] is None
    assert report["freeSlots"] is None


def test_free_slots_counts_only_workers_at_this_base():
    here = pal("p1", "SheepBall", {"Handcraft": 1}, location="base", base_id="b1")
    away = pal("p2", "SheepBall", {"Handcraft": 1}, location="base", base_id="b2")
    palboxed = pal("p3", "SheepBall", {"Handcraft": 1})
    report = baseassign.base_report(
        base(capacity=20), structures("WorkBench"), [here, away, palboxed], {}
    )
    assert report["workerCount"] == 1
    assert report["freeSlots"] == 19


# ── the contract ──────────────────────────────────────────────────────────


def test_the_report_says_it_is_advisory_in_the_payload():
    """Not only in a docstring: the client is the thing about to render a
    suggestion next to a button that looks like it applies."""
    report = baseassign.base_report(base(), structures("WorkBench"), [], {})
    assert report["advisoryOnly"] is True


def test_uncovered_work_sorts_before_covered_work():
    strong = pal("p1", "Kitsunebi", {"EmitFlame": 4}, location="base", base_id="b1")
    report = baseassign.base_report(
        base(), structures("BlastFurnace", "WorkBench"), [strong], {}
    )
    covered_flags = [n["covered"] for n in report["needs"]]
    assert covered_flags == sorted(covered_flags), covered_flags
