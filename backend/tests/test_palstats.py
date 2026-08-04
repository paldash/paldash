"""
The calculated stat figures, and the claims made about them.

These are not regression pins on arbitrary numbers. Each one holds a *property*
the formula is supposed to have — the kind of thing that would go quietly wrong
and still produce plausible output. A wrong stat looks exactly like a right one.
"""

from __future__ import annotations

import gamedata
import palstats


# ─── Condenser stars ─────────────────────────────────────


def test_rank_one_means_no_stars():
    """
    The off-by-one that matters. A Pal with no condenser upgrades is stored as
    `Rank` 1, not 0, so the bonus is `(rank - 1) * 5%` and a fresh Pal gets +0%.
    Treating rank as the star count gives every Pal in the world a 5% bonus.
    """
    assert palstats.hp_breakdown("SheepBall", 50, condenser_rank=1)["condenserMultiplier"] == 1.0
    assert palstats.hp_breakdown("SheepBall", 50, condenser_rank=5)["condenserMultiplier"] == 1.2


def test_each_star_raises_every_stat():
    """
    "If I add stars, do the stats go up in game?" — yes, and this is the check
    that the answer this dashboard gives matches that. Strictly increasing, not
    merely non-decreasing: a bonus that rounds away to nothing is a bonus that
    is not being applied.
    """
    for stat_fn in (palstats.hp_breakdown, palstats.attack_breakdown,
                    palstats.defense_breakdown):
        finals = [
            stat_fn("SheepBall", 50, iv=50, condenser_rank=rank)["final"]
            for rank in range(1, 6)
        ]
        assert finals == sorted(finals) and len(set(finals)) == 5, finals


def test_work_speed_is_flat_until_the_condenser_is_used():
    """
    Work Speed does NOT behave like the other three. It is 70 regardless of
    level or species until rank 2, and only then do craft speed and level enter.
    A formula that treats it like HP shows work speed climbing with level on a
    Pal whose in-game work speed has not moved — wrong in a way that looks right.
    """
    assert palstats.work_speed_breakdown("SheepBall", 1, condenser_rank=1)["final"] == 70
    assert palstats.work_speed_breakdown("SheepBall", 60, condenser_rank=1)["final"] == 70
    assert palstats.work_speed_breakdown("SheepBall", 60, condenser_rank=2)["final"] > 70


# ─── The alpha bonus lives in the data ───────────────────


def test_the_alpha_form_is_looked_up_as_itself():
    """
    `BOSS_Alpaca` carries hp scaling 108 where `Alpaca` carries 90, and that
    difference *is* the alpha bonus. `gamedata.pal()` strips the prefix — right
    for naming, since an alpha Lamball is still called Lamball — so reading stats
    through it silently returns the ordinary species' numbers.
    """
    assert gamedata.pal_exact("BOSS_Alpaca")["stats"]["hp"] == 108
    assert gamedata.pal_exact("Alpaca")["stats"]["hp"] == 90
    # And `pal()`, the naming lookup, does the normalising thing — which is why
    # palstats must not use it.
    assert gamedata.pal("BOSS_Alpaca")["stats"]["hp"] == 90

    boss = palstats.hp_breakdown("BOSS_Alpaca", 50)["final"]
    plain = palstats.hp_breakdown("Alpaca", 50)["final"]
    assert boss > plain


def test_no_separate_boss_multiplier_is_applied():
    """
    The bonus must be counted once. `BOSS_` scaling is 1.2x the base species'
    here, so the HP *excess over the flat 500 + 5L term* must scale by exactly
    that and no more. An extra multiplier on top would show up as a larger ratio.
    """
    level = 50
    flat = 500 + 5 * level
    boss = palstats.hp_breakdown("BOSS_Alpaca", level)["final"] - flat
    plain = palstats.hp_breakdown("Alpaca", level)["final"] - flat
    assert abs(boss / plain - 108 / 90) < 0.01


# ─── Trust and souls ─────────────────────────────────────


def test_friendship_ranks_are_bounded_and_ordered():
    assert palstats.friendship_rank(0) == 0
    assert palstats.friendship_rank(5999) == 0
    assert palstats.friendship_rank(6000) == 1
    assert palstats.friendship_rank(200000) == 10
    assert palstats.friendship_rank(10_000_000) == 10


