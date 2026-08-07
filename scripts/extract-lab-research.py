#!/usr/bin/env python3
"""
The Pal Lab research tree — 168 nodes with prerequisites, costs and effects.

WHY THIS IS WORTH BUNDLING. Research is **guild-wide and permanent**, so it is
the one base upgrade that explains why two identical Pals produce differently on
two different servers. The dashboard has always shown a Pal's work level and
speed and had no idea the guild had bought +10% Handiwork.

`DT_LabResearchDataTable` was found by searching every table's *columns* for
`WorkSuitability` while chasing an unrelated question. It had never been opened.
That is the same shape `docs/GAMEDATA-SOURCES.md` exists to prevent, one
directory over.

WHAT A ROW IS
-------------
    TextId                       NAME_HANDCRAFT1, joined to the game's own strings
    LabCategoryWorkSuitability   which work it improves
    LabCategorySubType           CraftSpeed_Handcraft / TechnologyUnlock / …
    RequiredWorkAmount           50,000 work units — NOT a time, see below
    RequiredResearchId           the prerequisite, or None for a root
    Material1..4_Id / _Count     what it costs to start
    EffectType                   EPalPassiveSkillEffectType, the SAME vocabulary
                                 `passive_effects.json.gz` uses
    EffectValue                  10.0
    bIsEssential                 see below

**`EffectType` shares the passive vocabulary, but only partly — CHECKED, because
the first version of this docstring asserted otherwise.** Five of the sixteen
types here (`CraftSpeed`, `FarmCropGrowupSpeed`, `FarmCropHarvestNumRate`,
`ItemCorruptionSpeedRate`, `PalEggHatchingSpeed`) are already known to
`backend/passiveeffects.py`. **Eleven are research-only** and appear on no
passive: base-worker combat rates, energy storage and consumption, expedition
reward and time, oil extraction, production yield, and the lab's own research
speed. Rules for those were added to `passiveeffects` so a research effect gets
the same label and category treatment; nothing was assumed to be there already.

**`EPalPassiveSkillEffectType::no` is the game's own "no effect".** Ten rows
carry it, all `subType: TechnologyUnlock` with `effectValue: 0.0` — they unlock
a technology rather than granting a rate. It is normalised to `None` here, so a
client never renders "no +0%".

TWO THINGS THIS DOES NOT SAY
----------------------------
- **`RequiredWorkAmount` is not a duration.** It is work units, and how fast a
  base delivers them depends on which Pals are assigned. `basesupply.py`'s rule:
  report facts, not mechanics. The field travels named for what it is.
- **`bIsEssential` is not asserted to mean "required to progress".** It is a
  field whose name reads like an answer, which is the `TowerLockBarrier` and
  `IgnoreCombi` shape. It is carried verbatim and nothing branches on it.

THE ACCEPTANCE CRITERION
------------------------
Every `RequiredResearchId` must resolve to another row, and every material to a
real item id. A dangling prerequisite means the join is wrong, and a tree with a
broken edge renders as a node nobody can reach — which reads as a game rule
rather than a bug. The script refuses rather than shipping one.
"""

from __future__ import annotations

import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uassettable          # noqa: E402
from palpak import Pak      # noqa: E402
from jsonout import write_json  # noqa: E402

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(PROJECT_ROOT, "backend", "data", "lab_research.json.gz")
TABLE = "DT_LabResearchDataTable"

#: Below this the table is not what we think it is and the build is refused.
MIN_ROWS = 100


class LabError(Exception):
    """A decode or a join that could not be verified."""


def _plain(value):
    """`EPalWorkSuitability::Handcraft` -> `Handcraft`; `None` -> None."""
    text = str(value or "")
    tail = text.rsplit("::", 1)[-1]
    return None if tail in ("", "None") else tail


def _effect_type(value) -> str | None:
    """`EffectType`, with the game's `::no` sentinel normalised to None."""
    name = _plain(value)
    return None if name == "no" else name


