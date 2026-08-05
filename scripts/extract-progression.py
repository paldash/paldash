#!/usr/bin/env python3
"""
What the relics you collected actually did, where the quests are, what the
regions are called, and what is in each dungeon.

Phase 1.6 of `docs/PLAN.md`. Six tables, all small, all feeding the progression
tab (#47) and the effigy panel (#61):

    DT_PlayerStatusRankMasterDataTable  279  relic ranks and their effects
    DT_GainStatusPointsItem              11  the stat elixirs
    DT_WorldMapAreaData                 123  region ids
    DT_PalQuestLocationData             166  quest world positions
    DT_DungeonSpawnAreaDataTable         23  dungeon names
    DT_DungeonLevelDataTable             15  dungeon layouts and EXP bonus

TWO THINGS THIS UNBLOCKS THAT ARE ALREADY HALF-BUILT:

**The map shows all 396 effigies and which a player has found, and never says
what finding them achieved.** `PlayerStatusRankMasterDataTable` turns "you have
40 relics" into "rank N in Capture Power, +X%, and the next rank needs M more",
plus the respec cost, which is stated nowhere in the dashboard today.

**`areasFound` is a flag map with opaque keys.** 123 named regions turns it into
"you have found 47 of 123 areas". The names themselves are message ids —
`REGION_Desert_1` — so a display name needs the text tables, which are a separate
job; the ids are bundled as they are rather than half-resolved.

THE VERIFICATION is the cell grid again, on the 166 quest positions. It is a
weaker instrument here than it was for 8,253 spawn points — a small sample makes
a coincidence cheaper — so the controls matter more, not less, and the script
refuses if either matches as well as the real size.

TWO COLUMNS THAT WORK IN OPPOSITE DIRECTIONS, and getting either backwards
produces a confident wrong number rather than an error:

  * **`RequiredRelicNum` is the cost of that rank, not a running total.**
    `HungerReduction` charges 1 relic for each of its 20 ranks — read
    cumulatively, rank 20 would cost one relic. `MoveSpeed` runs 1, then 3 for
    seventy-eight ranks, then 4: **287 relics** for all 92.
  * **`EffectRate` is already cumulative** — the total at that rank, not an
    increment. HungerReduction reads 2.5, 5.0, 7.5 across its first three.

`CapturePower` IS THE ONE LINE WITH NO RATE. All 15 of its ranks are 0.0 while
the other twelve types carry real values, so its effect is expressed somewhere
other than this column. A caller must not render "+0%" for it; `hasEffectRate`
in `gamedata.relic_rank` distinguishes the two cases.

Usage:  python3 scripts/extract-progression.py [--verify]
Output: backend/data/progression.json.gz
"""

from __future__ import annotations

import os
import re
import struct
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import palpak            # noqa: E402
import uassettable       # noqa: E402
from jsonout import write_json  # noqa: E402

try:
    import l10n  # noqa: E402
except ImportError:  # pragma: no cover - the client pak is optional here
    l10n = None

OUT = os.path.join(os.path.dirname(HERE), "backend", "data", "progression.json.gz")

CELL_SIZE = 25600
CONTROLS = (12800, 51200)
UNSET = {"", "None", None}


def _enum(value) -> str:
    text = str(value or "")
    return text.rsplit("::", 1)[-1] if "::" in text else text


def _read(pak, name: str, vectors: bool = False) -> dict:
    path = next((p for p in pak.files if p.endswith(name + ".uasset")), None)
    if path is None:
        raise SystemExit(f"{name} is not in this pak — did the game update?")
    if not vectors:
        return uassettable.read_table(pak, path)

    original = uassettable._value

    def patched(r, typ, size, extra):
        if typ == "StructProperty" and extra.get("struct") == "Vector" and size == 24:
            x, y, z = struct.unpack_from("<3d", r.b, r.o)
            r.o += 24
            return {"x": x, "y": y, "z": z}
        return original(r, typ, size, extra)

    uassettable._value = patched
    try:
        return uassettable.read_table(pak, path)
    finally:
        uassettable._value = original


def occupied_cells(pak) -> set:
    out = set()
    for path in pak.files:
        m = re.search(r"MainGrid_L0_X(-?\d+)_Y(-?\d+)", path)
        if m:
            out.add((int(m.group(1)), int(m.group(2))))
    return out


