"""
Work, combat and element rankings.

The load-bearing test in here is `test_matchup_never_enters_the_ordering`. The
element chart is a hand-entered *relation* with no multiplier anywhere — the
game's settings object holds exactly one element-damage constant
(`DamageElementMatchRate = 1.2`, meaning inferred from its name) and no halving
or resist counterpart, so the popular "2x dealt, 1/2 taken" is reproduced by no
file this project can read.

An optimiser that folded a matchup into a score would therefore be sorting on a
coefficient nobody has, and the result would look more authoritative than the
data behind it. `elements.py` is quarantined precisely to stop that leaking; this
is the same guard one level up.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import elements  # noqa: E402
import optimise  # noqa: E402


def pal(**kw):
    """
    A minimally complete Pal record, as `/api/pals` serves one.

    **The ids are the game's, not the player's.** Lamball is `SheepBall`,
    Foxparks is `Kitsunebi`, Pengullet is `Penguin` — writing the display name
    here makes `palstats.describe` return None and every combat row vanish, which
    is the same trap `gamedata`'s case-insensitive lookups exist for one level
    down. It cost a test run to find, so it is written down.
    """
    base = {
        "instanceId": "id-1",
        "characterId": "SheepBall",
        "speciesId": "SheepBall",
        "speciesName": "Lamball",
        "nickname": "",
        "level": 20,
        "rank": 1,
        "gender": "Female",
        "ivs": {"hp": 50, "shot": 50, "defense": 50},
        "soulRanks": {},
        "passiveSkills": [],
        "elements": ["Neutral"],
        "workSuitabilities": {},
        "workRanks": None,
    }
    base.update(kw)
    return base


# ─── Work levels: read, not derived ──────────────────────


def test_species_level_and_bought_rank_are_kept_apart():
    """
    "This species is good at mining" and "somebody spent Pal Souls on this one"
    are different facts, and a single number hides which.
    """
    p = pal(workSuitabilities={"Mining": 2}, workRanks={"Mining": 3})
    assert optimise.work_level(p, "Mining") == {"base": 2, "bought": 3, "level": 5}


def test_absent_work_ranks_read_as_zero_not_as_an_error():
    """
    `workRanks` is None when the property is absent, which is the common case —
    1,563 of the reference world's 1,905 Pals do not carry it.
    """
    p = pal(workSuitabilities={"Mining": 2}, workRanks=None)
    assert optimise.work_level(p, "Mining")["level"] == 2
    assert optimise.work_level(pal(), "Mining")["level"] == 0


def test_pals_that_cannot_do_the_job_are_excluded_not_ranked_last():
    can = pal(instanceId="a", workSuitabilities={"Mining": 3})
    cannot = pal(instanceId="b", workSuitabilities={"Watering": 3})
    rows = optimise.rank_for_work([can, cannot], "Mining")
    assert [r["instanceId"] for r in rows] == ["a"]


def test_level_beats_speed_because_speed_cannot_substitute_for_it():
    """
    A Pal with no suitability cannot do the job at any speed, so speed is a
    tie-break rather than a competing axis.
    """
    low_level_fast = pal(instanceId="fast", characterId="Kitsunebi",
                         speciesId="Kitsunebi", rank=5,
                         workSuitabilities={"Mining": 1})
    high_level_slow = pal(instanceId="slow", workSuitabilities={"Mining": 4})
    rows = optimise.rank_for_work([low_level_fast, high_level_slow], "Mining")
    assert [r["instanceId"] for r in rows] == ["slow", "fast"]


def test_work_speed_is_flagged_as_calculated_per_row():
    rows = optimise.rank_for_work([pal(workSuitabilities={"Mining": 1})], "Mining")
    assert rows[0]["workSpeedCalculated"] is True


def test_every_bundled_work_type_is_offered():
    ids = {t["id"] for t in optimise.work_types()}
    # The thirteen the game ships. If this changes, the fallback list in
    # `src/lib/work-types.ts` needs the same change.
    assert "Mining" in ids and "Handcraft" in ids and "OilExtraction" in ids
    assert len(ids) == 13


# ─── Combat: stats only ──────────────────────────────────


def test_npcs_are_absent_rather_than_zeroed():
    """
    No scaling data exists for the humans sharing CharacterSaveParameterMap with
    the Pals, so a breakdown of zeroes would show confident stats for a merchant.
    """
    rows = optimise.rank_for_combat([pal(characterId="Male_Soldier")])
    assert rows == []


def test_the_composite_score_says_it_is_arbitrary():
    rows = optimise.rank_for_combat([pal()])
    assert rows[0]["scoreIsArbitrary"] is True
    assert rows[0]["calculated"] is True
    # Every component travels so a caller can sort on what it cares about.
    assert {"attack", "defense", "hp"} <= set(rows[0])


def test_matchup_never_enters_the_ordering():
    """
    THE GUARD. Same list, ranked with and without a target element: the order
    must be identical. There is no multiplier to rank by, so any difference here
    means one was invented.
    """
    roster = [
        pal(instanceId="a", characterId="Kitsunebi", speciesId="Kitsunebi",
            elements=["Fire"]),
        pal(instanceId="b", characterId="Penguin", speciesId="Penguin",
            elements=["Water", "Ice"]),
        pal(instanceId="c", elements=["Neutral"]),
    ]
    plain = [r["instanceId"] for r in optimise.rank_for_combat(roster)]
    # Grass is weak to Fire and strong against nothing here, so "a" would be
    # promoted by any effectiveness weighting.
    against = [r["instanceId"] for r in optimise.rank_for_combat(roster, against=["Grass"])]
    assert plain == against

    matchups = {
        r["instanceId"]: r["matchup"]
        for r in optimise.rank_for_combat(roster, against=["Grass"])
    }
    # The flag is present and correct — it is attached, just not sorted on.
    assert matchups["a"] == "strong"


def test_no_ranking_row_carries_a_damage_number():
    """
    Nothing in a row may look like a damage figure. If a multiplier is ever
    sourced, this is a deliberate change rather than an accident.
    """
    rows = optimise.rank_for_combat([pal()], against=["Grass"])
    forbidden = {"multiplier", "damage", "effectiveness", "matchRate", "dps"}
    assert not (forbidden & set(rows[0]))


# ─── Counters: sets, not an ordering ─────────────────────


def test_counters_partition_the_roster():
    roster = [
        pal(instanceId="fire", characterId="Kitsunebi", speciesId="Kitsunebi",
            elements=["Fire"]),
        pal(instanceId="water", characterId="Penguin", speciesId="Penguin",
            elements=["Water"]),
        pal(instanceId="plain", elements=["Neutral"]),
    ]
    out = optimise.counters(roster, ["Grass"])
    assert [p["instanceId"] for p in out["strong"]] == ["fire"]
    # Grass is strong against Ground and weak to Fire; Water and Neutral are
    # neither, which is a real answer rather than missing data.
    assert {p["instanceId"] for p in out["neutral"]} == {"water", "plain"}
    assert out["strong"] and not out["weak"]


def test_counters_declare_that_there_is_no_multiplier():
    out = optimise.counters([pal()], ["Fire"])
    assert out["hasMultiplier"] is False


def test_counters_report_chart_staleness():
    """
    A content update adding a tenth element makes every matchup involving it read
    as a confident "neutral". `unknown_to_chart` is the only thing that can say
    so, and empty is the healthy state.
    """
    out = optimise.counters([pal()], ["Fire"])
    assert out["chartIsCurrent"] == elements.chart_is_current()
    assert out["unknownElements"] == list(elements.unknown_to_chart())


# ─── Identity ────────────────────────────────────────────


def test_rows_carry_enough_to_tell_two_of_a_species_apart():
    """
    A player usually owns several of the same species at the same level, so a
    row has to differ by more than name and level.
    """
    row = optimise.rank_for_combat([pal()])[0]
    for field in ("instanceId", "level", "rank", "gender", "isBoss", "location"):
        assert field in row


def test_limit_is_honoured_and_zero_means_everything():
    roster = [pal(instanceId=str(i), workSuitabilities={"Mining": 1}) for i in range(30)]
    assert len(optimise.rank_for_work(roster, "Mining", limit=5)) == 5
    assert len(optimise.rank_for_work(roster, "Mining", limit=0)) == 30
