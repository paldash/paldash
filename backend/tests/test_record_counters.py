"""
The RecordData lifetime counters (#138): shape handling and, above all, the
absent-is-not-zero rule.

The game writes a counter the first time it has something to count, so a
missing key means "not recorded", never 0 — refworld carries players with no
CampConqueredCount at all beside players at 6. A first pass at the boss
counters read absent as 0 and "refuted" them; these tests pin the discipline
so the payload cannot regress into confident zeros.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import parser as pparser  # noqa: E402


class _FakeGvas:
    def __init__(self, record: dict):
        self.properties = {
            "SaveData": {"value": {"RecordData": {"value": record}}}
        }


def _progress(record: dict) -> dict:
    return pparser.extract_player_progress(_FakeGvas(record))


def test_absent_counter_is_absent_not_zero():
    progress = _progress({})
    for label, _prop in pparser._PROGRESS_COUNTERS:
        assert label not in progress, (
            f"{label} rendered for a player whose save never wrote it — "
            "absent and zero are different facts"
        )


def test_scalar_counter_has_no_invented_distinct():
    progress = _progress({"CampConqueredCount": {"value": 6}})
    assert progress["campsConquered"] == {"total": 6, "distinct": None}


def test_map_counter_sums_values_and_counts_entries():
    # TowerBossDefeatCount's real shape on refworld: per-boss repeat counts.
    progress = _progress({
        "TowerBossDefeatCount": {"value": [
            {"key": "GrassBoss_Normal", "value": 5},
            {"key": "ElectricBoss_Normal", "value": 12},
        ]},
    })
    assert progress["towerBossDefeats"] == {"total": 17, "distinct": 2}


def test_the_four_138_counters_are_wired():
    props = dict(pparser._PROGRESS_COUNTERS)
    assert props["towerBossDefeats"] == "TowerBossDefeatCount"
    assert props["campsConquered"] == "CampConqueredCount"
    assert props["oilrigsCleared"] == "OilrigClearCount"
    assert props["npcTalks"] == "NPCTalkCountMap"
