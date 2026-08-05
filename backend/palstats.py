"""
Effective Pal stats: what the game will show for a Pal, from what the save holds.

The save stores *inputs* — level, IVs, condenser rank, soul ranks, trust points —
and never the resulting HP, Attack, Defense or Work Speed. Those are computed by
the game at load. So a dashboard that wants to answer "is this Pal actually any
good" has to run the same arithmetic.

WHERE THE FORMULA COMES FROM
----------------------------
`refs/PalWorldSaveTools-main.zip`, two files that must be read together:

  * `.opencode/skills/pst-stat-formula/SKILL.md` — the derivation, and a record
    of which terms were corrected against in-game stat breakdowns on maxed test
    Pals (June 2026 session).
  * `src/palworld_aio/utils.py` — the implementation, which is where the exact
    constants live.

It is **not** invented here and not scraped from a wiki. This file is a
transcription with the project's own naming, and the reference implementation is
the thing to diff against if a game update moves a number.

Its own documented tolerance is ±1–2 on the trust and awakening terms at some
level/condenser boundaries — float rounding, not a modelling error. So these
figures are labelled *calculated* in the UI rather than presented as read from
the save, because one of those two things is exact and the other is very close.

THE SHAPE
---------
    base      = additive_const + floor(scaling x K x level x (1+IV) x (1+condenser))
    subtotal  = base + trust + awakening                      # additive
    final     = floor(subtotal x (1+soul) x (1+passive))      # multiplicative

Three things worth knowing before touching it:

* **The alpha multiplier is already in the data.** `BOSS_Alpaca` carries hp 108
  where `Alpaca` carries 90. Applying a separate boss multiplier on top would
  count it twice — the reference implementation removed exactly that bug, and
  the comment survives in SKILL.md as "removed lucky_alpha".

* **Condenser rank 1 means no stars.** The bonus is `(rank - 1) x 0.05`, so a
  fresh Pal at rank 1 gets +0%, and the four-star maximum (rank 5) gets +20%.
  This is the answer to "does raising stars raise stats" — it does, 5% per star,
  and it multiplies the base term rather than the final one.

* **Work Speed does not work like the other three.** It is a flat 70 until the
  condenser is used at all, and only then does level and craft speed enter. A
  formula that treats it like HP produces a number that rises with level on a
  Pal whose work speed the game shows as unchanged.
"""

from __future__ import annotations

import math
from typing import Any, Optional

import gamedata

# Trust thresholds, index = friendship rank. Ten ranks above zero.
FRIENDSHIP_THRESHOLDS = (
    0, 6000, 13000, 21000, 30000, 40000, 55000, 80000, 110000, 150000, 200000,
)

# Per-point contributions. Both are "per unit", not percentages of anything.
IV_PER_POINT = 0.3 / 100        # a Talent_* of 100 is +30%
SOUL_PER_RANK = 0.03            # each Pal Soul rank is +3%
CONDENSER_PER_STAR = 0.05       # each condenser star above the first is +5%

# The condenser tops out at four stars, which the save stores as Rank 5.
MAX_CONDENSER_RANK = 5


def friendship_rank(trust_points: int) -> int:
    """
    Trust points -> heart rank (0-10).

    Descending scan rather than `bisect`, matching the reference implementation
    exactly — the boundary behaviour is what the trust term is sensitive to.
    """
    try:
        points = int(trust_points or 0)
    except (TypeError, ValueError):
        return 0
    for rank in range(len(FRIENDSHIP_THRESHOLDS) - 1, 0, -1):
        if points >= FRIENDSHIP_THRESHOLDS[rank]:
            return rank
    return 0


