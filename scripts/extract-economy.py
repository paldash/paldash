#!/usr/bin/env python3
"""
Where every item comes from: recipes, Pal drops, loot tables, shops, food effects
and what production structures yield.

Phase 1.3 of `docs/PLAN.md`, and it closes the two oldest extraction tasks (#35
item drops, #36 technology recipes and shop stock) which were both filed against
the *client* pak and were impossible there. The client's properties are
unversioned, so a DataTable gives only its name table. The server pak's are
tagged and every one of these decodes completely.

WHAT COMES OUT

    recipes     1,414  product, count, work amount, up to 5 materials
    drops       1,044  per species per level band, item + rate + min/max
    lottery     8,777  chest and field loot with real WeightInSlot values
    shops          38  merchant stock, price overrides
    palShops        8  Pal merchants: roster and level range
    food           53  what a cooked dish actually does, and for how long
    production     16  what a production structure yields and how fast

THREE THINGS MEASURED WHILE BUILDING IT, all worth recording

**`WorkableAttribute` is 0 on all 1,414 recipe rows.** It looked like the link
from a recipe to the work suitability that crafts it, which would have paired
with `DT_MapObjectAssignData`. It is not — the column is present and uniformly
empty. Which bench crafts what therefore still has no source; do not infer it
from the recipe table.

**Drops are level-banded, not per-level.** `Level` takes exactly the values
0, 10, 20 … 80, so a row is "this species at level 30-39" and not "at level 30".
894 species have a table. Interpolating between bands would be inventing.

**One food row does not decode and is dropped rather than guessed at.**
`BaconEggs_53` comes back with name-table strings where its property names should
be, so its interior is unreadable — 53 of 54 rows are clean. It is reported, not
silently omitted, for the same reason `mine-datatables.py` lists its refusals.

VERIFICATION, and it is deliberately asymmetric. Every **item** id this bundle
names — products, materials, drops, loot, shop stock, food — must resolve in the
bundled catalogue, and a miss refuses the write: the catalogue is complete at
2,466, so an unresolvable item means the projection drifted rather than that the
game added content.

Unknown **species** are an advisory instead. 35 of the 894 drop-table species are
not in the bundled character tables and every one is real — `_BossRush` arena
variants, quest NPCs, plain humans. AGENTS.md already records that even the
reference world contains NPCs no bundled table names, so refusing here would
block this extraction over a gap in a different bundle.

Usage:  python3 scripts/extract-economy.py [--verify]
Output: backend/data/economy.json.gz
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

OUT = os.path.join(os.path.dirname(HERE), "backend", "data", "economy.json.gz")

# The game's own "this slot is unused".
UNSET = {"", "None", None}

# How many material / item slots each table carries. Fixed-width columns rather
# than arrays, which is why they are enumerated rather than iterated.
MATERIAL_SLOTS = 5
DROP_SLOTS = 5
FOOD_SLOTS = 2


def _enum(value) -> str:
    text = str(value or "")
    return text.rsplit("::", 1)[-1] if "::" in text else text


def _read(pak, name: str) -> dict:
    path = next((p for p in pak.files if p.endswith(name + ".uasset")), None)
    if path is None:
        raise SystemExit(f"{name} is not in this pak — did the game update?")
    return uassettable.read_table(pak, path)


def _recipes(pak) -> dict:
    out = {}
    for row in _read(pak, "DT_ItemRecipeDataTable").values():
        product = str(row.get("Product_Id") or "")
        if product in UNSET:
            continue
        materials = []
        for n in range(1, MATERIAL_SLOTS + 1):
            item_id = str(row.get(f"Material{n}_Id") or "")
            count = int(row.get(f"Material{n}_Count") or 0)
            if item_id not in UNSET and count > 0:
                materials.append({"itemId": item_id, "count": count})
        unlock = str(row.get("UnlockItemID") or "")
        out[product] = {
            "productId": product,
            "count": int(row.get("Product_Count") or 1),
            "workAmount": float(row.get("WorkAmount") or 0.0),
            "materials": materials,
            # The schematic that unlocks it, where one exists.
            "unlockItemId": "" if unlock in UNSET else unlock,
        }
        # `WorkableAttribute` is deliberately not carried: measured 0 on every
        # row, so bundling it would imply a link that is not in the data.
    return out


def _drops(pak) -> dict:
    out: dict[str, list] = defaultdict(list)
    for row in _read(pak, "DT_PalDropItem").values():
        species = str(row.get("CharacterID") or "")
        if species in UNSET:
            continue
        items = []
        for n in range(1, DROP_SLOTS + 1):
            item_id = str(row.get(f"ItemId{n}") or "")
            rate = float(row.get(f"Rate{n}") or 0.0)
            if item_id in UNSET or rate <= 0:
                continue
            items.append({
                "itemId": item_id,
                # A percentage, as the table stores it.
                "rate": rate,
                # `min` is lowercase in the table and `Max` is not. Reading
                # `Min` finds nothing and yields a silent zero.
                "min": int(row.get(f"min{n}") or 0),
                "max": int(row.get(f"Max{n}") or 0),
            })
        if not items:
            continue
        out[species].append({
            # The BAND this row covers, not an exact level: the column only ever
            # holds 0, 10, 20 … 80.
            "levelFrom": int(row.get("Level") or 0),
            "items": items,
        })
    for rows in out.values():
        rows.sort(key=lambda r: r["levelFrom"])
    return dict(out)


def _lottery(pak) -> dict:
    out: dict[str, list] = defaultdict(list)
    for row in _read(pak, "DT_ItemLotteryDataTable").values():
        field = str(row.get("FieldName") or "")
        item_id = str(row.get("StaticItemId") or "")
        if field in UNSET or item_id in UNSET:
            continue
        out[field].append({
            "slot": int(row.get("SlotNo") or 0),
            # The real drop rate this project once recorded as unavailable.
            "weight": float(row.get("WeightInSlot") or 0.0),
            "itemId": item_id,
            "min": int(row.get("MinNum") or 0),
            "max": int(row.get("MaxNum") or 0),
            "grade": _enum(row.get("TreasureBoxGrade")),
        })
    for rows in out.values():
        rows.sort(key=lambda r: (r["slot"], -r["weight"]))
    return dict(out)


def _shops(pak) -> dict:
    out = {}
    for key, row in _read(pak, "DT_ItemShopCreateData").items():
        products = []
        for entry in row.get("productDataArray") or []:
            if not isinstance(entry, dict):
                continue
            item_id = str(entry.get("StaticItemID") or "")
            if item_id in UNSET:
                continue
            products.append({
                "itemId": item_id,
                # 0 means "use the item's own price" rather than free.
                "overridePrice": int(entry.get("OverridePrice") or 0),
                "count": int(entry.get("ProductNum") or 1),
                "stock": int(entry.get("Stock") or 0),
                "type": _enum(entry.get("ProductType")),
            })
        if products:
            out[str(key)] = products
    return out


def _pal_shops(pak) -> dict:
    out = {}
    for key, row in _read(pak, "DT_PalShopCreateData").items():
        species = [str(s) for s in (row.get("CharacterIDArray") or []) if str(s) not in UNSET]
        if not species:
            continue
        out[str(key)] = {
            "species": species,
            "count": int(row.get("CharacterNum") or 0),
            "levelMin": int(row.get("MinCharacterLevel") or 0),
            "levelMax": int(row.get("MaxCharacterLevel") or 0),
        }
    return out


def _food(pak) -> tuple[dict, list]:
    out, refused = {}, []
    for key, row in _read(pak, "DT_StatusEffectFood").items():
        # A row whose property names did not decode comes back carrying name
        # table strings instead. Reported rather than guessed at.
        if "EffectTime" not in row or "EffectType1" not in row:
            refused.append(str(key))
            continue
        effects = []
        for n in range(1, FOOD_SLOTS + 1):
            effect = _enum(row.get(f"EffectType{n}"))
            if effect in UNSET:
                continue
            effects.append({
                "type": effect,
                "value": int(row.get(f"EffectValue{n}") or 0),
                "interval": int(row.get(f"Interaval{n}") or 0),  # the game's typo
            })
        if effects:
            out[str(key)] = {
                "itemId": str(key),
                "durationSeconds": int(row.get("EffectTime") or 0),
                "effects": effects,
            }
    return out, refused


def _production(pak) -> dict:
    out = {}
    for key, row in _read(pak, "DT_MapObjectItemProductDataTable").items():
        product = str(row.get("Product_Id") or "")
        if product in UNSET:
            continue
        out[str(key)] = {
            "productId": product,
            "requiredWork": float(row.get("RequiredWorkAmount") or 0.0),
            # Non-zero means it ticks along without a Pal assigned.
            "autoWorkPerSecond": float(row.get("AutoWorkAmountBySec") or 0.0),
        }
    return out


def build(pak=None) -> tuple[dict, dict]:
    pak = pak or palpak.Pak()
    food, food_refused = _food(pak)
    data = {
        "recipes": _recipes(pak),
        "drops": _drops(pak),
        "lottery": _lottery(pak),
        "shops": _shops(pak),
        "palShops": _pal_shops(pak),
        "food": food,
        "production": _production(pak),
    }
    return data, {"foodRefused": food_refused}


def verify(data: dict) -> list[str]:
    """
    Every id named must exist. A drifted projection starts naming things that do
    not, which a row count would never catch.
    """
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "backend"))
    import gamedata  # noqa: E402

    problems = []

    def check_items(ids, label):
        missing = sorted({i for i in ids if not gamedata.item(i)})
        if missing:
            problems.append(f"{label}: {len(missing)} unknown items, e.g. {missing[:5]}")

    check_items(data["recipes"], "recipe products")
    check_items(
        (m["itemId"] for r in data["recipes"].values() for m in r["materials"]),
        "recipe materials",
    )
    check_items(
        (i["itemId"] for rows in data["drops"].values() for r in rows for i in r["items"]),
        "drops",
    )
    check_items(
        (i["itemId"] for rows in data["lottery"].values() for i in rows), "lottery"
    )
    check_items((p["itemId"] for rows in data["shops"].values() for p in rows), "shops")
    check_items(data["food"], "food")
    check_items((p["productId"] for p in data["production"].values()), "production")

    return problems


def unknown_drop_species(data: dict) -> list[str]:
    """
    Drop-table species the bundled character tables do not list.

    **Advisory, never a refusal**, and that is the same call `palcheck` makes for
    the same reason: the bundled tables are *known* incomplete. 35 of the 894
    species here are absent, and every one is real — `_BossRush` arena variants
    of known Pals (`BOSS_Horus_BossRush`), quest NPCs
    (`BOSS_Hunter_Fat_GatlingGun_Quest_StrongOldMan`) and plain humans
    (`BOSS_Female_Soldier`). AGENTS.md already records that 13 of the reference
    world's own characters are NPCs no bundled table names.

    Refusing on this would block the extraction over a gap in a *different*
    bundle. Unknown **items** stay a hard refusal, because the item catalogue is
    complete at 2,466 and an id that misses there really does mean the
    projection drifted.

    `character()` rather than `pal()`: it covers humans too, which is the lookup
    `palcheck` switched to after reporting 108 Pals on a clean world as illegal.
    """
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "backend"))
    import gamedata  # noqa: E402

    return sorted({s for s in data["drops"] if not gamedata.character(s)})


def main() -> int:
    pak = palpak.Pak()
    data, stats = build(pak)

    problems = verify(data)
    if problems:
        for line in problems:
            print(f"REFUSING: {line}", file=sys.stderr)
        print(
            "An id that resolves to nothing means the projection has drifted, "
            "not that the game added content — check the column names first.",
            file=sys.stderr,
        )
        return 2

    unknown = unknown_drop_species(data)

    if "--verify" in sys.argv:
        print("verified: every product, material, drop, loot, shop and food id "
              "resolves in the catalogue")
        print(f"  {len(unknown)} of {len(data['drops'])} drop species are not in "
              "the bundled character tables (advisory — boss-rush variants and "
              "quest NPCs, see `unknown_drop_species`)")
        return 0

    write_json(OUT, data)
    print(f"wrote {OUT}")
    print(f"  {len(data['recipes'])} recipes")
    print(f"  {len(data['drops'])} species with drop tables")
    print(f"  {len(data['lottery'])} loot fields, "
          f"{sum(len(v) for v in data['lottery'].values())} entries")
    print(f"  {len(data['shops'])} item shops, {len(data['palShops'])} Pal shops")
    print(f"  {len(data['food'])} foods with effects")
    print(f"  {len(data['production'])} production structures")
    if unknown:
        print(f"  advisory: {len(unknown)} of {len(data['drops'])} drop species "
              f"are not in the bundled character tables (e.g. {unknown[:3]}) — "
              "boss-rush variants and quest NPCs, not a drift")
    if stats["foodRefused"]:
        print(f"  NOTE: {len(stats['foodRefused'])} food row(s) did not decode "
              f"and were dropped: {stats['foodRefused']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