# The pairs a wrong positional offset could not survive. Not the whole mapping —
# asserting all 13 would just restate the join. These four are distinctive and
# spread across the range, so an off-by-one breaks at least one of them.
_RELIC_NAME_ANCHORS = {
    "CapturePower": "Capture Power",
    "StaminaReduction": "Endurance",
    "SphereHoming": "Sphere Tracking",
    "MoveSpeed": "Movement Speed",
}


def _relic_text() -> tuple[dict[int, str], dict[int, str]]:
    """`({index: name}, {index: description})` from `BUILDUP_PLAYER_STATUS_NN`."""
    if l10n is None:
        return {}, {}
    try:
        ui = l10n.strings("DT_UI_Common_Text_Common", "en")
    except Exception as exc:  # noqa: BLE001
        print(f"   (no relic names: {exc})", file=sys.stderr)
        return {}, {}

    def indexed(prefix: str) -> dict[int, str]:
        out = {}
        for key, value in ui.items():
            if key.startswith(prefix) and key[len(prefix):].isdigit():
                out[int(key[len(prefix):])] = value
        return out

    return indexed("BUILDUP_PLAYER_STATUS_"), indexed("BUILDUP_PLAYER_STATUS_DESC_")


def _verify_relic_names(ordered: list[str], meta: dict) -> None:
    """Refuse a positional join that has drifted, rather than mislabel a stat."""
    if all(meta[k]["nameIsInternal"] for k in ordered):
        print("   (relic names unavailable — ids will stand in)", file=sys.stderr)
        return
    if len(ordered) != 13:
        raise SystemExit(f"!! expected 13 relic lines, found {len(ordered)}")
    for kind, expected in _RELIC_NAME_ANCHORS.items():
        got = (meta.get(kind) or {}).get("name")
        if got != expected:
            raise SystemExit(
                f"!! relic name join drifted: {kind} resolved to {got!r}, "
                f"expected {expected!r}. The BUILDUP_PLAYER_STATUS_NN order no "
                f"longer matches the table's row order — do NOT relax this, a "
                f"mislabelled stat line is unverifiable once shipped."
            )


