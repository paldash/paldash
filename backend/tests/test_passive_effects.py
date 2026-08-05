"""
Passive skills, as numbers rather than as English sentences.

The bundled `passives` section carries prose — "Attack +5%" — which is right for
showing a player and useless for computing. `palstats.describe` therefore took a
caller-supplied `passive_bonus` defaulting to **zero**, so every stat this
dashboard has ever shown ignored passive skills entirely.

`DT_PassiveSkill_Main` decodes out of the SERVER pak with structured effects
(`scripts/extract-passive-effects.py`). VERIFIED AGAINST THE GAME'S OWN PROSE:
1,754 of the 1,759 passives with a numeric English description match the
extracted numbers exactly. Four of the five exceptions are the archive's own
descriptions failing to substitute their `{EffectValue1}` placeholder — the
table is right and the sentence is broken.
"""

from __future__ import annotations

import os
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

import gamedata      # noqa: E402
import palstats      # noqa: E402


def _pal(**extra):
    pal = {
        "characterId": "Alpaca",
        "level": 50,
        "ivs": {"hp": 50, "shot": 50, "defense": 50},
        "rank": 1,
        "friendshipPoint": 0,
        "soulRanks": {},
        "passiveSkills": [],
    }
    pal.update(extra)
    return pal


# ─── The bundle ──────────────────────────────────────────────────


def test_a_known_passive_resolves_to_structured_effects():
    legend = gamedata.passive_effects("Legend")
    assert legend is not None
    kinds = {e["type"]: e["value"] for e in legend["effects"]}
    assert kinds["ShotAttack"] == 20.0
    assert kinds["Defense"] == 20.0


def test_lookup_is_case_insensitive_like_everything_else_here():
    assert gamedata.passive_effects("legend") == gamedata.passive_effects("Legend")


def test_an_unknown_passive_is_none_rather_than_an_exception():
    assert gamedata.passive_effects("NotARealPassive") is None


def test_the_developers_test_rows_are_not_shipped():
    """
    `DT_PassiveSkill_Main` carries eight `TestSkill*` fixtures, all marked
    SortNotDisplayable. A Pal cannot roll them and a search box should not
    offer them.
    """
    assert gamedata.passive_effects("TestSkill1") is None


# ─── Per stat, which is the whole point ──────────────────────────


def test_a_passive_bonus_is_per_stat_not_one_number():
    """`Legend` is +20% attack AND +20% defence — a single float cannot say that."""
    assert palstats.passive_bonuses(["Legend"]) == {"attack": 0.2, "defense": 0.2}


def test_a_negative_effect_keeps_its_sign():
    """
    `Noukin` trades work speed for attack. Dropping the sign would turn a
    -50% penalty into a bonus, which is wrong in the direction nobody checks.
    """
    bonuses = palstats.passive_bonuses(["Noukin"])
    assert bonuses["attack"] == 0.3
    assert bonuses["workSpeed"] == -0.5


def test_effects_stack_additively_within_a_stat():
    stacked = palstats.passive_bonuses(["Legend", "Rare"])
    assert abs(stacked["attack"] - 0.35) < 1e-9
    assert abs(stacked["defense"] - 0.35) < 1e-9


def test_an_unset_target_still_counts_as_a_self_buff():
    """
    `Rare`'s defence effect is the ONE effect in the whole bundle whose target
    is `None`, and the game's own description of it reads "Defense +15%". A
    strict `ToSelf` test dropped 15% defence from every Lucky Pal — and the only
    reason it showed up is that stacking it with Legend left defence unmoved.
    """
    assert palstats.passive_bonuses(["Rare"])["defense"] == 0.15


def test_an_unknown_passive_contributes_nothing_rather_than_failing():
    """A modded passive should cost the term, not the Pal's whole stat block."""
    assert palstats.passive_bonuses(["NotAThing"]) == {}


# ─── Through the formula ─────────────────────────────────────────


def test_passives_now_reach_the_calculated_stats():
    with_legend = palstats.describe(_pal(passiveSkills=["Legend"]))
    without = palstats.describe(_pal())
    assert with_legend["attack"]["final"] > without["attack"]["final"]
    assert with_legend["defense"]["final"] > without["defense"]["final"]


def test_a_passive_only_moves_the_stats_it_names():
    """`Legend` does not touch HP, so HP must not move."""
    with_legend = palstats.describe(_pal(passiveSkills=["Legend"]))
    without = palstats.describe(_pal())
    assert with_legend["hp"]["final"] == without["hp"]["final"]


def test_an_explicit_override_still_wins():
    """
    Kept as a parameter because "what would this Pal be without its passives"
    is a real question, and a fixed value is how it gets asked.
    """
    overridden = palstats.describe(_pal(passiveSkills=["Legend"]), passive_bonus=0.0)
    plain = palstats.describe(_pal())
    assert overridden["attack"]["final"] == plain["attack"]["final"]


def test_a_worker_only_passive_does_not_buff_a_palbox_pal():
    """
    A skill that fires only while working at a base is not a buff to a Pal
    sitting in a box, and counting it would show an attack the game never uses.
    """
    worker_only = [
        pid for pid in _all_passive_ids()
        if not (set((gamedata.passive_effects(pid) or {}).get("invoke") or [])
                & palstats.PASSIVE_SELF_INVOKES)
    ]
    assert worker_only, "expected at least one non-self-invoking passive"
    assert palstats.passive_bonuses(worker_only[:5]) == {}


def _all_passive_ids() -> list:
    gamedata.passive_effects("Legend")  # force the bundle to load
    return list(gamedata._passive_effects or {})
