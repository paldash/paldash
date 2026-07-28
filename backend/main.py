"""
Palworld Dashboard — save-file backend.

Binds to loopback by default. It has NO authentication of its own: the Next.js
layer in front of it is what distinguishes admin from guest. Never publish this
port.

Everything that writes is gated on safety.assert_writable(), which only passes
when the game server is *provably* stopped.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

import breeding
import lifecycle
import policy as policy_module
import savecache
import saveedit
import settings_ini
from backup import create_backup, delete_backup, list_backups, restore_backup
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

app = FastAPI(title="Palworld Save Backend", version="2.0.0")


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
        "lifecycle": lifecycle.status(),
    }


# ─── Server lifecycle ────────────────────────────────────


class ShutdownNote(BaseModel):
    reason: Optional[str] = ""


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
def restart_server() -> dict[str, Any]:
    """Run the configured RESTART_COMMAND, if the operator enabled one."""
    try:
        return lifecycle.run_restart_command()
    except RuntimeError as e:
        raise HTTPException(501 if not lifecycle.restart_supported() else 500, str(e))


@app.post("/api/server/start-container")
def start_container() -> dict[str, Any]:
    """Bring the server container back after maintenance."""
    try:
        return lifecycle.run_start_command()
    except RuntimeError as e:
        raise HTTPException(501 if not lifecycle.start_supported() else 500, str(e))


@app.post("/api/server/stop-container")
def stop_container() -> dict[str, Any]:
    """
    Stop the whole server container, not just the game process.

    This is the clean way to prepare for save edits: a stopped container cannot
    relaunch the server underneath an in-progress write.
    """
    try:
        return lifecycle.run_stop_command()
    except RuntimeError as e:
        raise HTTPException(501 if not lifecycle.stop_supported() else 500, str(e))


# ─── Access policy ───────────────────────────────────────


@app.get("/api/policy")
def get_policy() -> dict[str, Any]:
    """Current security level and guest visibility toggles."""
    return policy_module.describe()


class PolicyUpdate(BaseModel):
    securityLevel: Optional[str] = None
    guestVisibility: Optional[dict[str, bool]] = None


@app.post("/api/policy")
def update_policy(req: PolicyUpdate) -> dict[str, Any]:
    try:
        policy_module.save_policy(req.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(400, str(e))
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
    return pals


@app.get("/api/mapobjects")
def get_map_objects(category: Optional[str] = None) -> list[dict]:
    """Placed world objects with coordinates: chests, palboxes, farms, benches."""
    objects = savecache.get_section("mapObjects")
    if category:
        objects = [o for o in objects if o.get("category") == category]
    return objects


@app.get("/api/items")
def get_item_totals(limit: int = Query(500, ge=1, le=5000)) -> dict[str, Any]:
    """
    Every item on the server, totalled across all containers — the equivalent of
    standing at an item retrieval unit and asking what exists.
    """
    data = savecache.get_data() or {}
    items = data.get("items") or []
    containers = data.get("containers") or {}
    return {
        "items": items[:limit],
        "itemTypes": len(items),
        "totalCount": sum(i["count"] for i in items),
        "containersScanned": len(containers),
        "truncated": len(items) > limit,
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
def write_settings(req: SettingsWrite) -> dict:  # noqa: D401
    """
    Write settings to the INI.

    Allowed while the server is running — this is the config directory, not the
    save directory, so there is no corruption risk — but it will not take effect
    until the server restarts.
    """
    try:
        policy_module.require_capability("settings.write")
        return settings_ini.write_ini(req.changes)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except settings_ini.SettingsError as e:
        raise HTTPException(400, str(e))


@app.post("/api/settings/preset/{preset_id}")
def apply_settings_preset(preset_id: str) -> dict:
    try:
        policy_module.require_capability("settings.write")
        return settings_ini.apply_preset(preset_id)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except settings_ini.SettingsError as e:
        raise HTTPException(400, str(e))


# ─── Backups ─────────────────────────────────────────────


@app.get("/api/backups")
def get_backups() -> list[dict]:
    return list_backups()


class BackupRequest(BaseModel):
    description: Optional[str] = ""


@app.post("/api/backup")
def make_backup(req: BackupRequest) -> dict:
    """
    Snapshot the world directory.

    Safe to run while the server is live: it only reads the save files and
    writes elsewhere. Files may be mid-autosave, so a backup taken on a running
    server is a best-effort snapshot — stop the server for a guaranteed-clean one.
    """
    world_dir = get_default_world_dir()
    if not world_dir:
        raise HTTPException(404, "No world directory found")
    try:
        return create_backup(world_dir, req.description or "")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Backup failed: {e}")


@app.post("/api/restore/{backup_id}")
def do_restore(backup_id: str) -> dict:
    try:
        policy_module.require_capability("backup.manage")
        assert_writable()
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ServerRunningError as e:
        raise HTTPException(423, str(e))

    if not restore_backup(backup_id):
        raise HTTPException(404, f"Backup {backup_id} not found")

    savecache.request_parse(force=True)
    return {"success": True}


@app.delete("/api/backups/{backup_id}")
def remove_backup(backup_id: str) -> dict:
    if not delete_backup(backup_id):
        raise HTTPException(404, f"Backup {backup_id} not found")
    return {"success": True}


# ─── Save editing ────────────────────────────────────────


class SortRequest(BaseModel):
    merge: bool = True


def _run_sort(mode: str, merge: bool) -> dict:
    capability = "save.sort.stackables" if mode == "stackables" else "save.sort.all"
    try:
        # Security level is enforced here as well as in the proxy, so the policy
        # holds even if something reaches the backend directly.
        policy_module.require_capability(capability)
        return saveedit.sort_containers(mode=mode, merge=merge)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ServerRunningError as e:
        raise HTTPException(423, str(e))
    except saveedit.SaveEditError as e:
        raise HTTPException(409, str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("Sort failed")
        raise HTTPException(500, f"Sort failed: {e}")


@app.post("/api/edit/sort/stackables")
def sort_stackables(req: SortRequest) -> dict:
    """
    Tidy containers, touching only plain stackable items.

    Anything with a dynamic_id (weapons, armour, tools) is left exactly where it
    is, so durability records cannot be orphaned.
    """
    return _run_sort("stackables", req.merge)


@app.post("/api/edit/sort/all")
def sort_all(req: SortRequest) -> dict:
    """Tidy containers including equipment, carrying dynamic_id links along."""
    return _run_sort("all", req.merge)


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
