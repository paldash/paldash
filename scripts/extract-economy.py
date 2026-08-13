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
    techUnlocks   588  which technology unlocks which recipe, and what it needs
    redirects      29  accessory tiers onto their base tier — NOT renames

`recipes` is a **list per product**. The first version kept one, which read as
1,399 products and was really 1,414 rows with fifteen alternates thrown away.

FOUR THINGS MEASURED WHILE BUILDING IT, all worth recording

**`WorkableAttribute` is 0 on all 1,414 recipe rows.** It looked like the link
from a recipe to the work suitability that crafts it, which would have paired
with `DT_MapObjectAssignData`. It is not — the column is present and uniformly
empty. Which bench crafts what therefore still has no source; do not infer it
from the recipe table.

**Drops are level-banded, not per-level.** `Level` takes exactly the values
0, 10, 20 … 80, so a row is "this species at level 30-39" and not "at level 30".
894 species have a table. Interpolating between bands would be inventing.

**The technology-to-recipe join needs a case-fold, and the check is what found
that.** Two of the 588 technologies spell a recipe row differently from the
recipe table (`Bow_triple`/`Bow_Triple`). An exact join loses them and reports a
number that looks like data. `FName` does not care about case; `dict` does.

**`DT_PalStaticItemIDRedirectData` is not a rename map, and treating it as one
would have been a regression.** All 29 rows point an accessory's `_2` and `_3`
tiers at its `_1`, and all 58 source ids already resolve to distinct names —
"Attack Pendant +1" and "+2". Applying it to a lookup replaces 58 correct names
with 29 wrong ones. See `_redirects`.

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


def _key(value) -> str:
    """
    Unwrap a `FName`-valued cell, which decodes as `{"Key": "SheepBall"}`.

    `str()` on that dict gives the literal string `"{'Key': 'SheepBall'}"`, which
    serialises perfectly and is not an id — the Pal-shop rosters shipped that way
    until this helper existed. Anything already flat passes through.
    """
    if isinstance(value, dict):
        value = value.get("Key")
    return str(value or "")


def _read(pak, name: str) -> dict:
    path = next((p for p in pak.files if p.endswith(name + ".uasset")), None)
    if path is None:
        raise SystemExit(f"{name} is not in this pak — did the game update?")
    return uassettable.read_table(pak, path)


def _recipes(pak) -> dict:
    """
    Every recipe row, grouped by product — a **list**, not one recipe each.

    The first version of this bundle keyed one recipe per product and so
    collapsed 1,414 rows to 1,399, silently discarding the second way to make
    fifteen items. That is fine for "how do I make X" and wrong for the question
    this bundle now exists to answer, which is "where does X come from" — an
    alternate recipe is exactly one of the answers.
    """
    out: dict[str, list] = defaultdict(list)
    for key, row in _read(pak, "DT_ItemRecipeDataTable").items():
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
        deny = [str(d) for d in (row.get("DenyRecipeChain") or []) if str(d)]
        out[product].append({
            # The row name, so two recipes for one product can be told apart.
            "recipeId": str(key),
            "productId": product,
            "count": int(row.get("Product_Count") or 1),
            "workAmount": float(row.get("WorkAmount") or 0.0),
            "materials": materials,
            # The schematic that unlocks it, where one exists.
            "unlockItemId": "" if unlock in UNSET else unlock,
            # Craft EXP. Carried because it is half of the conversion test in
            # `_mark_conversions` — on its own it means "grants no EXP" and
            # nothing more.
            "craftExpRate": float(row.get("CraftExpRate") or 0.0),
            # The game's own "do not chain into these", which turns out to name
            # a weapon's own higher tiers rather than anything cyclic. 94 rows,
            # 373 targets, every one a real recipe row. See `_mark_conversions`.
            "denyRecipeChain": deny,
        })
        # `WorkableAttribute` is deliberately not carried: measured 0 on every
        # row, so bundling it would imply a link that is not in the data.
    for rows in out.values():
        rows.sort(key=lambda r: (r["workAmount"], r["recipeId"]))
    return _mark_conversions(dict(out))


