"""
Which Palworld build is installed, and whether the bundled data still matches it.

The dashboard ships three files derived from game content: `gamedata.json.gz`
(items, Pals, skills), `effigies.json.gz` (396 positions and GUIDs) and
`worldobjects.json.gz` (35,687 positions). All three are **static per game build**.
A content update can move an ore node, add a Pal, or rename a settings key, and
nothing in the save file says so — the dashboard would keep confidently reporting
last patch's world.

So this module answers one question: *is what we shipped still true?* It cannot
fix a mismatch, and does not try. It detects one and says which extractor to
re-run, because a wrong-but-plausible map is worse than a banner saying the data
is stale.

**Three signals, in descending order of authority.** None is required.

  1. `steamapps/appmanifest_2394010.acf` → `buildid`. Steam's own record of what
     it installed, and exact. Present whenever the game's install directory is
     visible to this container.
  2. The main pak's `(size, mtime)`. Weaker — an unchanged fingerprint does not
     prove an unchanged build — but it moves on any content update, and it works
     when only part of the install is mounted.
  3. The game's REST `/v1/api/info` version string. Available only while the
     server is running, and it reports the *game* version rather than the build,
     which is coarser but human-readable.

**A missing signal is "unknown", never "unchanged".** Reporting a match we cannot
prove is exactly the failure this exists to prevent, so `status()` distinguishes
"matches", "differs" and "cannot tell", and the UI says which.

The build is recorded the first time it is seen so a *change* is detectable even
when the bundled data's own provenance is unknown — which it is for
`gamedata.json.gz`, extracted from a third-party data dump rather than from a pak.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

import db

logger = logging.getLogger(__name__)

APP_ID = "2394010"

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
PROVENANCE_PATH = os.path.join(DATA_DIR, "provenance.json")

# Where the game lives. Normally derived from the save directory — a save path of
# `<root>/Pal/Saved/SaveGames/0` puts the install root four levels up — because
# that mount is the one the dashboard is guaranteed to have.
INSTALL_DIR = os.environ.get("PALWORLD_INSTALL_DIR", "")
SAVE_BASE_DIR = os.environ.get("SAVE_BASE_DIR", "")

PAK_RELATIVE = os.path.join("Pal", "Content", "Paks", "Pal-LinuxServer.pak")

# How often the scheduled check actually looks, regardless of how often it is
# called.
#
# The cost is not what sets this — a check is ~0.05 ms, so even once a minute is
# 0.1 seconds of CPU per day. What sets it is that a Palworld update lands roughly
# monthly, and nothing bad happens while an ore position is stale for an hour. At a
# minute it would be ~43,000 checks per detection.
#
# The useful moment is **startup**, because an auto-updating server container
# updates and restarts, so the first check after boot catches almost every real
# update immediately. The interval is only a backstop for a dashboard left running
# across one.
CHECK_INTERVAL_SECONDS = int(
    os.environ.get("GAME_BUILD_CHECK_INTERVAL_SECONDS", str(6 * 3600))
)

# Monotonic, not wall-clock: a container's clock jumping (NTP settling after boot
# is the common case) would otherwise either skip a check or spin on every tick.
_last_check: Optional[float] = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS game_build (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    build_id    TEXT NOT NULL DEFAULT '',
    pak_stamp   TEXT NOT NULL DEFAULT '',
    first_seen  TEXT NOT NULL DEFAULT '',
    last_seen   TEXT NOT NULL DEFAULT '',
    -- The build in place when an operator last acknowledged the data as current.
    -- Separate from `build_id` so dismissing the banner is not the same act as
    -- observing a new build.
    acknowledged TEXT NOT NULL DEFAULT ''
);
"""


def init() -> None:
    with db.transaction() as conn:
        conn.executescript(SCHEMA)
        conn.execute("INSERT OR IGNORE INTO game_build (id) VALUES (1)")


# ─── Finding the install ─────────────────────────────────


def install_dir() -> str:
    """
    Best guess at the game's install root, or "".

    Checked for existence rather than returned hopefully: every caller here treats
    a missing directory as "no signal", and a path that does not exist would make
    each of them re-derive that.
    """
    if INSTALL_DIR and os.path.isdir(INSTALL_DIR):
        return INSTALL_DIR

    if SAVE_BASE_DIR:
        # <root>/Pal/Saved/SaveGames/0 -> <root>
        candidate = os.path.abspath(os.path.join(SAVE_BASE_DIR, "..", "..", "..", ".."))
        if os.path.isdir(candidate):
            return candidate

    return ""


