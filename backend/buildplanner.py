"""
Which Pal is fastest, toughest or hardest-hitting at a build you choose.

The question this answers is the one an operator actually asks — *"at level 80,
four stars, with these passives, which mount is quickest?"* — and it answers it
over the whole species table rather than over the Pals somebody happens to own.
`optimise.py` ranks a **world**; this ranks the **game**.

## Movement carries no build term IN THE FILES — which is not "none"

**This section overclaimed and was corrected 2026-08-11, after the operator
challenged it.** It said flatly that a maxed Pal is not faster. What is actually
established is narrower, and the difference is the whole point:

- **Fact.** `RideSprintSpeed`, `RunSpeed`, `SwimSpeed` and `Stamina` are flat
  per-species columns with no level, IV, condenser or soul term beside them.
- **Fact.** `StatusCalculate_GenkaiToppa_PerAdd = 0.05` is the condenser bonus,
  and `palstats` applies it to HP, Attack, Defense and CraftSpeed.
- **NOT established.** That those four are its *only* targets. `palstats`
  transcribes a community-derived formula, and those four are precisely the
  stats the game prints a number for — so they are the only ones anybody could
  ever have checked it against.

The naming cuts *against* the old claim rather than for it. Every other constant
in that family is stat-suffixed — `StatusCalculate_ConstPlus_Attack`,
`_LevelMultiply_HP`, `_LevelMultiply_Defense`, `_TribeMultiply_CraftSpeed`.
**`GenkaiToppa_PerAdd` carries no suffix at all.**

**The precedent is work suitability, and it is exactly this shape.** That bonus
is applied at load, is invisible in the save, appears in no DataTable, and was
answered "no" three times before the operator turned out to be right. A movement
bonus applied at load would look identical from here: absent from every file,
and real.

So the payload says `movementInFiles` — the columns carry no build term — and
never a claim of absence. Nothing here multiplies a speed by a bonus the game
has not stated, and nothing here asserts there is none.

### AND THE CONDENSER DOES CHANGE MOVEMENT — VIA THE PARTNER SKILL

**Confirmed 2026-08-11 from the operator's observation, then found in the
files.** Direhowl's move speed rises with rank, and not every Pal's does. Both
halves of the argument above were correct and the *connection* was missed:
`DT_PartnerSkillParameter.PassiveSkills` is a list **indexed by condenser
rank**, which is #103's own finding sitting one module over in the bundle.

    Garm (Direhowl).ranks = [ [],                                # rank 1: none
                              MoveSpeed_up_PartnerSkill_Ride_1,  # rank 2: +10%
                              _2, _3, _4 ]                       # +12/15/20%

`partner_movement()` reads it and `rank()`/`compare()` apply it, broken out as
`partnerBonus` so a player can see which part four stars bought. **96 of the
species/forms have a movement-scaling partner skill**; the rest genuinely gain
nothing, which is the "not all Pals" half of the observation. Which *figure*
moves also varies — Azurobe's is `SwimSpeed`, Dazemu's the terrain-gated
`MoveSpeed_Ground` — which is the other half.

**Two questions, and only one is now answered.** Whether
`GenkaiToppa_PerAdd` *also* multiplies the speed columns is untouched by this
and still needs a timing run, so it keeps its own flag
(`condenserOnSpeedColumns`) rather than being buried by the half that got
resolved. Collapsing the two would be how a partial answer becomes a wrong one.

**Passives demonstrably do change movement.** `MoveSpeed`, `SwimSpeed` and
friends are real effect types on real passives — Legend is +20% — so that route
is established rather than inferred.

## `InvokeRiding` is why this module has its own passive policy

`palstats.PASSIVE_SELF_INVOKES` excludes `InvokeRiding`, correctly: a buff that
fires only while a Pal is being ridden is not part of the stat block the game
prints on a palbox Pal. For *ride speed* it is the whole point. So the invoke
sets live here rather than being widened there, which is the same argument
AGENTS.md records for `passiveeffects` existing as a second reader.

Effects are **grouped by when they apply and never silently summed**: `always`,
`riding`, and `conditional` for the ones gated on night or terrain. A single
"+150% speed" that quietly includes a night-only bonus is a worse answer than
three honest numbers.

## What it will not say

- **How a speed converts to metres per second.** The column is the game's own
  unit and nothing states the scale.

*(This list began with "Whether a mount flies, swims or walks. In no file." —
see the correction below.)*

## THE MOUNT MODE IS IN A FILE, AND `BP_Pal_*` WAS THE MISTAKE

Retracted 2026-08-12. The avenues recorded above were all real dead ends: the
client pak has no `BP_Pal_<species>` blueprint, and the 213 per-species
animation folders attribute nothing. The conclusion drawn from them was wrong,
because the **server** pak carries the species blueprints under a different
name:

    PalActorBP/<Species>/BP_<Species>  ->  StaticCharacterParameterComponent
                                             MovementType = EPalMonsterMovementType::Fly

`mountMode` is read from `gamedata.movement_mode` now, and `mountModeKnown` is
True. **Fastest rideable flyer is Jetragon at 3,300**, which this module was
built saying it could not answer.

`GroundOnly` is still an **inference** — nothing states the native default, only
that the 31 overrides are exactly the non-walking Pals — so every row carries
`mountModeInferred` and a UI must not render the two alike.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

import elements
import gamedata
import palresist
import palstats
import viewcache

logger = logging.getLogger(__name__)

# What can be ranked, and where each number comes from. `source` travels in the
# payload because a species column and a computed figure do not deserve the same
# authority, which is the rule `palstats`' `calculated: true` already follows.
METRICS: dict[str, dict[str, Any]] = {
    "rideSprint": {"label": "Ride speed", "source": "table", "ridesOnly": True},
    "run": {"label": "Run speed", "source": "table", "ridesOnly": False},
    "swimDash": {"label": "Swim dash", "source": "table", "ridesOnly": False},
    "swim": {"label": "Swim speed", "source": "table", "ridesOnly": False},
    "transport": {"label": "Transport speed", "source": "table", "ridesOnly": False},
    "stamina": {"label": "Stamina", "source": "table", "ridesOnly": False},
    "hp": {"label": "HP", "source": "calculated", "ridesOnly": False},
    "attack": {"label": "Attack", "source": "calculated", "ridesOnly": False},
    "defense": {"label": "Defense", "source": "calculated", "ridesOnly": False},
    "workSpeed": {"label": "Work speed", "source": "calculated", "ridesOnly": False},
}

# Which passive effect type moves which movement figure.
_MOVEMENT_EFFECTS = {
    "MoveSpeed": ("rideSprint", "run"),
    "SwimSpeed": ("swim", "swimDash"),
}

# Terrain- and time-gated variants. Kept apart from the plain ones rather than
# dropped: "+50% at night" is true and is not a headline.
_CONDITIONAL_EFFECTS = {
    "MoveSpeed_Grass", "MoveSpeed_Ground", "MoveSpeed_Snow", "MoveSpeed_Water",
    "ClimbMoveSpeedRate",
}

# Applies whatever the Pal is doing.
_ALWAYS = {"InvokeAlways"}
# Applies only while the Pal is being ridden — which for a ride-speed ranking is
# exactly the case, and for anything else is not. See the module docstring.
_RIDING = {"InvokeRiding"}

_SELF_TARGETS = palstats.PASSIVE_SELF_TARGETS


def _species() -> list[dict[str, Any]]:
    """
    One row per rankable species, deduplicated and with encounter forms dropped.

    `BOSS_`, `PREDATOR_`, `GYM_` and `RAID_` rows are the same Pal placed by
    encounter logic, and they carry their own scaling — an alpha Melpaca is
    faster on paper and is not a Pal anybody rides. Leaving them in produces a
    leaderboard where the top ten are four Pals.
    """
    # Keyed on the bundle rather than on the parse generation: this describes
    # the game, so it changes when `gamedata.json.gz` does and at no other time.
    return viewcache.per_file(gamedata.DATA_PATH, _build_species)


def _build_species() -> list[dict[str, Any]]:
    out = []
    for species_id, entry in (gamedata.load().get("pals") or {}).items():
        if species_id.upper().startswith(("BOSS_", "PREDATOR_", "GYM_", "RAID_")):
            continue
        out.append({
            "speciesId": species_id,
            "name": entry.get("name") or gamedata.humanize(species_id),
            "icon": entry.get("icon"),
            "elements": list(entry.get("elements") or []),
            "movement": dict(entry.get("movement") or {}),
            "rideable": bool(entry.get("rideable")),
            "mountGearItem": entry.get("mountGearItem"),
            # Whether it flies, swims or walks. AGENTS.md recorded this as
            # unavailable across five checked avenues; the sixth was never
            # tried, because the search looked for `BP_Pal_*` and the game
            # names its species blueprints `BP_<Species>`.
            #
            # `GroundOnly` is INFERRED — nothing states the native default — so
            # `movementModeInferred` travels per row rather than only in the
            # bundle, since a row is what a caller renders.
            "movementMode": gamedata.movement_mode(species_id),
            "movementModeInferred": (
                species_id not in ((gamedata.movement_modes() or {}).get("species") or {})
            ),
        })
    return out


def movement_bonuses(passive_ids: list) -> dict[str, Any]:
    """
    The movement multipliers a passive set gives, grouped by when they apply.

    Returns `{"always": {metric: fraction}, "riding": {...}, "conditional":
    [...]}`. The conditional list is *described*, not totalled, because nothing
    here knows whether it is night or whether you are on snow.
    """
    always: dict[str, float] = {}
    riding: dict[str, float] = {}
    conditional: list[dict[str, Any]] = []

    for passive_id in passive_ids or []:
        entry = gamedata.passive_effects(str(passive_id))
        if not entry:
            continue
        invokes = set(entry.get("invoke") or [])
        for effect in entry.get("effects") or []:
            kind = str(effect.get("type") or "")
            if str(effect.get("target") or "") not in _SELF_TARGETS:
                continue
            value = float(effect.get("value") or 0.0) / 100.0
            if kind in _CONDITIONAL_EFFECTS:
                conditional.append({
                    "passiveId": passive_id, "type": kind,
                    "value": float(effect.get("value") or 0.0),
                })
                continue
            metrics = _MOVEMENT_EFFECTS.get(kind)
            if not metrics:
                continue
            # An empty invoke set is one skill in the bundle (`Test_MoveSpeed_
            # Night`) and is not treated as unconditional — an unset field is
            # not a claim that it always fires.
            if invokes & _ALWAYS:
                for metric in metrics:
                    always[metric] = always.get(metric, 0.0) + value
            elif invokes & _RIDING:
                for metric in metrics:
                    riding[metric] = riding.get(metric, 0.0) + value
            else:
                conditional.append({
                    "passiveId": passive_id, "type": kind,
                    "value": float(effect.get("value") or 0.0),
                    "when": sorted(invokes),
                })

    return {"always": always, "riding": riding, "conditional": conditional}


def partner_movement(species_id: str, condenser_rank: int = 1) -> dict[str, Any]:
    """
    Movement a species' own PARTNER SKILL grants, at a given condenser rank.

    **This is how the condenser makes a Pal faster, and nothing read it.** The
    module docstring above argues at length that the speed columns carry no
    build term — true, and it is not the whole story, because
    `DT_PartnerSkillParameter.PassiveSkills` is a list *indexed by condenser
    rank*. Direhowl's is empty at rank 1 and `MoveSpeed +10/12/15/20%` at ranks
    2-5, so a four-star Direhowl really is 20% faster and the reason was sitting
    one module over in the bundle #103 produced.

    Confirmed by the operator's in-game observation before it was found in the
    files, which is the third time the condenser has worked that way round.

    **`movement_bonuses` is reused rather than reimplemented**, because its
    target and invoke rules are exactly the ones needed here and a second copy
    would drift. Three things fall out of that reuse and all three are right:

    - `InvokeRiding` (156 effects) lands in `riding`, so it only ever counts
      towards a figure you get while riding.
    - `InvokeInOtomo` (160) lands in `conditional` — it applies while the Pal is
      out with you rather than while ridden, and describing it beats folding it
      into a ride-speed headline.
    - `ClimbMoveSpeedRate` is **`ToTrainer`** — it speeds up the *player's*
      climbing, not the Pal — so `_SELF_TARGETS` drops it. Counting it would
      have credited a Pal with a bonus that moves somebody else.

    A species with no partner skill, or one whose skill does not touch movement,
    returns empty rather than zero-filled: 96 of the species/forms have a
    movement-scaling partner skill and the rest genuinely gain nothing.
    """
    skills = [str(s) for s in
              (gamedata.partner_skills_at(str(species_id), int(condenser_rank)) or [])]
    out = movement_bonuses(skills)
    out["skillIds"] = skills
    out["condenserRank"] = int(condenser_rank)
    # Named so a client can say "10% of this is the partner skill at 4 stars"
    # rather than presenting one merged figure the player cannot attribute.
    out["source"] = "partnerSkill"
    return out


def _merged_movement(moves: dict[str, Any], partner: dict[str, Any],
                     metric: str) -> tuple[float, float]:
    """
    `(always, riding)` fractions for one metric, from passives AND partner skill.

    Separate arguments rather than one merged dict because the two are reported
    separately in the payload — the partner-skill half is what a condenser buys,
    and merging them at source would make that unattributable.
    """
    always = (moves["always"].get(metric, 0.0)
              + partner["always"].get(metric, 0.0))
    riding = (moves["riding"].get(metric, 0.0)
              + partner["riding"].get(metric, 0.0))
    return always, riding


# Where a partner skill's effect lands, and therefore which question it answers.
# `ToTrainer` is 669 of the bundle's 2,057 effects and `palstats` excludes it
# **correctly** — it is not part of a Pal's stat block. It is the entire content
# of "which Pal should I carry", which is why it is read here instead.
_PLAYER_TARGETS = {"ToTrainer", "ToTrainerAndOtomo"}
# In the party / out with you, versus specifically while ridden.
_PARTY_INVOKES = {"InvokeInOtomo", "InvokeActiveOtomo", "InvokeAlways"}


def partner_effects(species_id: str, condenser_rank: int = 1) -> dict[str, Any]:
    """
    What carrying this Pal does for the player, at a given condenser rank.

    **This is the axis `palstats` deliberately cannot see.** Its filters exclude
    `ToTrainer` and `InvokeInOtomo`, both right for a Pal's own stat block and
    both blind to the reason somebody carries a Pal that never fights: Silvegis
    cuts the player's shield damage by 65% at one star and **80% at five**.

    Split by *when*, never summed into one number:

    - `party` — `InvokeInOtomo` / `InvokeActiveOtomo` / `InvokeAlways`. Just for
      being out with you.
    - `riding` — `InvokeRiding`. `GiveAElectricity_Ride` on Solmora Lux is
      `ElementElectricity -> ToTrainer`, which is the game stating that riding
      it makes your damage electric.
    - `toPal` — effects the skill puts on the Pal rather than on you, kept apart
      because "+15% Dark boost" reads very differently depending on whose it is.

    Nothing is inferred from a skill id. Each one is looked up in
    `passive_effects.json.gz`; an id that does not resolve is dropped rather
    than guessed at from its name.
    """
    out: dict[str, list] = {"party": [], "riding": [], "toPal": [], "unknown": []}
    for skill_id in gamedata.partner_skills_at(species_id, condenser_rank):
        entry = gamedata.passive_effects(skill_id)
        if not entry:
            out["unknown"].append(skill_id)
            continue
        invokes = set(entry.get("invoke") or [])
        for effect in entry.get("effects") or []:
            row = {
                "skillId": skill_id,
                "type": effect.get("type"),
                "value": effect.get("value"),
                "target": effect.get("target"),
                "invoke": sorted(invokes),
            }
            if str(effect.get("target") or "") in _PLAYER_TARGETS:
                bucket = "riding" if invokes & _RIDING else (
                    "party" if invokes & _PARTY_INVOKES else "party"
                )
            else:
                bucket = "toPal"
            out[bucket].append(row)
    return out


_PLACEHOLDER = re.compile(r"\{Passive(\d+)_EffectValue(\d+)\}")


def partner_skill(species_id: str, condenser_rank: int = 1) -> dict[str, Any]:
    """
    The partner skill's name, and what it does **at this condenser rank**.

    The game writes the description with placeholders — *"reduces damage taken
    by your shield by `{Passive2_EffectValue1}`%"* — because the number moves
    with the rank. `Passive<n>` indexes that rank's skill list and
    `EffectValue<m>` indexes that skill's effects, both 1-based.

    **THE INDEX MAPPING IS AN INFERENCE, SO IT IS CHECKED RATHER THAN TRUSTED.**
    Silvegis's prose reads "reduces shield regeneration delay by
    {Passive1_EffectValue1}% and reduces damage taken by your shield by
    {Passive2_EffectValue1}%", and its rank-1 skills are, in order,
    `PlayerShield_RecoverStartTimeRate 30` then `ShieldDamageCutRate 65` — the
    prose and the order agree on which is which. `filled` reports whether every
    placeholder resolved.

    **A half-filled sentence is never returned.** If any placeholder has no
    value the raw text comes back with `filled: false`, on the resolver rule
    this project already holds: failing to state a number is recoverable,
    stating the wrong one is not.
    """
    entry = (gamedata.pal_exact(species_id) or gamedata.pal(species_id) or {})
    skill = dict(entry.get("partnerSkill") or {})
    if not skill:
        return {}

    ids = gamedata.partner_skills_at(species_id, condenser_rank)
    values: list[list[float]] = []
    for skill_id in ids:
        effect = gamedata.passive_effects(skill_id) or {}
        values.append([float(e.get("value") or 0.0)
                       for e in (effect.get("effects") or [])])

    missing = False

    def fill(match: "re.Match") -> str:
        nonlocal missing
        skill_index = int(match.group(1)) - 1
        value_index = int(match.group(2)) - 1
        try:
            return f"{values[skill_index][value_index]:g}"
        except IndexError:
            missing = True
            return match.group(0)

    text = str(skill.get("description") or "")
    filled_text = _PLACEHOLDER.sub(fill, text) if text else ""
    # ANY brace left over, not just the ones this pattern knows. The first
    # version checked only its own substitutions and reported Jetragon's
    # `{ReferenceMsgId_DamageUp}` as filled — a flag that says "complete" about
    # a sentence with a placeholder in it is worse than no flag.
    if "{" in filled_text:
        missing = True

    out: dict[str, Any] = {"name": skill.get("name")}
    if text:
        out["description"] = text if missing else filled_text
        out["filled"] = not missing
        # An unfilled description still says what the skill does; it just leaves
        # the magnitude as the game wrote it. The flag is so a UI can choose.
        out["atRank"] = max(1, min(int(condenser_rank or 1), 5))
    return out


def _hypothetical(species_id: str, build: dict[str, Any]) -> dict[str, Any]:
    """
    A Pal-shaped record for a build nobody owns.

    Deliberately the **same shape** `/api/pals` serves, so `palstats.describe`
    is reused rather than reimplemented — a second stat implementation is how
    the ranking and the Pal page come to disagree.
    """
    iv = int(build.get("iv") or 0)
    soul = int(build.get("soulRank") or 0)
    return {
        "characterId": species_id,
        "level": int(build.get("level") or 1),
        "rank": int(build.get("condenserRank") or 1),
        "ivs": {"hp": iv, "shot": iv, "defense": iv},
        "soulRanks": {"hp": soul, "attack": soul, "defense": soul,
                      "craftSpeed": soul},
        "friendshipPoint": int(build.get("trustPoints") or 0),
        "passiveSkills": list(build.get("passives") or []),
    }


def rank(metric: str, build: Optional[dict[str, Any]] = None,
         limit: int = 50, rideable_only: bool = False,
         against: str = "") -> dict[str, Any]:
    """
    Every species ordered by one metric at one build.

    `build` is `{level, condenserRank, iv, soulRank, trustPoints, passives}` and
    every field is optional — the defaults are a level-1 Pal with nothing on it,
    which for a movement metric is the same answer as a maxed one.

    ## `against` — and why this may sort on a matchup where `optimise.py` may not

    `optimise.py` holds a rule with a test on both sides of the wire: a matchup
    is a badge, never a sort key. That rule was written when the element chart
    had **no coefficient**, so folding "strong against Grass" into a score meant
    inventing one.

    `BP_PalGameSetting.DamageElementMatchRate = 1.2` is now read from the game,
    and this is a different question. Ranking a roster for general use must not
    move on a matchup; ranking *specifically for a fight against Grass* is a
    question whose answer legitimately depends on it, and 1.2 is Pocketpair's
    number rather than one chosen here.

    So it is applied only when `against` is given, only to `attack`, and never
    silently: `attack`, `matchup` and `effectiveAttack` all travel, so the
    un-multiplied figure is always visible beside the multiplied one.

    ## There is no "resist half" to find, and looking for one was the mistake

    This docstring used to say the defensive question was unanswerable because
    the settings object has no halving constant. **That was the wrong shape of
    answer.** The chart is a one-directional "strong against" relation, so a
    disadvantaged defender does not take a *penalty* — it takes the attacker's
    ×1.2. One constant covers both directions:

        your damage to them   x1.2 when YOUR element beats theirs, else x1.0
        their damage to you   x1.2 when THEIR element beats yours, else x1.0

    Being "weak" therefore costs nothing offensively and means they hit you 20%
    harder, which is exactly what a single `DamageElementMatchRate` with no
    counterpart implies. So `incoming` and `effectiveHp`/`effectiveDefense`
    travel too, and a defensive ranking against a named element is a real
    answer rather than a refusal.

    What is still **not** established is whether anything stacks on top of the
    1.2 — `DamageUpElement_ByElementStatus` and `DamageDownElement_ByElementStatus`
    are C++ and unread — so the figure is one multiplier and says so.
    """
    spec = METRICS.get(metric)
    if spec is None:
        return {"metric": metric, "known": False,
                "note": f"No such metric. Try one of: {', '.join(METRICS)}."}

    build = dict(build or {})
    passives = [str(p) for p in (build.get("passives") or []) if p]
    moves = movement_bonuses(passives)
    condenser = max(1, min(int(build.get("condenserRank") or 1), 5))
    only_rides = rideable_only or spec["ridesOnly"]

    # A target element only means something for a damage or survival ranking.
    # Asking who runs fastest "against Grass" is not a question, and answering
    # it would reorder a movement table for no reason.
    target = elements.canonical(against) if against else None
    # Offensive: your attack gains when you beat them.
    # Defensive: your effective bulk drops when they beat you — the SAME
    # constant read from the other side, not a second mechanic.
    applies = bool(target) and metric in ("attack", "hp", "defense")
    rate = elements.match_rate()

    rows: list[dict[str, Any]] = []
    for entry in _species():
        if only_rides and not entry["rideable"]:
            continue

        if spec["source"] == "table":
            base = entry["movement"].get(metric)
            # Absent means the game says "not applicable" (a -1 in the table),
            # which is not a slow Pal and must not be ranked as one.
            if base is None:
                continue
            # The species' own partner skill, at THIS build's condenser rank —
            # which is where a condenser's effect on movement actually lives.
            partner = partner_movement(entry["speciesId"], condenser)
            always, riding_all = _merged_movement(moves, partner, metric)
            # A riding bonus only counts towards a figure you get while riding.
            riding = riding_all if metric == "rideSprint" else 0.0
            value = base * (1.0 + always + riding)
            row = {
                "base": base,
                "value": round(value, 1),
                "passiveBonus": round(always + riding, 4),
                # Broken out, because this is the part that changes when you
                # condense — and a merged figure could not tell a player that
                # four stars is what bought it.
                "partnerBonus": round(
                    partner["always"].get(metric, 0.0)
                    + (partner["riding"].get(metric, 0.0)
                       if metric == "rideSprint" else 0.0), 4),
            }
        else:
            stats = palstats.describe(_hypothetical(entry["speciesId"], build))
            # 99 characters in the reference world have no scaling data at all —
            # NPCs sharing the species map with Pals. None, never zero.
            if not stats:
                continue
            block = stats.get(metric) or {}
            value = block.get("final")
            if value is None:
                continue
            row = {"value": value, "breakdown": block}
            if target:
                # Two readings of one relation. `matchup` is you hitting them;
                # `incoming` is them hitting you, and it is NOT the inverse —
                # Fire beats Grass and Grass beats Earth, so a Fire Pal facing
                # Grass is strong AND safe, while a Water Pal facing Grass is
                # neutral both ways.
                verdict = elements.matchup(entry["elements"], [target])
                incoming = elements.matchup([target], entry["elements"])
                row["matchup"] = verdict
                row["incoming"] = incoming
                if applies:
                    # The un-multiplied figure always stays on the row, so
                    # nothing is hidden behind the sort.
                    row["raw"] = value
                    if metric == "attack":
                        row["matchRate"] = rate if verdict == "strong" else 1.0
                        row["value"] = (
                            int(value * rate) if verdict == "strong" else value
                        )
                    else:
                        # Effective bulk: how much of this stat survives an
                        # attacker that beats your element. Dividing by the same
                        # 1.2 is the constant read from the defender's side, not
                        # a resist coefficient — there is no such thing to find.
                        row["matchRate"] = (
                            round(1.0 / rate, 4) if incoming == "strong" else 1.0
                        )
                        row["value"] = (
                            int(value / rate) if incoming == "strong" else value
                        )

        rows.append({
            "speciesId": entry["speciesId"],
            "name": entry["name"],
            "icon": entry["icon"],
            "elements": entry["elements"],
            "rideable": entry["rideable"],
            # **This used to be a hardcoded None with a comment saying the mode
            # is "not in any file".** It is: `EPalMonsterMovementType`, on each
            # species blueprint in the SERVER pak. The search that ruled it out
            # looked for `BP_Pal_*` and the game names them `BP_<Species>`.
            "mountMode": entry["movementMode"],
            # `GroundOnly` is inferred from the overrides being exactly the
            # non-walkers, not read. Carried per row because a row is what gets
            # rendered, and the two must not look equally authoritative.
            "mountModeInferred": entry["movementModeInferred"],
            "stamina": entry["movement"].get("stamina"),
            **row,
        })

    rows.sort(key=lambda r: (-float(r["value"] or 0), r["name"]))

    return {
        "metric": metric,
        "known": True,
        "label": spec["label"],
        "source": spec["source"],
        "ranked": len(rows),
        "rows": rows[: max(1, min(int(limit or 50), 400))],
        "build": {
            "level": int(build.get("level") or 1),
            "condenserRank": int(build.get("condenserRank") or 1),
            "condenserStars": max(0, int(build.get("condenserRank") or 1) - 1),
            "iv": int(build.get("iv") or 0),
            "soulRank": int(build.get("soulRank") or 0),
            "trustPoints": int(build.get("trustPoints") or 0),
            "passives": passives,
        },
        "passiveEffect": moves,
        # Movement from the species' own partner skill AT THIS CONDENSER RANK.
        # Per row rather than here, because it differs by species — this flag
        # only says the ranking accounts for it at all.
        "partnerSkillMovementApplied": True,
        # THE PART PEOPLE GET WRONG, carried in the payload rather than only in
        # a docstring: the client is the thing about to render a build form.
        # "Does a build change this number *in this ranking*" — a statement
        # about what is computed here, never about the game.
        "buildAffectsMetric": spec["source"] == "calculated",
        # The columns carry no build term. That is a fact about the FILES.
        "movementInFiles": True,
        # **THE CONDENSER DOES CHANGE MOVEMENT, and this used to read
        # "unverified".** Confirmed 2026-08-11 from the operator's observation
        # and then found in the game's own data: a partner skill is a list
        # indexed by condenser rank, so Direhowl reads MoveSpeed +0/10/12/15/20%
        # across the stars. Applied above, per species, and broken out as
        # `partnerBonus` on each row.
        "condenserOnMovement": "viaPartnerSkill",
        # Which is NOT the same question as whether `GenkaiToppa_PerAdd` also
        # multiplies the speed columns the way it multiplies HP and Attack. That
        # half is still unsettled and still needs a timing run, so it keeps its
        # own flag rather than being buried by the half that got answered.
        # Never "false" — see the module docstring.
        "condenserOnSpeedColumns": "unverified",
        # **Both are answerable now.** This said "fastest flyer is not" and
        # shipped False, on the strength of a search for `BP_Pal_*` — the game
        # names its species blueprints `BP_<Species>` and puts them in the
        # server pak, where `EPalMonsterMovementType` decodes.
        "mountModeKnown": True,
        "speedUnitKnown": False,
        # The element half, stated rather than implied. `against` echoes back
        # canonicalised so a caller who typed "Ground" can see it became "Earth".
        "against": target or "",
        "matchupApplied": applies,
        "matchRate": rate if applies else None,
        # ONE constant, read from both sides: it is your bonus when you beat
        # them and their bonus when they beat you. There is no separate resist
        # coefficient, which is why there was never one to find.
        "matchRateAppliesBothWays": True,
        # What is genuinely unknown: whether anything stacks on top of the 1.2.
        # `Damage{Up,Down}Element_ByElementStatus` are C++ and unread.
        "stackingKnown": False,
        "chartIsHandEntered": True,
        "unknownElements": list(elements.unknown_to_chart()),
    }


def compare(species_ids: list, build: Optional[dict[str, Any]] = None
            ) -> dict[str, Any]:
    """
    Every metric for a handful of named species — the "test a build" view.

    Same numbers as `rank`, pivoted: a ranking answers "who is best at X" and
    this answers "how does this Pal come out across the board", which is the
    question somebody has once they have picked two candidates.
    """
    build = dict(build or {})
    wanted = [str(s) for s in (species_ids or []) if s][:12]
    by_id = {e["speciesId"].lower(): e for e in _species()}

    out = []
    unknown = []
    for species_id in wanted:
        entry = by_id.get(species_id.lower())
        if entry is None:
            unknown.append(species_id)
            continue
        stats = palstats.describe(_hypothetical(entry["speciesId"], build))
        moves = movement_bonuses([str(p) for p in (build.get("passives") or []) if p])
        # Per species, because the partner skill IS the species — this is the
        # term that makes a four-star Direhowl faster than a one-star one, and
        # `rank()` applies the identical one.
        partner = partner_movement(
            entry["speciesId"], int(build.get("condenserRank") or 1)
        )
        movement = {}
        for key, base in (entry["movement"] or {}).items():
            if key == "stamina":
                movement[key] = {"base": base, "value": base}
                continue
            always, riding_all = _merged_movement(moves, partner, key)
            riding = riding_all if key == "rideSprint" else 0.0
            movement[key] = {
                "base": base,
                "value": round(base * (1.0 + always + riding), 1),
                "partnerBonus": round(
                    partner["always"].get(key, 0.0)
                    + (partner["riding"].get(key, 0.0)
                       if key == "rideSprint" else 0.0), 4),
            }
        out.append({
            "speciesId": entry["speciesId"],
            "name": entry["name"],
            "icon": entry["icon"],
            "elements": entry["elements"],
            "rideable": entry["rideable"],
            "mountMode": None,
            "movement": movement,
            # None for an NPC rather than a block of zeroes, which would read as
            # a confident answer about a merchant.
            "stats": stats,
            # What carrying it does for YOU, at this build's condenser rank —
            # the axis a stat comparison cannot show.
            "partner": partner_effects(
                entry["speciesId"], int(build.get("condenserRank") or 1)
            ),
            # The defensive half, which `stats` does not cover — the game prints
            # four figures and a 35% Fire reduction is none of them.
            #
            # Computed per species even though the passives are one chosen
            # build, because `softTo` is the type chart read from each species'
            # own side: the same build on a Grass Pal and a Water Pal is soft to
            # different things, which is exactly what a comparison is for.
            "resist": palresist.profile(
                entry["elements"],
                [str(p) for p in (build.get("passives") or []) if p],
            ),
            "mountGearItem": entry.get("mountGearItem"),
        })

    return {
        "species": out,
        # Named, not dropped: a typo must not look like a Pal with no data.
        "unknown": unknown,
        "build": {
            "level": int(build.get("level") or 1),
            "condenserRank": int(build.get("condenserRank") or 1),
            "iv": int(build.get("iv") or 0),
            "soulRank": int(build.get("soulRank") or 0),
            "passives": [str(p) for p in (build.get("passives") or []) if p],
        },
        "movementInFiles": True,
        # Same two flags `rank()` carries, and they must stay in step — a
        # comparison that disagreed with the ranking about whether the condenser
        # moves a speed would be the worse kind of wrong, since both are on
        # screen together.
        "condenserOnMovement": "viaPartnerSkill",
        "condenserOnSpeedColumns": "unverified",
        "partnerSkillMovementApplied": True,
        # See `rank`: the mode is read from the species blueprints now.
        "mountModeKnown": True,
    }
