"""
`get_server_state()` skips the expensive process scan when a cheaper signal has
already voted "running". These tests exist to prove that is a *reordering* and
not a weakening of the corruption guard.

The scan costs 26.5 ms of a 34 ms `/api/health`, and every open dashboard polls
that endpoint continuously. But this is the module that decides whether it is
safe to write to a save file, so "it got faster" is not on its own an acceptable
justification — the verdict has to be identical.

It is, because `_probe_process` never returns "stopped": absence of a match
proves nothing when the dashboard may not share a PID namespace with the server.
Its only possible contributions are "running" (already established by the signal
that triggered the skip) and "unknown" (which `proven_stopped` ignores, since
that reads rest, tcp and activity only).
"""

import itertools

import pytest

import safety
from safety import Signal


VERDICTS = ("running", "stopped", "unknown")


def _patch(monkeypatch, rest, tcp, activity, process, scans):
    monkeypatch.setattr(safety, "_probe_rest_api", lambda: Signal("rest_api", rest, rest))
    monkeypatch.setattr(safety, "_probe_tcp", lambda: Signal("tcp_port", tcp, tcp))
    monkeypatch.setattr(
        safety, "_probe_save_activity", lambda: Signal("save_activity", activity, activity)
    )

    def scan():
        scans.append(1)
        return Signal("process", process, process)

    monkeypatch.setattr(safety, "_probe_process", scan)


@pytest.mark.parametrize("rest,tcp,activity", itertools.product(VERDICTS, repeat=3))
@pytest.mark.parametrize("process", ("running", "unknown"))
def test_verdict_matches_an_always_scanning_reference(monkeypatch, rest, tcp, activity, process):
    """
    Exhaustive over every combination the probes can produce.

    The reference is what the old code computed: all four signals gathered
    unconditionally. `running`, `editable` and `confidence` must agree.
    """
    scans: list[int] = []
    _patch(monkeypatch, rest, tcp, activity, process, scans)
    actual = safety.get_server_state()

    # The reference: process scanned every time, verdict derived the same way.
    #
    # Note the fail-closed default, which an earlier version of this model got
    # wrong: an *inconclusive* state is not "not running", it is "running". Only
    # positive proof from all three cheap signals yields editable, so the whole
    # verdict collapses to `running == not proven_stopped`.
    cheap_running = rest == "running" or tcp == "running" or activity == "running"
    any_running = cheap_running or process == "running"
    proven_stopped = (
        not any_running
        and rest == "stopped"
        and tcp == "stopped"
        and activity == "stopped"
    )

    assert actual.running is not proven_stopped
    assert actual.editable is proven_stopped


@pytest.mark.parametrize("signal_name", ("rest_api", "tcp_port", "save_activity"))
def test_scan_is_skipped_once_something_reports_running(monkeypatch, signal_name):
    verdicts = {"rest_api": "stopped", "tcp_port": "stopped", "save_activity": "stopped"}
    verdicts[signal_name] = "running"
    scans: list[int] = []
    _patch(monkeypatch, verdicts["rest_api"], verdicts["tcp_port"],
           verdicts["save_activity"], "unknown", scans)

    state = safety.get_server_state()
    assert state.running is True
    assert state.editable is False
    assert scans == [], "the process scan ran even though the verdict was already decided"


def test_inconclusive_still_fails_closed(monkeypatch):
    """
    The guarantee the whole module exists for: not knowing means "running".
    Skipping a probe must never turn an unknown into permission to write.
    """
    scans: list[int] = []
    _patch(monkeypatch, "unknown", "unknown", "unknown", "unknown", scans)
    state = safety.get_server_state()
    assert state.running is True
    assert state.editable is False


@pytest.mark.parametrize("rest,tcp,activity", itertools.product(("stopped", "unknown"), repeat=3))
def test_scan_always_runs_when_nothing_reports_running(monkeypatch, rest, tcp, activity):
    """
    The case that matters: nothing says running, so somebody may be about to
    edit a save. The expensive signal must not be skipped here for any reason.
    """
    scans: list[int] = []
    _patch(monkeypatch, rest, tcp, activity, "unknown", scans)
    safety.get_server_state()
    assert scans == [1], "the process scan was skipped while the server looked stopped"


def test_a_process_only_detection_still_blocks_writing(monkeypatch):
    """
    The scan's whole purpose: catching a server that is up but not yet
    listening. Skipping it in this situation would be the dangerous bug.
    """
    scans: list[int] = []
    _patch(monkeypatch, "stopped", "stopped", "stopped", "running", scans)

    state = safety.get_server_state()
    assert scans == [1]
    assert state.running is True
    assert state.editable is False, "a live process must veto editing"


def test_skipped_signal_says_why_rather_than_claiming_stopped(monkeypatch):
    """A skipped probe must never look like positive evidence of anything."""
    scans: list[int] = []
    _patch(monkeypatch, "running", "stopped", "stopped", "unknown", scans)

    state = safety.get_server_state()
    process = next(s for s in state.signals if s.name == "process")
    assert process.verdict == "unknown"
    assert "not scanned" in process.detail