def manifest_path() -> str:
    root = install_dir()
    if not root:
        return ""
    path = os.path.join(root, "steamapps", f"appmanifest_{APP_ID}.acf")
    return path if os.path.isfile(path) else ""


def pak_path() -> str:
    root = install_dir()
    if not root:
        return ""
    path = os.path.join(root, PAK_RELATIVE)
    return path if os.path.isfile(path) else ""


# ─── Reading the signals ─────────────────────────────────

_ACF_PAIR = re.compile(r'"([^"]+)"\s+"([^"]*)"')


def read_manifest(path: str = "") -> dict[str, str]:
    """
    Flat key/value pairs out of an `.acf` file.

    A deliberately loose parse rather than a VDF implementation: the file is
    Valve's nested key-value format, but every field wanted here (`buildid`,
    `LastUpdated`, `name`) is a scalar at the top level, and duplicate keys in
    nested `InstalledDepots` blocks do not collide with any of them. A real parser
    would be more code for no additional answer.
    """
    target = path or manifest_path()
    if not target:
        return {}
    try:
        with open(target, "r", errors="replace") as f:
            text = f.read(200_000)
    except OSError as e:
        logger.warning("Could not read %s: %s", target, e)
        return {}
    return {key: value for key, value in _ACF_PAIR.findall(text)}


def pak_stamp() -> str:
    """`<size>:<mtime>` for the main pak, or "". Cheap, and moves on any update."""
    path = pak_path()
    if not path:
        return ""
    try:
        stat = os.stat(path)
    except OSError:
        return ""
    return f"{stat.st_size}:{int(stat.st_mtime)}"


def game_version() -> str:
    """The running server's own version string, or "". Never raises."""
    try:
        import gameapi

        info = gameapi.info()
        return str(info.get("version") or "") if isinstance(info, dict) else ""
    except Exception:  # noqa: BLE001 - an unreachable server is not an error here
        return ""


def fingerprint() -> dict[str, Any]:
    """
    The cheap signals: two file reads and a stat, no network.

    This is the one that runs on a timer. Measured at well under a millisecond,
    which is what makes "check every minute, diff only when it changes" a sensible
    shape — the expensive part (re-extracting 35,687 positions from 9,977 cell
    packages) is prompted by a change here rather than performed on a schedule.

    Deliberately excludes `game_version()`. That is an HTTP request to the game
    server, and putting it here would mean a network round trip on every scheduler
    tick and every page load, to learn something the files already say.
    """
    manifest = read_manifest()
    build_id = str(manifest.get("buildid") or "")
    return {
        "buildId": build_id,
        "buildIdSource": "appmanifest" if build_id else "",
        "lastUpdated": str(manifest.get("LastUpdated") or ""),
        "pakStamp": pak_stamp(),
        "installDir": install_dir(),
        "manifestFound": bool(manifest_path()),
        "pakFound": bool(pak_path()),
    }


def detect(include_game: bool = False) -> dict[str, Any]:
    """
    `fingerprint()`, optionally plus the running server's version string.

    `include_game` is off by default and opt-in per caller, so nothing acquires a
    network dependency by accident.
    """
    signals = fingerprint()
    signals["gameVersion"] = game_version() if include_game else ""
    return signals


# ─── Provenance of the bundled data ──────────────────────


def provenance() -> dict[str, Any]:
    """
    What each bundled artifact was generated from.

    A missing or unreadable file is an empty dict, which reads downstream as
    "unknown provenance" — the same as a `gameBuild` of null. Both mean the same
    thing to an operator: we cannot prove this data matches your install.
    """
    try:
        with open(PROVENANCE_PATH) as f:
            loaded = json.load(f)
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


# ─── Comparing ───────────────────────────────────────────


