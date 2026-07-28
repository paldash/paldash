"""
Runtime access policy: how much the dashboard is allowed to change, and how much
guests are allowed to see.

Two independent dials:

  * `securityLevel` — a ladder of write permissions, from "touch nothing" up to
    "everything". This is about protecting the world.
  * `guestVisibility` — per-feature toggles for what a non-admin session can
    read. This is about not handing every visitor a map of where all the loot is.

Defaults come from the environment so an operator can lock the deployment down in
compose, and an admin can adjust things at runtime through the UI. Environment
variables set a *ceiling*: SECURITY_LEVEL=readonly in compose cannot be raised
from the web UI, so a compromised admin session cannot unlock writes that the
operator disabled.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

from savefiles import CACHE_DIR

logger = logging.getLogger(__name__)

POLICY_FILE = os.environ.get("POLICY_FILE", os.path.join(CACHE_DIR, "policy.json"))

# Write ladder, least to most permissive.
SECURITY_LEVELS = ("readonly", "safe", "full")

# Capabilities unlocked at each level (cumulative).
LEVEL_CAPABILITIES: dict[str, list[str]] = {
    "readonly": [],
    "safe": ["backup.manage", "settings.write", "save.sort.stackables"],
    "full": ["backup.manage", "settings.write", "save.sort.stackables",
             "save.sort.all", "save.edit.full"],
}

GUEST_VISIBILITY_KEYS = (
    "serverStatus",   # online/offline, FPS, player counts
    "onlinePlayers",  # live player list and map positions
    "bases",          # guild base locations
    "guilds",         # guild names and membership
    "mapObjects",     # placed objects: palboxes, farms, benches
    "chests",         # chest locations specifically
    "items",          # server-wide item totals
    "breeding",       # palbox contents and breeding planner
)

_lock = threading.Lock()
_cache: dict[str, Any] | None = None


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_level() -> str:
    """Operator-configured ceiling. Anything unrecognised falls back to 'safe'."""
    level = os.environ.get("SECURITY_LEVEL", "safe").strip().lower()
    if level not in SECURITY_LEVELS:
        logger.warning("Unknown SECURITY_LEVEL=%r, falling back to 'safe'", level)
        return "safe"
    return level


def default_policy() -> dict[str, Any]:
    return {
        "securityLevel": _env_level(),
        "guestVisibility": {
            "serverStatus": _env_bool("GUEST_SEE_SERVER_STATUS", True),
            "onlinePlayers": _env_bool("GUEST_SEE_PLAYERS", True),
            "bases": _env_bool("GUEST_SEE_BASES", True),
            "guilds": _env_bool("GUEST_SEE_GUILDS", True),
            "mapObjects": _env_bool("GUEST_SEE_MAP_OBJECTS", False),
            "chests": _env_bool("GUEST_SEE_CHESTS", False),
            "items": _env_bool("GUEST_SEE_ITEMS", False),
            "breeding": _env_bool("GUEST_SEE_BREEDING", False),
        },
    }


def _level_rank(level: str) -> int:
    try:
        return SECURITY_LEVELS.index(level)
    except ValueError:
        return 0


def load_policy() -> dict[str, Any]:
    """Stored policy merged over defaults, clamped to the environment ceiling."""
    global _cache
    with _lock:
        if _cache is not None:
            return json.loads(json.dumps(_cache))

    policy = default_policy()

    if os.path.exists(POLICY_FILE):
        try:
            with open(POLICY_FILE) as f:
                stored = json.load(f)
            if isinstance(stored.get("securityLevel"), str):
                policy["securityLevel"] = stored["securityLevel"]
            visibility = stored.get("guestVisibility")
            if isinstance(visibility, dict):
                for key in GUEST_VISIBILITY_KEYS:
                    if isinstance(visibility.get(key), bool):
                        policy["guestVisibility"][key] = visibility[key]
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not read policy file, using defaults: %s", e)

    # The environment is a ceiling, never a floor.
    ceiling = _env_level()
    if _level_rank(policy["securityLevel"]) > _level_rank(ceiling):
        logger.info(
            "Stored security level %r exceeds SECURITY_LEVEL=%r; clamping",
            policy["securityLevel"], ceiling,
        )
        policy["securityLevel"] = ceiling

    with _lock:
        _cache = policy
    return json.loads(json.dumps(policy))


def save_policy(update: dict[str, Any]) -> dict[str, Any]:
    """Persist a policy change. Returns the effective policy after clamping."""
    global _cache
    current = load_policy()

    level = update.get("securityLevel")
    if isinstance(level, str):
        if level not in SECURITY_LEVELS:
            raise ValueError(f"Unknown security level: {level}")
        ceiling = _env_level()
        if _level_rank(level) > _level_rank(ceiling):
            raise ValueError(
                f"SECURITY_LEVEL={ceiling} is set in the environment; "
                f"'{level}' cannot be enabled from the web UI. Change it in your "
                f"compose file and restart."
            )
        current["securityLevel"] = level

    visibility = update.get("guestVisibility")
    if isinstance(visibility, dict):
        for key in GUEST_VISIBILITY_KEYS:
            if isinstance(visibility.get(key), bool):
                current["guestVisibility"][key] = visibility[key]

    os.makedirs(os.path.dirname(POLICY_FILE) or ".", exist_ok=True)
    tmp = POLICY_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(current, f, indent=2)
    os.replace(tmp, POLICY_FILE)

    with _lock:
        _cache = current
    logger.info("Policy updated: level=%s", current["securityLevel"])
    return json.loads(json.dumps(current))


def allowed_capabilities() -> list[str]:
    """Write capabilities unlocked by the current security level."""
    return list(LEVEL_CAPABILITIES.get(load_policy()["securityLevel"], []))


def require_capability(capability: str) -> None:
    """Raise unless the current security level permits this operation."""
    if capability not in allowed_capabilities():
        level = load_policy()["securityLevel"]
        raise PermissionError(
            f"'{capability}' is not permitted at security level '{level}'. "
            f"Raise it in the Access tab, or via SECURITY_LEVEL in your compose file."
        )


def describe() -> dict[str, Any]:
    """Policy plus the metadata the settings UI needs to render itself."""
    policy = load_policy()
    return {
        **policy,
        "envCeiling": _env_level(),
        "levels": [
            {
                "id": "readonly",
                "label": "Read only",
                "description": "Nothing may modify save files or server config. Backups can still be created.",
            },
            {
                "id": "safe",
                "label": "Safe edits",
                "description": "Backups and restores, server settings, and sorting of plain stackable items. Equipment is never moved.",
            },
            {
                "id": "full",
                "label": "Full access",
                "description": "Everything, including sorting equipment and (once implemented) the general save editor.",
            },
        ],
        "visibilityKeys": list(GUEST_VISIBILITY_KEYS),
        "allowedCapabilities": allowed_capabilities(),
    }
