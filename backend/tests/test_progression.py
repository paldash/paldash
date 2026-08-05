"""
Relic ranks, quest positions, regions and dungeons.

The two tests that matter are about a pair of adjacent columns that work in
opposite directions — `RequiredRelicNum` is a per-rank cost and `EffectRate` is
a cumulative total. Reading either the other way produces a confident wrong
number rather than an error, and I got both backwards on the first pass.
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


# ─── The two columns ─────────────────────────────────────


def test_relic_cost_is_per_rank_and_is_summed_to_find_the_current_rank():
    """
    `HungerReduction` charges 1 relic for each of its 20 ranks. Read as a
    cumulative threshold, rank 20 would cost a single relic — so 5 spent must
    give rank 5, not rank 20.
    """
    assert gamedata.relic_rank("HungerReduction", 5)["rank"] == 5
    assert gamedata.relic_rank("HungerReduction", 20)["rank"] == 20
    assert gamedata.relic_rank("HungerReduction", 0)["rank"] == 0

    # MoveSpeed's costs are 1 then 3s, so 10 relics buys four ranks (1+3+3+3).
    assert gamedata.relic_rank("MoveSpeed", 10)["rank"] == 4


def test_effect_rate_is_already_cumulative():
    """
    2.5, 5.0, 7.5 across HungerReduction's first three ranks — the total at that
    rank. Summing it would triple the answer.
    """
    assert gamedata.relic_rank("HungerReduction", 1)["effectRate"] == 2.5
    assert gamedata.relic_rank("HungerReduction", 2)["effectRate"] == 5.0
    assert gamedata.relic_rank("HungerReduction", 20)["effectRate"] == 50.0


def test_capture_power_has_no_rate_and_says_so():
    """
    All 15 CapturePower ranks are 0.0 while the other twelve types carry real
    values. Its effect lives somewhere other than this column, so a UI must not
    render "+0%" — and it can tell, because `hasEffectRate` is False.
    """
    capture = gamedata.relic_rank("CapturePower", 3)
    assert capture["hasEffectRate"] is False
    assert capture["effectRate"] == 0.0

    hunger = gamedata.relic_rank("HungerReduction", 3)
    assert hunger["hasEffectRate"] is True


def test_it_says_what_the_next_rank_costs_and_what_maxing_takes():
    row = gamedata.relic_rank("MoveSpeed", 10)
    assert row["nextRank"] == 5
    assert row["relicsToNext"] == 3
    # 92 ranks: 1, then 3 x 78, then 4 x 13.
    assert row["totalToMax"] == 287


def test_a_maxed_line_reports_no_next_rank():
    maxed = gamedata.relic_rank("HungerReduction", 20)
    assert maxed["nextRank"] is None
    assert maxed["relicsToNext"] == 0


def test_an_unknown_relic_type_is_none():
    assert gamedata.relic_rank("NotAThing", 5) is None


def test_all_thirteen_relic_lines_are_present():
    assert len(gamedata.relic_types()) == 13
    assert "CapturePower" in gamedata.relic_types()


# ─── Quests, regions, dungeons ───────────────────────────


def test_quests_carry_world_positions():
    quest = gamedata.quest_location("Main_UnlockFastTravel")
    assert quest is not None
    assert {"x", "y", "z", "range"} <= set(quest)


def test_a_range_of_minus_one_means_no_radius():
    """Most quests have no radius; -1 is the game's way of saying so."""
    ranges = [q["range"] for q in gamedata.progression()["quests"].values()]
    assert -1.0 in ranges


def test_regions_are_ids_not_display_names():
    """
    `MsgID` is a localisation key (`REGION_Desert_1`). Humanising it here would
    create a second source of truth against the text tables — but 123 of them is
    still enough to turn `areasFound`'s opaque flag map into "47 of 123".
    """
    areas = gamedata.area_ids()
    assert len(areas) == 123
    assert areas["Desert_001"].startswith("REGION_")


def test_dungeons_carry_their_layouts_where_the_game_has_them():
    dungeons = gamedata.progression()["dungeons"]
    assert len(dungeons) == 23
    # Only 15 layout rows exist across all 23 areas; the rest keep an empty list
    # rather than being dropped.
    with_levels = [d for d in dungeons.values() if d["levels"]]
    assert 0 < len(with_levels) < len(dungeons)


