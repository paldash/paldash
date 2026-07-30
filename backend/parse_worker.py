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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="path to write the JSON result")
    ap.add_argument("--items", action="store_true", help="also decode item containers")
    args = ap.parse_args()

    lower_priority()

    # Imported after the priority drop so even module import is niced.
    from parser import (
        extract_base_camps,
        extract_characters,
        extract_container_ownership,
        extract_containers,
        extract_guilds,
        extract_map_objects,
        guild_name_map,
        load_gvas,
        summarise_base_storage,
    )
    from savefiles import get_default_world_dir, get_level_sav_path

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

    # Which placed object owns which container, and therefore which base. Cheap
    # (it re-walks an already-decoded MapObjectSaveData) and it is what turns a
    # single undifferentiated item pile into per-base storage.
    ownership = extract_container_ownership(gvas)
    base_storage = summarise_base_storage(containers, ownership, bases) if args.items else []
    storage_by_base = {s["baseId"]: s for s in base_storage}

    for base in bases:
        # A GUILD figure, and named as one.
        #
        # It was `palCount` on the base, which read as "Pals at this base" and
        # was not: every base in a guild got the guild's whole total, so the
        # Bases tab summed 100 Pals across three bases into 300.
        #
        # Per-base attribution is not available. Pals live in character
        # containers (palboxes), `extract_container_ownership` maps *item*
        # containers to bases, and guild bases share a palbox anyway — so
        # "which base is this Pal at" has no answer in the save. Reporting the
        # guild total once, honestly labelled, is the whole of what the data
        # supports.
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
        "mapObjects": map_objects,
        "items": items,
        "counts": {
            "guilds": len(guilds),
            "bases": len(bases),
            "players": len(players),
            "pals": len(pals),
            "containers": len(containers),
            "ownedContainers": len(ownership),
            "mapObjects": len(map_objects),
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
