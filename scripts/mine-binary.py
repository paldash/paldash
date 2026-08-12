#!/usr/bin/env python3
"""
Index the dedicated server BINARY's reflection symbols.

`mine-datatables.py` did this for the pak and `mine-savefields.py` for the save.
This is the third surface, and it went unexamined for the whole project — which
cost more than it should have, because it is the surface that says what the
*native C++* knows.

## Why a stripped binary still says so much

`PalServer-Linux-Shipping` is stripped of debug symbols, but Unreal's reflection
system needs every `UCLASS`, `USTRUCT`, `UENUM`, `UPROPERTY` and `UFUNCTION`
name as a runtime string so that Blueprints can bind to them by name. Those live
in `.rodata` and no strip removes them. The C++ ABI also emits `_ZTV…` vtable
symbols for polymorphic types, which recovers class names independently.

So: **names yes, values no.** A `UPROPERTY`'s default is assigned in compiled
constructor code, not stored as data, so this index can say a constant EXISTS
and cannot say what it equals. That is the same shape as `upackage.py`'s
"structure yes, properties no" and should be treated with the same discipline.

## THE FINDING THAT MOTIVATED IT

`BP_PalGameSetting`'s CDO gives 347 tuning constants and this project treated
that as the complete list. It is **the overridden subset**. A cooked Blueprint
CDO serialises only what differs from its native parent's defaults, so any
constant Pocketpair left alone is absent from the pak entirely — checked, and
**0 hits across all 76,972 server-pak packages** for the ones below.

The binary names at least 54 more in the same families, including:

    Combi_MutationRate              the base mutation rate AGENTS.md records as
                                    "stated in no file" — the name exists
    StatusCalculate_Talent_PerAdd   the IV coefficient palstats transcribes from
    StatusCalculate_TribePlus_HP    a community formula
    StatusCalculate_ConstPlus_Defense
    FriendshipPoint_Max

**"It is not in the settings CDO" therefore does not mean "the game does not
have it."** That is the same error as "a DataTable sweep is not a search of the
game", which this project has now made three times on three different surfaces.

Usage:
    python3 scripts/mine-binary.py                 # summary
    python3 scripts/mine-binary.py --json out.json # full index
    python3 scripts/mine-binary.py --grep Suitab   # search
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict

BINARY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "refs", "palworld", "Pal", "Binaries", "Linux", "PalServer-Linux-Shipping",
)

# An Itanium-mangled vtable symbol: _ZTV<len><name>. The length prefix is what
# makes the name recoverable without a demangler, and what stops a greedy regex
# swallowing the next symbol.
VTABLE = re.compile(r"_ZTV(\d+)([A-Za-z_][A-Za-z0-9_]*)")
ENUM_VALUE = re.compile(r"^(E[A-Za-z0-9_]+)::([A-Za-z0-9_]+)$")
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SOURCE = re.compile(r"^[A-Z]:/works/[A-Za-z0-9_./-]+\.(?:cpp|h)$")


def read_strings(path: str, minlen: int = 4) -> list[str]:
    """`strings` is ~1.5s on a 196 MB binary, so there is no reason to cache."""
    out = subprocess.run(
        ["strings", "-n", str(minlen), path],
        capture_output=True, text=True, errors="ignore", check=True,
    )
    return out.stdout.splitlines()


def build_index(lines: list[str]) -> dict:
    classes: set[str] = set()
    enums: dict[str, set[str]] = defaultdict(set)
    idents: set[str] = set()
    sources: set[str] = set()

    for raw in lines:
        s = raw.strip()
        if not s:
            continue

        for m in VTABLE.finditer(s):
            length, name = int(m.group(1)), m.group(2)
            # The length prefix is the check: a name that does not match its own
            # declared length means the regex ran past the symbol boundary, and
            # a truncated class name is worse than none.
            if len(name) >= length:
                classes.add(name[:length])

        m = ENUM_VALUE.match(s)
        if m:
            enums[m.group(1)].add(m.group(2))
            continue

        if SOURCE.match(s):
            sources.add(s)
            continue

        if IDENT.match(s) and len(s) >= 4:
            idents.add(s)

    # Mangled C++ symbols are a different surface from reflection names: they
    # name internal functions Blueprints can never bind to, and the `_ZTV` ones
    # are already recovered above as types. Counted rather than listed, so the
    # exclusion is visible instead of silent.
    mangled = {i for i in idents if i.startswith("_Z")}
    idents -= mangled

    def kind(name: str) -> str:
        for prefix, label in (("UPal", "class"), ("APal", "actor"),
                              ("FPal", "struct"), ("EPal", "enum")):
            if name.startswith(prefix):
                return label
        return "other"

    pal_classes = sorted(c for c in classes if c[1:].startswith("Pal"))
    return {
        "binary": os.path.basename(BINARY),
        "counts": {
            "strings": len(lines),
            "identifiers": len(idents),
            "mangledSymbolsExcluded": len(mangled),
            "vtableTypes": len(classes),
            "palTypes": len(pal_classes),
            "enums": len(enums),
            "enumValues": sum(len(v) for v in enums.values()),
            "sourceFiles": len(sources),
        },
        "palTypes": {k: sorted(c for c in pal_classes if kind(c) == k)
                     for k in ("class", "actor", "struct", "enum", "other")},
        "enums": {k: sorted(v) for k, v in sorted(enums.items())},
        "sourceFiles": sorted(sources),
        "identifiers": sorted(idents),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", default=BINARY)
    ap.add_argument("--json", help="write the full index here")
    ap.add_argument("--grep", help="case-insensitive search across every name")
    args = ap.parse_args()

    if not os.path.exists(args.binary):
        print(f"Not found: {args.binary}", file=sys.stderr)
        print("This needs refs/palworld/ — a dedicated server install.", file=sys.stderr)
        return 2

    index = build_index(read_strings(args.binary))
    c = index["counts"]
    print(f"{index['binary']}")
    print(f"  strings          {c['strings']:>8,}")
    print(f"  identifiers      {c['identifiers']:>8,}")
    print(f"  vtable types     {c['vtableTypes']:>8,}   ({c['palTypes']:,} Pal-specific)")
    print(f"  enums            {c['enums']:>8,}   ({c['enumValues']:,} values)")
    print(f"  source files     {c['sourceFiles']:>8,}")

    if args.grep:
        needle = args.grep.lower()
        hits = [i for i in index["identifiers"] if needle in i.lower()]
        enum_hits = [f"{e}::{v}" for e, vs in index["enums"].items()
                     for v in vs if needle in e.lower() or needle in v.lower()]
        type_hits = [t for group in index["palTypes"].values()
                     for t in group if needle in t.lower()]
        print(f"\n{len(hits)} identifiers, {len(type_hits)} types, "
              f"{len(enum_hits)} enum values matching {args.grep!r}\n")
        for label, items in (("types", type_hits), ("identifiers", hits),
                             ("enum values", enum_hits)):
            if items:
                print(f"  --- {label} ---")
                for i in items[:120]:
                    print(f"    {i}")
                if len(items) > 120:
                    print(f"    ... +{len(items) - 120} more")

    if args.json:
        with open(args.json, "w") as f:
            json.dump(index, f, indent=1, sort_keys=True)
        size = os.path.getsize(args.json) / 1024
        print(f"\nWrote {args.json} ({size:.0f} KB)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
