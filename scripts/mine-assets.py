#!/usr/bin/env python3
"""
Catalogue every data-bearing asset in the server pak that is NOT a DataTable.

`mine-datatables.py` indexes `DT_*.uasset` — 935 of the pak's **66,969**
assets — and this project has twice been surprised by something sitting in the
other 66,034. `BP_PalGameSetting` (347 tuning constants, including the
`WorkSuitabilityMaxRank` that overturned a documented negative) is a Blueprint.
`DA_BreedingItemEffectData` (what each cake does to a bred egg) is a DataAsset.
Neither is a DT_, so neither was in the index, and both were found only because
somebody went looking for one specific thing.

**That is the failure this script exists to end.** The index answers "does a
table exist that knows X". This answers the same question for everything else,
so "I could not find it" stops being evidence that it is not there.

## What it does and does not open

The pak is mostly art: 16,011 textures, 8,086 animation sequences, 7,682
material instances, 5,955 static meshes. Those are excluded by prefix, and the
exclusion is **listed in the output** rather than silent, so the next person can
see what was skipped and disagree.

What is swept:

    BP_    7,643   Blueprints — the CDO carries designer-set UPROPERTYs
    PA_      326   parameter assets
    DA_       16   DataAssets
    ST_        7

## The acceptance criterion is the one every reader here uses

A decode counts only if the tagged property walk **terminates at the end of the
export** (within `TOLERANCE`, the 4-byte tail `BP_PalGameSetting` also leaves).
Anything else is recorded as a refusal with its error. A partial decode of
tagged properties reads as real data, so "this exists and we cannot read it" is
a different and more useful statement than silence — the same reason
`mine-datatables.py` lists its 32 refusals rather than omitting them.

**No values from the pak are printed in bulk.** The index records property
*names*, types and counts, plus a short sample for orientation. It is a schema
index, not a copy of Pocketpair's data.
"""

from __future__ import annotations

import argparse
import collections
import os
import re
import sys
import time
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import upackage        # noqa: E402
import uassettable     # noqa: E402
from palpak import Pak  # noqa: E402
from jsonout import write_json  # noqa: E402

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_JSON = os.path.join(PROJECT_ROOT, "docs", "assets.json")
OUT_MD = os.path.join(PROJECT_ROOT, "docs", "ASSETS.md")

#: Prefixes swept. Everything else is art or engine plumbing and is reported as
#: skipped rather than quietly dropped.
DATA_PREFIXES = ("BP_", "PA_", "DA_", "ST_")

#: Prefixes known to be art/engine, listed so the skip is reviewable.
ART_PREFIXES = {
    "T_": "textures", "AS_": "animation sequences", "MI_": "material instances",
    "AM_": "animation montages", "SM_": "static meshes", "AKE_": "audio events",
    "NS_": "Niagara systems", "SK_": "skeletal meshes", "M_": "materials",
    "ABP_": "animation blueprints", "BS_": "blend spaces", "MF_": "material functions",
    "FABP_": "animation blueprints", "TX_": "textures", "LS_": "level sequences",
    "MTL_": "materials", "FT_": "font", "AO_": "audio", "C_": "curves",
    "S_": "sounds", "WBP_": "widget blueprints (unversioned — see AGENTS.md)",
}

#: The same 4-byte tail `extract-game-settings.py` tolerates on the settings CDO.
TOLERANCE = 8

#: Properties whose names suggest real tuning data rather than art wiring. Used
#: only to RANK the output — nothing is filtered on it, because a filter here
#: would recreate the blind spot this script exists to remove.
INTERESTING = re.compile(
    r"rate|amount|num|count|time|speed|level|rank|max|min|probability|"
    r"threshold|cost|value|percent|range|distance|damage|exp|price",
    re.I,
)


