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
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel

import accounts
import audit
import authz
import breeding
import charedit
import db
import editschema
import gamedata
import lifecycle
import policy as policy_module
import reports
import roles as roles_module
import savecache
import saveedit
import saveexport
import saveimport
import schedule as schedule_module
import settings_ini
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

app = FastAPI(title="Palworld Save Backend", version="3.0.0")

# Ceiling on anything the dashboard accepts as an upload (audit S9). A world
# export of a large server is a few MB; 64 is generous. The point is that an
# unbounded read into memory is a denial-of-service against the machine running
# the game server, which is the thing this dashboard exists not to disturb.
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "64")) * 1024 * 1024


@app.on_event("startup")
def _startup() -> None:
    """Prepare storage and make sure somebody can actually sign in."""
    db.init()
    schedule_module.init()
    accounts.purge_expired()
    schedule_module.start()
    created = accounts.bootstrap_from_env()
    if created:
        audit.record(
            audit.USER_CREATE,
            username="system", role="owner", target=created,
            detail="bootstrapped first Owner from PANEL_PASSWORD",
        )


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
def get_roles() -> list[dict]:
    """Role presets and what each grants."""
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
def note_shutdown(req: ShutdownNote) -> dict[str, Any]:
    """
    Told by the UI that a shutdown was just issued through the game's REST API.

    Starts watching for the server to come back, so we can tell the difference
    between "the container restarted it" and "the game process is gone and
    nothing is going to bring it back".
    """
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
def get_policy() -> dict[str, Any]:
    """Current security level and guest visibility toggles."""
    return policy_module.describe()


class PolicyUpdate(BaseModel):
    securityLevel: Optional[str] = None
    guestVisibility: Optional[dict[str, bool]] = None


@app.post("/api/policy")
def update_policy(req: PolicyUpdate, request: Request) -> dict[str, Any]:
    user = authz.require_user(request, roles_module.POLICY_MANAGE)
    changes = req.model_dump(exclude_none=True)
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
def refresh(force: bool = Query(True)) -> dict[str, Any]:
    """Ask for a re-parse. Returns immediately; parsing runs in the background."""
    return savecache.request_parse(force=force)


# ─── Save data (read-only) ───────────────────────────────


@app.get("/api/bases")
def get_bases() -> list[dict]:
    return savecache.get_section("bases")


@app.get("/api/guilds")
def get_guilds() -> list[dict]:
    return savecache.get_section("guilds")


@app.get("/api/pals")
def get_pals(owner: Optional[str] = None) -> list[dict]:
    pals = savecache.get_section("pals")
    if owner:
        key = owner.lower()
        pals = [p for p in pals if (p.get("ownerUid") or "").lower().startswith(key)]

    enriched = []
    for pal in pals:
        details = gamedata.describe_pal(pal.get("speciesId") or "")
        enriched.append(
            {
                **pal,
                "speciesName": details["name"],
                "icon": details["icon"],
                "elements": details["elements"],
                "paldeckNumber": details["paldeckNumber"],
                "passiveSkillNames": [
                    gamedata.passive_name(p) for p in (pal.get("passiveSkills") or [])
                ],
            }
        )
    return enriched


@app.get("/api/bases/storage")
def get_base_storage() -> list[dict]:
    """
    Per-base storage: containers owned, slots used, and what is in them.

    Computed during the parse (see parse_worker) rather than per request — the
    join is over every placed object in the world and has no business running on
    the request path.
    """
    return savecache.get_section("baseStorage")


@app.get("/api/bases/{base_id}/storage")
def get_one_base_storage(base_id: str) -> dict:
    for summary in savecache.get_section("baseStorage"):
        if summary["baseId"] == base_id:
            return summary
    raise HTTPException(404, f"No base {base_id}, or the world has not been parsed yet")


@app.get("/api/mapobjects")
def get_map_objects(category: Optional[str] = None) -> list[dict]:
    """Placed world objects with coordinates: chests, palboxes, farms, benches."""
    objects = savecache.get_section("mapObjects")
    if category:
        objects = [o for o in objects if o.get("category") == category]
    return [
        {**o, "name": gamedata.structure_name(o.get("objectId") or "")}
        for o in objects
    ]


# ─── Static world data (bundled, not from the save) ──────


@app.get("/api/world/fasttravel")
def get_fast_travel_points() -> dict[str, Any]:
    """
    All fast-travel points, with world coordinates and in-game names.

    These are static level actors, so they appear nowhere in a save file — only
    a player's *unlocked* list does. The coordinates share the save's world
    space, so they drop straight onto the existing map transform.
    """
    try:
        return {"points": gamedata.fast_travel_points()}
    except gamedata.GameDataUnavailable as e:
        raise HTTPException(503, str(e))


@app.get("/api/world/reference")
def get_reference_data() -> dict[str, Any]:
    """Exact Palworld 1.0 totals, computed from the game's own data tables."""
    try:
        return {
            "totals": gamedata.totals(),
            "workSuitability": gamedata.work_suitabilities(),
        }
    except gamedata.GameDataUnavailable as e:
        raise HTTPException(503, str(e))


