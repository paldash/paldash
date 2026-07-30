#!/usr/bin/env python3
"""
Decide the World Tree map image's orientation without needing anyone to visit it.

    python3 scripts/fit-worldtree.py

THE PROBLEM
-----------
`src/lib/map-coordinates.ts` derives the World Tree transform from the game's
own World Partition grid, so the landmass *extent* is exact. What is assumed is
the **orientation**: that the image maps world axes the same way Palpagos does.
Nothing has ever been checked against a known pixel position up there, so a flip
or a transpose would look completely normal — every marker would sit on land,
just the wrong land.

The project has said this needs a player to build something on that landmass.
That is true for *fitting* a precise transform. It is not true for *choosing
between the eight ways an image can be flipped and rotated*, which is a discrete
question and a much easier one.

THE METHOD
----------
1. Read the occupied `MainGrid_L0_X<col>_Y<row>` cells from the pak. The game
   only ships content for cells that contain something, so the occupied set *is*
   the landmass silhouette, at 25,600-world-unit resolution.
2. Build a land mask from the map texture at the same resolution.
3. Score all 8 dihedral orientations (4 rotations x optional transpose) by
   Intersection-over-Union and pick the best.

WHY THIS IS TRUSTWORTHY, OR ISN'T
---------------------------------
**The method is validated on Palpagos first.** Palpagos' transform is confirmed
independently — all 157 of its fast-travel points land on the image, and none
were used to fit it. So we already know the right answer there. If the
correlation recovers it, the same machinery applied to the World Tree means
something. If it does not, this script says so and changes nothing.

That check is the entire point. A silhouette correlation that cannot recover a
known-correct answer is not evidence about an unknown one.

LAND DETECTION
--------------
Not by colour. An earlier attempt classified "ocean blue" by hue and found 36%
of known-land pixels matched versus 58% of random ones — worse than useless.

Ocean in these textures is *flat*: low local variance over a wide area, because
it is a near-uniform gradient. Land carries terrain detail, coastlines, biome
edges. So the discriminator is local standard deviation, thresholded at the
value that best separates the two modes of its own histogram (Otsu). That needs
no assumption about what colour water is.

RESULT: THIS DOES NOT WORK (measured 2026-07-30)
------------------------------------------------
It fails its own control, and the reason is the premise rather than the tuning.

    Palpagos (known-correct orientation is rot0):
      transpose+rot90   IoU 0.335   <- wins
      rot0              IoU 0.190   <- the right answer, 6th of 8

**Occupied cells are not a coastline.** The game ships a streaming cell for
anything containing content, including open ocean with fishing spots, oil rigs
and small islands. Measured on Palpagos: the occupied set fills **51.8%** of its
bounding box while the texture's land mask covers **24.4%**. The two masks
describe different things, so their overlap is bounded low at *every*
orientation and the ranking is noise — note the winning margin of 0.015.

The occupied-cell grid is still exactly right for what it is already used for in
`map-coordinates.ts`: the landmass **extent**. It just carries no usable shape.

This script is kept because the negative result is worth not rediscovering, and
because the control check is the reusable part. Do not "fix" it by switching
metrics until the control passes — that is fitting the method to the answer.
Orientation needs a real point on that landmass.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CELL_SIZE = 25600
WORLD_TREE_X_THRESHOLD = 300_000

MAPS = {
    "palpagos": os.path.join(ROOT, "public", "maps", "palpagos.webp"),
    "worldtree": os.path.join(ROOT, "public", "maps", "worldtree.webp"),
}


def occupied_cells(pak) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    for path in pak.files:
        m = re.search(r"MainGrid_L0_X(-?\d+)_Y(-?\d+)", path)
        if m:
            out.add((int(m.group(1)), int(m.group(2))))
    return out


def split_landmasses(cells: set[tuple[int, int]]) -> dict[str, set[tuple[int, int]]]:
    """Palpagos and World Tree are far apart in world X; the gap is unambiguous."""
    threshold_col = WORLD_TREE_X_THRESHOLD // CELL_SIZE
    return {
        "palpagos": {c for c in cells if c[0] <= threshold_col},
        "worldtree": {c for c in cells if c[0] > threshold_col},
    }


def cell_mask(cells: set[tuple[int, int]]):
    """Occupied cells as a dense boolean array, plus the bounds it covers."""
    import numpy as np

    cols = [c for c, _ in cells]
    rows = [r for _, r in cells]
    c0, c1 = min(cols), max(cols)
    r0, r1 = min(rows), max(rows)
    grid = np.zeros((c1 - c0 + 1, r1 - r0 + 1), dtype=bool)
    for c, r in cells:
        grid[c - c0, r - r0] = True
    return grid, (c0, c1, r0, r1)


def land_mask(image_path: str, shape: tuple[int, int]):
    """
    Land/sea from the map texture, at the cell grid's resolution.

    Ocean is flat; land carries detail. Local standard deviation separates them
    without assuming anything about colour — see the module docstring.
    """
    import numpy as np
    from PIL import Image

    height, width = shape
    # Downsample generously first: we only need per-cell texture energy, and
    # 8192x8192 is 64 MP of work for a grid a few dozen cells across.
    with Image.open(image_path) as im:
        small = im.convert("L").resize((width * 8, height * 8), Image.Resampling.BILINEAR)
    a = np.asarray(small, dtype=np.float32)

    # Per-cell standard deviation over its 8x8 block.
    blocks = a.reshape(height, 8, width, 8).transpose(0, 2, 1, 3).reshape(height, width, 64)
    energy = blocks.std(axis=2)

    return energy, _otsu(energy)


def _otsu(values) -> float:
    """Threshold maximising between-class variance. No magic constant."""
    import numpy as np

    flat = values.ravel()
    hist, edges = np.histogram(flat, bins=64)
    centres = (edges[:-1] + edges[1:]) / 2
    total = hist.sum()
    if total == 0:
        return 0.0
    weight_bg = np.cumsum(hist)
    weight_fg = total - weight_bg
    mean_bg = np.cumsum(hist * centres) / np.maximum(weight_bg, 1)
    total_mean = (hist * centres).sum() / total
    mean_fg = (total_mean * total - np.cumsum(hist * centres)) / np.maximum(weight_fg, 1)
    between = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
    return float(centres[int(np.argmax(between))])


def orientations(grid):
    """The 8 ways a grid can be flipped and rotated, with readable names."""
    import numpy as np

    for transpose in (False, True):
        base = grid.T if transpose else grid
        for k in range(4):
            rotated = np.rot90(base, k)
            name = f"{'transpose+' if transpose else ''}rot{k * 90}"
            yield name, rotated


def iou(a, b) -> float:
    import numpy as np

    union = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / union) if union else 0.0


def evaluate(name: str, cells: set[tuple[int, int]]) -> list[tuple[str, float]]:
    import numpy as np
    from PIL import Image  # noqa: F401  (imported for the clear error if missing)

    grid, bounds = cell_mask(cells)
    print(f"\n=== {name} ===")
    print(f"  occupied cells: {len(cells)}  grid: {grid.shape}  "
          f"cols {bounds[0]}..{bounds[1]}  rows {bounds[2]}..{bounds[3]}")

    scores: list[tuple[str, float]] = []
    for orient_name, oriented in orientations(grid):
        energy, threshold = land_mask(MAPS[name], oriented.shape)
        land = energy > threshold
        scores.append((orient_name, iou(oriented, land)))

    scores.sort(key=lambda s: -s[1])
    for orient_name, score in scores:
        print(f"  {orient_name:18} IoU {score:.3f}")
    return scores


def main() -> int:
    try:
        import numpy  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError as e:
        print(f"!! needs numpy and Pillow ({e})")
        return 1

    from palpak import Pak

    pak = Pak()
    cells = occupied_cells(pak)
    masses = split_landmasses(cells)
    print(f"{len(cells)} occupied L0 cells: "
          f"{len(masses['palpagos'])} Palpagos, {len(masses['worldtree'])} World Tree")

    # Palpagos FIRST, because it is the control. Its transform is independently
    # verified (157/157 fast-travel points), so the correct answer is known and
    # the method has to recover it before its World Tree answer means anything.
    palpagos = evaluate("palpagos", masses["palpagos"])
    worldtree = evaluate("worldtree", masses["worldtree"])

    print("\n=== verdict ===")
    best_p, score_p = palpagos[0]
    margin_p = score_p - palpagos[1][1]
    print(f"  Palpagos (control): best {best_p} at IoU {score_p:.3f}, "
          f"margin over runner-up {margin_p:.3f}")

    # `rot0` is "the current transform is right". Palpagos' current transform is
    # known correct, so anything else winning means the silhouette signal is not
    # measuring what we want it to.
    if best_p != "rot0" or margin_p < 0.05:
        print("\n  METHOD FAILED ITS CONTROL.")
        print("  The correlation cannot recover Palpagos' known-correct orientation,")
        print("  so it says nothing reliable about the World Tree. Changing the")
        print("  transform on this evidence would be guessing with extra steps.")
        print("  Leave `calibrated: false` and wait for a real point on that landmass.")
        return 2

    best_w, score_w = worldtree[0]
    margin_w = score_w - worldtree[1][1]
    print(f"  World Tree:         best {best_w} at IoU {score_w:.3f}, "
          f"margin over runner-up {margin_w:.3f}")
    if margin_w < 0.05:
        print("\n  Inconclusive: the top two orientations are too close to separate.")
        return 3
    if best_w == "rot0":
        print("\n  The current orientation is the best fit. The assumption in")
        print("  map-coordinates.ts holds; the extent was already exact.")
    else:
        print(f"\n  The current orientation is WRONG — {best_w} fits better.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
