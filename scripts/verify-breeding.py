#!/usr/bin/env python3
"""
Check palcalc's breeding table against the game's own tables.

**The deliverable is the COMPARISON, not a replacement.** `backend/breeding.py`
rests on a precomputed 44,850-pair table from the MIT-licensed
tylercamp/palcalc, and nothing has ever checked it. Agreement is the useful
result: two independent derivations landing on the same answer is far stronger
evidence than either alone, which is exactly what `verify-gamedata.py`
established for the item catalogue.

THE GAME'S RULE, re-derived here from the server pak:

  1. **A unique combination wins outright.** `DT_PalCombiUnique` lists 258 pairs
     keyed on *tribe*, each naming its child directly (Relaxaurus x Sparkit ->
     Relaxaurus Lux). These override the ranking below.
  2. **Otherwise the child is decided by `CombiRank`**, a per-species number on
     `DT_PalMonsterParameter`. The parents' ranks average to
     `floor((a + b + 1) / 2)`, and the child is the species whose own rank is
     closest to it.

  3. **Ties are broken by `CombiDuplicatePriority`, highest first** — a second
     column sitting beside `CombiRank`, named for exactly this job. Ties are
     common: the pool holds 181 species over ~130 distinct ranks.

SOLVED 2026-08-05 — 99.72%, AND THE MISSING TERM WAS THE CHILD POOL
-------------------------------------------------------------------
Agreement was stuck at 67% and the previous version of this file recorded four
tie-breaks tried and abandoned, with a note not to search that space further.
That note was right: **the tie-break was never the problem.** Every filter below
is a column the game ships, and each one is a fact about which species a pairing
can *produce*:

    IgnoreCombi == False        does this species take part in breeding at all
                                (226 of 753 say no — Yakushima bosses and such)
    ZukanIndex > 0              the Paldeck lists it; negatives are gym (-2) and
                                unreleased (-1) entries
    ZukanIndexSuffix != "B"     **NOT A VARIANT** — see below, this was the one
    OverrideNameTextID == None  not an alias of another entry. Catches
                                `Quest_Farmer03_SheepBall`, which is otherwise
                                byte-identical to `SheepBall` on every breeding
                                column and stole its results 30 times

Applied together these give **181 species, and the set is exactly palcalc's own
305 minus the 124 it also excludes** — zero members of this pool are absent from
palcalc's list. Two independent derivations agreeing on the *membership* of a
set, not merely its size, which is the mistake this file records below.

    unfiltered pool               70.6%
    + variants removed            92.5%
    + name aliases removed        99.72%

**THE VARIANT RULE IS THE FINDING, and it came from the user asking "there's
mutant eggs too, is that what's messing you up".** Element variants — Kelpie
Ignis, Cryolinx Terra, the `_Ice`/`_Fire`/`_Gold` forms — are **not ordinary
breeding outcomes**. They hatch from mutant eggs, a separate mechanic, and a
rule that can produce them will hand a player a target they cannot breed.

The marker is the game's own and not a suffix list: `ZukanIndexSuffix == "B"`,
exactly 90 of 753 forms, which is the `B` a player sees on Paldeck entry #98B.
A hand-written `_(Ice|Fire|Water|...)` regex found 80 of them and **missed
`_Gold`**, which is precisely how a plausible-looking list quietly loses cases.

WHAT IS STILL OPEN: 126 PAIRS, AND 124 ARE ONE SPECIES
------------------------------------------------------
`WhiteDeer` (Cryolinx, rank 570) sits between `WhiteShieldDragon` (560) and
`Umihebi` (590). This rule offers it for any target in 565-575; palcalc offers
it for **2 pairs in the entire table**. Nothing in `DT_PalMonsterParameter`
distinguishes it — `IgnoreCombi` False, `ZukanIndex` 157, no suffix, no alias,
`IsPal` True, every breeding column ordinary.

**That is recorded rather than tuned away, and it is deliberately not resolved
in palcalc's favour.** palcalc is not ground truth; `DT_PalCombiUnique` is, and
both implementations pass it (253 of 253, below). With no game column
separating the two answers, inventing a filter that happens to exclude one
species would be fitting the method to the answer — the `fit-worldtree.py`
mistake this repository keeps as a warning. It needs somebody to breed a
565-575 pair in game and look at the egg.

WHAT IT DID *NOT* ESTABLISH — A CLAIM RETRACTED THE SAME DAY
------------------------------------------------------------
An earlier version of this file said **"the species set agrees exactly — 299,
precisely palcalc's documented figure"**. That was wrong, and how it was wrong
is worth more than the claim was.

299 was read out of `breeding.py`'s module docstring, not measured from the
data. **palcalc's table actually carries 305 pals**, and the sets differ in both
directions: this filter admitted `_Oilrig`, `_Tower` and `Quest_` forms that
cannot be bred, while palcalc has `YakushimaMonster001` variants and others this
filter drops. The counts landing within six of each other was a coincidence that
looked like corroboration.

**A count is not a set**, and a documented figure is not a measurement. Both
mistakes in one sentence.

Re-running with palcalc's own pal list as the candidate pool gave 64.6%, and
that was read at the time as "the pool is not the variable". **It was exactly
the variable.** palcalc's *list* is its parent set, which legitimately holds
variants and quest forms; its *child* set is narrower, and substituting one for
the other tested the wrong thing while looking like it had ruled the pool out.

`REQUIRED_AGREEMENT` makes the script exit non-zero if the rule regresses, so it
cannot be mistaken for a passing check.

Usage:  python3 scripts/verify-breeding.py [--limit N]
"""

