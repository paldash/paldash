#!/usr/bin/env python3
"""
Check the bundled game data against the installed Palworld build, and diff it.

`backend/gameversion.py` answers the cheap question at runtime — does the build id
match what we shipped. This script answers the expensive one: *what actually
changed*. It re-runs the position extractors against the installed pak and
compares the result to what is bundled, object by object.

Run it after a Palworld update:

    python3 scripts/check-game-build.py                 # build ids only, instant
    python3 scripts/check-game-build.py --extract       # re-extract and diff (~minutes)
    python3 scripts/check-game-build.py --extract --write   # and update the bundles

WHY A DIFF RATHER THAN A REGENERATE
-----------------------------------
Regenerating unconditionally would hide the interesting part. A patch that adds a
landmass and a patch that nudges one rock both produce "the file changed"; only
the diff distinguishes them, and only the diff tells you whether the map
calibration constants need revisiting. The World Tree transform was derived from
the cell grid, so a patch that adds cells is a patch that may invalidate it.

The comparison is on **positions, rounded to 1 unit**, not on file bytes. Gzip
output is not reproducible across zlib versions, and object ordering within a cell
is incidental — comparing bytes would report a difference on every run.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
DATA_DIR = os.path.join(BACKEND, "data")

if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


def load_bundle(name: str) -> dict:
    path = os.path.join(DATA_DIR, name)
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"  ! could not read {name}: {e}")
        return {}


def key_set(objects: list[dict]) -> set[tuple]:
    """
    A comparable identity per object: class plus position rounded to 1 unit.

    There is no stable id on these — the pak gives an actor name that includes a
    generated `_UAID_` suffix, which is not stable across builds. Class plus
    position is, and it is also exactly what a consumer of this data cares about.
    """
    return {
        (o.get("cls", ""), round(float(o.get("x", 0))), round(float(o.get("y", 0))))
        for o in objects
    }


def diff_groups(old: dict, new: dict) -> int:
    """Print a per-category diff. Returns the number of categories that changed."""
    changed = 0
    categories = sorted(set(old.get("groups", {})) | set(new.get("groups", {})))

    for category in categories:
        before = (old.get("groups", {}).get(category) or {}).get("objects") or []
        after = (new.get("groups", {}).get(category) or {}).get("objects") or []
        old_keys, new_keys = key_set(before), key_set(after)

        added, removed = new_keys - old_keys, old_keys - new_keys
        if not added and not removed:
            print(f"  = {category:10s} {len(after):6,} unchanged")
            continue

        changed += 1
        print(f"  ~ {category:10s} {len(before):6,} -> {len(after):6,} "
              f"(+{len(added):,} / -{len(removed):,})")

        # A handful of examples, because "+4,000 ore" and "+4,000 ore all in one
        # new region" call for different responses and the coordinates say which.
        for label, sample in (("added", added), ("removed", removed)):
            for cls, x, y in sorted(sample)[:3]:
                print(f"      {label:7s} {cls} at ({x}, {y})")
            if len(sample) > 3:
                print(f"      … and {len(sample) - 3:,} more {label}")

    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extract", action="store_true",
                        help="re-run the extractors and diff (slow: walks ~9,977 cells)")
    parser.add_argument("--write", action="store_true",
                        help="with --extract, replace the bundled files")
    parser.add_argument("--pak", default=None, help="override the pak path")
    args = parser.parse_args()

    import gameversion

    print("── Installed build ─────────────────────────────")
    # `include_game=True` only here: this is a one-shot command an operator is
    # watching, so a round trip to the game server for its version string is worth
    # it. The runtime paths use the file fingerprint alone.
    signals = gameversion.detect(include_game=True)
    if not signals["installDir"]:
        print("  No game install found. Set PALWORLD_INSTALL_DIR or SAVE_BASE_DIR.")
        return 2

    print(f"  install       {signals['installDir']}")
    print(f"  build id      {signals['buildId'] or '(manifest not found)'}")
    print(f"  pak stamp     {signals['pakStamp'] or '(pak not found)'}")
    if signals["gameVersion"]:
        print(f"  game version  {signals['gameVersion']}")

    print("\n── Bundled data ────────────────────────────────")
    provenance = gameversion.provenance()
    stale = []
    for name, entry in sorted(provenance.items()):
        if not isinstance(entry, dict):
            continue
        built = entry.get("gameBuild")
        if not built:
            state = "unknown origin"
        elif str(built) == signals["buildId"]:
            state = "current"
        else:
            state = f"STALE (built from {built})"
            stale.append(name)
        print(f"  {name:24s} {state}")
        if entry.get("regenerateWith"):
            print(f"  {'':24s}   {entry['regenerateWith']}")

    if not args.extract:
        print()
        if stale:
            print(f"  {len(stale)} artifact(s) stale. Re-run with --extract to see "
                  f"what actually moved.")
        else:
            print("  Nothing detectably stale. --extract diffs anyway, which is the "
                  "only way to be sure.")
        return 1 if stale else 0

    # ── Re-extract and diff ──────────────────────────────
    print("\n── Re-extracting world objects ─────────────────")
    pak = args.pak or gameversion.pak_path()
    if not pak:
        print("  No pak found; cannot extract.")
        return 2

    bundled_preview = load_bundle("worldobjects.json.gz")
    # THE TARGET LIST MUST COME FROM THE BUNDLE, NOT A LITERAL.
    #
    # This was hardcoded to "ore,treasure,fishing,oilrig" while the bundle
    # carries THIRTEEN categories. The diff therefore re-extracted four, found
    # the other nine missing, and reported 18,377 real world objects as
    # "removed" — every Pal spawner, every NPC, every skill fruit — with `ore`
    # and `treasure` sitting unchanged beside them. It then offered `--write`,
    # which would have destroyed all nine.
    #
    # Categories going to EXACTLY zero while their neighbours are byte-identical
    # is the tell: a game patch does not delete a category and leave the rest
    # untouched. That is this repo's own rule — a count that disagrees with what
    # the game plainly has is wrong however plausible it reads — and the
    # dangerous part was that it failed loudly in the direction of "act on me".
    targets = sorted((bundled_preview.get("groups") or {}).keys())
    if not targets:
        print("  ! the bundle names no categories; cannot diff safely")
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        fresh_path = os.path.join(tmp, "worldobjects.json")
        command = [
            sys.executable, os.path.join(ROOT, "scripts", "extract-world-objects.py"),
            "--pak", pak, "--out", fresh_path,
            "--targets", ",".join(targets),
        ]
        print(f"  {len(targets)} categories, taken from the bundle: "
              f"{', '.join(targets)}")
        print(f"  $ {' '.join(command[1:])}")
        result = subprocess.run(command, cwd=ROOT)
        if result.returncode != 0 or not os.path.exists(fresh_path):
            print("  ! extraction failed")
            return 2

        with open(fresh_path) as f:
            fresh = json.load(f)

        print("\n── Diff against the bundle ─────────────────────")
        bundled = load_bundle("worldobjects.json.gz")
        changed = diff_groups(bundled, fresh)

        old_total = sum(len((g or {}).get("objects") or [])
                        for g in bundled.get("groups", {}).values())
        new_total = sum(len((g or {}).get("objects") or [])
                        for g in fresh.get("groups", {}).values())
        print(f"\n  total {old_total:,} -> {new_total:,}")

        # Cell count is its own signal: a change here means the world's *extent*
        # moved, which is what the map transforms are fitted against.
        old_cells = bundled.get("cellsParsed", 0)
        new_cells = fresh.get("cellsParsed", 0)
        if old_cells != new_cells:
            print(f"  ! cells parsed {old_cells:,} -> {new_cells:,}")
            print("    The streaming grid changed. Re-check src/lib/map-coordinates.ts —")
            print("    the World Tree extent was derived from this grid.")

        # ── The guard that should have existed ────────────
        #
        # A category emptying completely is not a thing a content patch does,
        # and it IS what every mis-invocation of this script looks like. So it
        # refuses the write rather than reporting it, because the failure mode
        # here is not a wrong number on a screen — it is a bundle overwritten
        # with less than it had, from a source that cannot be recovered without
        # the original pak.
        emptied = [
            name for name, group in (bundled_preview.get("groups") or {}).items()
            if (group or {}).get("objects")
            and not ((fresh.get("groups") or {}).get(name) or {}).get("objects")
        ]
        if emptied:
            print(f"\n  ! {len(emptied)} categor(y/ies) came back EMPTY: "
                  f"{', '.join(emptied)}")
            print("    A patch does not delete a whole category and leave its")
            print("    neighbours byte-identical. Check the extraction before")
            print("    believing this — the pak still containing the assets is")
            print("    one `palpak` listing away.")
            if args.write:
                print("    REFUSING to write.")
                return 2

        if args.write and changed:
            out = os.path.join(DATA_DIR, "worldobjects.json.gz")
            with gzip.open(out, "wt", encoding="utf-8", compresslevel=9) as f:
                json.dump(fresh, f, separators=(",", ":"))
            print(f"\n  written {out} ({os.path.getsize(out):,} bytes)")
            print("  Now update backend/data/provenance.json:")
            print(f'    "worldobjects.json.gz": {{"gameBuild": "{signals["buildId"]}"}}')
            print("  And re-run the bundled-shape test, which pins the counts:")
            print("    .venv/bin/python -m pytest backend/tests/test_worldobjects.py")
        elif changed:
            print("\n  Re-run with --write to update the bundle.")

    print("\n── Also worth re-checking by hand ──────────────")
    print("  effigies.json.gz      python3 scripts/extract-effigies.py")
    print("  gamedata.json.gz      needs a newer PalWorldSaveTools release in refs/")
    print("  settings key list     refs/palworld/DefaultPalWorldSettings.ini")
    print("                        (119 settings; compare against settings_ini.py)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
