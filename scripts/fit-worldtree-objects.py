#!/usr/bin/env python3
"""
Pin the World Tree map orientation from where things actually are in a save.

    python3 scripts/fit-worldtree-objects.py /path/to/world

WHY THIS EXISTS, AND WHY IT IS NOT `fit-worldtree.py` RETUNED
------------------------------------------------------------
`src/lib/map-coordinates.ts` derives the World Tree transform from the game's
World Partition grid, so the landmass **extent** is exact. What has never been
checked is the **orientation** — whether the image maps the world axes the way
Palpagos does. A flip or a transpose would look entirely normal: every marker
would sit on land, just the wrong land. That is why the region ships
`calibrated: false` and says so in the UI.

`fit-worldtree.py` tried to settle it from the pak alone and is a recorded
**negative** result. It fails its own control — Palpagos' known-correct
orientation ranks 6th of 8 — and the reason is the premise, not the tuning:

    **Occupied streaming cells are not a coastline.** The game ships a cell for
    anything containing content, open ocean with fishing spots and oil rigs
    included. Measured on Palpagos the occupied set fills 51.8% of its bounding
    box while the texture's land mask covers 24.4% — two masks describing
    different things, so their overlap is bounded low at every orientation.

This script changes the **input**, not the metric, and that is the whole
difference. Chests, drops and placed objects sit on *land*. A player cannot open
a chest in the middle of the ocean. So a scatter of real object positions is a
land signal in a way the cell grid never was, and the same land mask that could
not discriminate silhouettes can discriminate these.

THE CONTROL IS NOT OPTIONAL
---------------------------
Palpagos' transform is independently confirmed — all 157 of its fast-travel
points land on the image and none were used to fit it — so the right answer
there is already known. This runs the identical procedure on Palpagos objects
first, and **refuses to report a World Tree answer unless the control puts
Palpagos' true orientation first with a clear margin.** Reporting a number from a
method that cannot recover a known answer is how `fit-worldtree.py` would have
produced a confident wrong transform.

WHAT COUNTS AS ENOUGH
---------------------
Orientation is a discrete choice between 8, so it needs far less than a precise
fit: a few dozen well-spread points settle it. A precise fit — replacing the four
constants and setting `calibrated: true` — needs points near opposite corners,
and this prints the spread so you can see whether you have them.

READ-ONLY. It opens a world and prints. It cannot write.
"""

from __future__ import annotations

import argparse
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, HERE)

# Matches `WORLD_TREE_X_THRESHOLD` in src/lib/map-coordinates.ts.
WORLD_TREE_X_THRESHOLD = 300000

MAPS = {
    "Palpagos": os.path.join(ROOT, "public", "maps", "palpagos.webp"),
    "World Tree": os.path.join(ROOT, "public", "maps", "worldtree.webp"),
}

# Palpagos' orientation is the one the confirmed transform implements, and is
# what the control has to recover. See `worldTreeFromCellGrid` for the axis
# convention this names.
CONTROL_ANSWER = "identity"

MAP_SIZE = 4096

# Each region's world extent, as the thing that maps onto the whole image.
#
# **Points are projected through these, never normalised to their own bounding
# box.** The first version of this script gridded points over the box they happen
# to occupy, which silently rescales the sample to fill the image — a world where
# nobody has visited the north coast gets stretched north, and the resulting
# shape is not the one the texture shows. It ranked Palpagos' known-correct
# orientation first but by 0.020, which is inside noise. The extent is known for
# both regions, so there is no reason to infer one from the sample.
#
# Palpagos: inverted from its confirmed transform in `map-coordinates.ts`, so the
# identity orientation here IS that transform and the control tests exactly the
# thing being assumed.
# World Tree: `WORLD_TREE_CELL_BOUNDS`, measured from the pak's streaming cells.
EXTENTS = {
    "Palpagos": {
        "y1": -2045.4249901028509 / 0.0028463649168173903,
        "y2": (MAP_SIZE - 2045.4249901028509) / 0.0028463649168173903,
        "x2": -987.5352466783819 / -0.0028275391990127056,
        "x1": (MAP_SIZE - 987.5352466783819) / -0.0028275391990127056,
    },
    "World Tree": {"x1": 332800, "x2": 691200, "y1": -793600, "y2": -486400},
}

# How much the winner must beat the runner-up by for the control to count as
# passed. `fit-worldtree.py`'s winner took it by 0.015, which is noise; anything
# that cannot clear a real margin is not evidence.
MIN_MARGIN = 0.05

