#!/usr/bin/env python3
"""
Which work each structure needs, and who is allowed to do it.

WHAT THIS CORRECTS. `backend/optimise.py` shipped refusing to build base
assignment, with a docstring saying no game file carried a
build-object-to-work-suitability mapping. `backend/basesupply.py` reached the
same conclusion one commit earlier from `DT_MapObjectMasterDataTable`, which
genuinely does not carry one — its columns are HP, defense and material type.

`DT_MapObjectAssignData` carries exactly that mapping, in 271 rows, and always
did. Both refusals were honest about what had been checked and wrong about what
was there, which is why `docs/GAMEDATA-SOURCES.md` now exists.

WHAT A ROW IS
-------------
The key is `<MapObjectId>_<slot>`, and **a structure can have several slots**.
A farm plot has three — Seeding, Watering and Collection — so reading only `_0`
answers a third of the question and looks complete. Slots are aggregated here.

Per slot the table gives:

    WorkSuitability          which work
    WorkSuitabilityRank      the MINIMUM rank a Pal needs. DamagableRock0004
                             wants rank 3, so a rank-1 miner cannot touch it —
                             this is the field that turns "who is best at
                             mining" into "who can work THIS node"
    WorkerMaxNum             see the warning below
    bBaseCampWorkerWorkable  whether a base Pal may work it at all
    bPlayerWorkable          whether it is a player-only station
    AffectSanityValue        sanity drained per tick, e.g. CopperPit -0.15,
                             Well -0.08, BreedFarm 0.0
    WorkableTribeIDs / GenusCategory / ElementType / WorkableSizeMin/Max
                             species restrictions
    MultiWorkSuitability1/2  structures needing a second and third work type

**`WorkerMaxNum` IS NOT A CAPACITY OF ZERO.** 178 of 271 rows carry 0, including
StonePit and CopperPit, which obviously take workers. Whatever it means — no
explicit cap, or a cap derived from the object's size — it is **not** "nobody can
work here", and a UI rendering "0/0 assigned" from it would be confidently wrong.
It is bundled as `workerMax` with 0 preserved and `workerMaxIsUnset` beside it so
a caller cannot read the two cases as one.

THE VERIFICATION
----------------
Two checks, and the second is the one that matters:

1. Every `WorkSuitability` must resolve against the 13 the bundled data knows.
   A new enum value means the game changed and this needs re-reading.

2. **The structures ABSENT from this table must be exactly the ones no Pal is
   ever assigned to.** Measured on the reference world: of 63 base-placed kinds,
   44 are in the table and the 19 that are not are chests, beds, the palbox, the
   spa, walls, the food box and the guild chest. That is a check the extraction
   cannot fake, because it depends on a save this script never reads —
   `test_work_assign.py` asserts it against `refworld`.

CASE. The save spells it `Workbench`; the table says `WorkBench`. Lookups are
case-insensitive for the same reason `gamedata`'s are — the upstream data is
inconsistently capitalised and an exact match silently loses real structures.

Usage:  python3 scripts/extract-work-assign.py [--verify]
Output: backend/data/work_assign.json.gz
"""

from __future__ import annotations

import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import palpak            # noqa: E402
import uassettable       # noqa: E402
from jsonout import write_json  # noqa: E402

TABLE_NAME = "DT_MapObjectAssignData.uasset"
OUT = os.path.join(os.path.dirname(HERE), "backend", "data", "work_assign.json.gz")

# The game's own "this field is unset". Every enum here uses it.
UNSET = {"", "None", None}


def _enum(value) -> str:
    """
    `EPalWorkSuitability::Mining` -> `Mining`.

    The bare id is what the API, the parser and `gamedata` all speak, exactly as
    `EquipWaza`'s `EPalWazaID::` prefix is stripped at the boundary and
    re-attached only on write. Nothing here writes, so it is stripped once.
    """
    text = str(value or "")
    return text.rsplit("::", 1)[-1] if "::" in text else text


def _slot(row: dict, suitability, rank_key: str) -> dict | None:
    """One work slot, or None when the row does not use it."""
    work = _enum(suitability)
    if work in UNSET:
        return None
    return {
        "work": work,
        # The minimum rank, not a target. A Pal below it cannot work the object.
        "requiredRank": int(row.get(rank_key) or 1),
    }


