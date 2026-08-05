#!/usr/bin/env python3
"""
Base raids: who attacks, at what grade, in which biome, and what they drop.

Phase 1.8 of `docs/PLAN.md`. Nothing in the dashboard mentions base raids today,
and four tables describe them completely:

    DT_PalInvader            143  attacker groups, biome, grade band, weight
    DT_PalInvaderReward       76  what each group drops
    DT_PalInvaderCancelCost   80  the money to call one off
    DT_PalVisitorNPC          48  friendly visitors, same shape

WHAT THIS DOES NOT ESTABLISH, and it is the thing a UI would most want.
`InvadeGradeMin`/`Max` bound a raid to a "grade", and **nothing here says what a
grade is in save terms** — base level, guild level and player level are all
plausible and none is confirmed. So this bundle is a static reference: "these
are the raid groups, their biomes, their grade bands and their loot". Any
per-base claim ("your base will be raided by X") needs that mapping established
first, and inventing it would be the kind of guess `basesupply` refuses to make.

Recorded rather than worked around, because a reference table is genuinely
useful on its own and a wrong per-base forecast is not.

SOME COLUMNS DO NOT DECODE and are skipped rather than guessed. `DT_PalInvader`
rows carry a few opaque entries (`1_510`, `1_3`) where `uassettable` could not
walk a struct; the fields this reads — group, biome, grade band, weight,
character ids — come through cleanly. `mine-datatables.py --check` will report
it if that ever changes.

VERIFICATION, and the direction matters. Every reward item must resolve in the
catalogue, and **every attacker group must have a reward table** — an attacker
without one is a raid that drops nothing, a join failure with a consequence.

The converse is *not* checked, because the game ships 32 reward tables with no
attacker: rewards exist for the mainland biomes (Basic, Desert, Forest, Volcano)
while `DT_PalInvader` carries only the island groups (Sakurajima, Sorajima,
Yamishima, Snow). Measured 44 of 44 attackers rewarded, 0 unrewarded. Checking
the harmless direction first would have blocked this extraction over the game
having spare data.

Usage:  python3 scripts/extract-invaders.py [--verify]
Output: backend/data/invaders.json.gz
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import palpak            # noqa: E402
import uassettable       # noqa: E402
from jsonout import write_json  # noqa: E402

OUT = os.path.join(os.path.dirname(HERE), "backend", "data", "invaders.json.gz")
UNSET = {"", "None", None, "no"}
DROP_SLOTS = 5


def _enum(value) -> str:
    text = str(value or "")
    return text.rsplit("::", 1)[-1] if "::" in text else text


def _read(pak, name: str) -> dict:
    path = next((p for p in pak.files if p.endswith(name + ".uasset")), None)
    if path is None:
        raise SystemExit(f"{name} is not in this pak — did the game update?")
    return uassettable.read_table(pak, path)


def build(pak=None) -> dict:
    pak = pak or palpak.Pak()

    groups: dict[str, list] = defaultdict(list)
    for key, row in _read(pak, "DT_PalInvader").items():
        group = str(row.get("GroupName") or "")
        if group in UNSET:
            continue
        groups[group].append({
            "id": str(key),
            "biome": _enum(row.get("BiomeID")),
            # A band, and what the grade *is* in save terms is not established.
            "gradeMin": int(row.get("InvadeGradeMin") or 0),
            "gradeMax": int(row.get("InvadeGradeMax") or 0),
            "weight": float(row.get("Weight") or 0.0),
            "exp": int(row.get("Exp") or 0),
            "waveLevelOffset": int(row.get("WaveLevelOffset") or 0),
            "conditionBuildObjectId": str(row.get("ConditionBuildObjectId") or ""),
        })

    rewards = {}
    for row in _read(pak, "DT_PalInvaderReward").values():
        group = str(row.get("GroupName") or "")
        if group in UNSET:
            continue
        items = []
        for n in range(1, DROP_SLOTS + 1):
            item_id = str(row.get(f"ItemId{n}") or "")
            rate = float(row.get(f"Rate{n}") or 0.0)
            if item_id in UNSET or rate <= 0:
                continue
            items.append({
                "itemId": item_id, "rate": rate,
                "min": int(row.get(f"Min{n}") or 0),
                "max": int(row.get(f"Max{n}") or 0),
            })
        if items:
            rewards[group] = items

    visitors = {}
    for key, row in _read(pak, "DT_PalVisitorNPC").items():
        visitors[str(key)] = {
            "biome": _enum(row.get("BiomeID")),
            "gradeMin": int(row.get("InvadeGradeMin") or 0),
            "gradeMax": int(row.get("InvadeGradeMax") or 0),
            "weight": float(row.get("Weight") or 0.0),
            "isSquad": bool(row.get("IsSquad")),
        }

    cancel = sorted(
        {int(r.get("Money") or 0) for r in _read(pak, "DT_PalInvaderCancelCost").values()}
    )

    return {
        "groups": dict(groups),
        "rewards": rewards,
        "visitors": visitors,
        "cancelCosts": cancel,
        # Said in the payload, not only in a docstring: a client must not turn a
        # grade band into a per-base prediction.
        "gradeMeaningKnown": False,
    }


def main() -> int:
    pak = palpak.Pak()
    data = build(pak)

    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "backend"))
    import gamedata  # noqa: E402

    unknown_items = sorted({
        i["itemId"] for items in data["rewards"].values() for i in items
        if not gamedata.item(i["itemId"])
    })
    if unknown_items:
        print(f"REFUSING: reward items not in the catalogue: {unknown_items[:5]}",
              file=sys.stderr)
        return 2

    # THE CHECK POINTS THIS WAY ROUND DELIBERATELY. An attacker with no reward
    # table is a raid that drops nothing — a join failure with a visible
    # consequence. A reward table with no attacker is an unused row, which the
    # game has 32 of: every Grade1-3 reward exists for the mainland biomes
    # (Basic, Desert, Forest, Volcano) while `DT_PalInvader` only carries the
    # island groups (Sakurajima, Sorajima, Yamishima, Snow). Measured: 44 of 44
    # attackers have rewards, 0 attackers lack one.
    #
    # My first version refused on the harmless direction and would have blocked
    # the extraction over the game shipping spare data.
    unrewarded = sorted(set(data["groups"]) - set(data["rewards"]))
    if unrewarded:
        print(f"REFUSING: {len(unrewarded)} attacker groups have no reward table "
              f"— the join has drifted: {unrewarded[:5]}", file=sys.stderr)
        return 3

    spare = sorted(set(data["rewards"]) - set(data["groups"]))

    if "--verify" in sys.argv:
        print(f"verified {len(data['groups'])} attacker groups; every reward item "
              f"resolves and every attacker has a reward table "
              f"({len(spare)} spare reward tables, which is fine)")
        return 0

    write_json(OUT, data)
    print(f"wrote {OUT}")
    print(f"  {len(data['groups'])} attacker groups, "
          f"{sum(len(v) for v in data['groups'].values())} entries")
    print(f"  {len(data['rewards'])} reward tables")
    print(f"  {len(data['visitors'])} friendly visitor types")
    print(f"  cancel costs {data['cancelCosts'][:1]}..{data['cancelCosts'][-1:]}")
    if spare:
        print(f"  {len(spare)} reward tables have no attacker group — mainland "
              "biomes the invader table does not carry. Harmless; the check runs "
              "the other way.")
    print("  NOTE: what InvadeGrade means in save terms is NOT established — "
          "this is a reference table, not a per-base forecast")
    return 0


if __name__ == "__main__":
    sys.exit(main())
