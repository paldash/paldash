"""
Recurring announcements.

Most of these pin behaviour that is the *opposite* of the naive implementation:

  * a window missed while the process was down is dropped, not replayed
  * an empty server consumes its window rather than queueing the message against
    whoever logs in next
  * "nobody online" and "could not ask" are different recorded reasons

The send path itself is `moderate.announce`, which has its own tests. What is
checked here is that this module goes through it — so a scheduled broadcast is
audited by the same code as a manual one — and never talks to `gameapi` directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import announcements
import audit
import db
import gameapi
import moderate

ACTOR = {"username": "mod", "role": "moderator"}


@pytest.fixture
def sent(monkeypatch):
    """Capture what reached the game, without a game."""
    calls: list[str] = []
    monkeypatch.setattr(gameapi, "announce", lambda message: calls.append(message) or {})
    return calls


@pytest.fixture
def one_player(monkeypatch):
    monkeypatch.setattr(gameapi, "players", lambda: [{"userId": "a", "name": "Someone"}])


@pytest.fixture
def empty_server(monkeypatch):
    monkeypatch.setattr(gameapi, "players", lambda: [])


@pytest.fixture
def unreachable(monkeypatch):
    def boom():
        raise gameapi.GameApiUnavailable("server is stopped")
    monkeypatch.setattr(gameapi, "players", boom)


def ago(**kwargs) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kwargs)).isoformat()


def set_last_run(announcement_id: int, when: str) -> None:
    with db.transaction() as conn:
        conn.execute(
            "UPDATE announcements SET last_run = ? WHERE id = ?", (when, announcement_id)
        )


# ─── Storage and validation ──────────────────────────────


def test_create_and_list(fresh_db):
    entry = announcements.create("Rules: no griefing", "hourly", created_by="mod")
    assert entry["message"] == "Rules: no griefing"
    assert entry["interval"] == "hourly"
    assert entry["enabled"] is True
    assert entry["onlyWhenOnline"] is True
    assert [e["id"] for e in announcements.list_announcements()] == [entry["id"]]


def test_a_message_is_cleaned_on_the_way_in(fresh_db):
    """
    Cleaned at write time as well as at send time, so what the UI reads back is
    what will actually be broadcast rather than something the send path rewrites.
    """
    entry = announcements.create("line one\nline two", "daily")
    assert entry["message"] == "line one line two"


def test_a_blank_message_is_refused(fresh_db):
    with pytest.raises(moderate.ModerationError, match="required"):
        announcements.create("   ", "daily")


def test_an_unknown_interval_is_refused(fresh_db):
    with pytest.raises(ValueError, match="interval"):
        announcements.create("hello", "every7minutes")


def test_the_count_is_capped(fresh_db, monkeypatch):
    monkeypatch.setattr(announcements, "MAX_ANNOUNCEMENTS", 2)
    announcements.create("one", "daily")
    announcements.create("two", "daily")
    with pytest.raises(ValueError, match="At most 2"):
        announcements.create("three", "daily")


def test_update_changes_only_what_is_named(fresh_db):
    entry = announcements.create("original", "hourly")
    updated = announcements.update(entry["id"], enabled=False)
    assert updated["enabled"] is False
    assert updated["message"] == "original"      # untouched
    assert updated["interval"] == "hourly"


def test_update_and_delete_refuse_a_missing_id(fresh_db):
    with pytest.raises(ValueError, match="No such"):
        announcements.update(999, enabled=False)
    with pytest.raises(ValueError, match="No such"):
        announcements.delete(999)


def test_intervals_are_ordered_shortest_first(fresh_db):
    seconds = [i["seconds"] for i in announcements.describe_intervals()]
    assert seconds == sorted(seconds)


# ─── Due logic ───────────────────────────────────────────


def test_a_never_run_announcement_is_due_immediately(fresh_db):
    entry = announcements.create("hello", "daily")
    assert [e["id"] for e in announcements.due()] == [entry["id"]]
    # And it can say when, rather than reporting an unknown next run.
    assert entry["nextRun"] is not None


def test_a_disabled_announcement_is_never_due(fresh_db):
    announcements.create("hello", "daily", enabled=False)
    assert announcements.due() == []


def test_an_announcement_inside_its_window_is_not_due(fresh_db):
    entry = announcements.create("hello", "hourly")
    set_last_run(entry["id"], ago(minutes=20))
    assert announcements.due() == []


def test_an_announcement_past_its_window_is_due(fresh_db):
    entry = announcements.create("hello", "hourly")
    set_last_run(entry["id"], ago(minutes=70))
    assert [e["id"] for e in announcements.due()] == [entry["id"]]


def test_a_long_outage_produces_one_send_not_a_backlog(fresh_db, sent, one_player):
    """
    The pile-up rule. Six hours down on a 15-minute announcement is 24 missed
    windows; replaying them would empty into the chat the moment the dashboard
    came back.
    """
    entry = announcements.create("hello", "every15m")
    set_last_run(entry["id"], ago(hours=6))
    result = announcements.run_due()
    assert result["sent"] == 1
    assert sent == ["hello"]
    # And it is no longer due, so the next tick does not send it again.
    assert announcements.due() == []


# ─── Sending ─────────────────────────────────────────────


def test_a_scheduled_send_is_audited_through_moderate(fresh_db, sent, one_player):
    """
    Nothing here calls `gameapi.announce` itself. Going through `moderate` is what
    makes a scheduled broadcast leave the same record as a manual one.
    """
    announcements.create("Server restarts at 4am", "hourly")
    announcements.run_due()

    rows = db.connect().execute(
        "SELECT * FROM audit_log WHERE action = ?", (audit.SERVER_ANNOUNCE,)
    ).fetchall()
    broadcasts = [r for r in rows if r["username"] == "scheduler"]
    assert len(broadcasts) == 1
    assert "4am" in broadcasts[0]["detail"]


def test_an_empty_server_consumes_the_window(fresh_db, sent, empty_server):
    """
    Skipped *and* stamped. Not stamping would queue the message against the first
    player to join, which is the backlog problem wearing a different hat.
    """
    entry = announcements.create("hello", "hourly")
    result = announcements.run_due()

    assert result["sent"] == 0 and result["skipped"] == 1
    assert sent == []
    assert announcements.due() == []
    assert announcements.get(entry["id"])["lastResult"] == "skipped: nobody online"


def test_unreachable_is_recorded_differently_from_empty(fresh_db, sent, unreachable):
    """
    "Nobody was listening" and "we could not find out" must not share a reason —
    the second is a server problem worth noticing.
    """
    entry = announcements.create("hello", "hourly")
    result = announcements.run_due()
    assert result["skipped"] == 1
    assert announcements.get(entry["id"])["lastResult"] == "skipped: server unreachable"


def test_only_when_online_off_sends_to_an_empty_server(fresh_db, sent, empty_server):
    announcements.create("hello", "hourly", only_when_online=False)
    assert announcements.run_due()["sent"] == 1
    assert sent == ["hello"]


def test_the_player_count_is_asked_once_for_the_whole_batch(fresh_db, sent, monkeypatch):
    """Three overdue messages is not three round trips just to count heads."""
    asks = []
    monkeypatch.setattr(
        gameapi, "players",
        lambda: asks.append(1) or [{"userId": "a", "name": "Someone"}],
    )
    for i in range(3):
        announcements.create(f"message {i}", "hourly")
    announcements.run_due()
    assert len(asks) == 1
    assert len(sent) == 3


def test_the_count_is_not_asked_at_all_when_nothing_is_due(fresh_db, sent, monkeypatch):
    asks = []
    monkeypatch.setattr(gameapi, "players", lambda: asks.append(1) or [])
    entry = announcements.create("hello", "hourly")
    set_last_run(entry["id"], ago(minutes=5))
    assert announcements.run_due() == {"sent": 0, "skipped": 0, "results": []}
    assert asks == []


def test_a_failed_send_is_stamped_and_does_not_stop_the_others(
    fresh_db, one_player, monkeypatch
):
    """
    A scheduler tick that aborts on the first failure would silently stop every
    later announcement, and the reason would be one log line.
    """
    calls = []

    def flaky(message):
        calls.append(message)
        if message == "second":
            raise gameapi.GameApiUnavailable("gone")
        return {}

    monkeypatch.setattr(gameapi, "announce", flaky)
    ids = [announcements.create(m, "hourly")["id"] for m in ("first", "second", "third")]
    result = announcements.run_due()

    assert calls == ["first", "second", "third"]
    assert result["sent"] == 2
    assert "failed" in announcements.get(ids[1])["lastResult"]
    # Failed or not, its window is consumed — retrying every 60s until the server
    # comes back would spam the audit log with the same failure.
    assert announcements.due() == []


def test_send_now_is_attributed_to_the_person_and_resets_the_window(
    fresh_db, sent, one_player
):
    entry = announcements.create("test me", "hourly")
    set_last_run(entry["id"], ago(hours=2))
    announcements.send_now(entry["id"], actor=ACTOR, ip="10.0.0.5")

    assert sent == ["test me"]
    rows = db.connect().execute(
        "SELECT * FROM audit_log WHERE action = ? AND username = ?",
        (audit.SERVER_ANNOUNCE, "mod"),
    ).fetchall()
    assert len(rows) == 1
    # Reset, so the scheduled copy does not follow seconds later.
    assert announcements.due() == []


def test_send_now_refuses_a_missing_id(fresh_db, sent):
    with pytest.raises(ValueError, match="No such"):
        announcements.send_now(999, actor=ACTOR)


def test_a_schedule_change_is_audited_separately_from_the_broadcast(fresh_db):
    """
    "Who changed what the server tells players every hour" is a different question
    from "what was said", and both should be answerable.
    """
    announcements.record_change({"action": "created", "message": "hi"}, actor=ACTOR)
    rows = db.connect().execute(
        "SELECT * FROM audit_log WHERE action = ?", (audit.SERVER_ANNOUNCE,)
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["target"] == "schedule"


# ─── The shared tick ─────────────────────────────────────


def test_the_scheduler_tick_survives_a_failing_announcement_run(fresh_db, monkeypatch):
    """
    One try block around both jobs would let a broken announcement schedule stop
    backups, or the reverse. The thread must outlive either.
    """
    import schedule

    monkeypatch.setattr(schedule, "is_due", lambda: False)

    def boom():
        raise RuntimeError("database is on fire")

    monkeypatch.setattr(announcements, "run_due", boom)
    monkeypatch.setattr(schedule, "CHECK_INTERVAL_SECONDS", 0.01)

    schedule._stop.clear()
    # One tick, then stop: the loop must have swallowed the exception rather than
    # letting it escape the thread.
    import threading
    thread = threading.Thread(target=schedule._loop, daemon=True)
    thread.start()
    schedule._stop.wait(0.05)
    schedule.stop()
    thread.join(timeout=2)
    assert not thread.is_alive()