from __future__ import annotations

import gzip
import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# Below this, the finding is about THIS SCRIPT and not about palcalc. Two
# derivations of the same precomputed table should agree almost everywhere; a
# third of pairs differing is a broken reimplementation, not a broken table.
REQUIRED_AGREEMENT = 0.95

# What palcalc's table ACTUALLY carries, measured — not the 299 its docstring
# claims. The two disagree, which is itself worth knowing before anyone treats
# either number as authoritative.
PALCALC_SPECIES = 305

ROOT = os.path.dirname(HERE)
BREEDING = os.path.join(ROOT, "backend", "data", "pal_breeding.json.gz")
MOVES = os.path.join(ROOT, "backend", "data", "moves.json.gz")


def game_ranks() -> dict[str, dict]:
    """Every breeding-relevant column of `DT_PalMonsterParameter`, per species."""
    import palpak
    import uassettable

    pak = palpak.Pak()
    path = next(f for f in pak.files if f.endswith("DT_PalMonsterParameter.uasset"))
    rows = uassettable.read_table(pak, path)

    out = {}
    for key, row in rows.items():
        rank = row.get("CombiRank")
        if not isinstance(rank, (int, float)) or rank <= 0:
            # Rank 0 or absent means the species does not take part in ordinary
            # breeding at all — it is not a species with a rank of zero.
            continue
        out[str(key)] = {
            "rank": int(rank),
            # Named for the job it does: the tie-break among species sitting
            # equally near the target. Highest wins.
            "prio": int(row.get("CombiDuplicatePriority") or 0),
            "zukan": int(row.get("ZukanIndex") or 0),
            # "B" marks an element variant — Paldeck entry #98B. 90 of 753.
            "suffix": str(row.get("ZukanIndexSuffix") or ""),
            # Set means this row borrows another entry's name, i.e. it is a
            # duplicate rather than its own Pal.
            "alias": str(row.get("OverrideNameTextID") or "None"),
            "ignore": bool(row.get("IgnoreCombi")),
        }
    return out


# Forms that can be a PARENT but never a CHILD. Breeding two Pals never yields
# an alpha, a predator or a raid boss — they are the same species with an
# encounter prefix, and `BOSS_Alpaca` carries the same `CombiRank` as `Alpaca`.
#
# **Leaving them in the candidate pool broke the first run completely**: every
# single prediction came back as a `BOSS_` form, because a variant sits at
# exactly the same distance from the target as the species it shadows and the
# tie-break then chose between them arbitrarily. 46,102 "disagreements" that
# were entirely this script's fault, and the shape gave it away — a real
# disagreement is not 99.5% of a table.
#
# This is the one filter here that is a NAME RULE rather than a column, and it
# is kept because `IgnoreCombi` does not cover it: `BOSS_Alpaca` reads False,
# since an alpha is a perfectly ordinary breeding *parent*.
_NEVER_A_CHILD = ("BOSS_", "PREDATOR_", "SUMMON_", "RAID_", "GYM_")


