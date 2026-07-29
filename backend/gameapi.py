"""
A client for the game server's own REST API, from the backend.

WHY THIS EXISTS AT ALL
----------------------
The Next.js proxy has always been able to reach the game's REST API, and it
already gates POSTs on `server.control`. So kick, ban, announce and force-save
were *reachable* before this module. What they were not, is **audited** — the
proxy has no audit call, and it cannot sensibly have one, because
`backend/audit.py` owns the SQLite database and the Python process holds that file
exclusively.

An unaudited kick is the wrong shape for this project. "Every mutating action is
audited" is a rule the rest of the codebase keeps, and moderation actions are
exactly the ones an operator later needs a record of — who banned whom, and when.

So administrative commands go **backend -> game**, not **browser -> proxy ->
game**, and the audit record is written by the same process that issues the
command. The proxy keeps serving reads (`info`, `metrics`, `players`), where there
is nothing to record.

WHAT THIS IS NOT
----------------
Not a replacement for `safety.py`. That module deliberately runs its own probe
with its own timeout and its own fail-closed logic, because it answers "is it safe
to write to a save file" and must not depend on anything that could be made to say
"stopped" more easily. Keep them separate.

TIMEOUTS AND FAILURE
--------------------
`urllib` from the standard library, matching `safety.py` — no new dependency, and
the container must work offline on a LAN.

A failed command raises. It is never reported as success and never retried
automatically: retrying a `kick` that may have already landed is worse than
telling the operator it failed and letting them look.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import socket
import urllib.error
import urllib.request
from typing import Any, Optional

logger = logging.getLogger(__name__)

REST_URL = os.environ.get("PALWORLD_REST_URL", "http://127.0.0.1:8212")
ADMIN_PASSWORD = os.environ.get("PALWORLD_ADMIN_PASSWORD", "")
TIMEOUT = int(os.environ.get("GAME_API_TIMEOUT_SECONDS", "10"))


class GameApiError(Exception):
    """The game server refused, was unreachable, or answered unusably."""


class GameApiUnavailable(GameApiError):
    """Nothing answered. Distinguished so callers can say "server is down"."""


def configured() -> bool:
    """Whether an admin password is set. Without one every command 401s."""
    return bool(ADMIN_PASSWORD)


def _request(path: str, method: str = "GET", payload: Optional[dict] = None) -> Any:
    url = f"{REST_URL.rstrip('/')}/v1/api/{path.lstrip('/')}"
    body = json.dumps(payload).encode() if payload is not None else None

    request = urllib.request.Request(url, data=body, method=method)
    creds = base64.b64encode(f"admin:{ADMIN_PASSWORD}".encode()).decode()
    request.add_header("Authorization", f"Basic {creds}")
    request.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read().decode("utf-8", "replace").strip()
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace").strip()[:200]
        except Exception:  # noqa: BLE001
            pass
        if e.code in (401, 403):
            raise GameApiError(
                "The game server rejected the admin password. Check "
                "PALWORLD_ADMIN_PASSWORD matches the server's ADMIN_PASSWORD."
            ) from e
        raise GameApiError(f"Game server returned {e.code}{': ' + detail if detail else ''}") from e
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as e:
        raise GameApiUnavailable(
            f"Could not reach the game server at {REST_URL} ({type(e).__name__}). "
            "It may be stopped, or REST_API_ENABLED may be off."
        ) from e

    if not raw:
        # Several of these endpoints answer 200 with an empty body on success.
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # The game answers some commands with a bare string. Not an error.
        return {"message": raw[:500]}


# ─── Reads ───────────────────────────────────────────────


def info() -> dict:
    return _request("info")


def metrics() -> dict:
    return _request("metrics")


def players() -> list[dict]:
    data = _request("players")
    found = data.get("players") if isinstance(data, dict) else None
    return found if isinstance(found, list) else []


# ─── Commands ────────────────────────────────────────────


def announce(message: str) -> dict:
    return _request("announce", "POST", {"message": message})


def kick(userid: str, message: str = "") -> dict:
    return _request("kick", "POST", {"userid": userid, "message": message})


def ban(userid: str, message: str = "") -> dict:
    return _request("ban", "POST", {"userid": userid, "message": message})


def unban(userid: str) -> dict:
    return _request("unban", "POST", {"userid": userid})


def save() -> dict:
    """Force a world save. The one command here that touches the save files."""
    return _request("save", "POST")


def shutdown(seconds: int = 30, message: str = "") -> dict:
    return _request("shutdown", "POST", {"waittime": seconds, "message": message})


def stop() -> dict:
    """
    Stop the game process immediately, with no warning to players.

    Distinct from `shutdown`, which counts down and announces. This one loses
    everything since the last autosave, so callers should be sure the operator
    meant it.
    """
    return _request("stop", "POST")
