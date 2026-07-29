"""
Scheduled backups.

The scheduler is one thread and a persisted timestamp. What matters is that it
fires when due, does not pile up missed windows, and records failures visibly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import schedule as schedule_module


@pytest.fixture
def scheduled(fresh_db):
    schedule_module.init()
    return schedule_module


def test_disabled_by_default(scheduled):
    assert scheduled.get_schedule()["enabled"] is False
    assert scheduled.is_due() is False


def test_enabling_persists(scheduled):
    scheduled.set_schedule(enabled=True, frequency="hourly")
    state = scheduled.get_schedule()
    assert state["enabled"] is True
    assert state["frequency"] == "hourly"


def test_unknown_frequency_is_refused(scheduled):
    with pytest.raises(ValueError, match="Unknown frequency"):
        scheduled.set_schedule(frequency="fortnightly")


def test_due_immediately_when_never_run(scheduled):
    scheduled.set_schedule(enabled=True, frequency="daily")
    assert scheduled.is_due() is True


def test_not_due_straight_after_a_run(scheduled):
    scheduled.set_schedule(enabled=True, frequency="daily")
    scheduled._record_run("ok: abc")
    assert scheduled.is_due() is False


def test_due_again_once_the_interval_passes(scheduled):
    scheduled.set_schedule(enabled=True, frequency="hourly")
    scheduled._record_run("ok: abc")

    later = datetime.now(timezone.utc) + timedelta(hours=2)
    assert scheduled.is_due(now=later) is True


def test_a_long_gap_produces_one_backup_not_a_backlog(scheduled, monkeypatch):
    """
    A machine asleep for a week must not wake up and take 168 hourly backups.
    `is_due` is a boolean rather than a count of missed windows, so catching up
    means one backup and the clock reset — not a replay of the whole gap.
    """
    scheduled.set_schedule(enabled=True, frequency="hourly")
    scheduled._record_run("ok: abc")

    much_later = datetime.now(timezone.utc) + timedelta(days=7)
    assert scheduled.is_due(now=much_later) is True

    # The single catch-up run stamps the clock at the time it actually happened.
    monkeypatch.setattr(scheduled, "_now", lambda: much_later)
    scheduled._record_run("ok: def")

    assert scheduled.is_due(now=much_later) is False
    assert scheduled.is_due(now=much_later + timedelta(minutes=59)) is False
    assert scheduled.is_due(now=much_later + timedelta(hours=1)) is True


def test_disabled_schedule_is_never_due(scheduled):
    scheduled.set_schedule(enabled=False, frequency="hourly")
    assert scheduled.is_due() is False


def test_next_run_is_reported_when_enabled(scheduled):
    scheduled.set_schedule(enabled=True, frequency="daily")
    scheduled._record_run("ok: abc")
    assert scheduled.get_schedule()["nextRun"] is not None


def test_failures_are_recorded(scheduled, monkeypatch):
    """A schedule failing silently for a week is worse than no schedule."""
    import backup as backup_module

    def explode(**kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(backup_module, "create_backup", explode)
    scheduled.set_schedule(enabled=True, frequency="daily")

    result = scheduled.run_scheduled_backup()
    assert result["ok"] is False
    assert "disk full" in scheduled.get_schedule()["lastResult"]
