"""
The boss planner, and the four kinds that must not be averaged together.

Against the shipped bundles, like every other reference test here.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import bossplanner  # noqa: E402
import elements  # noqa: E402
import gamedata  # noqa: E402
import viewcache  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_caches():
    gamedata._reset_cache()
    viewcache.clear()
    yield
    gamedata._reset_cache()
    viewcache.clear()


def test_all_three_kinds_are_present_and_counted_separately():
    report = bossplanner.encounters()
    assert report["counts"]["field"] == 90
    assert report["counts"]["tower"] == 8
    assert report["counts"]["raid"] > 0
    assert report["kindsAreNotComparable"] is True


def test_a_raid_boss_has_no_position_and_says_so():
    """
    Summoned at an altar, so there is nowhere to draw it. Absent, never (0, 0)
    — the failure the guild-marker extractor already records.
    """
    raids = [b for b in bossplanner.encounters(kind="raid")["bosses"]]
    assert raids
    assert all(b["position"] is None for b in raids)
    assert all(b.get("summonItemId") for b in raids)
    assert bossplanner.encounters()["raidBossesHaveNoPosition"] is True


def test_a_field_boss_carries_its_own_level_and_a_verified_position():
    field = bossplanner.encounters(kind="field")["bosses"]
    assert all(isinstance(b["level"], int) and b["level"] > 0 for b in field)
    assert all(b["position"] and b["position"]["x"] is not None for b in field)


def test_a_tower_gets_no_matchup_rather_than_an_empty_one():
    """
    A tower entrance is a location, not an encounter — the layer knows where it
    is and not what lives in it. Inventing a species from the tower's name is
    the `BP_LevelObject_TowerLockBarrier` mistake.
    """
    towers = bossplanner.encounters(kind="tower")["bosses"]
    assert len(towers) == 8
    assert all(t["counters"] is None for t in towers)
    assert all(t["elements"] == [] for t in towers)


def test_counters_report_both_directions_because_they_are_not_inverses():
    """
    Chillet is Ice/Dragon: bring Fire (beats Ice) or Ice (beats Dragon), and
    avoid Dark and Dragon, which its own elements beat. A planner showing only
    the offensive half sends somebody in glass-cannoned.
    """
    both = bossplanner.counters(["Ice", "Dragon"])
    assert "Fire" in both["bringElements"] and "Ice" in both["bringElements"]
    assert "Dark" in both["avoidElements"] and "Dragon" in both["avoidElements"]
    assert both["matchRate"] == elements.match_rate()
    assert both["matchRateAppliesBothWays"] is True


def test_the_element_filter_matches_the_boss_not_its_counter():
    """
    "Show me the Fire bosses" is the question people ask. Answering the other
    one under the same parameter name would be a quiet surprise.
    """
    fire = bossplanner.encounters(element="Fire")["bosses"]
    assert fire
    assert all("Fire" in b["elements"] for b in fire)


def test_a_level_filter_keeps_the_bosses_that_have_no_level():
    """
    Towers carry none, so dropping them would make a level filter silently
    narrow the *kinds* as well as the levels.
    """
    low = bossplanner.encounters(max_level=20)
    assert low["counts"]["tower"] == 8
    assert all(b["level"] <= 20
               for b in low["bosses"] if b["level"] is not None)


def test_it_refuses_to_recommend_a_level_or_a_party_size():
    """
    No file states either. "Boss level + 5" is a rule of thumb somebody made
    up, and this project does not ship those as the game's word.
    """
    report = bossplanner.encounters()
    assert report["recommendedLevelKnown"] is False
    assert report["partySizeKnown"] is False