def test_soul_ranks_are_three_percent_each_and_multiply_the_end():
    """
    Souls multiply the subtotal, so they compound with trust rather than being
    folded into the base. Ten ranks is +30%.
    """
    none = palstats.hp_breakdown("SheepBall", 50, soul_rank=0)
    ten = palstats.hp_breakdown("SheepBall", 50, soul_rank=10)
    assert ten["soulMultiplier"] == 1.3
    assert ten["final"] == int(none["subtotal"] * 1.3)


def test_attack_uses_shot_scaling_not_melee():
    """
    Palworld's displayed Attack is the *shot* figure. Both are bundled and they
    differ on most species (Melpaca: 90 melee, 75 shot), so reading the wrong one
    gives a plausible number that is quietly wrong almost everywhere.
    """
    stats = gamedata.pal_exact("Alpaca")["stats"]
    assert stats["meleeAttack"] != stats["shotAttack"]

    level, iv = 50, 0
    got = palstats.attack_breakdown("Alpaca", level, iv=iv)["base"]
    import math
    expected_shot = math.floor(
        math.floor(1.5 * level) + stats["shotAttack"] * 0.075 * level
    )
    assert got == expected_shot


# ─── Unknown species ─────────────────────────────────────


def test_an_unknown_species_gets_no_stats_rather_than_zeroes():
    """
    Humans and NPCs share `CharacterSaveParameterMap` with Pals and carry IVs
    exactly like one, so there is no structural way to tell them apart. Returning
    a breakdown full of zeroes would show confident stats for a merchant; None
    says the dashboard does not know.
    """
    assert palstats.describe({"characterId": "SalesPerson_Wander", "level": 20}) is None
    assert palstats.describe({"characterId": "SheepBall", "level": 20}) is not None


# ─── Progression ─────────────────────────────────────────


def test_level_progress_reads_the_pal_curve_not_the_player_one():
    """
    `palExpTable` carries both, side by side, and they differ from level 2
    onwards. `NextEXP` is the player's; `PalNextEXP` is the one a Pal follows.
    """
    table = gamedata.pal_exp_table()
    assert table["2"]["NextEXP"] != table["2"]["PalNextEXP"]
    assert palstats.level_progress(1, 0)["needed"] == table["2"]["PalNextEXP"]


def test_level_progress_is_a_fraction_of_the_current_band():
    at_start = palstats.level_progress(10, gamedata.pal_exp_table()["10"]["PalTotalEXP"])
    assert at_start["percent"] == 0.0
    assert at_start["intoLevel"] == 0

    span = (gamedata.pal_exp_table()["11"]["PalTotalEXP"]
            - gamedata.pal_exp_table()["10"]["PalTotalEXP"])
    midway = palstats.level_progress(
        10, gamedata.pal_exp_table()["10"]["PalTotalEXP"] + span // 2
    )
    assert 45 <= midway["percent"] <= 55


def test_low_exp_for_a_level_is_reported_not_rejected():
    """
    A freshly caught Pal arrives at its wild level with almost no EXP and the
    game leaves it there — 8 of the reference world's 1,905 Pals. So this
    function describes; it does not judge. The one-sided check that high EXP is
    suspect lives in `editschema`, deliberately not here.
    """
    result = palstats.level_progress(40, 0)
    assert result["known"] is True
    assert result["intoLevel"] == 0


# ─── describe() as a whole ───────────────────────────────


def test_describe_reports_stars_as_rank_minus_one():
    pal = {"characterId": "SheepBall", "level": 30, "rank": 3, "ivs": {}, "soulRanks": {}}
    described = palstats.describe(pal)
    assert described["inputs"]["condenserRank"] == 3
    assert described["inputs"]["condenserStars"] == 2


def test_describe_says_the_figures_are_calculated():
    """
    Present in the payload so a UI cannot render these with the same authority as
    a level or an IV. The formula's own documented tolerance is +/-1-2 on the
    trust and awakening terms at some boundaries.
    """
    described = palstats.describe({"characterId": "SheepBall", "level": 1})
    assert described["calculated"] is True


def test_describe_tolerates_a_pal_record_missing_every_optional_field():
    """
    Older cached parses predate `soulRanks`, `friendshipPoint` and `isLucky`, and
    `describe` is called on every Pal during enrichment — one KeyError there
    empties the whole Pals view.
    """
    described = palstats.describe({"characterId": "SheepBall"})
    assert described is not None
    assert described["hp"]["final"] > 0
