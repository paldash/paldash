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


def _key(value) -> str:
    """Unwrap an `FName` cell — `{"Key": "AncientParts2"}` -> `AncientParts2`."""
    if isinstance(value, dict):
        value = value.get("Key")
    return str(value or "")


def _eggs(value) -> dict:
    """
    `EggPalIDAndWeight` -> `{"eggWeights": [...], "eggWeightsRead": bool}`.

    Measured across all 11 raids: two entries each, the `BOSS_` (alpha) form at
    **0.1** and the ordinary form at **0.9**. Weights are the game's own floats
    and are reported as such — they sum to 1.0 here, but nothing in the table
    says they must, so they are not renormalised into percentages that would be
    this project's arithmetic rather than Pocketpair's.

    **A non-dict is reported as unread, never as empty.** If a future reader
    leaves the map opaque again, the string falls through to `eggWeightsRead:
    False` — which is the distinction the previous note was protecting and is
    worth keeping now that the happy path works.
    """
    # **AN EMPTY MAP IS AN ANSWER; A NON-MAP IS NOT.** `YakushimaBoss002` and
    # its `_2` ship `{}` — the game states those two raids have no egg table —
    # while a string here means the reader left the property opaque. Collapsing
    # both into `eggWeightsRead: False` was the first version's mistake and it
    # is the same "nothing" versus "we could not ask" distinction the missing
    # ban list and the unparsed world already turn on.
    if not isinstance(value, dict):
        return {"eggWeights": [], "eggWeightsRead": False}
    if not value:
        return {"eggWeights": [], "eggWeightsRead": True}
    eggs = []
    for raw_key, weight in value.items():
        species = _key(raw_key)
        if not species:
            continue
        eggs.append({
            "speciesId": species,
            "weight": float(weight or 0.0),
            # The alpha form, which is the whole reason a raid egg is interesting.
            "isBoss": species.startswith("BOSS_"),
        })
    if not eggs:
        # Entries present but none resolved — that IS a reader problem.
        return {"eggWeights": [], "eggWeightsRead": False}
    return {"eggWeights": sorted(eggs, key=lambda e: -e["weight"]),
            "eggWeightsRead": True}


def _items(row: dict, prefix: str) -> list:
    """
    A `SuccessItemList`-shaped array, or [].

    **The column is `ItemName`, and it took a whole shipped bundle to notice.**
    The first version read `ItemId` / `StaticItemId` — the spellings used by the
    drop and lottery tables — found neither, and produced an empty list for every
    raid boss in the game. Nothing errored, and "this boss drops nothing" is a
    perfectly ordinary-looking answer, so it survived until someone asked what
    Bellanoir actually gives you.

    It is also an `FName`, so `str()` on it yields `"{'Key': 'AncientParts2'}"` —
    the same trap the Pal-shop rosters fell into. `_key` is the one unwrapper.

    `Rate` is carried because it is a real per-item drop chance, unlike a lottery
    weight: these entries are independent rolls on one success, not shares of a
    slot.
    """
    out = []
    for entry in row.get(prefix) or []:
        if not isinstance(entry, dict):
            continue
        item_id = _key(
            entry.get("ItemName") or entry.get("ItemId") or entry.get("StaticItemId")
        )
        if item_id in UNSET:
            continue
        out.append({
            "itemId": item_id,
            "rate": float(entry.get("Rate") or 0.0),
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
            # **THIS FIELD WAS UNREADABLE UNTIL 2026-08-07 AND IS NOT ANY MORE.**
            # The note it replaces said `EggPalIDAndWeight` is a MapProperty that
            # `uassettable` decodes none of, so the honest thing was to report the
            # field unread rather than ship `[]` — because an empty egg table
            # reads as "this raid drops no eggs", a claim about the game rather
            # than about the reader. That was right, and the premise expired the
            # moment the map decoder landed.
            #
            # Before that it was worse: the original code iterated the map as a
            # list, which walked the *characters* of the string
            # `"<MapProperty 98B>"`, matched no dict, and produced an empty table
            # for every boss with no error at all.
            #
            # Keys are `FName` cells, so `{'Key': 'BOSS_NightLady'}` — `_key` is
            # the one unwrapper, and skipping it yields ids that serialise
            # perfectly and resolve to nothing.
            **_eggs(row.get("EggPalIDAndWeight")),
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

    # **THE CHECK THAT WOULD HAVE CAUGHT THE LAST BUG.** `_items` read the wrong
    # column name for a whole shipped bundle and produced an empty reward list
    # for every boss in the game — no error, and "drops nothing" looks like an
    # ordinary answer. A raid the game does not reward is not a thing, so an
    # empty result across the board is a reader fault by definition.
    rewarded = [b for b in data["bosses"].values() if b["rewards"] or b["rewardsAnyOne"]]
    if not rewarded:
        print("REFUSING: not one raid boss carries a reward. Every raid rewards "
              "something, so this is the column name being wrong rather than the "
              "game shipping empty tables.", file=sys.stderr)
        return 3

    # And every item named must exist, the same hard half `extract-economy.py`
    # applies: the catalogue is complete at 2,466, so a miss means drift.
    items = {
        r["itemId"] for b in data["bosses"].values()
        for r in (*b["rewards"], *b["rewardsAnyOne"])
    }
    missing_items = sorted(i for i in items if not gamedata.item(i))
    if missing_items:
        print(f"REFUSING: {len(missing_items)} reward items resolve to nothing: "
              f"{missing_items[:5]}", file=sys.stderr)
        return 4

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
    print(f"  {len(rewarded)} of {len(data['bosses'])} carry a reward table, "
          f"{len(items)} distinct items, all resolving")
    read = [b for b in data["bosses"].values() if b["eggWeightsRead"]]
    print(f"  egg weights read for {len(read)} of {len(data['bosses'])} raids "
          f"(MapProperty decoding landed 2026-08-07)")
    if read:
        alpha = {round(e["weight"], 3) for b in read for e in b["eggWeights"] if e["isBoss"]}
        print(f"    alpha-form weights observed: {sorted(alpha)}")
    if unknown:
        print(f"  advisory: {len(unknown)} of {len(species)} forms are the `_2` "
              f"difficulty variants, which the bundled character tables do not "
              f"carry: {unknown[:3]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
