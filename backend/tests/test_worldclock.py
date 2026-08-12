"""
The in-game clock, and the five hours it refuses to guess.

`GameTimeSaveData` holds two elapsed **durations** in .NET ticks — not
timestamps, unlike `OwnedTime`, whose name reads the opposite way round.

The units were verified against a control rather than assumed: two backups of
the same server exactly 24 real hours apart show `GameDateTimeTicks` advancing
43.99 game-days while `RealDateTimeTicks` advances 21.43 hours, which puts a
game day at **29.2 real minutes** — Palworld's own cycle — and says the real
counter tracks server uptime rather than wall time.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import worldclock  # noqa: E402

DAY = worldclock.TICKS_PER_DAY
HOUR = worldclock.TICKS_PER_SECOND * 3600


def test_day_one_starts_at_zero():
    """
    The game counts from one and so does every player talking about their
    world. A brand-new world is Day 1, not Day 0.
    """
    assert worldclock.describe({"gameTicks": 0})["day"] == 1
    assert worldclock.describe({"gameTicks": DAY - 1})["day"] == 1
    assert worldclock.describe({"gameTicks": DAY})["day"] == 2


def test_the_reference_world_reads_day_382():
    """refworld's own figure, as a regression signal."""
    result = worldclock.describe({"gameTicks": 329288250000000,
                                  "realTicks": 7160466200000})
    assert result["day"] == 382
    assert result["timeOfDay"] == "02:53"
    assert result["serverUptimeHours"] == 198.9


def test_time_of_day_wraps_within_a_day():
    for hour in (0, 5, 12, 23):
        result = worldclock.describe({"gameTicks": DAY * 10 + HOUR * hour})
        assert result["hour"] == hour
        assert result["timeOfDay"].startswith(f"{hour:02d}:")


def test_the_clock_offset_is_flagged_and_named():
    """
    THE REFUSAL. `PalWorldTime_GameStartHour` is 5 and it is **not established**
    whether the counter is seeded with it or the game adds it at draw time. The
    two readings differ by five hours and nothing here discriminates — a
    constant offset cancels in the difference the units were verified with.

    So the flag travels, and it names the constant rather than hedging.
    """
    result = worldclock.describe({"gameTicks": DAY * 3})
    assert result["clockOffsetVerified"] is False
    assert "PalWorldTime_GameStartHour" in result["clockOffsetNote"]


def test_no_day_night_indicator_is_emitted():
    """
    Night runs 23:00-03:00 — a four-hour window — so a five-hour error could
    invert it outright. A wrong clock reads as a cosmetic glitch; a wrong "it is
    night" reads as a fact about the world.

    Asserted as an absence, because the tempting next commit is to add one.
    """
    result = worldclock.describe({"gameTicks": DAY * 3 + HOUR})
    for key in ("isNight", "isDay", "phase", "daylight", "night"):
        assert key not in result, f"{key} is a claim built on an unverified offset"


def test_uptime_is_named_for_what_it_is():
    """
    `RealDateTimeTicks` counts **server uptime**, not the world's age — it
    advanced only 21.43 of 24 hours across a daily backup pair. A caller that
    read it as wall time would report a world as younger than it is.
    """
    result = worldclock.describe({"gameTicks": DAY * 44,
                                  "realTicks": HOUR * 21})
    assert "serverUptimeHours" in result
    assert result["serverUptimeHours"] == 21.0
    assert result["timeRatio"] == round((DAY * 44) / (HOUR * 21), 2)


def test_a_missing_clock_is_none_not_day_one():
    """
    A world with no `GameTimeSaveData` is one this cannot read, not a new one.
    The two must not share a representation — the `.catch(() => [])` lesson.
    """
    assert worldclock.describe(None) is None
    assert worldclock.describe({}) is None
    assert worldclock.describe({"realTicks": 5}) is None


def test_nonsense_input_is_refused_rather_than_rendered():
    for bad in ({"gameTicks": -1}, {"gameTicks": "lots"},
                {"gameTicks": None}, {"gameTicks": True}):
        assert worldclock.describe(bad) is None, bad


def test_a_missing_real_counter_still_gives_a_day():
    """
    The two fields are independent. A save carrying only the game clock must
    still report the day rather than dropping everything.
    """
    result = worldclock.describe({"gameTicks": DAY * 7})
    assert result["day"] == 8
    assert "serverUptimeHours" not in result