def _record(build_id: str, stamp: str) -> dict[str, str]:
    """Remember the current build, returning what was known before."""
    from datetime import datetime, timezone

    init()
    row = db.connect().execute("SELECT * FROM game_build WHERE id = 1").fetchone()
    previous = {
        "buildId": row["build_id"] if row else "",
        "pakStamp": row["pak_stamp"] if row else "",
        "firstSeen": row["first_seen"] if row else "",
        "acknowledged": row["acknowledged"] if row else "",
    }

    now = datetime.now(timezone.utc).isoformat()
    changed = build_id != previous["buildId"] or stamp != previous["pakStamp"]

    if build_id or stamp:
        with db.transaction() as conn:
            if changed:
                # `first_seen` resets on a change: it dates *this* build, which is
                # what "updated 3 days ago" needs, not when the dashboard was
                # first run.
                conn.execute(
                    "UPDATE game_build SET build_id = ?, pak_stamp = ?, "
                    "first_seen = ?, last_seen = ? WHERE id = 1",
                    (build_id, stamp, now, now),
                )
            else:
                conn.execute(
                    "UPDATE game_build SET last_seen = ? WHERE id = 1", (now,)
                )

    return previous


def direction(before: str, after: str) -> str:
    """
    `up`, `down`, `same`, or `unknown` for a pair of build ids.

    Steam build ids increase monotonically, so this is meaningful — and the two
    directions mean different things. `up` is the ordinary case: the server
    updated, and the bundled positions are now suspect. `down` is a rollback,
    which invalidates the data just as much but suggests the operator pinned an
    older build deliberately, so re-extracting against it is the fix rather than
    waiting for a newer bundle.

    `unknown` when either side is not an integer — the ids are strings from a
    text file and nothing guarantees Valve keeps them numeric forever.
    """
    try:
        first, second = int(before), int(after)
    except (TypeError, ValueError):
        return "unknown"
    if second > first:
        return "up"
    if second < first:
        return "down"
    return "same"


def poll(force: bool = False) -> dict[str, Any]:
    """
    The scheduled check: notice a build change and record it. Cheap and quiet.

    Called from `schedule.py`'s once-a-minute tick but **self-rate-limited** to
    `CHECK_INTERVAL_SECONDS`, so the caller does not need its own timer and this
    module owns how often is sensible. The first call after startup always runs,
    which is the one that matters: an auto-updating server container updates and
    restarts, so boot is when a new build is actually there to find.

    **It never runs the extractors.** Walking 9,977 cell packages takes minutes and
    would be doing it beside a live game server, so a detected change raises the
    banner and points at `scripts/check-game-build.py`. The decision to spend that
    time stays with the operator.

    A change is **audited**, because "the world data went stale on the 14th" is a
    question an operator asks after noticing something odd on the map, and the
    answer needs to have been written down at the time.
    """
    global _last_check
    import time

    now = time.monotonic()
    if not force and _last_check is not None and now - _last_check < CHECK_INTERVAL_SECONDS:
        return {"checked": False, "changed": False, "reason": "not due"}
    _last_check = now

    signals = fingerprint()
    if not signals["buildId"] and not signals["pakStamp"]:
        return {"checked": False, "changed": False, "reason": "no signal available"}

    previous = _record(signals["buildId"], signals["pakStamp"])
    changed = bool(previous["buildId"] or previous["pakStamp"]) and (
        previous["buildId"] != signals["buildId"]
        or previous["pakStamp"] != signals["pakStamp"]
    )

    if changed:
        logger.warning(
            "Palworld build changed: %s -> %s (%s). Bundled position data may be "
            "stale; run scripts/check-game-build.py --extract to diff it.",
            previous["buildId"] or "unknown", signals["buildId"] or "unknown",
            direction(previous["buildId"], signals["buildId"]),
        )
        try:
            import audit

            audit.record(
                audit.POLICY_UPDATE, username="scheduler", role="system",
                target=f"game_build:{signals['buildId']}",
                detail={
                    "previousBuild": previous["buildId"],
                    "newBuild": signals["buildId"],
                    "direction": direction(previous["buildId"], signals["buildId"]),
                    "note": "Palworld updated; bundled position data may be stale",
                },
            )
        except Exception as e:  # noqa: BLE001 - detection must not depend on audit
            logger.warning("Could not audit the build change: %s", e)

    return {
        "checked": True,
        "changed": changed,
        "buildId": signals["buildId"],
        "previousBuildId": previous["buildId"],
        "direction": direction(previous["buildId"], signals["buildId"]),
    }


