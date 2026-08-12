"""
How old the world is, and what time it is there.

`GameTimeSaveData` carries two counters and **both are elapsed durations, not
timestamps** — .NET ticks (100 ns) counted from zero rather than from year 1:

    GameDateTimeTicks   time inside the world
    RealDateTimeTicks   wall-clock time the SERVER HAS BEEN RUNNING

It is in the save, which is the better of the two candidates the task listed:
the game's REST API would give a live figure and nothing at all while the server
is down, and "how old is this world" is exactly the question somebody asks about
a server that is off.

## The units are verified against a control, not assumed

Two backups of the same server exactly 24 real hours apart:

| | |
|---|---:|
| `RealDateTimeTicks` advanced | **21.43 h** |
| `GameDateTimeTicks` advanced | 1,055.66 h = **43.99 game-days** |
| ratio | 49.26x |
| implied game-day length | **29.2 real minutes** |

Two things fall out and both are checks rather than conveniences. 29.2 real
minutes per game day is Palworld's own well-known cycle, which says the tick
scale is right. And `RealDateTimeTicks` advancing only 21.43 of 24 hours says it
counts **server uptime** rather than wall time — that server was down about 11%
of the day, which is a fact worth surfacing rather than an error.

## No INI dependency, which the task expected there to be

The worry was that `DayTimeSpeedRate`/`NightTimeSpeedRate` are operator-settable
so a hardcoded conversion would drift per server. They do not enter: those rates
govern how fast game time advances *relative to real time*, and
`GameDateTimeTicks` is already game time. Turning game ticks into a game day
needs only how many game-hours are in a game day.

## THE ONE THING THAT IS NOT ESTABLISHED, AND IT IS NOT THE DAY COUNT

`BP_PalGameSetting` carries `NightStartHour = 23`, `NightEndHour = 3` and
**`PalWorldTime_GameStartHour = 5`**. The first two settle that the clock is a
24-hour one. The third does not settle what it looks like it settles: it is not
established **whether the tick counter is seeded with those five hours at world
creation, or starts at zero and the game adds the offset when it draws a
clock.**

The two readings differ by five hours, and nothing in either pak or any save
here discriminates — the ratio above is identical under both, because a constant
offset cancels in a difference.

So:

- **`day` is safe.** A five-hour ambiguity moves a day boundary, so the number
  can only ever be out by one, and only for five hours in every twenty-four.
- **`timeOfDay` carries `clockOffsetVerified: False`** and names the constant.
- **No day/night indicator is emitted at all.** "Is it night" is a claim built
  on top of the unverified offset, and with night running 23:00-03:00 — a
  four-hour window — a five-hour error could invert it outright. A wrong clock
  reads as a cosmetic glitch; a wrong "it is night" reads as a fact about the
  world.

To settle it, someone reads the in-game clock and compares. One observation.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

TICKS_PER_SECOND = 10_000_000
SECONDS_PER_DAY = 86_400
TICKS_PER_DAY = TICKS_PER_SECOND * SECONDS_PER_DAY


def describe(clock: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """
    `parser.extract_world_clock`'s output, turned into something renderable.

    Returns None when the save carried no clock — which is a real case worth
    keeping distinct from "day 0", since a world with no `GameTimeSaveData` is
    not a brand-new world, it is one this cannot read.
    """
    if not clock:
        return None

    game_ticks = clock.get("gameTicks")
    # `isinstance(True, int)` is True in Python, so a bool sails through an
    # int check and renders as Day 1 at 00:00 — a confident-looking clock built
    # from a flag. Every numeric read in this codebase carries the same guard.
    if isinstance(game_ticks, bool) or not isinstance(game_ticks, int):
        return None
    if game_ticks < 0:
        return None

    day = game_ticks // TICKS_PER_DAY
    within = game_ticks % TICKS_PER_DAY
    seconds = within // TICKS_PER_SECOND
    hour, minute = divmod(seconds // 60, 60)

    out: dict[str, Any] = {
        # Day 1 is the first day, not day 0 — the game counts from one and so
        # does every player talking about their world.
        "day": int(day) + 1,
        "hour": int(hour),
        "minute": int(minute),
        "timeOfDay": f"{hour:02d}:{minute:02d}",
        "gameTicks": int(game_ticks),
        "gameHours": round(game_ticks / TICKS_PER_SECOND / 3600, 2),
        # **NOT verified**, and named so a caller can say which constant is in
        # doubt rather than hedging vaguely. See the module docstring.
        "clockOffsetVerified": False,
        "clockOffsetNote": (
            "PalWorldTime_GameStartHour is 5 and it is not established whether "
            "the counter is seeded with it, so the time of day may be five "
            "hours out. The day number is unaffected beyond a boundary."
        ),
    }

    real_ticks = clock.get("realTicks")
    if isinstance(real_ticks, int) and not isinstance(real_ticks, bool) and real_ticks >= 0:
        # Server UPTIME, not wall time — measured at 21.43 of 24 hours across a
        # daily backup pair. Named for what it is so nobody renders it as the
        # world's age.
        out["serverUptimeHours"] = round(real_ticks / TICKS_PER_SECOND / 3600, 2)
        if game_ticks:
            out["timeRatio"] = round(game_ticks / real_ticks, 2) if real_ticks else None

    return out
