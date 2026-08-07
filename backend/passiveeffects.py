"""
Everything a passive skill does, not the four terms the stat formula uses.

`palstats.PASSIVE_EFFECT_STATS` maps **four** effect types — `MaxHP`,
`ShotAttack`, `Defense`, `CraftSpeed` — and that is correct *for the formula*,
because those are its terms. The bundle carries **208 distinct effect types**, so
204 of them were invisible: sanity drain, hunger drain, carry weight, movement,
element damage, capture rate, egg hatching, fishing, breeding speed.

**DO NOT WIDEN `PASSIVE_EFFECT_STATS` TO FIX THAT.** Those four are multiplied
into `final = floor(subtotal x (1+soul) x (1+passive))`. `Mining` in that map
multiplies a work *level* by a percentage, which is not a smaller version of the
right answer, it is a meaningless one. This module is **description**, not
arithmetic — nothing here feeds a stat — so it has no verification burden beyond
naming things correctly and never silently dropping one.

## Three filters, and the point is that they differ

`palstats` excludes `InvokeWorker`, `InvokeInBaseCamp` and `InvokeRiding`
deliberately: a skill that only fires while the Pal works at a base is not part
of the stat block the game displays on that Pal. **For a "what does this Pal
actually do" panel those must appear** — a Pal whose passive cuts its own sanity
drain is materially better at a draining job, and that is exactly the fact a
palbox stat line has no room for.

So the policy here is *label, never filter*: every effect is returned with who it
reaches (`affects`) and when it fires (`when`). `PASSIVE_SELF_INVOKES` is
deliberately **not** reused — it answers a different question, and sharing it is
how two surfaces silently converge on one of their two answers.

That matters most for `ToTrainer`, which is 669 of the bundle's 2,057 effects.
Those are buffs to the *player*, not the Pal. `palstats` drops them, correctly.
Dropping them here would hide the entire point of most partner skills.

## The category map is rules first, and the leftovers are visible

Most of the 208 fall out of their own name — `ElementBoost_*`,
`ResistAdditionalEffect_*`, `Fishing_*`, `WorkSuitabilityAddRank_*`. Those are
prefix rules, so a game update adding a tenth element classifies without anyone
editing this file.

The rest are listed. **A type matching no rule is reported, not swallowed**:
`unclassified()` is the same device as `elements.unknown_to_chart()` — empty is
the healthy state, and a test pins it, because an uncategorised effect that
quietly vanishes is indistinguishable from one the game does not have.
"""

from __future__ import annotations

import re
from typing import Any, Optional

import gamedata

#: Category order, as a panel should read them: what it does in a fight, then at
#: a base, then everything that is about the player rather than the Pal.
CATEGORIES: tuple[tuple[str, str], ...] = (
    ("combat", "Combat"),
    ("survival", "Survival"),
    ("work", "Work & gathering"),
    ("movement", "Movement"),
    ("capture", "Capture & breeding"),
    ("fishing", "Fishing"),
    ("status", "Status effects"),
    ("player", "Player abilities"),
    ("economy", "Economy"),
)

_CATEGORY_LABELS = dict(CATEGORIES)

#: Prefix rules, longest first at match time. A rule earns its place by covering
#: a family the game will extend — nine elements today, ten after an update.
_PREFIX_RULES: tuple[tuple[str, str], ...] = (
    ("ElementBoostWeakness_", "combat"),
    ("ElementBoost_", "combat"),
    ("ElementResist_", "combat"),
    ("ElementAddItemDrop_", "work"),
    ("ResistAdditionalEffect_", "status"),
    ("AdditionalEffect_", "status"),
    ("DamageRateIfDefender_", "status"),
    ("AttackRateIfAttacker_", "status"),
    ("SlipDamageRate_", "status"),
    ("CaptureLevelUpIfTarget_", "capture"),
    ("WorkSuitabilityAddRank_", "work"),
    ("FishingSalvage_", "fishing"),
    ("Fishing_", "fishing"),
    ("TemperatureResist_", "survival"),
    ("MoveSpeed", "movement"),
    ("PlayerInflictEffect_", "player"),
    ("PlayerElementStepAttack_", "player"),
    ("Player_", "player"),
    ("Player", "player"),
    ("DamageUpIfEquipped_", "combat"),
    ("ShopBuyPrice_", "economy"),
    ("ShopSellPrice_", "economy"),
    ("FarmCrop", "work"),
    ("Element", "combat"),
)

