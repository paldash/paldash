"""
Server lifecycle: stopping, and actually getting it back.

The subtlety that bites people:

Palworld's REST API `/shutdown` and `/stop` kill the *game process*. They do not
restart it, and they know nothing about containers. What happens next depends
entirely on how your server is supervised:

  * If PalServer is the container's main process, it exits, the container exits,
    and Docker's `restart: unless-stopped` starts it again. Shutdown behaves like
    a restart.
  * If the image wraps PalServer in a supervisor/wrapper script that keeps
    running, the container stays up with no game server inside it. Shutdown means
    *stopped*, indefinitely, until someone intervenes.

The second case is common, so the dashboard must not claim it "restarted" the
server. We track the shutdown, watch for the server to come back, and report
honestly if it does not.

An actual restart requires control of the container, which this process
deliberately does not have by default. RESTART_COMMAND is the opt-in escape
hatch; see the README for the security trade-off.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import threading
import time
from typing import Any, Optional

from safety import get_server_state

logger = logging.getLogger(__name__)

# Optional command that genuinely restarts the server. Requires access to
# something that can do that (a Docker socket proxy, a systemd unit, an SSH key).
# Unset by default: no such access, no such button.
#
# Note the runtime image has **no `docker` CLI** — a command starting with
# `docker` fails with "not found". It does have `node` with a global fetch, so the
# Docker HTTP API is reachable without adding anything; docs/DEPLOYMENT.md §4 has
# the exact working commands.
#
# Split with shlex and run without a shell (see _run_configured), so a
# double-quoted script argument stays one argv element.
RESTART_COMMAND = os.environ.get("RESTART_COMMAND", "").strip()
# Stopping the *container* rather than just the game process. This is the one
# that makes save editing genuinely safe: with the container down, nothing can
# bring the server back mid-edit.
STOP_COMMAND = os.environ.get("STOP_COMMAND", "").strip()
# Bringing it back. With this configured, the full maintenance cycle
# (stop -> edit saves -> start) runs from the dashboard.
START_COMMAND = os.environ.get("START_COMMAND", "").strip()
RESTART_COMMAND_TIMEOUT = int(os.environ.get("RESTART_COMMAND_TIMEOUT", "120"))

# How long we keep watching for the server to return after a shutdown before
# calling it stopped-for-good.
RETURN_WATCH_SECONDS = int(os.environ.get("SERVER_RETURN_WATCH_SECONDS", "180"))

_lock = threading.Lock()
_state: dict[str, Any] = {
    "shutdownRequestedAt": None,   # epoch seconds
    "shutdownReason": None,
    "cameBack": None,              # True | False | None (still watching)
    "watching": False,
    "lastRestartCommand": None,
}


def restart_supported() -> bool:
    return bool(RESTART_COMMAND)


def stop_supported() -> bool:
    return bool(STOP_COMMAND)


def start_supported() -> bool:
    return bool(START_COMMAND)


def status() -> dict[str, Any]:
    """Lifecycle state for the UI, so it can explain what actually happened."""
    with _lock:
        snapshot = dict(_state)

    requested_at = snapshot["shutdownRequestedAt"]
    snapshot["secondsSinceShutdown"] = (
        int(time.time() - requested_at) if requested_at else None
    )
    snapshot["restartSupported"] = restart_supported()
    snapshot["stopSupported"] = stop_supported()
    snapshot["startSupported"] = start_supported()
    snapshot["returnWatchSeconds"] = RETURN_WATCH_SECONDS
    return snapshot


def _watch_for_return() -> None:
    """
    After a shutdown, poll until the server comes back or the window expires.

    This is what lets the UI say "your container is still running but the game
    process has not returned" instead of silently implying success.
    """
    deadline = time.time() + RETURN_WATCH_SECONDS
    # Give it a moment to actually go down first, so we do not immediately
    # observe the still-running server and declare success.
    time.sleep(15)

    while time.time() < deadline:
        if get_server_state().running:
            with _lock:
                _state["cameBack"] = True
                _state["watching"] = False
            logger.info("Server came back up after shutdown")
            return
        time.sleep(10)

    with _lock:
        _state["cameBack"] = False
        _state["watching"] = False
    logger.warning(
        "Server did not return within %ds. If your container is still running, "
        "its supervisor did not relaunch PalServer — restart the container.",
        RETURN_WATCH_SECONDS,
    )


def note_shutdown(reason: str = "") -> dict[str, Any]:
    """
    Record that a shutdown was issued and start watching for the server's return.

    Call this right after issuing /shutdown or /stop through the REST API.
    """
    with _lock:
        _state["shutdownRequestedAt"] = time.time()
        _state["shutdownReason"] = reason
        _state["cameBack"] = None
        if _state["watching"]:
            return status()
        _state["watching"] = True

    threading.Thread(target=_watch_for_return, name="server-return-watch", daemon=True).start()
    return status()


def run_restart_command() -> dict[str, Any]:
    """Restart the server container via RESTART_COMMAND."""
    return _run_configured(
        RESTART_COMMAND,
        "RESTART_COMMAND",
        "restart the server container",
    )


def run_start_command() -> dict[str, Any]:
    """
    Start the server container again after maintenance.

    Deliberately does not call note_shutdown — this is the opposite direction,
    and the health probes will observe the server returning on their own.
    """
    if not START_COMMAND:
        raise RuntimeError(
            "No START_COMMAND configured. Start the server container manually, "
            "e.g. `docker compose start palworld`."
        )
    result = _run_configured(START_COMMAND, "START_COMMAND", "start the server container")
    with _lock:
        _state["cameBack"] = None
        _state["watching"] = False
        _state["shutdownRequestedAt"] = None
    return result


def run_stop_command() -> dict[str, Any]:
    """
    Stop the server container via STOP_COMMAND.

    Stopping the container (not just the game process) is the recommended way to
    prepare for save edits: a stopped container cannot relaunch the server
    underneath you.
    """
    return _run_configured(STOP_COMMAND, "STOP_COMMAND", "stop the server container")


def _run_configured(command: str, name: str, description: str) -> dict[str, Any]:
    """
    Run an operator-configured command.

    The command comes from the environment, never from a request, and is split
    with shlex rather than run through a shell, so nothing a user types can be
    injected into it.
    """
    if not command:
        raise RuntimeError(
            f"No {name} configured. The dashboard cannot {description} by "
            "itself — see the README for how to enable this safely, or do it "
            "manually."
        )

    argv = shlex.split(command)
    logger.info("Running %s: %s", name, argv)

    try:
        proc = subprocess.run(
            argv,
            timeout=RESTART_COMMAND_TIMEOUT,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"{name} timed out after {RESTART_COMMAND_TIMEOUT}s") from e
    except FileNotFoundError as e:
        raise RuntimeError(f"{name} not found: {argv[0]}") from e

    with _lock:
        _state["lastRestartCommand"] = {
            "at": time.time(),
            "command": name,
            "returncode": proc.returncode,
            "stderr": (proc.stderr or "").strip()[-500:],
        }

    if proc.returncode != 0:
        raise RuntimeError(
            f"{name} exited {proc.returncode}: {(proc.stderr or '').strip()[-300:]}"
        )

    note_shutdown(name)
    return {"ok": True, "stdout": (proc.stdout or "").strip()[-500:]}
