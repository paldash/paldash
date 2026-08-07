"""
Who should be doing what: work assignment, party strength, and element matchups.

Three rankings over Pals this dashboard already parses. Nothing here writes, and
nothing here invents a number.

WHAT IS COMPUTED VERSUS WHAT IS READ
------------------------------------
Work suitability *levels* are read: `workSuitabilities` is the species' own table
from `gamedata`, and `workRanks` is `GotWorkSuitabilityAddRankList` off the Pal
— the ranks its owner bought with Pal Souls. Both are stored, neither is derived.

Work *speed*, attack, defense and HP are **calculated** by `palstats`, which is
a transcription of the game's formula, and every payload here carries
`calculated: true` for the same reason that module does: a UI must not show a
derived figure with the authority of a level.

THE ELEMENT CHART CARRIES NO MULTIPLIER, AND THIS MODULE MUST NOT INVENT ONE
---------------------------------------------------------------------------
`elements.py` ships the *relation* — strong, weak, neutral — as a documented
hand-entered constant, because it is in neither the pak nor the reference
archive. The only element-damage constant the game's own settings object
contains is `DamageElementMatchRate = 1.2`, whose semantic is inferred from its
name, and the widely repeated "2x dealt, 1/2 taken" is reproduced by no file.

So **combat ranking is on stats alone** and the matchup travels beside it as a
qualitative flag. Sorting by a made-up coefficient would produce an ordering that
looks authoritative and rests on nothing — the exact failure `elements.py` was
quarantined to avoid. `test_optimise.py` pins that no ranking key here is derived
from a matchup.

`chart_is_current()` travels in the payload for the same reason it exists: a
content update adding a tenth element makes every matchup involving it read as a
confident "neutral" rather than as a visible gap.

WHAT THIS DOES NOT DO
---------------------
**It does not say which base needs which work.** That would need a mapping from
each build object to the work suitability it consumes, and
`DT_MapObjectMasterDataTable` does not carry one — see `basesupply` for the same
wall. Ranking Pals by work type is a fact about the Pals; "your ore quarry is
understaffed" would be a claim about structures that no game file here supports.

**It does not assign.** There is no writer, and moving a Pal between containers
is `palclone`/`charedit` territory with its own verification. This answers "who
is best at this", which is the question a player actually asks before opening the
palbox.
"""

from __future__ import annotations

from typing import Any, Optional

import elements
import gamedata
import palstats
import workrank

# How many rows a ranking returns unless asked otherwise. A palbox holds 960 and
# nobody reads past the top of a list they asked to be sorted.
DEFAULT_LIMIT = 20


def work_types() -> list[dict[str, Any]]:
    """
    The game's own work list, in the game's own order.

    Order matters: `index` is Kindling, Watering, Planting, … — what every player
    already reads on a Pal's page in game. An alphabetical list shows the same
    thirteen things in a sequence nobody can scan against.
    """
    return gamedata.work_suitabilities()


#: Invoke conditions under which a suitability-granting passive applies to the
#: Pal carrying it. **Deliberately not `palstats.PASSIVE_SELF_INVOKES`**, which
#: answers "does this belong in the displayed stat block" — a different question,
#: and sharing the constant is how two surfaces silently converge. The exclusion
#: that matters is `InvokeInBaseCamp`: that is what the fourteen handbook effects
#: carry, and counting them would double the `bought` term.
_GRANT_INVOKES = frozenset({"InvokeAlways", "InvokeInOtomo", "InvokeActiveOtomo"})

#: Targets meaning "the Pal carrying this". `ToBaseCampPal` is excluded for the
#: same reason — it is the handbook shape, not a Pal's own trait.
_GRANT_TARGETS = frozenset({"ToSelf", "ToSelfAndTrainer"})

_GRANT_PREFIX = "WorkSuitabilityAddRank_"


def passive_work_rank(passive_ids: Any, work_id: str) -> int:
    """
    Work-suitability ranks this Pal's own passives grant for `work_id`.

    Derived from the effect bundle rather than a list of ids written here: a
    hardcoded list is how a passive added by an update stays uncounted, and the
    two that exist today (`Farmhand`, `Ranch Master`) were found in the bundle
    rather than from any documentation.

    Returns 0 for an unreadable bundle — a missing term is the behaviour this
    had before, and losing a whole ranking to a missing file is not.
    """
    ids = passive_ids if isinstance(passive_ids, (list, tuple)) else ()
    total = 0
    for passive_id in ids:
        entry = gamedata.passive_effects(str(passive_id))
        if not entry:
            continue
        if not (set(str(i) for i in (entry.get("invoke") or [])) & _GRANT_INVOKES):
            continue
        for effect in entry.get("effects") or []:
            kind = str(effect.get("type") or "")
            if not kind.startswith(_GRANT_PREFIX):
                continue
            if kind[len(_GRANT_PREFIX):] != work_id:
                continue
            if str(effect.get("target") or "") not in _GRANT_TARGETS:
                continue
            total += int(effect.get("value") or 0)
    return total