def status() -> dict[str, Any]:
    """
    Whether the bundled data still matches the installed game.

    `verdict` is one of:

      `current`  — the installed build matches what the data was built from
      `stale`    — it does not, and these artifacts need regenerating
      `unknown`  — no signal, or no recorded provenance to compare against

    `unknown` is a first-class answer rather than an optimistic `current`. The
    whole point of this module is to avoid asserting a match nobody verified.
    """
    # Fingerprint only: this backs a page load, and asking the game server for its
    # version string would make every dashboard refresh wait on a network round
    # trip to learn something two local files already say.
    signals = detect(include_game=False)
    previous = _record(signals["buildId"], signals["pakStamp"])
    prov = provenance()

    build_id = signals["buildId"]
    artifacts: list[dict[str, Any]] = []
    for name, entry in sorted(prov.items()):
        if not isinstance(entry, dict):
            continue
        built_from = entry.get("gameBuild")
        if not build_id or not built_from:
            state = "unknown"
        else:
            state = "current" if str(built_from) == build_id else "stale"
        artifacts.append({
            "artifact": name,
            "builtFromBuild": built_from,
            "source": entry.get("source", ""),
            "regenerateWith": entry.get("regenerateWith", ""),
            "note": entry.get("note", ""),
            "state": state,
        })

    stale = [a for a in artifacts if a["state"] == "stale"]
    unknown = [a for a in artifacts if a["state"] == "unknown"]

    if stale:
        verdict = "stale"
    elif artifacts and not unknown:
        verdict = "current"
    else:
        verdict = "unknown"

    # A build change is worth surfacing even when every artifact's provenance is
    # unknown: "the game updated since we last looked" is actionable on its own,
    # and it is the only signal available for data whose origin we cannot date.
    build_changed = bool(
        previous["buildId"] and build_id and previous["buildId"] != build_id
    )

    return {
        "verdict": verdict,
        "buildId": build_id,
        "previousBuildId": previous["buildId"],
        "buildChanged": build_changed,
        # `up` on an ordinary update, `down` on a deliberate rollback. Both make the
        # bundled positions suspect, but only one of them is waiting for us to ship
        # newer data — the other needs re-extraction against the pinned build.
        "buildDirection": direction(previous["buildId"], build_id),
        "acknowledgedBuild": previous["acknowledged"],
        # Dismissed only for the build it was dismissed for: a later update raises
        # it again rather than staying quiet because someone once clicked it.
        "acknowledged": bool(build_id) and previous["acknowledged"] == build_id,
        "signals": signals,
        "artifacts": artifacts,
        "staleArtifacts": [a["artifact"] for a in stale],
        "unknownArtifacts": [a["artifact"] for a in unknown],
        "reason": _reason(verdict, signals, stale, unknown, build_changed),
    }


def _reason(
    verdict: str,
    signals: dict[str, Any],
    stale: list[dict],
    unknown: list[dict],
    build_changed: bool,
) -> str:
    if verdict == "stale":
        names = ", ".join(a["artifact"] for a in stale)
        return (
            f"Palworld build {signals['buildId']} does not match the build these "
            f"files were generated from: {names}. Positions and game data may be "
            f"wrong until they are regenerated."
        )
    if verdict == "current":
        return f"Bundled game data matches installed build {signals['buildId']}."
    if not signals["manifestFound"]:
        return (
            "Cannot read the installed build — "
            f"steamapps/appmanifest_{APP_ID}.acf is not visible to this container. "
            "Mount the game directory or set PALWORLD_INSTALL_DIR to check "
            "automatically."
        )
    if build_changed:
        return (
            f"The game updated to build {signals['buildId']}. The bundled data's "
            "own origin is not recorded, so whether it is still accurate cannot be "
            "determined automatically."
        )
    names = ", ".join(a["artifact"] for a in unknown)
    return (
        f"Installed build is {signals['buildId']}, but these files do not record "
        f"which build they came from: {names}."
    )


def reset_for_tests() -> None:
    global _last_check
    _last_check = None


def acknowledge(build_id: str) -> None:
    """
    Mark the current build as reviewed, silencing the banner for it alone.

    Scoped to the build rather than a boolean so the next update raises it again.
    An operator who checked their data against build A has said nothing about B.
    """
    init()
    with db.transaction() as conn:
        conn.execute(
            "UPDATE game_build SET acknowledged = ? WHERE id = 1", (build_id,)
        )
