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
# The floor a *forced* parse (someone pressed Refresh) still respects.
#
# `PARSE_MIN_INTERVAL` above only ever applied to automatic parses, and the
# Refresh button posts `force=true` — so the 15-minute floor was bypassed by the
# one caller most likely to hit it repeatedly. One parse at a time was enforced,
# but the moment it finished the next click started another, and on a busy
# dashboard several people pressing Refresh produced a continuous parse loop
# against the same unchanged save.
#
# Deliberately short. The operator asked and is watching, so this is about
# stopping a stampede, not about making them wait.
PARSE_FORCE_MIN_INTERVAL = int(os.environ.get("PARSE_FORCE_MIN_INTERVAL_SECONDS", "120"))
PARSE_TIMEOUT = int(os.environ.get("PARSE_TIMEOUT_SECONDS", "600"))  # 10 min
PARSE_INCLUDE_ITEMS = os.environ.get("PARSE_INCLUDE_ITEMS", "true").lower() == "true"
# Refuse to parse absurdly large saves unless explicitly raised.
PARSE_MAX_SIZE_MB = int(os.environ.get("PARSE_MAX_SIZE_MB", "1024"))

# ─── Load-aware throttling ───────────────────────────────
#
# Gameplay wins over dashboard responsiveness, so a parse gives way to a server
# that is already struggling.
#
# This gates the *start* of a parse and never interrupts one in flight. A parse
# that is already running has paid most of its cost, runs niced, and killing it
# wastes that work while freeing capacity only briefly — then the next request
# starts it again from nothing. Refusing to begin is the cheap, effective end.
#
# `false` disables the check rather than the parse, so an operator who does not
# want their parses deferred keeps everything else.
PARSE_LOAD_AWARE = os.environ.get("PARSE_LOAD_AWARE", "true").lower() != "false"
# Below this server FPS the game is visibly stuttering for players. Palworld's own
# target is 30; sustained figures under 20 are what players report as lag.
PARSE_MIN_SERVER_FPS = float(os.environ.get("PARSE_MIN_SERVER_FPS", "20"))
# A forced parse (someone pressed Refresh) is deferred only below this, lower
# bound. They asked explicitly and are watching; overriding them needs the server
# to be in real trouble, not merely busy.
PARSE_FORCE_MIN_SERVER_FPS = float(os.environ.get("PARSE_FORCE_MIN_SERVER_FPS", "12"))

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
    # Set when the on-disk cache was thrown away because an upgrade changed the
    # payload shape. Cleared by the first successful parse. Exists so "there is
    # no data" can say *why*, and so startup knows it must parse even when
    # PARSE_AUTO is off.
    "schemaStale": False,
}


def generation() -> int:
    """Which parse the current data came from. 0 means nothing has loaded."""
    return int(_state["generation"])


