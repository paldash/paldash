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
import breeding
import charedit
import db
import editschema
import gameapi
import gameversion
import gamedata
import lifecycle
import metrics
import mods
import moderate
import palcheck
import palclone
import palimport
import policy as policy_module
import privacy
import reports
import roles as roles_module
import savecache
import saveedit
import saveexport
import saveimport
import slotedit
import teleport
import soloexport
import schedule as schedule_module
import settings_ini
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
    return baseprivacy.filter_bases(
        savecache.get_section("bases"), _hidden_base_ids(request)
    )


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
    guilds = savecache.get_section("guilds")

    foreign = _foreign_guild_ids(request)
    if foreign:
        guilds = [g for g in guilds if str(g.get("id") or "") not in foreign]

    hidden = privacy.hidden_uids(*_viewer(request))
    if not hidden["players"] and not hidden["guilds"]:
        return guilds

    out = []
    for guild in guilds:
        members = guild.get("members") or []
        if any(privacy.normalise_uid(m.get("uid")) in hidden["guilds"] for m in members):
            continue          # guild-wide privacy: the whole guild is concealed
        out.append({
            **guild,
            "members": privacy.filter_players(members, hidden["players"]),
        })
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

    effective = _breeding_owner(request, owner)
    if effective:
        # Normalise BOTH sides. `_own_identity` returns a dash-stripped uid and
        # the save stores a dashed one, so comparing them raw matches nothing —
        # and matches nothing *silently*, which is how a scoped view becomes an
        # empty view that looks like "you own no Pals". `_pals_for` already does
        # this; the inline filter here did not.
        key = effective.replace("-", "").lower()
        pals = [
            p for p in pals
            if (p.get("ownerUid") or "").replace("-", "").lower().startswith(key)
        ]
    return pals


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
    summaries = baseprivacy.filter_storage(
        savecache.get_section("baseStorage"), _hidden_base_ids(request)
    )
    own = _own_guild_base_ids(request)
    if own is None:
        return summaries
    return [s for s in summaries if str(s.get("baseId") or "") in own]


@app.get("/api/bases/{base_id}/storage")
def get_one_base_storage(base_id: str, request: Request) -> dict:
    own = _own_guild_base_ids(request)
    if base_id in _hidden_base_ids(request) or (own is not None and base_id not in own):
        # 404 rather than 403: "you may not see this base" confirms the base
        # exists, which is the one thing a hidden base is not supposed to say.
        raise HTTPException(404, f"No base {base_id}, or the world has not been parsed yet")
    for summary in savecache.get_section("baseStorage"):
        if summary["baseId"] == base_id:
            return summary
    raise HTTPException(404, f"No base {base_id}, or the world has not been parsed yet")


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


def _may_see_undiscovered(request: Request) -> bool:
    role, _ = _viewer(request)
    return policy_module.may_see_undiscovered(
        role,
        policy_module.load_policy().get(
            "discoveryVisibility", policy_module.DEFAULT_DISCOVERY
        ),
    )


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
        **gamedata.reload(),
    }
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
    return worldobjects.query(
        category=category,
        min_x=minX, min_y=minY, max_x=maxX, max_y=maxY,
        kinds=[k for k in kinds.split(",") if k],
        allowed=allowed,
        limit=limit,
    )


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
    visibility = policy_module.load_policy().get(
        "discoveryVisibility", policy_module.DEFAULT_DISCOVERY
    )
    may_see_undiscovered = policy_module.may_see_undiscovered(user["role"], visibility)

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

    def mark(entries: list[dict], key_field: str, found: set[str]) -> list[dict]:
        out = []
        for entry in entries:
            discovered = str(entry.get(key_field, "")).upper() in found
            if not discovered and not may_see_undiscovered:
                continue
            out.append({**entry, "discovered": discovered})
        return out

    try:
        travel = mark(gamedata.fast_travel_points(), "key", found_travel)
        effigies = mark(gamedata.effigies(), "guid", found_effigies)
    except gamedata.GameDataUnavailable as e:
        raise HTTPException(503, str(e))

    return {
        "scope": uid or ("all" if can_see_others else "self"),
        "linkedToPlayer": bool(own_uid) or can_see_others,
        "discoveryVisibility": visibility,
        "showsUndiscovered": may_see_undiscovered,
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
    return {
        "containerId": container_id,
        "slots": slots,
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

    guilds = {
        str(guild.get("id") or "")
        for guild in savecache.get_section("guilds")
        if uid in {
            privacy.normalise_uid(m.get("uid"))
            for m in (guild.get("members") or [])
            if m.get("uid")
        }
    }
    return uid, guilds


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


def _pals_for(owner: Optional[str]) -> list[dict]:
    pals = savecache.get_section("pals")
    if not owner:
        return pals
    key = owner.replace("-", "").lower()
    return [p for p in pals if (p.get("ownerUid") or "").replace("-", "").lower().startswith(key)]


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


@app.get("/api/breeding/palbox")
def breeding_palbox(request: Request, owner: Optional[str] = None) -> dict:
    try:
        return breeding.summarize_palbox(_pals_for(_breeding_owner(request, owner)))
    except breeding.BreedingDataError as e:
        raise HTTPException(503, str(e))


@app.get("/api/breeding/offspring")
def breeding_offspring(request: Request, owner: Optional[str] = None) -> list[dict]:
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
    try:
        pals = _pals_for(_breeding_owner(request, owner))
        summary = breeding.summarize_palbox(pals)
        owned = [s["internalName"] for s in summary["species"]]
        return breeding.indirect_targets(owned, genders=breeding.gender_pool(pals))
    except breeding.BreedingDataError as e:
        raise HTTPException(503, str(e))


@app.get("/api/breeding/paths")
def breeding_path(request: Request, target: str, owner: Optional[str] = None) -> dict:
    """
    A route to one target, using only pairs the owner can actually make.

    Gender is enforced (see `breeding._expand`): scoping the planner to one
    player's palbox made single-gender species common, and a plan whose second
    step needs two males is not a plan.
    """
    try:
        pals = _pals_for(_breeding_owner(request, owner))
        summary = breeding.summarize_palbox(pals)
        owned = [s["internalName"] for s in summary["species"]]
        return breeding.breeding_paths(
            target, owned, genders=breeding.gender_pool(pals)
        )
    except breeding.BreedingDataError as e:
        raise HTTPException(503, str(e))


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
    return {
        **data,
        "presets": settings_ini.PRESETS,
        "groups": settings_ini.HIGHLIGHT_GROUPS,
        "serverRunning": get_server_state().running,
        # Nothing in this file is hot-swappable: the server reads it at boot only.
        "restartRequiredForAll": True,
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
    authz.require_user(request, roles_module.BACKUP_MANAGE)
    renamed = backup_module.rename_backup(backup_id, req.description)
    if not renamed:
        raise HTTPException(404, f"Backup {backup_id} not found")
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


def _run_sort(mode: str, merge: bool, request: Request, base_id: Optional[str] = None) -> dict:
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
        result = saveedit.sort_containers(mode=mode, merge=merge, base_id=base_id)
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

    Pass `baseId` to scope the sort to one base's storage.
    """
    return _run_sort("stackables", req.merge, request, req.baseId)


@app.post("/api/edit/sort/all")
def sort_all(req: SortRequest, request: Request) -> dict:
    """Tidy containers including equipment, carrying dynamic_id links along."""
    return _run_sort("all", req.merge, request, req.baseId)


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