@app.get("/api/items")
def get_item_totals(limit: int = Query(500, ge=1, le=5000)) -> dict[str, Any]:
    """
    Every item on the server, totalled across all containers — the equivalent of
    standing at an item retrieval unit and asking what exists.

    Names are resolved at request time rather than baked into the parse cache, so
    refreshing the bundled game data does not require re-parsing the world.
    """
    data = savecache.get_data() or {}
    items = data.get("items") or []
    containers = data.get("containers") or {}

    enriched = []
    for entry in items[:limit]:
        details = gamedata.describe_item(entry.get("itemId") or "")
        details.pop("id", None)  # `itemId` is the canonical key here
        enriched.append({**entry, **details})

    return {
        "items": enriched,
        "itemTypes": len(items),
        "totalCount": sum(i["count"] for i in items),
        "containersScanned": len(containers),
        "truncated": len(items) > limit,
        "namesResolved": gamedata.available(),
    }


@app.get("/api/players")
def get_players() -> list[dict]:
    """
    Players from Level.sav, enriched with their own .sav where available.

    Player .sav files are ~100KB, so reading them is cheap enough to do inline;
    only Level.sav goes through the background worker.
    """
    players = savecache.get_section("players")
    enriched = []

    for player in players:
        entry = dict(player)
        uid = (player.get("uid") or "").replace("-", "")
        path = get_player_sav_path(uid) if uid else None
        if path:
            gvas = load_gvas(path)
            if gvas:
                try:
                    entry.update(extract_player_save(gvas, uid))
                    entry["progress"] = extract_player_progress(gvas)
                except Exception as e:  # noqa: BLE001
                    logger.warning("Player save extract failed for %s: %s", uid, e)
        enriched.append(entry)

    return enriched


@app.get("/api/progress")
def get_progress() -> dict[str, Any]:
    """
    Per-player progression, with "how much is left" for each category.

    The denominators are the union of what every player on this server has
    found, because the save records only obtained entries — so they are a floor,
    not the game's true totals. Anything nobody has discovered yet is invisible
    to us, and the numbers rise as people explore.
    """
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
def get_player(uid: str) -> dict:
    for player in get_players():
        if (player.get("uid") or "").replace("-", "").lower() == uid.replace("-", "").lower():
            return player
    raise HTTPException(404, f"Player {uid} not found")


@app.get("/api/inventory/{container_id}")
def get_inventory(container_id: str) -> dict:
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


def _pals_for(owner: Optional[str]) -> list[dict]:
    pals = savecache.get_section("pals")
    if not owner:
        return pals
    key = owner.replace("-", "").lower()
    return [p for p in pals if (p.get("ownerUid") or "").replace("-", "").lower().startswith(key)]


@app.get("/api/breeding/palbox")
def breeding_palbox(owner: Optional[str] = None) -> dict:
    try:
        return breeding.summarize_palbox(_pals_for(owner))
    except breeding.BreedingDataError as e:
        raise HTTPException(503, str(e))


@app.get("/api/breeding/offspring")
def breeding_offspring(owner: Optional[str] = None) -> list[dict]:
    try:
        return breeding.possible_offspring(_pals_for(owner))
    except breeding.BreedingDataError as e:
        raise HTTPException(503, str(e))


@app.get("/api/breeding/paths")
def breeding_path(target: str, owner: Optional[str] = None) -> dict:
    try:
        summary = breeding.summarize_palbox(_pals_for(owner))
        owned = [s["internalName"] for s in summary["species"]]
        return breeding.breeding_paths(target, owned)
    except breeding.BreedingDataError as e:
        raise HTTPException(503, str(e))


@app.get("/api/breeding/pals")
def breeding_all_pals() -> list[dict]:
    try:
        return breeding.all_pals()
    except breeding.BreedingDataError as e:
        raise HTTPException(503, str(e))


@app.get("/api/breeding/odds")
def breeding_odds() -> dict:
    try:
        return breeding.inheritance_odds()
    except breeding.BreedingDataError as e:
        raise HTTPException(503, str(e))


@app.get("/api/breeding/predict")
def breeding_predict(a: str, b: str) -> dict:
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
def read_settings() -> dict:
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
def list_reports() -> dict:
    """What can be exported, and in which formats."""
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


@app.get("/api/export/{kind}")
def export_save(kind: str, request: Request, id: Optional[str] = None):
    """
    Export world / player / guild / base / container as a verifiable JSON document.

    Read-only. Gated on VIEW_DETAIL and audited, because an export is the whole
    inventory (and real Steam IDs) in one file.
    """
    from fastapi.responses import Response

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


class EditRequest(BaseModel):
    targetType: str
    targetId: str
    changes: dict


@app.post("/api/edit")
def edit_save(req: EditRequest) -> dict:
    """
    General-purpose editing (player stats, Pals, arbitrary slots).

    Still unimplemented. The write path is proven — sorting uses it and verifies
    byte-level conservation — but a general editor has far more ways to produce
    a world the game refuses to load, so it stays off until each field is
    validated individually.
    """
    try:
        assert_writable()
    except ServerRunningError as e:
        raise HTTPException(423, str(e))

    raise HTTPException(
        501,
        "The general save editor is not implemented yet. Container sorting is "
        "available at /api/edit/sort/stackables and /api/edit/sort/all.",
    )


# ─── Entry point ─────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("BACKEND_HOST", "127.0.0.1")
    port = int(os.environ.get("BACKEND_PORT", "8400"))
    logger.info("Starting Palworld save backend on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")