#: Types with no family to belong to. Listed rather than pattern-matched because
#: guessing from a substring is how `Mute` becomes a movement skill.
_EXACT_RULES: dict[str, str] = {
    # Combat
    "MaxHP": "combat", "ShotAttack": "combat", "MeleeAttack": "combat",
    "Defense": "combat", "AttackSpeedUp": "combat", "ReloadSpeedUp": "combat",
    "BulletSpeed": "combat", "BulletAccuracy": "combat", "Recoil": "combat",
    "Explosive": "combat", "Homing": "combat", "LifeSteal": "combat",
    "BodyPartsWeakDamage": "combat", "DamageRateByEquippedWeapon": "combat",
    "DamageUpToNonBattleEnemy": "combat", "DamageUp_LastBullet": "combat",
    "DamageUpPartnerSkillAttack": "combat", "BulletHit_StackBuff": "combat",
    "DefeatEnemy_StackBuff": "combat", "ExplosionResist": "combat",
    "DefeatEnemy_ActiveSkillCoolTime_Decrease": "combat",
    "ActiveSkillCoolTime_Decrease": "combat", "AttackRateHPThreshold": "combat",
    "DefenseRateHPThreshold": "combat", "ShieldDamageCutRate": "combat",
    "BuildingDamageReduction": "combat", "Support": "combat",
    "AvoidDurationUp_PartnerSkill": "combat", "AvoidDurationUp_EquipSkill": "combat",
    "PartnerSkillCoolTime_Decrease": "combat", "CurveType": "combat",
    "ForYakushimaDefenceRate": "combat", "Mute": "combat",
    "Defuser_ExplosiveSpore": "combat", "EnemySightDetectionRate": "combat",
    # Survival
    "Sanity_Decrease": "survival", "FullStomatch_Decrease": "survival",
    "Regene_HP": "survival", "Regene_HP_Rate": "survival",
    "AutoHPRegeneRate": "survival", "RecoverHPOnHPThreshold": "survival",
    "Regene_Stomatch_Hungriest": "survival", "FallDamageRate": "survival",
    "LavaDamageInvalid": "survival", "ItemCorruptionSpeedRate": "survival",
    "EquipmentDurabilityRate": "survival",
    # Work & gathering
    "CraftSpeed": "work", "Mining": "work", "Logging": "work",
    "CollectItemDrop": "work", "CollectItemDrop_NaturalObject": "work",
    "GainItemDrop": "work", "MeatCutAddItemDrop": "work",
    "SelfDeathAddItemDrop": "work", "ItemWeightReduction": "work",
    "MaxInventoryWeight": "work",
    # Movement
    "SwimSpeed": "movement", "ClimbMoveSpeedRate": "movement",
    "AirDash": "movement", "LowGravity": "movement",
    "JumpCount_Increase": "movement", "JumpPower_Increase": "movement",
    "RideJumpCount_Increase": "movement",
    # Capture & breeding
    "CaptureLevel": "capture", "CaptureLevel_SneakBonus": "capture",
    "SphereRecovery": "capture", "SyncroPassiveWhenCapture": "capture",
    "BreedSpeed": "capture", "BreedSpeed_InBaseCamp": "capture",
    "PalEggHatchingSpeed": "capture", "EggAlphaConversion": "capture",
    "EggObtainExtraEgg": "capture", "PalExp_Increase": "capture",
    "PalSP_Increase": "capture", "FriendshipPoint_Increase": "capture",
}

#: Effect targets, and who each one actually reaches. `None` occurs exactly once
#: in the bundle — `Rare`'s defence, whose own description reads as a self buff —
#: so it is an unset field rather than a category.
_TARGET_AFFECTS: dict[str, str] = {
    "ToSelf": "pal",
    "None": "pal",
    "ToTrainer": "player",
    "ToSelfAndTrainer": "pal_and_player",
    "ToTrainerAndOtomo": "player_and_party",
    "ToOtomo": "party",
    "ToActiveOtomo": "active_party",
    "ToBaseCampPal": "base_pals",
    "ToBuildObject": "structures",
}