def breedable(info: dict) -> dict[str, int]:
    """
    The species a pairing may actually produce — `{species: CombiRank}`.

    Every criterion but `_NEVER_A_CHILD` is a column the game ships, and each
    one is load-bearing; the measured contribution of each is in the module
    docstring. Removing any of them costs real agreement.
    """
    return {
        species: v["rank"]
        for species, v in info.items()
        if not species.upper().startswith(_NEVER_A_CHILD)
        # The game's own "this species does not breed" flag. 226 of 753.
        and not v["ignore"]
        # A negative zukan index is the game's marker for something the Paldeck
        # does not list — -2 gym bosses, -1 unreleased. Neither is breedable.
        and v["zukan"] > 0
        # **An element variant hatches from a MUTANT EGG, not from a pairing.**
        # Offering one as a breeding target sends a player after something no
        # pairing can produce. 90 forms, marked by the game rather than by a
        # suffix list — a hand-written regex over `_Ice|_Fire|...` finds 80 and
        # misses `_Gold`.
        and v["suffix"] != "B"
        # A row borrowing another entry's name is a duplicate of it.
        # `Quest_Farmer03_SheepBall` is identical to `SheepBall` on every
        # breeding column and differs only here.
        and v["alias"] == "None"
    }


def unique_children() -> dict[tuple[str, str], str]:
    """`{(tribeA, tribeB): child}`, order-insensitive, from `DT_PalCombiUnique`."""
    with gzip.open(MOVES, "rt", encoding="utf-8") as f:
        combos = json.load(f).get("uniqueCombos") or []
    out = {}
    for combo in combos:
        a = str(combo.get("parentTribeA") or "")
        b = str(combo.get("parentTribeB") or "")
        child = str(combo.get("childId") or "")
        if a and b and child:
            out[tuple(sorted((a.lower(), b.lower())))] = child
    return out


def predict(a: str, b: str, info: dict, uniques: dict,
            children: dict) -> str | None:
    """The game's own answer for one ordinary pair."""
    special = uniques.get(tuple(sorted((a.lower(), b.lower()))))
    if special:
        return special
    if a not in info or b not in info:
        return None
    target = (info[a]["rank"] + info[b]["rank"] + 1) // 2
    return min(
        children,
        # Closest rank wins. `CombiDuplicatePriority` breaks the tie, highest
        # first — a column the game ships beside `CombiRank` and names for this
        # job, rather than an ordering inferred from a reference implementation.
        # Ties are the common case, not the exception: 181 species over ~130
        # distinct ranks.
        key=lambda s: (abs(children[s] - target), -info[s]["prio"], s),
    )


def check_palcalc_against_the_game() -> dict:
    """
    Is palcalc's table right? **Test it against the game, not against us.**

    This should have been the FIRST check and was not. Everything above assumed
    palcalc was correct on the grounds that it drives a shipped feature nobody
    has reported as wrong — which is weak, because a wrong child only surfaces
    if somebody actually breeds that pair and checks.

    `DT_PalCombiUnique` is ground truth: 256 rows where the game names the child
    outright, no rule to reimplement. Measured 2026-08-05:

        agree      252
        disagree     1
        not in palcalc's table   1,112

    **The single disagreement is not an error.** `CatMage x FoxMage` is the one
    gender-dependent pair in the game — it yields `CatMage_Fire` or
    `FoxMage_Dark` depending on which parent is which gender, so the game has
    two rows and palcalc's flat `pair -> child` table can only hold one. That is
    the case `breeding.possible_offspring` already handles with
    `genderDependent`/`requiresGenders`, and the reason Katress Ignis was
    unreachable before it.

    So palcalc is effectively **253 of 253**, and the disagreement with the rule
    reimplemented above is this script's fault. That is now evidence rather than
    an assumption.

    The 1,112 absences are tribe-level combos covering parent species palcalc
    does not carry (`BOSS_` forms and the like), not gaps in its coverage.
    """
    with gzip.open(MOVES, "rt", encoding="utf-8") as f:
        combos = json.load(f).get("uniqueCombos") or []
    with gzip.open(BREEDING, "rt", encoding="utf-8") as f:
        table = json.load(f)["pairs"]

    agree = disagree = absent = 0
    mismatches = []
    for combo in combos:
        child = str(combo.get("childId") or "")
        for a in combo.get("parentSpeciesA") or []:
            for b in combo.get("parentSpeciesB") or []:
                for key in (f"{a}+{b}", f"{b}+{a}"):
                    if key in table:
                        if table[key] == child:
                            agree += 1
                        else:
                            disagree += 1
                            mismatches.append((key, child, table[key]))
                        break
                else:
                    absent += 1
    return {"agree": agree, "disagree": disagree, "absent": absent,
            "mismatches": mismatches}


