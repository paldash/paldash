"""
Palworld Dashboard — save-file backend.

Binds to loopback by default. It authenticates for itself: the session token
arrives as `X-Session-Token` and is resolved against the local database (see
authz.py), so the Next.js proxy forwards a credential rather than asserting an
identity. Loopback binding is defence in depth, not the only control — but there
is still no reason to publish this port.

Everything that writes is gated twice: on the caller's capability, and on
safety.assert_writable(), which only passes when the game server is *provably*
stopped.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel

import accounts
import announcements
import audit
import authz
import baseprivacy
import baseassign
import basesupply
import breeding
import charedit
import db
import editschema
import elements
import gameapi
import gameversion
import gamedata
import guildedit
import iniwatch
import itemsource
import lifecycle
import metrics
import mods
import moderate
import optimise
import palcheck
import palclone
import palimport
import palstats
import passiveeffects
import policy as policy_module
import progresscheck
import privacy
import reports
import roles as roles_module
import savecache
import saveedit
import saveexport
import saveimport
import itemclone
import slotedit
import teleport
import soloexport
import schedule as schedule_module
import settings_ini
import settingshelp
import viewcache
import worldobjects
import habitats
import backup as backup_module
from backupstore import BackupError
from parser import (
    extract_player_progress,
    extract_player_save,
    load_gvas,
    progress_totals,
)
from safety import ServerRunningError, assert_writable, get_server_state
from savefiles import find_world_dirs, get_default_world_dir, get_player_sav_path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Ceiling on anything the dashboard accepts as an upload (audit S9). A world
# export of a large server is a few MB; 64 is generous. The point is that an
# unbounded read into memory is a denial-of-service against the machine running
# the game server, which is the thing this dashboard exists not to disturb.
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "64")) * 1024 * 1024


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """
    Prepare storage and make sure somebody can actually sign in.

    Lifespan rather than `@app.on_event("startup")`, which is deprecated and
    logs a warning on every container boot.
    """
    db.init()
    schedule_module.init()
    # Its own table, created here so `describe()` on a fresh database returns
    # "unknown" rather than raising on the first Settings tab load.
    iniwatch.init()
    accounts.purge_expired()
    schedule_module.start()
    # After db.init(), because the first sample writes a row.
    metrics.start()
    # After db.init(): this consults the metrics table to decide whether the
    # game server is too busy to parse.
    savecache.recover_stale_schema()
    created = accounts.bootstrap_from_env()
    if created:
        audit.record(
            audit.USER_CREATE,
            username="system", role="owner", target=created,
            detail="bootstrapped first Owner from PANEL_PASSWORD",
        )
    yield
    # Nothing to tear down: the scheduler and metrics samplers run on daemon
    # threads, and SQLite connections are per-call.


app = FastAPI(title="Palworld Save Backend", version="3.0.0", lifespan=_lifespan)


# ─── Authentication ──────────────────────────────────────


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
def login(req: LoginRequest, request: Request) -> dict[str, Any]:
    """
    Verify credentials and open a session.

    Rate limiting lives in accounts.authenticate: per-IP and per-username, with
    exponential backoff persisted across restarts.
    """
    ip = authz.client_ip(request)
    try:
        token, user = accounts.authenticate(
            req.username, req.password, ip=ip,
            user_agent=request.headers.get("User-Agent", ""),
        )
    except accounts.RateLimited as e:
        audit.record(
            audit.RATE_LIMITED, username=req.username, target="login",
            detail=f"retry after {e.retry_after}s", ip=ip, result=audit.RESULT_DENIED,
        )
        raise HTTPException(429, str(e), headers={"Retry-After": str(e.retry_after)})
    except accounts.AccountError as e:
        audit.record(
            audit.LOGIN_FAILED, username=req.username, target="login",
            detail=str(e), ip=ip, result=audit.RESULT_FAILED,
        )
        raise HTTPException(401, str(e))

    audit.record(audit.LOGIN, username=user["username"], role=user["role"], ip=ip)
    return {
        "token": token,
        "user": user,
        "capabilities": sorted(authz.effective_capabilities(user)),
    }


@app.post("/api/auth/logout")
def logout(request: Request) -> dict[str, Any]:
    token = request.headers.get(authz.SESSION_HEADER, "")
    who = authz.actor(request)
    revoked = accounts.revoke_session(token) if token else False
    if revoked:
        audit.record(
            audit.LOGOUT, username=who["username"], role=who["role"], ip=who["ip"]
        )
    return {"ok": True, "revoked": revoked}


@app.get("/api/auth/session")
def whoami(request: Request) -> dict[str, Any]:
    """Who the caller is and what they may do. Anonymous callers get guest."""
    user = authz.current_user(request)
    policy = policy_module.load_policy()
    return {
        "user": user,
        "role": user["role"] if user else "guest",
        "capabilities": sorted(authz.effective_capabilities(user)),
        "securityLevel": policy["securityLevel"],
        "visibility": None if user else policy["guestVisibility"],
        "anyUsers": accounts.user_count() > 0,
    }


class PasswordChange(BaseModel):
    currentPassword: str
    newPassword: str


@app.post("/api/auth/password")
def change_own_password(req: PasswordChange, request: Request) -> dict[str, Any]:
    """
    Change your own password.

    Requires the current one, so a stolen session cannot lock the real owner out.
    Every other session for this account is revoked afterwards.
    """
    user = authz.current_user(request)
    if not user:
        raise HTTPException(401, "Sign in to do this.")

    ip = authz.client_ip(request)
    try:
        accounts.authenticate(user["username"], req.currentPassword, ip=ip)
    except accounts.AccountError:
        audit.record(
            audit.USER_PASSWORD, username=user["username"], role=user["role"],
            target=user["username"], detail="wrong current password",
            ip=ip, result=audit.RESULT_FAILED,
        )
        raise HTTPException(403, "Current password is incorrect.")

    try:
        accounts.set_password(user["username"], req.newPassword)
    except accounts.AccountError as e:
        raise HTTPException(400, str(e))

    audit.record(
        audit.USER_PASSWORD, username=user["username"], role=user["role"],
        target=user["username"], detail="changed own password", ip=ip,
    )
    return {"ok": True, "signedOutEverywhere": True}


# ─── Accounts ────────────────────────────────────────────


@app.get("/api/roles")
def get_roles(request: Request) -> list[dict]:
    """Role presets and what each grants."""
    authz.require(request, roles_module.VIEW_BASIC)
    return roles_module.describe()


@app.get("/api/users")
def get_users(request: Request) -> list[dict]:
    authz.require(request, roles_module.USERS_MANAGE)
    return accounts.list_users()


class UserCreate(BaseModel):
    username: str
    password: str
    role: str = roles_module.DEFAULT_ROLE
    steamUid: str = ""
    displayName: str = ""
    mustChangePassword: bool = True


@app.post("/api/users")
def add_user(req: UserCreate, request: Request) -> dict:
    actor_user = authz.require_user(request, roles_module.USERS_MANAGE)

    if not roles_module.can_manage(actor_user["role"], req.role):
        raise HTTPException(403, "You cannot create an account above your own role.")

    try:
        user = accounts.create_user(
            req.username, req.password, role=req.role,
            steam_uid=req.steamUid, display_name=req.displayName,
            must_change_password=req.mustChangePassword,
        )
    except accounts.AccountError as e:
        raise HTTPException(400, str(e))

    audit.record(
        audit.USER_CREATE, username=actor_user["username"], role=actor_user["role"],
        target=req.username, detail={"role": req.role},
        ip=authz.client_ip(request),
    )
    return user


class UserUpdate(BaseModel):
    role: Optional[str] = None
    steamUid: Optional[str] = None
    displayName: Optional[str] = None
    disabled: Optional[bool] = None
    password: Optional[str] = None


@app.patch("/api/users/{username}")
def edit_user(username: str, req: UserUpdate, request: Request) -> dict:
    actor_user = authz.require_user(request, roles_module.USERS_MANAGE)

    existing = accounts.get_user(username)
    if not existing:
        raise HTTPException(404, f"No such user: {username}")

    # You may not act on somebody more privileged than you, nor promote anyone
    # above yourself.
    if not roles_module.can_manage(actor_user["role"], existing["role"]):
        raise HTTPException(403, "You cannot modify an account above your own role.")
    if req.role and not roles_module.can_manage(actor_user["role"], req.role):
        raise HTTPException(403, "You cannot grant a role above your own.")

    try:
        if req.password:
            accounts.set_password(username, req.password)
        user = accounts.update_user(
            username, role=req.role, steam_uid=req.steamUid,
            display_name=req.displayName, disabled=req.disabled,
        )
    except accounts.AccountError as e:
        raise HTTPException(400, str(e))

    audit.record(
        audit.USER_UPDATE, username=actor_user["username"], role=actor_user["role"],
        target=username,
        detail=req.model_dump(exclude_none=True, exclude={"password"})
        | ({"password": "changed"} if req.password else {}),
        ip=authz.client_ip(request),
    )
    return user


@app.delete("/api/users/{username}")
def remove_user(username: str, request: Request) -> dict:
    actor_user = authz.require_user(request, roles_module.USERS_MANAGE)

    existing = accounts.get_user(username)
    if not existing:
        raise HTTPException(404, f"No such user: {username}")
    if not roles_module.can_manage(actor_user["role"], existing["role"]):
        raise HTTPException(403, "You cannot delete an account above your own role.")
    if existing["username"].lower() == actor_user["username"].lower():
        raise HTTPException(400, "You cannot delete your own account.")

    try:
        accounts.delete_user(username)
    except accounts.AccountError as e:
        raise HTTPException(400, str(e))

    audit.record(
        audit.USER_DELETE, username=actor_user["username"], role=actor_user["role"],
        target=username, ip=authz.client_ip(request),
    )
    return {"ok": True}


# ─── Audit log ───────────────────────────────────────────


@app.get("/api/audit")
def get_audit(
    request: Request,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    action: Optional[str] = None,
    username: Optional[str] = None,
    result: Optional[str] = None,
    since: Optional[str] = None,
) -> dict[str, Any]:
    authz.require(request, roles_module.AUDIT_VIEW)
    return {
        **audit.query(
            limit=limit, offset=offset, action=action,
            username=username, result=result, since=since,
        ),
        "actions": audit.actions_seen(),
    }


# ─── Health & status ─────────────────────────────────────


@app.get("/api/health")
def health() -> dict[str, Any]:
    state = get_server_state()
    world_dirs = find_world_dirs()
    return {
        "status": "ok",
        "serverRunning": state.running,
        "server": state.as_dict(),
        "saveDir": os.environ.get("SAVE_BASE_DIR", "/palworld/Pal/Saved/SaveGames/0"),
        "worldDir": get_default_world_dir(),
        "worldGuids": [os.path.basename(d) for d in world_dirs],
        "worldCount": len(world_dirs),
        "cache": savecache.status(),
        "breedingData": breeding.data_available(),
        "gameData": gamedata.available(),
        "lifecycle": lifecycle.status(),
        "viewCache": viewcache.stats(),
    }


# ─── Server lifecycle ────────────────────────────────────


class ShutdownNote(BaseModel):
    reason: Optional[str] = ""


def _lifecycle(request: Request, action: str, runner, supported) -> dict[str, Any]:
    """Shared authorization, auditing and error handling for container control."""
    user = authz.require_user(request, roles_module.SERVER_CONTROL)
    ip = authz.client_ip(request)
    try:
        result = runner()
    except RuntimeError as e:
        audit.record(
            action, username=user["username"], role=user["role"],
            detail=str(e), ip=ip, result=audit.RESULT_FAILED,
        )
        raise HTTPException(501 if not supported() else 500, str(e))

    audit.record(action, username=user["username"], role=user["role"], detail=result, ip=ip)
    return result


@app.post("/api/server/note-shutdown")
def note_shutdown(req: ShutdownNote, request: Request) -> dict[str, Any]:
    """
    Told by the UI that a shutdown was just issued through the game's REST API.

    Starts watching for the server to come back, so we can tell the difference
    between "the container restarted it" and "the game process is gone and
    nothing is going to bring it back".
    """
    authz.require(request, roles_module.SERVER_CONTROL)
    return lifecycle.note_shutdown(req.reason or "")


@app.post("/api/server/restart")
def restart_server(request: Request) -> dict[str, Any]:
    """Run the configured RESTART_COMMAND, if the operator enabled one."""
    return _lifecycle(
        request, audit.SERVER_RESTART,
        lifecycle.run_restart_command, lifecycle.restart_supported,
    )


@app.post("/api/server/start-container")
def start_container(request: Request) -> dict[str, Any]:
    """Bring the server container back after maintenance."""
    return _lifecycle(
        request, audit.SERVER_START,
        lifecycle.run_start_command, lifecycle.start_supported,
    )


@app.post("/api/server/stop-container")
def stop_container(request: Request) -> dict[str, Any]:
    """
    Stop the whole server container, not just the game process.

    This is the clean way to prepare for save edits: a stopped container cannot
    relaunch the server underneath an in-progress write.
    """
    return _lifecycle(
        request, audit.SERVER_STOP,
        lifecycle.run_stop_command, lifecycle.stop_supported,
    )


# ─── Access policy ───────────────────────────────────────


@app.get("/api/policy")
def get_policy(request: Request) -> dict[str, Any]:
    """Current security level and guest visibility toggles."""
    authz.require(request, roles_module.VIEW_BASIC)
    return policy_module.describe()


class PolicyUpdate(BaseModel):
    """
    Every field `save_policy` understands has to be declared here.

    Pydantic drops what it does not know, so an omission is silent: the request
    returns 200, the setting never changes, and only hand-editing policy.json
    works. `discoveryVisibility` was missing this way once already — adding a
    field to `save_policy` without adding it here is the failure to watch for.
    """

    securityLevel: Optional[str] = None
    guestVisibility: Optional[dict[str, bool]] = None
    discoveryVisibility: Optional[str] = None
    baseVisibility: Optional[str] = None
    serverTotalsVisibility: Optional[str] = None
    allPalsVisibility: Optional[str] = None
    worldObjectVisibility: Optional[dict[str, str]] = None
    # Per-category overrides for fast travel vs effigies. Declared here or
    # Pydantic drops it silently — the trap this class's docstring names.
    discoveryCategoryVisibility: Optional[dict[str, str]] = None
    # A named starting point for the four visibility thresholds. Expanded into
    # those same four fields below rather than stored, so nothing downstream has
    # to know presets exist and the individual dials stay the source of truth.
    visibilityPreset: Optional[str] = None


@app.post("/api/policy")
def update_policy(req: PolicyUpdate, request: Request) -> dict[str, Any]:
    user = authz.require_user(request, roles_module.POLICY_MANAGE)
    changes = req.model_dump(exclude_none=True)

    preset_id = changes.pop("visibilityPreset", None)
    if preset_id is not None:
        preset = policy_module.VISIBILITY_PRESETS.get(preset_id)
        if preset is None:
            raise HTTPException(
                400,
                f"Unknown visibility preset {preset_id!r}. Known: "
                + ", ".join(policy_module.VISIBILITY_PRESETS),
            )
        # The preset goes in first so an explicit field in the same request
        # still wins. "Community, but keep discoveries open" should be one
        # request, not two.
        changes = {**preset["values"], **changes}

    try:
        policy_module.save_policy(changes)
    except ValueError as e:
        audit.record(
            audit.POLICY_UPDATE, username=user["username"], role=user["role"],
            detail=str(e), ip=authz.client_ip(request), result=audit.RESULT_FAILED,
        )
        raise HTTPException(400, str(e))

    audit.record(
        audit.POLICY_UPDATE, username=user["username"], role=user["role"],
        detail=changes, ip=authz.client_ip(request),
    )
    return policy_module.describe()


@app.post("/api/refresh")
def refresh(request: Request, force: bool = Query(True)) -> dict[str, Any]:
    """
    Ask for a re-parse. Returns immediately; parsing runs in the background.

    **This checks for itself now.** The route had no `authz.require` at all — it
    relied entirely on the Next.js allowlist, which is the one thing
    `backend/authz.py` exists to not do. The proxy forwards a credential; it does
    not assert an identity, and a backend route that trusts it has no defence if
    anything ever reaches port 8400 directly.

    **Forcing requires an account.** A parse is the most expensive thing this
    dashboard can do to a machine that is also running a game server, so the
    ability to demand one on request is not something to hand to an
    unauthenticated visitor. A guest still gets a parse — theirs is simply
    subject to the normal interval and "has the save even changed" checks, which
    is what `force` skips.
    """
    authz.require(request, roles_module.VIEW_BASIC)
    user = authz.current_user(request)
    if force and not user:
        force = False
    return savecache.request_parse(force=force)


# ─── Map privacy (each player, about themselves) ─────────


class PrivacyRequest(BaseModel):
    mode: str


@app.get("/api/privacy/hidden")
def get_hidden_uids(request: Request) -> dict[str, list[str]]:
    """
    Player uids this session must not be shown, by category.

    Exists for the Next.js proxy: live positions come from the game's REST API
    rather than from a save, so the proxy has to apply the same rules or a hidden
    player would vanish from the map and keep showing as a live dot on it.

    Returning ids the caller may not see is safe — they are already known to
    anyone who can read the roster, and the alternative is the proxy shipping
    every player's position and filtering in the browser.
    """
    authz.require(request, roles_module.VIEW_BASIC)
    hidden = privacy.hidden_uids(*_viewer(request))
    return {key: sorted(value) for key, value in hidden.items()}


class BaseVisibilityRequest(BaseModel):
    hidden: bool


@app.get("/api/privacy/bases")
def get_manageable_bases(request: Request) -> dict[str, Any]:
    """
    Bases this account may hide, with their current state.

    A guild master sees their guild's; everyone else sees a reason instead of an
    empty list, because "you are not a guild master" and "nothing is hidden" are
    different answers.
    """
    user = authz.require_user(request, roles_module.VIEW_BASIC)
    return baseprivacy.manageable_bases(user["username"], user["role"])


@app.post("/api/privacy/bases/{base_id}")
def set_base_visibility(
    base_id: str, req: BaseVisibilityRequest, request: Request
) -> dict[str, Any]:
    """
    Hide or unhide one base.

    Authorisation is ownership, not rank: a base belongs to a guild, so its guild
    master decides. Staff have no override here and need none — nothing is ever
    concealed from someone ranked above the person who hid it.
    """
    user = authz.require_user(request, roles_module.VIEW_BASIC)
    allowed, why = baseprivacy.can_manage(base_id, user["username"])
    if not allowed:
        raise HTTPException(403, why)

    result = baseprivacy.set_hidden(
        base_id, req.hidden, username=user["username"], role=user["role"]
    )
    audit.record(
        audit.USER_UPDATE, username=user["username"], role=user["role"],
        target=f"base_privacy:{base_id}",
        detail={"hidden": req.hidden, "authority": why},
        ip=authz.client_ip(request),
    )
    return result


@app.get("/api/privacy/me")
def get_my_privacy(request: Request) -> dict[str, Any]:
    """This account's own map-privacy setting, and what the choices mean."""
    user = authz.require_user(request, roles_module.VIEW_BASIC)
    return {
        "mode": privacy.get_mode(user["username"]),
        "modes": privacy.describe_modes(),
        "role": user["role"],
        # Without a linked character there is nothing on the map to hide, so the
        # UI can say that rather than offering a setting with no effect.
        #
        # `steamUid`, not `steam_uid` — the latter is the database column and
        # `accounts._row_to_user` does not return it, so this was always False
        # and every account was told it had no linked character. Same slip as
        # `get_discoveries`, which is why both now go through
        # `authz.linked_uid`.
        "linkedToPlayer": bool(authz.linked_uid(user)),
        "hidesFrom": [
            name for name in roles_module.ROLES
            if privacy.conceals(name, user["role"], "player")
        ],
    }


@app.post("/api/privacy/me")
def set_my_privacy(req: PrivacyRequest, request: Request) -> dict[str, Any]:
    """
    Change this account's own setting. Nobody can set anyone else's.

    Deliberately not behind a management capability: it is the player's own
    visibility, and an Owner overriding it would defeat the point. Owners can
    still see everyone below them, which is what oversight actually needs.
    """
    user = authz.require_user(request, roles_module.VIEW_BASIC)
    try:
        mode = privacy.set_mode(user["username"], req.mode)
    except ValueError as e:
        raise HTTPException(400, str(e))

    audit.record(
        audit.USER_UPDATE, username=user["username"], role=user["role"],
        target=f"privacy:{user['username']}", detail={"mode": mode},
        ip=authz.client_ip(request),
    )
    return {"mode": mode, "ok": True}


# ─── Save data (read-only) ───────────────────────────────


def _viewer(request: Request) -> tuple[str, str]:
    """(role, username) for privacy filtering. A guest has no username."""
    user = authz.current_user(request)
    if not user:
        return "guest", ""
    return str(user.get("role") or "guest"), str(user.get("username") or "")


def _foreign_guild_ids(request: Request) -> Optional[set[str]]:
    """
    Guild ids this viewer is not in, when `baseVisibility` withholds them.

    `None` means "withhold nothing" and is deliberately distinct from an empty
    set: empty means the viewer belongs to no guild and therefore everything is
    foreign, which is the opposite outcome. Collapsing the two is how a filter
    that should be off ends up hiding the entire world.

    Separate from `privacy.py` because it answers a different question. Privacy
    is a **choice a player makes**, and it only protects accounts — someone who
    has never signed into the dashboard has no row in `users`, so nothing hides
    them however private the default is. This is the operator's blanket rule,
    which needs no account to take effect.
    """
    role, _ = _viewer(request)
    user = authz.current_user(request)

    # Staff are exempt, always. This is the same rule per-player privacy
    # follows — "a player can never hide from staff, so moderation works
    # without anyone maintaining an exemption list" — and it applies with more
    # force here, because this filter is the *operator's* own setting and an
    # operator who cannot see the bases they are responsible for has misread
    # the switch. `players.moderate` is the line: it is the capability that
    # means answerable for what happens on this server.
    if roles_module.PLAYERS_MODERATE in authz.effective_capabilities(user):
        return None

    level = policy_module.load_policy().get(
        "baseVisibility", policy_module.DEFAULT_BASE_VISIBILITY
    )
    if policy_module.may_see_all_bases(role, level):
        return None

    guilds = savecache.get_section("guilds")
    if not guilds:
        return None

    # A guest, or an account with no linked character, is in no guild — so at
    # anything short of `everyone` they see no guild bases at all. That is the
    # setting working rather than a bug: "only your own guild's" is an empty set
    # when you have no guild.
    viewer_uid = authz.linked_uid(user)

    own: set[str] = set()
    if viewer_uid:
        for guild in guilds:
            members = {
                privacy.normalise_uid(m.get("uid"))
                for m in (guild.get("members") or [])
                if m.get("uid")
            }
            if viewer_uid in members:
                own.add(str(guild.get("id") or ""))

    return {str(g.get("id") or "") for g in guilds} - own


def _foreign_guild_base_ids(request: Request) -> set[str]:
    """
    Bases belonging to guilds this viewer is not in, when policy withholds them.

    Returns nothing when the world has not been parsed — the caller's other
    filters still apply, and a missing world means there are no bases to
    withhold rather than every base to withhold.
    """
    foreign = _foreign_guild_ids(request)
    if foreign is None:
        return set()

    return {
        str(base.get("id") or "")
        for base in savecache.get_section("bases")
        if str(base.get("guildId") or "") in foreign
    }


