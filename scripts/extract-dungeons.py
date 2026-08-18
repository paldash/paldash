#!/usr/bin/env python3
"""
The random-dungeon guide: what spawns inside, at what level, and what the
chests roll — per spawn area, from the game's own five dungeon tables.

    DT_DungeonSpawnAreaDataTable      23   the areas (join hub: SpawnAreaId)
    DT_DungeonLevelDataTable          15   which streaming level, EXP bonus
    DT_DungeonEnemySpawnDataTable     59   weighted spawner groups per area+rank
    DT_DungeonItemLotteryDataTable    32   chest loot, by FieldLottery NAME
    DT_DungeonRewardSpawnerLotteryDataTable  162  end-of-dungeon reward spawners

The enemy rosters resolve through `DT_PalWildSpawner` — the dungeon table's
`SpawnerName` matches the wild table's SpawnerName COLUMN (its row keys are a
different id space; joining on keys resolves 0 of 38, on the column 38 of 38,
and the extractor refuses if that coverage drops). The loot rows carry a
FieldLottery *name* (`Grass01`), which `economy.json.gz` already expands — so
this bundle stores the name and the backend joins at serve time, rather than
shipping every item list twice.

**The areas are deliberately UNNAMED.** Pocketpair never localised the random
dungeons: `NAME_Dungeon01..06` all ship the untranslated `en Text` marker and
the postfix row is literally "{DungeonName} Cave (Temporary)". The 33 named
"Sealed Realm" entries are the FIXED overworld dungeons — a different system,
already on the map. So each area travels with its id (`Meadow01`) and
`named: false`, and the UI says the game does not name these rather than
inventing names — the empty-work-suitability rule.

TestDebug areas are excluded and counted, never silently dropped.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import jsonout       # noqa: E402
import palpak        # noqa: E402
import uassettable   # noqa: E402

OUT = os.path.join(os.path.dirname(HERE), "backend", "data", "dungeons.json.gz")


def _table(pak, name: str) -> dict:
    path = next((f for f in pak.files if f.endswith(f"{name}.uasset")), None)
    if path is None:
        raise SystemExit(f"!! {name} not in the pak")
    return uassettable.read_table(pak, path)


def _enum_tail(value) -> str:
    s = str(value or "")
    return s.rsplit("::", 1)[-1] if "::" in s else s


def build() -> dict:
    pak = palpak.Pak()

    areas = _table(pak, "DT_DungeonSpawnAreaDataTable")
    levels = _table(pak, "DT_DungeonLevelDataTable")
    enemies = _table(pak, "DT_DungeonEnemySpawnDataTable")
    loot = _table(pak, "DT_DungeonItemLotteryDataTable")
    rewards = _table(pak, "DT_DungeonRewardSpawnerLotteryDataTable")
    wild = _table(pak, "DT_PalWildSpawner")

    # The wild table grouped by its SpawnerName COLUMN — the join the dungeon
    # rows actually use. Its row keys are a different id space.
    wild_by_name: dict[str, list[dict]] = {}
    for row in wild.values():
        name = str(row.get("SpawnerName") or "")
        if name:
            wild_by_name.setdefault(name, []).append(row)

    def roster(spawner_name: str) -> list[dict]:
        out = []
        for row in wild_by_name.get(spawner_name, []):
            for i in (1, 2, 3):
                species = str(row.get(f"Pal_{i}") or "None")
                npc = str(row.get(f"NPC_{i}") or "None")
                who = species if species != "None" else npc
                if who == "None":
                    continue
                out.append({
                    "id": who,
                    "isNpc": species == "None",
                    "levelMin": int(row.get(f"LvMin_{i}") or 0),
                    "levelMax": int(row.get(f"LvMax_{i}") or 0),
                    "countMin": int(row.get(f"NumMin_{i}") or 0),
                    "countMax": int(row.get(f"NumMax_{i}") or 0),
                    # Relative within this spawner group only — the habitat
                    # bundle's `weightIsWithinGroup` rule, same source.
                    "weight": float(row.get("Weight") or 0.0),
                })
        return out

    # Which areas the four content tables actually reference. Seven area rows
    # (Desert01, Volcano01, WorldTree01, ...) are stubs from an older naming
    # scheme, superseded by the rows the content keys on (Dessert001 — the
    # typo is Pocketpair's — Volcano001, ...). Nothing references them, so a
    # guide entry for one would be a dungeon with no enemies and no loot.
    referenced: set[str] = set()
    for table in (levels, enemies, loot, rewards):
        for row in table.values():
            referenced.add(str(row.get("SpawnAreaId") or ""))

    debug = [a for a in areas if str(a).startswith("TestDebug")]
    stubs = sorted(str(a) for a in areas
                   if not str(a).startswith("TestDebug")
                   and str(a) not in referenced)
    real = {a: r for a, r in areas.items()
            if not str(a).startswith("TestDebug") and str(a) in referenced}

    unjoined_spawners: list[str] = []
    out_areas: dict[str, dict] = {}
    for area_id in sorted(real):
        entry: dict = {
            # Pocketpair never named the random dungeons — see the module
            # docstring. `named: false` is the flag the UI keys its honesty on.
            "named": False,
            "levels": [], "enemies": [], "loot": [], "rewards": [],
        }
        out_areas[str(area_id)] = entry

    for row in levels.values():
        area = str(row.get("SpawnAreaId") or "")
        if area in out_areas:
            out_areas[area]["levels"].append({
                "levelName": str(row.get("LevelName") or ""),
                "weight": float(row.get("WeightInSpawnArea") or 0.0),
                "bonusExpRate": float(row.get("BonusExpRate") or 1.0),
            })

    for row in enemies.values():
        area = str(row.get("SpawnAreaId") or "")
        if area not in out_areas:
            continue
        spawner = str(row.get("SpawnerName") or "")
        pals = roster(spawner)
        if not pals:
            unjoined_spawners.append(spawner)
        out_areas[area]["enemies"].append({
            "rank": _enum_tail(row.get("RankType")),
            "weight": float(row.get("WeightInSpawnAreaAndRank") or 0.0),
            "spawnerName": spawner,
            "roster": pals,
        })

    for row in loot.values():
        area = str(row.get("SpawnAreaId") or "")
        if area in out_areas:
            out_areas[area]["loot"].append({
                "type": _enum_tail(row.get("Type")),
                # The name `economy.json.gz`'s lottery section expands; the
                # backend joins at serve time so item lists ship once.
                "lotteryName": str(row.get("ItemFieldLotteryName") or ""),
            })

    for row in rewards.values():
        area = str(row.get("SpawnAreaId") or "")
        if area in out_areas:
            out_areas[area]["rewards"].append({
                "type": _enum_tail(row.get("RewardSpawnerType")),
                "weight": float(row.get("Weight") or 0.0),
                "contentType": _enum_tail(row.get("SpawnerContentType")),
                "value": str(row.get("LotteryValue") or ""),
            })

    if unjoined_spawners:
        # 38 of 38 joined when this was written. A drop means the wild table's
        # SpawnerName column moved, and a guide with silently-empty rosters
        # reads as dungeons with no enemies.
        raise SystemExit(
            f"!! {len(unjoined_spawners)} enemy spawner(s) resolve to no wild "
            f"rows: {sorted(set(unjoined_spawners))[:5]} — refusing")

    empty = [a for a, e in out_areas.items() if not e["enemies"] and not e["loot"]]
    if empty:
        raise SystemExit(f"!! referenced areas with no content: {empty} — refusing")
    return {
        "_note": (
            "Random-dungeon spawn areas. Pocketpair does not name these "
            "(NAME_Dungeon01..06 ship the untranslated marker); the 33 named "
            "'Sealed Realm' dungeons are the FIXED overworld ones, a separate "
            "system. Enemy weight is relative within its spawner group only. "
            "'BaseInsurance' reward rows key no dungeon area — they are the "
            "base-raid insurance spawner, excluded here."
        ),
        "areas": out_areas,
        "debugAreasExcluded": len(debug),
        "unusedAreaRows": stubs,
    }


def main() -> int:
    data = build()
    jsonout.write_json(OUT, data)
    areas = data["areas"]
    print(f"wrote {OUT}")
    print(f"  {len(areas)} areas ({data['debugAreasExcluded']} debug excluded); "
          f"{sum(len(a['enemies']) for a in areas.values())} enemy groups, "
          f"{sum(len(a['loot']) for a in areas.values())} loot rows, "
          f"{sum(len(a['rewards']) for a in areas.values())} reward rows")
    if data["unusedAreaRows"]:
        print(f"  unused area-row stubs excluded: {len(data['unusedAreaRows'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
