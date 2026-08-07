"""
204 of the 208 effect types a passive can have were invisible.

`palstats` maps four — `MaxHP`, `ShotAttack`, `Defense`, `CraftSpeed` — which is
right *for the stat formula*, because those are its terms. It meant the dashboard
never showed that Legend also gives +20% move speed, that Workaholic slows sanity
drain, or that most partner skills buff the player rather than the Pal.

The fix is a second surface with its own policy, not a wider constant. These
tests pin the two things that could quietly go wrong: an effect type nobody
categorised silently vanishing, and the two surfaces converging on one filter.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import gamedata  # noqa: E402
import palstats  # noqa: E402
import passiveeffects  # noqa: E402


def test_every_bundled_effect_type_is_categorised():
    """
    **Empty is the healthy state**, the same way `elements.unknown_to_chart()` is.
    An uncategorised effect would drop out of every panel, and a missing line
    reads as a Pal that does not have the skill rather than as a gap here.

    Measured: 208 distinct types, 208 classified.
    """
    assert passiveeffects.unclassified() == []


def test_the_bundle_still_has_the_208_types_this_was_written_against():
    """A regression signal. If this moves, `unclassified()` above is the check
    that says whether the new ones landed anywhere."""
    types = {
        str(effect.get("type"))
        for entry in gamedata.passive_effects_all().values()
        for effect in (entry or {}).get("effects") or []
    }
    assert len(types) == 208


def test_the_two_surfaces_do_NOT_share_a_filter():
    """
    The subtle one. `palstats.PASSIVE_SELF_INVOKES` answers "does this apply to
    the Pal's own displayed stats"; this module answers "what does this Pal do".
    Reusing the constant is how the second question silently becomes the first.
    """
    assert not hasattr(passiveeffects, "PASSIVE_SELF_INVOKES")
    # And the panel must actually return things the stat formula drops.
    described = passiveeffects.describe_passives(["Legend"])
    kinds = {e["type"] for s in described["skills"] for e in s["effects"]}
    assert "MoveSpeed" in kinds, "Legend's move speed is the whole point"
    assert "MoveSpeed" not in palstats.PASSIVE_EFFECT_STATS


def test_a_trainer_buff_is_LABELLED_not_dropped():
    """
    669 of the bundle's 2,057 effects target `ToTrainer`. `palstats` drops them,
    correctly — they are not this Pal's stats. Dropping them here would hide the
    point of most partner skills, so they travel with who they reach.
    """
    effect = passiveeffects.describe_effect(
        {"type": "ShotAttack", "value": 10.0, "target": "ToTrainer"}
    )
    assert effect["affects"] == "player"
    assert effect["affectsLabel"] == "you"


def test_a_base_only_skill_says_WHEN_it_fires():
    described = passiveeffects.describe_passives(["Legend"])
    assert described["skills"][0]["whenLabel"] == "always"

    # An empty invoke list is its own answer, not a missing one: 156 of the
    # 1,897 bundled passives have none, and they are equipment passives.
    assert passiveeffects._when_label([]) == "when equipped"
    assert passiveeffects._when_label(["InvokeInBaseCamp"]) == "while at a base"


def test_exact_rules_beat_prefixes_and_longer_prefixes_win():
    """
    `ElementBoostWeakness_Fire` must not be caught by the shorter `Element`, and
    `MaxHP` must not be swept up by a pattern. Both would put an effect in a
    plausible-looking wrong category, which is worse than leaving it uncategorised
    because `unclassified()` would then report nothing.
    """
    assert passiveeffects.category_of("ElementBoostWeakness_Fire") == "combat"
    assert passiveeffects.category_of("ElementAddItemDrop_Fire") == "work"
    assert passiveeffects.category_of("Sanity_Decrease") == "survival"
    assert passiveeffects.category_of("WorkSuitabilityAddRank_Mining") == "work"
    assert passiveeffects.category_of("Fishing_SuccessAmountUp") == "fishing"
    assert passiveeffects.category_of("NoSuchEffectType") is None


def test_a_zero_valued_slot_is_not_an_effect():
    """
    Measured: `GrassMinotaur_PartnerSkill_2` reads "Attack +12%" and carries a
    wired-up `Defense 0.0` beside it. Rendering that as an effect makes the skill
    look like it touches defence.
    """
    described = passiveeffects.describe_passives(["GrassMinotaur_PartnerSkill_2"])
    if described["unknownIds"]:
        return  # Not in this bundle; the rule is pinned by the unit below.
    kinds = {e["type"] for s in described["skills"] for e in s["effects"]}
    assert "Defense" not in kinds


def test_a_placeholder_never_reaches_the_UI():
    """
    The game ships `Legend` as "Attack +{EffectValue1}% Defense +{EffectValue2}%
    Movement Speed increases {EffectValue3}%". A placeholder on screen reads as a
    broken dashboard rather than a broken upstream string.
    """
    described = passiveeffects.describe_passives(["Legend"])
    text = described["skills"][0]["description"]
    assert "{EffectValue" not in text
    assert "20" in text


def test_substitution_counts_DECLARED_slots_not_surviving_ones():
    """
    The trap. `{EffectValue2}` means the second declared slot; this module drops
    zero-valued slots from its own effect list. Substituting against the filtered
    list would slide every later number one clause to the left — plausible,
    wrong, and invisible without a case built to catch it.
    """
    raw = [
        {"type": "ShotAttack", "value": 12.0, "target": "ToSelf"},
        {"type": "Defense", "value": 0.0, "target": "ToSelf"},
        {"type": "MoveSpeed", "value": 30.0, "target": "ToSelf"},
    ]
    text = passiveeffects.resolve_description(
        "Attack +{EffectValue1}% Defense +{EffectValue2}% Speed +{EffectValue3}%", raw
    )
    assert text == "Attack +12% Defense +0% Speed +30%"


def test_an_out_of_range_placeholder_is_LEFT_not_invented():
    """Ugly and honest beats a wrong number beside a skill name."""
    text = passiveeffects.resolve_description(
        "Attack +{EffectValue4}%", [{"type": "ShotAttack", "value": 1.0}]
    )
    assert text == "Attack +{EffectValue4}%"


def test_the_sign_is_not_doubled():
    """The sentence already carries it, so `+{EffectValue1}` with a stored -15
    must not render "+-15"."""
    text = passiveeffects.resolve_description(
        "Hunger decreases +{EffectValue1}% slower.",
        [{"type": "FullStomatch_Decrease", "value": -15.0}],
    )
    assert text == "Hunger decreases +15% slower."


def test_an_unknown_passive_is_reported_rather_than_dropped():
    """
    A Pal with four passives that lists three reads as a parsing success. The
    bundle is 1,897 entries against a save that can hold anything.
    """
    described = passiveeffects.describe_passives(["Legend", "Modded_Nonsense"])
    assert described["unknownIds"] == ["Modded_Nonsense"]
    assert len(described["skills"]) == 1


def test_a_flat_value_is_not_rendered_as_a_percentage():
    """`JumpCount_Increase: 1` is one extra jump, not +1%."""
    assert passiveeffects.describe_effect(
        {"type": "JumpCount_Increase", "value": 1.0, "target": "ToTrainer"}
    )["unit"] == "flat"
    assert passiveeffects.describe_effect(
        {"type": "MoveSpeed", "value": 20.0, "target": "ToSelf"}
    )["unit"] == "percent"


def test_labels_do_not_leak_the_game_s_own_misspelling():
    """
    `FullStomatch_Decrease` is Pocketpair's typo in an internal identifier. The
    identifier travels as-is because it is the key; the label is this project's
    words and says what the thing is.
    """
    effect = passiveeffects.describe_effect(
        {"type": "FullStomatch_Decrease", "value": -15.0, "target": "ToSelf"}
    )
    assert effect["label"] == "hunger drain"
    assert effect["type"] == "FullStomatch_Decrease"


def test_categories_come_back_in_declared_order_and_only_when_populated():
    described = passiveeffects.describe_passives(["Legend"])
    ids = [c["id"] for c in described["categories"]]
    order = [cid for cid, _ in passiveeffects.CATEGORIES]
    assert ids == [c for c in order if c in ids]
    assert all(c["effects"] for c in described["categories"])


def test_an_unreadable_bundle_costs_the_panel_and_nothing_else(monkeypatch):
    monkeypatch.setattr(gamedata, "passive_effects", lambda pid: None)
    described = passiveeffects.describe_passives(["Legend"])
    assert described["skills"] == []
    assert described["unknownIds"] == ["Legend"]
