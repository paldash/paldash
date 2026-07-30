"""
Per-base map visibility.

`privacy.py` hides a *person* and, at its widest setting, everything their guild
owns. That is all-or-nothing, and the common real request is narrower: a guild
happy to be on the map that would rather one particular base — the one with the
good loot, or the one nobody is meant to find — not be.

**Who may set it: the guild master, because a base belongs to a guild.** A base
is not one player's property, so "hide my base" is not one player's decision. The
guild's `admin_player_uid` is the closest thing the save has to an owner, and it
is populated on every guild of the reference world.

There is one fallback, and it exists because the alternative is a dead feature:
if the guild master has **no linked dashboard account**, any member of that guild
may set it. A guild whose leader does not use the dashboard would otherwise be
unable to hide anything at all.

**Staff cannot set it, and do not need to.** The same rank rule as `privacy.py`
applies — `hidden ⟺ viewer_rank <= hider_rank` — so anyone ranked above the
person who hid a base still sees it. Moderation never needs an override because
nothing is ever concealed from it in the first place.

**A guild always sees its own bases.** Without that, a guild master hiding a base
would hide it from themselves and their guildmates, which reads as data loss
rather than as a privacy setting.

**Filtering happens in three places, not one.** `/api/bases` is the obvious one,
but a base's *objects* carry the same coordinates (`/api/mapobjects`) and its
storage names its contents (`/api/bases/storage`). Dropping the marker while
still plotting the palbox that sits inside it hides nothing — see
`filter_objects` and `filter_storage`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import accounts
import db
import privacy
import roles
import savecache

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS base_visibility (
    base_id     TEXT PRIMARY KEY,
    hidden      INTEGER NOT NULL DEFAULT 1,
    set_by      TEXT    NOT NULL DEFAULT '',
    set_by_role TEXT    NOT NULL DEFAULT '',
    updated_at  TEXT    NOT NULL DEFAULT ''
);
"""


def init() -> None:
    with db.transaction() as conn:
        conn.executescript(SCHEMA)


# ─── Storage ─────────────────────────────────────────────


def _rows() -> list[dict[str, Any]]:
    init()
    return [
        {
            "baseId": r["base_id"],
            "hidden": bool(r["hidden"]),
            "setBy": r["set_by"],
            "setByRole": r["set_by_role"],
            "updatedAt": r["updated_at"],
        }
        for r in db.connect().execute(
            "SELECT * FROM base_visibility WHERE hidden = 1"
        ).fetchall()
    ]


def state_for(base_ids: list[str]) -> dict[str, bool]:
    """`{base_id: hidden}` for the ids asked about. Absent means visible."""
    hidden = {r["baseId"] for r in _rows()}
    return {base_id: base_id in hidden for base_id in base_ids}


def _hider_role(row: dict[str, Any]) -> str:
    """
    The rank a hidden base hides *behind*.

    Re-resolved from the account rather than trusted from the row, so demoting
    someone takes effect on what their old settings can conceal. The stored role
    is the fallback for an orphaned row — an account deleted after hiding a base
    should not silently expose it, and should not silently promote it either.
    """
    user = accounts.get_user(row["setBy"]) if row["setBy"] else None
    if user and user.get("role") in roles.ROLES:
        return str(user["role"])
    return row["setByRole"] if row["setByRole"] in roles.ROLES else roles.DEFAULT_ROLE


# ─── Ownership ───────────────────────────────────────────


def _guild_of_base(base_id: str, bases: list[dict]) -> str:
    for base in bases:
        if str(base.get("id") or "") == base_id:
            return str(base.get("guildId") or "")
    return ""


def _guild(guild_id: str, guilds: list[dict]) -> Optional[dict]:
    for guild in guilds:
        if str(guild.get("id") or "") == guild_id:
            return guild
    return None


def _member_uids(guild: dict) -> set[str]:
    return {
        privacy.normalise_uid(m.get("uid"))
        for m in (guild.get("members") or [])
        if m.get("uid")
    }


def _has_account(uid: str) -> bool:
    """Whether any dashboard account is linked to this character."""
    if not uid:
        return False
    rows = db.connect().execute(
        "SELECT steam_uid FROM users WHERE steam_uid IS NOT NULL AND steam_uid != ''"
    ).fetchall()
    return any(privacy.normalise_uid(r["steam_uid"]) == uid for r in rows)


def can_manage(base_id: str, username: str) -> tuple[bool, str]:
    """
    Whether this account may change one base's visibility, and why not if not.

    Fails **closed** on missing world data: with no parse there is no way to know
    who owns the base, and a permission check that guesses is not a check.
    """
    user = accounts.get_user(username)
    if not user:
        return False, "No such account."

    viewer_uid = privacy.normalise_uid(user.get("steamUid"))
    if not viewer_uid:
        return False, (
            "This account is not linked to a character, so it has no guild. "
            "An Administrator links it from the Users tab."
        )

    bases = savecache.get_section("bases")
    guilds = savecache.get_section("guilds")
    if not bases or not guilds:
        return False, "The world has not been parsed yet, so base ownership is unknown."

    guild_id = _guild_of_base(base_id, bases)
    if not guild_id:
        return False, f"No base {base_id} in the parsed world."

    guild = _guild(guild_id, guilds)
    if guild is None:
        return False, "That base's guild is not in the parsed world."

    master = privacy.normalise_uid(guild.get("adminPlayerUid"))
    members = _member_uids(guild)

    if master and viewer_uid == master:
        return True, "guild master"

    if viewer_uid not in members:
        return False, "Only that base's guild can change its visibility."

    # The fallback. Without it, a guild whose leader has no dashboard account
    # could never hide anything, which makes the feature useless for exactly the
    # guilds most likely to want it.
    if not master or not _has_account(master):
        return True, "guild member (the guild master has no dashboard account)"

    return False, "Only the guild master can change this guild's base visibility."