# The refinement control resamples Palpagos down to the World Tree's sample
# size, several times, because a single subsample is itself a coin flip.
CONTROL_TRIALS = 8
CONTROL_SEED = 7



def world_positions(world_dir: str) -> dict[str, list[tuple[float, float]]]:
    """
    Every world position a save gives up, split by landmass.

    Map objects are the useful population: chests someone opened, items dropped,
    anything built. Base camps and player last-positions come along because they
    cost nothing and a small world may have little else.
    """
    from parser import extract_base_camps, extract_map_objects, load_gvas

    level = os.path.join(world_dir, "Level.sav")
    if not os.path.exists(level):
        raise SystemExit(f"No Level.sav in {world_dir}")

    gvas = load_gvas(level)
    if gvas is None:
        raise SystemExit(f"Could not parse {level}")

    points: list[tuple[float, float]] = []
    for obj in extract_map_objects(gvas):
        points.append((float(obj.get("x") or 0.0), float(obj.get("y") or 0.0)))
    for base in extract_base_camps(gvas):
        points.append((float(base.get("x") or 0.0), float(base.get("y") or 0.0)))

    split: dict[str, list[tuple[float, float]]] = {"Palpagos": [], "World Tree": []}
    for x, y in points:
        if x == 0.0 and y == 0.0:
            continue  # an unset transform, not the origin of the world
        split["World Tree" if x > WORLD_TREE_X_THRESHOLD else "Palpagos"].append((x, y))
    return split


def unit_coords(name: str, points):
    """
    Each point as `(u, v)` in the region's own extent, `identity` convention.

    `u` is image X from world Y, `v` is image Y from world X negated — the axis
    convention `worldTreeFromCellGrid` implements and the thing under test.
    """
    import numpy as np

    e = EXTENTS[name]
    xs = np.array([p[0] for p in points], dtype=np.float64)
    ys = np.array([p[1] for p in points], dtype=np.float64)
    u = (ys - e["y1"]) / (e["y2"] - e["y1"])
    v = (e["x2"] - xs) / (e["x2"] - e["x1"])
    return u, v


def _orientations(u, v):
    """
    The 8 ways a unit square maps onto an image, as operations on `(u, v)`.

    Applied to the *coordinates* rather than to a rasterised grid, so no
    resolution is lost and a sparse sample stays exactly where the transform puts
    it. `identity` is the convention currently shipped.
    """
    for transpose, (a, b) in ((False, (u, v)), (True, (v, u))):
        for k, (p, q) in enumerate((
            (a, b), (b, 1 - a), (1 - a, 1 - b), (1 - b, a),
        )):
            prefix = "transpose+" if transpose else ""
            yield (f"{prefix}rot{k * 90}" if (transpose or k) else "identity"), p, q


def refine(name: str, points, land) -> tuple[tuple[float, float, float, float], float]:
    """
    Search shifts and scales around the declared extent for a better land fit.

    This answers a different question from `evaluate`, and a harder one.
    Orientation is a choice between 8 and a coarse signal settles it; *precision*
    asks whether the derived extent is the right one, which needs the metric to
    localise rather than merely rank.

    **Whether it can localise is measured, not assumed** — that is what running
    this on Palpagos is for. Palpagos' transform is independently correct, so any
    distance the optimiser travels from it is this method's noise floor, and a
    World Tree correction smaller than that floor is not a correction.

    Returns `((du, dv, su, sv), score)` — shift and scale in unit-square terms.
    """
    import numpy as np

    u, v = unit_coords(name, points)
    height, width = land.shape

    def score(du: float, dv: float, su: float, sv: float) -> float:
        p = (u - 0.5) * su + 0.5 + du
        q = (v - 0.5) * sv + 0.5 + dv
        col = np.clip((p * width).astype(int), 0, width - 1)
        row = np.clip((q * height).astype(int), 0, height - 1)
        return float(land[row, col].mean())

    steps = np.linspace(-0.12, 0.12, 13)
    scales = np.linspace(0.88, 1.12, 13)

    best = (0.0, 0.0, 1.0, 1.0)
    best_score = score(*best)
    for du in steps:
        for dv in steps:
            for su in scales:
                for sv in scales:
                    s = score(du, dv, su, sv)
                    if s > best_score:
                        best_score, best = s, (float(du), float(dv), float(su), float(sv))
    return best, best_score


