#!/usr/bin/env python3
"""
`BaseCampSaveData[].ModuleMap` — what its nine blobs actually contain.

A diagnostic, in the genre of `verify-figures.py` and `diff-dynamic-items.py`:
it re-derives the findings below across any number of worlds so they can be
re-checked rather than trusted. **Nothing in `backend/` calls this**, and that is
deliberate — see "Why nothing is wired up" at the bottom.

    python3 scripts/decode-basecamp-modules.py refworld/Level.sav [more...]

## Task #88 asked about "46 constant bytes on every base". It is one module.

The sweep that raised this reported `ModuleMap[].value.RawData` as 46 bytes,
constant, on every base in three worlds. Measured per module type, that is
`PassiveEffect` alone:

| Module | Width |
|---|---|
| `PassiveEffect` | **46 bytes, always** |
| `TransportItemDirector` | 8 bytes, or 82-159 when non-empty |
| Energy, Medical, ResourceCollector, ItemStorages, FacilityReservation, ObjectMaintenance, ItemStackInfo | **0 bytes — empty on every base** |

Seven of the nine carry nothing at all, which is worth knowing before anyone
spends time on them. `ItemStorages` being empty was already recorded in
AGENTS.md; the other six were not.

## `PassiveEffect` IS A CONSTANT — the measured negative

**One distinct value across 24 bases in two unrelated worlds.** Byte-identical,
every time. It decomposes into serialisation scaffolding rather than data:

    00  01 00 00 00     1
    04  02              -
    05  05 00 00 00     5
    09  01 00 00 00     1
    13  00              -
    14  18 00 00 00     24  <- the length of what follows, and it is exactly 24
    18  01 00 00 00     one CustomVersionData entry
    22  380b00de-4949-d7ce-97df-2d99c0c1c369
    38  01 00 00 00     version 1
    42  00 00 00 00

The version GUID is the same one embedded in `WorkAssignMap`'s own
`CustomVersionData`, which is the check that this reading is right rather than
merely tidy. **A blob that never varies carries no information**, so base level
is not in here and neither is anything else per-base. That is the answer to #88
as asked, and it is a negative.

## `TransportItemDirector` IS NOT, and it decodes

The neighbour nobody asked about. Layout, with the entry count first:

    int32   entry_count
    repeat:
      int32   unknown_a          always 1 in five observations
      int32   name_length        includes the trailing NUL
      char[]  item_id            null-terminated ASCII
      byte[32] zeros             must be zero, or the decode is refused
      int32   unknown_b          2, 4 or 5 observed
      double  x, y, z            a world position
    int32   trailing             0

**The size arithmetic is the first check and it is exact.** An entry is
`4 + 4 + strlen + 32 + 4 + 24`, and the three observed blobs come to 82, 159 and
156 bytes — matching 1, 2 and 2 entries respectively, with the walk consuming
the buffer to the byte every time. A wrong layout does not land on the end of
three differently-sized buffers.

**The second check is the one that makes it evidence.** Every decoded position
lands *inside its own base*:

| World | Item | Distance from its base |
|---|---|---:|
| refworld | `Wheat` | 1,947 |
| snapshot | `Coal` | 1,622 |
| snapshot | `CopperOre` | 1,407 |
| snapshot | `Stone` | 1,172 |
| snapshot | `Stone` | 950 |

`BaseCampAreaRange` is **3,500** (`BP_PalGameSetting`), so all five are well
within the base radius — and the base position comes from a completely different
field than the bytes being decoded. Five for five, with every item id resolving
in the catalogue.

## WHAT IT MEANS IS NOT CLAIMED

An (item, position) pair inside a base, under a module called
`TransportItemDirector`, reads like a haul order. **That is a guess from a name**
— the mistake this project records for `BP_LevelObject_TowerLockBarrier`, for
`DenyRecipeChain` and for `DamageRateIfDefender_*`, whose name says the opposite
of what it does. `unknown_a` and `unknown_b` are named for what is known about
them, which is nothing.

It is also **rare**: 3 of 53 bases across four worlds have a non-empty one, and
the live 16-base world has none. Whatever creates it is not something every base
does.

## Why nothing is wired up

A verified layout is not a feature. Shipping this into the parse would put an
(item, position) pair on the Bases tab with no honest caption — and the parse
already pays 0.30s for `WorkSaveData`, which answers a question somebody asked.

What would justify wiring it in is knowing what the game does with it, and the
way to find that is to watch one appear: run this against a base before and
after using whatever base logistics feature is suspected, and see what changes.
The decoder is here so that experiment costs minutes instead of a day.
"""

from __future__ import annotations

import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

# The 32 bytes between the item id and the position are zero on every
# observation. They are asserted rather than skipped: if they ever carry
# something, this must stop and say so instead of silently misreading the
# position that follows.
PAD_BYTES = 32


