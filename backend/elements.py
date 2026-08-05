"""
Element effectiveness — which element beats which.

THIS IS THE ONE PIECE OF GAME DATA HERE THAT IS HAND-ENTERED, and it lives in a
module rather than in `backend/data/` deliberately. Everything in that directory
is *extracted* — from the game pak or from `refs/PalWorldSaveTools-main.zip` —
and a regeneration script can re-derive it. This cannot be re-derived, so filing
it beside the extracted bundles would blur exactly the distinction that makes
those bundles trustworthy.

WHY IT IS NOT EXTRACTED
-----------------------
It is in neither source, and both were searched exhaustively rather than
guessed at:

- **Not in the server pak.** All 480 DataTables were listed and read; there is no
  `Compatibility`, `Effectiveness`, `Weakness`, `AttributeDamage` or
  `ElementDamage` asset of any kind. The only element DataTable is
  `DT_PalAwakeningItemElement`, which maps items to elements and carries no
  multipliers. Everything else matching "Element" is visual effects, elemental
  treasure-box locks and player step-attack statuses.
- **Not in the PST archive.** All 78 entries matching "element" there are icons.

So it lives in C++ or in a blueprint's unversioned properties — the same wall
`DT_BossSpawnerLoactionData` hits, and unlike the passive-effect table it does
not come down by switching paks.

The precedent for shipping it anyway is `editschema.MAX_LEVEL`, which is also a
documented community-sourced constant rather than a derived one. The rule this
project actually holds is "do not hand-write game data **that already exists** in
`refs/`" — this does not exist there, so the obligation is to say where it came
from, which is what this docstring is.

SOURCE
------
Rock Paper Shotgun's Palworld element chart:
https://www.rockpapershotgun.com/palworld-element-chart

TWO THINGS THAT WERE VERIFIED, because a cited source is not the same as a
checked one:

1. **The relation is exactly reciprocal.** Nine "strong against" pairs and nine
   "weak to" pairs, and the two sets are identical — every strength has its
   matching weakness and there are no orphans in either direction. A chart
   transcribed with an error almost certainly breaks this.
2. **Every element name resolves against the bundled Pal data**, which uses
   `Dark, Dragon, Earth, Electric, Fire, Grass, Ice, Neutral, Water`. Eight of
   the nine match the article's spelling exactly; only `Ground` needed mapping to
   `Earth`. So this is not a vocabulary invented here.

WHAT IS DELIBERATELY ABSENT
---------------------------
**Damage multipliers.** The article presents them in an image, which is not text
that could be transcribed, so no numbers are shipped. `effectiveness()` returns a
*relation* — strong, weak or neutral — and callers must not invent a coefficient
to go with it. Saying "Water is strong against Fire" is supported; saying "for
1.5x damage" is not.
"""

from __future__ import annotations

import logging
from typing import Optional

import gamedata

logger = logging.getLogger(__name__)

# The nine elements, spelled as the bundled Pal data spells them.
#
# THIS IS A FALLBACK, NOT THE SOURCE. `game_elements()` reads the real list off
# the bundled Pal data, so the game decides what elements exist and this file
# only claims to know how they interact. The tuple exists so a missing bundle
# degrades to a working chart instead of an empty one.
_FALLBACK_ELEMENTS = (
    "Neutral", "Fire", "Water", "Electric", "Grass",
    "Earth", "Ice", "Dragon", "Dark",
)


def game_elements() -> tuple[str, ...]:
    """
    Every element the bundled Pal data actually uses, sorted.

    Derived rather than declared, so a content update that adds an element shows
    up here without a code change — and `unknown_to_chart()` can then say the
    hand-entered relation has fallen behind, instead of the new element silently
    reading as "neutral against everything".
    """
    try:
        found = {
            str(e)
            for pal in (gamedata.load().get("pals") or {}).values()
            for e in (pal.get("elements") or [])
            if e
        }
    except Exception:  # noqa: BLE001 - a missing bundle must not break combat maths
        found = set()
    return tuple(sorted(found)) if found else tuple(sorted(_FALLBACK_ELEMENTS))


ELEMENTS = game_elements()