def extract(pak: Pak) -> dict:
    paths = {os.path.basename(p)[:-7]: p for p in uassettable.data_tables(pak)}
    if TABLE not in paths:
        raise LabError(f"{TABLE} not in the pak")
    rows = uassettable.read_table(pak, paths[TABLE])
    if len(rows) < MIN_ROWS:
        raise LabError(f"{TABLE} decoded {len(rows)} rows, expected >= {MIN_ROWS}")

    nodes = {}
    for key, row in rows.items():
        materials = []
        for i in (1, 2, 3, 4):
            item = _plain(row.get(f"Material{i}_Id"))
            count = int(row.get(f"Material{i}_Count") or 0)
            if item and count > 0:
                materials.append({"itemId": item, "count": count})

        nodes[str(key)] = {
            "id": str(key),
            "textId": _plain(row.get("TextId")),
            "work": _plain(row.get("LabCategoryWorkSuitability")),
            "subType": _plain(row.get("LabCategorySubType")),
            "assignId": _plain(row.get("AssignDefineId")),
            # Work UNITS. Never rendered as a time — see the module docstring.
            "workAmount": float(row.get("RequiredWorkAmount") or 0.0),
            "requires": _plain(row.get("RequiredResearchId")),
            "materials": materials,
            # `::no` is the game's literal "this grants no rate" — see above.
            "effectType": _effect_type(row.get("EffectType")),
            "effectValue": float(row.get("EffectValue") or 0.0),
            "effectWork": _plain(row.get("EffectOptionWorkSuitability")),
            # Carried verbatim; nothing branches on it. Its name reads like an
            # answer and this project has been burned by exactly that twice.
            "essential": bool(row.get("bIsEssential")),
        }

    dangling = sorted(
        n["requires"] for n in nodes.values()
        if n["requires"] and n["requires"] not in nodes
    )
    if dangling:
        raise LabError(
            f"{len(dangling)} prerequisite(s) resolve to no row: {dangling[:6]}. "
            "This is a refusal, not a partial tree — a broken edge renders as a "
            "node nobody can reach, which reads as a game rule rather than a bug."
        )

    roots = [n["id"] for n in nodes.values() if not n["requires"]]
    by_work = collections.Counter(n["work"] for n in nodes.values())
    effects = collections.Counter(n["effectType"] for n in nodes.values())

    return {
        "research": nodes,
        "roots": sorted(roots),
        "byWork": dict(by_work),
        "effectTypes": dict(effects),
        "note": (
            "workAmount is work units, not a duration — how long it takes depends "
            "on which Pals are assigned, which no game file states. Research is "
            "guild-wide and permanent."
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pak", default=None)
    ap.add_argument("--verify", action="store_true", help="check, do not write")
    args = ap.parse_args()

    try:
        bundle = extract(Pak(args.pak) if args.pak else Pak())
    except Exception as e:  # noqa: BLE001
        print(f"Extraction failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    nodes = bundle["research"]
    print(f"{len(nodes)} research nodes, {len(bundle['roots'])} roots")
    print(f"  by work: {bundle['byWork']}")
    # None is a real key here — the ten TechnologyUnlock rows grant no rate.
    named = sorted(k for k in bundle['effectTypes'] if k)
    print(f"  effect types: {named}")
    print(f"  nodes granting no rate (TechnologyUnlock): {bundle['effectTypes'].get(None, 0)}")

    # Materials must be real items. Reported rather than enforced here: the
    # catalogue is a separate bundle and a mismatch is a naming question, not a
    # broken tree. A dangling PREREQUISITE is the fatal one and already raised.
    try:
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "backend"))
        import gamedata

        unknown = sorted({
            m["itemId"] for n in nodes.values() for m in n["materials"]
            if not gamedata.item_name(m["itemId"])
        })
        if unknown:
            print(f"  WARNING: {len(unknown)} material ids not in the catalogue: "
                  f"{unknown[:8]}")
        else:
            print(f"  all material ids resolve against the item catalogue")
    except Exception as e:  # noqa: BLE001
        print(f"  (material check skipped: {e})")

    if args.verify:
        return 0
    write_json(OUT, bundle)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
