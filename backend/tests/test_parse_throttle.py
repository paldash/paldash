"""
Load-aware parse throttling.

Gameplay wins over dashboard responsiveness, so a parse defers to a server that is
already struggling. The direction of failure is the opposite of the corruption
guard's and that is deliberate: `safety.py` fails *closed* because writing to a
live save destroys a world, while this fails **open** because refusing to parse
forever over a missing signal just breaks the dashboard.
"""

from __future__ import annotations

import time

import pytest

import metrics
import savecache


@pytest.fixture
def loaded(fresh_db, monkeypatch):
    monkeypatch.setattr(savecache, "PARSE_LOAD_AWARE", True)
    monkeypatch.setattr(savecache, "PARSE_MIN_SERVER_FPS", 20.0)
    monkeypatch.setattr(savecache, "PARSE_FORCE_MIN_SERVER_FPS", 12.0)
    return fresh_db


def sample(fps, ts=None, reachable=1):
    row = {name: None for name in metrics._COLUMNS}
    row.update({
        "ts": int(ts if ts is not None else time.time()),
        "server_fps": fps,
        "reachable": reachable,
    })
    metrics.store(row)


# ─── Fails open ──────────────────────────────────────────


def test_no_samples_means_no_opinion(loaded):
    """
    A fresh install has no history. Deferring every parse until metrics exist
    would make the dashboard useless on day one for no safety benefit.
    """
    verdict = savecache.load_verdict()
    assert verdict["busy"] is False
    assert "no load data" in verdict["reason"]


def test_stale_samples_are_ignored(loaded):
    """
    A reading from an hour ago says nothing about now — and if the metrics thread
    has died, throttling must not latch on forever.
    """
    sample(2.0, ts=time.time() - 7200)
    assert savecache.load_verdict()["busy"] is False


def test_an_unreachable_server_does_not_defer_a_parse(loaded):
    """
    A stopped server is not a busy one. It is also exactly when someone wants a
    parse, since editing saves requires the server to be down.
    """
    sample(None, reachable=0)
    assert savecache.load_verdict()["busy"] is False


def test_throttling_can_be_switched_off(loaded, monkeypatch):
    monkeypatch.setattr(savecache, "PARSE_LOAD_AWARE", False)
    sample(1.0)
    assert savecache.load_verdict()["busy"] is False


def test_a_missing_metrics_table_is_not_fatal(monkeypatch, tmp_path):
    """The verdict must survive a database that has not been initialised."""
    import db

    monkeypatch.setattr(savecache, "PARSE_LOAD_AWARE", True)
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "empty.db"))
    db.reset_for_tests()
    try:
        assert savecache.load_verdict()["busy"] is False
    finally:
        db.reset_for_tests()


# ─── Defers on real evidence ─────────────────────────────


def test_a_struggling_server_defers_a_parse(loaded):
    sample(8.0)
    verdict = savecache.load_verdict()
    assert verdict["busy"] is True
    assert verdict["serverFps"] == 8.0
    assert "8 fps" in verdict["reason"]


def test_a_healthy_server_does_not(loaded):
    sample(55.0)
    assert savecache.load_verdict()["busy"] is False


def test_the_most_recent_reachable_sample_wins(loaded):
    """One bad minute followed by recovery should not keep deferring."""
    now = time.time()
    sample(5.0, ts=now - 120)
    sample(50.0, ts=now)
    assert savecache.load_verdict()["busy"] is False


# ─── An explicit request gets more latitude ──────────────


def test_a_forced_parse_has_a_lower_floor(loaded):
    """
    Someone pressed Refresh and is watching. Overriding them needs the server to
    be in real trouble, not merely busy.
    """
    sample(15.0)      # under the automatic floor of 20, over the forced floor of 12
    assert savecache.load_verdict(force=False)["busy"] is True
    assert savecache.load_verdict(force=True)["busy"] is False


def test_a_forced_parse_is_still_deferred_when_things_are_dire(loaded):
    sample(4.0)
    assert savecache.load_verdict(force=True)["busy"] is True


# ─── Wired into request_parse ────────────────────────────


def test_request_parse_defers_and_says_why(loaded, monkeypatch):
    """
    The refusal has to be legible. A Refresh button that silently does nothing is
    indistinguishable from a broken one.
    """
    monkeypatch.setattr(savecache, "PARSE_ENABLED", True)
    monkeypatch.setattr(savecache, "get_level_sav_path", lambda: "/nonexistent/Level.sav",
                        raising=False)
    sample(3.0)

    result = savecache.request_parse(force=True)
    assert result["started"] is False
    assert result.get("deferredForLoad") is True
    assert "fps" in result["reason"]


def test_the_load_check_happens_before_any_work(loaded, monkeypatch):
    """
    Cheapest possible response to a struggling server: do nothing, including not
    stat-ing the save file.
    """
    monkeypatch.setattr(savecache, "PARSE_ENABLED", True)
    calls = []

    def watched():
        calls.append(1)
        return "/nonexistent/Level.sav"

    monkeypatch.setattr(savecache, "get_level_sav_path", watched, raising=False)
    sample(2.0)
    savecache.request_parse(force=True)

    assert calls == []


def test_status_exposes_the_verdict(loaded):
    """So the UI can explain a deferral rather than leaving Refresh looking dead."""
    sample(6.0)
    status = savecache.status()
    assert status["loadAware"] is True
    assert status["load"]["busy"] is True
