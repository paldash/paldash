"""
Request authorization for the backend.

The backend used to have no authentication at all: it bound to loopback and
trusted the Next.js layer completely, so every proxy bug was an auth bypass and
anything that could reach port 8400 had full control.

Now the session token travels with the request (`X-Session-Token`) and the
backend resolves it against its own database. The proxy no longer asserts who the
caller is — it forwards a credential the backend verifies. Nothing here can be
spoofed by setting a header.

Guests (no token) are still permitted for read paths that the guest visibility
policy allows; the proxy applies those toggles and strips personal data.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import HTTPException, Request

import accounts
import audit
import policy as policy_module
import privacy
import roles

logger = logging.getLogger(__name__)

SESSION_HEADER = "X-Session-Token"


def client_ip(request: Request) -> str:
    """
    Caller's address.

    X-Forwarded-For is honoured because the documented deployment puts this
    behind a reverse proxy. It is used for rate-limiting and audit context only —
    never for authorization — so a spoofed value cannot grant access, at worst it
    lets an attacker spread their guessing budget.
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "") or ""


def current_user(request: Request) -> Optional[dict[str, Any]]:
    """The signed-in user, or None for an anonymous/guest request."""
    token = request.headers.get(SESSION_HEADER, "")
    return accounts.resolve_session(token) if token else None


def actor(request: Request) -> dict[str, Any]:
    """Who is acting, for audit purposes. Always returns something loggable."""
    user = current_user(request)
    if user:
        return {"username": user["username"], "role": user["role"], "ip": client_ip(request)}
    return {"username": None, "role": "guest", "ip": client_ip(request)}


def effective_capabilities(user: Optional[dict[str, Any]]) -> set[str]:
    """Role capabilities intersected with what the security level allows."""
    role = user["role"] if user else "guest"
    allowed = set(policy_module.allowed_capabilities())
    return roles.effective_capabilities(role, allowed)


def require(request: Request, capability: str) -> dict[str, Any]:
    """
    Enforce a capability, or raise 401/403.

    Denials are audited: a 403 on the save editor is exactly the kind of event
    worth seeing later.
    """
    user = current_user(request)
    granted = effective_capabilities(user)

    if capability in granted:
        return user or {"username": None, "role": "guest"}

    who = actor(request)
    role = who["role"]

    # Distinguish "you are not signed in" from "your role does not allow this"
    # and from "this server has writes switched off entirely" — they need
    # different fixes and a single generic message helps nobody.
    if user is None:
        audit.record(
            audit.DENIED,
            username=None, role="guest", target=capability,
            detail="not authenticated", ip=who["ip"], result=audit.RESULT_DENIED,
        )
        raise HTTPException(401, "Sign in to do this.")

    audit.record(
        audit.DENIED,
        username=who["username"], role=role, target=capability,
        detail="capability not granted", ip=who["ip"], result=audit.RESULT_DENIED,
    )

    if capability in roles.POLICY_GATED and capability in roles.capabilities_for(role):
        level = policy_module.load_policy()["securityLevel"]
        raise HTTPException(
            403,
            f"'{capability}' is blocked by the current security level "
            f"('{level}'). Raise it in the Access tab, or via SECURITY_LEVEL "
            f"in your compose file.",
        )

    raise HTTPException(
        403,
        f"Your role ({roles.ROLES.get(role, {}).get('label', role)}) does not "
        f"allow '{capability}'.",
    )


def require_user(request: Request, capability: str) -> dict[str, Any]:
    """Like `require`, but guarantees a real signed-in account is returned."""
    user = require(request, capability)
    if not user or not user.get("username"):
        raise HTTPException(401, "Sign in to do this.")
    return user


def linked_uid(user: Optional[dict[str, Any]]) -> str:
    """
    The character uid an account is linked to, normalised, or "".

    One accessor because the field has **two spellings and both look right**:
    `steam_uid` is the database column, `steamUid` is what
    `accounts._row_to_user` actually returns. Reading the column name off a
    resolved user gets `None` every time, and every caller that did so failed
    silently — `get_discoveries` decided nobody had discovered anything (which
    emptied the map's fast-travel layer under the default policy), and
    `/api/privacy/me` told every account it had no linked character.

    Normalised here as well, because the *other* half of that trap is comparing
    a dash-stripped account uid against the dashed one `Level.sav` stores.
    """
    if not user:
        return ""
    return privacy.normalise_uid(user.get("steamUid"))