def _hidden_base_ids(request: Request) -> set[str]:
    """
    Every base id concealed from this viewer, for either of the two reasons.

    Three reasons now: per-player privacy, per-base visibility set by a guild
    master, and the server-wide `baseVisibility` policy.

    One function because there are three endpoints that must agree — the base
    markers, the objects standing inside them, and their storage contents. A
    base dropped from one and returned by another is not hidden, and the
    coordinates travel on the objects, so that mistake publishes the location
    while looking like it concealed it.

    The person-level half deliberately runs `privacy.filter_bases` and takes the
    difference rather than reimplementing its rule. Whether `player_bases` covers
    a given base depends on solo-versus-shared guild membership, and a second
    copy of that logic is a second thing to get wrong.
    """
    viewer = _viewer(request)
    hidden = privacy.hidden_uids(*viewer)

    ids: set[str] = set() | _foreign_guild_base_ids(request)
    if hidden["bases"] or hidden["guilds"]:
        bases = savecache.get_section("bases")
        kept = {
            str(b.get("id") or "")
            for b in privacy.filter_bases(
                bases, savecache.get_section("guilds"),
                hidden["bases"], hidden["guilds"],
            )
        }
        ids |= {str(b.get("id") or "") for b in bases} - kept

    return ids | baseprivacy.hidden_base_ids(*viewer)


@app.get("/api/bases")
def get_bases(request: Request) -> list[dict]:
    """
    Base camps, minus any hidden from this viewer.

    Two reasons a base is concealed: a *person* hid themselves and their guild's
    bases with them (`privacy`), or a guild master hid this *specific* base
    (`baseprivacy`). Both resolve to base ids in `_hidden_base_ids`.
    """
    # Gated here as well as in the proxy allowlist. `_viewer()` below resolves
    # an identity and returns "guest" when there is none, which filters but does
    # not refuse — see AGENTS.md: the proxy forwards a credential, it does not
    # assert one, so a route that only filters is trusting exactly what the
    # security model says not to trust.
    authz.require(request, roles_module.VIEW_BASIC)
    return baseprivacy.filter_bases(
        savecache.get_section("bases"), _hidden_base_ids(request)
    )


def _name_guild_roles(guild: dict) -> dict:
    """
    Attach rank NAMES to a guild's chest-access list.

    The save stores bare indices, and "role 2" tells an operator nothing. Only
    the ranks are named: the permission numbers travel unnamed on purpose,
    because the game's permission enum order is not established and a guessed
    mapping would confidently tell someone a rank can kick players when it
    cannot. See `scripts/extract-guild-roles.py`.
    """
    allowed = guild.get("chestAllowedRoles") or []
    # The member cap is the OPERATOR'S, from the INI — not a game constraint,
    # however much "20" looks like one (every difficulty preset ships 20). None
    # when the INI is unreadable, which is the common deployment, and the UI
    # must then show no denominator rather than a guessed one.
    cap = gamedata.server_guild_member_cap()
    if not allowed and cap is None:
        return guild
    return {
        **guild,
        "memberCap": cap,
        "chestAllowedRoleNames": [gamedata.guild_role_name(r) for r in allowed],
        # So the UI can say "2 of 4 ranks" rather than implying a total.
        "roleCount": len((gamedata.guild_roles().get("roles") or {})) or None,
    }


@app.get("/api/guilds")
def get_guilds(request: Request) -> list[dict]:
    """
    Guilds, with hidden members removed and fully hidden guilds dropped.

    **`baseVisibility` applies here too, and originally did not.** That setting
    withheld other guilds' *bases* while the Bases tab's guild roster went on
    listing those same guilds by name, member count and base count — including
    guilds whose members have no dashboard account, which per-player privacy
    cannot reach. Hiding a base while naming its owner and saying how many bases
    they have is not hiding it, and it is the same mistake `_hidden_base_ids`
    exists to prevent one level down.
    """
    # Gated here as well as in the proxy allowlist. `_viewer()` below resolves
    # an identity and returns "guest" when there is none, which filters but does
    # not refuse — see AGENTS.md: the proxy forwards a credential, it does not
    # assert one, so a route that only filters is trusting exactly what the
    # security model says not to trust.
    authz.require(request, roles_module.VIEW_BASIC)
    guilds = savecache.get_section("guilds")

    foreign = _foreign_guild_ids(request)
    if foreign:
        guilds = [g for g in guilds if str(g.get("id") or "") not in foreign]

    hidden = privacy.hidden_uids(*_viewer(request))
    if not hidden["players"] and not hidden["guilds"]:
        return [_name_guild_roles(g) for g in guilds]

    out = []
    for guild in guilds:
        members = guild.get("members") or []
        if any(privacy.normalise_uid(m.get("uid")) in hidden["guilds"] for m in members):
            continue          # guild-wide privacy: the whole guild is concealed
        out.append(_name_guild_roles({
            **guild,
            "members": privacy.filter_players(members, hidden["players"]),
        }))
    return out


def _enriched_pals() -> list[dict]:
    """Every Pal with its friendly names attached. ~12 ms on a 1,905-Pal world."""
    base_names = {
        str(b.get("id") or ""): b.get("name") or ""
        for b in savecache.get_section("bases")
    }
    enriched = []
    for pal in savecache.get_section("pals"):
        details = gamedata.describe_pal(pal.get("speciesId") or "")
        enriched.append(
            {
                **pal,
                # `location` and `baseId` come from the parse (see
                # `parse_worker`); only the human-readable base name is joined
                # here, because base names change without the world changing.
                "baseName": base_names.get(str(pal.get("baseId") or ""), ""),
                "speciesName": details["name"],
                "icon": details["icon"],
                "elements": details["elements"],
                "paldeckNumber": details["paldeckNumber"],
                "passiveSkillNames": [
                    gamedata.passive_name(p) for p in (pal.get("passiveSkills") or [])
                ],
                "activeSkillNames": [
                    gamedata.skill_name(w) for w in (pal.get("activeSkills") or [])
                ],
                # Work suitabilities and rarity come from the bundled tables, not
                # the save — a Pal's work levels are a property of its species.
                # Attached here so the client can filter on "can mine at 3+"
                # without a lookup table of its own.
                "workSuitabilities": details.get("workSuitabilities") or {},
                "rarity": details.get("rarity", 0),
                # HP / Attack / Defense / Work Speed, and how far through the
                # level. The save stores only the *inputs* — level, IVs,
                # condenser rank, souls, trust — and the game computes the rest
                # at load, so there is nothing to read and this has to be
                # calculated. `palstats` says so in the payload.
                #
                # None for the 99 humans and NPCs in the reference world's
                # character map, which carry IVs exactly like a Pal and have no
                # scaling numbers anywhere. Guessing would produce confident
                # stats for a merchant.
                "stats": palstats.describe(pal),
                # An equipped skin, labelled. The save stores the raw id and the
                # game ships no display name for it, so this is derived — see
                # `gamedata.skin_label`. None when absent, which is 2,943 of the
                # live world's 2,963 Pals.
                "skin": gamedata.skin_label(pal.get("skinName")),
            }
        )
    return enriched


@app.get("/api/pals")
def get_pals(request: Request, owner: Optional[str] = None) -> list[dict]:
    """
    Pals, named. Enrichment is cached per parse rather than redone per request.

    Filtering happens *after* the cached build, not before: `?owner=` would
    otherwise make the cache key depend on the query and the shared work would
    never be shared. Narrowing 1,905 rows costs microseconds; naming them costs
    milliseconds.

    **`VIEW_SELF` is enough to read your own Pals.** A palbox is 960 slots and
    the game shows one Pal at a time, so this is the view that most justifies a
    dashboard — and gating it on `VIEW_DETAIL` meant a Player could see nothing
    of their own. Below the `allPalsVisibility` threshold the caller is pinned to
    their own character regardless of `?owner=`.
    """
    authz.require(request, roles_module.VIEW_SELF)
    pals = viewcache.derived("pals:enriched", _enriched_pals)

    # `_scope_pals`, not a filter written out here again.
    #
    # There used to be one, and it had already drifted twice: first on uid
    # normalisation (the save stores dashed GUIDs, `_own_identity` returns them
    # stripped, and comparing raw matched nothing *silently* — a scoped view that
    # looked like "you own no Pals"), and then on shared ownership, which would
    # have left this page hiding the base workers and stored Pals the breeding
    # planner had just started counting.
    effective = _breeding_owner(request, owner)
    return _scope_pals(pals, effective, _guilds_of(effective) if effective else None)


#: How low sanity has to get before it is worth telling someone about.
#
# **This is the game's own threshold, and that was a lucky escape.** It was
# picked here as a judgement call — 50, because a Pal below roughly that point
# starts refusing work and because the live world's distribution separates there
# (33 of 2,963 Pals under 50, the rest clustered in the nineties). It is written
# down as a judgement call in the git history.
#
# `BP_PalGameSetting` turns out to carry `FriendshipPoint_AutoIncrementRequireSanity
# = 50`: the sanity a Pal must hold to keep gaining trust. So the number is the
# game's, not ours, and it now comes from the file rather than from agreement
# with it. The literal remains as the fallback for a missing bundle.
LOW_SANITY = float(
    gamedata.game_setting("FriendshipPoint_AutoIncrementRequireSanity", 50.0)
)


@app.get("/api/welfare")
def get_welfare(request: Request, owner: Optional[str] = None) -> dict:
    """
    Pals that need attention: sick, starving, injured, or losing their minds.

    All four conditions were sitting in the save the whole time and none were
    read. On the live world that is 54 sick, 97 hungry or starving, 21 injured
    and 33 below `LOW_SANITY` — a base quietly falling apart with nothing in the
    dashboard to say so.

    Scoped exactly like `/api/pals`, through the same helper: a Player sees
    their own and their guild's, and nobody gets a roster of someone else's
    struggling base as a side effect of a welfare view.
    """
    authz.require(request, roles_module.VIEW_SELF)
    pals = viewcache.derived("pals:enriched", _enriched_pals)
    effective = _breeding_owner(request, owner)
    scoped = _scope_pals(pals, effective, _guilds_of(effective) if effective else None)

    def _needs_help(pal: dict) -> list[str]:
        problems = []
        if pal.get("workerSick"):
            problems.append("sick")
        if pal.get("physicalHealth"):
            problems.append("injured")
        hunger = pal.get("hungerType")
        if hunger:
            problems.append("starving" if hunger == "Starvation" else "hungry")
        sanity = pal.get("sanity")
        if isinstance(sanity, (int, float)) and sanity < LOW_SANITY:
            problems.append("lowSanity")
        return problems

    affected = []
    counts: dict[str, int] = {}
    for pal in scoped:
        problems = _needs_help(pal)
        if not problems:
            continue
        for problem in problems:
            counts[problem] = counts.get(problem, 0) + 1
        affected.append({**pal, "problems": problems})

    # Worst first: a Pal with three things wrong with it is the one to open.
    affected.sort(key=lambda p: (-len(p["problems"]), p.get("name") or ""))

    # **"Sick" was a flag, and a flag is not an answer.** The game's own table
    # says what each condition costs and how fast the palbox clears it, so the
    # panel can say "Depressed — work -20%, move -10%, palbox cures 10% an hour"
    # instead of a red dot. Only the illnesses actually present are returned:
    # a reference table of all eight beside a roster of two is noise.
    present = {
        str(p.get("workerSick") or "") for p in affected if p.get("workerSick")
    }
    illnesses = [row for row in (gamedata.illness(sick) for sick in sorted(present)) if row]

    return {
        # **THE SCOPE SPREADS FIRST, AND THAT ORDER IS THE BUG FIX.**
        # `_breeding_scope` returns its own `"pals"` — the COUNT of Pals the
        # answer was built from — and spreading it last silently replaced this
        # route's `pals` ARRAY with an integer. Nothing errored server-side; the
        # client got a number where it expected a list, and
        # `report.pals.length.toLocaleString()` threw "Cannot read properties of
        # undefined", killing the My Pals tab for every user.
        #
        # A generic key name in a helper that is spread into other people's
        # payloads is the hazard. Spreading first means an explicit key always
        # wins, which is the direction that cannot surprise anyone.
        **_breeding_scope(request, effective),
        "counts": counts,
        "pals": affected,
        "scanned": len(scoped),
        "lowSanityBelow": LOW_SANITY,
        # What each condition present actually costs.
        "illnesses": illnesses,
        # The palbox cure chance is rolled once per this many seconds, so a
        # percentage without it is a rate with no denominator.
        "palboxCurePeriodSeconds": gamedata.game_setting(
            "PalBoxTimePeriodRecoverySick"
        ),
        # **85, not `LOW_SANITY`.** A worker starts taking short breaks at 85 and
        # has long stopped being useful by 50, so a panel that only warns at 50
        # is answering a different question from the one it appears to answer.
        # See `gamedata.worker_sanity_thresholds`.
        "sanityThresholds": gamedata.worker_sanity_thresholds(),
    }


@app.get("/api/optimise/work")
def get_work_ranking(
    request: Request,
    work: Optional[str] = None,
    owner: Optional[str] = None,
    limit: int = optimise.DEFAULT_LIMIT,
) -> dict:
    """
    Who should be doing each job, best first.

    Scoped exactly like `/api/pals` and `/api/welfare`, through the same helper —
    a Player is answered from their own and their guild's Pals, and the scope
    travels in the payload for the reason `_breeding_scope` documents: a ranking
    computed from one palbox and displayed under a server-wide heading reads as a
    wrong answer rather than as a narrower question.

    Without `work`, every work type is ranked. The suitability levels are read
    from the save and the bundled table; only work *speed* is calculated, and it
    is flagged per row.
    """
    authz.require(request, roles_module.VIEW_SELF)
    pals = viewcache.derived("pals:enriched", _enriched_pals)
    effective = _breeding_owner(request, owner)
    scoped = _scope_pals(pals, effective, _guilds_of(effective) if effective else None)

    types = optimise.work_types()
    wanted = [t for t in types if not work or str(t.get("id")) == work]
    if work and not wanted:
        raise HTTPException(404, f"No work type {work!r}")

    limit = max(0, min(int(limit), 200))
    return {
        "workTypes": types,
        "rankings": [
            {
                "workId": t.get("id"),
                # `display_name` is the bundled table's own key — not `name`,
                # which every other section here uses.
                "workName": t.get("display_name") or t.get("id"),
                "pals": optimise.rank_for_work(scoped, str(t.get("id")), limit=limit),
            }
            for t in wanted
        ],
        **_breeding_scope(request, effective),
    }


@app.get("/api/optimise/combat")
def get_combat_ranking(
    request: Request,
    owner: Optional[str] = None,
    against: Optional[str] = None,
    limit: int = optimise.DEFAULT_LIMIT,
) -> dict:
    """
    Strongest Pals by computed stats, with an optional elemental matchup.

    **`against` does not affect the ordering**, and that is the whole discipline
    of this route. The element chart carries a relation and no multiplier — the
    only element-damage constant in the game's settings object is
    `DamageElementMatchRate = 1.2`, whose meaning is inferred from its name, and
    the popular "2x / half" figures are reproduced by no file this project can
    read. Ranking by a coefficient nobody has would look authoritative and rest
    on nothing, so the matchup is attached per row as a qualitative flag and the
    sort key never sees it.
    """
    authz.require(request, roles_module.VIEW_SELF)
    pals = viewcache.derived("pals:enriched", _enriched_pals)
    effective = _breeding_owner(request, owner)
    scoped = _scope_pals(pals, effective, _guilds_of(effective) if effective else None)

    target = [e.strip() for e in (against or "").split(",") if e.strip()]
    limit = max(0, min(int(limit), 200))

    return {
        # `ranking`, not `pals`: `_breeding_scope` already contributes a `pals`
        # key holding the *count* the answer was built from, and spreading it
        # last silently replaced this list with an integer. Two dicts merged
        # with `**` share one namespace, and the collision showed up as
        # "'int' object is not iterable" three layers away.
        "ranking": optimise.rank_for_combat(scoped, limit=limit, against=target or None),
        "against": target,
        "counters": optimise.counters(scoped, target) if target else None,
        # The caller is the one about to render a damage figure, so it is told
        # here rather than only in a docstring that there is none to render.
        "hasMultiplier": False,
        "elements": list(elements.ELEMENTS),
        "chartIsCurrent": elements.chart_is_current(),
        "unknownElements": list(elements.unknown_to_chart()),
        **_breeding_scope(request, effective),
    }


def _own_guild_base_ids(request: Request) -> Optional[set[str]]:
    """
    The caller's own guilds' base ids, or `None` when they may see everyone's.

    `None` and an empty set are deliberately different, as in `_foreign_guild_ids`:
    empty means "you are in no guild, so none of these are yours", which is the
    opposite outcome from "no restriction".
    """
    if roles_module.VIEW_DETAIL in authz.effective_capabilities(
        authz.current_user(request)
    ):
        return None
    _, own_guilds = _own_identity(request)
    return {
        str(base.get("id") or "")
        for base in savecache.get_section("bases")
        if str(base.get("guildId") or "") in own_guilds
    }


@app.get("/api/bases/storage")
def get_base_storage(request: Request) -> list[dict]:
    """
    Per-base storage: containers owned, slots used, and what is in them.

    Computed during the parse (see parse_worker) rather than per request — the
    join is over every placed object in the world and has no business running on
    the request path.

    **`VIEW_SELF` is enough for your own guild's bases.** This was `VIEW_DETAIL`
    for everything, which left a Player able to see their guild's *total* Wood on
    the Items tab and not which of their own chests it was in — the same data,
    withheld in the more useful shape. Your own base's contents are something you
    can walk up to in game.

    **`baseVisibility` does not widen this.** That setting is about *locations on
    a map*; an inventory is a much larger disclosure than a map pin, so opening
    the map up does not hand out other guilds' chest contents. Above the
    threshold `_hidden_base_ids` still applies on top, so anything hidden from
    the base list is hidden here too.
    """
    # Gated here as well as in the proxy allowlist. `_viewer()` below resolves
    # an identity and returns "guest" when there is none, which filters but does
    # not refuse — see AGENTS.md: the proxy forwards a credential, it does not
    # assert one, so a route that only filters is trusting exactly what the
    # security model says not to trust.
    authz.require(request, roles_module.VIEW_SELF)
    summaries = baseprivacy.filter_storage(
        savecache.get_section("baseStorage"), _hidden_base_ids(request)
    )
    own = _own_guild_base_ids(request)
    if own is None:
        return summaries
    return [s for s in summaries if str(s.get("baseId") or "") in own]


@app.get("/api/bases/{base_id}/storage")
def get_one_base_storage(base_id: str, request: Request) -> dict:
    # Gated here as well as in the proxy allowlist. `_viewer()` below resolves
    # an identity and returns "guest" when there is none, which filters but does
    # not refuse — see AGENTS.md: the proxy forwards a credential, it does not
    # assert one, so a route that only filters is trusting exactly what the
    # security model says not to trust.
    authz.require(request, roles_module.VIEW_SELF)
    own = _own_guild_base_ids(request)
    if base_id in _hidden_base_ids(request) or (own is not None and base_id not in own):
        # 404 rather than 403: "you may not see this base" confirms the base
        # exists, which is the one thing a hidden base is not supposed to say.
        raise HTTPException(404, f"No base {base_id}, or the world has not been parsed yet")
    for summary in savecache.get_section("baseStorage"):
        if summary["baseId"] == base_id:
            return summary

    raise HTTPException(404, f"No base {base_id}, or the world has not been parsed yet")


def _scoped_base_storage(request: Request) -> tuple[list[dict], dict, dict]:
    """
    The base summaries this caller may see, plus the container tables they index.

    **One copy of the scoping, because there are now two endpoints reporting
    container contents.** `/api/bases/supply` and `/api/bases/craftable` answer
    different questions about the same items, and a filter applied to one of two
    endpoints serving the same data is not a filter — the standing example is
    `/api/world/fasttravel` returning all 174 points beside a sibling that
    carefully dropped the undiscovered ones.
    """
    summaries = baseprivacy.filter_storage(
        savecache.get_section("baseStorage"), _hidden_base_ids(request)
    )
    own = _own_guild_base_ids(request)
    if own is not None:
        summaries = [s for s in summaries if str(s.get("baseId") or "") in own]

    # NOT `get_section`: it returns `[]` for anything that is not a list, and
    # both of these are dicts. That is the trap `_export_sections` documents —
    # the report would come back looking fine with every container empty.
    data = savecache.get_data() or {}
    containers = data.get("containers") if isinstance(data.get("containers"), dict) else {}
    guild_storage = (
        data.get("guildStorage") if isinstance(data.get("guildStorage"), dict) else {}
    )
    return summaries, containers, guild_storage


@app.get("/api/bases/craftable")
def get_craftable(
    request: Request,
    guild: Optional[str] = None,
    limit: int = 200,
) -> dict:
    """
    What the materials in this guild's bases and chest could make.

    The census half of the item-source feature, and therefore privacy-scoped —
    unlike `/api/world/items/{id}`, which describes the game rather than this
    world and needs no parsed save.

    **Each recipe is costed against the whole pile independently.** Crafting one
    thing consumes what another needs, so these counts are not simultaneously
    achievable; `simultaneous: false` travels in the payload rather than only in
    a docstring, because a list of numbers that cannot all be true at once reads
    as a plan unless something says otherwise.

    Stock is base storage **plus** the guild chest, which is right here and wrong
    in `/api/bases/supply`: a chest is one shared box, so adding it to each base
    would invent stock, while adding it once to a guild's total is exactly what a
    player can actually reach.
    """
    authz.require(request, roles_module.VIEW_SELF)

    summaries, containers, guild_storage = _scoped_base_storage(request)
    wanted = str(guild or "").strip()

    stock: dict[str, int] = {}
    bases_counted = 0
    for summary in summaries:
        if wanted and str(summary.get("guildId") or "") != wanted:
            continue
        bases_counted += 1
        for row in summary.get("items") or []:
            item_id = str(row.get("itemId") or "")
            if item_id:
                stock[item_id] = stock.get(item_id, 0) + int(row.get("count") or 0)

    # Guild chests, for the guilds whose bases are already visible — the same
    # rule `/api/bases/supply` uses, so the two halves are scoped once.
    visible_guilds = {
        str(s.get("guildId") or "") for s in summaries if s.get("guildId")
    }
    if wanted:
        visible_guilds &= {wanted}
    chests = 0
    for guild_entry in savecache.get_section("guilds"):
        guild_id = str(guild_entry.get("id") or "")
        if guild_id not in visible_guilds or guild_id not in guild_storage:
            continue
        chests += 1
        # `basesupply.container_totals`, not a second slot reader: the count
        # lives in `stackCount` and a reimplementation that reached for `count`
        # would total zero on every slot and report full chests as empty.
        totals = basesupply.container_totals(
            {"slots": containers.get(guild_storage[guild_id], [])}
        )
        for item_id, count in totals.items():
            stock[item_id] = stock.get(item_id, 0) + count

    recipes = itemsource.craftable_from(stock, limit=max(1, min(int(limit), 500)))
    return {
        "recipes": recipes,
        "basesCounted": bases_counted,
        "guildChestsCounted": chests,
        "distinctMaterials": len(stock),
        # See the docstring: these are alternatives, not a shopping list.
        "simultaneous": False,
        # Which bench crafts a recipe has no source in any game file, so nothing
        # here says where to stand. `WorkableAttribute` is 0 on all 1,414 rows.
        "workstationKnown": False,
    }


