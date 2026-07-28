"""
Safety module — decides whether it is safe to touch the save files.

RULE: FAIL CLOSED.

Writes are permitted only when the server is *positively proven* to be stopped.
If any signal says "running", or if we simply cannot tell, we report running and
refuse to write. A false "offline" verdict is the one failure mode that corrupts
a live world, so every ambiguous case resolves to "running".

The previous implementation returned False (= offline = writes allowed) whenever
the REST API was unreachable. A wrong admin password, a typo'd URL, a container
DNS hiccup or RESTAPIEnabled=False would all have unlocked the save editor on a
live server.
"""

from __future__ import annotations

import base64
import glob
import logging
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

PALWORLD_REST_URL = os.environ.get("PALWORLD_REST_URL", "http://127.0.0.1:8212")
PALWORLD_ADMIN_PASSWORD = os.environ.get("PALWORLD_ADMIN_PASSWORD", "")
SAVE_BASE_DIR = os.environ.get("SAVE_BASE_DIR", "/palworld/Pal/Saved/SaveGames/0")

# If any .sav changed within this many seconds, the server is almost certainly
# alive (it autosaves periodically). Generous by design.
SAVE_ACTIVITY_WINDOW = int(os.environ.get("SAVE_ACTIVITY_WINDOW_SECONDS", "300"))

# Escape hatch, off by default. When true, an inconclusive verdict counts as
# offline. Only for people running the dashboard with no REST API at all who
# accept the risk.
ALLOW_UNVERIFIED_EDITS = os.environ.get("ALLOW_UNVERIFIED_EDITS", "false").lower() == "true"

# Hard lock: never allow writes regardless of server state.
SAVE_READ_ONLY = os.environ.get("SAVE_READ_ONLY", "false").lower() == "true"

PROBE_TIMEOUT = float(os.environ.get("SAFETY_PROBE_TIMEOUT", "3"))


class ServerRunningError(Exception):
    """Raised when attempting to modify save files while the server may be running."""


@dataclass
class Signal:
    name: str
    verdict: str  # "running" | "stopped" | "unknown"
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "verdict": self.verdict, "detail": self.detail}


@dataclass
class ServerState:
    running: bool
    editable: bool
    confidence: str  # "high" | "medium" | "low"
    reason: str
    signals: list[Signal] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "editable": self.editable,
            "confidence": self.confidence,
            "reason": self.reason,
            "signals": [s.as_dict() for s in self.signals],
            "readOnlyLock": SAVE_READ_ONLY,
        }


# ─── Individual signals ──────────────────────────────────────────


def _probe_rest_api() -> Signal:
    """Hit /v1/api/info. A 200 or a 401 both prove the process is alive."""
    try:
        url = f"{PALWORLD_REST_URL.rstrip('/')}/v1/api/info"
        creds = base64.b64encode(f"admin:{PALWORLD_ADMIN_PASSWORD}".encode()).decode()
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Basic {creds}")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT) as resp:
            if resp.status == 200:
                return Signal("rest_api", "running", "REST API responded 200")
            return Signal("rest_api", "running", f"REST API responded {resp.status}")
    except urllib.error.HTTPError as e:
        # 401/403 means something is listening and rejecting us — that is a
        # live server with a wrong password, NOT a stopped server.
        return Signal("rest_api", "running", f"REST API responded {e.code} (server is up, auth may be wrong)")
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as e:
        return Signal("rest_api", "stopped", f"REST API unreachable ({type(e).__name__})")
    except Exception as e:  # noqa: BLE001 - never let a probe crash the verdict
        logger.warning("REST probe error: %s", e)
        return Signal("rest_api", "unknown", f"probe error: {e}")


def _probe_tcp() -> Signal:
    """A TCP connect to the REST port. Cheap corroboration of the HTTP probe."""
    try:
        parsed = urlparse(PALWORLD_REST_URL)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 8212
        with socket.create_connection((host, port), timeout=PROBE_TIMEOUT):
            return Signal("tcp_port", "running", f"{host}:{port} accepting connections")
    except (socket.timeout, ConnectionRefusedError, OSError) as e:
        return Signal("tcp_port", "stopped", f"REST port closed ({type(e).__name__})")
    except Exception as e:  # noqa: BLE001
        return Signal("tcp_port", "unknown", f"probe error: {e}")


