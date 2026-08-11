"""
What a Pal RESISTS — the defensive half of a passive set, which nothing read.

`palstats.passive_bonuses` maps four effect types onto the four stats the game
prints, and it is correct for that job. Being the only reader is what made it a
blind spot: `ElementResist_Fire_1` is `ToSelf` / `InvokeAlways` at a flat **15%
reduction in incoming Fire damage**, on an ordinary passive any catchable Pal can
roll, and it has never appeared anywhere in this dashboard. Two Pals identical in
HP, Attack, Defense and Work Speed can differ by 35% of the damage they take.

**Third occurrence of the same shape**, which is why this is a separate module
rather than a wider constant in `palstats`: work-suitability passives (#94) and
partner skills (#103) were both invisible because the one filter that looked at
the effect table was written for a different surface. A second reader with its
own stated policy is the fix that generalises.

## What counts, and what the prose says

Every claim below is checked against the game's own English description, which is
the acceptance test `extract-passive-effects.py` already uses (1,754 of 1,759
numeric descriptions matched their extracted values):

| Effect type | Effects | The game's own words |
|---|---:|---|
| `ElementResist_<element>` | 120 | *"Fire damage resistance +15%"* |
| `ResistAdditionalEffect_<ailment>` | 63 | *"Burn resistance +100%"* |
| `ExplosionResist` | 8 | *"Explosion Resistance +60%"* |
| `TemperatureResist_Cold` / `_Heat` | 16 | *"Heat Resistance +1%"* |
| `DefenseRateHPThreshold` | 1 | *"Defense (Low HP) +50%"* |

`ResistAdditionalEffect_*` is **always 100.0** — every one of the 63. So it is
immunity to an ailment, not a reduction in it, and reporting it as a percentage
beside a 15% element figure would invite adding two things that are not the same
kind of number.

## `DamageRateIfDefender_*` IS OFFENSIVE, AND ITS NAME SAYS OTHERWISE

53 effects across eight ailments, `ToSelfAndTrainer`, values 30-70. It reads
exactly like "the rate at which I take damage as a defender" and task #104's own
description filed it under buffs-the-Pal. The game's prose settles it:

    DamageUpTrainerAndOtomo_ToPoison  ->  "Damage vs Poison +70%"

It is damage **you deal** to a defender who is suffering that ailment. Folding it
in would have shown a Pal as 70% more resistant to poison when the passive makes
it hit *poisoned enemies* harder — wrong in the one direction nobody would
question, because the number is large and the sign is comforting.

The transferable half is the one this project keeps writing down: a plausible
reading of a field name is not a reading of the field. The prose column exists
precisely so this is checkable, and checking it cost one query.

## The policy is restated, not imported

`PASSIVE_SELF_INVOKES` and `PASSIVE_SELF_TARGETS` in `palstats` mean "part of the
stat block the game prints on a palbox Pal". That is a narrower question than
this one and the sets happen to coincide today; importing them would make a
future correction to one silently change the other. They are written out here
with this module's own reason attached.

**`InvokeInOtomo` IS included and `palstats` excludes nothing of the sort** — a
resistance that applies while the Pal is out with you is exactly the resistance
that matters in a boss fight, and `when` says which is which rather than merging
them.

## What it will not say

- **No effective-HP figure.** How an element resistance composes with the type
  chart's `DamageElementMatchRate = 1.2` is stated in no file — `buildplanner`
  carries `stackingKnown: false` for the same reason. "15% fire resistance" is
  supported; "survives 18% longer against Fire" is a mechanic nobody has read.
- **No sort key.** Same argument `optimise.py` holds for matchups: there is no
  coefficient to rank by, so a score would be invented. This returns badges.
"""

from __future__ import annotations

import functools
import logging
from typing import Any

import elements
import gamedata

logger = logging.getLogger(__name__)

# Effect types that reduce damage *this Pal* takes, or protect it from a state.
#
# `ElementResist_*` and `ResistAdditionalEffect_*` are matched by prefix because
# their suffix is the element or ailment; the rest are whole names.
_ELEMENT_PREFIX = "ElementResist_"
_AILMENT_PREFIX = "ResistAdditionalEffect_"

# Flat defensive effects, each with the unit its own prose uses.
_FLAT_RESISTS = {
    "ExplosionResist": "explosion",
    "TemperatureResist_Cold": "cold",
    "TemperatureResist_Heat": "heat",
    # Location-gated: Yakushima is a place, so this is real and conditional.
    "ForYakushimaDefenceRate": "yakushima",
}

