#!/usr/bin/env python3
"""
Extract the game's icon set from the reference archive into public/icons/.

    python3 scripts/install-icons.py            # install the default categories
    python3 scripts/install-icons.py --all      # everything the archive ships
    python3 scripts/install-icons.py --list     # what is available, and how big

Like `install-map-assets.py`, `refs/` is gitignored but the extracted files ARE
committed, so a clone and the Docker image both work with no archive present.

WHY ONLY SOME CATEGORIES
------------------------
The archive ships 2,485 icons totalling 15.0 MB. Committing all of it would more
than triple the repo's binary weight to serve views that do not exist:

    items         917   6.29 MB   the Items tab, container contents, imports
    structures    534   3.34 MB   the map draws its own markers; no icon slot
    technologies  460   3.10 MB   nothing renders a tech tree, only point totals
    pals          301   0.95 MB   roster, breeding, the Pal editor
    npcs          155   0.43 MB   merchants and guards in the character list
    ui             59   0.06 MB   PalworldSaveTools' own chrome
    elements       36   0.03 MB   type badges on Pals
    game           12   0.12 MB   ditto
    passives        6   0.00 MB   only 6 of 1,905 — not worth a lookup path
    app             4   0.66 MB   someone else's application branding

So the default is the four that something actually displays. `--all` is there
because "we shipped what the UI needed in 2026" is a statement that expires, and
re-running with a flag is cheaper than re-deriving this reasoning later.

NAMING: DON'T
-------------
Filenames are preserved exactly as the archive ships them, because
**`gamedata.json.gz` already records each icon's path**:

    AIcore  -> icon: "/icons/items/T_itemicon_Material_AIcore.webp"
    Alpaca  -> icon: "/icons/pals/T_Alpaca_icon_normal.webp"

`describe_item()` and `describe_pal()` already return that field, so installing
into `public/icons/<category>/<original name>` makes every existing path resolve
with no mapping table, no manifest and no lookup code.

A first version renamed files to the ids the API speaks
(`T_Alpaca_icon_normal.webp` -> `pals/Alpaca.webp`) and shipped a lowercased
manifest to resolve them. It scored **0 of 2,466** items, because item icons are
named after their *texture* (`T_itemicon_Material_AIcore`) and no amount of
prefix-stripping turns that into `AIcore`. The data had the answer the whole
time; deriving one was inventing a second source of truth that disagreed with
the first.

Source: PalworldSaveTools resources/icons (MIT, (c) 2026 Pylar).
The underlying artwork is Pocketpair's — see docs/LICENSING.md before
distributing this publicly.
"""

from __future__ import annotations

import argparse
import os
import sys
import zipfile
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE = os.path.join(ROOT, "refs", "PalWorldSaveTools-main.zip")
OUT_DIR = os.path.join(ROOT, "public", "icons")

# Categories installed unless --all is passed. See the module docstring.
DEFAULT_CATEGORIES = ("pals", "items", "elements", "npcs")

def scan(archive: zipfile.ZipFile) -> dict[str, list[zipfile.ZipInfo]]:
    by_category: dict[str, list[zipfile.ZipInfo]] = defaultdict(list)
    for info in archive.infolist():
        if "/icons/" not in info.filename or info.is_dir():
            continue
        if not info.filename.lower().endswith((".webp", ".png")):
            continue
        parts = info.filename.split("/icons/")[1].split("/")
        if len(parts) < 2:
            continue
        by_category[parts[0]].append(info)
    return by_category


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true",
                        help="install every category, not just the displayed ones")
    parser.add_argument("--list", action="store_true",
                        help="show categories and sizes, install nothing")
    parser.add_argument("--categories", default="",
                        help="comma-separated category names")
    args = parser.parse_args()

    if not os.path.exists(ARCHIVE):
        print(f"!! {ARCHIVE} not found.")
        print("   Download PalworldSaveTools-main.zip from")
        print("   https://github.com/deafdudecomputers/PalworldSaveTools into refs/")
        return 1

    archive = zipfile.ZipFile(ARCHIVE)
    by_category = scan(archive)

    if args.list:
        total_files = total_bytes = 0
        for name in sorted(by_category, key=lambda c: -sum(i.file_size for i in by_category[c])):
            entries = by_category[name]
            size = sum(i.file_size for i in entries)
            mark = "*" if name in DEFAULT_CATEGORIES else " "
            print(f" {mark} {name:14} {len(entries):5} files  {size / 1024 / 1024:6.2f} MB")
            total_files += len(entries)
            total_bytes += size
        print(f"   {'TOTAL':14} {total_files:5} files  {total_bytes / 1024 / 1024:6.2f} MB")
        print("\n * = installed by default")
        return 0

    if args.categories:
        wanted = [c.strip() for c in args.categories.split(",") if c.strip()]
    elif args.all:
        wanted = sorted(by_category)
    else:
        wanted = list(DEFAULT_CATEGORIES)

    unknown = [c for c in wanted if c not in by_category]
    if unknown:
        print(f"!! Unknown categor{'y' if len(unknown) == 1 else 'ies'}: {', '.join(unknown)}")
        print(f"   Available: {', '.join(sorted(by_category))}")
        return 1

    installed = 0
    total_bytes = 0

    for category in wanted:
        target_dir = os.path.join(OUT_DIR, category)
        os.makedirs(target_dir, exist_ok=True)
        count = 0

        for info in sorted(by_category[category], key=lambda i: i.filename):
            # The archive's own filename, unchanged — that is what the `icon`
            # field in gamedata.json.gz points at.
            out_name = os.path.basename(info.filename)
            with archive.open(info) as src:
                data = src.read()
            with open(os.path.join(target_dir, out_name), "wb") as dst:
                dst.write(data)
            count += 1
            total_bytes += len(data)

        installed += count
        print(f"  {category:12} {count:5} icons", file=sys.stderr)

    print(f"\ninstalled {installed} icons ({total_bytes / 1024 / 1024:.2f} MB) into {OUT_DIR}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
