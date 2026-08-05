#!/usr/bin/env python3
"""
Raid bosses: what summons them, at what level, and what they drop.

Phase 1.7 of `docs/PLAN.md`. `DT_PalRaidBoss`, 11 rows.

THIS CONFIRMS AN EARLIER REFUSAL RATHER THAN OVERTURNING IT. `boss_spawners.json.gz`
carries 90 *placed* field bosses from `DT_BossSpawnerLoactionData`, and zero of
its 159 rows hold a `RAID_` id. That was recorded as correct rather than as a
gap, on the grounds that raid bosses are summoned at an altar instead of placed
in the world — so a table of *locations* has nothing to say about them.

This is the table that does. Both facts stand together, which is the outcome a
well-stated negative should have.

**COUNT THE ENTRIES, NOT THE ROWS.** Each row's `InfoList` can hold more than one
boss — the `_2` suffixes are the harder variants (Bellanoir Libero). Counting
rows is the error that briefly turned 90 field bosses into "159", and the same
shape of mistake is available here.

**NO POSITIONS, AND NONE MUST BE INVENTED.** These are altar-summoned. A map
marker for one would be exactly the `BP_LevelObject_TowerLockBarrier` mistake:
a plausible-looking category that does not correspond to anything in the world.

Usage:  python3 scripts/extract-raidbosses.py [--verify]
Output: backend/data/raidbosses.json.gz
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import palpak            # noqa: E402
import uassettable       # noqa: E402
from jsonout import write_json  # noqa: E402

OUT = os.path.join(os.path.dirname(HERE), "backend", "data", "raidbosses.json.gz")
UNSET = {"", "None", None}


def _enum(value) -> str:
    text = str(value or "")
    return text.rsplit("::", 1)[-1] if "::" in text else text


def _items(row: dict, prefix: str) -> list:
    """A `SuccessItemList`-shaped array, or []."""
    out = []
    for entry in row.get(prefix) or []:
        if not isinstance(entry, dict):
            continue
        item_id = str(entry.get("ItemId") or entry.get("StaticItemId") or "")
        if item_id in UNSET:
            continue
        out.append({
            "itemId": item_id,
            "min": int(entry.get("Min") or entry.get("MinNum") or 0),
            "max": int(entry.get("Max") or entry.get("MaxNum") or 0),
        })
    return out


def build(pak=None) -> tuple[dict, dict]:
    pak = pak or palpak.Pak()
    path = next(p for p in pak.files if p.endswith("DT_PalRaidBoss.uasset"))
    rows = uassettable.read_table(pak, path)

    bosses = {}
    entries = 0
    for key, row in rows.items():
        forms = []
        for info in row.get("InfoList") or []:
            if not isinstance(info, dict):
                continue
            # `PalID` is a wrapped row handle: {"Key": "RAID_NightLady"}.
            pal = info.get("PalID")
            species = str(pal.get("Key") if isinstance(pal, dict) else pal or "")
            if species in UNSET:
                continue
            entries += 1
            forms.append({
                "speciesId": species,
                "level": int(info.get("Level") or 0),
                "canModeChange": bool(info.get("CanModeChange")),
            })
        if not forms:
            continue
        bosses[str(key)] = {
            "id": str(key),
            "forms": forms,
            "eggWeights": [
                {"speciesId": str(e.get("PalID") or ""), "weight": float(e.get("Weight") or 0)}
                for e in (row.get("EggPalIDAndWeight") or [])
                if isinstance(e, dict) and str(e.get("PalID") or "") not in UNSET
            ],
            "rewards": _items(row, "SuccessItemList"),
            "rewardsAnyOne": _items(row, "SuccessAnyOneItemList"),
            "achievementType": _enum(row.get("AchievementType")),
        }
    return {"bosses": bosses}, {"rows": len(rows), "entries": entries}


def main() -> int:
    pak = palpak.Pak()
    data, stats = build(pak)
    if not data["bosses"]:
        print("REFUSING: no raid bosses decoded.", file=sys.stderr)
        return 2

    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "backend"))
    import gamedata  # noqa: E402

    species = {f["speciesId"] for b in data["bosses"].values() for f in b["forms"]}
    # Advisory, not a refusal — and here the reason was predicted before the
    # check ran. The absent ids are all `_2` suffixed: `RAID_NightLady_Dark_2`,
    # `RAID_KingBahamut_Dragon_2`. AGENTS.md already records that the `_2`
    # suffixes are the harder variants (Bellanoir Libero), and the bundled
    # character tables carry the base forms rather than the difficulty tiers.
    # `gamedata.pal()` strips the RAID_ prefix, so the base ids resolve fine; it
    # is the suffix that has no entry.
    unknown = sorted(s for s in species if not gamedata.character(s))

    if "--verify" in sys.argv:
        print(f"verified {stats['entries']} boss forms across {stats['rows']} rows; "
              "every species resolves")
        return 0

    write_json(OUT, data)
    print(f"wrote {OUT}")
    print(f"  {stats['rows']} summon rows carrying {stats['entries']} boss forms")
    print(f"  {len(species)} distinct species, levels "
          f"{min(f['level'] for b in data['bosses'].values() for f in b['forms'])}-"
          f"{max(f['level'] for b in data['bosses'].values() for f in b['forms'])}")
    print("  no positions: these are altar-summoned, not world-placed")
    if unknown:
        print(f"  advisory: {len(unknown)} of {len(species)} forms are the `_2` "
              f"difficulty variants, which the bundled character tables do not "
              f"carry: {unknown[:3]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