def _mark_conversions(recipes: dict) -> dict:
    """
    Flag the recipes that convert a thing back into what it was made from.

    A recursive crafting tree needs to know which way is *down*, and 16 products
    sit in a cycle: a Pal Sphere is made from Paldium Fragment and dismantles
    back into Paldium, and the four Pal Soul sizes convert both up and down.
    Expanding either direction forever is the obvious failure; picking a
    direction by name (`CryStal_*`, `_2_1`) is the failure this repo keeps
    recording.

    **The test is a conjunction of two independent things, and each alone is
    wrong.**

    - *Structural*: the recipe's product is transitively required by its own
      materials. True of all 26 recipes in the cycle, in **both** directions —
      a symmetric test cannot pick one, which is exactly why it is not enough.
    - *The game's own column*: `CraftExpRate == 0`. Every conversion has it,
      and so do **17 ordinary crafts** — Money from Copper Ingots, Baked Berries
      from Berries, every gym head band from Cloth. Dropping on this alone
      deletes seventeen real production paths.

    Together they name **exactly 16 rows**, and the acceptance criterion is not
    that those 16 look like dismantles: it is that removing them leaves a graph
    with **zero cycles**, checked in `verify`. A wrong predicate does not
    produce a DAG.

    What falls out is right rather than merely consistent: the four Pal Soul
    sizes end up with no production recipe at all, which is the game — souls are
    found and traded up, never crafted.
    """
    in_cycle = _cyclic_products(recipes, lambda row: True)
    for rows in recipes.values():
        for row in rows:
            row["isConversion"] = (
                row["craftExpRate"] == 0.0
                and row["productId"] in in_cycle
                and any(m["itemId"] in in_cycle for m in row["materials"])
            )
    return recipes


def _cyclic_products(recipes: dict, keep) -> set:
    """Products that transitively require themselves, over the kept recipes."""
    graph: dict[str, list] = defaultdict(list)
    for product, rows in recipes.items():
        for row in rows:
            if keep(row):
                graph[product].extend(m["itemId"] for m in row["materials"])

    def reachable(start: str) -> set:
        seen: set = set()
        stack = [start]
        while stack:
            for material in graph.get(stack.pop(), ()):
                if material not in seen:
                    seen.add(material)
                    stack.append(material)
        return seen

    return {p for p in graph if p in reachable(p)}


def _tech_unlocks(pak, recipes_by_row: dict) -> dict:
    """
    Which technology unlocks which recipe, and what that technology itself needs.

    `gamedata.json.gz` already carries each technology's name, cost, tier and
    level cap — everything except the two columns that make it answerable in the
    direction a player asks. "What do I need to research to craft this" needs
    `UnlockItemRecipes` to find the technology and `RequireTechnology` to walk
    back up the chain.

    **The join is case-insensitive because the game's own two tables disagree**,
    and the recipe table's spelling wins. Two of the 588 technologies name
    `Bow_triple` and `SkillUnlock_Sakurasaurus_Water` against recipe rows spelled
    `Bow_Triple` and `SkillUnlock_SakuraSaurus_Water`. An `FName` compares
    case-insensitively, so nothing is wrong in the game; a `dict` does not, so an
    exact join drops two technologies and reports 586 of 588 as if that were the
    data. Same inconsistency `gamedata`'s lookups already fold, one table over.
    """
    canonical = {row.lower(): row for row in recipes_by_row}
    out = {}
    for key, row in _read(pak, "DT_TechnologyRecipeUnlock_Common").items():
        recipes = [_key(v) for v in (row.get("UnlockItemRecipes") or [])]
        objects = [_key(v) for v in (row.get("UnlockBuildObjects") or [])]
        recipes = [canonical.get(r.lower(), r) for r in recipes if r not in UNSET]
        objects = [o for o in objects if o not in UNSET]
        if not recipes and not objects:
            continue
        require = _key(row.get("RequireTechnology"))
        boss = _enum(row.get("RequireDefeatTowerBoss"))
        out[str(key)] = {
            "technologyId": str(key),
            "unlocksRecipes": recipes,
            "unlocksStructures": objects,
            "requiresTechnology": "" if require in UNSET else require,
            # A boss technology is bought with Ancient Technology Points, which
            # is a different currency from the ordinary one.
            "isBossTechnology": bool(row.get("IsBossTechnology")),
            "requiresBoss": "" if boss in UNSET else boss,
            "cost": int(row.get("Cost") or 0),
            "levelCap": int(row.get("LevelCap") or 0),
        }
    return out


