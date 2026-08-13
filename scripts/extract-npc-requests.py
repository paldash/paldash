#!/usr/bin/env python3
"""
Bundle the two NPC request chains — "show me this Pal" and "bring me this item".

    DA_PalDisplay    Area_A1_1 -> show a Carbunclo -> PalSphere x10, TreasureBoxKey01
    DA_ItemRequest   FoodNPC_A_1 -> bring BakedMeat_LazyCatfish -> TreasureBoxKey02 x5

Both are `DataAsset`s, so no DataTable sweep saw them; both were found by the
class census (`upackage.Package.export_class()`).

## The save records which are DONE, and that is not what the names suggest

The binary calls the runtime state `Local_PalDisplayNPCDataTableProgress`, and
`Local_` reads as client-side. **It is in the save**, on the player's
`RecordData`, in the same `[{key, value}]` shape as every other progression flag:

    PalDisplayNPCDataTableProgress  [{"key": "Area_F1_1", "value": true}, ...]

and the keys are exactly this asset's `RequestID`s. So the Pal-display half is a
real checklist rather than a catalogue of what exists.

**`docs/savefields.json` could not have told anyone that** — the index covers
**0 `RecordData` paths**, so the whole progression-flag region of the save is
uncatalogued. AGENTS.md's rule is "grep the index before writing a negative";
the corollary this cost is that **an index only rules something out where it
actually looked.** Checked against the real saves instead.

## The item half has no observed progress key

`Local_ItemRequestCircumCountMap` appears in the binary and on **no player in
any world examined**. So item requests ship as a catalogue with `tracked: false`
rather than as a checklist — a panel that implied completion it cannot see would
be worse than one that says it only lists what exists.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import palpak            # noqa: E402
import uassettable       # noqa: E402
import upackage          # noqa: E402
from jsonout import write_json  # noqa: E402

BASE = "../../../Pal/Content/Pal/DataAsset/NPC/"
OUT = os.path.join(os.path.dirname(HERE), "backend", "data", "npc_requests.json.gz")

SOURCES = {
    "palDisplay": {
        "asset": BASE + "DA_PalDisplay",
        "cls": "PalDisplayRequestDataAsset",
        "prop": "DisplayRequestDataMap",
        # The save key whose entries are this map's RequestIDs.
        "savedFlag": "PalDisplayNPCDataTableProgress",
    },
    "itemRequest": {
        "asset": BASE + "DA_ItemRequest",
        "cls": "PalCircumRequestDataAsset",
        "prop": "ItemRequestDataMap",
        # None observed on any player in any world — see the module docstring.
        "savedFlag": None,
    },
}

TOLERANCE = 8


class RequestError(Exception):
    """Raised when a decode does not land where it must."""


def _key(value) -> str:
    """Unwrap an `FName` cell. `str()` on `{"Key": ...}` is the shipped-a-dict trap."""
    if isinstance(value, dict):
        value = value.get("Key")
    return str(value or "")


def _rewards(entry: dict) -> list[dict]:
    out = []
    for item in entry.get("RewardItems") or []:
        if not isinstance(item, dict):
            continue
        item_id = _key(item.get("StaticItemId"))
        count = item.get("Num")
        if item_id and isinstance(count, int):
            out.append({"itemId": item_id, "count": count})
    return out


def _read(pak, spec: dict) -> dict:
    package = upackage.read(pak.read(spec["asset"] + ".uasset"))
    cls = package.export_class()
    if cls != spec["cls"]:
        raise RequestError(
            f"{spec['asset']} has export class {cls!r}, expected {spec['cls']!r}"
        )

    uexp = pak.read(spec["asset"] + ".uexp")
    body = package.exports[0].data(uexp)
    reader = uassettable._Reader(body, package.names)

    found: dict = {}
    while reader.o < len(body):
        tag = uassettable._tag(reader)
        if tag is None:
            break
        name, typ, size, extra = tag
        start = reader.o
        value = uassettable._value(reader, typ, size, extra)
        if typ != "BoolProperty":
            reader.o = start + size
        found[name] = value

    remaining = len(body) - reader.o
    if not 0 <= remaining <= TOLERANCE:
        raise RequestError(
            f"{spec['asset']}: walk ended {remaining} bytes from the end of a "
            f"{len(body)}-byte export — a refusal, not a partial result."
        )

    raw = found.get(spec["prop"])
    if not isinstance(raw, dict) or not raw:
        raise RequestError(f"{spec['asset']}: {spec['prop']} missing or not a map")
    return raw


def build(pak=None) -> tuple[dict, dict]:
    pak = pak or palpak.Pak()
    out: dict = {}
    stats: dict = {}

    display_raw = _read(pak, SOURCES["palDisplay"])
    display = {}
    for request_id, entry in sorted(display_raw.items()):
        if not isinstance(entry, dict):
            continue
        species = _key(entry.get("RequestPalID"))
        if not species:
            continue
        display[str(request_id)] = {
            "category": str(entry.get("RequestCategory") or "").split("::")[-1],
            "speciesId": species,
            "rewards": _rewards(entry),
        }

    item_raw = _read(pak, SOURCES["itemRequest"])
    items = {}
    for request_id, entry in sorted(item_raw.items()):
        if not isinstance(entry, dict):
            continue
        wanted = entry.get("RequestItem") or {}
        item_id = _key(wanted.get("StaticItemId")) if isinstance(wanted, dict) else ""
        if not item_id:
            continue
        items[str(request_id)] = {
            "category": str(entry.get("RequestCategory") or "").split("::")[-1],
            "itemId": item_id,
            "count": wanted.get("Num") if isinstance(wanted.get("Num"), int) else 1,
            "rewards": _rewards(entry),
        }

    out["palDisplay"] = {
        "requests": display,
        # The save DOES record these — see the module docstring.
        "tracked": True,
        "savedFlag": SOURCES["palDisplay"]["savedFlag"],
    }
    out["itemRequest"] = {
        "requests": items,
        # **Not tracked.** `Local_ItemRequestCircumCountMap` is named in the
        # binary and observed on no player. A catalogue, not a checklist, and it
        # must say so rather than implying completion it cannot see.
        "tracked": False,
        "savedFlag": None,
        "note": (
            "No save field for item-request progress has been observed on any "
            "player, so this lists what exists rather than what is outstanding."
        ),
    }
    stats = {"palDisplay": len(display), "itemRequest": len(items)}
    return out, stats


def main() -> int:
    try:
        data, stats = build()
    except Exception as e:  # noqa: BLE001 - report and refuse
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2

    if "--verify" in sys.argv:
        print(f"verified: {stats['palDisplay']} Pal-display requests, "
              f"{stats['itemRequest']} item requests")
        return 0

    write_json(OUT, data)
    print(f"wrote {OUT}")
    print(f"  {stats['palDisplay']} Pal-display requests (tracked in the save)")
    print(f"  {stats['itemRequest']} item requests (NOT tracked — catalogue only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
