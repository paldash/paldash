#!/usr/bin/env python3
"""
The egg-move pool: which active skills a species can roll beyond its level-up
learnset, from the game's own tables (#139, #64's open half).

Two server-pak tables, read together because each verifies the other:

    DT_WazaDataTable.IgnoreRandomInherit   384 skills; FALSE on 103
    DT_WazaMasterTamago                    7,111 {PalID, WazaID} rows

`IgnoreRandomInherit` is the game naming the mechanic — a per-skill flag
excluding it from *random inheritance*. The 281 excluded skills are exactly
the partner skills, unique funnels and boss moves; the 103 remaining are
ordinary attacks. *Tamago* is "egg", and every one of the pool's 47 distinct
moves sits inside the 103-skill inheritable set with ZERO exceptions — two
independently-read tables agreeing is what makes this a pool of randomly
grantable moves rather than a guess from a table name. The build refuses if
that agreement ever breaks.

**Every PalID in the tamago table is a `BOSS_` form** — 283 of 283. The pool
is stored per species on its boss row and resolved here to the base id,
exactly as `gamedata.pal()` resolves names (an alpha Anubis is still an
Anubis). Recorded rather than interpreted: nothing here claims why Pocketpair
keyed it that way.

WHAT IS NOT CLAIMED, because no file states it: how many moves an egg rolls,
at what rate, or whether the parents' own moves enter the draw.
`bInheritAllActiveSkills` on Cake05 (the breeding-cake table) says active-
skill inheritance exists and that cake makes it total; no `Combi_Waza*`
constant exists in the settings CDO or the binary's 100,368 identifiers. A
save-side check cannot settle it either — measured on refworld, 103 of 212
out-of-learnset mastered moves on real Pals fall inside the pool, and the
rest are indistinguishable from Skill Fruit teaching, which can add any move.
So this bundle is the POOL, never the odds.
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import jsonout       # noqa: E402
import palpak        # noqa: E402
import uassettable   # noqa: E402

OUT = os.path.join(os.path.dirname(HERE), "backend", "data", "egg_moves.json.gz")


def _table(pak, name: str) -> dict:
    path = next((f for f in pak.files if f.endswith(f"{name}.uasset")), None)
    if path is None:
        raise SystemExit(f"!! {name} not in the pak")
    return uassettable.read_table(pak, path)


def _bare(w) -> str:
    s = str(w or "")
    return s.rsplit("::", 1)[-1] if "::" in s else s


def build() -> dict:
    pak = palpak.Pak()
    waza = _table(pak, "DT_WazaDataTable")
    tamago = _table(pak, "DT_WazaMasterTamago")

    inheritable: set[str] = set()
    blocked: set[str] = set()
    for row in waza.values():
        wid = _bare(row.get("WazaType"))
        if wid and wid != "None":
            (blocked if row.get("IgnoreRandomInherit") else inheritable).add(wid)

    pools: dict[str, list[str]] = {}
    raw_pools: dict[str, set[str]] = defaultdict(set)
    non_boss: list[str] = []
    for row in tamago.values():
        pal_id = str(row.get("PalID") or "")
        # Case-insensitive on the prefix: 52 of the 7,111 rows spell it
        # `Boss_FlameBambi` where the rest say `BOSS_` — an FName compares
        # case-insensitively, a str.startswith does not, and the first version
        # of this refused the build over Pocketpair's own capitalisation.
        # Same disagreement the item ids have (`GunPowder2`/`Gunpowder2`).
        if not pal_id[:5].upper() == "BOSS_":
            # The observed invariant is all-BOSS keys. A base-form key would
            # mean the table changed shape underneath this reader.
            non_boss.append(pal_id)
            continue
        raw_pools[pal_id[5:]].add(_bare(row.get("WazaID")))

    if non_boss:
        raise SystemExit(
            f"!! {len(non_boss)} tamago rows key a non-BOSS form "
            f"({sorted(set(non_boss))[:5]}) — the all-BOSS invariant broke, "
            "re-examine before trusting the base-species resolution")

    strays = sorted({w for pool in raw_pools.values() for w in pool} - inheritable)
    if strays:
        # The agreement between the two tables IS the verification — a pool
        # move the skill table marks non-inheritable means one of the two
        # reads drifted, and shipping either would be shipping a guess.
        raise SystemExit(
            f"!! {len(strays)} pool move(s) carry IgnoreRandomInherit "
            f"({strays[:5]}) — the two tables no longer agree, refusing")

    pools = {species: sorted(pool) for species, pool in sorted(raw_pools.items())}

    return {
        "_note": (
            "Per-species egg-move pools from DT_WazaMasterTamago (tamago = "
            "egg), stored by the game on each species' BOSS_ row and resolved "
            "to the base id here. Every move is one the skill table marks "
            "randomly inheritable (IgnoreRandomInherit false) — 47 of 47 at "
            "extraction, refused otherwise. The POOL only: no file states how "
            "many moves an egg rolls or at what rate, and none of this says "
            "the child inherits its parents' own moves."
        ),
        "pools": pools,
        # The mechanism's global half: the skills random inheritance may ever
        # grant, and the count it excludes — kept so the UI can say the pool
        # is drawn from a set the game itself closes.
        "inheritableSkills": sorted(inheritable),
        "excludedSkillCount": len(blocked),
    }


def main() -> int:
    data = build()
    jsonout.write_json(OUT, data)
    pools = data["pools"]
    sizes = [len(v) for v in pools.values()]
    print(f"wrote {OUT}")
    print(f"  {len(pools)} species, pool sizes {min(sizes)}-{max(sizes)}, "
          f"{len(data['inheritableSkills'])} skills inheritable, "
          f"{data['excludedSkillCount']} excluded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