def _cache_schema() -> int:
    """The payload shape this build expects, read from the worker it spawns."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import parse_worker

        return int(parse_worker.SCHEMA_VERSION)
    except Exception:  # noqa: BLE001 - a missing worker is a bigger problem elsewhere
        return 0


def _load_from_disk() -> None:
    if not os.path.exists(_CACHE_FILE):
        return
    try:
        with open(_CACHE_FILE) as f:
            cached = json.load(f)

        # A cache written by an older build is discarded, not adapted.
        #
        # The alternative is serving a payload missing fields this build reads,
        # which does not raise anywhere — it surfaces as `undefined` in the API
        # and "NaN" in the UI, on a server whose only mistake was upgrading
        # without re-parsing.
        #
        # **Discarding is only half the job, and shipping only that half broke a
        # live server.** `PARSE_AUTO` is false by default, so nothing re-parses
        # on its own: throwing the cache away left the entire dashboard empty —
        # no Pals, no bases, no breeding, for every role — with no error and no
        # path back except somebody happening to press Refresh. A stale number is
        # bad; an empty dashboard that never recovers is worse.
        #
        # So the flag below is set, `status()` reports it, and the app kicks a
        # parse at startup regardless of `PARSE_AUTO`. Not started here, because
        # this runs at *import* time: `request_parse` consults the metrics table
        # through `db`, which the lifespan hook has not initialised yet.
        expected = _cache_schema()
        if cached.get("ok") and int(cached.get("schema") or 0) != expected:
            logger.warning(
                "Save cache was written by an older build (schema %s, expected "
                "%s). Discarding it and re-parsing — the dashboard will have no "
                "world data until that finishes.",
                cached.get("schema") or "none", expected,
            )
            _state["schemaStale"] = True
            return

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
        # So the UI can say "deferred because the server is busy" rather than
        # leaving Refresh looking like it silently did nothing.
        "loadAware": PARSE_LOAD_AWARE,
        # Why the world is empty, when it is. Without this the upgrade case is
        # indistinguishable from "nobody has ever pressed Refresh", and the two
        # need different reassurance.
        "schemaStale": bool(_state["schemaStale"]),
        "load": load_verdict(),
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
            _state["schemaStale"] = False

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


def load_verdict(force: bool = False) -> dict[str, Any]:
    """
    Whether the game server is currently too busy to start a parse.

    Reads the most recent metrics sample rather than probing the game itself: a
    probe on the request path adds latency to the very thing being protected, and
    a sample from up to a minute ago is a better signal anyway — one bad frame is
    noise, a bad minute is load.

    Fails **open**. An unreachable server, no samples yet, or a metrics table that
    does not exist all read as "fine to parse". The stakes here are the opposite
    way round from the corruption guard: refusing to parse forever because a
    signal is missing breaks the dashboard, while parsing during load costs some
    frames. Only positive evidence of a struggling server defers anything.
    """
    if not PARSE_LOAD_AWARE:
        return {"busy": False, "reason": "load-aware throttling is off"}

    floor = PARSE_FORCE_MIN_SERVER_FPS if force else PARSE_MIN_SERVER_FPS

    try:
        import db

        row = db.connect().execute(
            "SELECT ts, server_fps, reachable FROM metrics "
            "WHERE reachable = 1 AND server_fps IS NOT NULL ORDER BY ts DESC LIMIT 1"
        ).fetchone()
    except Exception:  # noqa: BLE001 - no table, no db, no opinion
        return {"busy": False, "reason": "no load data"}

    if row is None:
        return {"busy": False, "reason": "no load data"}

    # A stale sample says nothing about now. Two intervals of slack so a single
    # missed tick does not disable throttling.
    import metrics as metrics_module

    age = time.time() - float(row["ts"])
    if age > max(120, metrics_module.INTERVAL * 2):
        return {"busy": False, "reason": f"load data is {int(age)}s old"}

    fps = float(row["server_fps"])
    if fps >= floor:
        return {"busy": False, "reason": f"server at {fps:.0f} fps", "serverFps": fps}

    return {
        "busy": True,
        "serverFps": fps,
        "reason": (
            f"The server is at {fps:.0f} fps, below the {floor:.0f} fps floor. "
            "Parsing is deferred so it does not make the lag worse."
        ),
    }


def request_parse(force: bool = False) -> dict[str, Any]:
    """
    Kick off a background parse if one is warranted.

    Returns a small dict describing what happened, so the UI can explain itself
    ("using cached data from 4 minutes ago") instead of silently doing nothing.
    """
    if not PARSE_ENABLED:
        return {"started": False, "reason": "Save parsing is disabled (PARSE_ENABLED=false)"}

    # Before touching the filesystem. If the server is struggling the cheapest
    # possible response is to do nothing at all, and a save directory on a slow or
    # unmounted network volume can make even a stat cost real time.
    load = load_verdict(force=force)
    if load["busy"]:
        return {"started": False, "reason": load["reason"], "deferredForLoad": True,
                "serverFps": load.get("serverFps")}

    level_path = get_level_sav_path()
    if not level_path:
        return {"started": False, "reason": "Level.sav not found"}

    try:
        size_mb = os.path.getsize(level_path) / 1024 / 1024
    except OSError as e:
        # The path resolved a moment ago and is gone now — a restore, an unmounted
        # volume. Reporting it beats raising out of a status call.
        return {"started": False, "reason": f"Could not read Level.sav ({e.strerror})"}

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
        elif age is not None and age < PARSE_FORCE_MIN_INTERVAL:
            # The "already running" check above stops parses *overlapping*; this
            # stops them queueing nose-to-tail. Without it, three people pressing
            # Refresh in the same minute produced three consecutive full parses of
            # a save that had not changed between them.
            wait = int(PARSE_FORCE_MIN_INTERVAL - age)
            return {
                "started": False,
                "cooldown": True,
                "retryAfterSeconds": wait,
                "reason": (
                    f"A parse finished {int(age)}s ago. Refresh is available again "
                    f"in {wait}s — the data on screen is from that parse."
                ),
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


def recover_stale_schema() -> Optional[dict[str, Any]]:
    """
    Rebuild data thrown away because an upgrade changed the payload shape.

    Called once from the app's lifespan hook, after `db.init()` — `request_parse`
    reads the metrics table to decide whether the game server is too busy, and at
    import time that table does not exist yet.

    **Forced, and deliberately not gated on `PARSE_AUTO`.** That setting means
    "do not parse speculatively"; this is not speculative. The cache was
    discarded a moment ago and there is nothing to serve, so the choice is
    between one parse now and a dashboard that stays empty until a human notices
    and presses Refresh. Everything else still applies: one parse at a time, the
    size limit, and the load check — a struggling game server still wins, and the
    flag stays set so the next start tries again.
    """
    if not _state["schemaStale"] or _state["data"] is not None:
        return None
    logger.info("Re-parsing after an upgrade changed the save cache format")
    result = request_parse(force=True)
    if not result.get("started"):
        logger.warning(
            "Could not start the post-upgrade re-parse (%s). The dashboard has "
            "no world data until a parse succeeds — press Refresh.",
            result.get("reason"),
        )
    return result