def decode_transport(data: bytes) -> list[dict] | None:
    """
    `TransportItemDirector`, or None if the buffer does not decode exactly.

    **Refuses rather than returns partial.** The acceptance criterion is the one
    `uassettable` uses everywhere: the walk must consume the buffer exactly. A
    decoder that returns what it managed is how a wrong layout becomes a
    confident wrong answer.
    """
    if len(data) < 8:
        return None
    try:
        offset = 0
        (count,) = struct.unpack_from("<i", data, offset)
        offset += 4
        if not 0 < count < 64:
            return None

        entries = []
        for _ in range(count):
            (unknown_a,) = struct.unpack_from("<i", data, offset)
            offset += 4
            (length,) = struct.unpack_from("<i", data, offset)
            offset += 4
            if not 0 < length < 128:
                return None
            item = data[offset:offset + length].split(b"\0")[0].decode("ascii")
            offset += length
            if any(data[offset:offset + PAD_BYTES]):
                return None
            offset += PAD_BYTES
            (unknown_b,) = struct.unpack_from("<i", data, offset)
            offset += 4
            position = struct.unpack_from("<ddd", data, offset)
            offset += 24
            entries.append({
                "item": item,
                "position": position,
                "unknownA": unknown_a,
                "unknownB": unknown_b,
            })
    except (struct.error, UnicodeDecodeError, IndexError):
        return None

    # Exactly one trailing int32 and nothing else.
    if offset + 4 != len(data):
        return None
    return entries


def main(paths: list[str]) -> int:
    import math

    import gamedata
    import parser as pparser

    if not paths:
        print(__doc__.strip().splitlines()[0])
        print("usage: decode-basecamp-modules.py <Level.sav> [Level.sav ...]")
        return 2

    widths: dict[str, set[int]] = {}
    passive_blobs: set[bytes] = set()
    bases_seen = 0
    transport_rows = 0
    inside = 0

    for path in paths:
        if not os.path.exists(path):
            print(f"!! {path} not found")
            continue
        gvas = pparser.load_gvas(path)
        if gvas is None:
            print(f"!! {path} did not parse")
            continue
        world = pparser._world_save_data(gvas)
        print(f"\n=== {path}")

        for base in pparser._v(world, "BaseCampSaveData", "value", default=[]):
            bases_seen += 1
            raw = pparser._v(base, "value", "RawData", "value") or {}
            base_id = str(raw.get("id") or "")
            transform = raw.get("transform") or {}
            origin = transform.get("translation") or {} if isinstance(transform, dict) else {}

            for entry in pparser._v(base, "value", "ModuleMap", "value", default=[]):
                kind = str(entry.get("key")).rsplit("::", 1)[-1]
                values = pparser._v(entry, "value", "RawData", "value", "values")
                blob = bytes(values) if values else b""
                widths.setdefault(kind, set()).add(len(blob))

                if kind == "PassiveEffect":
                    passive_blobs.add(blob)
                if kind != "TransportItemDirector" or len(blob) <= 8:
                    continue

                decoded = decode_transport(blob)
                if decoded is None:
                    print(f"  {base_id[:8]}  TransportItemDirector  DECODE REFUSED "
                          f"({len(blob)} bytes)")
                    continue
                for row in decoded:
                    transport_rows += 1
                    x, y, _z = row["position"]
                    name = gamedata.item_name(row["item"])
                    if origin.get("x") is not None:
                        distance = math.hypot(x - origin["x"], y - origin["y"])
                        # 3500 is BaseCampAreaRange from BP_PalGameSetting.
                        within = distance <= 3500
                        inside += 1 if within else 0
                        mark = "inside" if within else "OUTSIDE"
                        print(f"  {base_id[:8]}  {row['item']:12s} -> {name:16s} "
                              f"a={row['unknownA']} b={row['unknownB']}  "
                              f"{distance:7,.0f} from base  {mark}")
                    else:
                        print(f"  {base_id[:8]}  {row['item']:12s} -> {name:16s} "
                              f"(base position unavailable)")

    print(f"\n--- {bases_seen} bases across {len(paths)} world(s)")
    print("module widths:")
    for kind in sorted(widths):
        print(f"   {kind:24s} {sorted(widths[kind])}")
    print(f"\nPassiveEffect: {len(passive_blobs)} distinct blob(s) — "
          f"{'CONSTANT, carries nothing' if len(passive_blobs) == 1 else 'VARIES, worth reading'}")
    print(f"TransportItemDirector: {transport_rows} entries, "
          f"{inside} inside their own base's {3500} radius")
    # The join is the finding. If a position ever lands outside the base, the
    # layout has moved and the reading is no longer supported.
    return 0 if transport_rows == inside else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