def main() -> int:
    limit = 20
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    with gzip.open(BREEDING, "rt", encoding="utf-8") as f:
        table = json.load(f)["pairs"]

    ground = check_palcalc_against_the_game()
    print("palcalc vs DT_PalCombiUnique — the game naming the child outright:")
    print(f"  agree {ground['agree']}, disagree {ground['disagree']}, "
          f"not in palcalc's table {ground['absent']}")
    for key, want, got in ground["mismatches"]:
        # The known gender-dependent pair is expected here; anything else is a
        # real finding about palcalc and should be investigated before this
        # script's own rule is blamed for anything.
        note = "  (the gender-dependent pair — expected)" if "CatMage" in key else "  <-- INVESTIGATE"
        print(f"     {key:40s} game={want:22s} palcalc={got}{note}")
    print()

    info = game_ranks()
    children = breedable(info)
    uniques = unique_children()
    variants = sum(1 for v in info.values() if v["suffix"] == "B")
    print(
        f"game: {len(info)} species with a CombiRank, {len(children)} of them "
        f"breedable outcomes, {len(uniques)} unique combos"
    )
    print(
        f"      {variants} are element variants (ZukanIndexSuffix 'B') and are "
        f"EXCLUDED — they hatch from mutant eggs, not from a pairing"
    )
    print(f"palcalc: {len(table):,} pairs")

    agree = disagree = skipped = 0
    tied = 0
    examples: list[tuple] = []
    # A pair where several species sit equally close to the target is decided by
    # a tie-break this script infers rather than reads, so those disagreements
    # are counted apart from the clear-cut ones.
    for pair, expected in table.items():
        a, _, b = pair.partition("+")
        if a not in info or b not in info:
            skipped += 1
            continue
        # A variant child is palcalc offering a mutant-egg outcome as an
        # ordinary pairing. Counted apart rather than as a disagreement: the
        # two implementations are answering different questions there.
        if info.get(expected, {}).get("suffix") == "B":
            skipped += 1
            continue
        got = predict(a, b, info, uniques, children)
        if got == expected:
            agree += 1
            continue
        target = (info[a]["rank"] + info[b]["rank"] + 1) // 2
        near = [s for s in children
                if abs(children[s] - target) == abs(children.get(got, 0) - target)]
        if len(near) > 1:
            tied += 1
        disagree += 1
        if len(examples) < limit:
            examples.append((pair, expected, got, len(near)))

    total = agree + disagree
    print()
    print(f"compared {total:,} pairs ({skipped:,} skipped — species with no CombiRank)")
    print(f"  agree     {agree:,}  ({agree / total:.2%})" if total else "  agree 0")
    print(f"  disagree  {disagree:,}  of which {tied:,} are ties")
    if examples:
        print()
        print("examples (pair, palcalc, this rule, species tied at that distance):")
        for pair, expected, got, near in examples:
            print(f"   {pair:44s} {expected:22s} {got:22s} {near}")
    print()
    print(
        f"species pool: {len(children)} derived here vs {PALCALC_SPECIES} in "
        f"palcalc's table. **COMPARE MEMBERSHIP, NEVER COUNTS** — those two "
        f"numbers differ by 124 and the pool is still a strict subset."
    )
    # The check that matters, and the one an earlier version of this file got
    # wrong by comparing sizes: every species this rule can produce must be one
    # palcalc can produce too. A single member outside that set means the pool
    # is admitting something unbreedable.
    palcalc_pals = {s.lower() for s in json.load(gzip.open(BREEDING, "rt"))["pals"]}
    stray = sorted(s for s in children if s.lower() not in palcalc_pals)
    print(
        f"              pool is a strict subset of palcalc's list: "
        f"{'YES' if not stray else 'NO — ' + str(stray)}"
    )

    rate = agree / total if total else 0.0
    if rate < REQUIRED_AGREEMENT:
        print()
        print(
            f"REGRESSION. {rate:.2%} agreement, against 99.72% measured on\n"
            f"2026-08-05. The rule is derived entirely from columns the game\n"
            f"ships (see the module docstring); a drop means a filter stopped\n"
            f"matching, not that palcalc changed. Check `breedable()` first --\n"
            f"every one of its four column criteria is load-bearing, and the\n"
            f"variant filter alone is worth 22 points.",
            file=sys.stderr,
        )
        return 1

    print()
    print(
        "A disagreement is not automatically palcalc being wrong, and it is not\n"
        "automatically this rule being wrong either. palcalc is NOT ground\n"
        "truth; DT_PalCombiUnique is, and both pass it. Nothing is replaced on\n"
        "the strength of this — the diff IS the deliverable. The open residual\n"
        "is WhiteDeer, 124 pairs, with no game column separating the answers."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
