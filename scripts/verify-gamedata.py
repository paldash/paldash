#!/usr/bin/env python3
"""
Check the bundled catalogue against the game's own tables — the first thing that
has ever verified `gamedata.json.gz`.

WHAT PHASE 1.10 SET OUT TO DO, AND WHY IT CANNOT BE DONE
--------------------------------------------------------
`docs/PLAN.md` proposed regenerating `gamedata.json.gz` from the server pak
instead of `refs/PalWorldSaveTools-main.zip`, dropping a third-party attribution
and — the real prize — giving the bundle a real `gameBuild` so staleness
detection works. It is the only bundle with `gameBuild: null`.

**The server pak cannot supply display names.** Every name and description lives
in an `FText`, and `uassettable` does not decode `TextProperty`. Measured, and it
is total rather than partial:

    DT_ItemNameText          1994 rows, 1994 opaque
    DT_PalNameText            322 rows,  322 opaque
    DT_TechnologyNameText     835 rows,  835 opaque
    DT_ItemDescriptionText   1924 rows, 1924 opaque

**THE PAK READER NOW DECODES FText**, and the names it yields are Japanese.
`uassettable._text` reads all 1,994 item names and 322 Pal names — as
`メルパカ`, `シラヌイ` and so on, because **Japanese is Palworld's source
language**. English is a translation and is not in the source strings.

Nor is it in `.locres`: all 17 of
`Pal/Content/Localization/Game/<lang>/Game.locres` are **37-byte placeholders**
with zero entries. Palworld does not ship its translations that way.

So English display names remain the one thing only the reference archive
supplies, and the swap (#69) is still blocked. The blocker is now precisely
located rather than vague: if English exists in the paks at all it is in the
client pak's `Pal/Content/L10N/en/` asset overrides. See #34.

WHAT IS ACHIEVABLE, AND IT IS MOST OF THE VALUE
------------------------------------------------
Every *numeric* field can be checked. That validates the catalogue the whole
dashboard rests on against the game itself, which nothing has ever done — and a
clean result lets `provenance.json` record the build the data was **verified
against**, which is what `gameversion` actually needs. It is not the same as a
build the data was generated from, and it is not presented as one.

Fields compared, chosen because a wrong value is silently consequential:

    items  maxStack   the sorter's merge ceiling
           weight     inventory capacity maths
           price      the shop and economy views
           rarity     display and filtering
           sortId     the category sort order players recognise
    pals   rarity
           zukanIndex the Paldeck ordering, including negative indices

Usage:  python3 scripts/verify-gamedata.py [--details]
"""

from __future__ import annotations

import gzip
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import palpak            # noqa: E402
import uassettable       # noqa: E402

ROOT = os.path.dirname(HERE)
BUNDLE = os.path.join(ROOT, "backend", "data", "gamedata.json.gz")

# bundle key -> pak column, per section.
ITEM_FIELDS = {
    "maxStack": "MaxStackCount",
    "weight": "Weight",
    "price": "Price",
    "rarity": "Rarity",
    "sortId": "SortID",
}
PAL_FIELDS = {
    "rarity": "Rarity",
    "zukanIndex": "ZukanIndex",
}


def _read(pak, name: str) -> dict:
    path = next((p for p in pak.files if p.endswith(name + ".uasset")), None)
    if path is None:
        raise SystemExit(f"{name} is not in this pak")
    return uassettable.read_table(pak, path)


def _lower(table: dict) -> dict:
    """Case-insensitive index — the archive and the pak disagree on eight ids."""
    return {str(k).lower(): v for k, v in table.items()}


def compare(section: dict, pak_rows: dict, fields: dict, label: str,
            details: bool) -> dict:
    pak = _lower(pak_rows)
    checked = Counter()
    mismatched: list = []
    missing = []

    for item_id, entry in section.items():
        row = pak.get(str(item_id).lower())
        if row is None:
            missing.append(item_id)
            continue
        for key, column in fields.items():
            if key not in entry or column not in row:
                continue
            ours, theirs = entry[key], row[column]
            try:
                same = abs(float(ours) - float(theirs)) < 0.001
            except (TypeError, ValueError):
                same = str(ours) == str(theirs)
            checked[key] += 1
            if not same:
                mismatched.append((item_id, key, ours, theirs))

    total = sum(checked.values())
    print(f"\n{label}: {len(section)} bundled, {len(pak_rows)} in the pak")
    print(f"  {total} field values compared across {len(fields)} fields")
    print(f"  matching   {total - len(mismatched)}")
    print(f"  DIFFERENT  {len(mismatched)}")
    print(f"  not in the pak at all: {len(missing)}")

    if mismatched and details:
        by_field = Counter(m[1] for m in mismatched)
        print(f"  by field: {dict(by_field)}")
        for item_id, key, ours, theirs in mismatched[:15]:
            print(f"    {item_id:34s} {key:10s} bundle={ours!r} pak={theirs!r}")
    if missing and details:
        print(f"  missing e.g. {missing[:8]}")

    return {"compared": total, "mismatched": mismatched, "missing": missing}


def main() -> int:
    details = "--details" in sys.argv

    with gzip.open(BUNDLE, "rt", encoding="utf-8") as f:
        bundle = json.load(f)

    pak = palpak.Pak()
    items = compare(
        bundle["items"], _read(pak, "DT_ItemDataTable"), ITEM_FIELDS, "items", details
    )
    pals = compare(
        bundle["pals"], _read(pak, "DT_PalMonsterParameter"), PAL_FIELDS, "pals", details
    )

    total_bad = len(items["mismatched"]) + len(pals["mismatched"])
    compared = items["compared"] + pals["compared"]

    print(f"\n{'=' * 60}")
    print(f"{compared - total_bad} of {compared} values agree with the game.")
    if total_bad:
        print(
            f"{total_bad} disagree. That is a finding about one of the two "
            "sources, not necessarily about the archive — read them before "
            "changing anything."
        )
        return 1

    print(
        "The bundled catalogue matches the game on every numeric field checked.\n"
        "\n"
        "Names are not checkable here. FText now decodes, but its source\n"
        "strings are JAPANESE — that is Palworld's source language — and all 17\n"
        ".locres archives are empty 37-byte placeholders. English remains the\n"
        "one thing only the reference archive supplies. See tasks #34 and #69."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