def _species_stats(species_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    (base stats, trust coefficients) for a species, or ({}, {}) if unknown.

    Looked up through `gamedata.pal`, so the case-insensitivity that the rest of
    the project depends on applies here too — the save spells `Sheepball` and the
    reference spells `SheepBall`, and an exact match silently loses eight real
    Pals.

    **The boss form is looked up as itself, not as its base species.** `BOSS_`
    entries carry their own, higher, scaling numbers and that is where the alpha
    bonus lives — hence `pal_exact`, not `pal`, which strips the prefix and would
    quietly hand back the ordinary species' numbers.
    """
    entry = gamedata.pal_exact(species_id) or {}
    return (entry.get("stats") or {}), (entry.get("friendship") or {})


# Which `EPalPassiveSkillEffectType` feeds which calculated stat.
#
# `attack` is **ShotAttack**, matching the rest of this module: the game shows
# shot attack, `meleeAttack` is a different number on most species, and reading
# the wrong one is plausible everywhere and wrong everywhere. `MeleeAttack`
# passives are therefore deliberately absent — they modify a stat nothing here
# displays, and folding them into attack would inflate it.
PASSIVE_EFFECT_STATS = {
    "MaxHP": "hp",
    "ShotAttack": "attack",
    "Defense": "defense",
    "CraftSpeed": "workSpeed",
}

# Invoke conditions under which a passive applies to a Pal's own displayed stats.
#
# `InvokeWorker` and `InvokeInBaseCamp` are NOT here, and that is the point: a
# skill that only fires while the Pal is working at a base does not apply to the
# Pal sitting in a palbox, so counting it would show an attack figure the game
# never uses.
PASSIVE_SELF_INVOKES = {"InvokeAlways", "InvokeActiveOtomo", "InvokeInOtomo"}

# Effect targets that mean "this Pal". Measured across the bundle's 2,057
# effects: ToSelf 736, ToSelfAndTrainer 341, and everything else names someone
# who is not the Pal carrying the skill — ToTrainer 669, ToOtomo 226,
# ToBaseCampPal 40, ToBuildObject 29, ToActiveOtomo 10, ToTrainerAndOtomo 5.
# Counting a trainer buff as a Pal buff would inflate a third of all Pals.
#
# `None` is in the list because it occurs EXACTLY ONCE in the whole bundle —
# `Rare`'s defence effect — and the game's own description of that skill reads
# "Attack +15% Defense +15% Work Speed +20%", so it is a self buff with an unset
# target field rather than a category of its own. Excluding it silently dropped
# 15% defence from every Lucky Pal, and the only reason it was noticed is that
# stacking Legend with Rare produced a defence bonus that had not moved.
PASSIVE_SELF_TARGETS = {"ToSelf", "ToSelfAndTrainer", "None"}


def passive_bonuses(passive_ids: list) -> dict[str, float]:
    """
    `{stat: fraction}` for a Pal's passive skills — e.g. `{"attack": 0.4}`.

    A PASSIVE'S BONUS IS PER STAT, NOT ONE NUMBER, which is why this returns a
    map and why `passive_bonus` as a single float was the wrong shape. `Legend`
    is +20% shot attack AND +20% defence together; `Noukin` is +30% attack and
    **-50%** craft speed. 175 of the 1,897 bundled passives touch more than one
    stat and 77 carry a negative, so one multiplier is wrong for hundreds of
    real Pals in at least one direction.

    Effects stack additively within a stat, matching how the formula applies the
    term: `final = floor(subtotal x (1+soul) x (1+passive))`.

    Unknown ids contribute nothing rather than raising. The bundle is 1,897
    entries against a save that can hold anything, and a Pal with a modded
    passive should lose the term, not its whole stat block.
    """
    out: dict[str, float] = {}
    for passive_id in passive_ids or []:
        entry = gamedata.passive_effects(str(passive_id))
        if not entry:
            continue
        # A skill that only fires at a base is not a buff to this Pal's stats.
        if not (set(entry.get("invoke") or []) & PASSIVE_SELF_INVOKES):
            continue
        for effect in entry.get("effects") or []:
            stat = PASSIVE_EFFECT_STATS.get(str(effect.get("type") or ""))
            if not stat or str(effect.get("target") or "") not in PASSIVE_SELF_TARGETS:
                continue
            out[stat] = out.get(stat, 0.0) + float(effect.get("value") or 0.0) / 100.0
    return out


def hp_breakdown(
    species_id: str,
    level: int,
    *,
    iv: int = 0,
    condenser_rank: int = 1,
    soul_rank: int = 0,
    trust_points: int = 0,
    is_awake: bool = False,
    passive_bonus: float = 0.0,
) -> dict[str, Any]:
    """
    HP, term by term.

    Returned as a breakdown rather than a number because the number alone is
    unfalsifiable: a player who thinks it is wrong has no way to say *which part*
    is wrong, and neither has anyone reading a bug report about it.
    """
    stats, trust_factors = _species_stats(species_id)
    scaling = float(stats.get("hp") or 0)
    iv_bonus = iv * IV_PER_POINT
    soul_bonus = soul_rank * SOUL_PER_RANK
    condenser_bonus = max(0, condenser_rank - 1) * CONDENSER_PER_STAR

    base = math.floor(500 + 5 * level + scaling * 0.5 * level * (1 + iv_bonus))
    # HP is the one stat where the condenser multiplies the whole base rather
    # than sitting inside it. The other three fold it into the scaling term.
    base_with_condenser = math.floor(base * (1 + condenser_bonus))

    factor = float(trust_factors.get("hp") or 0)
    trust = int(
        level * friendship_rank(trust_points) * factor * 0.65 * (1 + condenser_bonus) + 0.5
    )
    awake = (
        math.floor(scaling * level * 0.065 * (1 + condenser_bonus))
        if is_awake and scaling else 0
    )

    subtotal = base_with_condenser + trust + awake
    return {
        "base": base,
        "condenserMultiplier": 1 + condenser_bonus,
        "baseWithCondenser": base_with_condenser,
        "trust": trust,
        "awakening": awake,
        "subtotal": subtotal,
        "soulMultiplier": 1 + soul_bonus,
        "passiveMultiplier": 1 + passive_bonus,
        "final": math.floor(subtotal * (1 + soul_bonus) * (1 + passive_bonus)),
    }


def _attack_like(
    scaling: float,
    trust_factor: float,
    additive_const: int,
    level: int,
    iv: int,
    condenser_rank: int,
    soul_rank: int,
    trust_points: int,
    is_awake: bool,
    passive_bonus: float,
    *,
    split_trust_floor: bool,
) -> dict[str, Any]:
    """
    Attack and Defense, which share a shape and differ in three constants.

    `split_trust_floor` is not a style choice. Attack computes its trust term as
    `floor(x) + floor(x * condenser)` while Defense computes `floor(x * (1 +
    condenser))`, and the two disagree by one at some boundaries. The reference
    implementation makes exactly this distinction, having found it by comparing
    against the game's own breakdown — so collapsing them into one expression
    would be reintroducing a fixed bug.
    """
    iv_bonus = iv * IV_PER_POINT
    soul_bonus = soul_rank * SOUL_PER_RANK
    condenser_bonus = max(0, condenser_rank - 1) * CONDENSER_PER_STAR

    base = math.floor(
        additive_const + scaling * 0.075 * level * (1 + iv_bonus) * (1 + condenser_bonus)
    )

    raw_trust = level * friendship_rank(trust_points) * trust_factor / 10.2
    trust = (
        math.floor(raw_trust) + math.floor(raw_trust * condenser_bonus)
        if split_trust_floor
        else math.floor(raw_trust * (1 + condenser_bonus))
    )
    awake = math.floor(scaling * level * (1 + iv_bonus) * 0.009) if is_awake else 0

    subtotal = base + trust + awake
    return {
        "base": base,
        "condenserMultiplier": 1 + condenser_bonus,
        "baseWithCondenser": base,
        "trust": trust,
        "awakening": awake,
        "subtotal": subtotal,
        "soulMultiplier": 1 + soul_bonus,
        "passiveMultiplier": 1 + passive_bonus,
        "final": math.floor(subtotal * (1 + soul_bonus) * (1 + passive_bonus)),
    }


def attack_breakdown(
    species_id: str,
    level: int,
    *,
    iv: int = 0,
    condenser_rank: int = 1,
    soul_rank: int = 0,
    trust_points: int = 0,
    is_awake: bool = False,
    passive_bonus: float = 0.0,
) -> dict[str, Any]:
    """
    Attack, from **shot** attack scaling.

    Palworld's displayed Attack is the shot figure; `meleeAttack` is a separate
    number the UI never shows and the formula never reads. Both are bundled, and
    using the wrong one produces a plausible number that is quietly wrong for
    every species where they differ (Melpaca: 90 melee, 75 shot).
    """
    stats, trust_factors = _species_stats(species_id)
    return _attack_like(
        float(stats.get("shotAttack") or 0),
        float(trust_factors.get("shotAttack") or 0),
        math.floor(1.5 * level),
        level, iv, condenser_rank, soul_rank, trust_points, is_awake, passive_bonus,
        split_trust_floor=True,
    )


def defense_breakdown(
    species_id: str,
    level: int,
    *,
    iv: int = 0,
    condenser_rank: int = 1,
    soul_rank: int = 0,
    trust_points: int = 0,
    is_awake: bool = False,
    passive_bonus: float = 0.0,
) -> dict[str, Any]:
    stats, trust_factors = _species_stats(species_id)
    return _attack_like(
        float(stats.get("defense") or 0),
        float(trust_factors.get("defense") or 0),
        math.floor(0.75 * level),
        level, iv, condenser_rank, soul_rank, trust_points, is_awake, passive_bonus,
        split_trust_floor=False,
    )


def work_speed_breakdown(
    species_id: str,
    level: int,
    *,
    condenser_rank: int = 1,
    soul_rank: int = 0,
    passive_bonus: float = 0.0,
) -> dict[str, Any]:
    """
    Work Speed, which is **flat 70 until the condenser is used at all**.

    Neither level nor the species' craft speed enters until rank 2. A formula
    that treats this like HP shows work speed climbing with level on a Pal whose
    in-game work speed has not moved, which is a wrong answer that looks right —
    it rises when you would expect it to.

    There is no trust or awakening term. Not omitted: they do not exist here.
    """
    stats, _ = _species_stats(species_id)
    craft_speed = float(stats.get("craftSpeed") or 100)
    soul_bonus = soul_rank * SOUL_PER_RANK
    condenser_bonus = max(0, condenser_rank - 1) * CONDENSER_PER_STAR

    base = 70
    if condenser_rank > 1:
        base = 70 + math.floor(craft_speed * condenser_bonus * level / 57)

    return {
        "base": base,
        "condenserMultiplier": 1 + condenser_bonus,
        "baseWithCondenser": base,
        "trust": 0,
        "awakening": 0,
        "subtotal": base,
        "soulMultiplier": 1 + soul_bonus,
        "passiveMultiplier": 1 + passive_bonus,
        # `+ 0.5` — this one rounds where the others floor.
        "final": int(base * (1 + soul_bonus) * (1 + passive_bonus) + 0.5),
    }


# ─── Progression ─────────────────────────────────────────


def level_progress(level: int, exp: int) -> dict[str, Any]:
    """
    How far through the current level a Pal is, from the game's own EXP table.

    Exact, unlike the stat figures — `palExpTable` is bundled from the game data
    rather than derived, so "3,412 EXP to level 25" is a fact rather than an
    estimate. `PalNextEXP` is the Pal table; `NextEXP` beside it is the *player*
    table and the two differ (25 vs 50 at level 2).

    **Low EXP for a level is normal and is not flagged.** A freshly caught Pal
    arrives at its wild level with almost no EXP and the game leaves it there —
    measured at 8 of the reference world's 1,905 Pals. High EXP never occurs
    naturally, which is why only that direction is treated as suspect, and that
    check lives in `editschema` rather than here.
    """
    table = gamedata.pal_exp_table()
    if not table:
        return {"known": False}

    current = table.get(str(level)) or {}
    nxt = table.get(str(level + 1)) or {}

    total_at_level = int(current.get("PalTotalEXP") or 0)
    total_at_next = int(nxt.get("PalTotalEXP") or 0)
    needed = int(nxt.get("PalNextEXP") or 0)

    if not nxt:
        # Max level: there is no next band, which is not missing data.
        return {
            "known": True,
            "maxed": True,
            "intoLevel": max(0, int(exp) - total_at_level),
            "needed": 0,
            "remaining": 0,
            "percent": 100.0,
        }

    into = max(0, int(exp) - total_at_level)
    span = max(1, total_at_next - total_at_level)
    return {
        "known": True,
        "maxed": False,
        "intoLevel": into,
        "needed": needed,
        "remaining": max(0, total_at_next - int(exp)),
        "percent": round(min(100.0, into * 100.0 / span), 1),
    }


# ─── The one entry point everything else should use ──────


def describe(
    pal: dict[str, Any], *, passive_bonus: Optional[float] = None
) -> Optional[dict[str, Any]]:
    """
    Every calculated figure for one parsed Pal.

    Takes the record `/api/pals` already serves rather than re-reading the save,
    so there is one source for the inputs and no second parse to fall out of step.

    Returns None for a species the bundled tables do not know — 13 of the
    reference world's 1,905 characters are ordinary NPCs with no entry, and they
    carry IVs exactly like a Pal. Guessing scaling numbers for them would produce
    confident stats for a merchant.

    PASSIVE SKILLS ARE NOW COMPUTED, NOT ASSUMED AWAY. This used to take a
    caller-supplied `passive_bonus` float defaulting to **zero**, on the grounds
    that the bundled passives table gave only an English sentence ("Attack +5%")
    that this module could not interpret. That was true of the PST archive and
    is no longer true of the game: `DT_PassiveSkill_Main` decodes out of the
    server pak with structured effect types and values, bundled by
    `scripts/extract-passive-effects.py`. Every stat this dashboard has shown
    until now ignored passives entirely.

    `passive_bonus` remains as an **override**, because a caller asking "what
    would this Pal be without its passives" is a real question and a fixed value
    is how it gets asked. `None` means compute from the Pal.
    """
    species = str(pal.get("characterId") or pal.get("speciesId") or "")
    stats, _ = _species_stats(species)
    if not stats:
        return None

    level = int(pal.get("level") or 1)
    ivs = pal.get("ivs") or {}
    souls = pal.get("soulRanks") or {}
    condenser = int(pal.get("rank") or 1)
    trust = int(pal.get("friendshipPoint") or 0)
    # No field in any save examined marks an awakened Pal, so this stays False
    # and the awakening term contributes nothing. Wired through rather than
    # dropped, because the term is part of the formula and a future save that
    # does carry the flag should need one line, not a rewrite.
    awake = bool(pal.get("isAwake"))

    # Per stat, because a passive's bonus is per stat. An explicit override
    # applies to every stat equally, which is what "compute this Pal as if its
    # passives gave X" means.
    bonuses = (
        {stat: passive_bonus for stat in PASSIVE_EFFECT_STATS.values()}
        if passive_bonus is not None
        else passive_bonuses(pal.get("passiveSkills") or [])
    )

    common = {
        "condenser_rank": min(condenser, MAX_CONDENSER_RANK),
        "trust_points": trust,
        "is_awake": awake,
    }
    return {
        "hp": hp_breakdown(species, level, iv=int(ivs.get("hp") or 0),
                           soul_rank=int(souls.get("hp") or 0),
                           passive_bonus=bonuses.get("hp", 0.0), **common),
        "attack": attack_breakdown(species, level, iv=int(ivs.get("shot") or 0),
                                   soul_rank=int(souls.get("attack") or 0),
                                   passive_bonus=bonuses.get("attack", 0.0), **common),
        "defense": defense_breakdown(species, level, iv=int(ivs.get("defense") or 0),
                                     soul_rank=int(souls.get("defense") or 0),
                                     passive_bonus=bonuses.get("defense", 0.0), **common),
        "workSpeed": work_speed_breakdown(
            species, level,
            condenser_rank=min(condenser, MAX_CONDENSER_RANK),
            soul_rank=int(souls.get("craftSpeed") or 0),
            passive_bonus=bonuses.get("workSpeed", 0.0),
        ),
        "friendshipRank": friendship_rank(trust),
        "progress": level_progress(level, int(pal.get("exp") or 0)),
        # So a caller can say *why* a number is what it is without re-deriving it.
        "inputs": {
            "level": level,
            "condenserRank": min(condenser, MAX_CONDENSER_RANK),
            "condenserStars": max(0, min(condenser, MAX_CONDENSER_RANK) - 1),
            "soulRanks": {k: int(v or 0) for k, v in souls.items()},
            "trustPoints": trust,
            "isAlpha": bool(pal.get("isBoss")),
            "isLucky": bool(pal.get("isLucky")),
        },
        # The stats are computed, not read. Said in the payload so a UI cannot
        # present them with the same authority as a level or an IV.
        "calculated": True,
    }
