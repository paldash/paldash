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

# The two kinds of discoverable location, separately settable.
#
# `discoveryVisibility` above stays the default for both; these are overrides,
# so an existing policy keeps working and nothing changes silently.
#
# **They are split for the same reason `worldObjectVisibility` is per-category:
# they are not equivalent.** A fast-travel point is navigation infrastructure —
# knowing where one is costs an operator almost nothing and saves a player a lot
# of walking. An effigy is a collectible, and a complete map of all 396 removes
# the hunt entirely. Grouping them forced an operator who wanted convenient
# travel and an intact collectathon to choose one.
#
# Keeping them grouped while ore, chests, dungeons and fishing spots each get
# their own dial would also just be inconsistent: the argument for splitting
# those is word-for-word the argument here.
DISCOVERY_CATEGORIES: tuple[str, ...] = ("fastTravel", "effigies")

DISCOVERY_CATEGORY_LABELS: dict[str, str] = {
    "fastTravel": "Fast-travel points",
    "effigies": "Lifmunk effigies",
}

# Who may see guild bases they are not a member of.
#
#   everyone      — anyone who can see the map
#   own           — only your own guild's bases
#   <role name>   — that role's rank and above sees everything; below that,
#                   only their own guild's
#
# **Defaults to `own`**, for the same reason per-player privacy defaults to its
# most private mode: `privacy.py` protects *accounts*, and a player who has
# never signed into the dashboard has no row in `users` and therefore no privacy
# setting at all. Without this, everyone's bases were visible to every signed-in
# Player until each of them independently created an account — which is exactly
# the "exposed until you discover the setting exists" failure the privacy
# default was written to avoid.
#
# Like `discoveryVisibility`, this is a taste question about how a server is run
# rather than a security one, so it is configurable rather than hardcoded. Staff
# ranks are unaffected: moderation cannot work through a filter.
BASE_SENTINELS = ("everyone", "own")
DEFAULT_BASE_VISIBILITY = "own"

# Who may see figures covering the **whole server** rather than their own things.
#
#   everyone      — anyone with the tab
#   <role name>   — that rank and above
#   own           — nobody; everyone is scoped to their own guild/character
#
# `admin` by default. A server-wide item total is a useful operations number and
# a poor player-facing one: it answers "what exists on this server", which is a
# question about the server rather than about you, and it discloses every
# guild's holdings in one figure. Below the threshold the same endpoints scope to
# the caller instead of refusing, because an empty tab teaches nothing.
DEFAULT_SERVER_TOTALS = "admin"

# Who may see **everyone's** Pals in the breeding planner, rather than their own.
#
# `trusted` by default: the planner is far more useful across a whole server, and
# Trusted is already the "may see other players in detail" rank. A plain Player
# gets the same planner scoped to their own box, which is the common case anyway.
DEFAULT_ALL_PALS = "trusted"


def is_scope_level(value: Any) -> bool:
    import roles

    return isinstance(value, str) and (
        value in BASE_SENTINELS or roles.is_role(value)
    )


def meets_scope(role: str, level: str) -> bool:
    """
    Whether `role` clears a scope threshold.

    Shares `may_see_all_bases`' vocabulary deliberately — `everyone`, a role
    name, or `own` meaning nobody clears it — so an operator learns one set of
    words rather than four.
    """
    return may_see_all_bases(role, level)


def base_visibility_choices() -> tuple[str, ...]:
    import roles

    return BASE_SENTINELS[:1] + roles.ASSIGNABLE_ROLES + BASE_SENTINELS[1:]


def is_base_visibility(value: Any) -> bool:
    import roles

    return isinstance(value, str) and (
        value in BASE_SENTINELS or roles.is_role(value)
    )


def may_see_all_bases(role: str, level: str) -> bool:
    """
    Whether `role` sees every guild's bases, or only their own guild's.

    `own` means nobody clears it by rank — which is deliberate and is what makes
    the setting meaningful. Staff are exempted by the *caller*, not here, so this
    stays a pure threshold test.
    """
    import roles

    if level == "everyone":
        return True
    if level == "own" or not roles.is_role(role):
        return False
    return roles.ROLES[role]["rank"] >= roles.ROLES.get(level, {"rank": 99})["rank"]


