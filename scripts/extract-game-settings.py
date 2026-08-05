#!/usr/bin/env python3
"""
Bundle `BP_PalGameSetting` — the game's own tuning constants, 347 of them.

WHAT THIS UNLOCKS THAT NOTHING ELSE DID. Until now the only decodable things in
the pak were DataTables. This reads a **Blueprint's class-default object**, which
turns out to be tagged the same way in the server pak — so every balance constant
Pocketpair exposes as a UPROPERTY is readable: damage rates, sanity and hunger
thresholds, capture rates, base-camp ranges, breeding timings.

It is the answer to "surely that number is in the files somewhere". It usually is.

HOW THE CDO IS FOUND
--------------------
The export named `Default__<Class>_C`. Not by index and not by size — a
`Default__` prefix is UE's own convention for a class-default object, and picking
"the biggest export" would silently choose a large function body after a content
update.

THE ACCEPTANCE CRITERION IS THE SAME ONE `uassettable` USES: the property walk
must terminate at the end of the export's bytes. A tagged reader that has drifted
does not land there — it runs off the end or stops early. Measured here: **41,416
of 41,420 bytes**, the remainder being the terminator, and 5 of 352 properties
whose *values* are types this reader does not decode.

Those 5 are **skipped by the size in their own tag**, which is why an unknown
type costs one property rather than the rest of the file. That is what the size
field is for, and it is the difference between this and `read_table`, which
refuses outright — there, a bad offset means everything after it is garbage; here
each property is self-describing and independently placed.

WHY IT VERIFIES ITSELF
----------------------
Two constants this project already held, from sources that could not be checked
against the install, come out of the decode exactly:

    CharacterMaxLevel = 80    <- editschema.MAX_LEVEL, documented as
                                 "a community-sourced figure, not one read from
                                 the game files" and "cannot be verified"
    CharacterMaxRank  = 5     <- editschema.MAX_RANK

A misaligned walk does not produce two independently-known values in the right
places. `--verify` asserts them and is the check to run after a game update.

Usage:  python3 scripts/extract-game-settings.py [--verify]
Output: backend/data/game_settings.json.gz
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import palpak            # noqa: E402
import upackage          # noqa: E402
import uassettable       # noqa: E402
from jsonout import write_json  # noqa: E402

ASSET = "../../../Pal/Content/Pal/Blueprint/System/BP_PalGameSetting"
OUT = os.path.join(os.path.dirname(HERE), "backend", "data", "game_settings.json.gz")

# Constants this project already held from unverifiable sources. If the decode is
# aligned, these come out right; if it is not, they will not.
EXPECTED = {
    "CharacterMaxLevel": 80,
    "CharacterMaxRank": 5,
}

# How close to the end of the export the walk must land. The terminator itself
# accounts for the remainder; anything larger means the reader lost its place.
TOLERANCE = 8


class SettingsError(Exception):
    """Raised when the walk does not end where it must."""


def extract(pak=None) -> tuple[dict, dict]:
    """`(settings, stats)`, or raise if the walk does not terminate correctly."""
    pak = pak or palpak.Pak()
    package = upackage.read(pak.read(ASSET + ".uasset"))
    uexp = pak.read(ASSET + ".uexp")

    cdo = next(
        (e for e in package.exports if e.name.startswith("Default__")), None
    )
    if cdo is None:
        raise SettingsError(
            "no Default__ export — the class-default object is how every value "
            "here is reached, and picking the biggest export instead would "
            "silently choose a function body after a content update."
        )

    body = cdo.data(uexp)
    reader = uassettable._Reader(body, package.names)
    settings: dict = {}
    unread: list[str] = []

    while reader.o < len(body):
        try:
            tag = uassettable._tag(reader)
        except Exception as e:  # noqa: BLE001 - report position, do not guess
            raise SettingsError(
                f"tag read failed at byte {reader.o} of {len(body)}: {e}"
            ) from e
        if tag is None:
            break

        name, typ, size, extra = tag
        start = reader.o
        try:
            value = uassettable._value(reader, typ, size, extra)
        except Exception:  # noqa: BLE001 - one unreadable type is not a failure
            value = None
            unread.append(f"{name} ({typ})")
        # A BoolProperty's value lives in its tag and occupies no value bytes, so
        # snapping past `size` would skip the following property.
        if typ != "BoolProperty":
            reader.o = start + size

        settings[name] = _plain(value)

    remaining = len(body) - reader.o
    if remaining > TOLERANCE or remaining < 0:
        raise SettingsError(
            f"walk ended {remaining} bytes from the end of a {len(body)}-byte "
            "export. This is a refusal, not a partial result — a tagged reader "
            "that has drifted produces plausible numbers in the wrong places."
        )

    return settings, {
        "properties": len(settings),
        "unread": unread,
        "bytes": len(body),
        "endedAt": reader.o,
    }


def _plain(value):
    """JSON-safe, and non-scalars are recorded as their repr rather than dropped."""
    if value is None or isinstance(value, (int, float, str, bool)):
        return value
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    return str(value)


def verify(settings: dict) -> list[str]:
    """Mismatches against constants this project already held. Empty is good."""
    return [
        f"{key}: expected {want!r}, decoded {settings.get(key)!r}"
        for key, want in EXPECTED.items()
        if settings.get(key) != want
    ]


def main() -> int:
    try:
        settings, stats = extract()
    except Exception as e:  # noqa: BLE001
        print(f"Extraction failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    problems = verify(settings)
    if problems:
        print("VERIFICATION FAILED — not writing:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        print(
            "\nTwo constants that are independently known should fall out of an "
            "aligned decode. They did not, so the walk is suspect and the output "
            "would be plausible numbers in the wrong places.",
            file=sys.stderr,
        )
        return 2

    if "--verify" in sys.argv:
        print(f"verified: {len(EXPECTED)} known constants match")
        print(f"  {stats['properties']} properties, "
              f"ended at {stats['endedAt']} of {stats['bytes']} bytes")
        return 0

    write_json(OUT, settings)
    print(f"wrote {OUT}")
    print(f"  {stats['properties']} properties, "
          f"ended at {stats['endedAt']} of {stats['bytes']} bytes")
    print(f"  verified against {len(EXPECTED)} independently-known constants")
    if stats["unread"]:
        print(f"  {len(stats['unread'])} values of undecoded types, "
              f"recorded as null: {', '.join(stats['unread'][:4])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
