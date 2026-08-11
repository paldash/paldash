"""
Ranking the game at a chosen build, and the two claims that need pinning.

Against the shipped `gamedata.json.gz`, not fixtures — the movement figures and
the mount list are properties of that bundle, so a fixture would pin the walker
and let a regeneration regress underneath it.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import buildplanner  # noqa: E402
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


MAXED = {"level": 80, "condenserRank": 5, "iv": 100, "soulRank": 20}


# ─── Movement takes no build term, which is the whole finding ───


def test_this_ranking_applies_no_build_bonus_to_a_speed():
    """
    A statement about THIS MODULE, not about the game.

    **The earlier version of this test was called `test_a_maxed_pal_is_not_
    faster` and asserted the game's behaviour, which it cannot see.** The
    operator challenged it and was right: `StatusCalculate_GenkaiToppa_PerAdd`
    carries no stat suffix while every other constant in its family does, and
    the condenser's effect on work suitability is applied at load, absent from
    every file, and was wrongly denied three times.

    What is testable is that nothing here invents a multiplier — the ranking
    reports the species column untouched — and that the payload says so as an
    unknown rather than as a denial.
    """
    base = buildplanner.rank("rideSprint", limit=20)
    maxed = buildplanner.rank("rideSprint", build=MAXED, limit=20)
    assert [(r["speciesId"], r["value"]) for r in base["rows"]] == \
        [(r["speciesId"], r["value"]) for r in maxed["rows"]]
    assert base["buildAffectsMetric"] is False
    assert base["movementInFiles"] is True
    # NEVER False. False would be a claim about the game that nothing here can
    # support, which is exactly the mistake this test replaces.
    assert base["condenserOnMovement"] == "unverified"


def test_passives_are_the_only_thing_that_moves_a_speed():
    plain = buildplanner.rank("rideSprint", limit=1)["rows"][0]
    swift = buildplanner.rank(
        "rideSprint", build={"passives": ["Legend", "MoveSpeed_up_3"]}, limit=1
    )["rows"][0]
    # Legend +20% and Swift +30%, additive within the stat, as the formula does.
    assert swift["passiveBonus"] == pytest.approx(0.5)
    assert swift["value"] == pytest.approx(plain["base"] * 1.5)


def test_a_riding_only_passive_counts_for_ride_speed_and_not_for_running():
    """
    `palstats` excludes `InvokeRiding` and is right to — a buff that fires only
    while a Pal is ridden is not part of a palbox Pal's stat block. This module
    needs it for exactly one metric, which is why it has its own policy.
    """
    moves = buildplanner.movement_bonuses(["MoveSpeed_up_PartnerSkill_Ride_4"])
    assert moves["riding"].get("rideSprint") == pytest.approx(0.2)
    assert moves["always"] == {}

    ride = buildplanner.rank(
        "rideSprint", build={"passives": ["MoveSpeed_up_PartnerSkill_Ride_4"]}, limit=1
    )["rows"][0]
    run = buildplanner.rank(
        "run", build={"passives": ["MoveSpeed_up_PartnerSkill_Ride_4"]}, limit=1
    )["rows"][0]
    assert ride["value"] > ride["base"]
    assert run["value"] == run["base"]


def test_conditional_passives_are_described_and_never_summed():
    """A night-only bonus is true and is not a headline."""
    moves = buildplanner.movement_bonuses(["MoveSpeed_Night_03"])
    assert moves["always"] == {} and moves["riding"] == {}
    assert [c["passiveId"] for c in moves["conditional"]] == ["MoveSpeed_Night_03"]


# ─── The mount list ───


def test_the_mount_list_excludes_the_pals_that_merely_have_gear():
    """
    Galeclaw is the case AGENTS.md named as the counterexample and it is the
    proof the other way — it has PalGear and is not in `RestrictionItems`.
    Caprity is the other trap: a 960 ride speed and no saddle.
    """
    rideable = {r["speciesId"] for r in
                buildplanner.rank("rideSprint", limit=400)["rows"]}
    for not_a_mount in ("Eagle", "Baphomet", "SheepBall", "CatMage", "LazyCatfish"):
        assert not_a_mount not in rideable
    for mount in ("JetDragon", "Alpaca", "Deer", "Penguin", "FlowerDinosaur"):
        assert mount in rideable


def test_jetragon_is_the_fastest_ride():
    """The independent check: the table agrees with what the game is known for."""
    rows = buildplanner.rank("rideSprint", limit=3)["rows"]
    assert rows[0]["speciesId"] == "JetDragon"
    assert rows[0]["base"] == 3300


def test_a_not_applicable_speed_is_dropped_rather_than_ranked_as_slow():
    """`-1` is the game's sentinel. 60 species carry it on RideSprintSpeed."""
    assert all(r["value"] >= 0 for r in buildplanner.rank("transport", limit=400)["rows"])
    entry = gamedata.pal_exact("JetDragon") or {}
    assert "rideSprint" in (entry.get("movement") or {})