# Conditional on the Pal's own state rather than on what is hitting it. Kept
# separate so a client cannot render "Defense +50%" beside an unconditional one.
_CONDITIONAL = {"DefenseRateHPThreshold": "lowHp"}

# **OFFENSIVE, despite the name.** See the module docstring — the game's prose is
# "Damage vs Poison +70%". Named here rather than merely absent, so the next
# person to grep for a resistance family finds the reason instead of the gap.
_NOT_A_RESISTANCE = "DamageRateIfDefender_"

# Targets that mean "the Pal carrying this skill". Same three values
# `palstats.PASSIVE_SELF_TARGETS` holds, and for the same measured reason:
# `None` occurs exactly once in the bundle, on `Rare`, whose own description
# proves it is a self buff with an unset field rather than a category.
_SELF_TARGETS = {"ToSelf", "ToSelfAndTrainer", "None"}

# When a resistance applies. Unlike the stat formula, `InvokeInOtomo` counts —
# a buff that fires while the Pal is out fighting is the whole point here — but
# it is labelled rather than merged, because "always" and "only when deployed"
# are different answers to "is this Pal a good Fire tank".
_INVOKE_WHEN = {
    "InvokeAlways": "always",
    "InvokeActiveOtomo": "deployed",
    "InvokeInOtomo": "deployed",
}

# A passive with NO invoke condition at all. 9 of the 120 element-resist effects
# are like this — the `_BossDefeat` tier, at 1% each. Treated as "always" with
# the absence recorded, never dropped: an empty invoke list is the file not
# stating a condition, which is not the same as stating a restrictive one.
_NO_INVOKE = "always"


@functools.lru_cache(maxsize=256)
def _soft_to(defenders: tuple) -> tuple:
    """
    Which elements beat a Pal with these elements — the chart, memoised.

    `/api/pals` calls `profile` once per Pal, and this half of it depends only on
    the species. Recomputed per Pal it cost **185 ms** on the reference world's
    1,905 — nine `matchup` calls each against a relation that has at most a few
    dozen distinct inputs. There are 9 elements and Pals carry one or two, so the
    cache is small and complete rather than an eviction gamble.

    Keyed on a tuple because `lru_cache` needs one; the caller converts.
    """
    return tuple(sorted({
        attacker for attacker in elements.game_elements()
        if elements.matchup([attacker], list(defenders)) == "strong"
    }))


def _bucket(effect_type: str) -> tuple[str, str]:
    """`(kind, key)` for an effect type, or `("", "")` if it is not a resistance."""
    if effect_type.startswith(_ELEMENT_PREFIX):
        # **The element comes from the effect TYPE, never the skill id.** The ids
        # use a third vocabulary — `ElementResist_Aqua_1` carries effect type
        # `ElementResist_Water` — and `Aqua`/`Thunder` resolve to nothing in
        # `elements.canonical`. Reading the id would drop Water and Electric
        # resistance entirely while every other element worked.
        raw = effect_type[len(_ELEMENT_PREFIX):]
        return ("element", elements.canonical(raw) or raw)
    if effect_type.startswith(_AILMENT_PREFIX):
        return ("ailment", effect_type[len(_AILMENT_PREFIX):])
    if effect_type in _FLAT_RESISTS:
        return ("other", _FLAT_RESISTS[effect_type])
    if effect_type in _CONDITIONAL:
        return ("conditional", _CONDITIONAL[effect_type])
    return ("", "")


