"""
Which Pal is fastest, toughest or hardest-hitting at a build you choose.

The question this answers is the one an operator actually asks — *"at level 80,
four stars, with these passives, which mount is quickest?"* — and it answers it
over the whole species table rather than over the Pals somebody happens to own.
`optimise.py` ranks a **world**; this ranks the **game**.

## Movement takes no level, IV, condenser or soul term, and that is the finding

The obvious model is that a maxed Pal is faster. It is not. `RideSprintSpeed`,
`RunSpeed`, `SwimSpeed` and `Stamina` are flat per-species columns, and nothing
in `BP_PalGameSetting`, `DT_PalMonsterParameter` or the stat formula gives any
of them a level, IV, condenser or soul-rank term — `StatusCalculate_GenkaiToppa_
PerAdd` moves HP, Attack, Defense and CraftSpeed and stops there.

**Only passives change movement.** `MoveSpeed`, `SwimSpeed` and friends are real
effect types on real passives — Legend is +20% — so a build genuinely matters,
just not the part of it people expect. Saying so is the feature; quietly
multiplying a speed by the condenser bonus would be inventing a mechanic.

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

- **Whether a mount flies, swims or walks.** In no file. AGENTS.md establishes
  that at length, and two further avenues were checked while building this and
  changed nothing: the client pak has no `BP_Pal_<species>` blueprint, and the
  213 per-species animation folders do not attribute it — Jetragon has no
  fly-named animation and everything has an `Idle_Swim`. So **"fastest ride" is
  answerable and "fastest flyer" is not**, and `mountMode` is null rather than
  guessed from a name.
- **How a speed converts to metres per second.** The column is the game's own
  unit and nothing states the scale.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import elements
import gamedata
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
            always = moves["always"].get(metric, 0.0)
            # A riding bonus only counts towards a figure you get while riding.
            riding = moves["riding"].get(metric, 0.0) if metric == "rideSprint" else 0.0
            value = base * (1.0 + always + riding)
            row = {
                "base": base,
                "value": round(value, 1),
                "passiveBonus": round(always + riding, 4),
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
            # Not in any file. Null rather than inferred from a name — see the
            # module docstring.
            "mountMode": None,
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
        # THE PART PEOPLE GET WRONG, carried in the payload rather than only in
        # a docstring: the client is the thing about to render a build form.
        "buildAffectsMetric": spec["source"] == "calculated",
        "movementIgnoresLevel": True,
        # "Fastest ride" is answerable; "fastest flyer" is not.
        "mountModeKnown": False,
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
        movement = {}
        for key, base in (entry["movement"] or {}).items():
            if key == "stamina":
                movement[key] = {"base": base, "value": base}
                continue
            always = moves["always"].get(key, 0.0)
            riding = moves["riding"].get(key, 0.0) if key == "rideSprint" else 0.0
            movement[key] = {
                "base": base,
                "value": round(base * (1.0 + always + riding), 1),
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
        "movementIgnoresLevel": True,
        "mountModeKnown": False,
    }
