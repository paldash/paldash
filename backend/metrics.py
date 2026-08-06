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
_last_cpu_stat: Optional[tuple[float, float]] = None   # (total jiffies, steal)
_last_net: Optional[tuple[float, int, int]] = None     # (timestamp, rx, tx)
_game_pid: Optional[int] = None                        # re-checked, never trusted


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


def swap() -> tuple[Optional[float], Optional[float]]:
    """
    `(used_mb, total_mb)` of swap, or `(None, None)`.

    **A box with no swap is a real answer and not the same as a box we could not
    read.** Both come back `None` here; the difference is expressed by
    `swap_total_mb` being 0 versus absent, and the UI shows the gauge only when
    the total is non-zero — a permanently empty swap bar is noise on the majority
    of servers that have none.
    """
    total_kb = free_kb = None
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("SwapTotal:"):
                    total_kb = float(line.split()[1])
                elif line.startswith("SwapFree:"):
                    free_kb = float(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None, None

    if total_kb is None or free_kb is None:
        return None, None
    return round((total_kb - free_kb) / 1024, 1), round(total_kb / 1024, 1)


def cpu_steal() -> Optional[float]:
    """
    Percentage of time the CPU was ready and the hypervisor ran someone else.

    **The one host signal nothing else in this dashboard can substitute for.** On
    a rented VPS a high steal figure says the stutter is the host being
    oversubscribed rather than anything the operator did — and without it, a
    server dropping frames looks like the operator's fault.

    Field 8 of the aggregate line in `/proc/stat`, as a share of total jiffies
    since the previous sample. Two readings are needed, so the first call returns
    `None` rather than a fabricated zero — the same rule `cpu_percent` follows
    and for the same reason: one sample is not a slow rate, it is no rate.

    `None` on a bare-metal host too, where the field exists and is always 0 —
    that is genuinely "no contention" rather than "unknown", so 0.0 is returned
    and only an unreadable `/proc/stat` gives `None`.
    """
    global _last_cpu_stat

    try:
        with open("/proc/stat") as f:
            fields = f.readline().split()
    except OSError:
        return None
    if not fields or fields[0] != "cpu" or len(fields) < 9:
        return None

    try:
        values = [float(v) for v in fields[1:9]]
    except ValueError:
        return None

    total = sum(values)
    steal = values[7]

    previous = _last_cpu_stat
    _last_cpu_stat = (total, steal)
    if previous is None:
        return None

    elapsed = total - previous[0]
    if elapsed <= 0:
        return None
    return round(max(0.0, (steal - previous[1]) / elapsed) * 100.0, 2)


def network() -> tuple[Optional[float], Optional[float]]:
    """
    `(rx_kb_per_sec, tx_kb_per_sec)` across every real interface.

    **Inside a container this is the CONTAINER's traffic, not the host's**, which
    is the same caveat `memory()` carries about cgroup limits — and it is the
    useful reading either way, since what an operator wants is whether the box is
    saturated, and the dashboard is on the same box.

    `lo` is excluded: loopback counts this process talking to itself, which on a
    dashboard that proxies every request to its own backend is most of the
    traffic and none of the interest.

    Two samples again, so the first returns `None`.
    """
    global _last_net

    rx = tx = 0
    found = False
    try:
        with open("/proc/net/dev") as f:
            for line in f.readlines()[2:]:
                name, _, rest = line.partition(":")
                name = name.strip()
                if not name or name == "lo":
                    continue
                parts = rest.split()
                if len(parts) < 9:
                    continue
                rx += int(parts[0])
                tx += int(parts[8])
                found = True
    except (OSError, ValueError, IndexError):
        return None, None
    if not found:
        return None, None

    now = time.monotonic()
    previous = _last_net
    _last_net = (now, rx, tx)
    if previous is None:
        return None, None

    elapsed = now - previous[0]
    if elapsed <= 0:
        return None, None
    return (
        round(max(0, rx - previous[1]) / elapsed / 1024, 1),
        round(max(0, tx - previous[2]) / elapsed / 1024, 1),
    )


def _process_matches(pid: int) -> bool:
    """Whether this pid is the game server, by its own command line."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmdline = f.read().replace(b"\0", b" ").decode("utf-8", "replace").lower()
    except OSError:
        return False
    return "palserver" in cmdline


def _find_game_pid() -> Optional[int]:
    """
    The game server's pid, cached until it exits.

    **A cached pid is re-checked rather than trusted**: pids are reused, and a
    metrics chart that silently started reporting some other process's memory
    would be worse than reporting none — it would look like the leak had
    stopped.

    Scans `/proc` directly rather than reusing `safety._probe_process`. Two
    reasons, both deliberate: that function costs 26.5 ms walking every process
    and is *skipped* whenever a cheaper signal already says "running", so it is
    not a reliable source of a pid; and `metrics` is standard-library only by
    design, while `safety` uses psutil.
    """
    global _game_pid

    if _game_pid is not None and _process_matches(_game_pid):
        return _game_pid

    _game_pid = None
    try:
        entries = os.listdir("/proc")
    except OSError:
        return None

    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        if _process_matches(pid):
            _game_pid = pid
            return pid
    return None


def game_memory() -> Optional[float]:
    """
    The game server process's resident memory in MB, or `None`.

    **This is the number the leak actually happens in.** `memory()` above reports
    the cgroup's usage, which under Docker is *this container* — the dashboard —
    and says nothing about the game beside it. An operator watching that chart to
    predict a crash is watching the wrong process.

    `None` is the ordinary answer in the normal deployment: the dashboard runs in
    its own container without a shared PID namespace, so the game's `/proc`
    entries are simply not visible. It must render as "not available" rather than
    0, which would read as a server using no memory at all.

    `VmRSS` rather than `VmSize`: resident is what the machine is actually
    holding, and virtual size on a 64-bit game process is a large number that
    means very little.
    """
    pid = _find_game_pid()
    if pid is None:
        return None
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(float(line.split()[1]) / 1024, 1)
    except (OSError, ValueError, IndexError):
        return None
    return None


def cpu_temperature() -> Optional[float]:
    """
    The hottest CPU thermal zone in °C, or `None`.

    **Absent under most virtualisation and in most containers**, so `None` is the
    ordinary answer rather than an error — and it must never be rendered as 0°C,
    which reads as a machine at freezing point rather than as a machine that does
    not report.

    Zones are filtered by type: `/sys/class/thermal` also exposes battery and
    wireless sensors on a laptop, and the hottest of everything is not the CPU.
    """
    best: Optional[float] = None
    try:
        zones = sorted(os.listdir("/sys/class/thermal"))
    except OSError:
        return None

    for zone in zones:
        if not zone.startswith("thermal_zone"):
            continue
        base = os.path.join("/sys/class/thermal", zone)
        try:
            with open(os.path.join(base, "type")) as f:
                kind = f.read().strip().lower()
        except OSError:
            continue
        if not any(w in kind for w in ("cpu", "core", "pkg", "x86", "soc")):
            continue
        milli = _read_first_number(os.path.join(base, "temp"))
        if milli is None:
            continue
        celsius = milli / 1000.0
        # A plausibility bound: some drivers report in °C already and others
        # expose a sentinel. A reading outside this is not a temperature.
        if not -40.0 < celsius < 150.0:
            continue
        best = celsius if best is None else max(best, celsius)
    return round(best, 1) if best is not None else None


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
    row["swap_used_mb"], row["swap_total_mb"] = swap()
    row["cpu_steal"] = cpu_steal()
    row["net_rx_kbs"], row["net_tx_kbs"] = network()
    row["cpu_temp_c"] = cpu_temperature()
    # The GAME's memory, not this container's. See `game_memory` — the leak an
    # operator is watching for happens in a process the cgroup figure above does
    # not describe.
    row["game_mem_mb"] = game_memory()

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
    # Added 2026-08-06. NULL means "could not read", never 0 — the same rule
    # `players` follows when the server is down, and it matters most for
    # `cpu_temp_c`, where a 0 reads as a machine at freezing point rather than
    # as one that does not report a temperature.
    "swap_used_mb", "swap_total_mb", "cpu_steal", "net_rx_kbs", "net_tx_kbs",
    "cpu_temp_c", "game_mem_mb",
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


def series(
    hours: int = 24, buckets: int = 120, now: Optional[int] = None
) -> dict[str, Any]:
    """
    Bucketed history for a chart.

    Averaging happens in SQL over a computed bucket rather than in Python over
    every row, so a 30-day window costs the same as an hour.

    `reachable` is averaged too, giving the *fraction* of each bucket the server
    answered. A bucket at 0.0 is an outage; anything below 1.0 is a partial one,
    which is exactly the shape of an intermittently crashing server and would be
    invisible if this were a boolean.

    **`buckets` is a resolution target, not a promised point count.** Bucket
    boundaries are aligned to absolute epoch time (`ts / width * width`) rather
    than measured back from now, so a chart's x positions stay still between
    refreshes instead of every boundary sliding a few seconds on each poll. The
    cost is that a window can straddle a boundary and return one extra point —
    correct, and worth it for a chart that does not jitter.

    `now` is injectable for the same reason `prune`'s is: a test that anchors its
    samples to real wall-clock time passes or fails depending on how close the
    clock happens to be to a bucket edge.
    """
    hours = max(1, min(int(hours), RETENTION_DAYS * 24))
    buckets = max(1, min(int(buckets), 1000))

    since = int((time.time() if now is None else now) - hours * 3600)
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
               AVG(reachable)      AS reachable,
               -- MAX for swap and steal because a spike is the finding: an
               -- average hides the minute the box was thrashing. Averages for
               -- throughput, which is a rate and reads wrong as a peak.
               MAX(swap_used_mb)   AS swap_used_mb,
               MAX(swap_total_mb)  AS swap_total_mb,
               MAX(cpu_steal)      AS cpu_steal,
               AVG(net_rx_kbs)     AS net_rx_kbs,
               AVG(net_tx_kbs)     AS net_tx_kbs,
               MAX(cpu_temp_c)     AS cpu_temp_c,
               -- MAX, because the question this series exists to answer is how
               -- close the leak got, not what it averaged.
               MAX(game_mem_mb)    AS game_mem_mb
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
                # All NULL-preserving: a bucket with no reading stays absent
                # rather than becoming 0, which for a temperature would draw a
                # machine at freezing point and for steal would claim a quiet
                # host we never measured.
                "swapUsedMb": _round(r["swap_used_mb"], 1),
                "swapTotalMb": _round(r["swap_total_mb"], 1),
                "cpuSteal": _round(r["cpu_steal"], 2),
                "netRxKbs": _round(r["net_rx_kbs"], 1),
                "netTxKbs": _round(r["net_tx_kbs"], 1),
                "cpuTempC": _round(r["cpu_temp_c"], 1),
                "gameMemMb": _round(r["game_mem_mb"], 1),
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