def build(pak=None) -> tuple[dict, dict]:
    pak = pak or palpak.Pak()
    path = next((p for p in pak.files if p.endswith(TABLE_NAME)), None)
    if path is None:
        raise SystemExit(f"{TABLE_NAME} is not in this pak — did the game update?")

    rows = uassettable.read_table(pak, path)

    objects: dict[str, dict] = {}
    works: Counter = Counter()

    for key, row in rows.items():
        # `<MapObjectId>_<slot>`. rsplit because ids themselves contain
        # underscores — `FarmBlockV2_wheet_2` is slot 2 of `FarmBlockV2_wheet`.
        object_id = str(key).rsplit("_", 1)[0]

        slots = []
        primary = _slot(row, row.get("WorkSuitability"), "WorkSuitabilityRank")
        if primary:
            slots.append(primary)
        for n in (1, 2):
            extra = _slot(
                row, row.get(f"MultiWorkSuitability{n}"), f"MultiRequiredRank{n}"
            )
            if extra:
                slots.append(extra)

        entry = objects.setdefault(object_id, {
            "id": object_id,
            "slots": [],
            "playerWorkable": False,
            "baseWorkerWorkable": False,
            "restrictions": {},
        })

        for slot in slots:
            works[slot["work"]] += 1
            entry["slots"].append({
                **slot,
                # 0 is preserved rather than normalised away. See the module
                # docstring: it is not a capacity of zero.
                "workerMax": int(row.get("WorkerMaxNum") or 0),
                "workerMaxIsUnset": int(row.get("WorkerMaxNum") or 0) == 0,
                "sanityPerTick": float(row.get("AffectSanityValue") or 0.0),
                "fullStomachPerTick": float(row.get("AffectFullStomachValue") or 0.0),
            })

        # Any slot allowing it makes the object workable by that party.
        entry["playerWorkable"] |= bool(row.get("bPlayerWorkable"))
        entry["baseWorkerWorkable"] |= bool(row.get("bBaseCampWorkerWorkable"))

        # Species restrictions, recorded only when the game actually sets one —
        # an entry of `None` everywhere is noise that makes 271 objects look
        # constrained when almost none are.
        for field, out_key in (
            ("GenusCategory", "genus"),
            ("ElementType", "element"),
            ("WorkableSizeMin", "sizeMin"),
            ("WorkableSizeMax", "sizeMax"),
        ):
            value = _enum(row.get(field))
            if value not in UNSET:
                entry["restrictions"][out_key] = value
        tribes = row.get("WorkableTribeIDs") or []
        if tribes:
            entry["restrictions"]["tribes"] = [_enum(t) for t in tribes]

    return objects, {"rows": len(rows), "objects": len(objects), "works": dict(works)}


def verify(objects: dict) -> list[str]:
    """
    Every work id must be one the bundled data knows.

    A new enum value is the game having changed, and is worth refusing over: a
    structure needing a work type nothing else in the dashboard recognises would
    silently drop out of every ranking rather than raising.
    """
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "backend"))
    import gamedata  # noqa: E402

    known = {str(w.get("id")) for w in gamedata.work_suitabilities()}
    # The game's own catch-alls: `Anyone` is any Pal (the Breeding Farm),
    # `None` means the slot exists but needs no suitability (incubators).
    allowed = known | {"Anyone", "None"}

    seen = {slot["work"] for entry in objects.values() for slot in entry["slots"]}
    return sorted(seen - allowed)


def main() -> int:
    pak = palpak.Pak()
    objects, stats = build(pak)

    unknown = verify(objects)
    if unknown:
        print(
            "REFUSING: work suitabilities this dashboard does not know: "
            f"{unknown}. The game has changed; re-read the work list before "
            "bundling, or a structure will silently vanish from every ranking.",
            file=sys.stderr,
        )
        return 2

    if "--verify" in sys.argv:
        print(f"verified {stats['objects']} objects from {stats['rows']} rows; "
              f"all work ids known")
        multi = sum(1 for e in objects.values() if len(e["slots"]) > 1)
        print(f"  {multi} objects need more than one kind of work")
        return 0

    write_json(OUT, {"objects": objects})
    print(f"wrote {OUT}")
    print(f"  {stats['objects']} structures from {stats['rows']} rows")
    print(f"  work types used: {len(stats['works'])}")
    for work, count in sorted(stats["works"].items(), key=lambda kv: -kv[1]):
        print(f"    {work:24s} {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