def evaluate(name: str, points) -> list[tuple[str, float]]:
    """Score all 8 orientations by what fraction of the sample lands on land."""
    import numpy as np

    u, v = unit_coords(name, points)
    inside = (u >= 0) & (u <= 1) & (v >= 0) & (v <= 1)

    print(f"\n=== {name} ===")
    print(f"  {len(points):,} positions, {inside.sum():,} inside the declared extent")
    print(f"  spread: u {u.min():.2f}..{u.max():.2f}   v {v.min():.2f}..{v.max():.2f}")

    land = _land_mask(MAPS[name])
    height, width = land.shape

    scores: list[tuple[str, float]] = []
    for orient_name, p, q in _orientations(u, v):
        col = np.clip((p * width).astype(int), 0, width - 1)
        row = np.clip((q * height).astype(int), 0, height - 1)
        hits = land[row, col]
        scores.append((orient_name, float(hits.mean()) if hits.size else 0.0))

    scores.sort(key=lambda s: -s[1])
    for orient_name, score in scores:
        mark = "  <- current convention" if orient_name == CONTROL_ANSWER else ""
        print(f"  {orient_name:18} on land {score:6.1%}{mark}")
    return scores


def _land_mask(image_path: str, cells: int = 256):
    """
    A boolean land mask for the whole texture, at `cells` per side.

    Ocean is flat; land carries terrain detail, so local standard deviation
    separates them without assuming what colour water is. Same discriminator
    `fit-worldtree.py` documents — it was never the part that failed there.

    Built once per image and at a fixed resolution, because the sample no longer
    dictates the grid: points are looked up in it rather than rasterised into it.
    """
    import numpy as np
    from PIL import Image

    with Image.open(image_path) as im:
        small = im.convert("L").resize((cells * 8, cells * 8), Image.Resampling.BILINEAR)
    a = np.asarray(small, dtype=np.float32)
    blocks = a.reshape(cells, 8, cells, 8).transpose(0, 2, 1, 3).reshape(cells, cells, 64)
    energy = blocks.std(axis=2)
    return energy > _otsu(energy)


