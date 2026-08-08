"""
What condenser stars do to work suitability — measured in game, not in a file.

**This project answered "no, they do nothing" three times before anyone looked.**
The operator kept saying otherwise and was right. It is not in any file: no
DataTable, no setting in `BP_PalGameSetting`, and nothing in the save — across 20
Pals at condenser rank 4 or 5, nineteen have no `GotWorkSuitabilityAddRankList`
at all, and one rank-5 Verdash carries an **empty passive list**, so it differs
from a rank-1 Verdash in `Rank` and nothing else. The bonus is derived at load,
which is exactly why every search of static data came back empty and why those
searches were the wrong instrument.

So this module is on `elements.py`'s terms: hand-entered is legitimate when the
data genuinely does not exist, and the obligation is provenance plus a visible
"unknown" rather than a guess dressed as a reading.

## The rule, and what each part rests on

    star 1   +1 to its best suitability
    star 2   +1 to its 2nd-best
    star 3   +1 to its 3rd-best
    star 4   +1 to EVERY suitability
             ... all clamped to WorkSuitabilityMaxRank (10)

Cumulative, so a 4-star Pal has three suitabilities at +2 and the rest at +1.

**Two of the four columns of the community table this came from are reproduced
exactly by the game's own files** — `CharacterRankUpRequiredNumMap = {1:4, 2:8,
3:12, 4:24}` and `StatusCalculate_GenkaiToppa_PerAdd = 0.05` — which is why it
was worth testing rather than dismissing.

## Observations (operator's own world, 2026-08-07)

| Pal | Base | Stars | Observed | Predicted |
|---|---|---:|---|---|
| Jetragon | Gathering 8 | 4 | 10 | 8+4=12, capped **10** |
| Aegidron | Mining 8 | 4 | 10 | capped **10** |
| Jormuntide | Watering 7 | 4 | 10 | 7+4=11, capped **10** |
| Anubis | Handcraft 6, Mining 6, Transport 4 | 1 | **7 / 6 / 4** | +1 to best only |
| Verdash | Seeding 4, Handcraft 5, Collection 5, Deforest 3, Transport 3 | 4 | 5 / 7 / 7 / 5 / 4 | three at +2, two at +1 |

**THE 1-STAR ANUBIS IS THE LOAD-BEARING READING**, and a 4-star one could not
have replaced it. Its top two are *tied at 6* and only Handiwork moved — so a
tie is broken by the game's own `BestWorkSuitability` column, not by both sides
getting it and not by enum order. At 4 stars the tied pair converges on 8 either
way and the question would have stayed open.

**THE FALLTHROUGH IS REAL AND IT IS THE MAJORITY CASE.** A Pal with one
suitability has no "2nd-best", and Jormuntide reaching 10 rather than 9 is what
says the bonus lands on the only one it has. 181 of the 343 base species have
fewer than three suitabilities.

## WHAT IS STILL UNKNOWN, AND IT IS NOT A DETAIL

**The 2nd- and 3rd-best ordering is not by base value.** Verdash's three winners
are Handiwork (5), Gathering (5) and **Lumbering (3)** — while Planting, at base
**4**, got only the all-stars +1. So whatever orders the 2nd and 3rd slots, it
is not the numbers, and `BestWorkSuitability` names only the first.

One observation cannot distinguish the alternatives (enum order continuing from
the best; some designer-set priority; or a `WorkSuitabilityAddRank_Deforest`
passive on a Pal at that base adding a confounding +1). Picking whichever fits
this single Pal would be fitting the method to the answer, which this repo
records as a mistake twice already.

So `bonus()` returns a value **only where the rule determines one**, and says
`determined: False` otherwise. That covers **262 of 343 species (76%)** for
free, because a Pal with three or fewer suitabilities has every one of them
inside the top three — which three is which stops mattering.
"""

from __future__ import annotations

from typing import Any, Optional

import gamedata

