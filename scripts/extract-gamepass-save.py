#!/usr/bin/env python3
"""
Extract a Palworld save from an Xbox Game Pass container directory.

**A script rather than a dashboard button, and that is not laziness.** A Game Pass
save lives on a Windows PC under

    %LOCALAPPDATA%\\Packages\\PocketpairInc.Palworld_ad4psfrxyesvt\\SystemAppData\\wgs

The dashboard runs in a container beside a Linux dedicated server and cannot see
that directory at all. A UI for it would be a button that can never work on the
machine the UI runs on.

    python3 scripts/extract-gamepass-save.py --wgs <path-to-wgs> --out ./extracted
    python3 scripts/extract-gamepass-save.py --wgs <path-to-wgs> --inspect

The result is an ordinary save directory: copy it into `SaveGames/0/` on the server,
or open it locally.

UNVERIFIED
----------
No Game Pass save has been run through this. The container format comes from
`PalWorldSaveTools/xgp_save_extract.py`, and the tests exercise a synthetic tree
built to the same understanding — which proves the parser matches its spec, not that
the spec is right.

It is safe to try anyway: it only reads the source, and it refuses to keep anything
unless every extracted file parses as a real Palworld save. A wrong offset produces
an error naming the file, never a directory of plausible garbage.
"""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wgs", required=True, help="the wgs directory, or a user folder inside it")
    parser.add_argument("--out", default="", help="where to write the extracted save")
    parser.add_argument("--inspect", action="store_true", help="list contents and stop")
    parser.add_argument(
        "--no-verify", action="store_true",
        help="skip the GVAS check (only useful for diagnosing a format change)",
    )
    args = parser.parse_args()

    import gamepass

    if not args.inspect and not args.out:
        parser.error("--out is required unless --inspect is given")

    try:
        report = gamepass.inspect(args.wgs)
    except gamepass.GamePassError as e:
        print(f"error: {e}")
        return 2

    print(f"package   {report['packageDisplayName']} ({report['packageName']})")
    if not report["looksLikePalworld"]:
        print("warning:  this package does not look like Palworld")
    print(f"saves     {len(report['saves'])}")
    for save in report["saves"]:
        print(f"  {save['savePath']:<48} {save['sizeBytes']:>12,} bytes")
    for problem in report["problems"]:
        print(f"  ! {problem}")

    if args.inspect:
        return 0

    try:
        result = gamepass.extract(args.wgs, args.out, verify=not args.no_verify)
    except gamepass.GamePassError as e:
        print(f"\nerror: {e}")
        return 1

    print(f"\nwrote {len(result['files'])} file(s) to {result['destination']}")
    if result["verified"]:
        print("every file parsed as a Palworld save")
    else:
        print("NOT verified — you asked for --no-verify")
    print("\nCopy the contents into your server's SaveGames/0/ directory.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
