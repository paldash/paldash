"""
The corruption guard.

These are the most important tests in the project. A false "stopped" verdict is
the single failure mode that destroys somebody's world, so every ambiguous case
must resolve to "running". Each test below is a way that could go wrong.
"""

from __future__ import annotations

import urllib.error

import pytest

import safety
from safety import ServerRunningError, Signal


def _patch_signals(monkeypatch, rest, tcp, activity, process="unknown"):
    monkeypatch.setattr(safety, "_probe_rest_api", lambda: Signal("rest_api", rest, "t"))
    monkeypatch.setattr(safety, "_probe_tcp", lambda: Signal("tcp_port", tcp, "t"))
    monkeypatch.setattr(
        safety, "_probe_save_activity", lambda: Signal("save_activity", activity, "t")
    )
    monkeypatch.setattr(
        safety, "_probe_process", lambda: Signal("process", process, "t")
    )


@pytest.fixture(autouse=True)
def _default_flags(monkeypatch):
    monkeypatch.setattr(safety, "SAVE_READ_ONLY", False)
    monkeypatch.setattr(safety, "ALLOW_UNVERIFIED_EDITS", False)


# ─── The one case where writing is allowed ───────────────────────


def test_all_signals_stopped_permits_writes(monkeypatch):
    _patch_signals(monkeypatch, "stopped", "stopped", "stopped")
    state = safety.get_server_state()
    assert state.running is False
    assert state.editable is True
    assert state.confidence == "high"


# ─── Every way it must refuse ────────────────────────────────────


@pytest.mark.parametrize(
    "rest,tcp,activity",
    [
        ("running", "stopped", "stopped"),   # REST answered
        ("stopped", "running", "stopped"),   # port open
        ("stopped", "stopped", "running"),   # save written recently
        ("running", "running", "running"),   # unambiguously up
    ],
)
def test_any_running_signal_blocks_writes(monkeypatch, rest, tcp, activity):
    _patch_signals(monkeypatch, rest, tcp, activity)
    state = safety.get_server_state()
    assert state.running is True
    assert state.editable is False


@pytest.mark.parametrize(
    "rest,tcp,activity",
    [
        ("unknown", "stopped", "stopped"),
        ("stopped", "unknown", "stopped"),
        ("stopped", "stopped", "unknown"),   # e.g. save dir not mounted
        ("unknown", "unknown", "unknown"),   # no information at all
    ],
)
def test_inconclusive_fails_closed(monkeypatch, rest, tcp, activity):
    """Not proven stopped is treated as running. This is the whole design."""
    _patch_signals(monkeypatch, rest, tcp, activity)
    state = safety.get_server_state()
    assert state.running is True
    assert state.editable is False
    assert "Cannot prove" in state.reason


def test_process_signal_alone_cannot_authorise(monkeypatch):
    """
    The process probe never votes "stopped" — absence of a match proves nothing
    when we may not share a PID namespace. A stopped-looking process scan must
    not be enough on its own.
    """
    _patch_signals(monkeypatch, "unknown", "unknown", "unknown", process="unknown")
    assert safety.get_server_state().editable is False


def test_read_only_lock_overrides_everything(monkeypatch):
    monkeypatch.setattr(safety, "SAVE_READ_ONLY", True)
    _patch_signals(monkeypatch, "stopped", "stopped", "stopped")
    state = safety.get_server_state()
    assert state.running is False
    assert state.editable is False, "SAVE_READ_ONLY must win even when provably stopped"

    with pytest.raises(ServerRunningError, match="SAVE_READ_ONLY"):
        safety.assert_writable()


def test_allow_unverified_edits_opens_the_escape_hatch(monkeypatch):
    """Documented, off by default, and it must still respect the read-only lock."""
    monkeypatch.setattr(safety, "ALLOW_UNVERIFIED_EDITS", True)
    _patch_signals(monkeypatch, "unknown", "unknown", "unknown")
    state = safety.get_server_state()
    assert state.editable is True
    assert state.confidence == "low"

    monkeypatch.setattr(safety, "SAVE_READ_ONLY", True)
    assert safety.get_server_state().editable is False


def test_assert_writable_raises_when_running(monkeypatch):
    _patch_signals(monkeypatch, "running", "stopped", "stopped")
    with pytest.raises(ServerRunningError, match="Refusing to write"):
        safety.assert_writable()


def test_assert_writable_passes_when_stopped(monkeypatch):
    _patch_signals(monkeypatch, "stopped", "stopped", "stopped")
    safety.assert_writable()  # must not raise


# ─── REST probe specifics ────────────────────────────────────────


def test_http_401_counts_as_running(monkeypatch):
    """
    A 401 means something is listening and rejecting our password. That is a live
    server with the wrong credentials, not a stopped one — the exact confusion
    that made the original implementation unsafe.
    """

    def raise_401(*args, **kwargs):
        raise urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)

    monkeypatch.setattr(safety.urllib.request, "urlopen", raise_401)
    assert safety._probe_rest_api().verdict == "running"


def test_connection_refused_counts_as_stopped(monkeypatch):
    def raise_refused(*args, **kwargs):
        raise urllib.error.URLError(ConnectionRefusedError("refused"))

    monkeypatch.setattr(safety.urllib.request, "urlopen", raise_refused)
    assert safety._probe_rest_api().verdict == "stopped"


def test_probe_never_raises(monkeypatch):
    """A crashing probe must degrade to 'unknown', not take the request down."""

    def explode(*args, **kwargs):
        raise ValueError("something unexpected")

    monkeypatch.setattr(safety.urllib.request, "urlopen", explode)
    assert safety._probe_rest_api().verdict == "unknown"


# ─── Save-activity probe ─────────────────────────────────────────


def test_recent_save_write_means_running(monkeypatch, tmp_path):
    (tmp_path / "Level.sav").write_bytes(b"x")
    monkeypatch.setattr(safety, "SAVE_BASE_DIR", str(tmp_path))
    assert safety._probe_save_activity().verdict == "running"


def test_old_save_write_means_stopped(monkeypatch, tmp_path):
    import os
    import time

    sav = tmp_path / "Level.sav"
    sav.write_bytes(b"x")
    old = time.time() - (safety.SAVE_ACTIVITY_WINDOW + 600)
    os.utime(sav, (old, old))

    monkeypatch.setattr(safety, "SAVE_BASE_DIR", str(tmp_path))
    assert safety._probe_save_activity().verdict == "stopped"


def test_missing_save_dir_is_unknown_not_stopped(monkeypatch, tmp_path):
    """An unmounted volume must not read as 'server is off, go ahead and write'."""
    monkeypatch.setattr(safety, "SAVE_BASE_DIR", str(tmp_path / "nope"))
    assert safety._probe_save_activity().verdict == "unknown"


def test_empty_save_dir_is_unknown_not_stopped(monkeypatch, tmp_path):
    monkeypatch.setattr(safety, "SAVE_BASE_DIR", str(tmp_path))
    assert safety._probe_save_activity().verdict == "unknown"