#: Where this comes from. Carried in the payload, not just this docstring, for
#: the reason `hasMultiplier` and `stated` are: the client is the thing about to
#: render a number and must be able to say where it came from.
SOURCE = (
    "Measured in game by the server operator, 2026-08-07, and corroborated by "
    "the community table whose sacrifice counts and stat percentages the game's "
    "own files reproduce exactly. Not stated in any game file."
)

MAX_STARS = 4


def _cap() -> Optional[int]:
    """`WorkSuitabilityMaxRank`, or None when the bundle is unreadable."""
    value = gamedata.game_setting("WorkSuitabilityMaxRank")
    return int(value) if isinstance(value, (int, float)) and value > 0 else None


def bonus(
    suitabilities: dict[str, int],
    best: str = "",
    stars: int = 0,
) -> dict[str, Any]:
    """
    `{work: extra_rank}` from `stars` condenser stars, where the rule decides it.

    `stars` is 0-4 — **not** the save's `Rank`, which is 1-5. Rank 1 is no stars,
    and treating the two as the same number is the mistake `palstats` documents
    for the stat multiplier.

    Returns `determined: False` with a reason when the rule does not fix an
    answer, which is any Pal with four or more suitabilities at two stars or
    more. A number there would be a guess, and a guess beside a read one is the
    failure this module exists to avoid.
    """
    works = {k: int(v) for k, v in (suitabilities or {}).items() if int(v or 0) > 0}
    stars = max(0, min(int(stars or 0), MAX_STARS))

    out: dict[str, Any] = {
        "stars": stars,
        "bonus": {w: 0 for w in works},
        "determined": True,
        "source": SOURCE,
        "reason": "",
    }
    if not works or stars == 0:
        # Nine species have no suitability at all. There is nothing to add to,
        # and inventing one would be worse than saying zero.
        return out

    count = len(works)
    # Star 4 lifts everything; stars 1-3 each lift one slot.
    everyone = 1 if stars == MAX_STARS else 0
    slots = min(stars, 3)

    if count <= 3:
        # **Every suitability is inside the top three, so the ordering cannot
        # matter.** With `slots` individual bonuses spread over `count` targets
        # and a fallthrough onto what the Pal actually has, each one receives
        # `slots // count` guaranteed, plus one more for the first
        # `slots % count` in whatever order the game uses — which is only
        # ambiguous when the division is uneven.
        share, spare = divmod(slots, count)
        if spare == 0 or count == 1:
            for work in out["bonus"]:
                out["bonus"][work] = share + everyone
            return out
        # Uneven: some get one more than others and we cannot say which, except
        # that `best` is always first in line.
        for work in out["bonus"]:
            out["bonus"][work] = share + everyone
        if best in out["bonus"]:
            out["bonus"][best] += 1
            spare -= 1
        if spare == 0:
            return out
        out["determined"] = False
        out["reason"] = (
            f"{spare} of the per-star bonuses land on suitabilities this rule "
            f"cannot order — only the best one is named by the game."
        )
        return out

    # Four or more suitabilities: the 2nd and 3rd slots are unordered.
    for work in out["bonus"]:
        out["bonus"][work] = everyone
    if best in out["bonus"] and slots >= 1:
        out["bonus"][best] += 1
        slots -= 1
    if slots == 0:
        return out
    out["determined"] = False
    out["reason"] = (
        f"{slots} further +1 bonus(es) go to the 2nd- and 3rd-best "
        f"suitabilities, and the game's ordering for those is unknown — "
        f"measured on Verdash, it is NOT by base value."
    )
    return out


def apply(suitabilities: dict[str, int], best: str = "", stars: int = 0) -> dict[str, Any]:
    """
    The resulting levels, clamped, plus the same `determined` flag.

    Clamping is why the three single-suitability readings all land on 10 from
    three different bases: 8+4 and 7+4 both exceed `WorkSuitabilityMaxRank`.
    """
    result = bonus(suitabilities, best, stars)
    cap = _cap()
    levels = {}
    for work, base in (suitabilities or {}).items():
        total = int(base) + int(result["bonus"].get(work, 0))
        levels[work] = min(total, cap) if cap else total
    return {**result, "levels": levels, "cap": cap}
