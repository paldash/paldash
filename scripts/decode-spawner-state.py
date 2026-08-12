#!/usr/bin/env python3
"""
`MapObjectSpawnerInStageSaveData` — the respawn clock for every world gatherable.

    python3 scripts/decode-spawner-state.py refworld/Level.sav [more...]

The single largest unread structure in the save (task #86): **31,824 slots on
refworld**, and no backend module mentioned a single one of its field names. The
hypothesis was that it holds the runtime state of the world's object spawners —
which ore nodes and chests have been harvested and when they come back.

**It does, and the confirmation is unusually strong.** See below. It is *still*
not mappable, and the blocker is named precisely so nobody re-derives it.

## The shape

    MapObjectSpawnerInStageSaveData          keyed by STAGE
      key.InternalId                         all-zero = the overworld
      value.SpawnerDataMapByLevelObjectInstanceId
        key                                  a LEVEL OBJECT instance id
        value.ItemMap                        int -> struct
          NextLotteryGameTime  Int64         game ticks, or -1
          MapObjectInstanceId  Guid          the object standing there now

## The verification: a 2x4 table with every off-diagonal cell at zero

Cross-tabulate "does `MapObjectInstanceId` resolve in `MapObjectSaveData`"
against "is there a respawn timer", and the two independently-read fields agree
without a single exception:

| | live object | no object |
|---|---:|---:|
| no timer (`-1`) | **2,788** | 17,761 |
| timer set | **0** | 11,275 |

A spawner whose object is standing has no timer; a spawner with a timer has no
object. **Zero violations across three worlds and ~99,800 slots.** That is not a
coincidence, and it is a much better check than any count: it is the model
predicting a relationship between two fields and being right every time.

Two supporting readings:

- **`DateTime.MaxValue` means never, and it reads as a value.**
  `3155378975999999999` appears on three slots of one snapshot; as a duration
  that is 87,637,883 game-hours, and a naive summary prints it as the respawn
  range. Excluding it, **316 of 319 pending timers fall within 30 days**. Same
  family as `RideSprintSpeed = -1`.
- **The timestamps behave like a countdown.** Measured against
  `GameTimeSaveData.GameDateTimeTicks` — a clock from an entirely different
  structure — 154 of refworld's slots sit **in the future**, 0.46 to 219 game-
  hours out, with a smoothly decaying histogram. 985 have elapsed and not been
  cleared; 10,136 are exactly 0 and 20,549 are `-1`.
- **The objects that resolve are exactly the right kinds**: `TreasureBox`
  (1,142), fishing junk, and `DamagableRock*` — Copper Ore and Rock. Gatherables,
  which is what a spawner should point at.

## READ EVERY STAGE, NOT `[0]`

The outer map is keyed by stage. refworld has **one** entry (the all-zero
overworld id) and a later snapshot of the same server has **three**: the
overworld with 34,598 spawners, plus two instanced stages with **5 spawners
each** — dungeon chests.

A reader that takes `outer[0]` gets the overworld and silently drops every
dungeon. That is the `base_camp_level` mistake exactly — it was missed for
months because a check sampled `GroupSaveDataMap[0]`, which could never have
carried it. **Sample by variant, never by index.**

## WHY THERE IS NO MAP LAYER, AND WHAT WOULD UNBLOCK ONE

The obvious feature is "show me which ore nodes are available". It cannot be
built today:

- the spawner key is a **level object instance id**, and **0 of 31,774 of them**
  resolve against `MapObjectSaveData`. Different id space, as the field name
  says.
- `worldobjects.json.gz` has all 59,396 spawn points from the pak — but it
  carries `cls`, `x`, `y`, `z` and **no GUID**, so there is nothing to join to.

So the save knows *that* a node is respawning and the pak knows *where* every
node is, and nothing connects them.

**The unblock is a known technique.** `scripts/extract-effigies.py` already
reads an actor's instance GUID out of a world cell (byte 252 on the relic
actor), which is how 396 effigies got their save-matching ids. Teaching
`extract-world-objects.py` to capture the same GUID per object would make this
join work and turn 154 numbers into 154 map pins. That is a real piece of work
and the offset will not be 252 for every actor class — but it is a path, not a
wall.

Until then this script reports counts, and nothing in `backend/` reads the
structure: a respawn timer with no position is a number, not a feature.
"""

from __future__ import annotations

import collections
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

TICKS_PER_SECOND = 10_000_000
OVERWORLD_STAGE = "00000000-0000-0000-0000-000000000000"

# THREE SENTINELS, AND ONE OF THEM READS AS A VALUE.
#
# `3155378975999999999` is exactly .NET's `DateTime.MaxValue.Ticks`. Taken as a
# timer it is 87,637,883 game-hours — ten thousand years — and a naive "soonest
# and latest" summary prints that as the respawn range, which is nonsense
# wearing a number. It means **never**.
#
# Same family as `RideSprintSpeed = -1` and the all-zero player uid: a sentinel
# that survives arithmetic is the one that gets reported as data.
NEVER_TICKS = 3_155_378_975_999_999_999
NO_TIMER = -1          # spawner idle; its object is standing
UNSET = 0              # never written