def _redirects(pak) -> dict:
    """
    `DT_PalStaticItemIDRedirectData` — and it is **not** a rename map.

    It was reached for as one: 29 rows of `SourceItemIds -> DestinationItemId`
    reads exactly like "an old save's ids now mean these", which would have been
    worth wiring into every `gamedata` lookup so a stale id resolved instead of
    falling back to `humanize()`.

    Every one of the 29 is an accessory tier collapsing onto its own base tier —
    `Accessory_AT_2` and `Accessory_AT_3` onto `Accessory_AT_1`, and so on for
    all seventeen pendants and twelve whistles. There is not one genuine rename
    in the table.

    **And all 58 source ids already resolve, to distinct names.** The game calls
    `Accessory_AT_2` "Attack Pendant +1" and `Accessory_AT_3` "Attack Pendant
    +2"; applying this map to a lookup would replace 58 correct names with 29
    wrong ones and undo the tier distinction the L10N join was built to get
    right. So it is carried as data with its meaning stated, and no lookup
    consults it.
    """
    out = {}
    for key, row in _read(pak, "DT_PalStaticItemIDRedirectData").items():
        sources = [_key(v) for v in (row.get("SourceItemIds") or [])]
        sources = sorted(s for s in sources if s not in UNSET)
        destination = _key(row.get("DestinationItemId"))
        if not sources or destination in UNSET:
            continue
        out[str(key)] = {"to": destination, "from": sources}
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
        # `_key`, not `str`: these cells decode as {"Key": "SheepBall"} and the
        # rosters shipped as literal "{'Key': 'SheepBall'}" strings until they
        # were unwrapped. A stringified dict is not an id and resolves to
        # nothing, which is invisible until someone looks at a Pal merchant.
        species = [_key(s) for s in (row.get("CharacterIDArray") or [])]
        species = [s for s in species if s not in UNSET]
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


def _build_objects(pak) -> dict:
    """
    What a STRUCTURE costs to place — `DT_BuildObjectDataTable`, 498 rows.

    **This was the gap behind "the tree view for build isn't working".**
    `crafting.tree()` reads `recipes`, which comes from
    `DT_ItemRecipeDataTable` and covers *items*. A Palbox, a Furnace and a
    Breeding Farm are not items; they are build objects with their own table
    and their own `Material1..4_Id/Count` columns, and nothing extracted them.
    So every one of the 1,088 structures returned an empty tree — not an
    error, which is why it read as a broken view rather than as missing data.

    Shaped like a recipe row deliberately, so `crafting.py` can walk a
    structure's materials with the code it already has instead of growing a
    second traversal. The differences that are real are kept and named:
    a structure has no output *count* (you place one) and its work column is
    `RequiredBuildWorkAmount` rather than `WorkAmount`.

    `BlueprintItemID` is carried and **not interpreted**. It reads like "the
    schematic that unlocks this", and the technology join in `techUnlocks` is
    the thing that actually answers that — asserting it from a column name is
    the `TowerLockBarrier` mistake.
    """
    out: dict[str, dict] = {}
    for key, row in _read(pak, "DT_BuildObjectDataTable").items():
        materials = []
        for n in range(1, MATERIAL_SLOTS + 1):
            item_id = str(row.get(f"Material{n}_Id") or "")
            count = int(row.get(f"Material{n}_Count") or 0)
            if item_id not in UNSET and count > 0:
                materials.append({"itemId": item_id, "count": count})
        if not materials:
            # MEASURED: this never fires — all 498 rows carry at least one
            # material. Kept as a guard rather than removed, because a costless
            # row would otherwise render as a buildable thing that is free,
            # which is the more misleading of the two readings. Stated as an
            # observation and not as a filter that does something, so nobody
            # later cites it as evidence that uncosted structures exist.
            continue
        blueprint = str(row.get("BlueprintItemID") or "")
        out[str(key)] = {
            "mapObjectId": str(row.get("MapObjectId") or key),
            "materials": materials,
            "workAmount": float(row.get("RequiredBuildWorkAmount") or 0.0),
            "typeA": str(row.get("TypeA") or ""),
            "typeB": str(row.get("TypeB") or ""),
            "rank": int(row.get("Rank") or 0),
            "buildCapacity": int(row.get("BuildCapacity") or 0),
            "blueprintItemId": "" if blueprint in UNSET else blueprint,
        }
    return out


