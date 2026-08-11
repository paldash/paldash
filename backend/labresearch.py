"""
The Pal Lab research tree, and how far a guild has got through it.

Research is **guild-wide and permanent**, which makes it the one base upgrade
that explains why two identical Pals produce differently on two different
servers. The dashboard showed a Pal's work level and speed and had no idea the
guild had bought +10% Handiwork.

`DT_LabResearchDataTable` had never been opened. It was found by searching every
table's *columns* for `WorkSuitability` while chasing an unrelated question —
the same shape `docs/GAMEDATA-SOURCES.md` exists to prevent.

## Completion is a comparison, not a flag

The save gives no "done" boolean. `GuildExtraSaveDataMap[].Lab.RawData` carries
**all 168 rows for every guild**, started or not — one live guild's are all
`0.0` — so a row's presence says nothing. What says something is `work_amount`
against the bundle's `RequiredWorkAmount`:

    work_amount >= required     complete
    0 < work_amount < required  in progress
    work_amount == 0            not started

Which is why `parser.extract_guild_research` returns raw amounts and the join
happens here: the save half and the catalogue half come from different files
and only one of them knows the target.

## Two things this refuses to say

- **`workAmount` is not a time.** It is work units, and how fast a base delivers
  them depends on which Pals are assigned, which no game file states.
  `basesupply.py`'s rule: report facts, not mechanics.
- **`available` is prerequisite-only.** A node whose parent is done is
  *unlocked*; whether the guild can afford its materials is a different question
  answered by base storage, and conflating them would claim a stock check this
  does not perform.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
from typing import Any, Optional

import gamedata
import passiveeffects

logger = logging.getLogger(__name__)

DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "data", "lab_research.json.gz")

_bundle: Optional[dict[str, Any]] = None


def load() -> dict[str, Any]:
    """The bundled tree, or `{}` when unreadable — never an exception."""
    global _bundle
    if _bundle is None:
        try:
            with gzip.open(DATA_PATH, "rt", encoding="utf-8") as f:
                _bundle = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(
                "Lab research data unavailable (%s); the research tree will be "
                "empty", e,
            )
            _bundle = {}
    return _bundle


def reload() -> None:
    global _bundle
    _bundle = None


def _work_name(work_id: str) -> str:
    """
    `Handcraft` -> "Handiwork", from the game's own table.

    The bundled key is `display_name`, not `name` — reading the wrong one
    silently labels every row with an internal id, which `/api/optimise/work`
    already records getting wrong once.
    """
    for entry in gamedata.work_suitabilities() or []:
        if str(entry.get("id")) == work_id:
            return str(entry.get("display_name") or work_id)
    return work_id


def _describe_effect(node: dict[str, Any]) -> dict[str, Any]:
    """
    The node's effect, named through the same classifier the passive panel uses.

    Reused rather than reimplemented: eleven of the sixteen research effect
    types appear on no passive at all and had rules added to `passiveeffects`
    for exactly this, so a second mapping here could only ever disagree with it.
    """
    kind = node.get("effectType")
    if not kind:
        # Ten rows are `TechnologyUnlock` and grant no rate. The extractor
        # normalises the game's `::no` to null so nothing renders "no +0%".
        return {"kind": None, "label": "Unlocks a technology", "value": 0.0}
    described = passiveeffects.describe_effect(
        {"type": kind, "value": node.get("effectValue") or 0.0, "target": "ToSelf"}
    )
    return {
        "kind": kind,
        "label": described["label"],
        "value": float(node.get("effectValue") or 0.0),
        "unit": described["unit"],
        "category": described["category"],
        "categoryLabel": described["categoryLabel"],
    }


def tree(progress: Optional[dict[str, float]] = None,
         current: str = "") -> dict[str, Any]:
    """
    Every research node, optionally with one guild's progress folded in.

    `progress` is `{research_id: work_amount}` straight from
    `parser.extract_guild_research`. Omit it for the plain catalogue — the tree
    is worth showing on a server with no parsed world, which is why the state is
    a parameter rather than a fetch.
    """
    bundle = load()
    nodes = bundle.get("research") or {}
    if not nodes:
        return {"nodes": [], "byWork": {}, "known": False, "note": ""}

    state = progress or {}
    known_state = bool(progress)

    out: list[dict[str, Any]] = []
    for node_id, node in nodes.items():
        required = float(node.get("workAmount") or 0.0)
        done_amount = float(state.get(node_id) or 0.0)

        row: dict[str, Any] = {
            "id": node_id,
            "work": node.get("work"),
            "workName": _work_name(node.get("work") or ""),
            "subType": node.get("subType"),
            "requires": node.get("requires"),
            "workAmount": required,
            "materials": [
                {
                    "itemId": m.get("itemId"),
                    "name": gamedata.item_name(str(m.get("itemId") or "")),
                    "icon": (gamedata.item(str(m.get("itemId") or "")) or {}).get("icon", ""),
                    "count": int(m.get("count") or 0),
                }
                for m in node.get("materials") or []
            ],
            "effect": _describe_effect(node),
            "essential": bool(node.get("essential")),
        }

        if known_state:
            complete = required > 0 and done_amount >= required
            parent = node.get("requires")
            parent_done = True
            if parent:
                parent_required = float((nodes.get(parent) or {}).get("workAmount") or 0.0)
                parent_done = float(state.get(parent) or 0.0) >= parent_required > 0
            row.update({
                "workDone": done_amount,
                "complete": complete,
                "inProgress": (not complete) and done_amount > 0,
                "isCurrent": node_id == current,
                # Prerequisite only — NOT a claim that the materials are in
                # stock. See the module docstring.
                "available": (not complete) and parent_done,
            })
        out.append(row)

    out.sort(key=lambda r: (str(r["work"] or ""), r["id"]))
    result: dict[str, Any] = {
        "nodes": out,
        "byWork": dict(bundle.get("byWork") or {}),
        "known": known_state,
        "note": bundle.get("note") or "",
    }
    if known_state:
        result["completed"] = sum(1 for r in out if r.get("complete"))
        result["total"] = len(out)
        result["currentResearchId"] = current
    return result
