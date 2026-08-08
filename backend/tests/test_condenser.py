"""
Condenser stars DO raise work suitability, and this project said no three times.

Every assertion here is an in-game reading from the operator's own world on
2026-08-07, because the rule is in no file — not a DataTable, not
`BP_PalGameSetting`, not the save. See `backend/condenser.py`.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import condenser  # noqa: E402
import gamedata  # noqa: E402


def _levels(species: str, stars: int) -> dict:
    entry = gamedata.pal_exact(species) or {}
    return condenser.apply(
        entry.get("workSuitabilities") or {},
        entry.get("bestWorkSuitability") or "",
        stars,
    )


def test_one_star_adds_one_to_the_best_and_the_TIE_IS_BROKEN_BY_THE_LABEL():
    """
    **The load-bearing reading, and a 4-star Anubis could not have produced it.**

    Anubis is `{Handcraft: 6, Mining: 6, Transport: 4}` with
    `bestWorkSuitability = Handcraft` — the top two TIED. Observed at one star:
    Handiwork 7, Mining 6, Transport 4.

    Only Handiwork moved. So a tie is broken by the game's own
    `BestWorkSuitability` column — not by both sides getting it, not by enum
    order, and one star is not zero. At four stars the tied pair converges on 8
    under every reading and the question would have stayed open.
    """
    result = _levels("Anubis", 1)
    assert result["determined"] is True
    assert result["levels"] == {"Handcraft": 7, "Mining": 6, "Transport": 4}


def test_a_single_suitability_takes_every_star_and_clamps():
    """
    **The fallthrough, and it is the majority case.** A Pal with one suitability
    has no "2nd-best", so the bonus lands on the only one it has: +4 at four
    stars. Jormuntide reaching 10 rather than 9 is what says so — under the
    alternative reading (skip when there is no target) it would be 7+1=8.

    All three of these were read at four stars and all three showed 10, from two
    different bases, which is the clamp at `WorkSuitabilityMaxRank`.
    """
    assert _levels("JetDragon", 4)["levels"] == {"Collection": 10}       # 8 + 4 -> 10
    assert _levels("DomeArmorDragon", 4)["levels"] == {"Mining": 10}     # 8 + 4 -> 10
    assert _levels("Umihebi", 4)["levels"] == {"Watering": 10}           # 7 + 4 -> 10


def test_three_or_fewer_suitabilities_is_fully_determined_at_four_stars():
    """
    Every suitability is inside the top three, so which slot is which stops
    mattering: three individual bonuses plus the all-stars one gives +2 across
    the board. Not yet read in game, and it follows from readings that were.
    """
    result = _levels("Anubis", 4)
    assert result["determined"] is True
    assert result["levels"] == {"Handcraft": 8, "Mining": 8, "Transport": 6}


def test_FOUR_OR_MORE_SUITABILITIES_IS_REFUSED_RATHER_THAN_GUESSED():
    """
    **The ordering of the 2nd and 3rd slots is not by base value, and one
    observation cannot say what it is.**

    Verdash at four stars was read as Planting 5, Handiwork 7, Gathering 7,
    Lumbering 5, Transport 4 — three at +2 and two at +1, which is exactly the
    rule's total. But the three winners are Handiwork (5), Gathering (5) and
    **Lumbering (base 3)**, while Planting at base **4** got only the all-stars
    +1. So the numbers do not order it, and `BestWorkSuitability` names only the
    first.

    Alternatives that fit one Pal — enum order from the best, a designer list, a
    `WorkSuitabilityAddRank_Deforest` passive on a base-mate — are not
    distinguishable here, and picking one would be fitting the method to the
    answer. So this returns `determined: False`.
    """
    result = _levels("GrassRabbitMan", 4)
    assert result["determined"] is False
    assert "NOT by base value" in result["reason"]
    # The floor is still known and still useful: every suitability gets at
    # least the all-stars +1, and the best one is named.
    assert result["bonus"]["Handcraft"] == 2
    assert all(v >= 1 for v in result["bonus"].values())


def test_zero_stars_changes_nothing_and_a_workless_pal_gains_nothing():
    """
    Nine species have no suitability at all. There is nothing to add to, and
    inventing one would be worse than saying zero.
    """
    assert _levels("Anubis", 0)["bonus"] == {"Handcraft": 0, "Mining": 0, "Transport": 0}
    assert condenser.bonus({}, "", 4)["bonus"] == {}


def test_stars_are_not_the_save_s_rank():
    """
    `Rank` is 1-5 and rank 1 is NO stars — the same off-by-one `palstats`
    documents for the condenser stat multiplier, where treating rank as a star
    count gives every Pal in the world a bonus it has not got.
    """
    entry = gamedata.pal_exact("Anubis") or {}
    ws, best = entry["workSuitabilities"], entry["bestWorkSuitability"]
    # A save rank of 1 is zero stars, so callers must subtract before calling.
    assert condenser.bonus(ws, best, 0)["bonus"]["Handcraft"] == 0
    assert condenser.bonus(ws, best, 4)["bonus"]["Handcraft"] == 2


def test_the_source_travels_in_the_payload():
    """
    Not read from a game file, so it says so where a client can see it — the
    same reason `hasMultiplier` and `stated` travel rather than sitting in a
    docstring.
    """
    result = _levels("Anubis", 1)
    assert "Measured in game" in result["source"]
    assert "Not stated in any game file" in result["source"]
