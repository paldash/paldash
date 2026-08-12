#!/usr/bin/env python3
"""
Bundle `DA_PalBuildObjectCapabilityData` — what a STRUCTURE contributes.

`workrank.py` reads the Pal's half of base output: a work rank indexes
`CraftSpeeds`, so a rank-10 miner is worth 1,000 against a rank-3's 100. The
structure's half was never read at all, and it is a bigger spread than most of
what this dashboard already shows:

    BlastFurnace   1.0      BlastFurnace2  1.5      BlastFurnace3  3.0
    BlastFurnace4  4.5      AncientBlastFurnace     11.0

An operator with a tier-1 furnace is running at **1/11th** of what the same Pals
would produce at the Ancient one, and nothing here said so.

## Found by class, not by name

This asset is a `PalBuildObjectCapabilityDataAsset`. It is not a `DT_`, so no
DataTable sweep ever saw it, and the prefix-based asset census excluded it too —
the fourth instance of that failure. `upackage.Package.export_class()` now
enumerates by what an asset *is*, which is how this surfaced.

## WHAT IT DOES NOT SAY

**It does not say what a container accepts.** The class name reads like it
might. There is no Feed Box row and no item filter anywhere in it, so
`basesupply.py`'s refusal — "Pal food must be in a Feed Box" is stated in no game
file — is untouched.

**And it does not say how a structure's rate composes with a Pal's work rank.**
Two numbers from two files, with nothing stating whether they multiply, add, or
gate each other. `composesWithWorkRank: false` travels in the bundle for the same
reason `stackingKnown: false` does in `buildplanner` and `palresist`: the client
is the thing about to render a combined figure, so it is the thing that has to be
told there is no stated rule.
"""

from __future__ import annotations

import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import palpak            # noqa: E402
import uassettable       # noqa: E402
import upackage          # noqa: E402
from jsonout import write_json  # noqa: E402

ASSET = ("../../../Pal/Content/Pal/DataAsset/MapObject/CapabilityData/"
         "DA_PalBuildObjectCapabilityData")
OUT = os.path.join(os.path.dirname(HERE), "backend", "data",
                   "build_capabilities.json.gz")

EXPECTED_CLASS = "PalBuildObjectCapabilityDataAsset"

# The walk must end within this of the export end. `BP_PalGameSetting` and every
# DataAsset read here leave the same four-byte tail.
TOLERANCE = 8

# Sanity anchors. These are read off the asset itself, so they are a regression
# signal rather than an independent source — but a decode that has drifted does
# not reproduce a 1.0/1.5/3.0/4.5 ladder in the right rows.
EXPECTED = {
    ("BlastFurnace", "WorkSpeedAdditionalRate"): 1.0,
    ("BlastFurnace4", "WorkSpeedAdditionalRate"): 4.5,
    ("AncientBlastFurnace", "WorkSpeedAdditionalRate"): 11.0,
    ("ManualElectricGenerator", "GenerateEnergyRateByWorker"): 0.2,
}


class CapabilityError(Exception):
    """Raised when the decode does not land where it must."""


def extract(pak=None) -> tuple[dict, dict]:
    pak = pak or palpak.Pak()
    package = upackage.read(pak.read(ASSET + ".uasset"))

    # Enumerate by CLASS. A renamed or moved asset should fail loudly here
    # rather than silently produce an empty bundle.
    cls = package.export_class()
    if cls != EXPECTED_CLASS:
        raise CapabilityError(
            f"{ASSET} has export class {cls!r}, expected {EXPECTED_CLASS!r}"
        )

    uexp = pak.read(ASSET + ".uexp")
    export = package.exports[0]
    body = export.data(uexp)
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
        raise CapabilityError(
            f"walk ended {remaining} bytes from the end of a {len(body)}-byte "
            "export. A refusal, not a partial result — a drifted tagged reader "
            "produces plausible numbers in the wrong rows."
        )

    raw = found.get("BuildObjectCapabilityMap")
    if not isinstance(raw, dict) or not raw:
        raise CapabilityError("BuildObjectCapabilityMap missing or not a map")

    structures: dict[str, dict] = {}
    for structure, entry in sorted(raw.items()):
        caps = (entry or {}).get("Capabilities") if isinstance(entry, dict) else None
        if not isinstance(caps, dict):
            continue
        clean = {k: v for k, v in sorted(caps.items())
                 if isinstance(v, (int, float)) and not isinstance(v, bool)}
        if clean:
            structures[str(structure)] = clean

    kinds = collections.Counter(k for c in structures.values() for k in c)
    return (
        {
            "structures": structures,
            # NOT a claim this bundle can support. See the module docstring.
            "composesWithWorkRank": False,
            "note": (
                "What a STRUCTURE contributes. The Pal's half is its work rank "
                "indexing CraftSpeeds (workrank.py). No game file states how "
                "the two combine, so they must be shown separately."
            ),
        },
        {"structures": len(structures), "kinds": dict(kinds),
         "bytes": len(body), "endedAt": reader.o},
    )


def verify(structures: dict) -> list[str]:
    """Mismatches against the anchors. Empty is good."""
    bad = []
    for (structure, cap), want in EXPECTED.items():
        got = (structures.get(structure) or {}).get(cap)
        if got != want:
            bad.append(f"{structure}.{cap}: expected {want!r}, decoded {got!r}")
    return bad


def main() -> int:
    try:
        data, stats = extract()
    except (CapabilityError, Exception) as e:  # noqa: BLE001 - report and refuse
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2

    problems = verify(data["structures"])
    if problems:
        for p in problems:
            print(f"MISMATCH: {p}", file=sys.stderr)
        return 3

    if "--verify" in sys.argv:
        print(f"verified: {stats['structures']} structures, walk ended at "
              f"{stats['endedAt']} of {stats['bytes']} bytes, all anchors match")
        return 0

    write_json(OUT, data)
    print(f"wrote {OUT}")
    print(f"  {stats['structures']} structures, walk ended at "
          f"{stats['endedAt']}/{stats['bytes']} bytes")
    for kind, n in sorted(stats["kinds"].items(), key=lambda kv: -kv[1]):
        print(f"    {kind:32s} {n:4d}")
    print("  composesWithWorkRank is FALSE: no file states how a structure's "
          "rate combines with a Pal's work rank, so they travel separately")
    print("  and this asset says NOTHING about what a container accepts — "
          "basesupply.py's Feed Box refusal is unaffected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
