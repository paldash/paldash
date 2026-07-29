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

# Who may see world content a player has NOT discovered yet — the 174 fast-travel
# points and 396 effigies come from bundled game data, so the dashboard knows
# where all of them are regardless of what anyone has found.
#
# This is a taste question, not a security one, which is exactly why it is
# configurable rather than decided here. On a server run as a shared exploration
# save, handing every player a complete effigy map removes the game. On a server
# where people just want the collectibles, hiding them is an obstacle. Both are
# legitimate, so the operator picks.
#
# The value is either a sentinel or a **role name**, and a role name means "this
# role and anything above it", using the same rank ladder `roles.py` already
# defines. That is deliberately finer-grained than a capability check: the
# operator may want Players to see everything on a casual server, or restrict it
# to Moderator on a competitive one, and neither maps to an existing capability.
#
#   everyone      — anyone who can see the map, including guests
#   <role name>   — that role's rank and above (readonly, player, trusted,
#                   moderator, admin, owner)
#   nobody        — never sent to any session, at any role
#
# Everyone always sees their *own* discoveries; this only governs the
# undiscovered half.
DISCOVERY_SENTINELS = ("everyone", "nobody")
DEFAULT_DISCOVERY = "trusted"


def discovery_choices() -> tuple[str, ...]:
    import roles

    return DISCOVERY_SENTINELS[:1] + roles.ASSIGNABLE_ROLES + DISCOVERY_SENTINELS[1:]


def is_discovery_level(value: Any) -> bool:
    import roles

    return isinstance(value, str) and (
        value in DISCOVERY_SENTINELS or roles.is_role(value)
    )


def may_see_undiscovered(role: str, level: str) -> bool:
    """Whether `role` clears the configured threshold."""
    import roles

    if level == "everyone":
        return True
    if level == "nobody" or not roles.is_role(role):
        return False
    return roles.ROLES[role]["rank"] >= roles.ROLES.get(level, {"rank": 99})["rank"]

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


def _env_discovery() -> str:
    level = os.environ.get("DISCOVERY_VISIBILITY", DEFAULT_DISCOVERY).strip().lower()
    if not is_discovery_level(level):
        logger.warning(
            "Unknown DISCOVERY_VISIBILITY=%r, falling back to %r. Known: %s",
            level, DEFAULT_DISCOVERY, ", ".join(discovery_choices()),
        )
        return DEFAULT_DISCOVERY
    return level


def default_policy() -> dict[str, Any]:
    return {
        "securityLevel": _env_level(),
        "discoveryVisibility": _env_discovery(),
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


def _describe_discovery_levels() -> list[dict[str, Any]]:
    """The threshold choices, for a UI to render. Roles come from `roles.py`."""
    import roles

    out: list[dict[str, Any]] = [{
        "id": "everyone", "label": "Everyone",
        "description": "Anyone who can see the map, including guests, also sees "
                       "undiscovered fast-travel points and effigies.",
    }]
    for name in roles.ASSIGNABLE_ROLES:
        role = roles.ROLES[name]
        out.append({
            "id": name,
            "label": f"{role['label']} and above",
            "description": f"Ranks below {role['label']} see only what they have "
                           "found themselves.",
        })
    out.append({
        "id": "nobody", "label": "Nobody",
        "description": "Undiscovered locations are never sent to any session. "
                       "Everyone sees only their own discoveries.",
    })
    return out


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
            if is_discovery_level(stored.get("discoveryVisibility")):
                policy["discoveryVisibility"] = stored["discoveryVisibility"]
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

    discovery = update.get("discoveryVisibility")
    if isinstance(discovery, str):
        if not is_discovery_level(discovery):
            raise ValueError(
                f"Unknown discovery visibility: {discovery}. "
                f"Known: {', '.join(discovery_choices())}"
            )
        current["discoveryVisibility"] = discovery

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
        "discoveryLevels": _describe_discovery_levels(),
        "allowedCapabilities": allowed_capabilities(),
    }