def _probe_save_activity() -> Signal:
    """
    Recent .sav writes mean the server is autosaving, i.e. alive.

    This is the signal that works even with no REST API at all, and it is the
    reason the dashboard is safe when it shares a bind mount with the server.
    """
    if not os.path.isdir(SAVE_BASE_DIR):
        return Signal("save_activity", "unknown", f"save dir not found: {SAVE_BASE_DIR}")

    newest = 0.0
    newest_file = ""
    for path in glob.glob(os.path.join(SAVE_BASE_DIR, "**", "*.sav"), recursive=True):
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if mtime > newest:
            newest, newest_file = mtime, path

    if not newest:
        return Signal("save_activity", "unknown", "no .sav files found")

    age = time.time() - newest
    if age < SAVE_ACTIVITY_WINDOW:
        return Signal(
            "save_activity",
            "running",
            f"{os.path.basename(newest_file)} written {int(age)}s ago (< {SAVE_ACTIVITY_WINDOW}s)",
        )
    return Signal("save_activity", "stopped", f"no save writes for {int(age)}s")


def _probe_process() -> Signal:
    """
    Look for a PalServer process. Only meaningful when the dashboard shares a
    PID namespace with the server; absence of a match proves nothing, so this
    signal never votes "stopped".
    """
    try:
        import psutil  # type: ignore
    except ImportError:
        return Signal("process", "unknown", "psutil not available")

    try:
        for proc in psutil.process_iter(["name", "cmdline"]):
            name = (proc.info.get("name") or "").lower()
            cmdline = " ".join(proc.info.get("cmdline") or []).lower()
            if "palserver" in name or "palserver" in cmdline:
                return Signal("process", "running", f"found process: {name or 'PalServer'}")
    except Exception as e:  # noqa: BLE001
        return Signal("process", "unknown", f"scan error: {e}")

    return Signal("process", "unknown", "no PalServer process in this namespace (inconclusive)")


# ─── Verdict ─────────────────────────────────────────────────────


def get_server_state() -> ServerState:
    """
    Combine every signal into a fail-closed verdict.

    running  = any signal says "running"
    editable = every meaningful signal positively says "stopped"
    """
    signals = [_probe_rest_api(), _probe_tcp(), _probe_save_activity(), _probe_process()]

    running_votes = [s for s in signals if s.verdict == "running"]
    if running_votes:
        return ServerState(
            running=True,
            editable=False,
            confidence="high",
            reason=running_votes[0].detail,
            signals=signals,
        )

    # Nothing says "running". Now demand positive proof of "stopped".
    rest = next(s for s in signals if s.name == "rest_api")
    tcp = next(s for s in signals if s.name == "tcp_port")
    activity = next(s for s in signals if s.name == "save_activity")

    proven_stopped = (
        rest.verdict == "stopped"
        and tcp.verdict == "stopped"
        and activity.verdict == "stopped"
    )

    if proven_stopped:
        return ServerState(
            running=False,
            editable=not SAVE_READ_ONLY,
            confidence="high",
            reason="REST API down, port closed, and no save writes in the activity window",
            signals=signals,
        )

    # Inconclusive — e.g. save dir missing, or files present but never written.
    unknowns = [s.name for s in signals if s.verdict == "unknown"]
    reason = f"Cannot prove the server is stopped (inconclusive: {', '.join(unknowns) or 'none'})"

    if ALLOW_UNVERIFIED_EDITS:
        logger.warning("ALLOW_UNVERIFIED_EDITS is on — treating inconclusive state as stopped")
        return ServerState(
            running=False,
            editable=not SAVE_READ_ONLY,
            confidence="low",
            reason=reason + " — allowed anyway by ALLOW_UNVERIFIED_EDITS",
            signals=signals,
        )

    return ServerState(
        running=True,
        editable=False,
        confidence="low",
        reason=reason + " — assuming running to protect the save",
        signals=signals,
    )


def is_server_running() -> bool:
    """Backwards-compatible boolean form of the verdict."""
    return get_server_state().running


def assert_writable() -> None:
    """Raise unless it is provably safe to write to the save directory."""
    if SAVE_READ_ONLY:
        raise ServerRunningError(
            "Save writes are disabled by SAVE_READ_ONLY=true"
        )
    state = get_server_state()
    if not state.editable:
        raise ServerRunningError(f"Refusing to write: {state.reason}")


def require_server_offline(func):
    """Decorator that blocks execution unless writes are provably safe."""

    def wrapper(*args, **kwargs):
        assert_writable()
        return func(*args, **kwargs)

    wrapper.__name__ = func.__name__
    return wrapper
