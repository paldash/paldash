"""
The game's own milestone progress, and the three things it must not claim.

Asserted against the **shipped bundle**, like `test_gametext.py` and
`test_palresist.py`: a test of the extractor passes happily beside a bundle
built before the extractor changed.

The counter mapping's real validation is in `test_achievements_world.py`, which
checks it against five real players — nobody may have *claimed* a tier their
counter has not reached. That is the only evidence available that `PalDex` is
driven by `TribeCaptureCount` rather than something else, and it is why the
integration half exists.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import achievements  # noqa: E402


def test_the_bundle_ships_and_has_the_three_categories():
    assert achievements.available()
    cats = achievements.catalogue()
    assert set(cats) == {"PalCapture", "PalDex", "BossDefeat"}
    assert sum(len(c["tiers"]) for c in cats.values()) == 26


def test_tiers_are_sorted_by_threshold():
    """
    Ascending is the order a player meets them in, and therefore the only order
    a progress list may render. Row-name order would put `PalDex_10` second.
    """
    for name, entry in achievements.catalogue().items():
        needs = [t["requireCount"] for t in entry["tiers"]]
        assert needs == sorted(needs), name
        assert len(set(needs)) == len(needs), f"{name} has duplicate thresholds"


def test_bossdefeat_has_no_counter_and_that_is_deliberate():
    """
    THE REFUSAL. No `BossDefeatCount` exists; towers max at 7 observed against a
    top tier of 100 so it cannot be towers alone; and the claim data cannot
    separate field-only from field-plus-tower, because every player who claimed
    the 5-boss tier clears it under either reading.

    A merely plausible match is not a match.
    """
    assert achievements.catalogue()["BossDefeat"]["counter"] is None
    assert achievements.catalogue()["PalCapture"]["counter"] == "palsCaptured"
    assert achievements.catalogue()["PalDex"]["counter"] == "speciesCaptured"


def test_a_category_with_no_counter_never_reports_locked():
    """
    "Not yet reached" is a claim about a number this cannot see. Such a tier is
    `claimed` or `unknown` — never `locked`, and never a 0% progress bar.
    """
    result = achievements.for_player({"achievementsClaimed": ["BossDefeat_1"]})
    boss = result["BossDefeat"]
    assert boss["hasProgress"] is False
    assert boss["value"] is None
    states = {t["state"] for t in boss["tiers"]}
    assert "locked" not in states
    assert states == {"claimed", "unknown"}
    assert boss["claimed"] == 1


def test_earned_but_not_collected_is_its_own_state():
    """
    A reward is claimed by walking to the NPC, so `unclaimed` is a real state
    and the useful one — "you have earned this and not collected it". One
    reference player has 149 species and has claimed none of the ten tiers.
    """
    result = achievements.for_player({
        "speciesCaptured": {"total": 149, "distinct": None},
        "achievementsClaimed": [],
    })
    dex = result["PalDex"]
    assert dex["value"] == 149
    assert dex["unclaimed"] == 10
    assert dex["claimed"] == 0
    assert all(t["state"] == "unclaimed" for t in dex["tiers"])


def test_locked_and_unclaimed_split_on_the_threshold():
    result = achievements.for_player({
        "speciesCaptured": {"total": 35, "distinct": None},
        "achievementsClaimed": [],
    })
    for tier in result["PalDex"]["tiers"]:
        expected = "unclaimed" if tier["requireCount"] <= 35 else "locked"
        assert tier["state"] == expected, tier["id"]


def test_a_claimed_tier_beats_a_locked_one():
    """
    Claimed is read from the save and locked is inferred. If they ever
    disagree — a tier claimed below its threshold — the save wins, because it
    is the record and the counter mapping is the guess.
    """
    result = achievements.for_player({
        "speciesCaptured": {"total": 0, "distinct": None},
        "achievementsClaimed": ["PalDex_10"],
    })
    top = [t for t in result["PalDex"]["tiers"] if t["id"] == "PalDex_10"][0]
    assert top["state"] == "claimed"


def test_a_missing_counter_field_is_not_zero():
    """
    A save that did not carry the field must not render as a player who has
    done nothing — the bug this feature found in `speciesCaptured`, which read
    `{total: 0}` for every player because the field is a plain int.
    """
    # An absent key and an explicitly null one both give None, never 0. My own
    # first version of this test asserted 0 for the absent case and the module
    # was right — a 0 here renders a progress bar at the bottom of the scale for
    # a player whose figure is simply unknown.
    for progress in ({"achievementsClaimed": []},
                     {"speciesCaptured": None, "achievementsClaimed": []},
                     {"speciesCaptured": {"distinct": 4}, "achievementsClaimed": []}):
        result = achievements.for_player(progress)
        assert result["PalDex"]["value"] is None, progress
        assert result["PalDex"]["hasProgress"] is False, progress
        assert all(t["state"] == "unknown" for t in result["PalDex"]["tiers"])

    # ...and a real 0 is a real 0: a brand-new player, not a missing field.
    zero = achievements.for_player({"speciesCaptured": {"total": 0},
                                    "achievementsClaimed": []})
    assert zero["PalDex"]["value"] == 0
    assert zero["PalDex"]["hasProgress"] is True
    assert all(t["state"] == "locked" for t in zero["PalDex"]["tiers"])


def test_the_payload_says_it_is_not_steam():
    """
    Reading Steam's achievements needs an external API this project forbids,
    and these are per-server rather than per-account. The client is what would
    otherwise label the panel "Achievements".
    """
    summary = achievements.summarise({"achievementsClaimed": []})
    assert summary["isSteam"] is False
    assert "DT_AchivementRewardNPC" in summary["source"]


def test_pocketpairs_typo_is_preserved():
    """
    The DataTable and the save key are both `Achivement`. Correcting it breaks
    the join, so the misspelling travels deliberately.
    """
    import parser as pparser

    src = open(pparser.__file__, encoding="utf-8").read()
    assert "NPCAchivementRewardFlag" in src
    assert "NPCAchievementRewardFlag" not in src


def test_rewards_parse_into_items():
    """
    `RewardItemString` is `((ItemId,Count))` — a stringified struct. Anything
    unparsed keeps its raw text rather than being dropped.
    """
    tiers = achievements.catalogue()["PalDex"]["tiers"]
    assert all(t.get("rewards") for t in tiers), "every PalDex tier has a reward"
    first = tiers[0]["rewards"][0]
    assert isinstance(first["itemId"], str) and first["count"] >= 1
    assert not any("rewardRaw" in t for t in tiers), "all parsed cleanly"