_AFFECTS_LABELS: dict[str, str] = {
    "pal": "this Pal",
    "player": "you",
    "pal_and_player": "this Pal and you",
    "player_and_party": "you and your party",
    "party": "your party",
    "active_party": "your active Pal",
    "base_pals": "Pals at the base",
    "structures": "structures",
}

#: When a passive fires. An empty invoke list is its own answer and a common one
#: — 156 of the 1,897 — and those are equipment passives rather than Pal ones.
_INVOKE_LABELS: dict[str, str] = {
    "InvokeAlways": "always",
    "InvokeInOtomo": "while in your party",
    "InvokeActiveOtomo": "while out as your active Pal",
    "InvokeRiding": "while being ridden",
    "InvokeWorker": "while working",
    "InvokeInBaseCamp": "while at a base",
    "InvokeReserve": "while in reserve",
}

#: Effect types whose value is a flat count rather than a percentage. Everything
#: else in the bundle is a rate, which the game's own prose confirms — rendering
#: `JumpCount_Increase: 1` as "+1%" would be nonsense in a way nobody would spot.
_ABSOLUTE_VALUES = frozenset({
    "JumpCount_Increase", "RideJumpCount_Increase", "CaptureLevel",
    "CaptureLevel_SneakBonus", "EggObtainExtraEgg", "CurveType",
})

#: Labels where splitting the camel case produces something wrong or ugly.
#: These are presentation, not game data — the game ships no string for an effect
#: type — so `FullStomatch_Decrease` becomes "hunger drain" rather than
#: preserving Pocketpair's misspelling in a label nobody asked to see.
_LABEL_OVERRIDES: dict[str, str] = {
    "FullStomatch_Decrease": "hunger drain",
    "Regene_Stomatch_Hungriest": "hunger recovery when starving",
    "Sanity_Decrease": "sanity drain",
    "ShotAttack": "attack",
    "MeleeAttack": "melee attack",
    "CraftSpeed": "work speed",
    "MaxHP": "max HP",
    "PalExp_Increase": "Pal EXP",
    "PalSP_Increase": "Pal skill points",
    "FriendshipPoint_Increase": "trust gain",
    "ItemWeightReduction": "item weight",
    "MaxInventoryWeight": "carry capacity",
    "CollectItemDrop": "gathering yield",
    "CollectItemDrop_NaturalObject": "yield from natural objects",
    "GainItemDrop": "drops from defeated Pals",
    "MeatCutAddItemDrop": "meat from butchering",
    "SelfDeathAddItemDrop": "drops on its own death",
    "SphereRecovery": "Pal Sphere recovery",
    "CaptureLevel": "capture power",
    "CaptureLevel_SneakBonus": "capture power when sneaking",
    "EggAlphaConversion": "chance a bred egg is an alpha",
    "EggObtainExtraEgg": "extra eggs",
    "PalEggHatchingSpeed": "egg hatching speed",
    "BreedSpeed": "breeding speed",
    "BreedSpeed_InBaseCamp": "breeding speed at a base",
    "Regene_HP": "HP regeneration",
    "Regene_HP_Rate": "HP regeneration rate",
    "AutoHPRegeneRate": "passive HP regeneration",
    "EquipmentDurabilityRate": "equipment wear",
    "ItemCorruptionSpeedRate": "food spoilage",
    "BodyPartsWeakDamage": "damage to weak points",
    "DamageRateByEquippedWeapon": "damage with the equipped weapon",
    "EnemySightDetectionRate": "enemy detection range",
    "LavaDamageInvalid": "immunity to lava damage",
    "FallDamageRate": "fall damage",
    "CurveType": "projectile arc",
    "Mute": "silence",
    "Support": "support",
}

#: `{EffectValueN}` in the game's own prose. The placeholder index is 1-based and
#: counts EVERY declared slot, including the zero-valued ones this module filters
#: out of its own effect list — so substitution has to run against the raw list
#: or the numbers land in the wrong sentence.
_PLACEHOLDER = re.compile(r"\{EffectValue(\d+)\}")

_SPLIT = re.compile(r"(?<!^)(?=[A-Z])")


