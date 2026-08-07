#!/usr/bin/env python3
"""
Re-derive every bundled game-data file, and say which ones actually changed.

**This is the game-update runbook, and it is a script rather than a document so
it cannot drift from the thing it describes.** The commands come from
`backend/data/provenance.json`'s own `regenerateWith` fields — the same entries
that already have to exist for a bundle to be committed — so a new bundle joins
this procedure by being documented, with nobody remembering to edit a list.

## Why the output is a diff and not a log

`scripts/jsonout.py` writes with `mtime=0`, so unchanged input produces a
**byte-identical** file. That is what makes a regeneration reviewable: after a
game update, `git status` names exactly the bundles the update touched, and
everything else is provably untouched rather than assumed to be. A run that
changes nothing should change no files at all, and if it does, that is a bug in
an extractor rather than news about Palworld.

So this prints three groups — changed, unchanged, failed — and **a failure is
never silent**. An extractor that refuses (the boss spawners refuse if a
position falls off the cell grid; the settings CDO refuses if two known
constants stop matching; `build-habitats.py` refuses if any species loses its
habitat) is doing its job, and its message is the useful output.

## Order

Dependencies are declared, not inferred. `gamedata.json.gz` is built last
because `build-gamedata.py` overlays the game's own strings onto the reference
archive and reads `l10n`/`gametext`, and the icon install must precede it so
`resolve_icons()` can fix filename case against what is actually on disk.

Everything else is independent — each extractor reads the pak and writes one
bundle — so the order among them does not matter and is alphabetical for
reviewability.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
from typing import Any

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "backend", "data")
PROVENANCE = os.path.join(DATA_DIR, "provenance.json")

#: Bundles that must be built after others, and why. Anything not named here is
#: independent. Declared rather than inferred: a dependency guessed from an
#: import graph would miss `install-icons.py`, which is not imported by anything
#: and must still run first.
LAST = ("gamedata.json.gz",)

#: Steps that produce no bundle of their own but must run before `LAST`. Kept
#: empty when provenance already names them — `install-icons.py` has its own
#: entry, and adding it here as well ran it twice.
PRE_STEPS: tuple[tuple[str, str], ...] = ()


def _digest(path: str) -> str:
    if not os.path.exists(path):
        return ""
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def load_plan() -> list[tuple[str, str]]:
    with open(PROVENANCE, encoding="utf-8") as f:
        provenance = json.load(f)

    steps: list[tuple[str, str]] = []
    # ONE RUN PER COMMAND, not per bundle. `build-breedingdata.py` produces both
    # `pal_breeding.json.gz` and `pal_db.json.gz`, so keying on the bundle ran it
    # twice — wasted work, and the second run's "unchanged" verdict was about a
    # file the first run had already rewritten.
    seen: set[str] = set()
    for bundle, meta in sorted(provenance.items()):
        if not isinstance(meta, dict):
            continue
        command = meta.get("regenerateWith")
        if not command:
            # Every bundle is supposed to have one. Say so rather than skipping
            # quietly — an undocumented bundle is one nobody can rebuild.
            steps.append((bundle, ""))
            continue
        if bundle in LAST or command in seen:
            continue
        seen.add(command)
        steps.append((bundle, command))

    steps.extend(s for s in PRE_STEPS if s[1] not in seen)
    for bundle in LAST:
        meta = provenance.get(bundle) or {}
        if meta.get("regenerateWith"):
            steps.append((bundle, meta["regenerateWith"]))
    return steps


def run(command: str, python: str) -> tuple[int, str]:
    argv = shlex.split(command)
    # The provenance entries say `python3`; a checkout's venv is what actually
    # has palooz and palsav, so the interpreter is substituted rather than the
    # entries being rewritten to something machine-specific.
    if argv and argv[0] in ("python3", "python"):
        argv[0] = python
    try:
        done = subprocess.run(
            argv, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=3600
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, f"{type(e).__name__}: {e}"
    return done.returncode, (done.stdout + done.stderr).strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plan", action="store_true", help="print the steps and stop")
    ap.add_argument("--only", help="substring: run only matching steps")
    ap.add_argument(
        "--python", default=sys.executable,
        help="interpreter for the extractors (default: this one)",
    )
    args = ap.parse_args()

    steps = load_plan()
    if args.only:
        steps = [s for s in steps if args.only in s[0] or args.only in s[1]]

    if args.plan:
        print(f"{len(steps)} steps, in order:\n")
        for name, command in steps:
            print(f"  {name:34s} {command or '** NO regenerateWith **'}")
        return 0

    changed: list[str] = []
    unchanged: list[str] = []
    failed: list[tuple[str, str]] = []

    for name, command in steps:
        if not command:
            failed.append((name, "no regenerateWith in provenance.json"))
            continue
        target = os.path.join(DATA_DIR, name)
        before = _digest(target)
        print(f"  running {name} ...", flush=True)
        code, output = run(command, args.python)
        if code != 0:
            # A refusal IS the output. Extractors here refuse on a control
            # failing, a coverage regression, or a walk not landing where it
            # must, and that message is worth more than the bundle would be.
            failed.append((name, output[-800:] or f"exit {code}"))
            continue
        after = _digest(target)
        if before != after:
            changed.append(name)
        else:
            unchanged.append(name)

    print("\n" + "=" * 64)
    print(f"CHANGED   {len(changed)}")
    for name in changed:
        print(f"    {name}")
    print(f"UNCHANGED {len(unchanged)}  (byte-identical — mtime=0 makes this meaningful)")
    print(f"FAILED    {len(failed)}")
    for name, why in failed:
        print(f"\n  --- {name} ---\n{why}")

    if changed:
        print(
            "\nReview with `git diff --stat backend/data/`. A changed bundle after "
            "a game update is expected; a changed bundle WITHOUT one is a bug in "
            "an extractor, because identical input must produce identical bytes."
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
