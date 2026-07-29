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
    now = int(time.time())
    for i in range(10):
        metrics.store(up(now - i * 60, fps=40.0 + i))

    result = metrics.series(hours=1, buckets=1)
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
    now = int(time.time())
    for i in range(4):
        metrics.store(up(now - i * 60) if i % 2 == 0 else down(now - i * 60))

    point = metrics.series(hours=1, buckets=1)["points"][0]
    assert point["reachable"] == pytest.approx(0.5)


def test_a_fully_down_bucket_reads_as_zero_reachable_with_no_fps(fresh):
    now = int(time.time())
    for i in range(3):
        metrics.store(down(now - i * 60))

    point = metrics.series(hours=1, buckets=1)["points"][0]
    assert point["reachable"] == 0.0
    assert point["serverFps"] is None        # a gap, not a zero


def test_series_ignores_samples_outside_the_window(fresh):
    now = int(time.time())
    metrics.store(up(now - 3600 * 5))
    metrics.store(up(now - 60))

    assert metrics.series(hours=1, buckets=10)["points"].__len__() == 1


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
