#!/usr/bin/env python3
"""
Diff `DynamicItemSaveData` between two saves, to settle how a record is created.

WHY THIS EXISTS
---------------
`backend/dynamicitem.py` can repair a weapon, armour or egg record but refuses to
**create** one, so "add an egg to this chest" and "add a weapon to this
inventory" are both refused. The refusal is not about the record's format — a
deep copy of an existing record of the right type solves that, the way
`palclone` does for Pals. It is about **how many copies to write.**

Measured on the reference world: 32,446 records, 2,052 distinct local ids, and
the copy count per id is **1, 5, 6 or 16** with no visible pattern. An earlier
guess here was that the duplicates were accumulated orphans; joining them against
the container slots that reference them disproved it — 1,487 of the 2,022
sixteen-copy ids are pointed at by a live slot, as are all 17 one-copy ids and
all 12 six-copy ids, with zero references resolving to nothing. They are real.

So writing one record where the game expects sixteen would leave a
half-registered item in someone's chest, and nobody can say which count is right
for a *new* item without watching the game make one.

THIS IS THE FIVE-MINUTE EXPERIMENT THAT ANSWERS IT
--------------------------------------------------
1. Stop the server. Take a backup, or just copy `Level.sav` somewhere.
2. Start it, and in game do exactly ONE thing — craft one weapon, or put one
   freshly-obtained egg in a chest. Nothing else.
3. Stop the server again and copy `Level.sav` a second time.
4. Run this against the two files.

What the output tells you:

  * how many records the new item added (**the number the refusal is waiting
    for**), and whether that number depends on the item's type;
  * whether the new records are byte-identical to each other, as the existing
    duplicates are;
  * whether anything *else* in the array moved, which would mean records are not
    simply appended and a create path has more to do than append.

If one crafted weapon adds exactly one record, `can_create` can be implemented as
a deep copy plus one append. If it adds sixteen, it can be implemented as sixteen
appends. Either way the answer replaces a guess.

READ-ONLY. It opens two saves and prints. It cannot write anything.
"""

from __future__ import annotations

import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))


def load(path: str):
    from palsav.core import decompress_sav_to_gvas
    from palsav.gvas import GvasFile
    from palsav.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS

    with open(path, "rb") as f:
        raw = f.read()
    # The FULL custom-property set, not the project's trimmed read-path one:
    # `DynamicItemSaveData.RawData` is an opaque ByteProperty without it, and the
    # diff would compare two blobs and report every record as changed.
    gvas = GvasFile.read(
        decompress_sav_to_gvas(raw)[0], PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES
    )
    return gvas.properties["worldSaveData"]["value"]


def fingerprint(record: dict) -> str:
    """
    A stable string for one record, for set comparison.

    `repr` rather than JSON because the values include palsav's own `UUID` class
    and raw `bytes`, neither of which serialises — and a lossy encoding here would
    hide exactly the byte-level difference this is looking for.
    """
    return repr(record)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", help="Level.sav from before the in-game action")
    parser.add_argument("after", help="Level.sav from after it")
    args = parser.parse_args()

    import dynamicitem as di

    before_world, after_world = load(args.before), load(args.after)
    before = di.index_by_local_id(before_world)
    after = di.index_by_local_id(after_world)

    def histogram(index):
        return dict(sorted(collections.Counter(len(v) for v in index.values()).items()))

    print(f"before: {sum(len(v) for v in before.values()):,} records, "
          f"{len(before):,} ids, copies-per-id {histogram(before)}")
    print(f"after:  {sum(len(v) for v in after.values()):,} records, "
          f"{len(after):,} ids, copies-per-id {histogram(after)}")

    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = [
        lid for lid in set(before) & set(after)
        if [fingerprint(r) for r in before[lid]] != [fingerprint(r) for r in after[lid]]
    ]

    print(f"\nnew ids: {len(added)}   removed ids: {len(removed)}   "
          f"ids whose records changed: {len(changed)}")

    # The headline. Everything else is context for it.
    for lid in added:
        records = after[lid]
        described = di.describe(records[0])
        prints = {fingerprint(r) for r in records}
        print(f"\n  NEW  {lid}")
        print(f"       {described.get('staticId')}  type={described.get('type')}")
        print(f"       ---> the game wrote {len(records)} copy/copies <---")
        print(f"       all copies byte-identical: {len(prints) == 1}")
        if len(prints) != 1:
            print("       (they are NOT identical, so a create path cannot just "
                  "duplicate one — dump them and compare)")

    for lid in removed:
        print(f"\n  GONE {lid}  ({len(before[lid])} copies)")

    for lid in changed[:10]:
        print(f"\n  CHANGED {lid}: {len(before[lid])} -> {len(after[lid])} copies")
        print(f"          {di.describe(after[lid][0])}")
    if len(changed) > 10:
        print(f"\n  …and {len(changed) - 10} more changed ids")

    if not (added or removed or changed):
        print("\nNothing changed. Either the in-game action did not create a "
              "durability item, or the save was not written — Palworld only "
              "persists on autosave or a clean shutdown.")
    elif added:
        print(
            "\nThe copy count above is the number `dynamicitem.can_create()` is "
            "waiting for. If it is 1, creation is a deep copy plus one append; if "
            "it is 16, it is sixteen appends. Record it in AGENTS.md either way — "
            "a measured number ends the refusal."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
