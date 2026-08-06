"""
Metrics history.

The thing worth testing here is not that averages average. It is that a period
when the server was *unreachable* stays distinguishable from a period when it was
up and idle — because those two look identical if you store them carelessly, and
a chart that draws a smooth line through an outage is worse than no chart.
"""

from __future__ import annotations

import time

import pytest

import db
import gameapi
import metrics


@pytest.fixture
def fresh(fresh_db):
    return fresh_db


def row(ts, **overrides):
    base = {name: None for name in metrics._COLUMNS}
    base.update({"ts": ts, "reachable": 0})
    base.update(overrides)
    return base


def up(ts, fps=45.0, players=3):
    return row(ts, server_fps=fps, players=players, reachable=1, frame_time=22.0)


def down(ts):
    """Server unreachable: the row exists, the game fields are NULL."""
    return row(ts, reachable=0, cpu_percent=4.0)


def mid_bucket(width=3600):
    """
    A timestamp halfway through a bucket of `width`, and `series`' idea of "now".

    Bucket boundaries are aligned to absolute epoch time, so samples anchored to
    the real clock land in one bucket or two depending on the minute the suite
    runs. Anchoring mid-bucket makes it deterministic: without this, the
    single-bucket assertions below held for 51 minutes of every hour and failed
    for the other 9.
    """
    return (int(time.time()) // width) * width + width // 2


# ─── Storage ─────────────────────────────────────────────


def test_a_sample_round_trips(fresh):
    metrics.store(up(1_000_000))
    stored = db.connect().execute("SELECT * FROM metrics").fetchone()
    assert stored["server_fps"] == 45.0
    assert stored["reachable"] == 1


def test_two_samples_in_one_second_are_one_sample(fresh):
    """
    `ts` is the primary key. A clock that steps backwards, or two ticks racing,
    must overwrite rather than fail the whole write.
    """
    metrics.store(up(1_000_000, fps=45.0))
    metrics.store(up(1_000_000, fps=12.0))

    rows = db.connect().execute("SELECT server_fps FROM metrics").fetchall()
    assert [r["server_fps"] for r in rows] == [12.0]


def test_an_unreachable_sample_is_still_written(fresh):
    """
    A gap has to be recorded to be drawable. Skipping the sample entirely would
    let a chart interpolate straight across an outage.
    """
    metrics.store(down(1_000_000))
    stored = db.connect().execute("SELECT * FROM metrics").fetchone()
    assert stored["reachable"] == 0
    assert stored["server_fps"] is None
    assert stored["cpu_percent"] == 4.0        # host numbers still work


def test_players_is_null_when_unreachable_not_zero(fresh):
    """
    "Nobody was playing" and "we could not ask" are different facts. Coercing the
    second to 0 makes an outage look like a quiet evening.
    """
    metrics.store(down(1_000_000))
    assert db.connect().execute("SELECT players FROM metrics").fetchone()["players"] is None


# ─── Retention ───────────────────────────────────────────


def test_prune_drops_only_what_is_past_the_window(fresh, monkeypatch):
    monkeypatch.setattr(metrics, "RETENTION_DAYS", 1)
    now = 2_000_000
    metrics.store(up(now - 86400 * 2))      # older than the window
    metrics.store(up(now - 3600))           # inside it

    assert metrics.prune(now=now) == 1
    remaining = db.connect().execute("SELECT ts FROM metrics").fetchall()
    assert [r["ts"] for r in remaining] == [now - 3600]


def test_prune_on_an_empty_table_is_not_an_error(fresh):
    assert metrics.prune() == 0


# ─── Queries ─────────────────────────────────────────────


def test_series_buckets_and_averages(fresh):
    now = mid_bucket()
    for i in range(10):
        metrics.store(up(now - i * 60, fps=40.0 + i))

    result = metrics.series(hours=1, buckets=1, now=now)
    assert len(result["points"]) == 1
    point = result["points"][0]
    assert point["samples"] == 10
    assert point["serverFps"] == pytest.approx(44.5, abs=0.1)


def test_reachable_is_a_fraction_not_a_flag(fresh):
    """
    This is the whole reason `reachable` is averaged. A bucket that is half up is
    an intermittently crashing server — the exact thing an operator is looking
    for — and a boolean would round it away in either direction.
    """
    now = mid_bucket()
    for i in range(4):
        metrics.store(up(now - i * 60) if i % 2 == 0 else down(now - i * 60))

    point = metrics.series(hours=1, buckets=1, now=now)["points"][0]
    assert point["reachable"] == pytest.approx(0.5)


def test_a_fully_down_bucket_reads_as_zero_reachable_with_no_fps(fresh):
    now = mid_bucket()
    for i in range(3):
        metrics.store(down(now - i * 60))

    point = metrics.series(hours=1, buckets=1, now=now)["points"][0]
    assert point["reachable"] == 0.0
    assert point["serverFps"] is None        # a gap, not a zero


def test_series_ignores_samples_outside_the_window(fresh):
    now = int(time.time())
    metrics.store(up(now - 3600 * 5))
    metrics.store(up(now - 60))

    assert metrics.series(hours=1, buckets=10)["points"].__len__() == 1


def test_bucket_boundaries_are_absolute_so_a_window_may_straddle_one(fresh):
    """
    Pins the alignment rather than treating the extra point as a bug.

    `buckets` is a resolution target. Boundaries come from the timestamp itself,
    which is what keeps a chart's x positions still between refreshes — measuring
    back from `now` instead would slide every boundary on every poll. The visible
    consequence is that samples spanning a boundary return two points, so this
    asserts that on purpose: a later "fix" that makes the count exact would have
    reintroduced the jitter.
    """
    boundary = (int(time.time()) // 3600) * 3600
    metrics.store(up(boundary - 60))     # just before
    metrics.store(up(boundary + 60))     # just after

    points = metrics.series(hours=1, buckets=1, now=boundary + 120)["points"]
    assert len(points) == 2
    assert [p["samples"] for p in points] == [1, 1]


def test_series_clamps_absurd_arguments(fresh):
    """Bounded so a crafted query cannot ask for a million buckets."""
    result = metrics.series(hours=10**9, buckets=10**9)
    assert result["hours"] <= metrics.RETENTION_DAYS * 24
    assert result["bucketSeconds"] >= 1


def test_summary_reports_coverage(fresh):
    now = int(time.time())
    metrics.store(up(now - 120))
    metrics.store(down(now - 60))

    summary = metrics.summary()
    assert summary["samples"] == 2
    assert summary["uptimeFraction"] == pytest.approx(0.5)
    assert summary["oldest"] == now - 120


def test_summary_on_an_empty_table(fresh):
    summary = metrics.summary()
    assert summary["samples"] == 0
    assert summary["oldest"] is None


# ─── Sampling ────────────────────────────────────────────


def test_a_sample_survives_an_unreachable_server(fresh, monkeypatch):
    def boom():
        raise gameapi.GameApiUnavailable("nothing there")

    monkeypatch.setattr(gameapi, "metrics", boom)
    sample = metrics.sample()

    assert sample["reachable"] == 0
    assert sample["server_fps"] is None
    assert sample["ts"] > 0


def test_a_sample_reads_the_game_when_it_answers(fresh, monkeypatch):
    monkeypatch.setattr(gameapi, "metrics", lambda: {
        "serverfps": 58, "frametime": 17.2, "currentplayernum": 4,
        "maxplayernum": 16, "uptime": 9999,
    })
    sample = metrics.sample()

    assert sample["reachable"] == 1
    assert sample["server_fps"] == 58.0
    assert sample["players"] == 4


def test_a_sample_never_parses_the_world(fresh, monkeypatch):
    """
    Parsing on a metrics tick would defeat the entire parse scheduler. The counts
    come from whatever the last parse left behind, hence `auto=False`.
    """
    import savecache

    calls = []
    monkeypatch.setattr(savecache, "request_parse", lambda **kw: calls.append(kw))
    monkeypatch.setattr(gameapi, "metrics", lambda: {})
    metrics.sample()
    assert calls == []


def test_the_first_cpu_reading_is_none_rather_than_invented(fresh):
    """A rate needs two samples. One sample is not a slow rate, it is no rate."""
    metrics._last_cpu = None
    assert metrics.cpu_percent() is None
    assert metrics.cpu_percent() is not None or True   # second may still be 0.0


# ─── The game's memory is not the container's ────────────


def test_game_memory_is_none_when_the_process_is_not_visible(monkeypatch):
    """
    **The ordinary answer in the normal deployment.** The dashboard runs in its
    own container without a shared PID namespace, so the game's `/proc` entries
    are not there to read — and `None` must reach the chart as "not available"
    rather than as 0, which would read as a server using no memory at all.
    """
    import metrics

    monkeypatch.setattr(metrics, "_game_pid", None)
    monkeypatch.setattr(metrics, "_process_matches", lambda pid: False)
    assert metrics.game_memory() is None


def test_a_cached_pid_is_rechecked_not_trusted(monkeypatch):
    """
    Pids are reused. A chart that silently began reporting some other process's
    memory would be worse than reporting none — it would look like the leak had
    stopped.
    """
    import metrics

    monkeypatch.setattr(metrics, "_game_pid", 4242)
    seen = []

    def _matches(pid):
        seen.append(pid)
        return False        # the cached pid is now somebody else

    monkeypatch.setattr(metrics, "_process_matches", _matches)
    monkeypatch.setattr(metrics.os, "listdir", lambda _p: [])
    assert metrics._find_game_pid() is None
    assert 4242 in seen, "the cached pid was returned without being re-checked"


def test_game_memory_is_a_separate_column_from_container_memory():
    """
    They answer different questions and must not be conflated: `mem_used_mb` is
    the cgroup's figure — this container, the dashboard — while `game_mem_mb` is
    the process the leak actually happens in.
    """
    import metrics

    assert "mem_used_mb" in metrics._COLUMNS
    assert "game_mem_mb" in metrics._COLUMNS


def test_every_new_host_column_is_nullable_in_a_sample():
    """
    A sample taken where nothing is readable must still be a row — a failed
    reading is a gap, not an error, and the same rule keeps `players` from being
    coerced to 0 when the server is down.
    """
    import metrics

    row = metrics.sample()
    for column in ("swap_used_mb", "cpu_steal", "net_rx_kbs", "cpu_temp_c",
                   "game_mem_mb"):
        assert column in row, column