def test_a_missing_bundle_costs_the_panel_not_the_page(monkeypatch):
    monkeypatch.setattr(gamedata, "PROGRESSION_PATH", "/nonexistent/p.json.gz")
    gamedata._reset_cache()
    assert gamedata.progression() == {}
    assert gamedata.relic_rank("HungerReduction", 5) is None
    assert gamedata.area_ids() == {}
    assert gamedata.quest_location("x") is None


# ── relic line names ──────────────────────────────────────────────────────


def test_every_relic_line_carries_the_games_own_name():
    """
    The table gives only an internal `RelicType`, so a panel could have offered
    "StatusAilmentResist". Names come from the client pak's
    `BUILDUP_PLAYER_STATUS_NN`, with descriptions beside them.
    """
    meta = gamedata.progression().get("relicTypes") or {}
    assert len(meta) == 13
    for kind, row in meta.items():
        assert row["name"], kind
        assert row["nameIsInternal"] is False, kind
        assert row["description"], kind


def test_the_positional_join_is_anchored_not_assumed():
    """
    **`BUILDUP_PLAYER_STATUS_NN` is indexed, not keyed**, so pairing it with the
    table is positional — the kind of join this project refuses when it cannot
    be checked. It is acceptable here because it *is* checked: these four are
    distinctive and spread across the range, so an off-by-one breaks at least
    one. `extract-progression.py` refuses the build if any drift.
    """
    meta = gamedata.progression()["relicTypes"]
    assert meta["CapturePower"]["name"] == "Capture Power"
    assert meta["StaminaReduction"]["name"] == "Endurance"
    assert meta["SphereHoming"]["name"] == "Sphere Tracking"
    assert meta["MoveSpeed"]["name"] == "Movement Speed"


def test_names_are_not_derivable_from_the_ids_which_is_why_they_are_looked_up():
    """
    Half of these could not be humanised into the right words. `HungerReduction`
    is "Satiety Duration", `GliderSpeed` is "Flight Capacity",
    `StaminaReduction` is "Endurance", `RainbowPassiveRate` is "Rainbow
    Fortune". A `humanize()` would have produced plausible, wrong labels.
    """
    meta = gamedata.progression()["relicTypes"]
    assert meta["HungerReduction"]["name"] == "Satiety Duration"
    assert meta["GliderSpeed"]["name"] == "Flight Capacity"
    assert meta["RainbowPassiveRate"]["name"] == "Rainbow Fortune"


# ── what the relics bought ────────────────────────────────────────────────


def test_relic_lines_return_every_line_including_untouched_ones():
    """
    "Nothing spent on Endurance" is what someone deciding where the next effigy
    goes needs. Dropping empty lines would make the panel look like it only
    knows about lines already invested in.
    """
    import main

    lines = main._relic_lines({"MoveSpeed": 18})
    assert len(lines) == 13
    assert {l["type"] for l in lines} == set(gamedata.progression()["relicTypes"])


def test_a_spend_resolves_to_a_rank_and_a_cumulative_effect():
    import main

    line = next(l for l in main._relic_lines({"MoveSpeed": 18}) if l["type"] == "MoveSpeed")
    assert line["name"] == "Movement Speed"
    assert line["spent"] == 18
    # `requiredRelics` is the cost OF EACH RANK, so 18 relics buys rank 6 on a
    # line that runs 1 then 3 per rank. A cumulative reading of that column
    # would put this at rank 18.
    assert line["rank"] == 6
    assert line["effectRate"] > 0
    assert line["hasEffectRate"] is True


def test_capture_power_never_reports_an_effect_rate():
    """
    **All 15 of its ranks are 0.0** while the other twelve carry real values, so
    its effect is expressed somewhere other than that column. Rendering "+0%"
    would be a confident wrong number rather than a missing one.
    """
    import main

    line = next(
        l for l in main._relic_lines({"CapturePower": 3}) if l["type"] == "CapturePower"
    )
    assert line["rank"] > 0, "the rank itself is real and must still be reported"
    assert line["hasEffectRate"] is False


def test_lines_sort_most_invested_first():
    import main

    lines = main._relic_lines({"MoveSpeed": 18, "CapturePower": 3})
    assert [l["type"] for l in lines[:2]] == ["MoveSpeed", "CapturePower"]