def resistances(passive_ids: list) -> dict[str, Any]:
    """
    What a passive set makes this Pal resistant to, grouped by kind.

    Element percentages stack **additively** within an element, matching how
    `passive_bonuses` treats the stat terms — three sources of Fire resistance
    are reported as one figure. Whether the game itself adds or multiplies them
    is unstated, and `stackingKnown` says so rather than the number implying it.

    Unknown ids contribute nothing rather than raising, for the reason
    `passive_bonuses` gives: the bundle is 1,897 entries against a save that can
    hold anything, and a modded passive should cost its own term, not the row.
    """
    element: dict[str, float] = {}
    ailment: dict[str, float] = {}
    other: dict[str, float] = {}
    conditional: dict[str, float] = {}
    when: dict[str, str] = {}
    sources: list[dict[str, Any]] = []

    for passive_id in passive_ids or []:
        entry = gamedata.passive_effects(str(passive_id))
        if not entry:
            continue
        invokes = [str(i) for i in (entry.get("invoke") or [])]
        applies = _NO_INVOKE if not invokes else next(
            (_INVOKE_WHEN[i] for i in invokes if i in _INVOKE_WHEN), "")
        if not applies:
            # Base-camp and worker-only invokes land here. A resistance that
            # fires only while the Pal is assigned to a furnace is not a fact
            # about the Pal in your party.
            continue

        for effect in entry.get("effects") or []:
            effect_type = str(effect.get("type") or "")
            if str(effect.get("target") or "") not in _SELF_TARGETS:
                continue
            kind, key = _bucket(effect_type)
            if not kind:
                continue
            value = float(effect.get("value") or 0.0)
            bucket = {"element": element, "ailment": ailment,
                      "other": other, "conditional": conditional}[kind]
            bucket[key] = bucket.get(key, 0.0) + value
            # "always" wins over "deployed" when a Pal has both, because the
            # stronger condition is the one that is true more often.
            if when.get(key) != "always":
                when[key] = applies
            sources.append({
                "passiveId": str(passive_id),
                "type": effect_type,
                "kind": kind,
                "key": key,
                "value": value,
                "when": applies,
            })

    return {
        # `{element: percent}` — a reduction in incoming damage of that element.
        "elements": {k: round(v, 2) for k, v in sorted(element.items())},
        # Always 100 in the whole bundle, so this is immunity. `immune` is the
        # honest reading and is reported as such rather than as a percentage
        # that invites being added to the element figures.
        "ailments": {k: {"percent": round(v, 2), "immune": v >= 100.0}
                     for k, v in sorted(ailment.items())},
        "other": {k: round(v, 2) for k, v in sorted(other.items())},
        # Real, and true only sometimes. Separate so nothing renders it flat.
        "conditional": {k: round(v, 2) for k, v in sorted(conditional.items())},
        "when": when,
        "sources": sources,
        "any": bool(element or ailment or other or conditional),
        # **How this composes with the type chart is not in any file.** The 1.2
        # multiplier and a 15% reduction are two numbers nobody has read
        # together, so a client must show them as separate lines.
        "stackingKnown": False,
        "matchRate": elements.match_rate(),
        # Said out loud because the name of the excluded family is the trap.
        "offensiveTypesExcluded": [_NOT_A_RESISTANCE + "*"],
    }


def profile(species_elements: list, passive_ids: list) -> dict[str, Any]:
    """
    `resistances`, plus the boss-planner question: which elements is this soft to?

    **Soft is not the complement of resistant.** A Pal with no Fire resistance is
    not "weak to Fire" — it takes normal damage. What makes it soft is the type
    chart: its own element being one an attacker beats, which is the *defender*
    side of the single `DamageElementMatchRate` this game has. The two facts are
    independent, so both are reported: a Grass Pal with 35% Fire resistance and a
    Grass Pal without are in genuinely different trouble against the same Fire
    boss, and neither number alone says that.

    `softTo` here is the same computation `bossplanner.counters` calls
    `avoidElements`, read from the other side. Duplicating the chart lookup was
    the alternative and it is how two answers about one relation drift apart.
    """
    out = resistances(passive_ids)
    defenders = [d for d in (species_elements or []) if d]
    soft = _soft_to(tuple(defenders))
    out["softTo"] = list(soft)
    # The overlap is the interesting row: an element that beats this Pal AND that
    # it carries a resistance to. Reported as a list rather than as a combined
    # figure, for the reason `stackingKnown` states.
    out["softToButResists"] = [e for e in soft if out["elements"].get(e)]
    out["defenderElements"] = defenders
    return out


def against(species_elements: list, passive_ids: list,
            attacker_element: str) -> dict[str, Any]:
    """
    How this Pal fares against one element — the two terms, never multiplied.

    `bonusToAttacker` is the chart: 1.2 when the attacker's element beats this
    Pal's, 1.0 otherwise, read from the defender's side of the one constant the
    game ships. `resistPercent` is the passive term. **They are returned
    separately** because no file states how they compose, and a single "effective
    damage taken" figure would be inventing that composition in the one place a
    player would trust it.
    """
    attacker = elements.canonical(attacker_element) or ""
    defenders = [d for d in (species_elements or []) if d]
    profile_ = resistances(passive_ids)
    beaten = bool(attacker) and elements.matchup([attacker], defenders) == "strong"
    return {
        "attackerElement": attacker,
        "defenderElements": defenders,
        "chartFavoursAttacker": beaten,
        "bonusToAttacker": elements.match_rate() if beaten else 1.0,
        "resistPercent": profile_["elements"].get(attacker, 0.0),
        "resistWhen": profile_["when"].get(attacker),
        # The two terms above are the whole answer and they do not combine here.
        "stackingKnown": False,
    }