def resolve_description(description: str, raw_effects: list) -> str:
    """
    Substitute the extracted numbers into the game's own sentence.

    Palworld ships `Legend` as *"Attack +{EffectValue1}% Defense +{EffectValue2}%
    Movement Speed increases {EffectValue3}%"*, and the archive this project used
    before had already failed to substitute four of them — a placeholder reaching
    a player reads as a broken dashboard rather than a broken upstream string.

    **The extracted table is the authority**: 1,754 of the 1,759 passives with a
    numeric description agree with it exactly. Where the prose has a placeholder
    there is nothing to disagree with, so this is filling a gap rather than
    overruling anything.

    `raw_effects` must be the **unfiltered** list. The index is positional over
    declared slots, so dropping a zero-valued one first shifts every later number
    into the wrong clause.
    """
    text = str(description or "")
    if "{EffectValue" not in text:
        return text

    def substitute(match: re.Match) -> str:
        index = int(match.group(1)) - 1
        if not (0 <= index < len(raw_effects)):
            # Leave the placeholder rather than inventing a number. It is ugly
            # and it is honest; a wrong figure beside a skill name is not.
            return match.group(0)
        value = float((raw_effects[index] or {}).get("value") or 0.0)
        # The sentence already carries the sign ("Attack +{EffectValue1}%"), so
        # emitting a signed number gives "+-15".
        value = abs(value)
        return str(int(value)) if value == int(value) else str(value)

    return _PLACEHOLDER.sub(substitute, text)


def category_of(effect_type: str) -> Optional[str]:
    """
    Which category an effect type belongs to, or None if no rule covers it.

    Exact rules win over prefixes, and prefixes are tried longest-first, so
    `ElementBoostWeakness_Fire` cannot be captured by the shorter `Element`.
    """
    name = str(effect_type or "")
    if not name:
        return None
    if name in _EXACT_RULES:
        return _EXACT_RULES[name]
    for prefix, category in sorted(_PREFIX_RULES, key=lambda r: -len(r[0])):
        if name.startswith(prefix):
            return category
    return None


def unclassified() -> list[str]:
    """
    Bundled effect types no rule covers. **Empty is the healthy state.**

    Same device as `elements.unknown_to_chart()`, for the same reason: this is
    the one part of the module that can silently rot. A content update adding an
    effect type would otherwise drop it from every panel, and a missing line
    reads as a Pal that does not have the skill rather than as a gap here.
    """
    bundle = gamedata.passive_effects_all() or {}
    seen: set[str] = set()
    for entry in bundle.values():
        for effect in (entry or {}).get("effects") or []:
            name = str(effect.get("type") or "")
            if name and category_of(name) is None:
                seen.add(name)
    return sorted(seen)


def _humanise_effect(effect_type: str) -> str:
    """
    `ElementBoost_Dark` -> "Dark damage". A label, not a translation: the game
    ships no string for these, so this is presentation rather than game data and
    must not be presented as the game's own words.
    """
    name = str(effect_type or "")
    if name in _LABEL_OVERRIDES:
        return _LABEL_OVERRIDES[name]
    for prefix, template in (
        ("ElementBoostWeakness_", "{} damage to weak targets"),
        ("ElementBoost_", "{} damage"),
        ("ElementResist_", "{} resistance"),
        ("ElementAddItemDrop_", "{} drops"),
        ("ResistAdditionalEffect_", "{} resistance"),
        ("AdditionalEffect_", "inflicts {}"),
        ("DamageRateIfDefender_", "damage taken while {}"),
        ("AttackRateIfAttacker_", "damage dealt while {}"),
        ("SlipDamageRate_", "{} damage over time"),
        ("CaptureLevelUpIfTarget_", "capture rate on {} targets"),
        ("WorkSuitabilityAddRank_", "{} work rank"),
        ("TemperatureResist_", "{} resistance"),
    ):
        if name.startswith(prefix):
            return template.format(_words(name[len(prefix):]))
    return _words(name)


def _words(name: str) -> str:
    text = str(name).replace("_", " ").strip()
    parts = [p for chunk in text.split(" ") for p in _SPLIT.split(chunk) if p]
    return " ".join(parts)