def work_level(pal: dict[str, Any], work_id: str) -> dict[str, Any]:
    """
    One Pal's level at one kind of work, with the two halves kept apart.

    `base` is the species' own suitability from the bundled table. `bought` is
    what this individual's owner spent on it, read off
    `GotWorkSuitabilityAddRankList`. They are reported separately as well as
    summed, because "this species is good at mining" and "somebody invested in
    this particular Pal" are different facts and a single number hides which.

    **FOURTEEN OF THE SIXTEEN `WorkSuitabilityAddRank_*` ENTRIES ARE NOT A THIRD
    SOURCE.** They sit in the passive-effect bundle and read exactly like a
    passive that grants a work rank — which is what a first attempt at this took
    them for, and what a second attempt took them for again in 2026-08. They are
    the effect applied by the **Applied … Handbook** items
    (`WorkSuitability_AddTicket_Mining` -> "Applied Mining Handbook I"), and the
    rank a handbook grants is written into `GotWorkSuitabilityAddRankList` — so
    it is *already* counted as `bought`. Adding them here counts it twice.

    The existing filters happen to reject them (`InvokeInBaseCamp` is not in
    `palstats.PASSIVE_SELF_INVOKES`, `ToBaseCampPal` not in
    `PASSIVE_SELF_TARGETS`), so nothing was ever double counted — but that is a
    coincidence of two unrelated guards, not a decision, which is why it is
    written down here rather than left to be rediscovered.

    **THE OTHER TWO ARE REAL, AND THEY WERE BEING DROPPED.** `Farmhand` and
    `Ranch Master` are `ToSelf` / `InvokeAlways` Pal passives with the game's own
    display names and prose ("Ranching's work suitability +2") — which is the
    tell that separates them from the fourteen, none of which carry either.
    Nothing writes them into `GotWorkSuitabilityAddRankList`: measured on the
    live world, **73 Pals carry one and every one of them has an empty
    `workRanks`**, so this is a gap rather than a double count.

    They are summed into a separate `passive` component rather than folded into
    `bought`, for the reason the other two are kept apart: "somebody spent a
    handbook on this Pal" and "this Pal was born with it" are different facts.

    The result is clamped to `WorkSuitabilityMaxRank`. A Ranch-8 Pal with Ranch
    Master is 10, not 10-and-a-bit, and the cap is read from the bundle rather
    than written here.

    **Condensing is believed to raise suitability too, and is deliberately NOT
    included** — see AGENTS.md. It is unverified, the rule is undetermined for
    half the roster by ties and fallthrough, and a third term that is wrong half
    the time is worse than a missing one.

    **A Pal with no suitability for a work type is level 0 and cannot do it** —
    that is the game's answer, not missing data. Two released Pals (Panthalus and
    Astralym) have an entirely empty work table, which is likewise real.
    """
    base = int((pal.get("workSuitabilities") or {}).get(work_id) or 0)
    # `workRanks` is None when the property is absent from the save, which is the
    # common case — most Pals have never had a rank bought. None and {} mean the
    # same thing here, so both read as zero.
    bought = int((pal.get("workRanks") or {}).get(work_id) or 0)
    granted = passive_work_rank(pal.get("passiveSkills"), work_id)

    level = base + bought + granted
    cap = workrank.max_rank()
    if cap:
        # A missing bound drops the clamp rather than guessing one, which is the
        # posture `editschema.max_work_rank()` already takes.
        level = min(level, cap)
    out = {"base": base, "bought": bought, "passive": granted, "level": level}

    # **What the level actually buys.** The game's own `CraftSpeeds` curve is not
    # linear — for Mining rank 3 is 100 and rank 10 is 1000 — so a bare integer
    # hid a tenfold difference in every row of this table.
    #
    # **And the curve is PER WORK TYPE.** Handcraft reaches 5,400 where Mining
    # reaches 1,000, so `speed` is comparable down a column and meaningless
    # across two work types. This module ranks within one work type at a time,
    # which is why that is safe here and would not be in a combined table.
    #
    # NOT folded into the sort. Level still orders this table, for the reason
    # below: speed cannot substitute for a level a Pal does not have, and the
    # material gate makes that literal — a rank-2 miner cannot touch Iron at any
    # speed. `test_matchup_never_enters_the_ordering`'s sibling logic.
    detail = workrank.describe(work_id, out["level"])
    if detail:
        out["speed"] = detail["speed"]
        out["relativeToRank3"] = detail["relativeToRank3"]
        out["curveStated"] = detail["stated"]
        for key in ("material", "materialGated", "dropRate", "pickupDisabled"):
            if key in detail:
                out[key] = detail[key]
    return out


