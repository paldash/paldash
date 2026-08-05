#!/usr/bin/env python3
"""
Base camp levels, worker caps, illness penalties and the sanity thresholds at
which a worker stops working.

THREE THINGS THE DASHBOARD CURRENTLY CANNOT SAY, all sitting in three small
tables:

1. **`palCount` has no denominator.** The Bases tab reports "11 Pals here" with
   nothing to compare it against, so it does not answer whether a base is full
   or starved. `DT_BaseCampLevelData` gives the per-level worker cap and the
   bases-per-guild cap.

   **BUT THE TABLE IS A DEFAULT, NOT A BOUND.** A real server in use runs 5
   bases per guild and 25 workers against the table's 4 and 30 — over it in one
   direction and under it in the other. `BaseCampWorkerMaxNum`,
   `BaseCampMaxNumInGuild` and `BaseCampMaxNum` are all `PalWorldSettings.ini`
   keys, so the INI is the only authority and nothing downstream may clamp to
   these numbers or fall back to them. `gamedata.server_limit()` is the reader.

   Base level is also **not in the save**, so the per-level rows have no caller
   yet. They are bundled because the table is real, not because anything uses
   it.

2. **"Sick" is a flag.** `DT_BaseCampWorkerSickDataTable` gives what each
   illness actually costs — work speed, move speed, satiety — and the chance the
   palbox cures it. `BP_PalGameSetting.PalBoxTimePeriodRecoverySick = 3600` says
   that chance is rolled hourly, so the two together are an actionable sentence
   rather than a warning triangle.

3. **`main.LOW_SANITY` may be measuring the wrong thing.** It is 50, from
   `FriendshipPoint_AutoIncrementRequireSanity` — the sanity a Pal needs to keep
   *gaining trust*. `DT_BaseCampWorkerEventDataTable` says a worker starts
   taking short breaks at **85** and stops working long before 50. Those are
   different questions and the welfare panel currently answers the first while
   appearing to answer the second.

   This script bundles the thresholds; deciding what the panel should use is
   task #59 and is deliberately not made here.

THE DEBUG NAMES ARE JAPANESE AND ARE NOT UI TEXT. `Debug_DisplayName` holds
Pocketpair's internal labels (サボり, 引きこもり). They are carried as
`debugName` because they disambiguate what an event id means, and are marked so
nothing renders them to a player. The ids themselves (`DodgeWork`,
`EatTooMuch`, `TurnFoodBox`) are already readable.

VERIFICATION. The worker cap must be at least as large as the biggest worker
container ever observed in a real save — `scripts/verify-figures.py` found
capacity 25 on the live world, and AGENTS.md records 20/16/13/8 on refworld. A
table whose maximum is below that is being read wrong, whatever it looks like.

Usage:  python3 scripts/extract-basecamp.py [--verify]
Output: backend/data/basecamp.json.gz
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import palpak            # noqa: E402
import uassettable       # noqa: E402
from jsonout import write_json  # noqa: E402

OUT = os.path.join(os.path.dirname(HERE), "backend", "data", "basecamp.json.gz")

LEVELS = "DT_BaseCampLevelData.uasset"
SICK = "DT_BaseCampWorkerSickDataTable.uasset"
EVENTS = "DT_BaseCampWorkerEventDataTable.uasset"

# The largest character-container capacity seen across four real worlds. The
# level table's maximum must reach it or the read is wrong. See AGENTS.md's
# "Which base a Pal works at" section and scripts/verify-figures.py.
OBSERVED_MAX_WORKERS = 25


def _enum(value) -> str:
    text = str(value or "")
    return text.rsplit("::", 1)[-1] if "::" in text else text


def _read(pak, name: str) -> dict:
    path = next((p for p in pak.files if p.endswith(name)), None)
    if path is None:
        raise SystemExit(f"{name} is not in this pak — did the game update?")
    return uassettable.read_table(pak, path)


def build(pak=None) -> dict:
    pak = pak or palpak.Pak()

    levels = []
    for row in _read(pak, LEVELS).values():
        levels.append({
            "level": int(row.get("Level") or 0),
            "workerMax": int(row.get("WorkerMaxNum") or 0),
            "basesPerGuild": int(row.get("BaseCampMaxNumInGuild") or 0),
        })
    levels.sort(key=lambda r: r["level"])

    illnesses = []
    for key, row in _read(pak, SICK).items():
        sick_type = _enum(row.get("SickType"))
        # `NoneSick` is the game's "not ill" row, not an illness.
        if sick_type in ("", "None"):
            continue
        illnesses.append({
            "id": sick_type,
            "row": str(key),
            # Signed percentages, as the table stores them: Cold is -5 work
            # speed, GastricUlcer is -10 work and -5 move.
            "workSpeed": int(row.get("WorkSpeed") or 0),
            "moveSpeed": int(row.get("MoveSpeed") or 0),
            "satietyDecrease": int(row.get("SatietyDecrease") or 0),
            # Rolled once per PalBoxTimePeriodRecoverySick (3600s).
            "palboxRecoveryPercent": int(
                row.get("RecoveryProbabilityPercentageInPalBox") or 0
            ),
            # Which medicine rank clears it. NOT resolved to an item here —
            # that mapping is unverified and would be a mechanic claim.
            "effectiveItemRank": int(row.get("EffectiveItemRank") or 0),
        })
    illnesses.sort(key=lambda r: r["id"])

    events = []
    for key, row in _read(pak, EVENTS).items():
        events.append({
            "id": str(key),
            "triggerSanity": int(row.get("TriggerSanity") or 0),
            "assignableWork": bool(row.get("bAssignableWork")),
            "assignableFixedWork": bool(row.get("bAssignableFixedWork")),
            "interruptsHunger": bool(row.get("bAllowInterruptRecoverHungry")),
            "interruptsSleep": bool(row.get("bAllowInterruptSleep")),
            # Pocketpair's internal Japanese label. Kept because it disambiguates
            # an id; flagged because it is not UI text.
            "debugName": str(row.get("Debug_DisplayName") or ""),
            "debugNameIsInternal": True,
        })
    events.sort(key=lambda r: -r["triggerSanity"])

    return {"levels": levels, "illnesses": illnesses, "workerEvents": events}


def main() -> int:
    pak = palpak.Pak()
    data = build(pak)

    levels = data["levels"]
    if not levels:
        print("REFUSING: no base camp levels decoded.", file=sys.stderr)
        return 2

    top = max(r["workerMax"] for r in levels)
    if top < OBSERVED_MAX_WORKERS:
        print(
            f"REFUSING: the table's highest worker cap is {top}, but a real "
            f"world has a {OBSERVED_MAX_WORKERS}-slot worker container. The "
            "read is wrong, however plausible the numbers look.",
            file=sys.stderr,
        )
        return 3

    if "--verify" in sys.argv:
        print(f"verified {len(levels)} levels (worker cap 1-{top}, "
              f">= the {OBSERVED_MAX_WORKERS} observed in a real save)")
        return 0

    write_json(OUT, data)
    print(f"wrote {OUT}")
    print(f"  {len(levels)} base levels, worker cap {levels[0]['workerMax']}"
          f"-{top}, bases per guild up to "
          f"{max(r['basesPerGuild'] for r in levels)}")
    print(f"  {len(data['illnesses'])} illnesses:")
    for ill in data["illnesses"]:
        print(f"    {ill['id']:16s} work {ill['workSpeed']:>4}%  "
              f"move {ill['moveSpeed']:>4}%  satiety {ill['satietyDecrease']:>3}%  "
              f"palbox cure {ill['palboxRecoveryPercent']}%/hr")
    print(f"  {len(data['workerEvents'])} worker events, sanity triggers "
          f"{data['workerEvents'][-1]['triggerSanity']}"
          f"-{data['workerEvents'][0]['triggerSanity']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