def _otsu(values) -> float:
    import numpy as np

    hist, edges = np.histogram(values.ravel(), bins=64)
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("world", help="a world directory containing Level.sav")
    ap.add_argument("--refine", action="store_true",
                    help="also test whether the derived extent is precise, not just oriented")
    ap.add_argument("--min-points", type=int, default=20,
                    help="refuse to judge the World Tree below this many (default 20)")
    args = ap.parse_args()

    split = world_positions(args.world)
    for name, points in split.items():
        print(f"{name}: {len(points):,} positions")

    # The control runs FIRST, and runs even when there is no World Tree data.
    #
    # It answers "does this method work at all", which is a fact about the
    # method rather than about the world being examined — so a save with nothing
    # up there still tells you whether a save that *does* have something would be
    # worth collecting. Checking the point count first would have thrown that
    # away on exactly the worlds where it is cheapest to learn.
    control = evaluate("Palpagos", split["Palpagos"])
    if not control:
        return 1

    winner, best = control[0]
    runner_up = control[1][1] if len(control) > 1 else 0.0
    margin = best - runner_up

    print(f"\nCONTROL: winner {winner} by {margin:.3f} "
          f"(needs {CONTROL_ANSWER} by >= {MIN_MARGIN})")

    if winner != CONTROL_ANSWER or margin < MIN_MARGIN:
        print(
            "\nCONTROL FAILED — no World Tree answer is reported.\n"
            "The method could not recover an orientation that is already known to "
            "be right, so anything it said about the unknown one would be noise. "
            "This is the same wall `fit-worldtree.py` hit. Do NOT respond by "
            "changing the metric until the control passes: that is fitting the "
            "method to the answer."
        )
        return 2

    print("Control passed — the method recovers a known-correct orientation.")

    if len(split["World Tree"]) < args.min_points:
        print(
            f"\nBut only {len(split['World Tree'])} World Tree positions, below the "
            f"{args.min_points} this will judge on. Nothing to apply it to yet.\n"
            "Opening chests, dropping items and placing anything all count; walking "
            "around does not, because the game persists objects rather than "
            "footprints. Come back with a save from a world where someone has."
        )
        return 1

    result = evaluate("World Tree", split["World Tree"])
    winner, best = result[0]
    margin = best - (result[1][1] if len(result) > 1 else 0.0)

    print(f"\nWORLD TREE: {winner}, by {margin:.3f}")
    if margin < MIN_MARGIN:
        print(
            "  ...but that margin is inside the noise the control was measured "
            "against, so treat it as unresolved rather than as the answer."
        )
        return 3
    if winner != CONTROL_ANSWER:
        print("  DIFFERENT from Palpagos. `worldTreeFromCellGrid` assumes the "
              "Palpagos convention, so its four constants need rewriting before "
              "`calibrated` can be set.")
        return 0

    print("  Same convention as Palpagos — the assumption in "
          "`worldTreeFromCellGrid` holds.")

    if not args.refine:
        print("  Pass --refine to ask the harder question: whether the derived "
              "extent is also precise enough to set `calibrated: true`.")
        return 0

    # ORIENTATION IS SETTLED; PRECISION IS A SEPARATE CLAIM.
    #
    # The control is not "does Palpagos win" any more — it already did. It is
    # "how far does the optimiser drag a transform that is already right", which
    # is this metric's noise floor. A World Tree correction smaller than that
    # floor is not a correction, and reporting one would be the exact mistake
    # `fit-worldtree.py` was stopped from making.
    print("\n=== Refinement (is the derived extent precise?) ===")

    # THE CONTROL MUST USE THE SAME NUMBER OF POINTS, and getting this wrong
    # nearly shipped a bogus correction.
    #
    # The first version refined Palpagos on all 7,201 of its positions and found
    # it did not move at all — a noise floor of 0.000, against which the World
    # Tree's movement of 0.100 looked like a real, applicable correction. It is
    # not. A 4-parameter search over 28,561 candidates has enormous freedom
    # relative to 52 points, and 7,201 points simply do not have that problem.
    # Subsampling Palpagos to the World Tree's own n and repeating shows a
    # KNOWN-CORRECT transform wandering by a mean of 0.090 and a max of 0.120 —
    # so 0.100 is squarely inside what noise produces.
    #
    # A control run at a different sample size than the thing it controls for is
    # not a control.
    control_land = _land_mask(MAPS["Palpagos"])
    n = len(split["World Tree"])
    rng = random.Random(CONTROL_SEED)
    drifts = []
    for _ in range(CONTROL_TRIALS):
        sub = rng.sample(split["Palpagos"], min(n, len(split["Palpagos"])))
        (cdu, cdv, csu, csv), _score = refine("Palpagos", sub, control_land)
        drifts.append(max(abs(cdu), abs(cdv), abs(csu - 1), abs(csv - 1)))

    drift = max(drifts)
    print(f"  Palpagos (known correct), refined on {CONTROL_TRIALS} random "
          f"subsamples of {n} points — the World Tree's own sample size:")
    print(f"    movement mean {sum(drifts) / len(drifts):.3f}, max {drift:.3f}")
    print(f"  -> noise floor: {drift:.3f}")

    tree_land = _land_mask(MAPS["World Tree"])
    (du, dv, su, sv), tscore = refine("World Tree", split["World Tree"], tree_land)
    move = max(abs(du), abs(dv), abs(su - 1), abs(sv - 1))
    print(f"  World Tree suggests shift ({du:+.3f}, {dv:+.3f}) "
          f"scale ({su:.3f}, {sv:.3f}), {tscore:.1%} on land")
    print(f"  -> movement: {move:.3f}")

    # A NOISE FLOOR IS A RESOLUTION, NOT A CLEAN BILL OF HEALTH.
    #
    # "Movement within the floor" says the metric cannot *detect* an error, not
    # that there is none — and this floor is coarse: 0.12 of the map is roughly
    # 490 px on a 4,096 px image. An earlier version of this printed
    # "`calibrated: true` is justified" here, which reads the absence of evidence
    # as evidence of absence. It is the same overclaim in the opposite
    # direction from the one the sample-size fix caught.
    print(f"\n  This metric resolves to about {drift:.2f} of the map "
          f"(~{drift * MAP_SIZE:.0f} px of {MAP_SIZE}).")
    if move <= drift:
        print(
            "  The suggested correction is inside that, so it is not a "
            "correction — do NOT apply it. Equally, no error has been ruled\n"
            "  out at finer than that resolution, so `calibrated` stays false.\n"
            "  What HAS changed: the orientation is measured rather than "
            "assumed, which is the larger of the two unknowns."
        )
    else:
        print(
            "  The suggested correction exceeds it, so the derived extent is "
            "measurably off. Apply the shift and scale above to\n"
            "  WORLD_TREE_CELL_BOUNDS — but the result is only accurate to "
            "that same resolution, so `calibrated` still stays false."
        )
    print(
        "\n  To actually calibrate, this needs ground truth of a different kind: "
        "a known world position whose PIXEL position on the image is\n"
        "  independently known — the way Palpagos' 157 fast-travel points did "
        "it. More chests cannot supply that; they only say 'on land'."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