# The article says "Ground"; the game's own data says "Earth". The only name
# that needed translating, which is itself a reason to trust the rest.
_ALIASES = {
    "ground": "Earth",
    # Tolerated because the passive-effect table uses a *third* vocabulary —
    # `ElementBoost_Leaf`, `_Electricity`, `_Normal` — and a caller holding one
    # of those should not silently get "no effect".
    "leaf": "Grass",
    "electricity": "Electric",
    "normal": "Neutral",
}

# attacker -> the elements it deals extra damage to.
#
# Neutral is strong against nothing, and that is the game's design rather than a
# gap in the transcription: Neutral Pals trade combat matchups for base work.
STRONG_AGAINST: dict[str, tuple[str, ...]] = {
    "Fire": ("Grass", "Ice"),
    "Grass": ("Earth",),
    "Earth": ("Electric",),
    "Electric": ("Water",),
    "Water": ("Fire",),
    "Ice": ("Dragon",),
    "Dragon": ("Dark",),
    "Dark": ("Neutral",),
    "Neutral": (),
}

# Derived rather than written twice. A second hand-maintained table is how the
# two drift apart, and the reciprocity check above is only meaningful if one of
# the two sides is not simply a copy of the other — so the *source* transcription
# was checked against the article's own weakness column once, and from here on
# there is a single definition.
WEAK_TO: dict[str, tuple[str, ...]] = {
    defender: tuple(
        attacker for attacker, targets in STRONG_AGAINST.items()
        if defender in targets
    )
    # Keyed on the chart's own elements rather than on `ELEMENTS`, so that a game
    # update adding one produces an *absence* here that `unknown_to_chart` can
    # report — instead of an entry with an empty tuple, which is indistinguishable
    # from a legitimately unopposed element like Neutral.
    for defender in STRONG_AGAINST
}


def unknown_to_chart() -> tuple[str, ...]:
    """
    Elements the game has that the hand-entered relation says nothing about.

    THE REASON THE ELEMENT LIST IS DERIVED. This chart is the only thing in the
    project that cannot be regenerated from the game, so it is the only thing
    that can silently rot: a content update adding a tenth element would make
    every matchup involving it read as "neutral", which is a confident wrong
    answer rather than a visible gap.

    Empty is the healthy state. Anything in here means the chart needs a human
    and a new source, and callers should say so rather than answering.
    """
    return tuple(e for e in game_elements() if e not in STRONG_AGAINST)


def chart_is_current() -> bool:
    """Whether the relation covers every element the game actually ships."""
    return not unknown_to_chart()


def canonical(element: str) -> Optional[str]:
    """
    An element name in any of the three vocabularies -> the bundled spelling.

    Case-insensitive, like every other lookup in this project and for the same
    reason: the save, the reference archive and the pak all disagree about
    capitalisation, and an exact match silently loses real data.
    """
    if not element:
        return None
    text = str(element).strip()
    alias = _ALIASES.get(text.lower())
    if alias:
        return alias
    for known in ELEMENTS:
        if known.lower() == text.lower():
            return known
    return None


def effectiveness(attacker: str, defender: str) -> str:
    """
    `"strong"`, `"weak"` or `"neutral"` for one attacking element against one
    defending element.

    NO MULTIPLIER, on purpose — see the module docstring. The source presents its
    damage values as an image, so the numbers were never available as text and
    nothing here should pretend otherwise. An unknown element on either side is
    `"neutral"`, so a modded element costs the matchup rather than the answer.
    """
    a, d = canonical(attacker), canonical(defender)
    if not a or not d:
        return "neutral"
    if d in STRONG_AGAINST.get(a, ()):
        return "strong"
    # `WEAK_TO[a]` is the set of elements strong against `a`, so the defender
    # appearing there is exactly "this attacker is at a disadvantage".
    if d in WEAK_TO.get(a, ()):
        return "weak"
    return "neutral"


def matchup(attacker_elements, defender_elements) -> str:
    """
    The best relation any of an attacker's elements has against any of a
    defender's.

    A Pal can carry two elements, so "is this a good matchup" is a question about
    sets rather than about a pair. **Strong wins over weak** when both apply: the
    player chooses which move to use, so having a strong option available is what
    decides the encounter, not the existence of a bad one alongside it.
    """
    relations = {
        effectiveness(a, d)
        for a in (attacker_elements or [])
        for d in (defender_elements or [])
    }
    if "strong" in relations:
        return "strong"
    if "weak" in relations:
        return "weak"
    return "neutral"