def test_encounter_forms_are_not_in_the_ranking():
    """Otherwise the top ten is four Pals wearing their alpha scaling."""
    ids = [r["speciesId"] for r in buildplanner.rank("attack", build=MAXED,
                                                     limit=400)["rows"]]
    assert not any(i.upper().startswith(("BOSS_", "PREDATOR_", "GYM_", "RAID_"))
                   for i in ids)


# ─── Elements: one constant, read from both sides ───


def test_a_matchup_only_reorders_a_metric_it_can_mean_something_for():
    """
    Asking who runs fastest "against Grass" is not a question. `optimise.py`'s
    differential guard, restated for the metrics that carry no coefficient.
    """
    plain = buildplanner.rank("run", limit=30)
    versus = buildplanner.rank("run", limit=30, against="Grass")
    assert versus["matchupApplied"] is False
    assert [r["speciesId"] for r in plain["rows"]] == \
        [r["speciesId"] for r in versus["rows"]]


def test_attack_gains_the_games_own_rate_when_you_beat_them():
    rate = elements.match_rate()
    rows = buildplanner.rank("attack", build=MAXED, limit=200,
                             against="Grass")["rows"]
    strong = [r for r in rows if r["matchup"] == "strong"]
    assert strong, "no species is strong against Grass"
    for row in strong[:5]:
        assert row["value"] == int(row["raw"] * rate)
    for row in [r for r in rows if r["matchup"] != "strong"][:5]:
        assert row["value"] == row["raw"]


def test_the_defensive_side_is_the_same_constant_not_a_second_one():
    """
    There is no resist coefficient because the design does not have one: a
    disadvantaged defender simply eats the attacker's bonus. Grass beats Earth,
    so Earth Pals lose effective bulk against a Grass attacker.
    """
    rate = elements.match_rate()
    result = buildplanner.rank("hp", build={"level": 50}, limit=400,
                               against="Grass")
    assert result["matchRateAppliesBothWays"] is True
    hit = [r for r in result["rows"] if r["incoming"] == "strong"]
    assert hit, "nothing is vulnerable to Grass"
    assert all("Earth" in r["elements"] for r in hit)
    for row in hit[:5]:
        assert row["value"] == int(row["raw"] / rate)


def test_matchup_and_incoming_are_not_inverses():
    """
    Fire beats Grass and Grass beats Earth, so a Fire Pal facing Grass is strong
    AND safe. One field could not say that, which is why there are two.
    """
    rows = {r["speciesId"]: r for r in
            buildplanner.rank("attack", limit=400, against="Grass")["rows"]}
    fire = rows["FlameBuffalo"]
    assert (fire["matchup"], fire["incoming"]) == ("strong", "weak")
    earth = rows["Anubis"]
    assert earth["incoming"] == "strong"


def test_the_raw_figure_is_never_hidden_behind_the_sort():
    for row in buildplanner.rank("attack", build=MAXED, limit=10,
                                 against="Grass")["rows"]:
        assert "raw" in row and row["raw"] > 0