@app.get("/api/bases/supply")
def get_base_supply(
    request: Request,
    materials: Optional[str] = None,
    floor: int = basesupply.DEFAULT_FLOOR,
) -> dict:
    """
    What each base holds, what is conspicuously missing, and the guild chest.

    **Scoped through exactly the same two filters as `/api/bases/storage`**, and
    that is not incidental. A supply report names container contents per base, so
    a filter applied to one of two endpoints serving the same data is not a
    filter — `/api/world/fasttravel` is the standing example, and
    `/api/inventory/{id}` is the worse one, because it looked up a container *by
    id* and thereby went around every base-privacy check built on top of it.
    Nothing here queries a container by id from the request; it reads the same
    already-scoped summaries.

    The guild chest is a **guild-level** container (see `basesupply`), so it is
    returned for the caller's own guilds, or for all of them above `VIEW_DETAIL`.
    Folding it into the per-base numbers would count one shared box once per
    base and report stock that does not exist.

    **Facts, not mechanics.** See the module docstring: the game files confirm
    these are distinct structures and say nothing about what they consume, so
    this reports what is where and never what to move.
    """
    authz.require(request, roles_module.VIEW_SELF)

    staples = basesupply.parse_materials(materials)
    floor = max(0, int(floor))

    summaries, containers, guild_storage = _scoped_base_storage(request)
    bases = {str(b.get("id") or ""): b for b in savecache.get_section("bases")}

    # Hunger per base, counted off the same Pal records `/api/welfare` reads.
    # `hungerType` is the game's own field: absent means fed.
    hungry: dict[str, int] = {}
    for pal in savecache.get_section("pals"):
        if pal.get("hungerType") and pal.get("baseId"):
            base_id = str(pal["baseId"])
            hungry[base_id] = hungry.get(base_id, 0) + 1

    reports = [
        basesupply.base_report(
            summary,
            containers,
            staples=staples,
            floor=floor,
            hungry=hungry.get(str(summary.get("baseId") or ""), 0),
            pal_count=int(
                (bases.get(str(summary.get("baseId") or "")) or {}).get("palCount") or 0
            ),
        )
        for summary in summaries
    ]

    # Guild chests, for the guilds whose bases this caller can already see. That
    # keeps the two halves of the answer scoped by one rule rather than two.
    visible_guilds = {str(r.get("guildId") or "") for r in reports if r.get("guildId")}
    chests = [
        basesupply.guild_report(guild, guild_storage[str(guild.get("id") or "")],
                                containers, staples=staples)
        for guild in savecache.get_section("guilds")
        if str(guild.get("id") or "") in guild_storage
        and str(guild.get("id") or "") in visible_guilds
    ]

    return {
        "bases": reports,
        "guildChests": chests,
        "materials": list(staples),
        "floor": floor,
        # Said outright so the UI never presents the floor as a game rule. The
        # game's own stack ceiling for these is 9999; the floor is the operator's.
        "floorIsOperatorSetting": True,
        "cakeItems": basesupply.cake_ids(),
    }


@app.get("/api/bases/assign")
def get_base_assignment(
    request: Request,
    base: Optional[str] = None,
    owner: Optional[str] = None,
    candidates: int = baseassign.DEFAULT_CANDIDATES,
) -> dict:
    """
    What work each base needs, who covers it, and who could fill the gaps.

    **Two independent scopes, and they are not interchangeable.** Bases go
    through the base-privacy filter (`_hidden_base_ids` plus own-guild
    narrowing), exactly as `/api/bases/supply` and `/api/bases/storage` do —
    a filter applied to one of several endpoints serving the same data is not a
    filter. Pals go through `_scope_pals`, exactly as `/api/optimise/work` does.
    A caller therefore sees suggestions drawn only from Pals they can actually
    move, for bases they are allowed to see.

    **Advisory only.** Nothing here writes, and `advisoryOnly` says so in the
    payload rather than only in a docstring — the client is the thing about to
    render a suggestion next to a button.
    """
    authz.require(request, roles_module.VIEW_SELF)

    if not baseassign.data_available():
        raise HTTPException(
            503,
            "Work assignment data is unavailable — backend/data/work_assign.json.gz "
            "did not load. Regenerate with scripts/extract-work-assign.py.",
        )

    hidden = _hidden_base_ids(request)
    own = _own_guild_base_ids(request)
    bases = [
        b for b in savecache.get_section("bases")
        if str(b.get("id") or "") not in hidden
        and (own is None or str(b.get("id") or "") in own)
    ]
    if base:
        bases = [b for b in bases if str(b.get("id") or "") == base]
        if not bases:
            raise HTTPException(404, "No such base, or it is not visible to you")

    # Names for every base the caller can see, so "committed at Base Camp 3"
    # names somewhere rather than saying "another base". Built from the same
    # filtered list, so a hidden base never leaks its name through a candidate.
    base_names = {str(b.get("id") or ""): str(b.get("name") or "") for b in bases}

    structures_by_base: dict[str, list[dict]] = {}
    for obj in savecache.get_section("mapObjects"):
        base_id = str(obj.get("baseCampId") or "")
        if base_id in base_names:
            structures_by_base.setdefault(base_id, []).append(
                {"kind": obj.get("objectId") or ""}
            )

    effective = _breeding_owner(request, owner)
    pals = _scope_pals(
        viewcache.derived("pals:enriched", _enriched_pals),
        effective,
        _guilds_of(effective) if effective else None,
    )

    candidates = max(0, min(int(candidates), 25))
    reports = [
        baseassign.base_report(
            b,
            structures_by_base.get(str(b.get("id") or ""), []),
            pals,
            base_names,
            candidates=candidates,
        )
        for b in bases
    ]

    return {
        "bases": reports,
        **_breeding_scope(request, effective),
    }


def _named_map_objects() -> list[dict]:
    """~10 ms across 3,370 placed objects, and identical until the next parse."""
    return [
        {**o, "name": gamedata.structure_name(o.get("objectId") or "")}
        for o in savecache.get_section("mapObjects")
    ]


@app.get("/api/mapobjects")
def get_map_objects(request: Request, category: Optional[str] = None) -> list[dict]:
    """
    Placed world objects with coordinates: chests, palboxes, farms, benches.

    Filtered for base privacy, and that filter is not optional: these objects
    carry the coordinates of the base they sit in, so returning them for a base
    whose marker was dropped from `/api/bases` would hide the label and publish
    the location.
    """
    # Gated here as well as in the proxy allowlist. `_viewer()` below resolves
    # an identity and returns "guest" when there is none, which filters but does
    # not refuse — see AGENTS.md: the proxy forwards a credential, it does not
    # assert one, so a route that only filters is trusting exactly what the
    # security model says not to trust.
    authz.require(request, roles_module.VIEW_BASIC)
    objects = viewcache.derived("mapObjects:named", _named_map_objects)
    if category:
        objects = [o for o in objects if o.get("category") == category]
    return baseprivacy.filter_objects(objects, _hidden_base_ids(request))


# ─── Static world data (bundled, not from the save) ──────


@app.get("/api/world/fasttravel")
def get_fast_travel_points(request: Request) -> dict[str, Any]:
    """
    Fast-travel points, with world coordinates and in-game names.

    These are static level actors, so they appear nowhere in a save file — only
    a player's *unlocked* list does. The coordinates share the save's world
    space, so they drop straight onto the existing map transform.

    **`discoveryVisibility` applies here, and it did not.** `/api/world/discoveries`
    carefully drops undiscovered locations server-side for anyone below the
    threshold — and this route sat next to it returning all 174 unconditionally.
    An operator who set the policy to hide unexplored map got exactly nothing
    from it, because the map's own fallback path reads this endpoint. Filtering
    in one of two endpoints that serve the same data is not filtering.
    """
    authz.require(request, roles_module.VIEW_BASIC)
    try:
        points = gamedata.fast_travel_points()
    except gamedata.GameDataUnavailable as e:
        raise HTTPException(503, str(e))

    if _may_see_undiscovered(request):
        return {"points": points, "filtered": False}

    found = _own_discovered_keys(request, "fastTravel")
    return {
        "points": [p for p in points if str(p.get("key", "")).upper() in found],
        "filtered": True,
    }


@app.get("/api/world/guildmarkers")
def get_guild_markers(request: Request) -> dict[str, Any]:
    """
    The pins a guild has dropped on its own map.

    **THE GAME SCOPES THESE AND SO DOES THIS.** `MAP_MARKER_GUILD_INFO` is
    literally "Shared with Guild Members", so returning every guild's pins to
    everybody would publish something the game deliberately keeps inside a guild
    — and on a PvP server "where has that guild pinned things" is exactly the
    kind of thing worth not publishing.

    So the rule is the narrow one, and it is the *opposite default* from bases:
    a base is visible until its owner hides it, a marker is hidden unless you
    share the guild. Staff see everything, as everywhere else, so moderation
    still works without an exemption list.

    An account with no linked character sees **nothing rather than everything**.
    That is the fail-safe direction: `steam_uid` is unset on a fresh account, and
    the alternative reading — "no uid, so no guild, so no filter" — is how a
    filter turns into a leak.
    """
    authz.require(request, roles_module.VIEW_BASIC)
    role, username = _viewer(request)

    guilds = savecache.get_section("guilds") or []
    # "Staff" is not a new list — it is the rank rule `privacy` already applies
    # everywhere: somebody a hiding Player cannot conceal themselves from. Using
    # `conceals` rather than naming roles here means a new role added to
    # `roles.py` lands on the right side of this automatically.
    staff = not privacy.conceals(role, "player", "player")

    own: set[str] = set()
    if username:
        user = accounts.get_user(username)
        uid = privacy.normalise_uid((user or {}).get("steamUid"))
        if uid:
            for guild in guilds:
                members = {
                    privacy.normalise_uid(m.get("uid"))
                    for m in (guild.get("members") or [])
                }
                if uid in members:
                    own.add(str(guild.get("id") or ""))

    points: list[dict[str, Any]] = []
    for guild in guilds:
        gid = str(guild.get("id") or "")
        if not staff and gid not in own:
            continue
        for marker in guild.get("markers") or []:
            points.append({**marker, "guildId": gid, "guildName": guild.get("name")})

    return {
        "points": points,
        # Why the list is the length it is. Zero markers and "you are in no
        # guild" are different answers, and a layer that is empty for the second
        # reason reads as broken — the same distinction the effigy fallback and
        # the ban list both had to make.
        "scope": "all" if staff else ("guild" if own else "none"),
        "guildsVisible": len(own) if not staff else len(guilds),
        "linkedToPlayer": bool(own) or staff,
    }


@app.get("/api/world/effigies")
def get_effigy_points(request: Request) -> dict[str, Any]:
    """
    Effigies, with world coordinates and their instance GUIDs.

    The plain-list counterpart to `/api/world/fasttravel`, and it exists for the
    same reason: **the map's effigy layer had no fallback.**
    `/api/world/discoveries` serves both categories at once, requires a real
    account (`require_user`) and 503s if either bundle is missing — so a guest, or
    a moment when the effigy bundle failed to load, took the whole response down.
    Fast travel survived that because the map falls back to its own endpoint;
    effigies simply vanished, with no error anywhere, which is exactly what
    "effigies not showing for some users" looks like from the outside.

    `discoveryVisibility` is applied **here**, not by the caller — the lesson from
    `/api/world/fasttravel`, which for months returned all 174 points beside a
    sibling that carefully filtered them. A filter applied to one of two endpoints
    serving the same data is not a filter.
    """
    authz.require(request, roles_module.VIEW_BASIC)
    points = gamedata.effigies()

    if _may_see_undiscovered(request, "effigies"):
        return {"points": points, "filtered": False, "total": len(points)}

    found = _own_discovered_keys(request, "effigies")
    return {
        "points": [p for p in points if str(p.get("guid", "")).upper() in found],
        "filtered": True,
        "total": len(points),
    }


def _raid_reward(entry: dict[str, Any]) -> dict[str, Any]:
    described = gamedata.describe_item(str(entry.get("itemId") or ""))
    return {
        "itemId": entry.get("itemId"),
        "name": described["name"],
        "icon": described["icon"],
        # A real per-item chance, unlike a lottery weight: these are independent
        # rolls on one success rather than shares of a slot.
        "rate": entry.get("rate"),
        "min": entry.get("min"),
        "max": entry.get("max"),
    }


@app.get("/api/world/invaders")
def get_invaders(request: Request) -> dict[str, Any]:
    """
    Base raids: which attacker groups exist, per biome, and what they drop.

    **A REFERENCE TABLE, NOT A PER-BASE FORECAST, and the difference is not
    caution — it is two joins that cannot be made.**

    1. `InvadeGradeMin`/`Max` bound a raid to a "grade", and nothing establishes
       what a grade is in save terms. Base level is the obvious candidate and is
       **not in the save at all** — `BaseCampSaveData` carries id, name, state,
       transform, area range, group and the owning palbox, and the palbox carries
       no level either. Guild level and player level are equally plausible and
       equally unevidenced.
    2. `BiomeID` would let a base be matched to the groups that can reach it, and
       biome is **placed geometry rather than a lookup**: `DT_WorldMapAreaData`
       carries only a `MsgID`, and the assignment lives in `BP_PalBiomeTriggerBox`
       volumes in the world cells. Matching a base to one means containment tests
       against rotated boxes, which is a real piece of work with its own
       verification story and has not been done.

    So this endpoint says what the game contains and never "your base at
    Windswept Island will be raided by X". `gradeMeaningKnown` and
    `perBaseForecast` both travel false, because a client that assumed either
    would render a confident claim nothing here supports.
    """
    authz.require(request, roles_module.VIEW_BASIC)

    data = gamedata.invaders() or {}
    rewards = data.get("rewards") or {}

    groups = []
    for name, entries in sorted((data.get("groups") or {}).items()):
        biomes = sorted({str(e.get("biome") or "") for e in entries if e.get("biome")})
        grades = [
            (int(e.get("gradeMin") or 0), int(e.get("gradeMax") or 0)) for e in entries
        ]
        reward_rows = [
            {
                **gamedata.describe_item(str(r.get("itemId") or "")),
                "itemId": r.get("itemId"),
                "rate": r.get("rate"),
                "min": r.get("min"),
                "max": r.get("max"),
            }
            for r in (rewards.get(name) or [])
        ]
        groups.append({
            "group": name,
            "biomes": biomes,
            "gradeMin": min((g[0] for g in grades), default=0),
            "gradeMax": max((g[1] for g in grades), default=0),
            "attackers": len(entries),
            # A raid triggered by something you built, where the game names one.
            # Carried unresolved: it is a build-object id and nothing here has
            # confirmed what the condition means.
            "conditions": sorted({
                str(e.get("conditionBuildObjectId") or "") for e in entries
                if str(e.get("conditionBuildObjectId") or "") not in ("", "None")
            }),
            "rewards": reward_rows,
        })

    return {
        "groups": groups,
        "total": len(groups),
        "visitors": data.get("visitors") or {},
        # What calling off a raid costs. A flat list — the game does not say
        # which cost applies to which raid.
        "cancelCosts": data.get("cancelCosts") or [],
        "gradeMeaningKnown": bool(data.get("gradeMeaningKnown")),
        "perBaseForecast": False,
        "note": (
            "These are the raid groups the game contains. Which of them can reach "
            "a particular base is not derivable: the grade a raid is bounded by "
            "has no established meaning in save terms, and a base's biome is "
            "defined by trigger volumes in the world rather than by any table."
        ),
    }


@app.get("/api/world/raidbosses")
def get_raid_bosses(request: Request) -> dict[str, Any]:
    """
    The altar-summoned bosses: what summons each, at what level, and what it drops.

    **A separate category from field bosses, and deliberately NOT on the map.**
    `DT_BossSpawnerLoactionData` carries zero `RAID_` ids, which is correct rather
    than a gap — a raid boss is summoned at an altar, so a table of *locations*
    has nothing to say about it. Giving one a map marker would be the
    `BP_LevelObject_TowerLockBarrier` mistake: a plausible-looking answer to a
    question the data does not address.

    **The row key is the summon item.** `PalSummon_NightLady` is a real
    catalogue id — "Bellanoir's Slab" — so "what do I need to start this raid"
    is a lookup rather than an inference, and it is checked rather than assumed:
    `summonItemKnown` is false if the id does not resolve.
    """
    authz.require(request, roles_module.VIEW_BASIC)

    bosses = []
    for summon_id, entry in (gamedata.raid_bosses() or {}).items():
        item = gamedata.item(summon_id)
        described = gamedata.describe_item(summon_id) if item else {}
        forms = []
        for form in entry.get("forms") or []:
            species = str(form.get("speciesId") or "")
            known = gamedata.character(species) is not None
            forms.append({
                "speciesId": species,
                # The `_2` difficulty variants have no character-table entry, so
                # the humanised id reads "Night Lady Dark 2". The summon item is
                # named properly ("Bellanoir Libero (Ultra) Slab"), so the UI is
                # told which name it can trust rather than being handed one bad
                # one silently.
                "name": gamedata.character_name(species),
                "nameIsInternal": not known,
                "level": form.get("level"),
                "canModeChange": form.get("canModeChange"),
            })
        bosses.append({
            "summonItemId": summon_id,
            "summonItemName": described.get("name") or gamedata.humanize(summon_id),
            "summonItemIcon": described.get("icon"),
            "summonItemKnown": item is not None,
            "forms": forms,
            "rewards": [_raid_reward(r) for r in entry.get("rewards") or []],
            # The game's own distinction: `SuccessAnyOneItemList` is one of
            # these, not all of them. Folding the two lists together would
            # overstate what a clear gives you.
            "rewardsAnyOne": [_raid_reward(r) for r in entry.get("rewardsAnyOne") or []],
            # `EggPalIDAndWeight` is a MapProperty the table reader does not
            # decode. Said out loud so an empty egg list reads as "not read"
            # rather than as "this raid drops no eggs".
            "eggWeightsRead": bool(entry.get("eggWeightsRead")),
        })

    bosses.sort(key=lambda b: min((f["level"] or 0) for f in b["forms"]) if b["forms"] else 0)
    return {
        "bosses": bosses,
        "total": len(bosses),
        # Stated rather than left for the client to infer from an empty list.
        "hasPositions": False,
        "positionNote": (
            "Raid bosses are summoned at an altar, so no game file gives them a "
            "world position. They are not on the map for that reason."
        ),
    }


@app.get("/api/world/npcs")
def get_npc_placements(
    request: Request, role: Optional[str] = None
) -> dict[str, Any]:
    """
    Placed NPCs by role — merchants, villagers, hunters, police, quest givers.

    The map has drawn these 220-odd spawn points as one anonymous "NPCs & camps"
    layer since it shipped, because a placed actor's properties were believed
    undecodable. They are, in the *client* pak; the server pak's world cells
    carry tagged properties, so a spawner now says which NPC it is and at what
    level. See `scripts/extract-npcs.py`.

    **Not `discoveryVisibility`-filtered**, for the same reason as
    `/api/world/bosses`: an NPC spawn is not a collectable, so the save holds no
    per-player record to filter against and inventing a discovery state would be
    worse than showing them.

    `VIEW_BASIC` — this is what the game contains, not what anyone here has done.
    """
    authz.require(request, roles_module.VIEW_BASIC)
    placements = viewcache.per_files(
        f"npcs:{role or 'all'}",
        [gamedata.NPCS_PATH, gamedata.DATA_PATH],
        lambda: gamedata.npc_placements(role),
    )
    return {
        "placements": placements,
        "total": len(placements),
        # The layer switches, so the UI does not hardcode a taxonomy that the
        # extractor owns.
        "roles": gamedata.npc_roles(),
        # THE ROLE SPLIT IS A NAME RULE, not a column the game ships — there is
        # no role column anywhere. Said in the payload for the same reason
        # `hasMultiplier` is: the client is the thing about to draw a legend.
        "roleFromName": True,
    }


@app.get("/api/world/bosses")
def get_boss_spawners(request: Request) -> dict[str, Any]:
    """
    The 90 placed field bosses, with species, **level** and world position.

    Levels were reported as unavailable throughout this project until
    `DT_BossSpawnerLoactionData` stopped being refused by the table reader. They
    were never missing.

    NOT FILTERED BY `discoveryVisibility`, and that is a deliberate difference
    from effigies and fast travel. Those two are *collectables* — the save
    records whether you personally found each one, so hiding the rest is a
    meaningful setting. A field boss respawns and is not collected: the save has
    no per-player record to filter against, so there is nothing to hide and
    pretending otherwise would mean inventing a discovery state.

    `VIEW_BASIC`, like the other world-reference layers: this is what the game
    contains, not what anyone on this server has done.
    """
    authz.require(request, roles_module.VIEW_BASIC)
    bosses = [gamedata.describe_boss(b) for b in gamedata.boss_spawners()]
    levels = [b["level"] for b in bosses if b.get("level")]
    return {
        "bosses": bosses,
        "total": len(bosses),
        # Stated so a UI can scale a legend without re-deriving it, and so an
        # empty layer is distinguishable from a bundle that failed to load.
        "levelRange": [min(levels), max(levels)] if levels else None,
    }


def _may_see_undiscovered(request: Request, category: str = "fastTravel") -> bool:
    role, _ = _viewer(request)
    return policy_module.may_see_undiscovered_category(role, category)


def _own_discovered_keys(request: Request, category: str) -> set[str]:
    """
    Upper-cased progress keys for the caller's own character, or every player's
    when they may see everyone.

    Empty for an account with no linked character — which reads as "you have
    discovered nothing", the same honest answer `/api/world/discoveries` gives.
    """
    user = authz.current_user(request)
    can_see_others = roles_module.VIEW_DETAIL in authz.effective_capabilities(user)
    own = authz.linked_uid(user)

    keys: set[str] = set()
    for player in get_players():
        if not can_see_others and privacy.normalise_uid(player.get("uid")) != own:
            continue
        progress = player.get("progress") or {}
        keys.update(
            str(k).upper() for k in ((progress.get(category) or {}).get("keys") or [])
        )
    return keys


@app.get("/api/world/build")
def get_game_build(request: Request) -> dict[str, Any]:
    """
    Whether the bundled game data still matches the installed Palworld build.

    A read at VIEW_BASIC because it qualifies the map everyone is looking at: if
    the ore positions are a patch out of date, the person reading them is the one
    who needs to know.
    """
    authz.require(request, roles_module.VIEW_BASIC)
    return gameversion.status()


class BuildAcknowledge(BaseModel):
    buildId: str


@app.post("/api/world/build/acknowledge")
def acknowledge_game_build(req: BuildAcknowledge, request: Request) -> dict[str, Any]:
    """
    Dismiss the stale-data banner for one build.

    Scoped to that build rather than a global "hide this": someone who verified
    their data against build A has said nothing about B, so the next update
    raises it again. POLICY_MANAGE because it is a server-wide statement, not a
    per-user preference.
    """
    user = authz.require_user(request, roles_module.POLICY_MANAGE)
    current = gameversion.status()
    if not req.buildId or req.buildId != current["buildId"]:
        raise HTTPException(
            400,
            "That is not the build currently installed. Reload and try again — "
            "acknowledging a build that is no longer there would silence a "
            "warning about the one that is.",
        )
    gameversion.acknowledge(req.buildId)
    audit.record(
        audit.POLICY_UPDATE, username=user["username"], role=user["role"],
        target=f"game_build:{req.buildId}",
        detail={"acknowledged": req.buildId, "verdict": current["verdict"]},
        ip=authz.client_ip(request),
    )
    return gameversion.status()


@app.post("/api/world/packs/reload")
def reload_world_packs(request: Request) -> dict[str, Any]:
    """
    Re-read the bundled data files from disk without restarting.

    Regenerating after a game update means replacing `worldobjects.json.gz`,
    `effigies.json.gz` or `gamedata.json.gz` on disk. Before this, the only way
    to pick that up was restarting the container — a heavier action than the one
    that made it necessary, and on a shared box it briefly takes the dashboard
    away from everyone else.

    **This reloads; it does not regenerate.** Extraction walks ~9,900 streaming
    cell packages and needs the game pak mounted, which is exactly the kind of
    work this project keeps off the machine running the game. It also could not
    persist: `backend/data/` lives in the image layer, so anything written there
    would silently revert on the next rebuild and the operator would have no way
    to tell. Regenerating stays a deliberate act on the host.

    POLICY_MANAGE rather than a view capability: it changes what every session
    sees, so it is a server-wide statement.
    """
    user = authz.require_user(request, roles_module.POLICY_MANAGE)
    result = {
        "worldObjects": worldobjects.reload(),
        # Bundled like the rest, so it goes stale like the rest — a regenerated
        # `settings_help.json.gz` that needed a container restart to take effect
        # would be the one bundle this button did not cover.
        "settingsHelp": settingshelp.reload(),
        **gamedata.reload(),
    }
    # `breeding` caches *derivations* of these bundles, not copies of them, so
    # dropping gamedata's caches alone leaves it folding the old `moves.json.gz`
    # into a child-keyed map while `gamedata.unique_combos()` returns the new
    # one. `viewcache` would then rebuild the limits view from stale input —
    # worse than not rebuilding, because the rebuild makes it look current.
    #
    # Called from here rather than inside `gamedata.reload()` because `breeding`
    # imports `gamedata`; the dependency only runs one way.
    breeding.reset_caches()
    audit.record(
        audit.DATA_RELOAD, username=user["username"], role=user["role"],
        target="world_packs",
        detail={
            "worldObjects": result["worldObjects"]["total"],
            "effigies": result["effigies"]["count"],
            "items": result["gamedata"]["items"],
        },
        ip=authz.client_ip(request),
    )
    # The build check compares bundled data against the installed game, so its
    # verdict is stale the moment the bundles change.
    return {**result, "build": gameversion.status()}


