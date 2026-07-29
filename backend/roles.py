"""
Role presets and the capabilities each grants.

TWO INDEPENDENT GATES
---------------------
A user may do something only if BOTH agree:

  1. their role grants the capability  (this file — "who is trusted with what")
  2. the security level permits it     (policy.py — "what this server allows at all")

They answer different questions. A server left at `SECURITY_LEVEL=readonly`
refuses save edits even from the owner, because that dial is about protecting the
world from mistakes, not about trust. Conversely, raising the security level does
not hand a Player account the save editor.

This module is the authority; `src/lib/permissions.ts` mirrors the names for the
UI, and the backend re-checks every write regardless of what the UI believes.
"""

from __future__ import annotations

# ─── Capabilities ────────────────────────────────────────────────

VIEW_BASIC = "view.basic"          # server status, map, bases
VIEW_DETAIL = "view.detail"        # inventories, item totals, all players
VIEW_SELF = "view.self"            # one's own player data only
SETTINGS_WRITE = "settings.write"  # PalWorldSettings.ini
BACKUP_MANAGE = "backup.manage"    # create/restore/delete backups
POLICY_MANAGE = "policy.manage"    # change the access policy
USERS_MANAGE = "users.manage"      # create/edit/remove accounts
AUDIT_VIEW = "audit.view"          # read the audit log

# Two capabilities where there used to be one.
#
# `server.control` was documented as "kick/ban/announce/restart", which bundled
# two different kinds of trust. Taking the server down is an operations decision;
# banning a player is a social one. An operator who wants a moderator who can
# remove a griefer but cannot shut the world down had no way to express that, and
# neither did the reverse.
#
# Both are granted to Moderator and above, so no existing account changes what it
# can do — but they are now separable by editing one set below.
SERVER_CONTROL = "server.control"      # restart/stop/start the server, force-save
PLAYERS_MODERATE = "players.moderate"  # kick, ban, unban, announce

SAVE_SORT_STACKABLES = "save.sort.stackables"
SAVE_SORT_ALL = "save.sort.all"
SAVE_EDIT_FULL = "save.edit.full"

ALL_CAPABILITIES = (
    VIEW_BASIC, VIEW_DETAIL, VIEW_SELF, SERVER_CONTROL, PLAYERS_MODERATE,
    SETTINGS_WRITE, BACKUP_MANAGE, POLICY_MANAGE, USERS_MANAGE, AUDIT_VIEW,
    SAVE_SORT_STACKABLES, SAVE_SORT_ALL, SAVE_EDIT_FULL,
)

# Capabilities the security level gates. Anything not listed here is a read or an
# account operation, which the security level has no opinion about.
POLICY_GATED = frozenset({
    SETTINGS_WRITE, BACKUP_MANAGE,
    SAVE_SORT_STACKABLES, SAVE_SORT_ALL, SAVE_EDIT_FULL,
})


# ─── Roles, least to most privileged ─────────────────────────────

ROLES: dict[str, dict] = {
    "guest": {
        "label": "Guest",
        "rank": 0,
        "description": (
            "Not signed in. Sees only what the guest visibility toggles allow, "
            "with player names and Steam IDs stripped."
        ),
        "capabilities": {VIEW_BASIC},
    },
    "readonly": {
        "label": "Read only",
        "rank": 1,
        "description": "A named account that can look at the server but change nothing.",
        "capabilities": {VIEW_BASIC},
    },
    "player": {
        "label": "Player",
        "rank": 2,
        "description": (
            "Sees the server overview and their own character: their bases, "
            "palbox, progression and discovered map. Not other players'."
        ),
        "capabilities": {VIEW_BASIC, VIEW_SELF},
    },
    "trusted": {
        "label": "Trusted player",
        "rank": 3,
        "description": (
            "Everything a Player sees, plus full visibility of other players, "
            "guild inventories and the breeding planner. Still read-only."
        ),
        "capabilities": {VIEW_BASIC, VIEW_SELF, VIEW_DETAIL},
    },
    "moderator": {
        "label": "Moderator",
        "rank": 4,
        "description": (
            "Full visibility plus day-to-day operation: kick, ban, announce, "
            "restart the server, and take backups. Cannot edit saves or accounts. "
            "Moderating players and controlling the server are separate "
            "capabilities, so either can be withdrawn without the other."
        ),
        "capabilities": {
            VIEW_BASIC, VIEW_SELF, VIEW_DETAIL,
            SERVER_CONTROL, PLAYERS_MODERATE, BACKUP_MANAGE, AUDIT_VIEW,
        },
    },
    "admin": {
        "label": "Administrator",
        "rank": 5,
        "description": (
            "Everything a Moderator can do, plus server settings and save "
            "editing. Cannot manage accounts or change the security policy."
        ),
        "capabilities": {
            VIEW_BASIC, VIEW_SELF, VIEW_DETAIL,
            SERVER_CONTROL, PLAYERS_MODERATE, BACKUP_MANAGE, AUDIT_VIEW,
            SETTINGS_WRITE,
            SAVE_SORT_STACKABLES, SAVE_SORT_ALL, SAVE_EDIT_FULL,
        },
    },
    "owner": {
        "label": "Owner",
        "rank": 6,
        "description": (
            "Everything, including creating accounts and changing the security "
            "policy. There is always at least one Owner."
        ),
        "capabilities": set(ALL_CAPABILITIES),
    },
}

# Roles that may exist as real accounts. "guest" is the absence of an account.
ASSIGNABLE_ROLES = tuple(name for name in ROLES if name != "guest")

DEFAULT_ROLE = "player"


def is_role(name: str) -> bool:
    return name in ROLES


def rank(role: str) -> int:
    return ROLES.get(role, {}).get("rank", -1)


def capabilities_for(role: str) -> set[str]:
    """Raw capabilities of a role, before the security level is applied."""
    return set(ROLES.get(role, {}).get("capabilities", set()))


def effective_capabilities(role: str, policy_allowed: set[str]) -> set[str]:
    """
    What a role may actually do on this server right now.

    Policy-gated capabilities need the security level's blessing as well as the
    role's; everything else depends on the role alone.
    """
    granted = capabilities_for(role)
    return {
        capability
        for capability in granted
        if capability not in POLICY_GATED or capability in policy_allowed
    }


def can_manage(actor_role: str, target_role: str) -> bool:
    """
    Whether `actor_role` may create or modify an account of `target_role`.

    You cannot grant a role above your own — otherwise an Administrator could
    make themselves Owner, and the distinction would be decorative.
    """
    if USERS_MANAGE not in capabilities_for(actor_role):
        return False
    return rank(actor_role) >= rank(target_role)


def describe() -> list[dict]:
    """Role presets for the UI, least privileged first."""
    return [
        {
            "id": name,
            "label": info["label"],
            "rank": info["rank"],
            "description": info["description"],
            "capabilities": sorted(info["capabilities"]),
            "assignable": name in ASSIGNABLE_ROLES,
        }
        for name, info in sorted(ROLES.items(), key=lambda kv: kv[1]["rank"])
    ]
