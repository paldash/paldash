#!/usr/bin/env python3
"""
Re-derive the measured figures AGENTS.md quotes, across more than one world.

    python3 scripts/verify-figures.py refworld <world> [<world> ...]

WHY THIS EXISTS
---------------
Nearly every number in AGENTS.md was measured on `refworld` alone, and on
2026-08-04 one of them turned out to be an artifact of that file rather than a
fact about Palworld: it maps a local `DynamicItemSaveData` id to **sixteen**
byte-identical records on 2,022 of its 2,052 ids, while nine snapshots of the
same world's own server backups are one-record-per-id throughout. `refworld` is a
processed copy. That belief shaped a shipped refusal for months.

The lesson is not "refworld is bad" — it is that a single file had no control.
There is one now: `refs/palworld/Pal/Saved/SaveGames/0/og.backup/backup/world/`
holds 27 snapshots of the same world across a week, plus the live world itself.

HOW TO READ THE OUTPUT
----------------------
Each row is a measurement; each column a world. What matters is the SHAPE of the
disagreement, because two different things produce one:

  * **An artifact** shows as a step change at one world with the others
    agreeing — the duplicated records look like this.
  * **Drift** shows as a monotonic trend across snapshots ordered by time, which
    is just a week of people playing.

So pass the snapshots in chronological order and the distinction reads off the
row directly. A figure that is identical everywhere is confirmed.

Nothing here is a pass/fail gate. The code these numbers describe carries its own
verifications that fail closed; this is about the DOCUMENTED figures being
re-derivable, which is a different and weaker claim.

READ-ONLY. It opens worlds and prints.
"""

from __future__ import annotations

import argparse
import collections
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "backend"))

ZERO = "00000000-0000-0000-0000-000000000000"


def load(world_dir: str):
    """
    Two parses of one world, because the two halves need **opposite** property
    sets and mixing them silently loses data.

    THIS COST A WRONG MEASUREMENT AND IS THE MAIN THING THIS SCRIPT TAUGHT.
    The first version used the full `PALWORLD_CUSTOM_PROPERTIES` throughout and
    reported `0 of 11` worker containers on every world — including `refworld`,
    where the documented figure is 11 of 11 and correct. The full set *decodes*
    `WorkerDirector.RawData` into a struct rather than leaving it an opaque
    ByteProperty, so `extract_base_workers`, which reads a GUID at byte 98, finds
    no bytes to read.

    Nothing was broken: the module logged its warning and returned nothing, which
    is exactly the fail-closed behaviour it documents. The *measurement* was
    wrong. A verification script that quietly uses the wrong reader is worse than
    no verification, because it manufactures a regression.

      * `load_gvas(..., include_items=True)` — the trimmed set plus item
        properties. What the parser itself uses, so every `parser.extract_*`
        figure is derived exactly as production derives it.
      * the full set — the only way `DynamicItemSaveData` decodes at all.
    """
    from palsav.core import decompress_sav_to_gvas
    from palsav.gvas import GvasFile
    from palsav.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS
    from parser import load_gvas

    level = os.path.join(world_dir, "Level.sav")
    if not os.path.exists(level):
        raise SystemExit(f"No Level.sav in {world_dir}")

    parser_gvas = load_gvas(level, include_items=True)
    if parser_gvas is None:
        raise SystemExit(f"Could not parse {level}")

    with open(level, "rb") as f:
        raw = f.read()
    full_gvas = GvasFile.read(
        decompress_sav_to_gvas(raw)[0], PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES
    )
    return parser_gvas, full_gvas