def read_stages(world: dict) -> list[dict]:
    """
    Every stage, not just the overworld.

    Returns one dict per stage entry. **Iterating rather than indexing `[0]` is
    the point** — see the module docstring; a later snapshot of the reference
    server carries two dungeon stages beside the overworld.
    """
    import parser as pparser

    node = world.get("MapObjectSpawnerInStageSaveData")
    if not node:
        return []

    stages = []
    for entry in node.get("value") or []:
        internal_id = str(pparser._v(entry, "key", "InternalId", "value") or "")
        spawners = pparser._v(
            entry, "value", "SpawnerDataMapByLevelObjectInstanceId", "value", default=[]
        ) or []
        rows = []
        for spawner in spawners:
            for slot in pparser._v(spawner, "value", "ItemMap", "value", default=[]) or []:
                rows.append({
                    "spawnerId": str(spawner.get("key") or "").lower(),
                    "slot": slot.get("key"),
                    "nextLotteryGameTime": pparser._v(
                        slot, "value", "NextLotteryGameTime", "value"),
                    "objectId": str(pparser._v(
                        slot, "value", "MapObjectInstanceId", "value") or "").lower(),
                })
        stages.append({
            "internalId": internal_id,
            "isOverworld": internal_id == OVERWORLD_STAGE,
            "spawners": len(spawners),
            "slots": rows,
        })
    return stages


def report(path: str) -> int:
    """Returns the number of contingency violations — 0 is the healthy state."""
    import gamedata
    import parser as pparser

    gvas = pparser.load_gvas(path)
    if gvas is None:
        print(f"!! {path} did not parse")
        return 0
    world = pparser._world_save_data(gvas)
    stages = read_stages(world)
    if not stages:
        print(f"{path}: structure absent")
        return 0

    now = pparser._v(world.get("GameTimeSaveData"), "value", "GameDateTimeTicks", "value")
    live = {}
    for obj in pparser._v(world, "MapObjectSaveData", "value", "values", default=[]) or []:
        instance = str(
            pparser._v(obj, "Model", "value", "RawData", "value", "instance_id") or ""
        ).lower()
        if instance:
            live[instance] = str(pparser._v(obj, "MapObjectId", "value") or "")

    print(f"\n=== {path}")
    print(f"  game clock: {now}")
    for stage in stages:
        label = "overworld" if stage["isOverworld"] else f"stage {stage['internalId'][:8]}"
        print(f"  {label:20s} {stage['spawners']:7,} spawners  "
              f"{len(stage['slots']):7,} slots")

    rows = [r for s in stages for r in s["slots"]]
    table = collections.Counter()
    pending, elapsed, never, kinds = [], 0, 0, collections.Counter()
    for row in rows:
        when = row["nextLotteryGameTime"]
        standing = row["objectId"] in live
        table[(when != -1, standing)] += 1
        if standing:
            kinds[live[row["objectId"]]] += 1
        elif when not in (NO_TIMER, UNSET, None) and now:
            if when == NEVER_TICKS:
                never += 1
            elif when > now:
                pending.append((when - now) / TICKS_PER_SECOND / 3600)
            else:
                elapsed += 1

    violations = table[(True, True)]
    print(f"\n  {'':18s}{'standing':>10s}{'gone':>10s}")
    print(f"  {'no timer (-1)':18s}{table[(False, True)]:10,}{table[(False, False)]:10,}")
    print(f"  {'timer set':18s}{violations:10,}{table[(True, False)]:10,}"
          f"   <- left column MUST be 0")
    if pending:
        pending.sort()
        print(f"\n  {len(pending)} respawning: soonest {pending[0]:.1f}h, "
              f"latest {pending[-1]:.1f}h (game time)")
    print(f"  {elapsed} timers already elapsed")
    if never:
        print(f"  {never} marked NEVER (DateTime.MaxValue), excluded from the range above")
    if kinds:
        print("  standing objects, by kind:")
        for kind, count in kinds.most_common(5):
            print(f"     {kind:36s} {count:6,}  {gamedata.structure_name(kind)}")
    return violations


def main(paths: list[str]) -> int:
    if not paths:
        print("usage: decode-spawner-state.py <Level.sav> [Level.sav ...]")
        return 2
    total = 0
    for path in paths:
        if not os.path.exists(path):
            print(f"!! {path} not found")
            continue
        total += report(path)
    print(f"\ncontingency violations across all worlds: {total}")
    # The relationship IS the finding. If a spawner ever has both a live object
    # and a respawn timer, the reading is no longer supported and this must say
    # so rather than printing counts that look fine.
    return 0 if total == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
