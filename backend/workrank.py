"""
What one more work rank actually buys.

The dashboard has shown work suitability as a bare integer since it shipped —
"Mining 3" — and that integer hides a **tenfold** difference:

    rank    0    1    2    3    4    5    6    7    8    9   10
    speed   0   50   70  100  140  190  260  370  510  720 1000

Rank 3 is 100 and rank 10 is 1000. `BP_PalGameSetting` carries this as
`WorkSuitabilityDefineData_<work>.CommonDefineData.CraftSpeeds`, already bundled
in `game_settings.json.gz` — nothing needed extracting, only reading.

**"THE CURVE IS SHARED" WAS A REASONABLE INFERENCE AND IT WAS WRONG FOR EIGHT OF
THE THIRTEEN.** This module used to apply the curve above to every work type,
flagged `stated: false`, because Collection, Deforest and Mining each ship their
own identical copy and every other work type's data sat inside
`WorkSuitabilityDefineDataMap` — an opaque `<MapProperty 1361B>`. Three identical
copies really is good evidence, and the flag really did say "assumed". Both were
beside the point once the map decoded (2026-08-07):

    Collection / Deforest / Mining      0  50  70 100 140 190 260 370 510  720 1000
    Watering / Seeding / OilExtraction  0  50  70 100 140 190 260 370 510  720 1000
    EmitFlame / Handcraft / Cool /
      ProductMedicine                   0  50  80 140 240 400 680 1100 1900 3200 5400
    GenerateElectricity                 0 250 325 400 500 750 1000 1500 2000 3000 4000
    Transport                           0   2   5  10  20  40  70 120 200  320  500
    MonsterFarm (Ranch)                10  12  14  16  18  20  22  24  26   28   30

Handcraft rank 10 is **5,400**, not 1,000 — the old answer was low by 5.4x on the
work type players buy handbooks for most. Transport is a different scale
entirely, and the Ranch **starts at 10 rather than 0**, so a rank-0 Ranch Pal
still produces.

The lesson is the one this repo keeps recording in its own voice: a documented
assumption, honestly labelled, still stops the next person looking. `stated` is
kept and is now `true` for all thirteen — it means "read from the game" and there
is no longer anything here that isn't.

`EPalWorkSuitability::Anyone` is in the map at a flat 100 across all eleven ranks.
It is not one of the game's 13 work suitabilities and is excluded — the map's
keys minus `Anyone`, plus the three that ship standalone, are **exactly** the 13
the species table uses, which is the check that the decode landed on real enum
values rather than plausible ones.

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

#: Work types shipping their curve as their own top-level settings property.
#: The other ten are in `WorkSuitabilityDefineDataMap`, which now decodes — so
#: this is where a curve is READ FROM, no longer which ones are trustworthy.
STATED = ("Collection", "Deforest", "Mining")

#: The map's own key prefix, and the one entry in it that is not a work type.
#: `Anyone` is a flat 100 at every rank; the game's 13 suitabilities are the map's
#: keys minus this, plus the three above.
_ENUM_PREFIX = "EPalWorkSuitability::"
_NOT_A_WORK_TYPE = "Anyone"

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


def _curve(work_type: str = "") -> list[int]:
    """
    This work type's own `CraftSpeeds`, or `[]`.

    **Per work type, not shared.** Passing nothing returns the
    Collection/Deforest/Mining curve, which is a real curve for those three and
    is *not* a default for anything else — a caller with a work type must pass
    it. See the module docstring for what the old shared answer cost.
    """
    name = str(work_type or "")
    if name and name not in STATED:
        entry = (gamedata.game_setting("WorkSuitabilityDefineDataMap") or {}).get(
            f"{_ENUM_PREFIX}{name}"
        )
        speeds = (entry or {}).get("CraftSpeeds")
        if isinstance(speeds, list) and speeds:
            return [int(v) for v in speeds]
        # No entry: fall through rather than substituting another work type's
        # numbers. An unknown work type gets `[]`, which `describe()` turns into
        # no detail at all — the same outcome as an unreadable bundle.
        return []

    for work in ((name,) if name else STATED):
        entry = gamedata.game_setting(f"WorkSuitabilityDefineData_{work}")
        speeds = ((entry or {}).get("CommonDefineData") or {}).get("CraftSpeeds")
        if isinstance(speeds, list) and speeds:
            return [int(v) for v in speeds]
    return []


def work_types() -> list[str]:
    """
    Every work type with a curve: the three standalone plus the map's, less
    `Anyone`. Should be the same 13 the species table uses, and
    `test_workrank.py` asserts exactly that.
    """
    keys = gamedata.game_setting("WorkSuitabilityDefineDataMap") or {}
    from_map = [
        str(k)[len(_ENUM_PREFIX):] for k in keys
        if str(k).startswith(_ENUM_PREFIX)
        and str(k)[len(_ENUM_PREFIX):] != _NOT_A_WORK_TYPE
    ]
    return sorted(set(STATED) | set(from_map))


def transport_range() -> list[float]:
    """Pickup range per rank. `[]` when unreadable; **0 below rank 4** when not."""
    value = gamedata.game_setting("TransportItemAbsorbRangeByWorkSuitabilityRank")
    return [float(v) for v in value] if isinstance(value, list) else []


def describe(work_type: str, rank: int) -> dict[str, Any]:
    """
    What `rank` means for `work_type`.

    Returns `{}` when the bundle is unreadable — missing detail should cost the
    tooltip, never the page.

    `stated` used to mean "this work type's curve is read rather than assumed"
    and was false for ten of thirteen. Every curve is read now, so it is true
    throughout; it stays in the payload because a client rendering an estimate
    differently from a fact should not have to be re-taught how when the next
    assumed number appears.
    """
    curve = _curve(work_type)
    if not curve:
        return {}

    cap = max_rank() or (len(curve) - 1)
    rank = max(0, min(int(rank), len(curve) - 1))
    out: dict[str, Any] = {
        "rank": rank,
        "maxRank": cap,
        "speed": curve[rank],
        # Against THIS work type's rank 3, not a shared one. It is 100 on the six
        # standard types and is 140, 400, 10 or 16 elsewhere, so the ratio only
        # ever compares a work type to itself — which is the comparison a player
        # wants ("what does one more rank buy me here") and the only one the data
        # supports. Rank 0 is 0 for every type but the Ranch, and that is a real
        # answer: no suitability is not slow work, it is none.
        "relativeToRank3": round(curve[rank] / curve[3], 2) if curve[3] else None,
        "curve": curve,
        # True throughout since the map decoded. Kept because the distinction it
        # draws is worth keeping wired up. See the module docstring.
        "stated": True,
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
    curves = {work: _curve(work) for work in work_types()}
    curves = {work: c for work, c in curves.items() if c}
    return {
        # `curve` was one array and is now one per work type. It stays for the
        # Collection/Deforest/Mining shape a caller may still be reading, but a
        # client showing a table must use `curves` — there is no single curve.
        "curve": _curve(),
        "curves": curves,
        "maxRank": max_rank(),
        "transportRange": transport_range(),
        "statedFor": sorted(curves),
        # Said out loud rather than left to a docstring, for the same reason
        # `hasMultiplier` travels in the optimiser's payload: the client is the
        # thing about to draw a number.
        "note": (
            "Each work type has its own curve, read from the game. They differ "
            "by a lot — Handcraft reaches 5,400 at rank 10 where Mining reaches "
            "1,000 — so a speed figure is only comparable within one work type."
        ) if curves else "",
    }