@app.get("/api/world/mods")
def get_installed_mods(request: Request) -> dict[str, Any]:
    """
    Mods installed on the game server, if the install directory is visible.

    Behind VIEW_DETAIL rather than VIEW_BASIC: the mod list is a fair description of
    the server's configuration, and it is also the answer to "why does the Pal
    checker not recognise these species", which is a detail-tab question.
    """
    authz.require(request, roles_module.VIEW_DETAIL)
    return mods.detect()


@app.get("/api/world/objects")
def get_world_objects(
    request: Request,
    category: str = Query(""),
    minX: Optional[float] = Query(None),
    minY: Optional[float] = Query(None),
    maxX: Optional[float] = Query(None),
    maxY: Optional[float] = Query(None),
    kinds: str = Query(""),
    limit: int = Query(worldobjects.MAX_POINTS),
) -> dict[str, Any]:
    """
    Static world objects inside a viewport: ore, chests, fishing spots, oil fields.

    Bundled pak data, identical for every viewer, so there is nothing here to
    filter for privacy — unlike `/api/mapobjects`, which is player content.

    The bounding box is not optional in practice: 51,921 markers is not a number
    to hand a browser. A response past the cap reports `truncated` and `inView` so
    the UI can say what it is not showing rather than presenting a slice as the set.
    """
    authz.require(request, roles_module.VIEW_BASIC)
    role = _viewer(request)[0]
    allowed = _visible_world_categories(role)

    if category and category not in allowed:
        # Empty rather than 403: the caller asked about something this server has
        # decided not to expose, and a refusal would confirm it exists and is
        # populated. The category is absent from the legend for the same reason.
        return {"points": [], "inView": 0, "returned": 0, "truncated": False,
                "limit": limit, "restricted": True}

    # `allowed` goes into the query rather than filtering its result: no category
    # named means "everything", which must still mean everything *this viewer* may
    # see, and a count taken before that filter would promise points that zooming
    # in never reveals.
    result = worldobjects.query(
        category=category,
        min_x=minX, min_y=minY, max_x=maxX, max_y=maxY,
        kinds=[k for k in kinds.split(",") if k],
        allowed=allowed,
        limit=limit,
    )

    # Field bosses carry the species they spawn; resolve it to what a player
    # reads. Done here rather than baked into the bundle so refreshing the game
    # data updates the names without a re-extraction — the same reason
    # `/api/items` resolves item names at request time.
    #
    # `character()`, not `pal()`: these are `BOSS_` variants, and the bundled
    # tables spell them inconsistently enough that an exact lookup drops some.
    for point in result["points"]:
        species = point.get("species")
        if species:
            point["speciesName"] = gamedata.character_name(species)
            point["icon"] = (gamedata.describe_pal(species) or {}).get("icon")
            # And the level, where the boss table has a row standing in the same
            # place. Joined here rather than baked into the bundle for the same
            # reason the name is: the two bundles are regenerated independently,
            # and a join frozen into one of them goes stale when the other moves.
            level = gamedata.boss_level_at(
                species, float(point.get("x") or 0.0), float(point.get("y") or 0.0)
            )
            if level:
                point["level"] = level["level"]
                point["levelSpawner"] = level["spawnerId"]
    return result


@app.get("/api/world/objects/categories")
def get_world_object_categories(request: Request) -> dict[str, Any]:
    """
    The layer's legend: what exists, how much of it, and the class breakdown.

    Categories the viewer may not see are **omitted, not flagged**. A name and a
    count in a legend has already told a player what is out there and roughly how
    much of it, which is most of what restricting it was for.
    """
    authz.require(request, roles_module.VIEW_BASIC)
    allowed = _visible_world_categories(_viewer(request)[0])
    categories = [c for c in worldobjects.categories() if c["id"] in allowed]
    totals = worldobjects.totals()
    # Spelled out rather than spread over `totals()`: that dict has its own
    # `categories` key holding a *count*, which silently replaced this list the
    # first time round.
    return {
        "categories": categories,
        # Recomputed over the visible subset: the honest total for this viewer, not
        # the world's, so the legend cannot say "of 51,921" while listing
        # categories that add up to less.
        "objects": sum(c["count"] for c in categories),
        "categoryCount": len(categories),
        "cellsParsed": totals["cellsParsed"],
        "skipped": totals["skipped"],
        "cellSize": totals["cellSize"],
        "maxPoints": totals["maxPoints"],
        "restrictedCategories": sorted(
            c["id"] for c in worldobjects.categories() if c["id"] not in allowed
        ),
    }


def _visible_world_categories(role: str) -> set[str]:
    """Static object categories this role's policy threshold admits."""
    current = policy_module.load_policy()
    return {
        category["id"]
        for category in worldobjects.categories()
        if policy_module.may_see_world_objects(role, category["id"], current)
    }


@app.get("/api/world/discoveries")
def get_discoveries(request: Request, uid: str = Query("")) -> dict[str, Any]:
    """
    Fast-travel points and effigies, each marked found or not found.

    Both lists come from bundled game data, so the dashboard knows where all 174
    points and all 396 effigies are regardless of what anyone has discovered. The
    save contributes only the *found* half, keyed by the same ids — fast-travel
    by its hex key, effigies by the instance GUID in
    `RelicObtainForInstanceFlag`.

    Whether undiscovered locations are actually sent is the operator's call
    (`discoveryVisibility` — a role threshold, or `everyone`/`nobody`).
    Filtering happens **here, server-side**: a UI that received everything and
    hid some of it would be handing out the answers in the network tab.
    """
    user = authz.require_user(request, roles_module.VIEW_BASIC)
    current = policy_module.load_policy()
    # Per category, because fast travel and effigies are different things: one is
    # navigation infrastructure, the other is a collectathon a full map ruins.
    # `discoveryVisibility` remains the fallback for both.
    levels = policy_module.discovery_category_levels(current)
    may_see = {
        category: policy_module.may_see_undiscovered(user["role"], level)
        for category, level in levels.items()
    }

    # Whose discoveries to fold in. A caller without VIEW_DETAIL may only ask
    # about themselves, so a Player cannot enumerate someone else's progress.
    #
    # `steam_uid` on the account is what links a login to a character. An account
    # without one has no "own" progress to show — it is not an error, it just
    # means every location reads as undiscovered for them.
    players = get_players()
    # `steamUid`, and TWO things were wrong here.
    #
    # The key was `steam_uid` — the *database column*. `accounts._row_to_user`
    # returns the camelCase `steamUid`, so this read `None` for every caller and
    # nobody ever matched. And even with the right key, `accounts` stores the uid
    # dash-stripped while `Level.sav` stores it dashed, so a raw `==` would still
    # have matched nothing.
    #
    # Both failed silently, and the visible symptom was the map: a Player's own
    # discoveries all read as not-found, and because the default
    # `discoveryVisibility` withholds undiscovered locations from Players, every
    # fast-travel point and every effigy was then dropped server-side. The layer
    # came back empty with no error to follow.
    own_uid = authz.linked_uid(user)
    asked = privacy.normalise_uid(uid)
    can_see_others = roles_module.VIEW_DETAIL in roles_module.capabilities_for(user["role"])

    if uid and not can_see_others and asked != own_uid:
        raise HTTPException(403, "You can only view your own discoveries")

    if uid:
        chosen = [p for p in players if privacy.normalise_uid(p.get("uid")) == asked]
        if not chosen:
            raise HTTPException(404, f"No player {uid}")
    else:
        chosen = players if can_see_others else [
            p for p in players if privacy.normalise_uid(p.get("uid")) == own_uid
        ]

    found_travel: set[str] = set()
    found_effigies: set[str] = set()
    for player in chosen:
        progress = player.get("progress") or {}
        found_travel.update(
            str(k).upper() for k in ((progress.get("fastTravel") or {}).get("keys") or [])
        )
        found_effigies.update(
            str(k).upper() for k in ((progress.get("effigies") or {}).get("keys") or [])
        )

    def mark(entries: list[dict], key_field: str, found: set[str],
             show_undiscovered: bool) -> list[dict]:
        out = []
        for entry in entries:
            discovered = str(entry.get(key_field, "")).upper() in found
            if not discovered and not show_undiscovered:
                continue
            out.append({**entry, "discovered": discovered})
        return out

    try:
        travel = mark(gamedata.fast_travel_points(), "key", found_travel,
                      may_see["fastTravel"])
        effigies = mark(gamedata.effigies(), "guid", found_effigies,
                        may_see["effigies"])
    except gamedata.GameDataUnavailable as e:
        raise HTTPException(503, str(e))

    return {
        "scope": uid or ("all" if can_see_others else "self"),
        "linkedToPlayer": bool(own_uid) or can_see_others,
        "discoveryVisibility": current.get(
            "discoveryVisibility", policy_module.DEFAULT_DISCOVERY
        ),
        "discoveryLevels": levels,
        # Kept for callers that predate the split. True only when *both*
        # categories are open, so nothing reads it as a blanket yes when only
        # one half is.
        "showsUndiscovered": all(may_see.values()),
        "showsUndiscoveredByCategory": may_see,
        "fastTravel": {
            "total": len(gamedata.fast_travel_points()),
            "found": len(found_travel),
            "points": travel,
        },
        "effigies": {
            "total": len(gamedata.effigies()),
            "found": len(found_effigies),
            "points": effigies,
        },
    }


@app.get("/api/world/items")
def get_item_catalogue(request: Request) -> dict[str, Any]:
    """
    Every item in the game, by id **and** friendly name.

    Reference data, like `/api/world/paldeck` — it describes what Palworld has,
    not what this world holds, so it is `VIEW_BASIC` and needs no parsed save.

    `/api/items` is the other one and they are easy to confuse: that reports the
    contents of the parsed world and is privacy-filtered per guild. This is the
    catalogue. The slot editor was built on the first, so any legitimate item
    nobody on the server owned rendered as "not in this world" — the editor
    calling valid input wrong while the backend, which had always validated
    against this catalogue, went on to accept it.
    """
    authz.require(request, roles_module.VIEW_BASIC)
    try:
        items = viewcache.per_file(gamedata.DATA_PATH, gamedata.all_items)
    except gamedata.GameDataUnavailable as e:
        raise HTTPException(503, str(e))
    return {"items": items, "total": len(items)}


@app.get("/api/world/passives")
def get_passive_catalogue(request: Request) -> dict[str, Any]:
    """
    The passive-effect category vocabulary, so a UI builds filters from the data.

    Reference data like `/api/world/items` — it describes what Palworld has, so
    `VIEW_BASIC` and no parsed save. `unclassified` travels in it deliberately:
    it is normally empty, and a non-empty list is the signal that a game update
    added an effect type nothing here knows how to file.
    """
    authz.require(request, roles_module.VIEW_BASIC)
    return passiveeffects.catalogue()


@app.get("/api/world/passives/effects")
def get_passive_effects(request: Request, ids: str = "") -> dict[str, Any]:
    """
    Everything a set of passive skills does — all 208 effect types, not the four
    that feed the stat formula.

    Takes the ids rather than a Pal, and that is the cheap part: it is catalogue
    data, so it needs no world and no privacy filter, and a client can ask about
    a hypothetical set of passives as easily as about a Pal it owns.

    **Not folded into `/api/pals`.** That endpoint enriches 1,905 Pals on every
    parse; expanding each one's passives there would pay for a panel almost
    nobody opens, on every Pal, forever.
    """
    authz.require(request, roles_module.VIEW_BASIC)
    wanted = [part for part in (ids or "").split(",") if part.strip()]
    if len(wanted) > 64:
        raise HTTPException(400, "at most 64 passive ids per request")
    return passiveeffects.describe_passives([w.strip() for w in wanted])


@app.get("/api/world/items/{item_id}")
def get_item_sources(item_id: str, request: Request) -> dict[str, Any]:
    """
    Where one item comes from: recipes, drops, chests, merchants, production.

    A sibling of `/api/world/items` and the catalogue half of the same
    distinction — reference data about what Palworld has, needing no parsed
    world, so `VIEW_BASIC`. What *this* world holds is `/api/items`, one letter
    apart and privacy-filtered per guild.

    An unknown id returns `known: false` with a 200 rather than a 404: the
    catalogue is complete at 2,466 items, so a miss means the caller asked about
    something that is not an item, which is a different thing from a route that
    is not there.
    """
    authz.require(request, roles_module.VIEW_BASIC)
    try:
        return itemsource.describe(item_id)
    except gamedata.GameDataUnavailable as e:
        raise HTTPException(503, str(e))


@app.get("/api/world/reference")
def get_reference_data(request: Request) -> dict[str, Any]:
    """Exact Palworld 1.0 totals, computed from the game's own data tables."""
    authz.require(request, roles_module.VIEW_BASIC)
    try:
        return {
            "totals": gamedata.totals(),
            "workSuitability": gamedata.work_suitabilities(),
        }
    except gamedata.GameDataUnavailable as e:
        raise HTTPException(503, str(e))


def _paldeck_entries() -> list[dict[str, Any]]:
    """
    One row per Paldeck entry, with the ids that feed it.

    Two kinds of duplicate have to collapse here or the same Pal is listed
    several times:

    - **`BOSS_`/`PREDATOR_` prefixes** — `describe_pal` already strips them, so
      anything reporting a prefix is a second copy of a species already present.
    - **Location suffixes** — `HadesBird` and `HadesBird_Oilrig`,
      `GrassPanda_Electric` and `..._Tower`. These share a Paldeck number and a
      name but genuinely spawn in different places, so they are merged into one
      entry whose habitat is the *union* of theirs. Dropping either would hide
      half of where the Pal is found.

    Entries the in-game Paldeck does not list are excluded outright — see the
    comment on the negative-index check below.

    **Cached on the bundle files' stamps.** This is entirely static reference
    data, and rebuilding it measured **20 ms** — paid on every listing request
    *and* on every detail request, which calls this only to find an entry's
    sibling ids. Clicking through twenty Pals was half a second of recomputing
    an answer that cannot change. Keying on the files means replacing a bundle
    (or pressing "Reload data packs") invalidates it with no explicit call to
    forget.
    """
    return viewcache.per_files(
        "paldeck:entries", [gamedata.DATA_PATH, habitats.DATA_PATH],
        _build_paldeck_entries)


def _build_paldeck_entries() -> list[dict[str, Any]]:
    grouped: dict[Any, dict[str, Any]] = {}
    for species_id in (gamedata.load().get("pals") or {}):
        details = gamedata.describe_pal(species_id)
        if details["variants"]:
            continue
        number = details["paldeckNumber"]
        # Only real Paldeck entries. Negative zukan indices are not "missing a
        # number" — they mark things the in-game Paldeck does not list at all:
        # -2 for gym bosses, -1 for species present in the files but unreleased.
        # Including them put five "Zoe & Grizzbolt (Gym)" rows in a Paldeck.
        if not number or number <= 0:
            continue
        row = grouped.get(number)
        if row is None:
            grouped[number] = {**details, "speciesIds": [species_id]}
            continue
        row["speciesIds"].append(species_id)
        # Keep the shortest id as canonical: `HadesBird` reads better than
        # `HadesBird_Oilrig` and is the one every other view already uses.
        if len(species_id) < len(row["id"]):
            row.update({**details, "speciesIds": row["speciesIds"]})

    entries = []
    for row in grouped.values():
        habitat = habitats.merged(row["speciesIds"])
        entries.append({**row, "hasHabitat": habitat["known"],
                        "habitatCells": len(habitat["cells"])})
    return entries


def _paldeck_siblings() -> dict[str, list[str]]:
    """
    Species id -> every id merged into the same Paldeck entry.

    An index rather than a scan: the detail route needs one lookup, and walking
    204 entries per request to find it was the whole reason that route cost as
    much as the listing.
    """
    def build() -> dict[str, list[str]]:
        index: dict[str, list[str]] = {}
        for entry in _paldeck_entries():
            for species_id in entry["speciesIds"]:
                index[species_id] = entry["speciesIds"]
        return index

    return viewcache.per_files(
        "paldeck:siblings", [gamedata.DATA_PATH, habitats.DATA_PATH], build)


@app.get("/api/world/paldeck")
def get_paldeck(request: Request) -> dict[str, Any]:
    """
    Every Pal in the game, from bundled data rather than from the save.

    This is a reference view, not a report on your server — it lists what exists
    so a player can look something up, which is why it is `VIEW_BASIC` and needs
    no parsed world.

    Variant and boss forms are excluded: they share a Paldeck number with their
    base species and would list the same Pal several times. `describe_pal`
    already strips those prefixes, so anything with a prefix is a duplicate.
    """
    authz.require(request, roles_module.VIEW_BASIC)
    try:
        entries = _paldeck_entries()
    except gamedata.GameDataUnavailable as e:
        raise HTTPException(503, str(e))

    # Paldeck order, with unlisted entries last.
    #
    # `paldeckNumber` is not simply "0 when absent": the game uses **negative**
    # zukan indices for things that are not Paldeck entries at all — gym bosses
    # are -2, and -1 marks a species present in the files but not in the
    # Paldeck. A plain `or 9999` leaves those negatives intact and sorts them
    # ahead of Lamball, which is how Axel & Orserk ended up as entry number one.
    entries.sort(key=lambda p: (p["paldeckNumber"] if p["paldeckNumber"] > 0 else 9999,
                                p["name"]))
    return {"pals": entries, "habitats": habitats.summary()}


@app.get("/api/world/paldeck/{species_id}")
def get_paldeck_entry(species_id: str, request: Request) -> dict[str, Any]:
    """One Pal's full detail, including where it spawns."""
    authz.require(request, roles_module.VIEW_BASIC)
    try:
        details = gamedata.describe_pal(species_id)
    except gamedata.GameDataUnavailable as e:
        raise HTTPException(503, str(e))
    if not details["known"]:
        raise HTTPException(404, f"No bundled data for species {species_id!r}")

    # palcalc's table carries stats, work values and breeding power that the
    # game-data bundle does not, so the two are merged rather than picked between.
    extra: dict[str, Any] = {}
    try:
        info = breeding.pal_info(species_id)
        if info.get("known"):
            extra = {
                "stats": info.get("stats") or {},
                "work": info.get("work") or {},
                "breedingPower": info.get("breedingPower"),
                "genderOdds": info.get("genderOdds") or {},
            }
    except breeding.BreedingDataError:
        pass        # breeding data is optional; the entry is still useful

    # What this species CAN learn, which is a different question from what a
    # given Pal has equipped — and the egg half of it is the reason to breed at
    # all, since an egg move cannot be taught to a Pal that already exists.
    try:
        extra["moves"] = gamedata.species_moves(species_id)
    except gamedata.GameDataUnavailable:
        pass        # the entry is still useful without them

    # And whether breeding can reach it. The Paldeck is where somebody looks a
    # Pal up before going after it, so "the game names one exact pairing for
    # this" belongs here as much as on the planner.
    try:
        extra["obtainability"] = breeding.obtainability(species_id)
    except (breeding.BreedingDataError, gamedata.GameDataUnavailable):
        pass

    # Merge the location variants that share this Paldeck number, so the map
    # shows every place the Pal is found rather than one of them.
    ids = _paldeck_siblings().get(species_id, [species_id])
    return {**details, **extra, "speciesIds": ids, "habitat": habitats.merged(ids)}


@app.get("/api/items/scopes")
def item_scopes(request: Request) -> dict[str, Any]:
    """
    Which item scopes this caller may ask for, and what they are called.

    Returned rather than inferred client-side because the answer depends on a
    policy the browser does not hold, and on which guilds the caller is actually
    in — a UI that guessed would offer options that come back empty.
    """
    authz.require(request, roles_module.VIEW_SELF)
    _, guild_ids = _own_identity(request)
    guilds = [
        {"id": str(g.get("id") or ""), "name": g.get("name") or "Guild"}
        for g in savecache.get_section("guilds")
        if str(g.get("id") or "") in guild_ids
    ]
    return {
        "guilds": guilds,
        "serverWide": _may_see_server_wide(request),
        "bases": roles_module.VIEW_DETAIL in authz.effective_capabilities(
            authz.current_user(request)
        ),
    }


@app.get("/api/items")
def get_item_totals(
    request: Request,
    limit: int = Query(500, ge=1, le=5000),
    guild: Optional[str] = None,
) -> dict[str, Any]:
    """
    Every item on the server, totalled across all containers — the equivalent of
    standing at an item retrieval unit and asking what exists.

    Names are resolved at request time rather than baked into the parse cache, so
    refreshing the bundled game data does not require re-parsing the world.
    """
    authz.require(request, roles_module.VIEW_SELF)
    data = savecache.get_data() or {}
    containers = data.get("containers") or {}

    # Scope before totalling. A server-wide figure answers "what exists here",
    # which is an operations question rather than a player-facing one, and it
    # discloses every guild's holdings in a single number.
    own_uid, own_guilds = _own_identity(request)
    server_wide = _may_see_server_wide(request)

    if guild:
        if guild not in own_guilds and not server_wide:
            raise HTTPException(403, "You are not a member of that guild.")
        items, scope = _items_for_guilds({guild}), f"guild:{guild}"
    elif server_wide:
        items, scope = (data.get("items") or []), "server"
    else:
        # The default for anyone below the threshold: their own guilds' bases.
        # Not "their own items" — containers belong to *bases*, which belong to
        # guilds, so there is no per-player ownership in the save to key on.
        items, scope = _items_for_guilds(own_guilds), "own"

    enriched = []
    for entry in items[:limit]:
        details = gamedata.describe_item(entry.get("itemId") or "")
        details.pop("id", None)  # `itemId` is the canonical key here
        enriched.append({**entry, **details})

    return {
        "items": enriched,
        # Say what was actually counted. A total labelled server-wide that
        # silently was not would be worse than a refusal.
        "scope": scope,
        "itemTypes": len(items),
        "totalCount": sum(i["count"] for i in items),
        "containersScanned": len(containers),
        "truncated": len(items) > limit,
        "namesResolved": gamedata.available(),
    }


@app.get("/api/players")
def list_players(request: Request) -> list[dict]:
    """
    The player roster, minus anyone hiding from this viewer.

    `get_players` stays unfiltered because other endpoints aggregate over it —
    progress totals and discovery denominators must count everyone, and both are
    gated above the ranks privacy can conceal from anyway.
    """
    # Gated here as well as in the proxy allowlist. `_viewer()` below resolves
    # an identity and returns "guest" when there is none, which filters but does
    # not refuse — see AGENTS.md: the proxy forwards a credential, it does not
    # assert one, so a route that only filters is trusting exactly what the
    # security model says not to trust.
    authz.require(request, roles_module.VIEW_DETAIL)
    return privacy.filter_players(
        get_players(), privacy.hidden_uids(*_viewer(request))["players"]
    )


