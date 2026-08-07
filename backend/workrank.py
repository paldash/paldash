"""
What one more work rank actually buys.

The dashboard has shown work suitability as a bare integer since it shipped —
"Mining 3" — and that integer hides a **tenfold** difference:

    rank    0    1    2    3    4    5    6    7    8    9   10
    speed   0   50   70  100  140  190  260  370  510  720 1000

Rank 3 is 100 and rank 10 is 1000. `BP_PalGameSetting` carries this as
`WorkSuitabilityDefineData_<work>.CommonDefineData.CraftSpeeds`, already bundled
in `game_settings.json.gz` — nothing needed extracting, only reading.

**THE GAME STATES THE CURVE FOR THREE WORK TYPES, NOT ALL OF THEM.** Collection,
Deforest and Mining each carry their own copy and **all three are identical**.
Every other work type's data lives in `WorkSuitabilityDefineDataMap`, which
decodes as an opaque `<MapProperty 1361B>` — the same wall the element chart
hits. Three identical copies is good evidence the curve is shared and it is not
the game saying so, which is why `describe()` returns `stated: false` for the
rest and the UI is expected to show it as an estimate. A number presented with
the same confidence as a read one is the failure this project keeps recording.

Two things the curve is NOT:

- **Mining and Deforest gate on MATERIAL, and that is eligibility rather than
  speed.** A rank-2 miner cannot touch Iron at any speed: rank 1 unlocks Stone,
  2 Copper, 3 Iron, 4 Platinum. That is a harder rule than a multiplier and
  belongs beside `requiredRank`, not folded into it.
- **A Transport Pal below rank 4 has ZERO pickup range.**
  `TransportItemAbsorbRangeByWorkSuitabilityRank` is `[0,0,0,0,300,…,1000]`, so
  ranks 1-3 are not "slower at transporting", they are a different thing.

**Condenser stars do not add work suitability.** `CharacterMaxRank` is 5 and
nothing links it to work rank; a suitability-10 Pal is base plus handbooks. See
task #74 — that remains unobserved rather than disproven.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import gamedata

logger = logging.getLogger(__name__)

#: Work types whose curve the game states outright, each carrying its own copy.
#: The rest are inside an opaque MapProperty — see the module docstring.
STATED = ("Collection", "Deforest", "Mining")

#: Extra per-rank data beyond speed, keyed by the settings field that holds it.
_DETAIL_FIELD = {
    "Collection": "CollectionDefineData",
    "Deforest": "DeforestDefineData",
    "Mining": "MiningDefineData",
}


def max_rank() -> Optional[int]:
    """
    `WorkSuitabilityMaxRank`, or None when unreadable.

    **10, and this project documented "the game ships none" for months.** The
    check that concluded that swept the DataTables, which is where a rank column
    would live and does not — but the constant is in the settings CDO, which
    nobody searched for this. A documented negative gets trusted and stops the
    next person looking.
    """
    value = gamedata.game_setting("WorkSuitabilityMaxRank")
    return int(value) if isinstance(value, (int, float)) and value > 0 else None


def _curve() -> list[int]:
    """The shared `CraftSpeeds` array, or `[]`."""
    for work in STATED:
        entry = gamedata.game_setting(f"WorkSuitabilityDefineData_{work}")
        speeds = ((entry or {}).get("CommonDefineData") or {}).get("CraftSpeeds")
        if isinstance(speeds, list) and speeds:
            return [int(v) for v in speeds]
    return []


def transport_range() -> list[float]:
    """Pickup range per rank. `[]` when unreadable; **0 below rank 4** when not."""
    value = gamedata.game_setting("TransportItemAbsorbRangeByWorkSuitabilityRank")
    return [float(v) for v in value] if isinstance(value, list) else []


def describe(work_type: str, rank: int) -> dict[str, Any]:
    """
    What `rank` means for `work_type`.

    Returns `{}` when the bundle is unreadable — missing detail should cost the
    tooltip, never the page.

    `stated` is the load-bearing field: true when the game gives this work type
    its own curve, false when we are applying the one the three that do share.
    Callers must not present the two identically.
    """
    curve = _curve()
    if not curve:
        return {}

    cap = max_rank() or (len(curve) - 1)
    rank = max(0, min(int(rank), len(curve) - 1))
    out: dict[str, Any] = {
        "rank": rank,
        "maxRank": cap,
        "speed": curve[rank],
        # Against rank 3, which the game sets to exactly 100 — so this reads as a
        # percentage of "ordinary" rather than of an arbitrary base. Rank 0 is 0
        # and that is a real answer: no suitability is not slow work, it is none.
        "relativeToRank3": round(curve[rank] / curve[3], 2) if curve[3] else None,
        "curve": curve,
        "stated": work_type in STATED,
    }

    detail = _detail(work_type, rank)
    if detail:
        out.update(detail)
    if work_type == "Transport":
        ranges = transport_range()
        if rank < len(ranges):
            out["pickupRange"] = ranges[rank]
            # Not "slower", *nothing*. Worth saying outright because a bare 0 in
            # a table reads as missing data.
            out["pickupDisabled"] = ranges[rank] == 0
    return out


def _detail(work_type: str, rank: int) -> dict[str, Any]:
    field = _DETAIL_FIELD.get(work_type)
    if not field:
        return {}
    entry = gamedata.game_setting(f"WorkSuitabilityDefineData_{work_type}") or {}
    rows = entry.get(field)
    if not isinstance(rows, list) or rank >= len(rows):
        return {}
    row = rows[rank] or {}
    out: dict[str, Any] = {}
    if "DropNumRate" in row:
        out["dropRate"] = row["DropNumRate"]
    if "DamageRate" in row:
        out["damageRate"] = row["DamageRate"]
    material = row.get("MaterialSubType")
    if isinstance(material, str):
        # `EPalMapObjectMaterialSubType::Iron` -> `Iron`, and `None` means the
        # rank unlocks no material at all rather than "unknown".
        name = material.rsplit("::", 1)[-1]
        out["material"] = None if name == "None" else name
        out["materialGated"] = True
    return out


def curve_table() -> dict[str, Any]:
    """
    The whole curve once, for a UI that wants to show it rather than one row.

    Cheap enough to build per request — it is a dozen integers out of an
    already-parsed bundle — so it is not cached; the thing worth caching here
    would be the bundle, and `gamedata` already does that.
    """
    curve = _curve()
    return {
        "curve": curve,
        "maxRank": max_rank(),
        "transportRange": transport_range(),
        "statedFor": list(STATED),
        # Said out loud rather than left to a docstring, for the same reason
        # `hasMultiplier` travels in the optimiser's payload: the client is the
        # thing about to draw a number.
        "note": (
            "The game states this curve for Collection, Deforest and Mining, "
            "and all three are identical. Other work types are assumed to share "
            "it — their own data is in a blueprint map this cannot read."
        ) if curve else "",
    }
