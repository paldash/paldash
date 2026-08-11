"""
The defensive half of a passive set, and the family whose name lies about it.

Asserted against the **shipped bundle** rather than a fixture, because the claims
worth pinning here are claims about the game's data — that `ElementResist_Fire_1`
is 15%, that every `ResistAdditionalEffect_*` is 100, and that
`DamageRateIfDefender_*` is offensive. A fixture would pin my reading of those
and pass forever after a regeneration changed them.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import elements  # noqa: E402
import gamedata  # noqa: E402
import palresist  # noqa: E402


def _effect_types() -> dict:
    """Every effect type in the bundle, with the skills carrying it."""
    out: dict[str, list[str]] = {}
    for skill_id, entry in (gamedata.passive_effects_all() or {}).items():
        for effect in entry.get("effects") or []:
            out.setdefault(str(effect.get("type") or ""), []).append(skill_id)
    return out


def test_element_resistance_is_read_at_all():
    """The whole point: a stat that existed and nothing computed."""
    out = palresist.resistances(["ElementResist_Fire_1"])
    assert out["elements"] == {"Fire": 15.0}
    assert out["any"] is True


def test_the_element_comes_from_the_effect_type_not_the_skill_id():
    """
    `ElementResist_Aqua_1` carries effect type `ElementResist_Water`.

    `Aqua` and `Thunder` resolve to nothing in `elements.canonical`, so a reader
    keyed on the id would silently drop Water and Electric resistance while every
    other element worked — the failure shape that looks like the data missing two
    entries rather than like the code reading the wrong field.
    """
    assert elements.canonical("Aqua") is None
    assert palresist.resistances(["ElementResist_Aqua_1"])["elements"] == {"Water": 15.0}
    assert palresist.resistances(["ElementResist_Thunder_1"])["elements"] == {"Electric": 15.0}


def test_damage_rate_if_defender_is_offensive_and_never_a_resistance():
    """
    **The correction this module exists to hold.**

    `DamageRateIfDefender_Poison` reads like damage taken and the game's own
    prose says *"Damage vs Poison +70%"* — damage you DEAL to a poisoned
    defender. Folding it in would report a Pal as 70% poison-resistant on a
    passive that does the opposite thing, in the direction nobody questions.
    """
    skill = "DamageUpTrainerAndOtomo_ToPoison"
    entry = gamedata.passive_effects(skill)
    assert entry, "the bundle no longer ships the skill this rule was read off"
    assert any(str(e.get("type") or "").startswith("DamageRateIfDefender_")
               for e in entry["effects"])

    out = palresist.resistances([skill])
    assert out["any"] is False
    assert out["elements"] == {} and out["ailments"] == {}
    # Named in the payload, so the exclusion is discoverable rather than a gap.
    assert "DamageRateIfDefender_*" in out["offensiveTypesExcluded"]


def test_every_ailment_resistance_in_the_bundle_is_total():
    """
    All 63 are 100.0, which is why they are reported as immunity.

    If a regeneration ever ships a partial one, `immune` starts lying and this is
    the test that says so — the alternative was a comment asserting it.
    """
    values = {
        float(effect.get("value") or 0.0)
        for entry in (gamedata.passive_effects_all() or {}).values()
        for effect in entry.get("effects") or []
        if str(effect.get("type") or "").startswith("ResistAdditionalEffect_")
    }
    assert values == {100.0}

    out = palresist.resistances(["VolcanoDragon_PartnerSkill_5"])
    assert out["ailments"]["Burn"] == {"percent": 100.0, "immune": True}


def test_resistances_of_one_element_add():
    out = palresist.resistances(["ElementResist_Fire_1", "ElementResist_Fire_2"])
    assert out["elements"]["Fire"] == 40.0
    assert len(out["sources"]) == 2


def test_a_base_only_passive_is_not_a_resistance_in_your_party():
    """
    `InvokeInBaseCamp` fires while the Pal is assigned to a structure.

    Same argument `palstats` makes for the stat block, restated rather than
    imported: whether a buff counts depends on the surface asking, and two
    readers with one shared constant cannot disagree when they should.
    """
    base_only = [
        skill for skill, entry in (gamedata.passive_effects_all() or {}).items()
        if entry.get("invoke") == ["InvokeInBaseCamp"]
        and any(str(e.get("type") or "").startswith("ElementResist_")
                for e in entry.get("effects") or [])
    ]
    for skill in base_only:
        assert palresist.resistances([skill])["any"] is False


def test_a_passive_with_no_invoke_condition_still_counts():
    """
    The nine `_BossDefeat` element resists carry an EMPTY invoke list.

    An absent condition is the file not stating one, which is not the same as a
    restrictive one — dropping them would lose a real (if small) stat because of
    a field the game left blank.
    """
    entry = gamedata.passive_effects("ElementResist_Fire_1_BossDefeat")
    assert entry and not entry.get("invoke")
    out = palresist.resistances(["ElementResist_Fire_1_BossDefeat"])
    assert out["elements"] == {"Fire": 1.0}
    assert out["when"]["Fire"] == "always"


def test_soft_to_is_not_the_complement_of_resistant():
    """
    Having no Fire resistance does not make a Pal weak to Fire.

    `softTo` is the type chart read from the defender's side; the resistance map
    is a passive term. The two are independent in both directions, which is the
    thing to pin: a resistance to something that does not beat you is real and
    useful, and being soft to something you resist is the interesting row rather
    than a contradiction.

    **The first version of this test asserted "a Neutral Pal is soft to
    nothing".** It is soft to Dark. Every one of the nine elements is beaten by
    exactly one other — there is no element with an empty `softTo` to write a
    test around, and the claim came from reading "Neutral is strong against
    nothing" in the chart's own notes as though the relation were symmetric.
    """
    grass = palresist.profile(["Grass"], [])
    assert grass["softTo"] == ["Fire"]
    assert grass["elements"] == {}

    # Resistance to an element that does not beat you: real, and NOT in `softTo`.
    off_axis = palresist.profile(["Grass"], ["ElementResist_Ice_1"])
    assert off_axis["elements"] == {"Ice": 15.0}
    assert off_axis["softTo"] == ["Fire"]
    assert off_axis["softToButResists"] == []

    # Passives never move `softTo` — it is the chart, not the build.
    both = palresist.profile(["Grass"], ["ElementResist_Fire_1"])
    assert both["softTo"] == grass["softTo"]
    assert both["softToButResists"] == ["Fire"]


def test_soft_to_agrees_with_the_boss_planner():
    """
    One relation, read from two sides, must not drift.

    `bossplanner.counters(x)["bringElements"]` is "what beats a Pal with elements
    x", which is exactly `profile(x, …)["softTo"]`. They call the same helper
    today; this fails the moment one of them grows its own copy.
    """
    import bossplanner

    for species_elements in (["Grass"], ["Fire"], ["Dragon", "Water"], ["Neutral"]):
        assert (palresist.profile(species_elements, [])["softTo"]
                == bossplanner.counters(species_elements)["bringElements"])


def test_the_two_terms_are_never_multiplied_together():
    """
    No file states how a 15% resistance composes with the chart's x1.2.

    So `against` returns both and combines neither, and says so in the payload.
    A single "effective damage taken" figure would be inventing the composition
    in the one place a player would trust it.
    """
    out = palresist.against(["Grass"], ["ElementResist_Fire_1"], "Fire")
    assert out["chartFavoursAttacker"] is True
    assert out["bonusToAttacker"] == elements.match_rate()
    assert out["resistPercent"] == 15.0
    assert out["stackingKnown"] is False
    # Nothing in the payload is the product, and nothing is an effective-HP
    # figure. Asserted on the keys because a number is easy to add later.
    assert not {"effectiveHp", "damageTaken", "effectiveDamage"} & set(out)


def test_an_unknown_passive_costs_its_own_term_only():
    out = palresist.resistances(["ElementResist_Fire_1", "NotARealPassive"])
    assert out["elements"] == {"Fire": 15.0}


@pytest.mark.parametrize("effect_type", [
    "ElementResist_Fire", "ResistAdditionalEffect_Burn", "ExplosionResist",
    "TemperatureResist_Cold", "DefenseRateHPThreshold",
])
def test_every_family_this_module_claims_still_exists(effect_type):
    """A bundle regeneration that renames a family must fail here, not go quiet."""
    assert effect_type in _effect_types()