# ─── Reading ─────────────────────────────────────────────


def hidden_base_ids(viewer_role: str, viewer_username: str = "") -> set[str]:
    """
    Base ids this viewer must not see.

    Empty for staff above every hider, and empty for a member of the owning
    guild — you never hide from yourself.
    """
    rows = _rows()
    if not rows:
        return set()

    viewer_uid = ""
    if viewer_username:
        user = accounts.get_user(viewer_username)
        if user:
            viewer_uid = privacy.normalise_uid(user.get("steamUid"))

    bases = savecache.get_section("bases")
    guilds = savecache.get_section("guilds")

    # Which guilds the viewer belongs to. Their own guild's bases stay visible
    # however they are flagged.
    own_guilds: set[str] = set()
    if viewer_uid:
        for guild in guilds:
            if viewer_uid in _member_uids(guild):
                own_guilds.add(str(guild.get("id") or ""))

    hidden: set[str] = set()
    for row in rows:
        base_id = row["baseId"]
        # The mode argument is only there to say "hiding is on" — the rank
        # comparison is the part that matters and it is the same one `privacy`
        # applies to players.
        if not privacy.conceals(viewer_role, _hider_role(row), "player"):
            continue
        if own_guilds and _guild_of_base(base_id, bases) in own_guilds:
            continue
        hidden.add(base_id)

    return hidden


def manageable_bases(username: str, role: str) -> dict[str, Any]:
    """
    Every base this account may set visibility on, with its current state.

    Returns a reason instead of an empty list when nothing is manageable, because
    "you are not in a guild" and "no base is hidden" look identical otherwise.
    """
    init()
    user = accounts.get_user(username)
    if not user:
        return {"bases": [], "reason": "No such account."}

    viewer_uid = privacy.normalise_uid(user.get("steamUid"))
    if not viewer_uid:
        return {"bases": [], "reason": (
            "This account is not linked to a character, so it has no guild bases "
            "to manage."
        )}

    bases = savecache.get_section("bases")
    guilds = savecache.get_section("guilds")
    if not bases or not guilds:
        return {"bases": [], "reason": "The world has not been parsed yet."}

    hidden = {r["baseId"] for r in _rows()}
    out = []
    for base in bases:
        base_id = str(base.get("id") or "")
        allowed, why = can_manage(base_id, username)
        if not allowed:
            continue
        out.append({
            "baseId": base_id,
            "name": base.get("name"),
            "guildId": base.get("guildId"),
            "guildName": base.get("guildName"),
            "hidden": base_id in hidden,
            "authority": why,
        })

    if not out:
        return {"bases": [], "reason": (
            "No bases here belong to a guild you can manage. Only a guild master "
            "can hide a guild's bases."
        )}
    return {"bases": out, "reason": ""}


# ─── Writing ─────────────────────────────────────────────


def set_hidden(base_id: str, hidden: bool, *, username: str, role: str) -> dict[str, Any]:
    """Set or clear one base's hidden flag. Authorisation is the caller's job."""
    init()
    now = datetime.now(timezone.utc).isoformat()
    with db.transaction() as conn:
        if hidden:
            conn.execute(
                "INSERT INTO base_visibility (base_id, hidden, set_by, set_by_role, "
                "updated_at) VALUES (?, 1, ?, ?, ?) "
                "ON CONFLICT(base_id) DO UPDATE SET hidden = 1, set_by = excluded.set_by, "
                "set_by_role = excluded.set_by_role, updated_at = excluded.updated_at",
                (base_id, username, role, now),
            )
        else:
            # Deleted rather than kept with hidden = 0: an absent row already
            # means visible, and two representations of the same state is how a
            # stale `set_by` ends up deciding a later rank comparison.
            conn.execute("DELETE FROM base_visibility WHERE base_id = ?", (base_id,))

    return {"baseId": base_id, "hidden": hidden, "setBy": username if hidden else ""}


# ─── Application ─────────────────────────────────────────


def filter_bases(bases: list[dict], hidden: set[str]) -> list[dict]:
    if not hidden:
        return bases
    return [b for b in bases if str(b.get("id") or "") not in hidden]


def filter_objects(objects: list[dict], hidden: set[str]) -> list[dict]:
    """
    Drop placed objects belonging to a hidden base.

    This is the half that is easy to forget. A base marker removed from the map
    while its palbox, chests and benches still plot at the same coordinates
    conceals a label, not a location.
    """
    if not hidden:
        return objects
    return [o for o in objects if str(o.get("baseCampId") or "") not in hidden]


def filter_storage(summaries: list[dict], hidden: set[str]) -> list[dict]:
    """A hidden base's contents are not listed either — that is most of the point."""
    if not hidden:
        return summaries
    return [s for s in summaries if str(s.get("baseId") or "") not in hidden]
