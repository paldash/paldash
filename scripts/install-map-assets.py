#!/usr/bin/env python3
"""
Extract the Palworld map textures from the reference archive into public/.

`refs/` is gitignored (66 MB of third-party zips) but the two images this pulls
out ARE committed, so a clone builds and runs with a working map and the Docker
image needs no archive.

    python3 scripts/install-map-assets.py

Source: PalworldSaveTools resources/assets/maps (MIT, (c) 2026 Pylar).
Underlying artwork is Pocketpair's.

Both textures are 8192x8192. The 4096-unit Leaflet coordinate space is kept as
-is and Leaflet scales the image, so the fitted transform in
src/lib/map-coordinates.ts stays valid unchanged.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE = os.path.join(ROOT, "refs", "PalWorldSaveTools-main.zip")
OUT_DIR = os.path.join(ROOT, "public", "maps")

# archive filename -> installed filename.
#
# "Feybreak" was the wrong name for the second landmass: 1.0's second map is the
# World Tree region, and the game ships it as T_TreeMap.
WANTED = {
    "T_WorldMap.webp": "palpagos.webp",
    "T_TreeMap.webp": "worldtree.webp",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", default=ARCHIVE)
    parser.add_argument("--out", default=OUT_DIR)
    args = parser.parse_args()
    archive_path, out_dir = args.archive, args.out
    if not os.path.exists(archive_path):
        print(f"!! {archive_path} not found.")
        print("   Download PalworldSaveTools-main.zip from")
        print("   https://github.com/deafdudecomputers/PalworldSaveTools into refs/")
        return 1

    os.makedirs(out_dir, exist_ok=True)
    installed = 0

    with zipfile.ZipFile(archive_path) as zf:
        names = zf.namelist()
        for source, target in WANTED.items():
            matches = [n for n in names if n.endswith(f"assets/maps/{source}")]
            if not matches:
                print(f"!! {source} not found in the archive — skipping")
                continue

            destination = os.path.join(out_dir, target)
            with zf.open(matches[0]) as src, open(destination, "wb") as dst:
                shutil.copyfileobj(src, dst)

            size = os.path.getsize(destination)
            print(f"  {source:20s} -> public/maps/{target:16s} {size / 1024 / 1024:.1f} MB")
            installed += 1

    if not installed:
        print("!! Nothing installed.")
        return 1

    print(f"\nInstalled {installed} map image(s) into public/maps/.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