def sweep(pak: Pak, limit: int = 0) -> dict[str, Any]:
    assets = [p for p in pak.files if p.endswith(".uasset")]
    targets = [p for p in assets if os.path.basename(p).startswith(DATA_PREFIXES)]
    if limit:
        targets = targets[:limit]

    decoded: list[dict] = []
    refused: list[dict] = []
    started = time.time()

    for path in targets:
        name = os.path.basename(path)[:-7]
        try:
            package = upackage.read(pak.read(path))
            uexp = pak.read(path.replace(".uasset", ".uexp"))
        except Exception as e:  # noqa: BLE001 - a missing pair is a refusal, not a crash
            refused.append({"asset": name, "path": path, "error": f"{type(e).__name__}: {e}"})
            continue

        # The CDO is found by its `Default__` prefix, never by size — picking the
        # biggest export works today and silently chooses a function body after
        # an update. Assets without one fall back to the first export.
        export = next((e for e in package.exports if e.name.startswith("Default__")), None)
        kind = "cdo"
        if export is None:
            export = package.exports[0] if package.exports else None
            kind = "export"
        if export is None:
            refused.append({"asset": name, "path": path, "error": "no exports"})
            continue

        try:
            body = export.data(uexp)
            reader = uassettable._Reader(body, package.names)
            props = uassettable._properties(reader)
        except Exception as e:  # noqa: BLE001
            refused.append({"asset": name, "path": path, "error": f"{type(e).__name__}: {e}"})
            continue

        remaining = len(body) - reader.o
        if remaining < 0 or remaining > TOLERANCE:
            # NOT recorded as a partial success. A tagged walk that did not land
            # on the end of the export is drifted, and its properties are
            # plausible values in the wrong places.
            refused.append({
                "asset": name, "path": path,
                "error": f"walk ended {remaining} bytes from the end of {len(body)}",
            })
            continue

        if not props:
            continue  # An empty CDO is real and uninteresting; not a refusal.

        opaque = [k for k, v in props.items() if isinstance(v, str) and v.startswith("<")]
        decoded.append({
            "asset": name,
            "path": path,
            "export": export.name,
            "kind": kind,
            "properties": len(props),
            "names": sorted(props),
            "opaque": sorted(opaque),
            "interesting": sorted(k for k in props if INTERESTING.search(k)),
            "bytes": len(body),
        })

    skipped = collections.Counter()
    for path in assets:
        base = os.path.basename(path)
        if base.startswith(DATA_PREFIXES):
            continue
        prefix = next((p for p in ART_PREFIXES if base.startswith(p)), "(other)")
        skipped[prefix] += 1

    return {
        "gameBuild": None,
        "totalAssets": len(assets),
        "swept": len(targets),
        "decoded": sorted(decoded, key=lambda d: (-d["properties"], d["asset"])),
        "refused": sorted(refused, key=lambda d: d["asset"]),
        "skippedByPrefix": [
            {"prefix": p, "count": n, "what": ART_PREFIXES.get(p, "unclassified")}
            for p, n in skipped.most_common()
        ],
        "seconds": round(time.time() - started, 1),
    }


def markdown(index: dict[str, Any]) -> str:
    decoded = index["decoded"]
    rich = [d for d in decoded if d["properties"] >= 10]
    lines = [
        "# Non-DataTable assets in the server pak",
        "",
        "Generated by `scripts/mine-assets.py`. **This is a schema index, not "
        "data** — property names and counts, never a copy of Pocketpair's values.",
        "",
        f"- **{index['totalAssets']:,}** `.uasset` files in the pak",
        f"- **{index['swept']:,}** swept (`BP_`, `PA_`, `DA_`, `ST_`)",
        f"- **{len(decoded):,}** decoded with at least one property",
        f"- **{len(rich):,}** carry 10 or more — the ones worth reading",
        f"- **{len(index['refused']):,}** refused, listed below with their errors",
        "",
        "A decode counts only if the tagged walk terminates at the end of the "
        "export. A partial decode is recorded as a refusal, because plausible "
        "values in the wrong places are worse than a gap.",
        "",
        "## The richest assets",
        "",
        "| Asset | Properties | Opaque | Kind |",
        "|---|---:|---:|---|",
    ]
    for d in decoded[:60]:
        lines.append(
            f"| `{d['asset']}` | {d['properties']} | {len(d['opaque'])} | {d['kind']} |"
        )
    lines += [
        "",
        "## Skipped by prefix",
        "",
        "Listed rather than silently dropped, so the exclusion can be argued with.",
        "",
        "| Prefix | Count | What |",
        "|---|---:|---|",
    ]
    for row in index["skippedByPrefix"]:
        lines.append(f"| `{row['prefix']}` | {row['count']:,} | {row['what']} |")

    if index["refused"]:
        lines += ["", "## Refusals", "", "| Asset | Error |", "|---|---|"]
        for r in index["refused"][:80]:
            lines.append(f"| `{r['asset']}` | {r['error']} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pak", default=None)
    ap.add_argument("--limit", type=int, default=0, help="sweep only the first N")
    ap.add_argument("--grep", help="print assets whose property names match this")
    args = ap.parse_args()

    pak = Pak(args.pak) if args.pak else Pak()
    index = sweep(pak, args.limit)

    if args.grep:
        pattern = re.compile(args.grep, re.I)
        for d in index["decoded"]:
            hits = [n for n in d["names"] if pattern.search(n)]
            if hits:
                print(f"{d['asset']}  ({d['properties']} props)")
                for h in hits:
                    print(f"    {h}")
        return 0

    write_json(OUT_JSON, index)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(markdown(index))
    print(f"wrote {OUT_JSON} and {OUT_MD}")
    print(f"  {index['swept']:,} swept, {len(index['decoded']):,} decoded, "
          f"{len(index['refused']):,} refused, in {index['seconds']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
