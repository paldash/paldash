#!/usr/bin/env python3
"""
Measure a mounted Pal's speed by polling the game's own REST API.

For #106: does the condenser scale movement speed? The protocol that task
carried was a stopwatch run, and the operator was right that this is a poor
instrument — a human timing a run over a landmark introduces error of the same
order as the effect being measured (a single condenser rank is 5%).

The game already publishes the answer. `/v1/api/players` returns
`location_x`/`location_y` per player, and `backend/gameapi.py` has spoken that
endpoint since Phase 8. Sampling position while riding gives speed in world
units per second directly, with no human in the timing path.

## What this measures, and what it deliberately does not

**It reports a RATIO between two runs, not a verdict.** That is #106's actual
requirement, and it is why this can work at all: the coefficient is already
known (`StatusCalculate_GenkaiToppa_PerAdd = 0.05`), so only its *scope* is in
question, and a ratio needs no unit. A reading of exactly **1.20** at four stars
confirms the known constant and generalises to all 753 species. Any *other*
non-unity ratio means a second native constant exists and **nothing may be
generalised from one Pal**.

**#106 WAS CLOSED FROM THE FILES (2026-08-17), so this run is a CONFIRMATION,
not the deciding instrument it was written as.** Five enumerable surfaces carry
zero movement members — decisively, `EPalCharacterStatusOperationName` is
exactly `{Attack, Defence, HP, WorkSpeed}`, the game's own vocabulary for the
pipeline the rank bonus feeds — so `buildplanner` ships
`condenserOnSpeedColumns: "absentByEnumeration"`. The expected reading here is
therefore **1.00** on a rank-skill-free Pal. A 1.00 closes the last gap
(names are not values, and this is the value); anything else means the enum
lies about its own pipeline, which would be a finding worth far more than the
run that produced it.

**It says nothing about which Pal to use.** Pick one with no rank-indexed
partner skill, or the partner-skill bonus (#103) is measured instead — Direhowl
reads 0/10/12/15/20% across the stars for exactly that reason and would produce
a confident wrong answer here. `backend/gamedata.partner_skills_at` says which
species are affected.

## Why the numbers are trimmed

A run starts and ends with acceleration, and the REST API's sampling is not
synchronised to anything. So the head and tail of every run are discarded and
the reported figure is the **median** of the per-interval speeds, with the
spread printed beside it — a median with a wide spread is a bad run and should
be visible as one rather than averaged into false precision.

**A sample where the position did not change is dropped, not counted as zero.**
The REST payload updates on its own schedule; a repeated position means "no new
data", and treating it as a stationary interval drags every average down. This
is the same distinction `metrics.py` draws between "nobody was playing" and "we
could not ask".

Usage:
    # Ride the Pal in a straight line for ~20s while this runs.
    python3 scripts/measure-speed.py --player Nirb --seconds 20 --label "rank1"
    python3 scripts/measure-speed.py --player Nirb --seconds 20 --label "rank5"

    # Then:
    python3 scripts/measure-speed.py --compare rank1 rank5

Needs `REST_API_ENABLED=True` and the admin password in the environment the
backend already uses (`PALWORLD_REST_URL`, `PALWORLD_ADMIN_PASSWORD`).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

RUNS = os.path.join(ROOT, ".speed-runs")

# Discard this fraction from each end of a run: acceleration at the start,
# deceleration (or a stop) at the end.
TRIM = 0.20
# Two positions closer than this are the same reading arriving twice, not
# movement. Well below anything a mount covers in a poll interval.
SAME_POSITION = 1e-6


def sample(player_name: str, seconds: float, interval: float) -> list[tuple[float, float, float]]:
    """`[(t, x, y), ...]` for the named player. Raises if they are not online."""
    import gameapi  # noqa: PLC0415

    out: list[tuple[float, float, float]] = []
    deadline = time.monotonic() + seconds
    target = player_name.strip().lower()
    while time.monotonic() < deadline:
        t = time.monotonic()
        try:
            rows = gameapi.players()
        except Exception as exc:                  # noqa: BLE001
            print(f"  ! REST call failed: {exc}", file=sys.stderr)
            time.sleep(interval)
            continue
        for row in rows:
            if str(row.get("name", "")).strip().lower() != target:
                continue
            x, y = row.get("location_x"), row.get("location_y")
            # Guard the bool-is-an-int trap this project already records:
            # `isinstance(True, int)` is True, and a flag would read as 1.0.
            if (isinstance(x, (int, float)) and not isinstance(x, bool)
                    and isinstance(y, (int, float)) and not isinstance(y, bool)):
                out.append((t, float(x), float(y)))
            break
        time.sleep(interval)
    return out


def speeds(points: list[tuple[float, float, float]]) -> list[float]:
    """Per-interval speed in world units per second, stationary samples dropped."""
    result = []
    for (t0, x0, y0), (t1, x1, y1) in zip(points, points[1:]):
        dt = t1 - t0
        if dt <= 0:
            continue
        dist = math.hypot(x1 - x0, y1 - y0)
        # A repeated position is a stale read, NOT a stationary interval.
        if dist < SAME_POSITION:
            continue
        result.append(dist / dt)
    return result


def trimmed(values: list[float]) -> list[float]:
    if len(values) < 5:
        return values
    cut = int(len(values) * TRIM)
    return values[cut:len(values) - cut] or values


def summarise(values: list[float]) -> dict:
    kept = trimmed(values)
    if not kept:
        return {"samples": 0}
    med = statistics.median(kept)
    return {
        "samples": len(kept),
        "dropped": len(values) - len(kept),
        "median": med,
        "mean": statistics.fmean(kept),
        "stdev": statistics.stdev(kept) if len(kept) > 1 else 0.0,
        # The spread relative to the value is what says whether the run is
        # usable. A tight run is a few percent; anything above ~15% is a run
        # with a corner or a stop in it and should be redone, not reported.
        "spreadPct": (statistics.stdev(kept) / med * 100) if len(kept) > 1 and med else 0.0,
    }


def do_measure(args) -> int:
    print(f"sampling '{args.player}' for {args.seconds}s every {args.interval}s")
    print("ride in a STRAIGHT line, at full speed, no corners\n")
    points = sample(args.player, args.seconds, args.interval)
    if len(points) < 4:
        print(f"only {len(points)} positions -- is the player online and named "
              "exactly right? (names are matched case-insensitively)",
              file=sys.stderr)
        return 1

    values = speeds(points)
    stats = summarise(values)
    if not stats.get("samples"):
        print("no movement between any two samples", file=sys.stderr)
        return 1

    print(f"positions       {len(points)}")
    print(f"usable intervals{stats['samples']:>6}  ({stats['dropped']} trimmed)")
    print(f"median          {stats['median']:>10.1f} units/s")
    print(f"mean            {stats['mean']:>10.1f}")
    print(f"spread          {stats['spreadPct']:>10.1f}%", end="")
    print("   <- redo this run, too noisy to compare"
          if stats["spreadPct"] > 15 else "")

    os.makedirs(RUNS, exist_ok=True)
    path = os.path.join(RUNS, f"{args.label}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"label": args.label, "player": args.player,
                   "note": args.note, **stats}, fh, indent=1)
    print(f"\nsaved {os.path.relpath(path, ROOT)}")
    return 0


def do_compare(args) -> int:
    runs = []
    for label in args.compare:
        path = os.path.join(RUNS, f"{label}.json")
        if not os.path.exists(path):
            print(f"no run named '{label}'", file=sys.stderr)
            return 1
        runs.append(json.load(open(path, encoding="utf-8")))

    a, b = runs[0], runs[1]
    ratio = b["median"] / a["median"] if a["median"] else 0.0
    print(f"{a['label']:>12}  {a['median']:>9.1f} units/s  ±{a['spreadPct']:.1f}%")
    print(f"{b['label']:>12}  {b['median']:>9.1f} units/s  ±{b['spreadPct']:.1f}%")
    print(f"\nRATIO         {ratio:>9.4f}")

    noisy = max(a["spreadPct"], b["spreadPct"])
    if noisy > 15:
        print(f"\n** Spread is {noisy:.0f}% -- this ratio cannot settle anything. "
              "Redo both runs in a straight line. **")
        return 1

    # The reading, stated the way #106 needs it: what generalises, and what does
    # not. Nothing here decides the answer -- it names which answer this is.
    print()
    if abs(ratio - 1.0) < 0.02:
        print("~1.00 -- no movement bonus detected between these two runs.")
        print("Consistent with the three file-side signals: the StatusCalculate_*")
        print("family has no movement member, the condenser screen previews no")
        print("speed row, and the rank-scaling function family has none either.")
    elif abs(ratio - 1.20) < 0.02:
        print("~1.20 -- this IS StatusCalculate_GenkaiToppa_PerAdd (0.05 x 4).")
        print("The coefficient was already known, so with no free parameter to")
        print("fit, this GENERALISES to all 753 species.")
    else:
        print(f"{ratio:.3f} -- neither 1.00 nor the known 1.20.")
        print("That means a second native constant exists, and NOTHING may be")
        print("generalised from one Pal. Repeat on a second species before")
        print("anything is written down.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--player", help="in-game player name, as the REST API reports it")
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--label", default="run", help="name this run, for --compare")
    ap.add_argument("--note", default="", help="species, condenser rank, on foot or ridden")
    ap.add_argument("--compare", nargs=2, metavar=("BASELINE", "OTHER"),
                    help="ratio between two saved runs")
    args = ap.parse_args()

    if args.compare:
        return do_compare(args)
    if not args.player:
        ap.error("--player is required unless --compare is given")
    return do_measure(args)


if __name__ == "__main__":
    sys.exit(main())
