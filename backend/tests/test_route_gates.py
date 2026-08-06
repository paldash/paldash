"""
Every route needs BOTH gates, and this is what stops the next one shipping without.

`AGENTS.md`:

    "The backend authenticates for itself" was an aspiration in eleven places.
    A sweep of all 112 routes found /api/refresh, /api/progress,
    /api/inventory/{id}, /api/players/{uid}, /api/settings/ini,
    /api/world/fasttravel, /api/world/reference, /api/roles, /api/policy,
    /api/reports and the breeding reference routes with **no `authz.require` at
    all** — reachable only through the proxy, and therefore trusting exactly
    what that section says not to trust.

That sweep was done by hand, once. Two of the eleven were also filter bypasses:
`/api/inventory/{id}` returned any container's contents by id, going around every
base-privacy check built on top of it.

**The rule is that neither gate substitutes for the other.** The proxy allowlist
decides what the outside world can address; `authz.require` decides what the
caller may do once addressed. A route with only the first trusts the proxy — the
thing the security model explicitly does not do. A route with only the second is
unreachable, which is a subtler failure: the feature is written, the tests pass,
and the tab is empty.

These tests enumerate the live FastAPI app, so a route added tomorrow is covered
without anyone remembering to come back here.
"""

from __future__ import annotations

import inspect
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import main  # noqa: E402

PERMISSIONS_TS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "src", "lib", "permissions.ts",
)

#: Routes the proxy deliberately does not carry.
#:
#: `/api/health` is hit by the container's own healthcheck, and the auth routes
#: are reached by the Next.js auth handler directly — the allowlist REFUSES
#: those on purpose, so that the proxy can never be used to mint a session.
_NOT_PROXIED = {
    "/api/health",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/session",
}

#: Calls that ENFORCE. Each raises rather than returning a default, which is the
#: property that makes it a gate.
#:
#: **`authz.current_user` is deliberately NOT here, and neither is `_viewer`.**
#: Both resolve an identity and return `None`/`"guest"` when there is none, so a
#: route using only those is open and then filtered — a different thing, and the
#: distinction this test exists to hold.
_GATE_CALLS = ("authz.require", "authz.require_user")

#: An inline refusal is a gate too. `/api/auth/password` resolves the session
#: itself and raises 401 when there is none, because "change your own password"
#: is authorised by *having an account* rather than by holding a capability —
#: there is no capability that would express it.
_INLINE_REFUSAL = "HTTPException(401"


def _gating_helpers() -> set[str]:
    """
    Names of `main` helpers that enforce, found rather than listed.

    Routes do not all call `authz.require` inline: `/api/moderate/kick` calls
    `_moderator(request)`, which is `authz.require_user(request,
    PLAYERS_MODERATE)`. A scan that only looked for the direct call reported
    nineteen gated routes as ungated, including every moderation and sort
    endpoint — a false alarm on a security test is worse than no test, because
    the next real one gets waved through.

    One level of indirection is enough for this codebase and is checked rather
    than assumed: a helper qualifies only if its own body enforces.
    """
    import ast

    tree = ast.parse(inspect.getsource(main))
    helpers = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        body = ast.unparse(node)
        if any(call in body for call in _GATE_CALLS):
            helpers.add(node.name)
    return helpers


def _routes() -> list[tuple[str, str, object]]:
    out = []
    for route in main.app.routes:
        path = getattr(route, "path", "")
        endpoint = getattr(route, "endpoint", None)
        if not path.startswith("/api/") or endpoint is None:
            continue
        for method in sorted(getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}):
            out.append((method, path, endpoint))
    return out


def test_there_are_routes_to_check():
    """A scan that silently found nothing would pass every test below."""
    assert len(_routes()) > 80


def test_every_route_authenticates_for_itself():
    """
    **The eleven-route bug, as a test.** A route reachable only through the proxy
    is still a route that trusts the proxy, and `authz.py` exists precisely so
    that a forged header does nothing.
    """
    helpers = _gating_helpers()
    ungated = []
    for method, path, endpoint in _routes():
        if path in _NOT_PROXIED:
            continue
        try:
            source = inspect.getsource(endpoint)
        except (OSError, TypeError):  # pragma: no cover
            continue
        direct = any(call in source for call in _GATE_CALLS)
        direct = direct or _INLINE_REFUSAL in source
        delegated = any(f"{name}(" in source for name in helpers)
        if not (direct or delegated):
            ungated.append(f"{method} {path}")

    assert ungated == [], (
        "these routes have no authz gate — the proxy is not an authenticator:\n  "
        + "\n  ".join(ungated)
    )


def _allowlist_patterns() -> list[re.Pattern]:
    with open(PERMISSIONS_TS, encoding="utf-8") as f:
        text = f.read()
    # `[^/]*` was the bug: every pattern containing an escaped slash
    # (`/^world\/items$/`) terminated early, so 20 of 109 parsed and the test
    # "found" ninety unreachable routes. Match to the `$/` that closes the
    # literal instead, non-greedily.
    return [
        re.compile(p.replace("\\/", "/"))
        for p in re.findall(r"pattern:\s*/(\^.*?)\$/", text)
    ]


def test_the_allowlist_is_readable():
    assert len(_allowlist_patterns()) > 90


def test_every_route_is_reachable_through_the_proxy():
    """
    The other direction, and the one that fails quietly: a backend route with no
    allowlist entry is refused before it is ever called, so the feature is
    finished, its tests pass, and the tab is empty. `/api/world/effigies` and the
    breeding routes both landed that way.

    Path parameters are substituted with a sample that matches the allowlist's
    own character classes — those classes are deliberately narrow (ids are the
    game's own, so letters, digits and underscores), and a route whose real ids
    are wider than its pattern is itself a bug worth failing on.
    """
    patterns = _allowlist_patterns()
    unreachable = []

    for method, path, _endpoint in _routes():
        if path in _NOT_PROXIED:
            continue
        # The proxy strips `/api/` and matches the remainder.
        #
        # Several probes, and reachable if ANY of them matches — which is the
        # honest reading of the question. Some patterns enumerate their values
        # (`^export/(world|guild|base|container)$`) and no generic placeholder
        # can satisfy those; others use `[A-Za-z0-9-]+`, which an underscore
        # fails and a plain word passes. A single probe reported six live routes
        # as dead.
        stem = path[len("/api/"):]
        probes = [
            re.sub(r"\{[^}]+\}", value, stem)
            for value in ("sampleid", "world", "pal", "abc-123")
        ]
        if not any(p.match(probe) for probe in probes for p in patterns):
            unreachable.append(f"{method} {path}")

    assert unreachable == [], (
        "these routes have no entry in src/lib/permissions.ts and are therefore "
        "unreachable — add one, or they are dead code:\n  " + "\n  ".join(unreachable)
    )