@app.get("/api/players/roster")
def player_roster(request: Request) -> dict[str, Any]:
    """
    Everyone who has played here, online or not, with what you may do about them.

    The live REST list only knows who is connected *right now*, which is the
    wrong population for "give this person a dashboard account" — the player you
    want to add is usually the one who logged off. The save knows everybody, so
    that is the base list and online status is an annotation on it.

    Account linkage is included only for callers who could act on it. It is not
    especially sensitive, but "which of your players has a dashboard login" is
    not a roster fact and does not belong in a Player's view of their peers.

    Privacy applies as everywhere else: a hidden player is absent, not greyed.
    """
    authz.require(request, roles_module.VIEW_DETAIL)
    user = authz.current_user(request)
    role, username = _viewer(request)
    hidden = privacy.hidden_uids(role, username)["players"]
    players = privacy.filter_players(get_players(), hidden)

    online_by_uid: dict[str, dict] = {}
    try:
        for entry in gameapi.players():
            online_by_uid[privacy.normalise_uid(entry.get("userId"))] = entry
    except Exception:  # noqa: BLE001
        # An unreachable game server means "nobody is known to be online", not
        # an error — the save half of this view is still worth showing.
        online_by_uid = {}

    may_manage_users = roles_module.USERS_MANAGE in authz.effective_capabilities(user)
    linked: dict[str, str] = {}
    if may_manage_users:
        for account in accounts.list_users():
            uid = privacy.normalise_uid(account.get("steamUid"))
            if uid:
                linked[uid] = account["username"]

    rows = []
    for player in players:
        uid = privacy.normalise_uid(player.get("uid"))
        live = online_by_uid.get(uid)
        row = {
            **player,
            "online": live is not None,
            # The REST id, which is what kick and ban take. It is not always
            # spelled the way the save spells the uid, so it is carried through
            # rather than reconstructed.
            "restUserId": (live or {}).get("userId", ""),
            "ping": (live or {}).get("ping"),
        }
        if may_manage_users:
            row["accountUsername"] = linked.get(uid, "")
            row["hasAccount"] = uid in linked
        rows.append(row)

    rows.sort(key=lambda r: (not r["online"], (r.get("name") or "").lower()))
    return {
        "players": rows,
        "onlineCount": sum(1 for r in rows if r["online"]),
        "gameApiReachable": bool(online_by_uid) or gameapi.configured(),
        "canManageAccounts": may_manage_users,
    }


def _items_for_guilds(guild_ids: set[str]) -> list[dict]:
    """
    Item totals restricted to the bases of the given guilds.

    Built from `baseStorage`, which the parse already produces per base with its
    own per-item breakdown — so this is a group-by rather than a second walk of
    11,639 containers.

    Player inventories and palboxes are **not** included. They are containers the
    per-base join does not attribute to any base (that is exactly how
    `extract_container_ownership` separates them), and folding them in would make
    a guild total silently include things nobody put in guild storage.
    """
    if not guild_ids:
        return []

    totals: dict[str, int] = {}
    for entry in savecache.get_section("baseStorage"):
        if str(entry.get("guildId") or "") not in guild_ids:
            continue
        for item in entry.get("items") or []:
            item_id = item.get("itemId") or ""
            if item_id:
                totals[item_id] = totals.get(item_id, 0) + int(item.get("count") or 0)

    return [
        {"itemId": item_id, "count": count}
        for item_id, count in sorted(totals.items(), key=lambda kv: -kv[1])
    ]


def _read_player_sav(path: str, uid: str) -> dict:
    """One player's own save, decompressed and extracted. ~2.3 ms."""
    gvas = load_gvas(path)
    if not gvas:
        return {}
    try:
        detail = dict(extract_player_save(gvas, uid))
        detail["progress"] = extract_player_progress(gvas)
        return detail
    except Exception as e:  # noqa: BLE001
        logger.warning("Player save extract failed for %s: %s", uid, e)
        return {}


def get_players() -> list[dict]:
    """
    Players from Level.sav, enriched with their own .sav where available.

    Each player .sav is Oodle-decompressed and GVAS-parsed, which is ~2.3 ms —
    small enough to have looked free, except that four endpoints call this
    (`/api/players`, `/api/progress`, `/api/world/discoveries`,
    `/api/players/{uid}`) and the cost is per player. A 32-player server was
    paying ~73 ms of identical parsing on every one of those requests.

    `viewcache.per_file` keys on the file's own size and mtime, so a player
    logging out, the player editor writing, or a backup restore all invalidate it
    without anything having to remember to say so.
    """
    enriched = []
    for player in savecache.get_section("players"):
        entry = dict(player)
        uid = (player.get("uid") or "").replace("-", "")
        path = get_player_sav_path(uid) if uid else None
        if path:
            entry.update(viewcache.per_file(path, lambda p=path, u=uid: _read_player_sav(p, u)))
        enriched.append(entry)

    return enriched