def build(pak=None) -> dict:
    pak = pak or palpak.Pak()

    # ── Relic statue lines ──
    #
    # WHAT THE GAME CALLS EACH LINE. The table gives only an internal
    # `RelicType`, so before this a panel could offer "StatusAilmentResist".
    # The names are in the client pak's L10N overrides as
    # `BUILDUP_PLAYER_STATUS_00..12`, with `..._DESC_NN` beside them.
    #
    # **THEY ARE INDEXED, NOT KEYED**, so the join is positional — which is
    # exactly the kind of pairing this project refuses elsewhere. It is
    # acceptable here only because it is *verified*: all 13 pair semantically,
    # and several are distinctive enough that a wrong offset could not survive
    # them (00 "Capture Power"/CapturePower, 08 "Endurance"/StaminaReduction,
    # 09 "Sphere Tracking"/SphereHoming, 12 "Movement Speed"/MoveSpeed).
    # `_verify_relic_names` asserts those anchors and the count; a game update
    # that reorders the enum fails the build rather than mislabelling a stat.
    relic_names, relic_descs = _relic_text()

    relics: dict[str, list] = defaultdict(list)
    for row in _read(pak, "DT_PlayerStatusRankMasterDataTable").values():
        kind = _enum(row.get("RelicType"))
        if kind in UNSET:
            continue
        relics[kind].append({
            "rank": int(row.get("Rank") or 0),
            # The cost OF THIS RANK. Not a running total — see the docstring.
            "requiredRelics": int(row.get("RequiredRelicNum") or 0),
            # Cumulative: the total effect at this rank, not an increment.
            # 0.0 throughout for CapturePower alone — see the module docstring.
            "effectRate": float(row.get("EffectRate") or 0.0),
            "resetCost": int(row.get("ResetRequiredMoney") or 0),
        })
    for rows in relics.values():
        rows.sort(key=lambda r: r["rank"])

    # Positional join, in first-appearance order of the table's own rows.
    ordered = list(relics.keys())
    relic_meta = {}
    for index, kind in enumerate(ordered):
        name = relic_names.get(index)
        relic_meta[kind] = {
            "name": name or kind,
            "nameIsInternal": name is None,
            "description": (relic_descs.get(index) or "").replace("\r\n", "\n").strip(),
        }
    _verify_relic_names(ordered, relic_meta)

    # ── Stat elixirs ──
    elixirs = {}
    for key, row in _read(pak, "DT_GainStatusPointsItem").items():
        gains = {
            name: int(row.get(field) or 0)
            for name, field in (
                ("hp", "MaxHP"), ("stamina", "MaxSP"), ("attack", "Power"),
                ("workSpeed", "WorkSpeed"), ("weight", "MaxInventoryWeight"),
            )
            if int(row.get(field) or 0)
        }
        if gains:
            elixirs[str(key)] = gains

    # ── Regions ──
    # `MsgID` is a text-table key (`REGION_Desert_1`), not a display name.
    # Carried unresolved rather than humanised: inventing "Desert 1" here would
    # be a second source of truth against the localisation tables.
    areas = {
        str(key): str(row.get("MsgID") or "")
        for key, row in _read(pak, "DT_WorldMapAreaData").items()
    }

    # ── Quests ──
    quests = {}
    for key, row in _read(pak, "DT_PalQuestLocationData", vectors=True).items():
        pos = row.get("Position")
        if not isinstance(pos, dict) or "x" not in pos:
            continue
        quests[str(key)] = {
            "x": round(float(pos["x"]), 1),
            "y": round(float(pos["y"]), 1),
            "z": round(float(pos["z"]), 1),
            # -1 means "no radius", which is most of them.
            "range": float(row.get("Range") or -1.0),
        }

    # ── Dungeons ──
    dungeons = {
        str(key): {
            "nameTextId": str(row.get("DungeonNameTextId") or ""),
            "postfixTextId": str(row.get("PostfixTextId") or ""),
            "levels": [],
        }
        for key, row in _read(pak, "DT_DungeonSpawnAreaDataTable").items()
    }
    for row in _read(pak, "DT_DungeonLevelDataTable").values():
        area = str(row.get("SpawnAreaId") or "")
        if area not in dungeons:
            continue
        dungeons[area]["levels"].append({
            "levelName": str(row.get("LevelName") or ""),
            "weight": float(row.get("WeightInSpawnArea") or 0.0),
            "bonusExpRate": float(row.get("BonusExpRate") or 1.0),
        })

    return {
        "relicRanks": dict(relics),
        "relicTypes": relic_meta,
        "statusItems": elixirs,
        "areas": areas,
        "quests": quests,
        "dungeons": dungeons,
    }


def grid_check(pak, quests) -> dict:
    cells = occupied_cells(pak)
    out = {}
    for size in (CELL_SIZE, *CONTROLS):
        out[size] = sum(
            1 for q in quests.values()
            if (int(q["x"]) // size, int(q["y"]) // size) in cells
        )
    return out


def main() -> int:
    pak = palpak.Pak()
    data = build(pak)
    quests = data["quests"]

    checks = grid_check(pak, quests)
    real = checks[CELL_SIZE]
    best_control = max(checks[c] for c in CONTROLS)

    if real != len(quests):
        print(
            f"REFUSING: {len(quests) - real} of {len(quests)} quest positions "
            f"fall outside every occupied cell at {CELL_SIZE}.",
            file=sys.stderr,
        )
        return 2
    if best_control >= real:
        # 166 points is a small sample, so a control matching is cheap and the
        # check has to be held to a higher bar rather than a lower one.
        print(
            "REFUSING: a wrong cell size matches as well as the right one. With "
            "only 166 points a coincidence is cheap, so this proves nothing.",
            file=sys.stderr,
        )
        return 3

    if "--verify" in sys.argv:
        print(f"verified {real}/{len(quests)} quest positions on occupied cells; "
              f"controls {dict((c, checks[c]) for c in CONTROLS)}")
        return 0

    write_json(OUT, data)
    print(f"wrote {OUT}")
    print(f"  {len(data['relicRanks'])} relic types, "
          f"{sum(len(v) for v in data['relicRanks'].values())} ranks")
    print(f"  {len(data['statusItems'])} stat elixirs")
    print(f"  {len(data['areas'])} named regions")
    print(f"  {len(quests)} quest positions — all on occupied cells at "
          f"{CELL_SIZE}, controls {dict((c, checks[c]) for c in CONTROLS)}")
    print(f"  {len(data['dungeons'])} dungeon areas, "
          f"{sum(len(d['levels']) for d in data['dungeons'].values())} layouts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
