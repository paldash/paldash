"""
Per-player map privacy: letting someone hide themselves from their peers.

Distinct from everything in `policy.py`, which is server-wide and set by the
Owner. This is set by **the player, about themselves**, and the operator does not
override it.

THE RULE
--------
A player's privacy applies to viewers at **their own role or below**, never
above.

    hidden  ⟺  viewer_rank <= hider_rank

That single line does a surprising amount of work:

- **Moderation always works.** A Player cannot hide from a Moderator, because
  Moderator outranks them. Nobody has to remember to add an exemption.
- **It is self-explaining.** "You can hide from your peers, not from staff" needs
  no documentation table.
- **It cannot be used to evade oversight**, which is the failure mode a naive
  "hide from everyone" toggle has.

An Owner who enables it hides from other Owners too, since equal rank counts.
That is intentional — at that level everyone can see the setting anyway.

MODES
-----
Bases belong to *guilds*, not to individuals, so "hide me" has three defensible
meanings and the player picks:

  off           visible to everyone
  player        live position and character detail only; guild bases stay visible
  player_bases  the above, plus bases in guilds where they are the only member
  guild         the above, plus their whole guild's bases and membership
                (**the default** — see DEFAULT_MODE)
  bases_only    the inverse: their guild's bases are hidden but they themselves
                stay visible, live position included

`bases_only` is the one mode that is not cumulative with the others, which is why
it sits at the end of the list rather than in the middle of it. The first four
form a ladder — each hides everything the previous one did, plus more — and a
fifth rung could only ever mean "and also hide the player". This is a different
axis: some people are happy to be seen playing and simply do not want their base
locations advertised. Modelling it as a rung would have meant either lying about
the ordering or refusing a reasonable request.

`guild` deliberately affects other people's view of a shared asset, so it is the
one mode with a social cost. It is offered because a two-person guild wanting to
stay off the map is a real thing, and refusing it would just push people to
`player_bases` and confusion about why their base still shows.

WHAT IS NEVER HIDDEN
--------------------
Anything the operator needs to run the server: the audit log, account
management, and save editing all work on real identities regardless. This
governs *map and roster visibility*, not administration.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import db
import roles

logger = logging.getLogger(__name__)

MODES = ("off", "player", "player_bases", "guild", "bases_only")

# The default is the MOST private option, not the least.
#
# Nobody should have to discover a privacy setting exists before they stop being
# exposed. A new account on a shared server is visible to strangers the moment it
# is created, and the person it describes has not agreed to that yet. Opting *in*
# to visibility is a choice someone makes; opting out is a thing they have to
# find out they need.
#
# It costs little, because the rule only conceals from peers and below: staff see
# everyone regardless, so moderation is unaffected on day one. What a fresh
# server loses is players seeing each other until they choose to be seen.
DEFAULT_MODE = "guild"

# Which modes conceal which things. Ordered by increasing reach so a UI can
# present them as a ladder.
MODE_LABELS: dict[str, dict[str, str]] = {
    "off": {
        "label": "Visible to everyone",
        "description": "Your position, character and bases are shown to anyone "
                       "who can see the map.",
    },
    "player": {
        "label": "Hide me",
        "description": "Your live position and character details are hidden from "
                       "players at your rank or below. Your guild's bases stay "
                       "visible.",
    },
    "player_bases": {
        "label": "Hide me and my solo bases",
        "description": "As above, and bases in guilds where you are the only "
                       "member are hidden too.",
    },
    "guild": {
        "label": "Hide me and my whole guild",
        "description": "As above, and your guild's bases and member list are "
                       "hidden. This affects what others see of a shared guild, "
                       "so agree it with your guildmates.",
    },
    "bases_only": {
        "label": "Hide my bases, not me",
        "description": "Your guild's bases are hidden, but you stay visible — "
                       "live position included. For when you do not mind being "
                       "seen playing and would rather your base locations were "
                       "not advertised. Also affects a shared guild.",
    },
}


def is_mode(value: Any) -> bool:
    return isinstance(value, str) and value in MODES


def describe_modes() -> list[dict[str, str]]:
    return [{"id": mode, **MODE_LABELS[mode]} for mode in MODES]


def normalise_uid(uid: Any) -> str:
    """
    One spelling for a player id, because there are two in circulation.

    `accounts.create_user` stores `steam_uid` dash-stripped and lowercased;
    `Level.sav` stores dashed lowercase GUIDs. Comparing them raw matches
    nothing, and the failure is silent — privacy simply hides nobody while every
    setting still reads as enabled. Everything here normalises on both sides.
    """
    return str(uid or "").replace("-", "").lower()


def _rank(role: str) -> int:
    entry = roles.ROLES.get(role)
    return entry["rank"] if entry else -1


def conceals(viewer_role: str, hider_role: str, mode: str) -> bool:
    """
    Whether `hider`'s setting hides them from `viewer`.

    The comparison is `<=` on purpose: equal rank is concealed. Peers are exactly
    who a privacy setting is for.
    """
    if mode == "off" or not is_mode(mode):
        return False
    return _rank(viewer_role) <= _rank(hider_role)


# ─── Storage ─────────────────────────────────────────────


def get_mode(username: str) -> str:
    row = db.connect().execute(
        "SELECT map_privacy FROM users WHERE username = ? COLLATE NOCASE", (username,)
    ).fetchone()
    mode = (row["map_privacy"] if row else None) or DEFAULT_MODE
    return mode if is_mode(mode) else DEFAULT_MODE


def set_mode(username: str, mode: str) -> str:
    if not is_mode(mode):
        raise ValueError(f"Unknown privacy mode: {mode}. Known: {', '.join(MODES)}")
    with db.transaction() as conn:
        conn.execute(
            "UPDATE users SET map_privacy = ? WHERE username = ? COLLATE NOCASE",
            (mode, username),
        )
    return mode


def all_settings() -> list[dict[str, Any]]:
    """Every account that has privacy on, with the uid it maps to."""
    rows = db.connect().execute(
        "SELECT username, role, steam_uid, map_privacy FROM users "
        "WHERE map_privacy IS NOT NULL AND map_privacy != 'off'"
    ).fetchall()
    return [
        {
            "username": r["username"],
            "role": r["role"],
            "steamUid": (r["steam_uid"] or "").strip(),
            "mode": r["map_privacy"],
        }
        for r in rows
        if is_mode(r["map_privacy"])
    ]


# ─── Application ─────────────────────────────────────────


def _linked_uid(username: str) -> str:
    """
    One account's linked character uid, normalised.

    Needed because `all_settings()` only returns accounts with privacy *on*, so
    a viewer whose own setting is `off` is absent from it — and that viewer still
    has a guild whose members must stay visible to them.
    """
    row = db.connect().execute(
        "SELECT steam_uid FROM users WHERE username = ? COLLATE NOCASE", (username,)
    ).fetchone()
    return normalise_uid(row["steam_uid"]) if row else ""


def _guildmates(viewer_uid: str) -> set[str]:
    """
    Every uid sharing a guild with the viewer, including their own.

    Imported lazily: `savecache` is a heavy module and `privacy` is on the
    request path for things that never touch a parsed world.

    Empty when the world has not been parsed, which fails towards *more* privacy
    — the exemption simply does not apply and the normal rank rule stands. That
    is the safe direction for a concealment feature.
    """
    if not viewer_uid:
        return set()
    import savecache

    mates: set[str] = set()
    for guild in savecache.get_section("guilds"):
        members = {
            normalise_uid(m.get("uid"))
            for m in (guild.get("members") or [])
            if m.get("uid")
        }
        if viewer_uid in members:
            mates |= members
    return mates


def hidden_uids(viewer_role: str, viewer_username: str = "") -> dict[str, set[str]]:
    """
    Player uids this viewer must not see, split by what is concealed.

    Returns `{"players": {...}, "bases": {...}, "guilds": {...}}` — uid sets,
    because that is what the save-derived endpoints key on.

    A viewer never hides from themselves; the whole point is that you can still
    see your own things.

    **Nor from their own guild, and the absence of that rule was a real bug.**
    `baseprivacy.py` already reasoned it out — "a guild always sees its own
    bases; without that, hiding a base would hide it from yourself and your
    guildmates, which reads as data loss rather than as a privacy setting" — and
    this module never got the same treatment. Combined with `DEFAULT_MODE`
    being the *most* private option, the effect on a fresh server was that two
    friends in one guild, both on defaults, could not see each other's base, each
    other's position, or each other at all. Nothing looked broken; the map was
    simply empty.

    A guild shares a palbox and shares bases. Concealing a shared asset from the
    people who share it is not privacy, it is breakage. These settings are about
    strangers, which is what "peers" was always meant to mean.
    """
    players: set[str] = set()
    bases: set[str] = set()
    guilds: set[str] = set()

    settings = all_settings()
    if not settings:
        return {"players": players, "bases": bases, "guilds": guilds}

    # The viewer's own uid, from the same rows — no second query, and no
    # dependency on `accounts` here.
    viewer_uid = ""
    if viewer_username:
        for entry in settings:
            if entry["username"].lower() == viewer_username.lower():
                viewer_uid = normalise_uid(entry["steamUid"])
                break
        else:
            viewer_uid = _linked_uid(viewer_username)

    mates = _guildmates(viewer_uid)

    for entry in settings:
        uid = normalise_uid(entry["steamUid"])
        if not uid:
            continue                       # no linked character, nothing to hide
        if viewer_username and entry["username"].lower() == viewer_username.lower():
            continue                       # never hide someone from themselves
        if uid in mates:
            continue                       # never hide from your own guild
        if not conceals(viewer_role, entry["role"], entry["mode"]):
            continue

        # `bases_only` is the one mode that does not hide the player.
        if entry["mode"] != "bases_only":
            players.add(uid)
        if entry["mode"] in ("player_bases", "guild", "bases_only"):
            bases.add(uid)
        if entry["mode"] in ("guild", "bases_only"):
            guilds.add(uid)

    return {"players": players, "bases": bases, "guilds": guilds}


def filter_players(entries: list[dict], hidden: set[str], uid_key: str = "uid") -> list[dict]:
    if not hidden:
        return entries
    return [e for e in entries if normalise_uid(e.get(uid_key)) not in hidden]


def filter_bases(bases: list[dict], guilds: list[dict], hidden_uids_: set[str],
                 guild_wide: set[str]) -> list[dict]:
    """
    Drop bases belonging to a hidden player.

    A base is attributed to a *guild*, so the question is which guilds a hidden
    player is in — and whether they are alone in it. `player_bases` hides only
    solo guilds; `guild` hides the guild outright, which is why the two sets are
    passed separately rather than merged.
    """
    if not hidden_uids_ and not guild_wide:
        return bases

    hidden_guilds: set[str] = set()
    for guild in guilds:
        members = [normalise_uid(m.get("uid")) for m in (guild.get("members") or [])]
        gid = str(guild.get("id") or "")
        if any(uid in guild_wide for uid in members):
            hidden_guilds.add(gid)
        elif len(members) == 1 and members[0] in hidden_uids_:
            hidden_guilds.add(gid)

    return [b for b in bases if str(b.get("guildId") or "") not in hidden_guilds]