def test_what_it_refuses_to_claim_travels_in_the_payload():
    result = buildplanner.rank("attack", limit=1, against="Grass")
    assert result["mountModeKnown"] is False
    assert result["speedUnitKnown"] is False
    assert result["stackingKnown"] is False
    assert result["chartIsHandEntered"] is True
    # Empty is the healthy state: a content update adding an element shows up
    # here rather than as a confident "neutral".
    assert result["unknownElements"] == []


# ─── Refusals ───


def test_an_unknown_metric_names_the_real_ones():
    result = buildplanner.rank("vibes")
    assert result["known"] is False
    assert "rideSprint" in result["note"]


def test_compare_names_a_typo_rather_than_dropping_it():
    result = buildplanner.compare(["JetDragon", "NotAPal"])
    assert [s["speciesId"] for s in result["species"]] == ["JetDragon"]
    assert result["unknown"] == ["NotAPal"]


# ─── Partner skills: the axis palstats deliberately cannot see ───


def test_silvegis_buffs_the_player_and_the_buff_scales_with_stars():
    """
    The operator's example, and the reason this axis exists: Silvegis raises
    the *player's* defence for merely being in the party, and four stars is
    meaningfully better than one.

    `palstats` cannot see this and is right not to — `ToTrainer` is not part of
    a Pal's stat block. It is the whole content of "which Pal should I carry".
    """
    at_one = {e["type"]: e["value"]
              for e in buildplanner.partner_effects("WhiteShieldDragon", 1)["party"]}
    at_five = {e["type"]: e["value"]
               for e in buildplanner.partner_effects("WhiteShieldDragon", 5)["party"]}
    assert at_one["ShieldDamageCutRate"] == 65.0
    assert at_five["ShieldDamageCutRate"] == 80.0
    assert at_five["PlayerShield_RecoverStartTimeRate"] > \
        at_one["PlayerShield_RecoverStartTimeRate"]


def test_riding_solmora_lux_electrifies_the_rider_and_plain_solmora_does_not():
    """
    Read off the effect table rather than off the skill id. `GiveAElectricity_
    Ride` is obvious-looking and what makes it true is
    `ElementElectricity -> ToTrainer` under `InvokeRiding`.
    """
    lux = buildplanner.partner_effects("KingSunfish_Thunder", 3)
    assert any(e["type"] == "ElementElectricity" and e["target"] == "ToTrainer"
               for e in lux["riding"])
    plain = buildplanner.partner_effects("KingSunfish", 3)
    assert not any(e["type"] == "ElementElectricity"
                   for e in plain["riding"] + plain["party"])


def test_a_buff_to_the_pal_is_kept_apart_from_a_buff_to_you():
    """"+15% Dark boost" reads very differently depending on whose it is."""
    lux = buildplanner.partner_effects("KingSunfish_Thunder", 3)
    assert all(e["target"] in ("ToTrainer", "ToTrainerAndOtomo")
               for e in lux["party"] + lux["riding"])
    assert all(e["target"] not in ("ToTrainer", "ToTrainerAndOtomo")
               for e in lux["toPal"])


def test_a_one_entry_species_clamps_rather_than_going_empty():
    """
    Valentail's only partner skill is on/off, so the game writes one rank entry
    instead of five identical ones. Asking for rank 4 must give it, not nothing.
    """
    assert gamedata.partner_skills_at("LongCat", 1) == \
        gamedata.partner_skills_at("LongCat", 4)
    assert gamedata.partner_skills_at("LongCat", 4)


def test_every_partner_skill_id_resolves():
    """
    The bundle is a MAPPING. A dangling id means the two extractions drifted,
    which a row count would never show — so it is asserted against the shipped
    files rather than left to the extractor's own run.
    """
    unresolved = []
    for species in gamedata.load().get("pals") or {}:
        effects = buildplanner.partner_effects(species, 5)
        unresolved.extend(effects["unknown"])
    assert unresolved == []
