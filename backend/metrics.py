"""
Server metrics with history.

Samples the game's own `/v1/api/metrics`, plus host CPU/memory/disk, into the
`metrics` table on a timer. The dashboard has always shown *current* numbers; this
is what makes "the server has been degrading since Tuesday" answerable.

STORED RAW, NOT ROLLED UP
-------------------------
At the default 60-second interval, 30 days is about 43,000 rows. SQLite answers
that instantly, and it is far cheaper than maintaining downsampled tables that can
disagree with the raw ones. Bucketing happens at query time in `series()`, which
means changing how a chart is aggregated needs no migration and cannot corrupt
history.

A GAP IS DATA
-------------
When the game is unreachable the row is still written, with `reachable = 0` and
NULL game fields. This is deliberate: a chart that simply skipped those samples
would draw a smooth line straight through an outage. NULL renders as a gap, and a
gap is the most important thing on the chart.

For the same reason, `players` is never coerced to 0 when the server is down —
"nobody was playing" and "we could not ask" are different facts and must not share
a representation.

HOST NUMBERS ARE THE CONTAINER'S VIEW
-------------------------------------
CPU and memory come from `/proc` and, where present, the cgroup v2 files, so under
Docker they describe **this container's** limits rather than the whole machine.
That is the useful reading — the point is whether the dashboard is behaving, and
`cpus: 1.0` in compose means the host's 16 cores are not the denominator. Disk is
the filesystem holding the save directory, which is the one that can actually fill
up and break the server.

No psutil: standard library only, so nothing new to ship.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Optional

import db
import gameapi

logger = logging.getLogger(__name__)

ENABLED = os.environ.get("METRICS_ENABLED", "true").lower() != "false"
INTERVAL = int(os.environ.get("METRICS_INTERVAL_SECONDS", "60"))
RETENTION_DAYS = int(os.environ.get("METRICS_RETENTION_DAYS", "30"))
# Pruning walks one index and deletes; no reason to do it more than hourly.
PRUNE_EVERY = 3600

_thread: Optional[threading.Thread] = None
_stop = threading.Event()
_lock = threading.Lock()
_last_prune = 0.0
_last_cpu: Optional[tuple[float, float]] = None   # (timestamp, cpu_seconds)


# ─── Host sampling ───────────────────────────────────────


def _read_first_number(path: str) -> Optional[float]:
    try:
        with open(path) as f:
            return float(f.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def cpu_percent() -> Optional[float]:
    """
    CPU use since the previous sample, as a percentage of one core.

    Needs two readings to mean anything, so the first call always returns None
    rather than a fabricated figure. Uses the cgroup's own accounting when
    available, which is what makes the number reflect the container's share
    instead of the host's total.
    """
    global _last_cpu

    usage = _read_first_number("/sys/fs/cgroup/cpu.stat")   # v2: "usage_usec N"
    if usage is not None:
        seconds = usage / 1_000_000.0
    else:
        # Fall back to this process tree's own CPU time. Less complete than the
        # cgroup figure but available everywhere, including a bare-metal install.
        try:
            times = os.times()
            seconds = times.user + times.system + times.children_user + times.children_system
        except OSError:
            return None

    now = time.monotonic()
    previous = _last_cpu
    _last_cpu = (now, seconds)
    if previous is None:
        return None

    elapsed = now - previous[0]
    if elapsed <= 0:
        return None
    return round(max(0.0, (seconds - previous[1]) / elapsed) * 100.0, 1)


def memory() -> tuple[Optional[float], Optional[float]]:
    """(used_mb, total_mb) for this container, or the host if not containerised."""
    current = _read_first_number("/sys/fs/cgroup/memory.current")
    limit_raw = None
    try:
        with open("/sys/fs/cgroup/memory.max") as f:
            text = f.read().strip()
        limit_raw = None if text == "max" else float(text)
    except (OSError, ValueError):
        pass

    if current is not None:
        used = current / 1024 / 1024
        total = limit_raw / 1024 / 1024 if limit_raw else _host_memory_total()
        return round(used, 1), (round(total, 1) if total else None)

    # /proc/meminfo fallback
    total_kb = available_kb = None
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total_kb = float(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    available_kb = float(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None, None

    if total_kb is None or available_kb is None:
        return None, None
    return round((total_kb - available_kb) / 1024, 1), round(total_kb / 1024, 1)


def _host_memory_total() -> Optional[float]:
    total = None
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total = float(line.split()[1]) / 1024
                    break
    except (OSError, ValueError, IndexError):
        return None
    return total


def disk(path: Optional[str] = None) -> tuple[Optional[float], Optional[float]]:
    """
    (used_mb, free_mb) for the filesystem holding the save directory.

    That filesystem specifically, not `/`: it is the one whose filling up stops
    the game server from saving, which is a genuine way to lose a world.
    """
    from savefiles import get_default_world_dir

    target = path or get_default_world_dir() or "/"
    try:
        stat = os.statvfs(target)
    except OSError:
        return None, None

    block = stat.f_frsize
    total = stat.f_blocks * block
    free = stat.f_bavail * block
    return round((total - free) / 1024 / 1024, 1), round(free / 1024 / 1024, 1)


# ─── Sampling ────────────────────────────────────────────


def sample() -> dict[str, Any]:
    """Take one reading. Never raises — a failed sample is a row, not an error."""
    row: dict[str, Any] = {
        "ts": int(time.time()),
        "server_fps": None, "frame_time": None, "players": None,
        "max_players": None, "uptime": None, "reachable": 0,
    }

    try:
        game = gameapi.metrics()
        row.update({
            "server_fps": _number(game.get("serverfps")),
            "frame_time": _number(game.get("frametime")),
            "players": _integer(game.get("currentplayernum")),
            "max_players": _integer(game.get("maxplayernum")),
            "uptime": _integer(game.get("uptime")),
            "reachable": 1,
        })
    except gameapi.GameApiError as e:
        # Expected whenever the server is stopped. Debug, not warning: an operator
        # who stops the server for maintenance should not get a log full of these.
        logger.debug("Game metrics unavailable: %s", e)

    row["cpu_percent"] = cpu_percent()
    row["mem_used_mb"], row["mem_total_mb"] = memory()
    row["disk_used_mb"], row["disk_free_mb"] = disk()

    row.update(_world_counts())
    return row


def _world_counts() -> dict[str, Any]:
    """
    World size and entity counts from the last completed parse.

    Read from the cache rather than measured here — parsing on a metrics tick
    would defeat the entire point of the parse scheduler.
    """
    import savecache

    data = savecache.get_data(auto=False) or {}
    counts = data.get("counts") or {}
    return {
        "world_size_mb": _number(data.get("levelSizeMb")),
        "pal_count": _integer(counts.get("pals")),
        "base_count": _integer(counts.get("bases")),
    }


def _number(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None       # drop NaN


def _integer(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


_COLUMNS = (
    "ts", "server_fps", "frame_time", "players", "max_players", "uptime",
    "cpu_percent", "mem_used_mb", "mem_total_mb", "disk_used_mb", "disk_free_mb",
    "world_size_mb", "pal_count", "base_count", "reachable",
)


def store(row: dict[str, Any]) -> None:
    """
    Write one sample.

    `INSERT OR REPLACE` because `ts` is the primary key: two samples in the same
    second are the same sample, and a clock that steps backwards should overwrite
    rather than fail the whole tick.
    """
    placeholders = ", ".join("?" for _ in _COLUMNS)
    with db.transaction() as conn:
        conn.execute(
            f"INSERT OR REPLACE INTO metrics ({', '.join(_COLUMNS)}) VALUES ({placeholders})",
            tuple(row.get(name) for name in _COLUMNS),
        )


def prune(now: Optional[float] = None) -> int:
    """Drop samples past the retention window. Returns how many went."""
    cutoff = int((now if now is not None else time.time()) - RETENTION_DAYS * 86400)
    with db.transaction() as conn:
        cursor = conn.execute("DELETE FROM metrics WHERE ts < ?", (cutoff,))
        return cursor.rowcount or 0


# ─── Queries ─────────────────────────────────────────────


def series(hours: int = 24, buckets: int = 120) -> dict[str, Any]:
    """
    Bucketed history for a chart.

    Averaging happens in SQL over a computed bucket rather than in Python over
    every row, so a 30-day window costs the same as an hour.

    `reachable` is averaged too, giving the *fraction* of each bucket the server
    answered. A bucket at 0.0 is an outage; anything below 1.0 is a partial one,
    which is exactly the shape of an intermittently crashing server and would be
    invisible if this were a boolean.
    """
    hours = max(1, min(int(hours), RETENTION_DAYS * 24))
    buckets = max(1, min(int(buckets), 1000))

    since = int(time.time() - hours * 3600)
    width = max(1, (hours * 3600) // buckets)

    rows = db.connect().execute(
        f"""
        SELECT (ts / ?) * ? AS bucket,
               COUNT(*)            AS samples,
               AVG(server_fps)     AS server_fps,
               AVG(frame_time)     AS frame_time,
               MAX(players)        AS players_peak,
               AVG(players)        AS players_avg,
               AVG(cpu_percent)    AS cpu_percent,
               AVG(mem_used_mb)    AS mem_used_mb,
               MAX(mem_total_mb)   AS mem_total_mb,
               MIN(disk_free_mb)   AS disk_free_mb,
               MAX(world_size_mb)  AS world_size_mb,
               MAX(pal_count)      AS pal_count,
               MAX(base_count)     AS base_count,
               AVG(reachable)      AS reachable
          FROM metrics
         WHERE ts >= ?
      GROUP BY bucket
      ORDER BY bucket
        """,
        (width, width, since),
    ).fetchall()

    return {
        "hours": hours,
        "bucketSeconds": width,
        "retentionDays": RETENTION_DAYS,
        "intervalSeconds": INTERVAL,
        "enabled": ENABLED,
        "points": [
            {
                "ts": int(r["bucket"]),
                "samples": r["samples"],
                "serverFps": _round(r["server_fps"], 1),
                "frameTime": _round(r["frame_time"], 2),
                "playersPeak": r["players_peak"],
                "playersAvg": _round(r["players_avg"], 1),
                "cpuPercent": _round(r["cpu_percent"], 1),
                "memUsedMb": _round(r["mem_used_mb"], 1),
                "memTotalMb": _round(r["mem_total_mb"], 1),
                "diskFreeMb": _round(r["disk_free_mb"], 1),
                "worldSizeMb": _round(r["world_size_mb"], 2),
                "palCount": r["pal_count"],
                "baseCount": r["base_count"],
                # Fraction of the bucket the server answered, not a boolean.
                "reachable": _round(r["reachable"], 3),
            }
            for r in rows
        ],
    }


def _round(value: Any, digits: int) -> Optional[float]:
    return None if value is None else round(float(value), digits)


def summary() -> dict[str, Any]:
    """Coverage and extents, for the UI to say what it actually has."""
    row = db.connect().execute(
        """
        SELECT COUNT(*) AS samples, MIN(ts) AS oldest, MAX(ts) AS newest,
               AVG(reachable) AS uptime_fraction
          FROM metrics
        """
    ).fetchone()

    return {
        "enabled": ENABLED,
        "intervalSeconds": INTERVAL,
        "retentionDays": RETENTION_DAYS,
        "samples": row["samples"] or 0,
        "oldest": row["oldest"],
        "newest": row["newest"],
        # Over the retained window only. Labelled as such in the UI — this is not
        # an all-time figure and must not be presented as one.
        "uptimeFraction": _round(row["uptime_fraction"], 4),
    }


# ─── The sampling loop ───────────────────────────────────


def _loop() -> None:
    global _last_prune

    while not _stop.wait(INTERVAL):
        try:
            store(sample())
        except Exception as e:  # noqa: BLE001 - a bad tick must not end the loop
            logger.warning("Metrics sample failed: %s", e)

        try:
            if time.time() - _last_prune > PRUNE_EVERY:
                _last_prune = time.time()
                removed = prune()
                if removed:
                    logger.info("Pruned %d metrics samples past retention", removed)
        except Exception as e:  # noqa: BLE001
            logger.warning("Metrics prune failed: %s", e)


def start() -> None:
    """Begin sampling. Idempotent."""
    global _thread

    if not ENABLED:
        logger.info("Metrics history disabled (METRICS_ENABLED=false)")
        return

    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(target=_loop, name="metrics", daemon=True)
        _thread.start()
    logger.info(
        "Metrics sampling every %ds, retained %d days", INTERVAL, RETENTION_DAYS
    )


def stop() -> None:
    _stop.set()
