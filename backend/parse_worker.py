"""
Out-of-process Level.sav parser.

Run as a subprocess so that:

  * the several GB of peak RSS a large Level.sav needs is handed back to the OS
    the instant the worker exits, instead of bloating the API process forever;
  * the work runs at the lowest CPU and I/O priority, so it yields to the game
    server rather than competing with it;
  * a hung or pathological parse can be killed by timeout without taking the
    dashboard down with it.

Writes an extracted-only JSON summary. The raw parse tree never leaves this
process.

Usage: python3 parse_worker.py --out result.json [--items]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [worker] [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# Shape of the payload this worker writes. Bump it whenever a field is added,
# removed or renamed.
#
# The disk cache (`savecache._CACHE_FILE`) survives an upgrade, and an upgraded
# dashboard reading an older payload does not fail — it reads a field that is not
# there. Renaming the per-base Pal count produced `undefined` in the API and
# **"NaN" on the Bases tab** on a server that had simply not re-parsed since the
# update, with nothing anywhere saying why. `savecache` now discards a cache
# whose schema does not match this, so the worst case is one re-parse instead of
# a wrong number.
SCHEMA_VERSION = 13


def lower_priority() -> None:
    """Drop to idle CPU/IO priority so the game server always wins."""
    try:
        os.nice(19)
    except (OSError, AttributeError):
        pass

    try:
        import psutil  # type: ignore

        proc = psutil.Process()
        if hasattr(psutil, "IOPRIO_CLASS_IDLE"):
            proc.ionice(psutil.IOPRIO_CLASS_IDLE)
    except Exception:  # noqa: BLE001 - best effort only
        pass


def _player_container_roles(players: list[dict]) -> dict[str, str]:
    """
    `{character_container_id: "palbox" | "party"}` across every player.

    The ids live in the *player* saves, not in `Level.sav`, so this opens each
    one — ~100 KB apiece, and this is the worker, already off the request path.

    Best effort by design: a player whose `.sav` is missing or unreadable simply
    leaves their containers unclassified, which shows up as `other` rather than
    failing a parse the rest of the dashboard depends on.
    """
    from parser import extract_player_save, load_gvas
    from savefiles import get_player_sav_path

    roles: dict[str, str] = {}
    for player in players:
        uid = str(player.get("uid") or "")
        if not uid:
            continue
        try:
            path = get_player_sav_path(uid)
            gvas = load_gvas(path) if path else None
            if gvas is None:
                continue
            info = extract_player_save(gvas, uid)
        except Exception as e:  # noqa: BLE001 - one bad save must not lose the rest
            logger.warning("Could not read player save for %s: %s", uid[:8], e)
            continue
        for field, role in (
            ("palStorageContainerId", "palbox"),
            ("otomoCharacterContainerId", "party"),
        ):
            container_id = str(info.get(field) or "").lower()
            if container_id:
                roles[container_id] = role
    return roles


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="path to write the JSON result")
    ap.add_argument("--items", action="store_true", help="also decode item containers")
    args = ap.parse_args()

    lower_priority()

    # Imported after the priority drop so even module import is niced.
    from parser import (
        extract_base_camps,
        extract_base_worker_capacity,
        extract_base_workers,
        extract_characters,
        extract_container_ownership,
        extract_containers,
        extract_dimension_storage,
        extract_guild_research,
        extract_guild_storage,
        extract_guilds,
        extract_map_objects,
        extract_pal_storage,
        extract_work_assignments,
        guild_name_map,
        load_gvas,
        summarise_base_storage,
    )
    from savefiles import (
        get_default_world_dir,
        get_level_sav_path,
        list_player_dps_paths,
    )

    world_dir = get_default_world_dir()
    level_path = get_level_sav_path(world_dir)
    if not level_path:
        json.dump(
            {"ok": False, "error": "Level.sav not found", "worldDir": world_dir},
            open(args.out, "w"),
        )
        return 2

    size_mb = os.path.getsize(level_path) / 1024 / 1024
    logger.info("Parsing %s (%.1f MB, items=%s)", level_path, size_mb, args.items)

    gvas = load_gvas(level_path, include_items=args.items)
    if gvas is None:
        json.dump(
            {"ok": False, "error": "parse failed — see backend logs", "levelPath": level_path},
            open(args.out, "w"),
        )
        return 3

    guilds = extract_guilds(gvas)
    bases = extract_base_camps(gvas, guild_name_map(guilds))
    players, pals = extract_characters(gvas)
    containers = extract_containers(gvas) if args.items else {}
    map_objects = extract_map_objects(gvas)

    # Who the game has ACTUALLY assigned to each job, as opposed to who
    # `baseassign` thinks should be there. Re-walks the already-decoded
    # MapObjectSaveData and CharacterSaveParameterMap, so the only real cost is
    # decoding `WorkSaveData` itself — +0.30s median on refworld's 3.06s parse.
    work_assignments = extract_work_assignments(gvas)

    # Which placed object owns which container, and therefore which base. Cheap
    # (it re-walks an already-decoded MapObjectSaveData) and it is what turns a
    # single undifferentiated item pile into per-base storage.
    ownership = extract_container_ownership(gvas)
    # The Guild Chest, which is *not* one of the above: it hangs no ItemContainer
    # module off its placed object, so `ownership` never sees it. Its contents
    # are guild property held one level up. See `extract_guild_storage`.
    guild_storage = extract_guild_storage(gvas)
    # The Pal Lab tree's per-guild progress. Guild-level for the same reason the
    # chest is: research is shared by everyone in it, so folding it into a base
    # would report the same thing once per base.
    guild_research = extract_guild_research(gvas)
    base_storage = summarise_base_storage(containers, ownership, bases) if args.items else []
    storage_by_base = {s["baseId"]: s for s in base_storage}

    # Which character container belongs to which base, and therefore where each
    # Pal actually is. See `extract_base_workers` — this used to be documented as
    # unobtainable, and the `guildPalCount` fallback below exists because of that.
    worker_containers = extract_base_workers(gvas)
    # The denominator `palCount` never had. Read from the base's own worker
    # container rather than computed from a setting — see the function's
    # docstring for why a server-wide figure cannot answer a per-base question.
    worker_capacity = extract_base_worker_capacity(gvas)
    player_containers = _player_container_roles(players)
    # Pal-holding *structures* — a Dimensional Pal Storage, a Global Pal
    # Storage, a Flea Market stand. See `extract_pal_storage`: these used to
    # fall through to `other`, which is why a Pal in one was missing from its
    # owner's counts and from breeding with nothing to say where it had gone.
    storage_containers = extract_pal_storage(gvas)

    for pal in pals:
        container_id = str(pal.get("containerId") or "").lower()
        base_id = worker_containers.get(container_id)
        store = storage_containers.get(container_id)
        if base_id:
            pal["location"] = "base"
            pal["baseId"] = base_id
        elif store:
            pal["location"] = "storage"
            pal["baseId"] = store["baseCampId"]
            pal["storageKind"] = store["kindName"]
            # A structure's store is guild property, so the guild is what makes
            # this Pal findable: it commonly has no `OwnerPlayerUId` at all, and
            # every member can take it out.
            if not pal.get("guildId"):
                pal["guildId"] = store["guildId"]
        else:
            # `other` is still a real state rather than a parse failure — a
            # container the game has kept but nothing references any more. It is
            # now genuinely rare: every container on the reference world
            # classifies.
            pal["location"] = player_containers.get(container_id, "other")
            pal["baseId"] = ""

    # Dimensional Pal Storage is not in Level.sav at all — it is a per-player
    # file, and a Pal moved into one was missing from every count here rather
    # than merely mislabelled. Appended after the loop above because these Pals
    # have no container to classify; `extract_dimension_storage` stamps their
    # location itself.
    #
    # Best-effort per file: one unreadable `_dps.sav` must not cost the whole
    # parse, because the rest of the world is still perfectly good data.
    dimension_pals = 0
    for uid, path in list_player_dps_paths(world_dir).items():
        dps = load_gvas(path)
        if dps is None:
            logger.warning("Could not read Dimensional Pal Storage for %s", uid[:8])
            continue
        stored = extract_dimension_storage(dps, uid)
        for pal in stored:
            pal["baseId"] = ""
        pals.extend(stored)
        dimension_pals += len(stored)
    if dimension_pals:
        logger.info("Read %d Pals from Dimensional Pal Storage", dimension_pals)

    pals_at_base: dict[str, int] = {}
    for pal in pals:
        if pal["baseId"]:
            pals_at_base[pal["baseId"]] = pals_at_base.get(pal["baseId"], 0) + 1

    for base in bases:
        # Two figures, because they answer two questions and conflating them is
        # what produced the original bug.
        #
        # `palCount` is the Pals *assigned to work at this base*, from its own
        # worker container. `guildPalCount` is every Pal the owning guild has
        # anywhere, which is a guild-level number that every base in the guild
        # legitimately shares — so summing it across bases triples a three-base
        # guild's total. The Bases tab counts it once per guild for that reason.
        base["palCount"] = pals_at_base.get(base["id"], 0)
        # **Absent, not 0, when the container did not resolve.** "No cap known"
        # and "a cap of zero" must stay distinguishable: a zero denominator
        # renders as a base that is infinitely full.
        capacity = worker_capacity.get(base["id"])
        if capacity:
            base["workerCapacity"] = capacity
        base["guildPalCount"] = sum(
            1 for p in pals if p.get("guildId") == base.get("guildId")
        )
        base["objectCount"] = sum(1 for o in map_objects if o.get("baseCampId") == base["id"])
        summary = storage_by_base.get(base["id"])
        base["containerIds"] = [c["containerId"] for c in summary["containers"]] if summary else []
        base["storedItemCount"] = summary["itemCount"] if summary else 0
        base["usedSlots"] = summary["usedSlots"] if summary else 0
        base["totalSlots"] = summary["totalSlots"] if summary else 0

    # Server-wide item totals — the "item retrieval unit" view. Aggregating here
    # rather than in the API keeps it out of the request path entirely.
    item_totals: dict[str, int] = {}
    for slots in containers.values():
        for slot in slots:
            if slot.get("isEmpty"):
                continue
            item_id = slot.get("itemId") or ""
            if item_id:
                item_totals[item_id] = item_totals.get(item_id, 0) + int(slot.get("stackCount") or 0)

    items = sorted(
        ({"itemId": k, "count": v} for k, v in item_totals.items()),
        key=lambda i: -i["count"],
    )

    result = {
        "ok": True,
        "schema": SCHEMA_VERSION,
        "levelPath": level_path,
        "worldDir": world_dir,
        "levelSizeMb": round(size_mb, 1),
        "parsedAt": __import__("time").time(),
        "guilds": guilds,
        "bases": bases,
        "players": players,
        "pals": pals,
        "containers": containers,
        "containerOwnership": ownership,
        "baseStorage": base_storage,
        "guildStorage": guild_storage,
        "guildResearch": guild_research,
        "mapObjects": map_objects,
        "workAssignments": work_assignments,
        "items": items,
        "counts": {
            "guilds": len(guilds),
            "bases": len(bases),
            "players": len(players),
            "pals": len(pals),
            "containers": len(containers),
            "ownedContainers": len(ownership),
            "guildChests": len(guild_storage),
            "guildResearch": len(guild_research),
            "mapObjects": len(map_objects),
            "workAssignments": len(work_assignments),
            "itemTypes": len(items),
        },
    }

    tmp = args.out + ".tmp"
    with open(tmp, "w") as f:
        json.dump(result, f)
    os.replace(tmp, args.out)

    logger.info(
        "Done: %d guilds, %d bases, %d players, %d pals",
        len(guilds), len(bases), len(players), len(pals),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