def rank_for_work(
    pals: list[dict[str, Any]], work_id: str, *, limit: int = DEFAULT_LIMIT
) -> list[dict[str, Any]]:
    """
    Who should be doing this job, best first.

    Ordered by **work level, then work speed** — level first because a Pal with
    no suitability cannot do the job at any speed, so speed is a tie-break rather
    than a competing axis. Pals with level 0 are dropped entirely: listing every
    Pal that cannot mine under a "who should mine" heading is noise, and a short
    list plus a count of the excluded says more than a long one.
    """
    rows = []
    for pal in pals:
        level = work_level(pal, work_id)
        if level["level"] <= 0:
            continue
        stats = palstats.describe(pal)
        speed = int((stats or {}).get("workSpeed", {}).get("final") or 0)
        rows.append({
            **_identity(pal),
            "work": level,
            "workSpeed": speed,
            # Said per row, not once at the top: this is the only derived number
            # in the row and it should not inherit the authority of the level
            # beside it.
            "workSpeedCalculated": True,
        })

    rows.sort(key=lambda r: (-r["work"]["level"], -r["workSpeed"], r["name"]))
    return rows[:limit] if limit else rows


def rank_for_combat(
    pals: list[dict[str, Any]],
    *,
    limit: int = DEFAULT_LIMIT,
    against: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """
    Strongest Pals, by computed stats.

    **`against` never enters the ordering.** It attaches a qualitative matchup to
    each row — "strong", "weak" or "neutral" against the given elements — and the
    sort key is untouched. There is no multiplier to rank by (see the module
    docstring), so folding the matchup into a score would mean inventing one.

    The composite is `attack + defense + hp/10`, and it is **arbitrary and
    labelled as such** in the payload. Every component travels beside it so a
    caller can sort on whichever it actually cares about; the composite exists to
    give the list a default order, not to be a truth about Pal strength.
    """
    rows = []
    for pal in pals:
        stats = palstats.describe(pal)
        if not stats:
            # An NPC sharing CharacterSaveParameterMap with the Pals. No scaling
            # data exists for them anywhere, so they are absent rather than
            # zeroed — a breakdown of zeroes reads as a very weak Pal.
            continue
        attack = int(stats["attack"]["final"])
        defense = int(stats["defense"]["final"])
        hp = int(stats["hp"]["final"])
        row = {
            **_identity(pal),
            "attack": attack,
            "defense": defense,
            "hp": hp,
            "score": attack + defense + hp // 10,
            "scoreIsArbitrary": True,
            "calculated": True,
        }
        if against:
            row["matchup"] = elements.matchup(pal.get("elements") or [], against)
        rows.append(row)

    rows.sort(key=lambda r: (-r["score"], r["name"]))
    return rows[:limit] if limit else rows


def counters(pals: list[dict[str, Any]], target_elements: list[str]) -> dict[str, Any]:
    """
    Which of these Pals are elementally strong against a target, and which are at
    risk from it.

    Deliberately returns **sets, not a ranking**. The chart says which way a
    matchup goes and not by how much, so ordering "strong" Pals against each
    other on elemental grounds would be ordering on nothing. Callers that want an
    order within the strong set should sort it by stats, which `rank_for_combat`
    already does.
    """
    strong, weak, neutral = [], [], []
    for pal in pals:
        verdict = elements.matchup(pal.get("elements") or [], target_elements)
        {"strong": strong, "weak": weak, "neutral": neutral}[verdict].append(
            _identity(pal)
        )

    return {
        "target": list(target_elements),
        "strong": strong,
        "weak": weak,
        "neutral": neutral,
        # No multiplier. Said in the payload rather than only in a docstring,
        # because the caller is the one about to render a number that does not
        # exist.
        "hasMultiplier": False,
        "chartIsCurrent": elements.chart_is_current(),
        "unknownElements": list(elements.unknown_to_chart()),
    }


def _identity(pal: dict[str, Any]) -> dict[str, Any]:
    """
    Enough to tell two Pals of one species apart.

    A player usually owns several of the same species at the same level, so
    "Lamball · Lv 50" three times over is a list nobody can choose from. The
    instance id is last and is not pretty, but it is the only thing guaranteed
    unique when everything else matches.
    """
    return {
        "instanceId": str(pal.get("instanceId") or ""),
        "name": str(pal.get("nickname") or pal.get("speciesName") or
                    gamedata.character_name(str(pal.get("characterId") or ""))),
        "speciesId": str(pal.get("speciesId") or ""),
        "speciesName": str(pal.get("speciesName") or ""),
        "icon": str(pal.get("icon") or ""),
        "level": int(pal.get("level") or 0),
        "rank": int(pal.get("rank") or 1),
        "gender": str(pal.get("gender") or ""),
        "isBoss": bool(pal.get("isBoss")),
        "elements": list(pal.get("elements") or []),
        "location": str(pal.get("location") or ""),
        "baseId": str(pal.get("baseId") or ""),
    }