def _relic_lines(spent: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Each statue line: what this player bought, and what the next rank costs.

    **Every line is returned, including the ones at zero.** "You have spent
    nothing on Endurance" is a real answer and is exactly what someone deciding
    where to put the next effigy needs; dropping empty lines would make the
    panel look like it only knows about the ones already invested in.

    `hasEffectRate` is passed straight through and must be honoured.
    `CapturePower` carries 0.0 on all 15 of its ranks while the other twelve
    carry real values — its effect is expressed somewhere other than that
    column, so rendering "+0%" for it would be a confident wrong number rather
    than a missing one.
    """
    lines = []
    for kind, meta in (gamedata.progression().get("relicTypes") or {}).items():
        used = int(spent.get(kind) or 0)
        rank = gamedata.relic_rank(kind, used)
        if rank is None:
            continue
        lines.append({
            "type": kind,
            "name": meta.get("name") or kind,
            "nameIsInternal": bool(meta.get("nameIsInternal")),
            "description": meta.get("description") or "",
            "spent": used,
            **rank,
        })
    # Most invested first — the lines someone has actually committed to are the
    # ones they are reasoning about, and thirteen alphabetical rows bury them.
    lines.sort(key=lambda r: (-r["spent"], r["name"]))
    return lines


@app.get("/api/progress")
def get_progress(request: Request) -> dict[str, Any]:
    """
    Per-player progression, with "how much is left" for each category.

    The denominators are the union of what every player on this server has
    found, because the save records only obtained entries — so they are a floor,
    not the game's true totals. Anything nobody has discovered yet is invisible
    to us, and the numbers rise as people explore.

    **Two filters, and the totals are computed before either.** A viewer without
    `VIEW_DETAIL` sees only their own row, and per-player privacy removes anyone
    hiding from them — but the union that forms each denominator is taken over
    *every* player first. Narrowing it to the visible rows would leak the
    opposite way: "of 174" quietly becoming "of 96" tells you exactly how much
    the people you cannot see have found.
    """
    authz.require(request, roles_module.VIEW_SELF)

    players = get_players()
    entries = [
        {
            "uid": p.get("uid"),
            "name": p.get("name"),
            "level": p.get("level"),
            **(p.get("progress") or {}),
        }
        for p in players
        if p.get("progress")
    ]

    totals = progress_totals(entries)

    if roles_module.VIEW_DETAIL not in authz.effective_capabilities(
        authz.current_user(request)
    ):
        own = authz.linked_uid(authz.current_user(request))
        entries = [e for e in entries if privacy.normalise_uid(e.get("uid")) == own]
    else:
        hidden = privacy.hidden_uids(*_viewer(request))
        entries = privacy.filter_players(entries, hidden["players"])

    for entry in entries:
        remaining = {}
        for label, info in totals.items():
            total = info["total"]
            obtained = (entry.get(label) or {}).get("obtained", 0)
            remaining[label] = max(0, total - obtained)
            # The key lists are large and were only needed to build the union.
            if isinstance(entry.get(label), dict):
                entry[label] = {
                    "obtained": obtained,
                    "of": total,
                    "source": info["source"],
                }
        entry["remaining"] = remaining
        # What the effigies this player collected actually bought them. The map
        # has shown all 396 and which are found since the layer shipped, and has
        # never said what finding them did.
        entry["relicLines"] = _relic_lines(entry.pop("relicsSpent", None) or {})

    return {
        "players": entries,
        "knownTotals": totals,
        "note": (
            "Categories marked 'reference' use published Palworld 1.0 totals. "
            "Those marked 'discovered' fall back to the union of what players "
            "here have found, which is a floor rather than a true total — save "
            "files only record obtained entries."
        ),
    }


@app.get("/api/progress/detail")
def get_progress_detail(request: Request, uid: Optional[str] = None) -> dict[str, Any]:
    """
    Named checklists — *which* bosses, regions and fast-travel points are left.

    `/api/progress` counts; this one lists. Split rather than folded in because
    the lists are large (174 fast-travel points, 396 effigies) and the summary is
    what most callers want.

    **THIS IS DISCOVERY DATA AND THE FILTERING IS SERVER-SIDE.** It goes through
    the same two gates as `/api/progress` — a viewer without `VIEW_DETAIL` sees
    only their own row, and per-player privacy removes anyone hiding from them —
    plus `discoveryVisibility`, which decides whether the *undiscovered* half is
    shown at all. A UI that received everything and hid some of it would be
    handing out the answers in the network tab, which is the mistake
    `/api/world/discoveries` exists not to make.
    """
    authz.require(request, roles_module.VIEW_SELF)

    user = authz.current_user(request)
    caps = authz.effective_capabilities(user)
    own = privacy.normalise_uid(authz.linked_uid(user))

    players = [p for p in get_players() if p.get("progress")]

    if roles_module.VIEW_DETAIL not in caps:
        players = [p for p in players if privacy.normalise_uid(p.get("uid")) == own]
    else:
        hidden = privacy.hidden_uids(*_viewer(request))
        players = privacy.filter_players(players, hidden["players"])

    if uid:
        wanted = privacy.normalise_uid(uid)
        players = [p for p in players if privacy.normalise_uid(p.get("uid")) == wanted]

    # Whether someone may see what they have NOT found is the operator's call,
    # exactly as on the map — and your own progress is always your own.
    #
    # `effigies` rather than `fastTravel`: the two categories are separately
    # configurable and a checklist showing every undiscovered location is the
    # stricter disclosure of the two, so it takes the stricter setting. If they
    # ever disagree, erring towards the tighter one is the safe direction.
    show_missing = _may_see_undiscovered(request, "effigies")

    entries = []
    for player in players:
        detail = progresscheck.describe(player.get("progress") or {})
        if not show_missing and privacy.normalise_uid(player.get("uid")) != own:
            detail = _drop_missing(detail)
        entries.append({
            "uid": player.get("uid"),
            "name": player.get("name"),
            "level": player.get("level"),
            **detail,
        })

    return {
        "players": entries,
        "showsMissing": show_missing,
        "available": progresscheck.available(),
    }


def _drop_missing(detail: dict[str, Any]) -> dict[str, Any]:
    """
    Strip the not-yet-found half, **server-side**.

    Recursive because `fieldBosses` nests its two halves, and a filter that only
    understood the top level would leave the Pal boss list untouched — which is
    the larger of the two and the one worth hiding.
    """
    def strip(value: Any) -> Any:
        if isinstance(value, dict):
            if "missing" in value:
                value = {k: v for k, v in value.items() if k != "missing"}
                value["missingHidden"] = True
            return {k: strip(v) for k, v in value.items()}
        return value

    return strip(detail)


# A player's own item containers, in the order the game shows them.
#
# `EssentialContainerId` is the key-items bag — saddles, harnesses, key spheres.
# Measured on the reference world: 25 items, **none carrying a `dynamic_id`**, so
# unlike weapons and armour they are genuinely editable through the existing slot
# writer. That was the open question about whether player inventory editing was
# worth building at all.
PLAYER_CONTAINERS: tuple[tuple[str, str, str], ...] = (
    ("CommonContainerId", "Inventory", "Everyday carry — materials, food, ammo"),
    ("EssentialContainerId", "Key items", "Saddles, harnesses, key spheres"),
    ("WeaponLoadOutContainerId", "Weapons", "Durability-bearing; read-only"),
    ("PlayerEquipArmorContainerId", "Armor", "Durability-bearing; read-only"),
    ("FoodEquipContainerId", "Food slots", "The quick-use food bar"),
    ("DropSlotContainerId", "Drop slots", "Staging area the game uses on death"),
)


@app.get("/api/players/{uid}/containers")
def get_player_containers(uid: str, request: Request) -> dict[str, Any]:
    """
    One player's item containers, with fill and how much of each is editable.

    **Scoped like everything else**: `VIEW_SELF` gets your own, anyone else's
    needs `VIEW_DETAIL`. Reading a container's *contents* still goes through
    `/api/inventory/{id}`, which enforces its own rules — this only says which
    ids belong to whom.

    `editableSlots` is reported per container so the UI can say "4 of 4 locked"
    before someone picks it, rather than after. A slot with a `dynamic_id` names
    a record in `DynamicItemSaveData`; overwriting it orphans that record and a
    replacement cannot be fabricated, so the writer refuses those outright.
    """
    authz.require(request, roles_module.VIEW_SELF)
    asked = privacy.normalise_uid(uid)
    user = authz.current_user(request)

    if roles_module.VIEW_DETAIL not in authz.effective_capabilities(user):
        if asked != authz.linked_uid(user):
            raise HTTPException(403, "You can only view your own inventory")
    elif asked in privacy.hidden_uids(*_viewer(request))["players"]:
        raise HTTPException(404, f"Player {uid} not found")

    player = next(
        (p for p in get_players() if privacy.normalise_uid(p.get("uid")) == asked),
        None,
    )
    if player is None:
        raise HTTPException(404, f"Player {uid} not found")

    ids = player.get("inventoryContainerIds") or {}
    data = savecache.get_data() or {}
    decoded = (data.get("containers") or {}) if isinstance(data.get("containers"), dict) else {}

    out = []
    for field, label, note in PLAYER_CONTAINERS:
        container_id = str(ids.get(field) or "")
        if not container_id:
            continue
        slots = decoded.get(container_id)
        if slots is None:
            # The container exists on the player but the parse did not decode
            # items. Reported rather than dropped, so "no items parsed" does not
            # masquerade as "this player has no key items".
            out.append({"field": field, "label": label, "note": note,
                        "containerId": container_id, "decoded": False})
            continue
        occupied = [s for s in slots if not s.get("isEmpty")]
        locked = sum(1 for s in occupied if s.get("hasDynamicId"))
        out.append({
            "field": field, "label": label, "note": note,
            "containerId": container_id, "decoded": True,
            "totalSlots": len(slots), "usedSlots": len(occupied),
            "itemCount": sum(int(s.get("stackCount") or 0) for s in occupied),
            "lockedSlots": locked,
            "editableSlots": len(occupied) - locked,
        })

    return {
        "uid": player.get("uid"),
        "name": player.get("name"),
        "containers": out,
    }


@app.get("/api/players/{uid}")
def get_player(uid: str, request: Request) -> dict:
    """
    One player's full record.

    `VIEW_SELF` is enough for your own; anyone else's needs `VIEW_DETAIL`, and
    privacy applies on top. Without the own-uid check this was the single-record
    way around every roster filter — `/api/players` hid someone and
    `/api/players/<their uid>` handed them over.
    """
    authz.require(request, roles_module.VIEW_SELF)
    asked = privacy.normalise_uid(uid)
    user = authz.current_user(request)

    if roles_module.VIEW_DETAIL not in authz.effective_capabilities(user):
        if asked != authz.linked_uid(user):
            raise HTTPException(403, "You can only view your own character")
    elif asked in privacy.hidden_uids(*_viewer(request))["players"]:
        # 404, not 403 — see `get_one_base_storage`. "You may not see this" is
        # itself a disclosure that the player exists and is hiding.
        raise HTTPException(404, f"Player {uid} not found")

    for player in get_players():
        if privacy.normalise_uid(player.get("uid")) == asked:
            return player
    raise HTTPException(404, f"Player {uid} not found")


@app.get("/api/inventory/{container_id}")
def get_inventory(container_id: str, request: Request) -> dict:
    """
    One container's slots, by id.

    **Base privacy applies here, and this was the hole underneath it.** The
    route took a container id and returned its contents with no check of any
    kind, so every filter built on top of `_hidden_base_ids` — the base marker,
    the objects inside it, its storage summary — was reachable around by asking
    for the containers directly. Container ids are not secret either: the base
    summary lists them for the bases you *can* see, and `/api/bases/storage`
    hands them out.

    A withheld container answers 404 for the same reason a withheld base does.

    **`VIEW_SELF` reaches your own guild's containers**, matching
    `/api/bases/storage` — a Player who can see their base's summary must be able
    to open the chest in it, or "what is in it?" is a button that always fails.
    Below `VIEW_DETAIL` the rule inverts: instead of "not one of the hidden
    ones", it is "must be one of *mine*", so a container belonging to no base at
    all (a player inventory, a world-placed chest) is refused rather than
    defaulting open.
    """
    authz.require(request, roles_module.VIEW_SELF)

    own = _own_guild_base_ids(request)
    if own is not None:
        mine = {
            c.get("containerId")
            for s in savecache.get_section("baseStorage")
            if str(s.get("baseId") or "") in own
            for c in (s.get("containers") or [])
        }
        if container_id not in mine:
            raise HTTPException(404, "Container not found")

    hidden = _hidden_base_ids(request)
    if hidden:
        for summary in savecache.get_section("baseStorage"):
            if str(summary.get("baseId") or "") not in hidden:
                continue
            if any(c.get("containerId") == container_id
                   for c in (summary.get("containers") or [])):
                raise HTTPException(404, "Container not found")

    data = savecache.get_data() or {}
    containers = data.get("containers") or {}
    slots = containers.get(container_id)
    if slots is None:
        raise HTTPException(
            404,
            "Container not found. Item containers are only decoded when "
            "PARSE_INCLUDE_ITEMS=true.",
        )
    used = sum(1 for s in slots if not s.get("isEmpty"))
    # Names and icons resolved here rather than baked into the parse, for the
    # same reason `/api/items` does it: refreshing the bundled game data then
    # updates every readout without re-parsing a 55 MB world. The parser writes
    # `itemName` as the raw id, which is a placeholder, not an answer.
    enriched = []
    for slot in slots:
        item_id = slot.get("itemId") or ""
        details = gamedata.describe_item(item_id) if item_id else {}
        enriched.append({
            **slot,
            "itemName": details.get("name") or item_id,
            "icon": details.get("icon") or "",
            "maxStack": details.get("maxStack") or 0,
        })
    return {
        "containerId": container_id,
        "slots": enriched,
        "capacity": len(slots),
        "usedSlots": used,
    }


# ─── Breeding ────────────────────────────────────────────


def _own_identity(request: Request) -> tuple[str, set[str]]:
    """
    The caller's own character uid and the guild ids they belong to.

    Both empty for a guest or an account with no linked character — which means
    "own-scoped" resolves to nothing rather than to everything. That is the
    intended direction: an unlinked account has no claim on anyone's data, and
    the fix is to link it from the Players tab.
    """
    user = authz.current_user(request)
    uid = authz.linked_uid(user)
    if not uid:
        return "", set()
    return uid, _guilds_of(uid)


def _guilds_of(uid: str) -> set[str]:
    """The guild ids a character uid belongs to, from the parsed guild list."""
    key = privacy.normalise_uid(uid)
    if not key:
        return set()
    return {
        str(guild.get("id") or "")
        for guild in savecache.get_section("guilds")
        if key in {
            privacy.normalise_uid(m.get("uid"))
            for m in (guild.get("members") or [])
            if m.get("uid")
        }
    }


def _may_see_server_wide(request: Request) -> bool:
    role, _ = _viewer(request)
    return policy_module.meets_scope(
        role,
        policy_module.load_policy().get(
            "serverTotalsVisibility", policy_module.DEFAULT_SERVER_TOTALS
        ),
    )


def _may_see_all_pals(request: Request) -> bool:
    role, _ = _viewer(request)
    return policy_module.meets_scope(
        role,
        policy_module.load_policy().get(
            "allPalsVisibility", policy_module.DEFAULT_ALL_PALS
        ),
    )


#: The uid the parser writes when a character has no `OwnerPlayerUId` at all.
#: It is a real value in the save rather than a placeholder this code invented,
#: which is why "unowned" has to test for it as well as for the empty string.
_NO_OWNER = "0" * 32


def _norm_uid(value: Any) -> str:
    return str(value or "").replace("-", "").lower()


def _unowned_pal(pal: dict) -> bool:
    """
    True for a Pal that belongs to a guild rather than to a person.

    159 of the reference world's 1,905: base workers and the contents of shared
    Pal stores. Defined once because `_pals_for` uses it to decide what to
    include and `_breeding_scope` uses it to say how many of those there were —
    two answers that must not be able to disagree.
    """
    uid = _norm_uid(pal.get("ownerUid"))
    return not uid or uid == _NO_OWNER


def _scope_pals(
    pals: list[dict], owner: Optional[str], guild_ids: Optional[set[str]] = None
) -> list[dict]:
    """
    Every Pal in `pals` that `owner` can actually get their hands on — theirs,
    plus their guild's shared ones.

    **A personal `OwnerPlayerUId` is not the whole of ownership**, and filtering
    on it alone was silently losing Pals. Measured on the reference world, 159 of
    1,905 carry no owner uid at all: they sit in a base's workforce or in a
    structure the guild built to store Pals (a Dimensional Pal Storage, a Global
    Pal Storage, a Flea Market stand). Those are not unowned — they belong to the
    *guild*, every member can walk up and take one out, and they are as breedable
    as anything in a palbox. Dropping them made the breeding planner insist a
    player did not have species standing in their own base.

    So the rule is: your own Pals, plus the ownerless Pals of every guild you are
    in.

    **Guild membership comes from the guild list, not from the Pals.** Deriving it
    from the Pals the caller already owns is a shorter route to the same set
    almost always, and it fails in exactly the case this whole change is about: a
    player with everything deployed at a base owns no Pals to derive a guild
    from, so the set comes out empty and they are shown nothing — the original
    bug, reintroduced at the point of fixing it. `guild_ids` is the caller's real
    membership; the Pal-derived set is only the fallback for a caller that did
    not supply one.

    A Pal with a *different* player's uid is never included, whatever guild it is
    in — a shared palbox is not a shared Pal, and merging the two would report
    somebody else's team as yours.

    Takes the list rather than fetching it, because two endpoints scope Pals and
    they read from different places — `/api/pals` from the enriched, name-joined
    copy and the breeding routes from the raw section. They had a filter each,
    and the comment beside the one in `/api/pals` already recorded that the two
    had drifted apart once. One rule, passed the list it applies to.
    """
    if not owner:
        return pals

    key = owner.replace("-", "").lower()

    def _owned(pal: dict) -> bool:
        return _norm_uid(pal.get("ownerUid")).startswith(key)

    guilds = set(guild_ids) if guild_ids else {
        str(p.get("guildId") or "") for p in pals if _owned(p)
    }
    guilds.discard("")

    return [
        p for p in pals
        if _owned(p) or (_unowned_pal(p) and str(p.get("guildId") or "") in guilds)
    ]


def _pals_for(owner: Optional[str]) -> list[dict]:
    """`_scope_pals` over the parsed world. The breeding routes' entry point."""
    return _scope_pals(
        savecache.get_section("pals"), owner, _guilds_of(owner) if owner else None
    )


def _breeding_owner(request: Request, owner: Optional[str]) -> Optional[str]:
    """
    Which player's Pals a breeding request may actually use.

    Below the `allPalsVisibility` threshold a caller is pinned to their own
    character, whatever they asked for — the query parameter is a convenience
    for people who may already see everyone, not a way around the setting.

    An unlinked account gets a uid that matches nothing, which yields an empty
    palbox. That is the honest answer: they have no Pals *here*.
    """
    if _may_see_all_pals(request):
        return owner
    uid, _ = _own_identity(request)
    return uid or "\u0000no-such-uid"


def _breeding_scope(request: Request, owner: Optional[str]) -> dict[str, Any]:
    """
    Which Pals a breeding answer was actually computed from.

    Every scoped breeding route returns this, not just `/palbox`. The planner
    fetches four endpoints and shows one header, so scope reported on one of them
    describes the other three by implication — and when the backend silently pins
    a request to the caller, that implication is wrong on all three. A route plan
    computed from your own palbox, displayed under a header saying "all Pals on
    the server", reads as a wrong answer rather than as a narrower question.

    `pals` is the count the answer was built from. Zero with `linkedToPlayer`
    false is the specific case people report as "it forgot my account": the
    request succeeded, the scope resolved to a character that does not exist, and
    an empty planner is indistinguishable from a broken one without this.

    `shared` breaks that total down, because the answer now legitimately includes
    Pals the player does not personally own — the guild's base workers and
    anything in a shared Pal store (see `_pals_for`). Someone reading "614 Pals"
    against a palbox holding 560 should be able to see where the rest came from
    rather than suspecting the count.
    """
    may_see_all = _may_see_all_pals(request)
    own_uid, _ = _own_identity(request)
    effective = _breeding_owner(request, owner)
    pals = _pals_for(effective)
    shared = sum(1 for p in pals if _unowned_pal(p))
    return {
        "mayScopeToOthers": may_see_all,
        "scope": ("server" if not owner else f"player:{owner}") if may_see_all else "own",
        "linkedToPlayer": bool(own_uid),
        "pals": len(pals),
        "shared": 0 if effective is None else shared,
    }


@app.get("/api/breeding/palbox")
def breeding_palbox(request: Request, owner: Optional[str] = None) -> dict:
    """
    The caller's breedable species, and **what "the caller's" resolved to**.

    `scope` travels with the answer for the same reason `/api/items` reports one:
    below `allPalsVisibility` the request is silently pinned to the caller's own
    character, and a UI that does not know that labels the result wrongly. The
    planner's owner selector read "All Pals on the server" while showing one
    Player their own palbox — the data was right and the header was a lie, which
    is worse than either being wrong on its own, because nothing looks broken.
    """
    authz.require(request, roles_module.VIEW_SELF)
    try:
        summary = breeding.summarize_palbox(
            _pals_for(_breeding_owner(request, owner))
        )
    except breeding.BreedingDataError as e:
        raise HTTPException(503, str(e))

    return {**summary, **_breeding_scope(request, owner)}


@app.get("/api/breeding/offspring")
def breeding_offspring(request: Request, owner: Optional[str] = None) -> list[dict]:
    authz.require(request, roles_module.VIEW_SELF)
    try:
        return breeding.possible_offspring(_pals_for(_breeding_owner(request, owner)))
    except breeding.BreedingDataError as e:
        raise HTTPException(503, str(e))


@app.get("/api/breeding/reachable")
def breeding_reachable(request: Request, owner: Optional[str] = None) -> dict:
    """
    Pals that need an intermediate step, with the shortest route to each.

    The offspring list answers "what can I breed right now"; this answers the
    question straight after it. One BFS serves the whole list, so this costs
    about the same as a single route lookup rather than one per species.

    Scoped through `_breeding_owner` like every other breeding route. It was
    not — it took `owner` and honoured it without a `Request` to check anyone
    against, so it answered for the whole server's Pals regardless of who asked
    and regardless of `allPalsVisibility`. Every sibling route was scoped; this
    one was simply missed.
    """
    authz.require(request, roles_module.VIEW_SELF)
    try:
        pals = _pals_for(_breeding_owner(request, owner))
        summary = breeding.summarize_palbox(pals)
        owned = [s["internalName"] for s in summary["species"]]
        result = breeding.indirect_targets(owned, genders=breeding.gender_pool(pals))
    except breeding.BreedingDataError as e:
        raise HTTPException(503, str(e))
    return {**result, **_breeding_scope(request, owner)}


@app.get("/api/breeding/paths")
def breeding_path(request: Request, target: str, owner: Optional[str] = None) -> dict:
    """
    A route to one target, using only pairs the owner can actually make.

    Gender is enforced (see `breeding._expand`): scoping the planner to one
    player's palbox made single-gender species common, and a plan whose second
    step needs two males is not a plan.

    The scope travels with the plan. "Not reachable" is a claim about a specific
    set of Pals, and a player reading it under the wrong header concludes the
    route finder is broken rather than that they were asked about their own box.
    """
    authz.require(request, roles_module.VIEW_SELF)
    try:
        pals = _pals_for(_breeding_owner(request, owner))
        summary = breeding.summarize_palbox(pals)
        owned = [s["internalName"] for s in summary["species"]]
        result = breeding.breeding_paths(
            target, owned, genders=breeding.gender_pool(pals)
        )
    except breeding.BreedingDataError as e:
        raise HTTPException(503, str(e))
    return {**result, **_breeding_scope(request, owner)}


@app.get("/api/breeding/pals")
def breeding_all_pals(request: Request) -> list[dict]:
    authz.require(request, roles_module.VIEW_SELF)
    try:
        return breeding.all_pals()
    except breeding.BreedingDataError as e:
        raise HTTPException(503, str(e))


@app.get("/api/breeding/odds")
def breeding_odds(request: Request) -> dict:
    authz.require(request, roles_module.VIEW_SELF)
    try:
        return breeding.inheritance_odds()
    except breeding.BreedingDataError as e:
        raise HTTPException(503, str(e))


@app.get("/api/breeding/limits")
def breeding_limits(request: Request) -> dict:
    """
    What breeding cannot reach, and why — reference data, no world needed.

    Split from the planner deliberately. "Not reachable from your Pals" is an
    answer about a palbox and changes with it; "the game names no pairing for
    this" is a fact about Palworld and does not. Serving them from one route
    would make the second look like it depended on the first.

    Cached on the bundles rather than the world for the same reason: nothing
    here reads a save. **Both** bundles, because the answer joins the breeding
    columns in `gamedata.json.gz` to the unique combos in `moves.json.gz` —
    keying on either alone serves a stale answer when the other is replaced.
    """
    authz.require(request, roles_module.VIEW_SELF)
    try:
        return viewcache.per_files(
            "breeding:limits",
            [gamedata.DATA_PATH, gamedata.MOVES_PATH],
            breeding.unbreedable,
        )
    except breeding.BreedingDataError as e:
        raise HTTPException(503, str(e))
    except gamedata.GameDataUnavailable as e:
        raise HTTPException(503, str(e))


@app.get("/api/breeding/predict")
def breeding_predict(request: Request, a: str, b: str) -> dict:
    authz.require(request, roles_module.VIEW_SELF)
    try:
        child = breeding.predict_child(a, b)
    except breeding.BreedingDataError as e:
        raise HTTPException(503, str(e))
    if not child:
        raise HTTPException(404, f"{a} and {b} cannot breed")
    return {
        "parentA": breeding.pal_info(a),
        "parentB": breeding.pal_info(b),
        "child": breeding.pal_info(child),
    }


# ─── Server settings (PalWorldSettings.ini) ──────────────


@app.get("/api/settings/ini")
def read_settings(request: Request) -> dict:
    authz.require(request, roles_module.SETTINGS_WRITE)
    try:
        data = settings_ini.read_ini()
    except settings_ini.SettingsError as e:
        raise HTTPException(404, str(e))
    # What each key does, from Pocketpair's own documentation and the game's own
    # world-settings strings. Attached here rather than inside `read_ini` so a
    # bundle problem can never reach the code that *writes* a server's config.
    settingshelp.annotate(data.get("options") or {})
    return {
        **data,
        # 19 of the 119 have no help at all, and saying so is deliberate: an
        # operator hunting for a missing tooltip should learn that Pocketpair
        # does not document that key, not conclude the dashboard is broken.
        "helpCoverage": settingshelp.coverage(),
        # `all_presets`, so the game's own difficulties reach the UI beside the
        # hand-made ones. Each row carries `source` for grouping.
        "presets": settings_ini.all_presets(),
        "groups": settings_ini.HIGHLIGHT_GROUPS,
        "serverRunning": get_server_state().running,
        # Nothing in this file is hot-swappable: the server reads it at boot only.
        "restartRequiredForAll": True,
        # Whether this deployment's image rewrites the file on start — measured
        # on this server rather than guessed from an image name. `unknown` until
        # the dashboard has written the INI and seen a restart, which is the
        # honest starting state: it means "not yet observed", not "safe".
        "iniWatch": iniwatch.describe(),
    }


class SettingsWrite(BaseModel):
    changes: dict[str, Any]


@app.post("/api/settings/ini")
def write_settings(req: SettingsWrite, request: Request) -> dict:  # noqa: D401
    """
    Write settings to the INI.

    Allowed while the server is running — this is the config directory, not the
    save directory, so there is no corruption risk — but it will not take effect
    until the server restarts.
    """
    user = authz.require_user(request, roles_module.SETTINGS_WRITE)
    try:
        result = settings_ini.write_ini(req.changes)
    except settings_ini.SettingsError as e:
        audit.record(
            audit.SETTINGS_WRITE, username=user["username"], role=user["role"],
            detail=str(e), ip=authz.client_ip(request), result=audit.RESULT_FAILED,
        )
        raise HTTPException(400, str(e))

    audit.record(
        audit.SETTINGS_WRITE, username=user["username"], role=user["role"],
        target=result.get("path"), detail=result.get("applied"),
        ip=authz.client_ip(request),
    )
    return result


@app.post("/api/settings/preset/{preset_id}")
def apply_settings_preset(preset_id: str, request: Request) -> dict:
    user = authz.require_user(request, roles_module.SETTINGS_WRITE)
    try:
        result = settings_ini.apply_preset(preset_id)
    except settings_ini.SettingsError as e:
        audit.record(
            audit.SETTINGS_PRESET, username=user["username"], role=user["role"],
            target=preset_id, detail=str(e), ip=authz.client_ip(request),
            result=audit.RESULT_FAILED,
        )
        raise HTTPException(400, str(e))

    audit.record(
        audit.SETTINGS_PRESET, username=user["username"], role=user["role"],
        target=preset_id, detail=result.get("applied"), ip=authz.client_ip(request),
    )
    return result


# ─── Backups ─────────────────────────────────────────────


@app.get("/api/backups")
def get_backups(request: Request) -> dict[str, Any]:
    authz.require(request, roles_module.BACKUP_MANAGE)
    return {
        "backups": backup_module.list_backups(),
        "usage": backup_module.storage_usage(),
        "scopes": backup_module.RESTORE_SCOPES,
        "retention": backup_module.DEFAULT_RETENTION,
    }


@app.get("/api/backups/{backup_id}")
def get_backup_detail(backup_id: str, request: Request) -> dict:
    authz.require(request, roles_module.BACKUP_MANAGE)
    detail = backup_module.describe_backup(backup_id)
    if not detail:
        raise HTTPException(404, f"Backup {backup_id} not found")
    return detail


class BackupRequest(BaseModel):
    description: Optional[str] = ""


@app.post("/api/backup")
def make_backup(req: BackupRequest, request: Request) -> dict:
    """
    Snapshot the world into a verified archive.

    Safe to run while the server is live: it only reads the save files and
    writes elsewhere. Files may be mid-autosave, so a backup taken on a running
    server is a best-effort snapshot — stop the server for a guaranteed-clean one,
    which is recorded in the manifest either way.
    """
    user = authz.require_user(request, roles_module.BACKUP_MANAGE)
    try:
        meta = backup_module.create_backup(
            description=req.description or "",
            trigger="manual",
            created_by=user["username"],
        )
    except Exception as e:  # noqa: BLE001
        audit.record(
            audit.BACKUP_CREATE, username=user["username"], role=user["role"],
            detail=str(e), ip=authz.client_ip(request), result=audit.RESULT_FAILED,
        )
        raise HTTPException(500, f"Backup failed: {e}")

    audit.record(
        audit.BACKUP_CREATE, username=user["username"], role=user["role"],
        target=meta["id"],
        detail={"sizeBytes": meta["sizeBytes"], "serverWasRunning": meta["serverWasRunning"]},
        ip=authz.client_ip(request),
    )
    return meta


@app.post("/api/backups/{backup_id}/verify")
def verify_backup_route(backup_id: str, request: Request) -> dict:
    """Re-hash the archive and every file in it. This is what makes a backup trustworthy."""
    authz.require(request, roles_module.BACKUP_MANAGE)
    if not backup_module.find_backup(backup_id):
        raise HTTPException(404, f"Backup {backup_id} not found")
    return backup_module.verify_backup(backup_id)


class RenameRequest(BaseModel):
    description: str


@app.patch("/api/backups/{backup_id}")
def rename_backup_route(backup_id: str, req: RenameRequest, request: Request) -> dict:
    """
    Re-describe a backup.

    Audited, because it mutates state a restore decision is made from. A backup
    labelled "before the big edit" that someone quietly relabels is exactly the
    kind of thing an operator needs to be able to look up afterwards — and this
    was the one mutating route in the file with no `audit.record` at all.
    """
    user = authz.require_user(request, roles_module.BACKUP_MANAGE)
    renamed = backup_module.rename_backup(backup_id, req.description)
    if not renamed:
        raise HTTPException(404, f"Backup {backup_id} not found")
    audit.record(
        audit.BACKUP_RENAME, username=user["username"], role=user["role"],
        target=backup_id, detail={"description": req.description},
        ip=authz.client_ip(request),
    )
    return renamed


@app.get("/api/backups/{backup_id}/preview")
def preview_restore_route(
    backup_id: str, request: Request, scope: str = Query("world")
) -> dict:
    """What a restore would change, without changing anything."""
    authz.require(request, roles_module.BACKUP_MANAGE)
    try:
        return backup_module.preview_restore(backup_id, scope)
    except BackupError as e:
        raise HTTPException(404, str(e))


class RestoreRequest(BaseModel):
    scope: str = "world"


@app.post("/api/restore/{backup_id}")
def do_restore(backup_id: str, request: Request, req: RestoreRequest = RestoreRequest()) -> dict:
    user = authz.require_user(request, roles_module.BACKUP_MANAGE)
    ip = authz.client_ip(request)

    def failed(message: str, status: int):
        audit.record(
            audit.BACKUP_RESTORE, username=user["username"], role=user["role"],
            target=backup_id, detail=message, ip=ip, result=audit.RESULT_FAILED,
        )
        return HTTPException(status, message)

    try:
        result = backup_module.restore_backup(
            backup_id, req.scope, created_by=user["username"]
        )
    except ServerRunningError as e:
        raise failed(str(e), 423)
    except BackupError as e:
        raise failed(str(e), 409)
    except Exception as e:  # noqa: BLE001
        logger.exception("Restore failed")
        raise failed(f"Restore failed: {e}", 500)

    audit.record(
        audit.BACKUP_RESTORE, username=user["username"], role=user["role"],
        target=backup_id,
        detail={
            "scope": req.scope,
            "files": len(result["restoredFiles"]),
            "rollbackId": result["rollbackId"],
        },
        ip=ip,
    )
    savecache.request_parse(force=True)
    return result


class PruneRequest(BaseModel):
    dryRun: bool = True


@app.post("/api/backups/prune")
def prune_route(req: PruneRequest, request: Request) -> dict:
    """Apply retention. Defaults to a dry run so nothing is deleted by accident."""
    user = authz.require_user(request, roles_module.BACKUP_MANAGE)
    result = backup_module.prune_backups(dry_run=req.dryRun)
    if not req.dryRun and result["removed"]:
        audit.record(
            audit.BACKUP_DELETE, username=user["username"], role=user["role"],
            target="retention",
            detail={"removed": [r["id"] for r in result["removed"]],
                    "freedBytes": result["freedBytes"]},
            ip=authz.client_ip(request),
        )
    return result


@app.delete("/api/backups/{backup_id}")
def remove_backup(backup_id: str, request: Request) -> dict:
    user = authz.require_user(request, roles_module.BACKUP_MANAGE)
    if not backup_module.delete_backup(backup_id):
        raise HTTPException(404, f"Backup {backup_id} not found")
    audit.record(
        audit.BACKUP_DELETE, username=user["username"], role=user["role"],
        target=backup_id, ip=authz.client_ip(request),
    )
    return {"success": True}


@app.get("/api/backups/{backup_id}/download")
def download_backup(backup_id: str, request: Request):
    """Stream the archive so it can be kept somewhere this server is not."""
    from fastapi.responses import FileResponse

    authz.require(request, roles_module.BACKUP_MANAGE)
    meta = backup_module.find_backup(backup_id)
    if not meta:
        raise HTTPException(404, f"Backup {backup_id} not found")

    path = backup_module.store().path_for(backup_id)
    if not os.path.exists(path):
        raise HTTPException(404, "Archive file is missing")

    stamp = (meta["timestamp"] or "")[:19].replace(":", "-")
    return FileResponse(
        path,
        media_type="application/gzip",
        filename=f"palworld-{meta['worldGuid'] or 'world'}-{stamp}-{backup_id}.tar.gz",
    )


# ─── Backup schedule ─────────────────────────────────────


@app.get("/api/backups/schedule/config")
def get_backup_schedule(request: Request) -> dict:
    authz.require(request, roles_module.BACKUP_MANAGE)
    return schedule_module.get_schedule()


class ScheduleUpdate(BaseModel):
    enabled: Optional[bool] = None
    frequency: Optional[str] = None
    pruneAfter: Optional[bool] = None


@app.post("/api/backups/schedule/config")
def set_backup_schedule(req: ScheduleUpdate, request: Request) -> dict:
    user = authz.require_user(request, roles_module.BACKUP_MANAGE)
    try:
        updated = schedule_module.set_schedule(
            enabled=req.enabled, frequency=req.frequency, prune_after=req.pruneAfter
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    audit.record(
        "backup.schedule", username=user["username"], role=user["role"],
        detail=req.model_dump(exclude_none=True), ip=authz.client_ip(request),
    )
    return updated


# ─── Reports ─────────────────────────────────────────────


@app.get("/api/reports")
def list_reports(request: Request) -> dict:
    """What can be exported, and in which formats."""
    authz.require(request, roles_module.VIEW_DETAIL)
    return {
        "formats": list(reports.FORMATS),
        "reports": [
            {"id": key, "title": title} for key, (title, _fn, _sec) in sorted(reports.REPORTS.items())
        ],
    }


@app.get("/api/reports/{report}")
def get_report(report: str, request: Request, format: str = Query("csv"), baseId: Optional[str] = None):
    """
    Render an inventory report.

    Read-only, but still capability-gated: a full item export is exactly the
    inventory detail VIEW_DETAIL exists to control, and it is easier to walk off
    with than the same data read a screen at a time.
    """
    from fastapi.responses import Response

    authz.require(request, roles_module.VIEW_DETAIL)

    try:
        section = reports.section_for(report)
    except reports.ReportError as e:
        raise HTTPException(404, str(e))

    data = savecache.get_section(section)
    meta: dict[str, Any] = {"generatedAt": _now_iso()}

    if baseId and section == "baseStorage":
        data = [b for b in data if b["baseId"] == baseId]
        if not data:
            raise HTTPException(404, f"No base {baseId} in the current parse")
        meta["base"] = data[0]["baseName"]

    try:
        body = reports.render(report, format, data, meta)
    except reports.ReportError as e:
        raise HTTPException(400, str(e))

    stamp = meta["generatedAt"][:10]
    suffix = f"-{baseId[:8]}" if baseId else ""
    return Response(
        content=body,
        media_type=reports.MEDIA_TYPES[format],
        headers={
            "Content-Disposition":
                f'attachment; filename="palworld-{report}{suffix}-{stamp}.{format}"'
        },
    )


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ─── Structured exports (Phase 6, export half) ───────────


def _export_sections() -> dict:
    """
    Everything the builders may need, from one cache read.

    Deliberately NOT `savecache.get_section`: that returns `[]` for anything
    that is not a list, which would silently empty `containers`, `counts` and
    `containerOwnership` — all dicts — and produce an export that verifies
    cleanly while containing nothing.
    """
    data = savecache.get_data() or {}

    lists = ("guilds", "bases", "baseStorage", "items", "players", "pals")
    dicts = ("counts", "containers", "containerOwnership")

    sections: dict[str, Any] = {
        name: data.get(name) if isinstance(data.get(name), list) else [] for name in lists
    }
    sections.update(
        {name: data.get(name) if isinstance(data.get(name), dict) else {} for name in dicts}
    )

    # The world directory is named after the world's GUID, which is what makes
    # an export identifiable as belonging to this server.
    sections["worldGuid"] = os.path.basename((data.get("worldDir") or "").rstrip("/"))
    return sections


def _owns_export_subject(request: Request, kind: str, subject_id: Optional[str]) -> bool:
    """
    Whether the caller's own character is the `player` or the `pal`'s owner.

    Fails closed on everything ambiguous: no id, no linked account, or a Pal the
    parse does not know. "I could not establish that this is yours" and "this is
    not yours" get the same answer, which is the only safe direction for a
    download.
    """
    own, _ = _own_identity(request)
    if not own or not subject_id:
        return False

    if kind == "player":
        return privacy.normalise_uid(subject_id) == own

    for pal in savecache.get_section("pals"):
        if str(pal.get("instanceId") or "") == subject_id:
            return privacy.normalise_uid(pal.get("ownerUid")) == own
    return False


@app.get("/api/export/{kind}")
def export_save(kind: str, request: Request, id: Optional[str] = None):
    """
    Export world / player / guild / base / container / pal as a verifiable JSON
    document.

    Read-only and audited, because an export is a whole inventory (and real
    Steam IDs) in one file.

    **`pal` and `player` need only `VIEW_SELF` when you ask for your own.**
    Exporting your own character is the same class of thing as reading your own
    palbox, and a Player who cannot get their own Pals out has no way to move a
    character between servers without asking an admin. Everything else — the
    world, a guild, another player — stays at `VIEW_DETAIL`, and asking for
    someone else's `pal`/`player` id at `VIEW_SELF` is refused rather than
    quietly scoped, because a download is a deliberate act with a named target.
    """
    from fastapi.responses import Response

    capabilities = authz.effective_capabilities(authz.current_user(request))
    if kind in ("pal", "player") and roles_module.VIEW_DETAIL not in capabilities:
        user = authz.require_user(request, roles_module.VIEW_SELF)
        if not _owns_export_subject(request, kind, id):
            raise HTTPException(403, "You can only export your own character and Pals")
    else:
        user = authz.require_user(request, roles_module.VIEW_DETAIL)

    try:
        document = saveexport.build(kind, _export_sections(), id)
    except saveexport.ExportError as e:
        raise HTTPException(404 if id else 400, str(e))

    audit.record(
        audit.EXPORT, username=user["username"], role=user["role"],
        target=f"{kind}:{id}" if id else kind,
        detail={"checksum": document["checksum"][:16], "schemaVersion": document["schemaVersion"]},
        ip=authz.client_ip(request),
    )

    return Response(
        content=json.dumps(document, indent=2, ensure_ascii=False),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{saveexport.filename_for(document)}"'
        },
    )


# ─── Teleport (a save edit, not a game command) ──────────


class TeleportRequest(BaseModel):
    uid: str
    x: float
    y: float
    z: float


@app.get("/api/teleport/destinations")
def get_teleport_destinations(request: Request) -> dict[str, Any]:
    """
    Known-good destinations: the 174 fast-travel points, with verified ground `z`.

    Offered because nothing here knows terrain height, so a hand-typed `z` can drop
    a character under the map. These are positions the game itself puts players at.
    """
    authz.require(request, roles_module.VIEW_DETAIL)
    return {"destinations": teleport.destinations()}


@app.post("/api/teleport/preview")
def preview_teleport(req: TeleportRequest, request: Request) -> dict[str, Any]:
    authz.require(request, roles_module.SAVE_EDIT_FULL)
    try:
        return teleport.plan_teleport(req.uid, req.x, req.y, req.z)
    except teleport.TeleportError as e:
        raise HTTPException(400, str(e))


@app.post("/api/teleport")
def do_teleport(req: TeleportRequest, request: Request) -> dict[str, Any]:
    """
    Move a player by editing their save.

    The game cannot do this: its only teleport is anchored to the issuing admin's
    in-game character, and this dashboard has none. The price is that the server
    must be stopped — `guarded_save_write` enforces that, and takes a rollback
    point first.
    """
    user = authz.require_user(request, roles_module.SAVE_EDIT_FULL)
    ip = authz.client_ip(request)
    try:
        result = teleport.apply_teleport(
            req.uid, req.x, req.y, req.z, label=f"teleport by {user['username']}"
        )
    except (teleport.TeleportError, ServerRunningError) as e:
        audit.record(
            audit.SAVE_EDIT, username=user["username"], role=user["role"],
            target=f"teleport:{req.uid}", detail=str(e), ip=ip,
            result=audit.RESULT_FAILED,
        )
        raise HTTPException(400, str(e))

    audit.record(
        audit.SAVE_EDIT, username=user["username"], role=user["role"],
        target=f"teleport:{req.uid}",
        detail={"from": result["from"], "to": result["to"],
                "backupId": result["backupId"]},
        ip=ip,
    )
    return result


# ─── World export with a uid remap (Phase 9) ─────────────


class WorldExportRequest(BaseModel):
    sourceUid: str
    targetUid: str
    planHash: Optional[str] = None


@app.post("/api/export/world-copy/preview")
def preview_world_export(req: WorldExportRequest, request: Request) -> dict[str, Any]:
    """
    What a remapped world copy would change. Reads only.

    Gated on BACKUP_MANAGE rather than VIEW_DETAIL: the export it previews is the
    entire world, every player's data included, which is the same disclosure a
    backup is.
    """
    authz.require(request, roles_module.BACKUP_MANAGE)
    try:
        return soloexport.plan_export(req.sourceUid, req.targetUid)
    except soloexport.SoloExportError as e:
        raise HTTPException(400, str(e))


@app.post("/api/export/world-copy")
def create_world_export(req: WorldExportRequest, request: Request) -> dict[str, Any]:
    """
    Write a copy of the world with one player's uid remapped, and archive it.

    **Deliberately not behind the save-write guard.** Every other writer here needs
    the server provably stopped because it mutates the world in place; this one only
    ever reads the source and writes a new directory, so there is nothing for a
    running server to collide with. Requiring a shutdown would be a ritual, not a
    protection.
    """
    user = authz.require_user(request, roles_module.BACKUP_MANAGE)
    try:
        result = soloexport.apply_export(
            req.sourceUid, req.targetUid, expected_plan_hash=req.planHash
        )
        archive = soloexport.archive_export(result["destination"])
    except soloexport.SoloExportError as e:
        audit.record(
            audit.EXPORT, username=user["username"], role=user["role"],
            target=f"world-copy:{req.sourceUid}->{req.targetUid}",
            detail=str(e), ip=authz.client_ip(request), result=audit.RESULT_FAILED,
        )
        raise HTTPException(400, str(e))

    audit.record(
        audit.EXPORT, username=user["username"], role=user["role"],
        target=f"world-copy:{result['sourceUid']}->{result['targetUid']}",
        detail={
            "mode": result["mode"],
            "referencesRemapped": result["applied"]["total"],
            "sha256": archive["sha256"][:16],
            "sizeBytes": archive["sizeBytes"],
        },
        ip=authz.client_ip(request),
    )
    return {**result, "archive": archive}


@app.post("/api/export/verify")
async def verify_export(request: Request) -> dict:
    """
    Check an export document without importing it.

    This is the read-only half of what the importer will need, and it exists now
    so people can confirm a file survived a round trip before any import path
    is capable of writing anything.
    """
    authz.require(request, roles_module.VIEW_DETAIL)

    raw = await request.body()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            413,
            f"Document is larger than the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit",
        )

    try:
        document = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return {"ok": False, "problems": [f"Not valid JSON: {e}"], "kind": None}

    return saveexport.verify(document)


