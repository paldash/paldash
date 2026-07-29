"""
Level.sav parse scheduling and caching.

This is the module that keeps the dashboard off your server's back.

Level.sav is rewritten on every autosave, so "re-parse whenever the mtime
changes" (what the old code did) means re-parsing every few minutes forever.
Instead:

  * parses happen on demand, or at most once per PARSE_MIN_INTERVAL;
  * exactly one parse can be in flight at a time;
  * the parse runs in a niced subprocess with a hard timeout;
  * results persist to disk, so a backend restart does not trigger a re-parse;
  * PARSE_ENABLED=false disables Level.sav parsing entirely, leaving the REST
    API features and the (cheap) per-player save reads fully working.

Stale data is served happily while a refresh runs in the background — the UI
shows the age.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from typing import Any, Optional

from savefiles import CACHE_DIR, get_level_sav_path

logger = logging.getLogger(__name__)

PARSE_ENABLED = os.environ.get("PARSE_ENABLED", "true").lower() != "false"
# When false (the default), nothing parses on its own. Reading save-derived data
# serves whatever is cached and never kicks off work; a parse happens only when
# someone presses Refresh (POST /api/refresh). This is the setting that keeps
# the dashboard completely idle between explicit requests.
PARSE_AUTO = os.environ.get("PARSE_AUTO", "false").lower() == "true"
PARSE_MIN_INTERVAL = int(os.environ.get("PARSE_MIN_INTERVAL_SECONDS", "900"))  # 15 min
PARSE_TIMEOUT = int(os.environ.get("PARSE_TIMEOUT_SECONDS", "600"))  # 10 min
PARSE_INCLUDE_ITEMS = os.environ.get("PARSE_INCLUDE_ITEMS", "true").lower() == "true"
# Refuse to parse absurdly large saves unless explicitly raised.
PARSE_MAX_SIZE_MB = int(os.environ.get("PARSE_MAX_SIZE_MB", "1024"))

_CACHE_FILE = os.path.join(CACHE_DIR, "level_cache.json")

_lock = threading.Lock()
_state: dict[str, Any] = {
    "data": None,
    "parsedAt": 0.0,
    "sourceMtime": 0.0,
    "running": False,
    "startedAt": 0.0,
    "lastError": None,
    "lastDurationSec": None,
    # Bumped every time `data` is replaced. `viewcache` keys derived views on
    # it, so anything computed from a parse is recomputed exactly once per
    # parse instead of once per request — and there is no invalidation call
    # anywhere to forget, because replacing the data *is* the invalidation.
    "generation": 0,
}


def generation() -> int:
    """Which parse the current data came from. 0 means nothing has loaded."""
    return int(_state["generation"])


def _load_from_disk() -> None:
    if not os.path.exists(_CACHE_FILE):
        return
    try:
        with open(_CACHE_FILE) as f:
            cached = json.load(f)
        if cached.get("ok"):
            _state["data"] = cached
            _state["parsedAt"] = cached.get("parsedAt", 0.0)
            _state["sourceMtime"] = cached.get("sourceMtime", 0.0)
            _state["generation"] += 1
            logger.info(
                "Loaded cached save data from disk (%s)",
                cached.get("counts", {}),
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not load cache file: %s", e)


_load_from_disk()


def _source_mtime() -> float:
    path = get_level_sav_path()
    try:
        return os.path.getmtime(path) if path else 0.0
    except OSError:
        return 0.0


def status() -> dict[str, Any]:
    """Cache state for the UI, without the payload."""
    data = _state["data"] or {}
    age = time.time() - _state["parsedAt"] if _state["parsedAt"] else None
    return {
        "enabled": PARSE_ENABLED,
        "hasData": bool(_state["data"]),
        "parsing": _state["running"],
        "parsedAt": _state["parsedAt"] or None,
        "ageSeconds": int(age) if age is not None else None,
        "stale": bool(_state["parsedAt"] and _source_mtime() > _state["sourceMtime"]),
        "lastError": _state["lastError"],
        "lastDurationSec": _state["lastDurationSec"],
        "minIntervalSeconds": PARSE_MIN_INTERVAL,
        "auto": PARSE_AUTO,
        "levelSizeMb": data.get("levelSizeMb"),
        "counts": data.get("counts", {}),
    }


def _run_worker() -> None:
    """Spawn parse_worker.py, wait, and fold the result into the cache."""
    started = time.time()
    out_path = os.path.join(CACHE_DIR, f"parse_{int(started)}.json")
    os.makedirs(CACHE_DIR, exist_ok=True)

    cmd = [sys.executable, os.path.join(os.path.dirname(__file__), "parse_worker.py"),
           "--out", out_path]
    if PARSE_INCLUDE_ITEMS:
        cmd.append("--items")

    source_mtime = _source_mtime()

    try:
        logger.info("Starting save parse worker (timeout %ds)", PARSE_TIMEOUT)
        proc = subprocess.run(
            cmd,
            timeout=PARSE_TIMEOUT,
            capture_output=True,
            text=True,
            cwd=os.path.dirname(__file__) or ".",
        )
        if proc.stderr:
            for line in proc.stderr.strip().splitlines()[-20:]:
                logger.info("worker: %s", line)

        if proc.returncode != 0 or not os.path.exists(out_path):
            raise RuntimeError(f"worker exited {proc.returncode}")

        with open(out_path) as f:
            result = json.load(f)

        if not result.get("ok"):
            raise RuntimeError(result.get("error", "unknown parse error"))

        duration = time.time() - started
        result["sourceMtime"] = source_mtime
        result["parsedAt"] = time.time()

        with _lock:
            _state["data"] = result
            _state["parsedAt"] = result["parsedAt"]
            _state["sourceMtime"] = source_mtime
            _state["lastError"] = None
            _state["lastDurationSec"] = round(duration, 1)
            _state["generation"] += 1

        try:
            os.replace(out_path, _CACHE_FILE)
        except OSError:
            pass

        logger.info("Parse finished in %.1fs: %s", duration, result.get("counts"))

    except subprocess.TimeoutExpired:
        msg = f"Parse timed out after {PARSE_TIMEOUT}s"
        logger.error(msg)
        with _lock:
            _state["lastError"] = msg
    except Exception as e:  # noqa: BLE001
        logger.error("Parse failed: %s", e)
        with _lock:
            _state["lastError"] = str(e)
    finally:
        with _lock:
            _state["running"] = False
        try:
            if os.path.exists(out_path):
                os.unlink(out_path)
        except OSError:
            pass


def request_parse(force: bool = False) -> dict[str, Any]:
    """
    Kick off a background parse if one is warranted.

    Returns a small dict describing what happened, so the UI can explain itself
    ("using cached data from 4 minutes ago") instead of silently doing nothing.
    """
    if not PARSE_ENABLED:
        return {"started": False, "reason": "Save parsing is disabled (PARSE_ENABLED=false)"}

    level_path = get_level_sav_path()
    if not level_path:
        return {"started": False, "reason": "Level.sav not found"}

    size_mb = os.path.getsize(level_path) / 1024 / 1024
    if size_mb > PARSE_MAX_SIZE_MB:
        return {
            "started": False,
            "reason": f"Level.sav is {size_mb:.0f}MB, above the {PARSE_MAX_SIZE_MB}MB limit "
                      f"(raise PARSE_MAX_SIZE_MB to override)",
        }

    with _lock:
        if _state["running"]:
            return {"started": False, "reason": "A parse is already running"}

        age = time.time() - _state["parsedAt"] if _state["parsedAt"] else None
        unchanged = _state["parsedAt"] and _source_mtime() <= _state["sourceMtime"]

        if not force:
            if unchanged:
                return {"started": False, "reason": "Save file unchanged since last parse"}
            if age is not None and age < PARSE_MIN_INTERVAL:
                return {
                    "started": False,
                    "reason": f"Last parse was {int(age)}s ago; minimum interval is "
                              f"{PARSE_MIN_INTERVAL}s",
                }

        _state["running"] = True
        _state["startedAt"] = time.time()

    threading.Thread(target=_run_worker, name="save-parse", daemon=True).start()
    return {"started": True, "reason": f"Parsing {size_mb:.0f}MB Level.sav in the background"}


def get_data(auto: bool = True) -> Optional[dict[str, Any]]:
    """
    Cached extraction result. Returns None only when nothing has ever been
    parsed.

    Only triggers a background refresh when PARSE_AUTO is explicitly enabled;
    by default reads are pure cache lookups and parsing is a manual action.
    """
    if auto and PARSE_AUTO:
        request_parse(force=False)
    return _state["data"]


def get_section(name: str, auto: bool = True) -> list:
    data = get_data(auto=auto)
    if not data:
        return []
    value = data.get(name)
    return value if isinstance(value, list) else []
