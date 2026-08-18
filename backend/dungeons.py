"""
The random-dungeon guide, joined for serving (#136).

`dungeons.json.gz` stores structure and NAMES of things — species ids, loot
lottery names — and this module attaches what players read: display names via
`gamedata`, and the loot tables themselves from `economy.json.gz`'s lottery
section, which already expands every FieldLottery. Item lists therefore ship
once and cannot drift between the two bundles.

Honesty rules carried from the extractor:

- **Areas are unnamed because the game does not name them.** `named: false`
  travels per area and the UI says so; `label` is a humanised area id
  (`Sakura001` -> "Sakura 001"), presented as an id, not a name.
- **`slotShare` is the chance an item fills its slot GIVEN the slot rolls** —
  the same claim `itemsource` makes, computed the same way, and nothing here
  claims how often a chest spawns or rolls.
- **Enemy `weight` is relative within its spawner group only**, never across
  groups — `weightIsWithinGroup` travels in the payload.
"""

from __future__ import annotations

import gzip
import json
import os
import re
from typing import Any

import gamedata
import viewcache

DUNGEONS_PATH = os.path.join(os.path.dirname(__file__), "data", "dungeons.json.gz")


class DungeonsUnavailable(RuntimeError):
    pass


def _load() -> dict:
    try:
        with gzip.open(DUNGEONS_PATH, "rt", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise DungeonsUnavailable(f"dungeons bundle unreadable: {e}") from e


def _label(area_id: str) -> str:
    # "Sakura001" -> "Sakura 001", "IceSnow01" -> "Ice Snow 01". An id made
    # readable, deliberately still id-shaped — the game has no name to show.
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", area_id)
    return re.sub(r"(?<=[A-Za-z])(?=\d)", " ", spaced)


def _slot_shares(rows: list[dict]) -> list[dict]:
    totals: dict[Any, float] = {}
    for r in rows:
        totals[r.get("slot")] = totals.get(r.get("slot"), 0.0) + float(r.get("weight") or 0.0)
    out = []
    for r in rows:
        total = totals.get(r.get("slot")) or 0.0
        share = (float(r.get("weight") or 0.0) / total) if total else None
        item_id = str(r.get("itemId") or "")
        described = gamedata.describe_item(item_id)
        out.append({
            **r,
            "name": described.get("name") or item_id,
            "icon": described.get("icon") or "",
            "slotShare": share,
        })
    return out


def _build() -> dict:
    bundle = _load()
    lottery = (gamedata.economy().get("lottery") or {})

    areas = []
    missing_lotteries: list[str] = []
    for area_id, entry in (bundle.get("areas") or {}).items():
        enemies = []
        for group in entry.get("enemies") or []:
            roster = []
            for row in group.get("roster") or []:
                who = str(row.get("id") or "")
                described = gamedata.describe_pal(who) if not row.get("isNpc") else None
                roster.append({
                    **row,
                    "name": (described or {}).get("name")
                            or gamedata.character_name(who) or who,
                    "icon": (described or {}).get("icon") or "",
                    "elements": (described or {}).get("elements") or [],
                })
            enemies.append({**group, "roster": roster})

        loot = []
        for l in entry.get("loot") or []:
            name = str(l.get("lotteryName") or "")
            rows = lottery.get(name)
            if rows is None:
                # Reported, never dropped: a chest whose table went missing
                # must not read as a chest that holds nothing.
                missing_lotteries.append(name)
            loot.append({
                **l,
                "items": _slot_shares(list(rows)) if rows else None,
            })

        areas.append({
            "areaId": area_id,
            "label": _label(area_id),
            "named": bool(entry.get("named")),
            "levels": entry.get("levels") or [],
            "enemies": enemies,
            "loot": loot,
            "rewards": entry.get("rewards") or [],
        })

    areas.sort(key=lambda a: a["areaId"])
    return {
        "areas": areas,
        "note": bundle.get("_note") or "",
        # The two scoping caveats the client must render rather than know.
        "weightIsWithinGroup": True,
        "namedByGame": False,
        "missingLotteries": sorted(set(missing_lotteries)),
    }


def catalogue() -> dict:
    """Joined and cached per bundle-file stamp — reference data, no save read."""
    return viewcache.per_files(
        "dungeons:catalogue",
        [DUNGEONS_PATH, gamedata.ECONOMY_PATH, gamedata.DATA_PATH],
        _build,
    )