def build(pak=None) -> tuple[dict, dict]:
    pak = pak or palpak.Pak()
    food, food_refused = _food(pak)
    recipes = _recipes(pak)
    recipe_rows = {r["recipeId"] for rows in recipes.values() for r in rows}
    data = {
        "recipes": recipes,
        "buildObjects": _build_objects(pak),
        "drops": _drops(pak),
        "lottery": _lottery(pak),
        "shops": _shops(pak),
        "palShops": _pal_shops(pak),
        "food": food,
        "production": _production(pak),
        "techUnlocks": _tech_unlocks(pak, recipe_rows),
        "redirects": _redirects(pak),
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
        (m["itemId"] for rows in data["recipes"].values()
         for r in rows for m in r["materials"]),
        "recipe materials",
    )
    # A structure's build cost is spent in ITEMS, so every material must
    # resolve in the item catalogue. This is the acceptance criterion for the
    # new section: 498 rows landing on 58 real materials is evidence the
    # columns were read correctly, in a way the row count alone is not.
    check_items(
        (m["itemId"] for row in data["buildObjects"].values()
         for m in row["materials"]),
        "build object materials",
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
    check_items(
        (i for r in data["redirects"].values() for i in [r["to"], *r["from"]]),
        "redirects",
    )

    # THE TECHNOLOGY JOIN IS THE ONE THAT COULD SILENTLY BE WRONG, so it is
    # checked rather than assumed. `UnlockItemRecipes` holds recipe ROW names
    # (`Axe_Tier_00`), not product ids — reading them as items would resolve to
    # nothing everywhere, which is loud, but reading them as products would
    # resolve for the handful whose row name happens to match their product and
    # look like a working join.
    recipe_rows = {r["recipeId"] for rows in data["recipes"].values() for r in rows}
    dangling = sorted({
        r for t in data["techUnlocks"].values()
        for r in t["unlocksRecipes"] if r not in recipe_rows
    })
    if dangling:
        problems.append(
            f"technology unlocks: {len(dangling)} name no recipe row, "
            f"e.g. {dangling[:5]}"
        )

    # And every technology a chain points back at must itself exist, or "what do
    # I need to research first" walks off the end of the table.
    missing_parents = sorted({
        t["requiresTechnology"] for t in data["techUnlocks"].values()
        if t["requiresTechnology"] and t["requiresTechnology"] not in data["techUnlocks"]
    })
    if missing_parents:
        problems.append(
            f"technology prerequisites: {len(missing_parents)} unknown, "
            f"e.g. {missing_parents[:5]}"
        )

    # THE CONVERSION FLAG IS ACCEPTED ON ITS RESULT, NOT ON ITS REASONING.
    # `_mark_conversions` argues that 16 rows convert rather than produce; what
    # is checked here is that removing exactly those rows leaves a graph a
    # crafting tree can be walked over at all. A predicate that picked the wrong
    # direction — or one row too few — leaves a cycle standing, and a cycle is
    # the one thing recursive expansion cannot survive.
    left = sorted(_cyclic_products(
        data["recipes"], lambda row: not row.get("isConversion")
    ))
    if left:
        problems.append(
            f"crafting cycles: {len(left)} product(s) still require themselves "
            f"after excluding conversions, e.g. {left[:5]} — the conversion "
            "test no longer picks a direction"
        )

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
        print(f"  every recipe named by the {len(data['techUnlocks'])} "
              "technologies resolves to a recipe row, and every prerequisite "
              "technology exists")
        print(f"  {len(unknown)} of {len(data['drops'])} drop species are not in "
              "the bundled character tables (advisory — boss-rush variants and "
              "quest NPCs, see `unknown_drop_species`)")
        return 0

    write_json(OUT, data)
    rows = sum(len(v) for v in data["recipes"].values())
    alternates = sum(1 for v in data["recipes"].values() if len(v) > 1)
    print(f"wrote {OUT}")
    conversions = [r["recipeId"] for v in data["recipes"].values()
                   for r in v if r["isConversion"]]
    print(f"  {rows} recipe rows over {len(data['recipes'])} products "
          f"({alternates} have more than one way to make them)")
    print(f"  {len(conversions)} of those convert rather than produce — "
          "dismantling and Pal Soul trading; excluding exactly these leaves a "
          "graph with no cycles, which is what accepts the test")
    bo_mats = {m["itemId"] for row in data["buildObjects"].values()
               for m in row["materials"]}
    print(f"  {len(data['buildObjects'])} STRUCTURES with a build cost, over "
          f"{len(bo_mats)} distinct materials — the table `crafting.tree()` "
          "could not see, which is why every structure returned an empty tree")
    print(f"  {len(data['drops'])} species with drop tables")
    print(f"  {len(data['lottery'])} loot fields, "
          f"{sum(len(v) for v in data['lottery'].values())} entries")
    print(f"  {len(data['shops'])} item shops, {len(data['palShops'])} Pal shops")
    print(f"  {len(data['food'])} foods with effects")
    print(f"  {len(data['production'])} production structures")
    print(f"  {len(data['techUnlocks'])} technologies with something to unlock")
    print(f"  {len(data['redirects'])} item redirects — accessory tiers onto "
          "their base tier, NOT renames; nothing consults them")
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