@app.post("/api/import/preview")
async def preview_import(request: Request) -> dict:
    """
    Dry-run an import: what would change, and why it might be refused.

    Read-only — this cannot write, and there is deliberately no apply endpoint
    yet. Requires SAVE_EDIT_FULL rather than a view capability, because the
    preview tells you precisely how to construct a document that would be
    accepted, and that is editor knowledge.
    """
    authz.require(request, roles_module.SAVE_EDIT_FULL)

    raw = await request.body()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            413, f"Document is larger than the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit"
        )

    try:
        document = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise HTTPException(400, f"Not valid JSON: {e}")

    container_id = ((document or {}).get("payload") or {}).get("containerId") or ""
    current = savecache.get_data() or {}
    containers = current.get("containers") if isinstance(current.get("containers"), dict) else {}
    if container_id not in containers:
        raise HTTPException(
            404,
            f"Container {container_id or '(none named)'} is not in the current parse. "
            "Refresh the save data, or check this export came from this world.",
        )

    try:
        plan = saveimport.plan_container_import(document, containers[container_id])
    except saveimport.ImportRefused as e:
        raise HTTPException(501, str(e))
    except saveimport.ImportError_ as e:
        raise HTTPException(400, str(e))

    return {**plan, "summary": saveimport.summarise(plan), "applied": False}


@app.post("/api/import/apply")
async def apply_import(request: Request, planHash: str = Query(...)) -> dict:
    """
    Apply a previously previewed import.

    `planHash` is required, not optional: it is the hash of the diff the
    operator was actually shown. The import re-plans against the live world and
    refuses if the hash no longer matches, so a world that changed between
    preview and apply cannot be written blind.
    """
    user = authz.require_user(request, roles_module.SAVE_EDIT_FULL)
    ip = authz.client_ip(request)

    raw = await request.body()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            413, f"Document is larger than the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit"
        )

    try:
        document = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise HTTPException(400, f"Not valid JSON: {e}")

    def failed(message: str, status: int):
        audit.record(
            audit.SAVE_IMPORT, username=user["username"], role=user["role"],
            target=((document or {}).get("payload") or {}).get("containerId", ""),
            detail=message, ip=ip, result=audit.RESULT_FAILED,
        )
        return HTTPException(status, message)

    try:
        result = saveimport.apply_container_import(document, expected_plan_hash=planHash)
    except ServerRunningError as e:
        raise failed(str(e), 423)
    except saveimport.ImportRefused as e:
        raise failed(str(e), 501)
    except saveimport.ImportError_ as e:
        raise failed(str(e), 409)
    except Exception as e:  # noqa: BLE001
        logger.exception("Import failed")
        raise failed(f"Import failed: {e}", 500)

    audit.record(
        audit.SAVE_IMPORT, username=user["username"], role=user["role"],
        target=result["containerId"],
        detail={
            "slotsChanged": result["slotsChanged"],
            "itemsBefore": result["itemsBefore"],
            "itemsAfter": result["itemsAfter"],
            "backupId": result["backupId"],
        },
        ip=ip,
    )
    return result


# ─── Save editing ────────────────────────────────────────


class SortRequest(BaseModel):
    merge: bool = True
    baseId: Optional[str] = None
    # How the result is arranged, which is a separate question from `mode` (what
    # is safe to move). Defaults to the previous behaviour so an existing client
    # that does not send it gets exactly what it got before.
    order: str = "id"


def _run_sort(
    mode: str,
    merge: bool,
    request: Request,
    base_id: Optional[str] = None,
    order: str = "id",
) -> dict:
    """
    Authorization and auditing for a container sort.

    Both gates apply: the caller's role must grant the capability AND the
    security level must permit it. Enforced here rather than only in the proxy,
    so the rule holds even for something that reaches the backend directly.
    """
    capability = (
        roles_module.SAVE_SORT_STACKABLES if mode == "stackables"
        else roles_module.SAVE_SORT_ALL
    )
    user = authz.require_user(request, capability)
    ip = authz.client_ip(request)

    def failed(message: str, status: int):
        audit.record(
            audit.SAVE_SORT, username=user["username"], role=user["role"],
            target=mode, detail=message, ip=ip, result=audit.RESULT_FAILED,
        )
        return HTTPException(status, message)

    try:
        result = saveedit.sort_containers(
            mode=mode, merge=merge, base_id=base_id, order=order
        )
    except ServerRunningError as e:
        raise failed(str(e), 423)
    except saveedit.SaveEditError as e:
        raise failed(str(e), 409)
    except Exception as e:  # noqa: BLE001
        logger.exception("Sort failed")
        raise failed(f"Sort failed: {e}", 500)

    audit.record(
        audit.SAVE_SORT, username=user["username"], role=user["role"],
        target=f"{mode} ({result.get('scope', 'world')})",
        detail={
            "containersTouched": result.get("containersTouched"),
            "containersInScope": result.get("containersInScope"),
            "slotsChanged": result.get("slotsChanged"),
            "backupId": result.get("backupId"),
            "merged": merge,
            "order": order,
            "baseId": base_id or "",
        },
        ip=ip,
    )
    return result


@app.post("/api/edit/sort/stackables")
def sort_stackables(req: SortRequest, request: Request) -> dict:
    """
    Tidy containers, touching only plain stackable items.

    Anything with a dynamic_id (weapons, armour, tools) is left exactly where it
    is, so durability records cannot be orphaned.

    Pass `baseId` to scope the sort to one base's storage, and `order` to choose
    between the internal-id ordering and the game's own category ordering.
    """
    return _run_sort("stackables", req.merge, request, req.baseId, req.order)


@app.post("/api/edit/sort/all")
def sort_all(req: SortRequest, request: Request) -> dict:
    """Tidy containers including equipment, carrying dynamic_id links along."""
    return _run_sort("all", req.merge, request, req.baseId, req.order)


@app.get("/api/edit/schema/{target}")
def edit_schema(target: str, request: Request) -> dict:
    """
    What is editable, and within what bounds — so the UI renders from the same
    schema the backend enforces rather than a second copy that can drift.
    """
    authz.require(request, roles_module.VIEW_DETAIL)
    try:
        return {
            "target": target,
            "fields": editschema.describe(target),
            "readOnly": list(charedit.PAL_READ_ONLY) if target == "pal" else [],
            "expBands": editschema.exp_bands(target),
            "maxLevel": editschema.MAX_LEVEL,
        }
    except editschema.SchemaError as e:
        raise HTTPException(404, str(e))


class PalEditRequest(BaseModel):
    changes: dict


def _find_pal_object(instance_id: str):
    """Locate one Pal in a fresh parse of Level.sav."""
    from savefiles import get_level_sav_path

    level_path = get_level_sav_path()
    if not level_path:
        raise HTTPException(503, "Level.sav not found")

    gvas = load_gvas(level_path)
    if gvas is None:
        raise HTTPException(503, "Could not parse Level.sav")

    for entry in charedit._character_entries(gvas):
        key = entry.get("key") if isinstance(entry, dict) else None
        if str((key or {}).get("InstanceId", {}).get("value") or "") == instance_id:
            obj = charedit._save_parameter(entry)
            if obj is None:
                raise HTTPException(404, "That character has no editable data")
            return obj
    raise HTTPException(404, f"No Pal with instance id {instance_id}")


@app.post("/api/edit/pal/{instance_id}/preview")
def preview_pal_edit(instance_id: str, req: PalEditRequest, request: Request) -> dict:
    """Dry-run a Pal edit. Read-only; returns the diff and a plan hash."""
    authz.require(request, roles_module.SAVE_EDIT_FULL)
    plan = charedit.plan_pal_edit(_find_pal_object(instance_id), req.changes)
    return {**plan, "instanceId": instance_id, "applied": False}


@app.post("/api/edit/pal/{instance_id}")
def edit_pal(
    instance_id: str, req: PalEditRequest, request: Request, planHash: str = Query(...)
) -> dict:
    """
    Apply a previewed Pal edit.

    `planHash` is mandatory for the same reason it is on imports: it is the diff
    the operator was shown, and a world that moved since then must not be
    written blind.
    """
    user = authz.require_user(request, roles_module.SAVE_EDIT_FULL)
    ip = authz.client_ip(request)

    def failed(message: str, status: int):
        audit.record(
            audit.SAVE_EDIT, username=user["username"], role=user["role"],
            target=f"pal:{instance_id}", detail=message, ip=ip, result=audit.RESULT_FAILED,
        )
        return HTTPException(status, message)

    try:
        result = charedit.apply_pal_edit(instance_id, req.changes, expected_plan_hash=planHash)
    except ServerRunningError as e:
        raise failed(str(e), 423)
    except charedit.EditError as e:
        raise failed(str(e), 409)
    except Exception as e:  # noqa: BLE001
        logger.exception("Pal edit failed")
        raise failed(f"Pal edit failed: {e}", 500)

    audit.record(
        audit.SAVE_EDIT, username=user["username"], role=user["role"],
        target=f"pal:{instance_id}",
        detail={"changes": result["changes"], "backupId": result["backupId"]},
        ip=ip,
    )
    return result


def _find_player_objects(uid: str):
    """
    (character object from Level.sav, SaveData from the player's own .sav).

    Both are needed because a player's editable fields are split across the two
    files — name/level/EXP in one, technology points in the other.
    """
    from savefiles import get_default_world_dir, get_level_sav_path

    world_dir = get_default_world_dir()
    level_path = get_level_sav_path(world_dir)
    if not level_path:
        raise HTTPException(503, "Level.sav not found")

    gvas = load_gvas(level_path)
    if gvas is None:
        raise HTTPException(503, "Could not parse Level.sav")

    key_uid = uid.replace("-", "").lower()
    char_obj = None
    for entry in charedit._character_entries(gvas):
        key = entry.get("key") if isinstance(entry, dict) else None
        entry_uid = str((key or {}).get("PlayerUId", {}).get("value") or "")
        if entry_uid.replace("-", "").lower() != key_uid:
            continue
        obj = charedit._save_parameter(entry)
        if obj is not None and obj.get("IsPlayer", {}).get("value") is True:
            char_obj = obj
            break
    if char_obj is None:
        raise HTTPException(404, f"No player character with uid {uid}")

    player_save = None
    player_path = get_player_sav_path(uid, world_dir)
    if player_path:
        player_gvas = load_gvas(player_path)
        if player_gvas is not None:
            player_save = (
                getattr(player_gvas, "properties", {}).get("SaveData", {}).get("value") or {}
            )
    return char_obj, player_save


@app.post("/api/edit/player/{uid}/preview")
def preview_player_edit(uid: str, req: PalEditRequest, request: Request) -> dict:
    """Dry-run a player edit. Read-only; says which files it would touch."""
    authz.require(request, roles_module.SAVE_EDIT_FULL)
    char_obj, player_save = _find_player_objects(uid)
    plan = charedit.plan_player_edit(char_obj, req.changes, player_save)
    return {**plan, "uid": uid, "applied": False}


@app.post("/api/edit/player/{uid}")
def edit_player(
    uid: str, req: PalEditRequest, request: Request, planHash: str = Query(...)
) -> dict:
    """
    Apply a previewed player edit.

    This one can write two files. They cannot be written atomically together, so
    both are verified afterwards and any mismatch rolls back the whole world.
    """
    user = authz.require_user(request, roles_module.SAVE_EDIT_FULL)
    ip = authz.client_ip(request)

    def failed(message: str, status: int):
        audit.record(
            audit.SAVE_EDIT, username=user["username"], role=user["role"],
            target=f"player:{uid}", detail=message, ip=ip, result=audit.RESULT_FAILED,
        )
        return HTTPException(status, message)

    try:
        result = charedit.apply_player_edit(uid, req.changes, expected_plan_hash=planHash)
    except ServerRunningError as e:
        raise failed(str(e), 423)
    except charedit.EditError as e:
        raise failed(str(e), 409)
    except Exception as e:  # noqa: BLE001
        logger.exception("Player edit failed")
        raise failed(f"Player edit failed: {e}", 500)

    audit.record(
        audit.SAVE_EDIT, username=user["username"], role=user["role"],
        target=f"player:{uid}",
        detail={
            "changes": result["changes"],
            "filesWritten": result["filesWritten"],
            "backupId": result["backupId"],
        },
        ip=ip,
    )
    return result


# ─── Bulk Pal editing ────────────────────────────────────


class BulkPalEditRequest(BaseModel):
    instanceIds: list[str]
    changes: dict
    # Move each Pal's EXP to match a new level. Without it, a bulk level change
    # is silently undone the next time the world loads.
    autoExp: bool = True


def _bulk_subjects(instance_ids: list[str], changes: dict, auto_exp: bool):
    """(subjects for the batch planner, the per-Pal edit map) from a request."""
    from savefiles import get_level_sav_path

    wanted = [i for i in dict.fromkeys(instance_ids or []) if i]
    if not wanted:
        raise HTTPException(400, "No Pals selected")

    level_path = get_level_sav_path()
    if not level_path:
        raise HTTPException(503, "Level.sav not found")
    gvas = load_gvas(level_path)
    if gvas is None:
        raise HTTPException(503, "Could not parse Level.sav")

    edits = charedit.spread_changes(wanted, changes, auto_exp=auto_exp)
    try:
        found = charedit._index_pals(gvas, set(wanted))
    except charedit.EditError as e:
        raise HTTPException(400, str(e))

    missing = [i for i in wanted if i not in found]
    if missing:
        raise HTTPException(
            404,
            f"{len(missing)} of the selected Pals are not in this world "
            f"(first: {missing[0]})",
        )
    return [(i, found[i], edits[i]) for i in wanted], edits


@app.post("/api/edit/pals/bulk/preview")
def preview_bulk_pal_edit(req: BulkPalEditRequest, request: Request) -> dict:
    """Dry-run one change set across many Pals. Read-only."""
    authz.require(request, roles_module.SAVE_EDIT_FULL)
    subjects, _ = _bulk_subjects(req.instanceIds, req.changes, req.autoExp)
    plan = charedit.plan_pal_batch(subjects)
    return {**plan, "autoExp": req.autoExp, "applied": False}


@app.post("/api/edit/pals/bulk")
def bulk_pal_edit(
    req: BulkPalEditRequest, request: Request, planHash: str = Query(...)
) -> dict:
    """
    Apply one change set across many Pals in a single guarded write.

    All-or-nothing: every Pal is validated before anything is written, and a
    verification failure on any one of them rolls the whole world back. A
    half-applied batch is worse than none, because nothing records where it
    stopped.
    """
    user = authz.require_user(request, roles_module.SAVE_EDIT_FULL)
    ip = authz.client_ip(request)
    edits = charedit.spread_changes(
        [i for i in dict.fromkeys(req.instanceIds or []) if i], req.changes, req.autoExp
    )

    def failed(message: str, status: int):
        audit.record(
            audit.SAVE_EDIT, username=user["username"], role=user["role"],
            target=f"pals:bulk({len(edits)})", detail=message, ip=ip,
            result=audit.RESULT_FAILED,
        )
        return HTTPException(status, message)

    try:
        result = charedit.apply_pal_batch(
            edits, label="bulk Pal edit", expected_plan_hash=planHash
        )
    except ServerRunningError as e:
        raise failed(str(e), 423)
    except charedit.EditError as e:
        raise failed(str(e), 409)
    except Exception as e:  # noqa: BLE001
        logger.exception("Bulk Pal edit failed")
        raise failed(f"Bulk Pal edit failed: {e}", 500)

    audit.record(
        audit.SAVE_EDIT, username=user["username"], role=user["role"],
        target=f"pals:bulk({result['palsChanged']})",
        detail={
            "changes": req.changes,
            "autoExp": req.autoExp,
            "palsChanged": result["palsChanged"],
            "fieldsChanged": result["fieldsChanged"],
            "backupId": result["backupId"],
        },
        ip=ip,
    )
    return result


# ─── Inventory slot editing ──────────────────────────────


class SlotEditRequest(BaseModel):
    # [{"slotIndex": 3, "itemId": "Wood", "stackCount": 50}]. An empty itemId or
    # a zero count clears the slot.
    patches: list[dict]


def _container_slots(container_id: str) -> list[dict]:
    """The container's slots from the current parse, or a 404."""
    data = savecache.get_data()
    containers = (data or {}).get("containers")
    containers = containers if isinstance(containers, dict) else {}
    slots = containers.get(container_id)
    if slots is None:
        raise HTTPException(
            404,
            "Container not found. Item containers are only decoded when the world "
            "has been parsed with items included.",
        )
    return slots


@app.post("/api/edit/container/{container_id}/slots/preview")
def preview_slot_edit(container_id: str, req: SlotEditRequest, request: Request) -> dict:
    """Dry-run a slot edit. Read-only; returns the diff and a plan hash."""
    authz.require(request, roles_module.SAVE_EDIT_FULL)
    try:
        plan = slotedit.plan_slot_edit(container_id, req.patches, _container_slots(container_id))
    except slotedit.SlotEditError as e:
        raise HTTPException(400, str(e))
    except saveimport.ImportError_ as e:
        raise HTTPException(409, str(e))
    return {**plan, "summary": slotedit.summarise(plan)}


@app.post("/api/edit/container/{container_id}/slots")
def apply_slot_edit(
    container_id: str, req: SlotEditRequest, request: Request, planHash: str = Query(...)
) -> dict:
    """
    Apply a previewed slot edit.

    Goes through the import write path, so the world is verified afterwards to
    contain exactly the planned change in this container and **nothing** in any
    other, or it rolls back.
    """
    user = authz.require_user(request, roles_module.SAVE_EDIT_FULL)
    ip = authz.client_ip(request)

    def failed(message: str, status: int):
        audit.record(
            audit.SAVE_EDIT, username=user["username"], role=user["role"],
            target=f"container:{container_id}", detail=message, ip=ip,
            result=audit.RESULT_FAILED,
        )
        return HTTPException(status, message)

    try:
        result = slotedit.apply_slot_edit(
            container_id, req.patches, _container_slots(container_id),
            expected_plan_hash=planHash,
        )
    except ServerRunningError as e:
        raise failed(str(e), 423)
    except slotedit.SlotEditError as e:
        raise failed(str(e), 400)
    except saveimport.ImportError_ as e:
        raise failed(str(e), 409)
    except Exception as e:  # noqa: BLE001
        logger.exception("Slot edit failed")
        raise failed(f"Slot edit failed: {e}", 500)

    audit.record(
        audit.SAVE_EDIT, username=user["username"], role=user["role"],
        target=f"container:{container_id}",
        detail={
            "slotsChanged": result["slotsChanged"],
            "itemsBefore": result["itemsBefore"],
            "itemsAfter": result["itemsAfter"],
            "backupId": result["backupId"],
        },
        ip=ip,
    )
    return result


class ItemCreateRequest(BaseModel):
    slotIndex: int
    itemId: str
    durability: Optional[float] = None
    # Eggs only: which species hatches. The egg ITEM does not decide it —
    # `PalEgg_Dark_03` covers 18 species on one world — so leaving this out means
    # inheriting whatever the copied record happened to hold, which the plan
    # reports as `hatchesFromTemplate`.
    hatches: Optional[str] = None


def _load_world_for_edit():
    """The live world, parsed with items. Read-only — planning never uses the cache."""
    from palsav.core import decompress_sav_to_gvas
    from palsav.gvas import GvasFile
    from palsav.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS
    from parser import _custom_properties
    from savefiles import get_level_sav_path, read_sav_bytes

    level_path = get_level_sav_path()
    if not level_path:
        raise HTTPException(503, "Level.sav not found")
    raw = read_sav_bytes(level_path)
    if raw is None:
        raise HTTPException(503, "Could not read Level.sav")
    props = {**PALWORLD_CUSTOM_PROPERTIES, **_custom_properties(include_items=True)}
    return GvasFile.read(decompress_sav_to_gvas(raw)[0], PALWORLD_TYPE_HINTS, props)