def describe_effect(effect: dict) -> dict[str, Any]:
    """One effect, named and categorised. `category` is None when no rule fits."""
    effect_type = str((effect or {}).get("type") or "")
    target = str((effect or {}).get("target") or "None")
    value = float((effect or {}).get("value") or 0.0)
    affects = _TARGET_AFFECTS.get(target, "other")
    absolute = effect_type in _ABSOLUTE_VALUES
    category = category_of(effect_type)
    return {
        "type": effect_type,
        "label": _humanise_effect(effect_type),
        "value": value,
        # The unit travels rather than being baked into a formatted string, so a
        # client can right-align a column of numbers instead of parsing "+20%".
        "unit": "flat" if absolute else "percent",
        "category": category,
        "categoryLabel": _CATEGORY_LABELS.get(category or "", ""),
        "target": target,
        "affects": affects,
        "affectsLabel": _AFFECTS_LABELS.get(affects, "something else"),
    }


def describe_passives(passive_ids: list) -> dict[str, Any]:
    """
    Every effect of every passive a Pal carries, grouped by category.

    **Nothing is filtered.** Effects that reach the player, the party or the base
    are labelled as such and returned; `palstats` drops exactly those, because it
    is computing this Pal's own stat block and they do not belong in it. Two
    surfaces, two policies, written out separately on purpose.

    Unknown ids are reported in `unknownIds` rather than dropped — a modded or
    newer passive is a fact about the save worth showing, and a Pal with four
    passives that lists three reads as a parsing success.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    skills: list[dict[str, Any]] = []
    unknown: list[str] = []

    for passive_id in passive_ids or []:
        key = str(passive_id)
        entry = gamedata.passive_effects(key)
        if entry is None:
            unknown.append(key)
            continue

        invokes = [str(i) for i in (entry.get("invoke") or [])]
        described = gamedata.describe_passive(key)
        raw_effects = list(entry.get("effects") or [])
        effects = [describe_effect(e) for e in raw_effects
                   # A declared slot with value 0.0 is not an effect. Measured:
                   # `GrassMinotaur_PartnerSkill_2` reads "Attack +12%" and
                   # carries a wired-up `Defense 0.0` beside it, which made the
                   # skill look like it touched defence. Note this filter runs
                   # AFTER `resolve_description` reads the raw list — the
                   # placeholder index counts declared slots, not surviving ones.
                   if float(e.get("value") or 0.0) != 0.0]

        skill = {
            "id": key,
            "name": described.get("name") or key,
            "description": resolve_description(
                described.get("description") or "", raw_effects
            ),
            "rank": entry.get("rank", described.get("rank", 0)),
            "icon": described.get("icon", ""),
            "invoke": invokes,
            "whenLabel": _when_label(invokes),
            "effects": effects,
        }
        skills.append(skill)

        for effect in effects:
            row = dict(effect)
            row["skillId"] = key
            row["skillName"] = skill["name"]
            row["whenLabel"] = skill["whenLabel"]
            groups.setdefault(effect["category"] or "other", []).append(row)

    ordered = [
        {"id": cid, "label": label, "effects": groups[cid]}
        for cid, label in CATEGORIES if cid in groups
    ]
    if "other" in groups:
        # Visible rather than dropped, and last. See `unclassified()`.
        ordered.append({"id": "other", "label": "Uncategorised", "effects": groups["other"]})

    return {
        "skills": skills,
        "categories": ordered,
        "unknownIds": unknown,
        # The client is the thing about to render these beside a stat block, so
        # it is the thing that has to be told they are not part of one.
        "note": (
            "These are every effect the game's own passive table records, "
            "including ones that buff you rather than the Pal. Only HP, attack, "
            "defence and work speed feed the calculated stats."
        ),
    }


def _when_label(invokes: list) -> str:
    if not invokes:
        # 156 of 1,897, and they are equipment passives rather than Pal ones.
        return "when equipped"
    named = [_INVOKE_LABELS.get(str(i), _words(str(i)).lower()) for i in invokes]
    if "always" in named:
        return "always"
    return " or ".join(dict.fromkeys(named))


def catalogue() -> dict[str, Any]:
    """The category vocabulary, for a UI that wants to build filters from it."""
    return {
        "categories": [{"id": cid, "label": label} for cid, label in CATEGORIES],
        "affects": [{"id": k, "label": v} for k, v in _AFFECTS_LABELS.items()],
        "unclassified": unclassified(),
    }
