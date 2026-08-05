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

**The tie-break is the part worth stating.** Several species can sit equally
near the target, and the game breaks the tie by `ZukanIndex` — Paldeck order.
That is an inference from the reference implementation rather than something a
table states, so a disagreement confined to ties is a *weaker* finding than one
on a clear-cut pair, and this script reports the two separately.

CURRENT STATE, 2026-08-05: **THE RULE ABOVE IS NOT YET FAITHFUL, AND THIS IS
THEREFORE NOT YET A VERIFICATION OF PALCALC.**

Measured agreement is 67% at best. That is far too low to be a real
disagreement: palcalc's table is precomputed from the same tables and drives a
shipped feature nobody has reported as wrong, so a third of all pairs differing
means *this script* has the rule wrong.

Four tie-breaks were tried and are recorded so nobody repeats them:

    rank desc      31,114/46,352   67.13%
    zukan asc      24,827/46,352   53.56%
    table order    24,706/46,352   53.30%
    rank asc       20,687/46,352   44.63%

**Tuning stopped there deliberately.** Trying variants until the number rises is
fitting the method to the answer — the mistake `scripts/fit-worldtree.py` is
kept as a recorded warning about. The next step is to read palcalc's own
derivation for the missing term, not to search this space further.

WHAT IT DID ESTABLISH
---------------------
**The species set agrees exactly.** Filtering `DT_PalMonsterParameter` to forms
that can be a child — no `BOSS_`/`PREDATOR_`/`SUMMON_`/`RAID_`/`GYM_` prefix and
a non-negative Paldeck index — yields **299**, which is precisely the count
palcalc documents. Two independent derivations of "which Pals can be bred"
landing on the same number is a real result even though the pairings are not yet
reproduced.

`REQUIRED_AGREEMENT` makes the script exit non-zero until the rule is faithful,
so it cannot be mistaken for a passing check.

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

# palcalc's own documented figure, and what the breedable-species filter here
# independently produces. Agreement on the SET is a real result even while the
# pairings are not reproduced.
PALCALC_SPECIES = 299

ROOT = os.path.dirname(HERE)
BREEDING = os.path.join(ROOT, "backend", "data", "pal_breeding.json.gz")
MOVES = os.path.join(ROOT, "backend", "data", "moves.json.gz")


def game_ranks() -> tuple[dict[str, int], dict[str, int]]:
    """`({species: CombiRank}, {species: ZukanIndex})` from the server pak."""
    import palpak
    import uassettable

    pak = palpak.Pak()
    path = next(f for f in pak.files if f.endswith("DT_PalMonsterParameter.uasset"))
    rows = uassettable.read_table(pak, path)

    ranks, zukan = {}, {}
    for key, row in rows.items():
        rank = row.get("CombiRank")
        if not isinstance(rank, (int, float)) or rank <= 0:
            # Rank 0 or absent means the species does not take part in ordinary
            # breeding at all — it is not a species with a rank of zero.
            continue
        ranks[str(key)] = int(rank)
        zukan[str(key)] = int(row.get("ZukanIndex") or 0)
    return ranks, zukan


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
_NEVER_A_CHILD = ("BOSS_", "PREDATOR_", "SUMMON_", "RAID_", "GYM_")


def breedable(ranks: dict, zukan: dict) -> dict[str, int]:
    """The species a pairing may actually produce."""
    return {
        species: rank
        for species, rank in ranks.items()
        if not species.upper().startswith(_NEVER_A_CHILD)
        # A negative zukan index is the game's marker for something the Paldeck
        # does not list — -2 gym bosses, -1 unreleased. Neither is breedable.
        and zukan.get(species, 0) > 0
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


def predict(a: str, b: str, ranks: dict, zukan: dict, uniques: dict,
            children: dict) -> str | None:
    """The game's own answer for one ordinary pair."""
    special = uniques.get(tuple(sorted((a.lower(), b.lower()))))
    if special:
        return special
    if a not in ranks or b not in ranks:
        return None
    target = (ranks[a] + ranks[b] + 1) // 2
    best = min(
        children,
        # Closest rank wins; Paldeck order breaks a tie. The second term is an
        # inference from the reference implementation, not a stated rule, which
        # is why ties are reported separately.
        key=lambda s: (abs(children[s] - target), zukan.get(s, 9999), s),
    )
    return best


def main() -> int:
    limit = 20
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    with gzip.open(BREEDING, "rt", encoding="utf-8") as f:
        table = json.load(f)["pairs"]

    ranks, zukan = game_ranks()
    children = breedable(ranks, zukan)
    uniques = unique_children()
    print(
        f"game: {len(ranks)} species with a CombiRank, {len(children)} of them "
        f"breedable outcomes, {len(uniques)} unique combos"
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
        if a not in ranks or b not in ranks:
            skipped += 1
            continue
        got = predict(a, b, ranks, zukan, uniques, children)
        if got == expected:
            agree += 1
            continue
        target = (ranks[a] + ranks[b] + 1) // 2
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
    if len(children) == PALCALC_SPECIES:
        print(
            f"SPECIES SET AGREES: {len(children)} breedable outcomes, which is "
            f"exactly palcalc's documented figure. Two independent derivations "
            f"of 'which Pals can be bred' landing on the same number is a real "
            f"result."
        )

    rate = agree / total if total else 0.0
    if rate < REQUIRED_AGREEMENT:
        print()
        print(
            f"NOT A VERIFICATION. {rate:.2%} agreement is far too low to be a\n"
            f"finding about palcalc: its table is precomputed from these same\n"
            f"game tables and drives a shipped feature nobody has reported as\n"
            f"wrong. A third of pairs differing means THIS SCRIPT has the rule\n"
            f"wrong, and the module docstring records the four tie-breaks already\n"
            f"tried. Read palcalc's derivation for the missing term rather than\n"
            f"searching that space further — tuning until the number rises is\n"
            f"fitting the method to the answer.",
            file=sys.stderr,
        )
        return 1

    print()
    print(
        "A disagreement is not automatically palcalc being wrong: it may be this\n"
        "rule, the inferred tie-break, or the species sets differing. Nothing is\n"
        "replaced on the strength of this — the diff IS the deliverable."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
