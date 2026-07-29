"""
Player moderation: kick, ban, unban, announce.

Every function here issues a command to the game server and writes an audit
record, in that order, whether it succeeded or not. That ordering is the point of
the module existing: these actions were already reachable through the Next.js
proxy and left no trace, and a ban with no record of who issued it is the one
kind of action an operator most reliably needs to look up later.

WHAT IS RECORDED
----------------
The actor, the target's uid *and* their display name at the time, the reason, and
the outcome. The name matters because a uid is unreadable and a player can change
their in-game name — six months later "who was 22b22b02?" has no answer unless it
was written down when it was known.

BANS ARE NOT TRACKED HERE
-------------------------
The game owns the ban list; this module does not keep a second copy. A local
mirror would drift the moment anyone edited the server's own list, and a ban list
that disagrees with the game's is worse than no list. `list_bans` reads the
server's file when it can be found and says so plainly when it cannot.

NO CONFIRMATION LOGIC
---------------------
A kick either returned success or it did not. Nothing here retries: re-issuing a
ban that may already have landed gains nothing and could double-announce it to the
whole server.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import audit
import gameapi
import privacy
import savefiles

logger = logging.getLogger(__name__)

MAX_MESSAGE = 200

# The game rejects some characters in a broadcast outright and silently truncates
# on others. Newlines end the command early, which would make the tail of a
# message disappear with no error — worth stripping rather than discovering.
_FORBIDDEN = "\r\n\t"


class ModerationError(Exception):
    """The command could not be issued, or the request was malformed."""


def clean_message(message: Any, *, required: bool = False) -> str:
    text = "" if message is None else str(message)
    for char in _FORBIDDEN:
        text = text.replace(char, " ")
    text = " ".join(text.split())
    if len(text) > MAX_MESSAGE:
        text = text[:MAX_MESSAGE]
    if required and not text:
        raise ModerationError("A message is required and cannot be blank")
    return text


def _resolve(userid: str) -> tuple[str, str]:
    """
    (uid_for_the_game, display_name_now).

    The uid is passed to the game exactly as the caller gave it — the game's own
    `players` listing is the authority on its spelling, and normalising it here
    could produce a form the game does not match. The *name* is looked up purely
    so the audit record is readable, and a lookup failure is not fatal: an
    unnamed ban is still better than a refused one.
    """
    uid = str(userid or "").strip()
    if not uid:
        raise ModerationError("No player named")

    try:
        wanted = privacy.normalise_uid(uid)
        for player in gameapi.players():
            for key in ("userId", "playerId"):
                if privacy.normalise_uid(player.get(key)) == wanted:
                    return uid, str(player.get("name") or "")
    except gameapi.GameApiError:
        pass          # offline, or a bad password; the command below will say so
    return uid, ""


def _run(
    action: str,
    call,
    *,
    actor: dict,
    ip: str,
    target: str = "",
    detail: Optional[dict] = None,
) -> dict:
    """Issue a command and record it, successfully or not."""
    try:
        result = call()
    except gameapi.GameApiError as e:
        audit.record(
            action, username=actor["username"], role=actor["role"],
            target=target, detail={**(detail or {}), "error": str(e)}, ip=ip,
            result=audit.RESULT_FAILED,
        )
        raise ModerationError(str(e)) from e

    audit.record(
        action, username=actor["username"], role=actor["role"],
        target=target, detail=detail or {}, ip=ip,
    )
    return {"ok": True, "response": result}


# ─── Commands ────────────────────────────────────────────


def announce(message: str, *, actor: dict, ip: str = "") -> dict:
    text = clean_message(message, required=True)
    return _run(
        audit.SERVER_ANNOUNCE, lambda: gameapi.announce(text),
        actor=actor, ip=ip, target="all", detail={"message": text},
    )


def kick(userid: str, reason: str = "", *, actor: dict, ip: str = "") -> dict:
    uid, name = _resolve(userid)
    text = clean_message(reason)
    return _run(
        audit.PLAYER_KICK, lambda: gameapi.kick(uid, text),
        actor=actor, ip=ip, target=uid,
        detail={"playerName": name, "reason": text},
    )


def ban(userid: str, reason: str = "", *, actor: dict, ip: str = "") -> dict:
    uid, name = _resolve(userid)
    text = clean_message(reason)
    return _run(
        audit.PLAYER_BAN, lambda: gameapi.ban(uid, text),
        actor=actor, ip=ip, target=uid,
        detail={"playerName": name, "reason": text},
    )


def unban(userid: str, *, actor: dict, ip: str = "") -> dict:
    uid = str(userid or "").strip()
    if not uid:
        raise ModerationError("No player named")
    # Deliberately no name lookup: an unban targets someone who is by definition
    # not connected, so the live roster cannot name them.
    return _run(
        audit.PLAYER_UNBAN, lambda: gameapi.unban(uid),
        actor=actor, ip=ip, target=uid,
    )


# ─── The ban list, read from the server's own file ────────


def ban_list_path() -> Optional[str]:
    """
    Where the game keeps `banlist.txt`, if it can be found.

    Beside `PalWorldSettings.ini` in the same `Config/<Platform>Server` directory.
    Returns None rather than guessing, so the UI can say "not found" instead of
    showing an empty list that looks like "nobody is banned".
    """
    explicit = os.environ.get("PALWORLD_BANLIST", "").strip()
    if explicit:
        return explicit if os.path.exists(explicit) else None

    ini = savefiles.find_settings_ini()
    if not ini:
        return None
    candidate = os.path.join(os.path.dirname(ini), "banlist.txt")
    return candidate if os.path.exists(candidate) else None


def list_bans() -> dict[str, Any]:
    """
    The server's ban list. Read-only, and never mirrored into SQLite.

    A local copy would drift the moment anyone edited the server's file by hand,
    and a ban list that disagrees with the game's is worse than not having one.
    """
    path = ban_list_path()
    if not path:
        return {
            "found": False,
            "path": "",
            "bans": [],
            "note": (
                "banlist.txt was not found. It is created the first time someone is "
                "banned, and lives beside PalWorldSettings.ini. Set PALWORLD_BANLIST "
                "if yours is elsewhere."
            ),
        }

    entries: list[str] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                cleaned = line.strip()
                if cleaned and not cleaned.startswith("#"):
                    entries.append(cleaned)
    except OSError as e:
        return {"found": False, "path": path, "bans": [], "note": f"Could not read it: {e}"}

    return {"found": True, "path": path, "bans": entries, "note": ""}