@app.post("/api/edit/container/{container_id}/create/preview")
def preview_item_create(
    container_id: str, req: ItemCreateRequest, request: Request
) -> dict:
    """
    Dry-run creating one piece of equipment or one egg. Read-only.

    Separate from the slot editor's preview because this is a different
    operation: the slot editor moves items that exist, and this one brings an
    item into the world that was never obtained in it.
    """
    authz.require(request, roles_module.SAVE_EDIT_FULL)
    return itemclone.plan_item_create(
        _load_world_for_edit(), container_id, req.slotIndex, req.itemId,
        durability=req.durability, hatches=req.hatches,
    )


@app.post("/api/edit/container/{container_id}/create")
def apply_item_create(
    container_id: str, req: ItemCreateRequest, request: Request,
    planHash: str = Query(...),
) -> dict:
    """Create the item. Audited as its own action — see `audit.ITEM_CREATE`."""
    user = authz.require_user(request, roles_module.SAVE_EDIT_FULL)
    ip = authz.client_ip(request)

    def failed(message: str, status: int):
        audit.record(
            audit.ITEM_CREATE, username=user["username"], role=user["role"],
            target=f"container:{container_id}", detail=message, ip=ip,
            result=audit.RESULT_FAILED,
        )
        return HTTPException(status, message)

    try:
        result = itemclone.apply_item_create(
            container_id, req.slotIndex, req.itemId,
            durability=req.durability, expected_plan_hash=planHash,
            hatches=req.hatches,
        )
    except ServerRunningError as e:
        raise failed(str(e), 423)
    except itemclone.ItemCloneError as e:
        raise failed(str(e), 400)
    except Exception as e:  # noqa: BLE001
        logger.exception("Item creation failed")
        raise failed(f"Item creation failed: {e}", 500)

    audit.record(
        audit.ITEM_CREATE, username=user["username"], role=user["role"],
        target=f"container:{container_id}",
        detail={
            "itemId": result["staticId"],
            "itemName": result["itemName"],
            "type": result["type"],
            "slotIndex": result["slotIndex"],
            "durability": result["durability"],
            # Already audited, and it matters more now that it is chosen rather
            # than inherited: for an egg the species IS the "what" in "who
            # spawned what".
            "hatchesInto": result["hatchesInto"],
            "backupId": result["backupId"],
        },
        ip=ip,
    )
    return result


# ─── Guild membership ────────────────────────────────────


class GuildMoveRequest(BaseModel):
    playerUid: str
    targetGuildId: str
    # Bring the origin guild's bases along when the move would otherwise leave
    # them in a guild with nobody in it. Off by default: it removes the emptied
    # guild, and that should be something the operator asked for rather than a
    # side effect they discover afterwards.
    transferBases: bool = False


@app.post("/api/edit/guild/move/preview")
def preview_guild_move(req: GuildMoveRequest, request: Request) -> dict:
    """
    Dry-run a guild move. Read-only; returns exactly what would change.

    Specific on purpose — character counts, base counts, whether the origin guild
    disappears and who inherits it. "Move this player" sounds like a one-field
    change and touches four structures plus, optionally, every base the origin
    guild owned.
    """
    authz.require(request, roles_module.SAVE_EDIT_FULL)
    try:
        return guildedit.plan_guild_move(
            req.playerUid, req.targetGuildId, req.transferBases
        )
    except guildedit.GuildEditError as e:
        raise HTTPException(400, str(e))


@app.post("/api/edit/guild/move")
def move_player_guild(
    req: GuildMoveRequest, request: Request, planHash: str = Query(...)
) -> dict:
    """
    Apply a previewed guild move. All of it, or none of it.

    `planHash` is required rather than optional: the preview is what the operator
    agreed to, and a world that moved since then is not the world they saw.
    """
    user = authz.require_user(request, roles_module.SAVE_EDIT_FULL)
    ip = authz.client_ip(request)

    def failed(message: str, status: int):
        audit.record(
            audit.GUILD_MOVE, username=user["username"], role=user["role"],
            target=f"player:{req.playerUid}", detail=message, ip=ip,
            result=audit.RESULT_FAILED,
        )
        return HTTPException(status, message)

    try:
        result = guildedit.apply_guild_move(
            req.playerUid, req.targetGuildId, req.transferBases, plan_hash=planHash,
        )
    except ServerRunningError as e:
        raise failed(str(e), 423)
    except guildedit.GuildEditError as e:
        raise failed(str(e), 409)
    except Exception as e:  # noqa: BLE001
        logger.exception("Guild move failed")
        raise failed(f"Guild move failed: {e}", 500)

    # The names are captured here, not looked up later, for the same reason
    # `moderate` captures a target's display name: guilds get renamed and
    # disbanded, and "who moved whom" has no answer afterwards otherwise.
    audit.record(
        audit.GUILD_MOVE, username=user["username"], role=user["role"],
        target=f"player:{result['playerName']} ({req.playerUid})",
        detail={
            "fromGuild": result["fromGuild"],
            "toGuild": result["toGuild"],
            "charactersMoved": result["charactersMoved"],
            "basesMoved": result["basesMoved"],
            "originGuildRemoved": result["originGuildRemoved"],
            "backupId": result["backupId"],
        },
        ip=ip,
    )
    return result


# ─── Pal duplication ─────────────────────────────────────


class CloneRequest(BaseModel):
    instanceId: str
    containerId: str
    count: int = 1
    # An optional edit applied to each clone, validated exactly like any other.
    changes: Optional[dict] = None


def _clone_gvas():
    """
    Level.sav parsed with character-container slots decoded.

    The clone needs `CharacterContainerSaveData.Slots`, which only the item
    property set decodes — without it there is nothing to append a slot to.
    """
    from savefiles import get_level_sav_path

    level_path = get_level_sav_path()
    if not level_path:
        raise HTTPException(503, "Level.sav not found")
    gvas = load_gvas(level_path, include_items=True)
    if gvas is None:
        raise HTTPException(503, "Could not parse Level.sav")
    return gvas


@app.get("/api/edit/pal-containers")
def list_pal_containers(request: Request) -> dict:
    """Character containers with capacity and free space — where a clone can go."""
    authz.require(request, roles_module.SAVE_EDIT_FULL)
    return {"containers": palclone.describe_containers(_clone_gvas())}


@app.post("/api/edit/pal/clone/preview")
def preview_clone(req: CloneRequest, request: Request) -> dict:
    """Dry-run a clone. Read-only; returns the plan and a hash."""
    authz.require(request, roles_module.SAVE_EDIT_FULL)
    return {
        **palclone.plan_clone(
            _clone_gvas(), req.instanceId, req.containerId, req.count, req.changes
        ),
        "applied": False,
    }


@app.post("/api/edit/pal/clone")
def clone_pal(req: CloneRequest, request: Request, planHash: str = Query(...)) -> dict:
    """
    Create clones of a Pal in a chosen container.

    The only operation here that *adds* records rather than overwriting fields,
    so its verification counts records: the character map and the target
    container must each grow by exactly `count`, every new id must resolve to its
    slot, and no other container may change length.
    """
    user = authz.require_user(request, roles_module.SAVE_EDIT_FULL)
    ip = authz.client_ip(request)

    def failed(message: str, status: int):
        audit.record(
            audit.SAVE_EDIT, username=user["username"], role=user["role"],
            target=f"clone:{req.instanceId}", detail=message, ip=ip,
            result=audit.RESULT_FAILED,
        )
        return HTTPException(status, message)

    try:
        result = palclone.apply_clone(
            req.instanceId, req.containerId, req.count, req.changes,
            expected_plan_hash=planHash,
        )
    except ServerRunningError as e:
        raise failed(str(e), 423)
    except palclone.CloneError as e:
        raise failed(str(e), 409)
    except Exception as e:  # noqa: BLE001
        logger.exception("Pal clone failed")
        raise failed(f"Pal clone failed: {e}", 500)

    audit.record(
        audit.SAVE_EDIT, username=user["username"], role=user["role"],
        target=f"clone:{req.instanceId}",
        detail={
            "containerId": result["containerId"],
            "count": result["count"],
            "newInstanceIds": result["newInstanceIds"],
            "backupId": result["backupId"],
        },
        ip=ip,
    )
    return result


# ─── Illegal-Pal detection and repair ────────────────────


class RepairRequest(BaseModel):
    # Omitted or empty means every repairable Pal the scan found.
    instanceIds: Optional[list[str]] = None


@app.get("/api/palcheck/scan")
def palcheck_scan(request: Request) -> dict:
    """
    Every Pal whose stats are outside what the game can produce. Read-only.

    A view, not an edit — it is how an admin finds out whether anyone has been
    cheating, so it sits behind VIEW_DETAIL rather than the editing capability.
    """
    authz.require(request, roles_module.VIEW_DETAIL)
    try:
        return palcheck.scan_current()
    except charedit.EditError as e:
        raise HTTPException(503, str(e))


@app.post("/api/palcheck/repair/preview")
def preview_palcheck_repair(req: RepairRequest, request: Request) -> dict:
    """
    Dry-run a repair. Read-only.

    The scan is re-run server-side; the caller chooses *which* Pals, never what
    their new values are.
    """
    authz.require(request, roles_module.SAVE_EDIT_FULL)
    try:
        return palcheck.preview_repair(req.instanceIds)
    except charedit.EditError as e:
        raise HTTPException(409, str(e))


@app.post("/api/palcheck/repair")
def palcheck_repair(
    req: RepairRequest, request: Request, planHash: str = Query(...)
) -> dict:
    """
    Clamp out-of-range Pal stats back into legal range.

    This makes Pals weaker, deliberately, which is why it never runs on its own.
    Issues that cannot be fixed by writing a scalar — passive skill lists,
    unknown species — are reported back untouched rather than counted as fixed.
    """
    user = authz.require_user(request, roles_module.SAVE_EDIT_FULL)
    ip = authz.client_ip(request)

    def failed(message: str, status: int):
        audit.record(
            audit.SAVE_EDIT, username=user["username"], role=user["role"],
            target="palcheck:repair", detail=message, ip=ip, result=audit.RESULT_FAILED,
        )
        return HTTPException(status, message)

    try:
        result = palcheck.apply_repair(req.instanceIds, expected_plan_hash=planHash)
    except ServerRunningError as e:
        raise failed(str(e), 423)
    except charedit.EditError as e:
        raise failed(str(e), 409)
    except Exception as e:  # noqa: BLE001
        logger.exception("Pal repair failed")
        raise failed(f"Pal repair failed: {e}", 500)

    audit.record(
        audit.SAVE_EDIT, username=user["username"], role=user["role"],
        target=f"palcheck:repair({result['palsChanged']})",
        detail={
            "palsChanged": result["palsChanged"],
            "fieldsChanged": result["fieldsChanged"],
            "palsWithUnfixableIssues": result["palsWithUnfixableIssues"],
            "backupId": result["backupId"],
        },
        ip=ip,
    )
    return result


# ─── Metrics history (Phase 8) ───────────────────────────


@app.get("/api/metrics/history")
def get_metrics_history(
    request: Request,
    hours: int = Query(24, ge=1, le=24 * 365),
    buckets: int = Query(120, ge=1, le=1000),
) -> dict[str, Any]:
    """
    Bucketed server history: FPS, frame time, players, CPU, memory, disk, world size.

    A gap in the data is a period the server did not answer, and is reported as
    such — `reachable` is the *fraction* of each bucket the game responded in, so
    an intermittently crashing server looks different from a cleanly stopped one.
    """
    authz.require(request, roles_module.VIEW_BASIC)
    return metrics.series(hours=hours, buckets=buckets)


@app.get("/api/metrics/summary")
def get_metrics_summary(request: Request) -> dict[str, Any]:
    """How much history exists, and what fraction of it the server was up for."""
    authz.require(request, roles_module.VIEW_BASIC)
    return metrics.summary()


# ─── Moderation (Phase 8) ────────────────────────────────


class AnnounceRequest(BaseModel):
    message: str


class ModerateRequest(BaseModel):
    userid: str
    reason: str = ""


def _moderator(request: Request) -> dict:
    return authz.require_user(request, roles_module.PLAYERS_MODERATE)


def _moderation(request: Request, call) -> dict:
    """
    Run a moderation command, mapping its failures to status codes.

    The audit record is written inside `moderate`, not here, so that a command
    which fails on the way to the game server is still recorded. Auditing at the
    endpoint would only capture what the endpoint managed to attempt.
    """
    try:
        return call()
    except moderate.ModerationError as e:
        raise HTTPException(502 if "reach" in str(e).lower() else 400, str(e))


@app.post("/api/moderate/announce")
def post_announce(req: AnnounceRequest, request: Request) -> dict:
    """Broadcast a message to everyone on the server."""
    user = _moderator(request)
    ip = authz.client_ip(request)
    return _moderation(request, lambda: moderate.announce(req.message, actor=user, ip=ip))


@app.post("/api/moderate/kick")
def post_kick(req: ModerateRequest, request: Request) -> dict:
    """Disconnect a player. They can rejoin immediately."""
    user = _moderator(request)
    ip = authz.client_ip(request)
    return _moderation(
        request, lambda: moderate.kick(req.userid, req.reason, actor=user, ip=ip)
    )


@app.post("/api/moderate/ban")
def post_ban(req: ModerateRequest, request: Request) -> dict:
    """Ban a player. The game owns the ban list; nothing is mirrored here."""
    user = _moderator(request)
    ip = authz.client_ip(request)
    return _moderation(
        request, lambda: moderate.ban(req.userid, req.reason, actor=user, ip=ip)
    )


@app.post("/api/moderate/unban")
def post_unban(req: ModerateRequest, request: Request) -> dict:
    user = _moderator(request)
    ip = authz.client_ip(request)
    return _moderation(request, lambda: moderate.unban(req.userid, actor=user, ip=ip))


@app.get("/api/moderate/bans")
def get_bans(request: Request) -> dict:
    """
    The server's own ban list, read from its file.

    Deliberately not mirrored into SQLite: a local copy drifts the moment someone
    edits the server's file by hand, and a ban list that disagrees with the game's
    is worse than none.
    """
    authz.require(request, roles_module.PLAYERS_MODERATE)
    return moderate.list_bans()


# ─── Recurring announcements ─────────────────────────────


class AnnouncementCreate(BaseModel):
    message: str
    interval: str = "hourly"
    enabled: bool = True
    onlyWhenOnline: bool = True


class AnnouncementUpdate(BaseModel):
    message: Optional[str] = None
    interval: Optional[str] = None
    enabled: Optional[bool] = None
    onlyWhenOnline: Optional[bool] = None


@app.get("/api/announcements")
def list_announcements(request: Request) -> dict[str, Any]:
    """
    The recurring-announcement schedule.

    Behind PLAYERS_MODERATE rather than VIEW_BASIC because the message list is
    the same content the broadcast capability covers — someone who cannot send an
    announcement has no reason to read the queue of them.
    """
    authz.require(request, roles_module.PLAYERS_MODERATE)
    return {
        "announcements": announcements.list_announcements(),
        "intervals": announcements.describe_intervals(),
        "max": announcements.MAX_ANNOUNCEMENTS,
    }


@app.post("/api/announcements")
def create_announcement(req: AnnouncementCreate, request: Request) -> dict[str, Any]:
    user = _moderator(request)
    try:
        entry = announcements.create(
            req.message, req.interval, enabled=req.enabled,
            only_when_online=req.onlyWhenOnline, created_by=user["username"],
        )
    except (ValueError, moderate.ModerationError) as e:
        raise HTTPException(400, str(e))
    announcements.record_change(
        {"action": "created", "message": entry["message"], "interval": entry["interval"]},
        actor=user, ip=authz.client_ip(request),
    )
    return entry


@app.patch("/api/announcements/{announcement_id}")
def update_announcement(
    announcement_id: int, req: AnnouncementUpdate, request: Request
) -> dict[str, Any]:
    user = _moderator(request)
    try:
        entry = announcements.update(
            announcement_id, message=req.message, interval=req.interval,
            enabled=req.enabled, only_when_online=req.onlyWhenOnline,
        )
    except (ValueError, moderate.ModerationError) as e:
        raise HTTPException(400, str(e))
    announcements.record_change(
        {"action": "updated", "id": announcement_id,
         "message": entry["message"], "interval": entry["interval"],
         "enabled": entry["enabled"]},
        actor=user, ip=authz.client_ip(request),
    )
    return entry


@app.delete("/api/announcements/{announcement_id}")
def delete_announcement(announcement_id: int, request: Request) -> dict[str, Any]:
    user = _moderator(request)
    existing = announcements.get(announcement_id)
    if existing is None:
        raise HTTPException(404, f"No such announcement: {announcement_id}")
    announcements.delete(announcement_id)
    # The message is recorded on delete as well as on create: otherwise the audit
    # log says something was removed without saying what the server had been
    # telling players for the last month.
    announcements.record_change(
        {"action": "deleted", "id": announcement_id, "message": existing["message"]},
        actor=user, ip=authz.client_ip(request),
    )
    return {"ok": True, "id": announcement_id}


@app.post("/api/announcements/{announcement_id}/send")
def send_announcement_now(announcement_id: int, request: Request) -> dict[str, Any]:
    """
    Send one now, attributed to whoever pressed the button.

    Resets its interval, so testing a message does not mean the scheduled copy
    arrives seconds afterwards.
    """
    user = _moderator(request)
    try:
        return announcements.send_now(
            announcement_id, actor=user, ip=authz.client_ip(request)
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    except moderate.ModerationError as e:
        raise HTTPException(502 if "reach" in str(e).lower() else 400, str(e))


class ShutdownRequest(BaseModel):
    seconds: int = 60
    message: str = "Server shutting down"


@app.post("/api/server/shutdown")
def shutdown_server(req: ShutdownRequest, request: Request) -> dict:
    """
    Announce a countdown and stop the game process.

    Stops the *process*, not the container — the game cannot start itself again,
    which is why `lifecycle.note_shutdown` starts watching for its return and the
    UI can say "it has not come back" instead of leaving you guessing.

    This is the one server action that works with no container control at all, so
    it is the useful half of stop/start on a default install.
    """
    user = authz.require_user(request, roles_module.SERVER_CONTROL)
    ip = authz.client_ip(request)
    seconds = max(0, min(int(req.seconds), 3600))
    message = moderate.clean_message(req.message)

    try:
        result = gameapi.shutdown(seconds, message)
    except gameapi.GameApiError as e:
        audit.record(
            audit.SERVER_STOP, username=user["username"], role=user["role"],
            detail={"error": str(e), "seconds": seconds}, ip=ip,
            result=audit.RESULT_FAILED,
        )
        raise HTTPException(502, str(e))

    lifecycle.note_shutdown(f"{user['username']}: {message}" if message else user["username"])
    audit.record(
        audit.SERVER_STOP, username=user["username"], role=user["role"],
        detail={"seconds": seconds, "message": message}, ip=ip,
    )
    return {"ok": True, "response": result, "lifecycle": lifecycle.status()}


@app.post("/api/server/force-stop")
def force_stop_server(request: Request) -> dict:
    """
    Stop the game process immediately, with no countdown.

    Loses everything since the last autosave, which is why it is a separate route
    from `shutdown` rather than `shutdown` with `seconds=0` — the two deserve
    different confirmations and different audit entries.
    """
    user = authz.require_user(request, roles_module.SERVER_CONTROL)
    ip = authz.client_ip(request)

    try:
        result = gameapi.stop()
    except gameapi.GameApiError as e:
        audit.record(
            audit.SERVER_STOP, username=user["username"], role=user["role"],
            target="force-stop", detail={"error": str(e)}, ip=ip,
            result=audit.RESULT_FAILED,
        )
        raise HTTPException(502, str(e))

    lifecycle.note_shutdown(f"{user['username']}: force stop")
    audit.record(
        audit.SERVER_STOP, username=user["username"], role=user["role"],
        target="force-stop", detail={"warning": "no countdown"}, ip=ip,
    )
    return {"ok": True, "response": result, "lifecycle": lifecycle.status()}


@app.post("/api/server/save")
def force_save(request: Request) -> dict:
    """
    Ask the game to write the world to disk now.

    Sits under `server.control` rather than moderation: it is an operations
    action, and it is the one command here that touches the save files.
    """
    user = authz.require_user(request, roles_module.SERVER_CONTROL)
    ip = authz.client_ip(request)
    try:
        result = gameapi.save()
    except gameapi.GameApiError as e:
        audit.record(
            audit.SERVER_SAVE, username=user["username"], role=user["role"],
            detail=str(e), ip=ip, result=audit.RESULT_FAILED,
        )
        raise HTTPException(502, str(e))

    audit.record(
        audit.SERVER_SAVE, username=user["username"], role=user["role"], ip=ip,
    )
    return {"ok": True, "response": result}


# ─── Pal import ──────────────────────────────────────────


class PalImportRequest(BaseModel):
    # A saveexport envelope of kind 'pal' or 'player' — the same file the export
    # endpoint produces, unmodified.
    document: dict
    mode: str = "overwrite"
    # overwrite: optional, forces every Pal in the document onto one target.
    instanceId: str = ""
    # create: required destination.
    containerId: str = ""
    # create: the template chosen at preview time. See palimport.apply_import.
    templateInstanceId: str = ""


@app.post("/api/edit/pal/import/preview")
def preview_pal_import(req: PalImportRequest, request: Request) -> dict:
    """
    Dry-run a Pal import. Read-only.

    The response carries `ignored`: the fields the document contains that this
    build will not write, each with a reason. An export says more than an import
    may write — ownership, container and slot describe where a Pal *is* — and
    dropping those silently would let someone believe a Pal changed hands.
    """
    authz.require(request, roles_module.SAVE_EDIT_FULL)
    try:
        return {
            **palimport.plan_import(
                _clone_gvas(), req.document, req.mode,
                instance_id=req.instanceId, container_id=req.containerId,
            ),
            "applied": False,
        }
    except palimport.PalImportRefused as e:
        raise HTTPException(422, str(e))
    except palimport.PalImportError as e:
        raise HTTPException(400, str(e))


@app.post("/api/edit/pal/import")
def import_pal(req: PalImportRequest, request: Request, planHash: str = Query(...)) -> dict:
    """
    Apply a Pal import.

    Writes nothing itself: `overwrite` goes to the batch Pal editor and `create`
    to the cloner, both of which re-plan against the live world inside the write
    guard and refuse a stale plan. This endpoint's job is the audit record and the
    error mapping.
    """
    user = authz.require_user(request, roles_module.SAVE_EDIT_FULL)
    ip = authz.client_ip(request)

    def failed(message: str, status: int):
        audit.record(
            audit.SAVE_EDIT, username=user["username"], role=user["role"],
            target=f"palimport:{req.mode}", detail=message, ip=ip,
            result=audit.RESULT_FAILED,
        )
        return HTTPException(status, message)

    try:
        result = palimport.apply_import(
            req.document, req.mode,
            instance_id=req.instanceId, container_id=req.containerId,
            template_instance_id=req.templateInstanceId,
            expected_plan_hash=planHash,
        )
    except ServerRunningError as e:
        raise failed(str(e), 423)
    except palimport.PalImportRefused as e:
        raise failed(str(e), 422)
    except palimport.PalImportError as e:
        raise failed(str(e), 400)
    except (charedit.EditError, palclone.CloneError) as e:
        raise failed(str(e), 409)
    except Exception as e:  # noqa: BLE001
        logger.exception("Pal import failed")
        raise failed(f"Pal import failed: {e}", 500)

    audit.record(
        audit.SAVE_EDIT, username=user["username"], role=user["role"],
        target=f"palimport:{req.mode}",
        detail={
            "mode": result["mode"],
            "ignoredFields": sorted({i["field"] for i in result.get("ignored") or [] if i.get("field")}),
            "backupId": result.get("backupId"),
            **({"newInstanceIds": result["newInstanceIds"]} if "newInstanceIds" in result
               else {"palsChanged": result.get("palsChanged")}),
        },
        ip=ip,
    )
    return result


# ─── Entry point ─────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("BACKEND_HOST", "127.0.0.1")
    port = int(os.environ.get("BACKEND_PORT", "8400"))
    logger.info("Starting Palworld save backend on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")