def measure(pair) -> dict:
    """Every figure, from one world. Keys are the row labels."""
    gvas, full = pair
    import dynamicitem
    import itemclone
    import saveedit
    from parser import (
        extract_base_camps,
        extract_base_workers,
        extract_characters,
        extract_container_ownership,
        extract_pal_storage,
    )

    world = gvas.properties["worldSaveData"]["value"]
    # Durability records only decode under the full property set — see `load`.
    full_world = full.properties["worldSaveData"]["value"]
    out: dict[str, object] = {}

    # ── Characters and their containers ──
    players, pals = extract_characters(gvas)
    out["characters (Pals + NPCs)"] = len(pals)
    out["player characters"] = len(players)

    no_owner = sum(
        1 for p in pals
        if not p.get("ownerUid") or str(p["ownerUid"]).replace("-", "") == "0" * 32
    )
    out["characters with no OwnerPlayerUId"] = no_owner

    ccs = world.get("CharacterContainerSaveData", {}).get("value") or []
    out["character containers"] = len(ccs)

    caps = collections.Counter()
    slot_entries = 0
    for entry in ccs:
        raw = entry.get("value") or {}
        cap = ((raw.get("SlotNum") or {}).get("value"))
        caps[cap] += 1
        slots = ((raw.get("Slots") or {}).get("value") or {}).get("values") or []
        slot_entries += len(slots)
    out["character-container capacities"] = dict(sorted(caps.items(), key=lambda kv: -kv[1]))
    out["character slot entries"] = slot_entries
    # `palclone` rests on this: the array holds only OCCUPIED slots, so a clone
    # appends rather than fills. If entries ever exceed characters, that is wrong.
    out["slot entries == characters"] = slot_entries == len(pals)

    # ── Base workers, and the measured byte offset ──
    workers = extract_base_workers(gvas)
    bases = extract_base_camps(gvas)
    out["base camps"] = len(bases)
    out["worker containers resolved"] = f"{len(workers)} of {len(bases)}"

    blob_lengths = collections.Counter()
    camps = (world.get("BaseCampSaveData") or {}).get("value") or []
    for entry in camps:
        blob = (
            ((entry.get("value") or {}).get("WorkerDirector") or {})
            .get("value", {}).get("RawData", {}).get("value", {})
        )
        blob = blob.get("values") if isinstance(blob, dict) else blob
        if isinstance(blob, (bytes, bytearray)):
            blob_lengths[len(blob)] += 1
    out["WorkerDirector blob lengths"] = dict(blob_lengths)

    # ── Pal storage structures ──
    storage = extract_pal_storage(gvas)
    out["Pal-storage containers"] = len(storage)
    out["Pal-storage kinds"] = dict(
        collections.Counter(s["kind"] for s in storage.values())
    )

    # ── Item containers and ownership ──
    ownership = extract_container_ownership(gvas)
    out["objects carrying a container id"] = len(ownership)
    out["…attributed to a base"] = sum(1 for o in ownership.values() if o["baseCampId"])
    out["…world-placed"] = sum(1 for o in ownership.values() if o["worldPlaced"])

    ics = (world.get("ItemContainerSaveData") or {}).get("value") or []
    out["item containers"] = len(ics)
    total_slots = 0
    missing_index = 0
    for entry in ics:
        for slot in itemclone._slots(entry):
            raw = saveedit._slot_raw(slot)
            if raw is None:
                continue
            total_slots += 1
            if raw.get("slot_index") is None:
                missing_index += 1
    out["item slot entries"] = total_slots
    out["…missing slot_index"] = missing_index

    # ── Durability records: the one that started this ──
    records = dynamicitem._records(full_world)
    index = dynamicitem.index_by_local_id(full_world)
    out["dynamic item records"] = len(records)
    out["…distinct local ids"] = len(index)
    out["…copies per id"] = dict(
        sorted(collections.Counter(len(v) for v in index.values()).items())
    )
    out["…record types"] = dict(
        collections.Counter(dynamicitem._raw(r).get("type") for r in records)
    )
    eggs = [r for r in records if dynamicitem._raw(r).get("type") == "egg"]
    out["eggs with a Pal inside"] = (
        f"{sum(1 for r in eggs if (dynamicitem._raw(r).get('object') or {}))} of {len(eggs)}"
    )

    # Slot references that resolve to no record at all — the check that said the
    # duplicates were live rather than orphaned.
    referenced = set(itemclone._records_by_item(full))
    out["slot refs resolving to nothing"] = len(referenced - set(index))

    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("worlds", nargs="+", help="world directories, oldest first")
    ap.add_argument("--width", type=int, default=26, help="column width")
    args = ap.parse_args()

    labels = [os.path.basename(w.rstrip("/")) or w for w in args.worlds]
    results = []
    for world in args.worlds:
        print(f"reading {world} …", file=sys.stderr)
        results.append(measure(load(world)))

    rows = list(results[0].keys())
    name_width = max(len(r) for r in rows) + 2

    header = "".ljust(name_width) + "".join(l[:args.width].ljust(args.width) for l in labels)
    print("\n" + header)
    print("-" * len(header))

    for row in rows:
        values = [r.get(row) for r in results]
        rendered = [str(v) for v in values]
        # A row where every world agrees needs no attention; one where they do
        # not is the entire output of this script, so it is marked rather than
        # left for the reader to diff by eye.
        agree = len(set(rendered)) == 1
        mark = "  " if agree else "* "
        print(mark + row.ljust(name_width - 2)
              + "".join(v[:args.width].ljust(args.width) for v in rendered))

    print(
        "\n* = the worlds disagree. A step change at one world with the rest "
        "agreeing is an artifact of that file;\n"
        "    a monotonic trend across chronologically-ordered snapshots is "
        "ordinary drift from people playing."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