def discovery_choices() -> tuple[str, ...]:
    import roles

    return DISCOVERY_SENTINELS[:1] + roles.ASSIGNABLE_ROLES + DISCOVERY_SENTINELS[1:]


def is_discovery_level(value: Any) -> bool:
    import roles

    return isinstance(value, str) and (
        value in DISCOVERY_SENTINELS or roles.is_role(value)
    )


def discovery_category_levels(policy: Optional[dict[str, Any]] = None) -> dict[str, str]:
    """
    The effective threshold per discovery category.

    Falls back to the single `discoveryVisibility` for any category with no
    override, which is what keeps a policy written before the split working
    exactly as it did.
    """
    current = policy if policy is not None else load_policy()
    fallback = current.get("discoveryVisibility", DEFAULT_DISCOVERY)
    overrides = current.get("discoveryCategoryVisibility") or {}
    return {
        category: (overrides.get(category)
                   if is_discovery_level(overrides.get(category))
                   else fallback)
        for category in DISCOVERY_CATEGORIES
    }


def may_see_undiscovered_category(
    role: str, category: str, policy: Optional[dict[str, Any]] = None
) -> bool:
    """Whether `role` may see undiscovered entries of one category."""
    levels = discovery_category_levels(policy)
    return may_see_undiscovered(role, levels.get(category, DEFAULT_DISCOVERY))


def may_see_undiscovered(role: str, level: str) -> bool:
    """Whether `role` clears the configured threshold."""
    import roles

    if level == "everyone":
        return True
    if level == "nobody" or not roles.is_role(role):
        return False
    return roles.ROLES[role]["rank"] >= roles.ROLES.get(level, {"rank": 99})["rank"]

# Who may see each **static world-object category** — the 24,359 ore nodes, 8,386
# chests, 2,757 fishing spots and 185 oil fields extracted from the game files.
#
# Same threshold vocabulary as `discoveryVisibility` (`everyone`, a role name
# meaning that rank and above, or `nobody`) and for the same reason: whether
# handing players a complete ore map is a convenience or the removal of the game
# is a question about how a server is run, not a security one.
#
# Per *category* rather than one switch, because they are not equivalent. A
# complete chest map is close to a loot solution; a fishing-spot map is a
# convenience nobody would consider cheating. An operator who wants one and not
# the other should not have to choose.
#
# **A category the viewer may not see is not listed either.** `world_object_levels`
# filters the legend as well as the points — a category named in a legend with a
# count beside it has already told the player what is out there and roughly how
# much of it, which is most of what hiding it was for.
DEFAULT_WORLD_OBJECT_LEVEL = "everyone"

# Chests are the one category defaulting tighter than the rest: their contents are
# the game's exploration reward, and a full map of them is the closest thing here
# to a spoiler. The others are terrain.
DEFAULT_WORLD_OBJECT_LEVELS: dict[str, str] = {
    "treasure": "trusted",
    # Spawn points say where to farm a species. On a server where finding things
    # is part of playing, handing every Player a complete spawn atlas is a
    # decision the operator should make deliberately, so it defaults closed —
    # same reasoning as chests.
    "palspawner": "trusted",
    "dungeon": "trusted",
    # Merchants and NPC camps. Open by default, unlike the three above: a
    # merchant is a service you go to rather than loot you find first, and the
    # camps are hostile — knowing where they are is a warning, not a spoiler.
    "npc": "everyone",
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


def _env_discovery() -> str:
    level = os.environ.get("DISCOVERY_VISIBILITY", DEFAULT_DISCOVERY).strip().lower()
    if not is_discovery_level(level):
        logger.warning(
            "Unknown DISCOVERY_VISIBILITY=%r, falling back to %r. Known: %s",
            level, DEFAULT_DISCOVERY, ", ".join(discovery_choices()),
        )
        return DEFAULT_DISCOVERY
    return level


def _env_discovery_categories() -> dict[str, str]:
    """
    Per-category overrides from `DISCOVERY_CATEGORY_VISIBILITY`.

        DISCOVERY_CATEGORY_VISIBILITY=effigies:nobody,fastTravel:everyone

    Absent categories inherit `DISCOVERY_VISIBILITY`, so setting neither keeps the
    pre-split behaviour exactly. Case-insensitive on the category name, because
    `fasttravel` is the spelling an operator will reach for first.
    """
    fold = {c.lower(): c for c in DISCOVERY_CATEGORIES}
    levels: dict[str, str] = {}
    raw = os.environ.get("DISCOVERY_CATEGORY_VISIBILITY", "").strip()
    for pair in raw.split(","):
        if not pair.strip():
            continue
        category, _, level = pair.partition(":")
        canonical = fold.get(category.strip().lower())
        level = level.strip().lower()
        if not canonical or not is_discovery_level(level):
            logger.warning(
                "Ignoring DISCOVERY_CATEGORY_VISIBILITY entry %r; expected "
                "<category>:<level> with category in %s and level in %s",
                pair, ", ".join(DISCOVERY_CATEGORIES), ", ".join(discovery_choices()),
            )
            continue
        levels[canonical] = level
    return levels


def _env_base_visibility() -> str:
    level = os.environ.get("BASE_VISIBILITY", DEFAULT_BASE_VISIBILITY).strip().lower()
    if not is_base_visibility(level):
        logger.warning(
            "Unknown BASE_VISIBILITY=%r, falling back to %r. Known: %s",
            level, DEFAULT_BASE_VISIBILITY, ", ".join(base_visibility_choices()),
        )
        return DEFAULT_BASE_VISIBILITY
    return level


def _env_scope(name: str, default: str) -> str:
    level = os.environ.get(name, default).strip().lower()
    if not is_scope_level(level):
        logger.warning(
            "Unknown %s=%r, falling back to %r. Known: %s",
            name, level, default, ", ".join(base_visibility_choices()),
        )
        return default
    return level


def _env_world_object_levels() -> dict[str, str]:
    """
    Per-category thresholds from the environment, e.g.
    `WORLD_OBJECT_VISIBILITY=treasure:moderator,ore:everyone`.

    One variable rather than one per category, because the category list comes
    from the bundled data and adding a hardcoded variable per category would mean
    a code change every time that data grows.
    """
    levels = dict(DEFAULT_WORLD_OBJECT_LEVELS)
    raw = os.environ.get("WORLD_OBJECT_VISIBILITY", "").strip()
    for pair in raw.split(","):
        if not pair.strip():
            continue
        category, _, level = pair.partition(":")
        category, level = category.strip(), level.strip().lower()
        if not category or not is_discovery_level(level):
            logger.warning(
                "Ignoring WORLD_OBJECT_VISIBILITY entry %r; expected "
                "category:level with level one of %s",
                pair, ", ".join(discovery_choices()),
            )
            continue
        levels[category] = level
    return levels


def world_object_level(category: str, policy: dict[str, Any] | None = None) -> str:
    """The threshold for one category, falling back to the global default."""
    current = policy if policy is not None else load_policy()
    levels = current.get("worldObjectVisibility") or {}
    level = levels.get(category)
    return level if is_discovery_level(level) else DEFAULT_WORLD_OBJECT_LEVEL


def may_see_world_objects(
    role: str, category: str, policy: dict[str, Any] | None = None
) -> bool:
    """
    Whether `role` may see one static category at all.

    Reuses `may_see_undiscovered`'s comparison rather than restating it: both
    answer "does this role clear a configured rank", and two copies of that is one
    to get wrong.
    """
    return may_see_undiscovered(role, world_object_level(category, policy))


def default_policy() -> dict[str, Any]:
    return {
        "securityLevel": _env_level(),
        "discoveryVisibility": _env_discovery(),
        "baseVisibility": _env_base_visibility(),
        "discoveryCategoryVisibility": _env_discovery_categories(),
        "serverTotalsVisibility": _env_scope("SERVER_TOTALS_VISIBILITY", DEFAULT_SERVER_TOTALS),
        "allPalsVisibility": _env_scope("ALL_PALS_VISIBILITY", DEFAULT_ALL_PALS),
        "worldObjectVisibility": _env_world_object_levels(),
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


def _describe_base_visibility() -> list[dict[str, Any]]:
    """The threshold choices for guild-base visibility, for a UI to render."""
    import roles

    out: list[dict[str, Any]] = [{
        "id": "everyone", "label": "Everyone",
        "description": "Anyone who can see the map sees every guild's bases.",
    }]
    for name in roles.ASSIGNABLE_ROLES:
        role = roles.ROLES[name]
        out.append({
            "id": name,
            "label": f"{role['label']} and above",
            "description": f"Ranks below {role['label']} see only their own "
                           "guild's bases.",
        })
    out.append({
        "id": "own", "label": "Own guild only",
        "description": "Everyone sees only their own guild's bases. Moderators "
                       "and above always see all of them, so moderation still "
                       "works.",
    })
    return out


def _describe_scope_levels() -> list[dict[str, Any]]:
    """Threshold choices for the server-wide/own-scope settings."""
    import roles

    out: list[dict[str, Any]] = [{
        "id": "everyone", "label": "Everyone",
        "description": "Anyone with the tab sees server-wide figures.",
    }]
    for name in roles.ASSIGNABLE_ROLES:
        role = roles.ROLES[name]
        out.append({
            "id": name,
            "label": f"{role['label']} and above",
            "description": f"Ranks below {role['label']} see only their own.",
        })
    out.append({
        "id": "own", "label": "Own only",
        "description": "Everyone is scoped to their own guild or character, "
                       "including staff.",
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
            if is_base_visibility(stored.get("baseVisibility")):
                policy["baseVisibility"] = stored["baseVisibility"]
            for field in ("serverTotalsVisibility", "allPalsVisibility"):
                if is_scope_level(stored.get(field)):
                    policy[field] = stored[field]
            categories = stored.get("discoveryCategoryVisibility")
            if isinstance(categories, dict):
                for category, level in categories.items():
                    if category in DISCOVERY_CATEGORIES and is_discovery_level(level):
                        policy["discoveryCategoryVisibility"][category] = level
            world_objects = stored.get("worldObjectVisibility")
            if isinstance(world_objects, dict):
                # Merged over the defaults rather than replacing them, so a
                # category added by newer bundled data keeps its default instead
                # of vanishing from an older stored policy.
                for category, level in world_objects.items():
                    if isinstance(category, str) and is_discovery_level(level):
                        policy["worldObjectVisibility"][category] = level
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

    base_visibility = update.get("baseVisibility")
    if isinstance(base_visibility, str):
        if not is_base_visibility(base_visibility):
            raise ValueError(
                f"Unknown base visibility: {base_visibility}. "
                f"Known: {', '.join(base_visibility_choices())}"
            )
        current["baseVisibility"] = base_visibility

    for field in ("serverTotalsVisibility", "allPalsVisibility"):
        value = update.get(field)
        if isinstance(value, str):
            if not is_scope_level(value):
                raise ValueError(
                    f"Unknown {field}: {value}. "
                    f"Known: {', '.join(base_visibility_choices())}"
                )
            current[field] = value

    categories = update.get("discoveryCategoryVisibility")
    if isinstance(categories, dict):
        for category, level in categories.items():
            if category not in DISCOVERY_CATEGORIES:
                raise ValueError(
                    f"Unknown discovery category: {category}. "
                    f"Known: {', '.join(DISCOVERY_CATEGORIES)}"
                )
            if not is_discovery_level(level):
                raise ValueError(
                    f"Unknown visibility for '{category}': {level}. "
                    f"Known: {', '.join(discovery_choices())}"
                )
            current.setdefault("discoveryCategoryVisibility", {})[category] = level

    world_objects = update.get("worldObjectVisibility")
    if isinstance(world_objects, dict):
        for category, level in world_objects.items():
            if not isinstance(category, str) or not category:
                raise ValueError("World-object category names must be strings")
            if not is_discovery_level(level):
                raise ValueError(
                    f"Unknown visibility for '{category}': {level}. "
                    f"Known: {', '.join(discovery_choices())}"
                )
            current.setdefault("worldObjectVisibility", {})[category] = level

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


def _world_object_categories() -> list[dict[str, Any]]:
    """
    The static categories a dial can be set for, with their world totals.

    Returns an empty list if the bundle is missing, so the settings page loses a
    section rather than failing — the same rule the map layer follows.
    """
    try:
        import worldobjects

        return [
            {"id": c["id"], "label": c["label"], "count": c["count"]}
            for c in worldobjects.categories()
        ]
    except Exception as e:  # noqa: BLE001
        logger.warning("World object categories unavailable for the policy UI: %s", e)
        return []


# ─── Visibility presets ──────────────────────────────────
#
# WHY THESE STAY FIVE SETTINGS AND NOT ONE
# ----------------------------------------
# Collapsing them into a single "openness" dial is tempting and wrong, because
# they are not points on one axis. They answer four different questions:
#
#   discoveryVisibility     — is showing unexplored map a *spoiler*?
#   baseVisibility          — is another guild's location *private*?
#   serverTotals/allPals    — is another guild's *inventory* private?
#   worldObjectVisibility   — is a full resource map a spoiler?
#
# Real servers disagree across them, not along them. A completionist co-op group
# wants every ore node shown (no spoiler concern) and every base hidden (six
# strangers on a rented box). A competitive server wants the exact opposite. A
# single dial forces one of those two to be wrong, and there is no ordering of
# the values that makes both right.
#
# So the settings stay independent and the *starting points* get named. A preset
# writes all five and then gets out of the way — nothing is locked, and the UI
# goes on showing each dial with whatever it now holds. That is the difference
# between a preset and a mode.
VISIBILITY_PRESETS: dict[str, dict[str, Any]] = {
    "private": {
        "label": "Private / friends",
        "description": (
            "Everyone sees everything. For a handful of people who already talk "
            "to each other and are playing the same save together."
        ),
        "values": {
            "discoveryVisibility": "everyone",
            "baseVisibility": "everyone",
            "serverTotalsVisibility": "everyone",
            "allPalsVisibility": "everyone",
        },
    },
    "community": {
        "label": "Community server",
        "description": (
            "The default. Players get their own things and the shared world; "
            "other guilds' bases, Pals and inventories need Trusted or above."
        ),
        "values": {
            "discoveryVisibility": DEFAULT_DISCOVERY,
            "baseVisibility": DEFAULT_BASE_VISIBILITY,
            "serverTotalsVisibility": DEFAULT_SERVER_TOTALS,
            "allPalsVisibility": DEFAULT_ALL_PALS,
        },
    },
    "competitive": {
        "label": "Competitive / PvP",
        "description": (
            "Nothing about another guild is visible, and the map gives away "
            "nothing a player has not personally found. Staff still see all."
        ),
        "values": {
            "discoveryVisibility": "nobody",
            "baseVisibility": "own",
            "serverTotalsVisibility": "own",
            "allPalsVisibility": "own",
        },
    },
}


def describe_presets() -> list[dict[str, Any]]:
    """
    The presets, each annotated with whether it is what the server currently has.

    `active` is computed rather than stored. Storing "which preset is selected"
    would make the individual dials lie the moment one of them was changed — and
    changing them individually afterwards is the entire point of a preset.
    """
    current = load_policy()
    out = []
    for preset_id, preset in VISIBILITY_PRESETS.items():
        out.append({
            "id": preset_id,
            "label": preset["label"],
            "description": preset["description"],
            "values": preset["values"],
            "active": all(
                current.get(key) == value for key, value in preset["values"].items()
            ),
        })
    return out


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
        # The per-category dials, with the level each currently resolves to.
        # Sent resolved rather than as raw overrides so the UI shows what is in
        # force, not a blank where it inherits.
        "discoveryCategories": [
            {
                "id": category,
                "label": DISCOVERY_CATEGORY_LABELS.get(category, category),
                "level": level,
                "inherited": category not in (policy.get("discoveryCategoryVisibility") or {}),
            }
            for category, level in discovery_category_levels(policy).items()
        ],
        "baseVisibilityLevels": _describe_base_visibility(),
        "scopeLevels": _describe_scope_levels(),
        # The categories to offer a dial for come from the bundled data rather
        # than a list here, so new extracted content is configurable without a
        # code change. Imported lazily: policy is loaded on paths that have no
        # business touching a 717 KB data bundle.
        "worldObjectCategories": _world_object_categories(),
        "visibilityPresets": describe_presets(),
        "allowedCapabilities": allowed_capabilities(),
    }
