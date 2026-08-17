"""
Where does this item come from — one answer, assembled from the catalogue.

A player holding a stack of Ancient Civilization Parts and wondering where to get
more has to ask six separate questions today: is it crafted, does something drop
it, is it in chests, does a merchant sell it, does a base structure make it, and
what do I have to research first. Every one of those is a bundled table and none
of them was reachable from the UI.

**This reads the CATALOGUE, never the census.** `/api/world/items` is what the
game has; `/api/items` is what this world holds and is privacy-filtered per
guild. They are one letter apart and the slot editor already reached for the
wrong one once — its autocomplete told people a perfectly legitimate item was
"not in this world" while the backend went on to accept the same input. Nothing
here needs a parsed world, so nothing here should read one.

WHAT IT WILL NOT SAY

- **Which bench crafts a recipe.** `WorkableAttribute` is on every one of the
  1,414 recipe rows and is 0 on all of them, so the recipe-to-workstation link
  has no source. `basesupply.py`'s rule applies: report facts, not mechanics.
- **How often a chest is opened.** `WeightInSlot` is relative within one field's
  slot. `slotShare` divides by that slot's own total and is therefore a real
  probability *given the field is rolled*; how often that happens is not in any
  table, so no figure here is a per-hour rate.
- **A rate between drop bands.** `levelFrom` is a band — the column holds only
  0, 10, 20 … 80 — so a row covers "level 30-39" and interpolating invents
  numbers.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Optional

import gamedata
import viewcache

logger = logging.getLogger(__name__)

# How many drop sources to name before summarising. A common material like
# Leather comes from hundreds of species, and a list that long is not an answer.
MAX_DROP_SOURCES = 40


def _build_index() -> dict[str, dict[str, list]]:
    """
    One pass over the bundle, producing every reverse lookup this module needs.

    `gamedata`'s `dropped_by`, `used_in_recipes` and friends each scan the whole
    table, which is the right shape for one occasional question and costs 3-4 ms
    per item here — 890 drop tables, 1,414 recipes and 500 loot fields, walked
    six times over.

    **Cached as ONE entry, not 2,466.** Keying the finished answer per item would
    have been the obvious move and is wrong twice: `viewcache`'s file cache is a
    shared 128-entry LRU, so a browse through the catalogue would evict the
    Paldeck listing and everything else in it, and the work being repeated is the
    scan rather than the assembly. Indexing once and assembling per request keeps
    the shared cache at one slot and makes a lookup a dict hit.
    """
    economy = gamedata.economy()
    index: dict[str, dict[str, list]] = defaultdict(
        lambda: {"drops": [], "loot": [], "shops": [], "usedIn": [], "production": []}
    )

    def lower(value: Any) -> str:
        return str(value or "").lower()

    for species, bands in (economy.get("drops") or {}).items():
        for band in bands:
            for entry in band.get("items") or []:
                index[lower(entry.get("itemId"))]["drops"].append({
                    "speciesId": species,
                    "levelFrom": band.get("levelFrom"),
                    **entry,
                })

    for field, rows in (economy.get("lottery") or {}).items():
        totals: dict[Any, float] = defaultdict(float)
        for row in rows:
            totals[row.get("slot")] += float(row.get("weight") or 0.0)
        for row in rows:
            slot_total = totals.get(row.get("slot")) or 0.0
            index[lower(row.get("itemId"))]["loot"].append({
                "field": field,
                "slot": row.get("slot"),
                "weight": row.get("weight"),
                # The share of its own slot, which IS comparable — the chance
                # this item fills that slot when the field is rolled. The raw
                # weight is not comparable between fields and never travels
                # alone as though it were a rate.
                "slotShare": (
                    round(float(row.get("weight") or 0.0) / slot_total, 4)
                    if slot_total else None
                ),
                "min": row.get("min"),
                "max": row.get("max"),
                "grade": row.get("grade"),
            })

    for shop, rows in (economy.get("shops") or {}).items():
        for row in rows:
            index[lower(row.get("itemId"))]["shops"].append({"shop": shop, **row})

    for rows in (economy.get("recipes") or {}).values():
        for row in rows:
            for material in row.get("materials") or []:
                index[lower(material.get("itemId"))]["usedIn"].append({
                    "recipeId": row.get("recipeId"),
                    "productId": row.get("productId"),
                    "count": row.get("count"),
                    "needs": material.get("count"),
                })

    for key, row in (economy.get("production") or {}).items():
        index[lower(row.get("productId"))]["production"].append(
            {"structureId": key, **row}
        )

    for entry in index.values():
        entry["drops"].sort(key=lambda r: (-float(r.get("rate") or 0), r["speciesId"]))
        entry["loot"].sort(key=lambda r: (-(r.get("slotShare") or 0), r["field"]))
        entry["shops"].sort(key=lambda r: r["shop"])
        entry["usedIn"].sort(key=lambda r: (r.get("needs") or 0, str(r.get("productId"))))
        entry["production"].sort(key=lambda r: str(r.get("structureId")))

    # Recipe row -> the technologies that unlock it. Keyed on the ROW and not on
    # the product, because a product with several recipes can have them unlocked
    # by different technologies.
    by_recipe: dict[str, list] = defaultdict(list)
    for tech in (economy.get("techUnlocks") or {}).values():
        for recipe_id in tech.get("unlocksRecipes") or []:
            by_recipe[lower(recipe_id)].append(tech)

    return {"items": dict(index), "techByRecipe": dict(by_recipe)}


def _index() -> dict[str, dict[str, list]]:
    """The reverse index, rebuilt when the economy bundle on disk changes."""
    # `crafting` builds a *different* index from this same file — the key is
    # what keeps the two from trading shapes. See `viewcache.per_file`.
    return viewcache.per_file("itemsource:index", gamedata.ECONOMY_PATH, _build_index)


_EMPTY: dict[str, list] = {
    "drops": [], "loot": [], "shops": [], "usedIn": [], "production": []
}


def _sources(item_id: str) -> dict[str, list]:
    return _index()["items"].get(str(item_id or "").lower(), _EMPTY)


def _item_ref(item_id: str) -> dict[str, Any]:
    """An item id, its display name and its icon — what a row needs to render."""
    entry = gamedata.item(item_id) or {}
    return {
        "itemId": item_id,
        "name": entry.get("name") or gamedata.humanize(item_id),
        "icon": entry.get("icon"),
    }


def _species_ref(species_id: str) -> dict[str, Any]:
    """
    A species id with its display name.

    `character_name`, not `pal_name`: drop tables carry humans — hunters,
    soldiers and merchants — and `pal_name` alone leaves them showing internal
    ids. The `BOSS_` prefix is deliberately *not* stripped here the way `pal()`
    strips it, because an alpha has its own drop table and saying so is the
    point; `isBoss` travels separately so the UI can label it without the name
    carrying an editorialised suffix.
    """
    return {
        "speciesId": species_id,
        "name": gamedata.character_name(species_id),
        "isBoss": species_id.upper().startswith("BOSS_"),
    }


def _crafting(item_id: str) -> list[dict[str, Any]]:
    """Every recipe that produces this item, with its materials resolved."""
    by_recipe = _index()["techByRecipe"]
    out = []
    for row in gamedata.recipes_for(item_id):
        recipe_id = str(row.get("recipeId") or "")
        techs = by_recipe.get(recipe_id.lower()) or []
        entry: dict[str, Any] = {
            "recipeId": recipe_id,
            "count": row.get("count"),
            "workAmount": row.get("workAmount"),
            "materials": [
                {**_item_ref(str(m.get("itemId") or "")), "count": m.get("count")}
                for m in row.get("materials") or []
            ],
        }
        # The schematic item that unlocks it, which is a different thing from
        # the technology: one is looted, the other is researched.
        unlock = str(row.get("unlockItemId") or "")
        if unlock:
            entry["unlockedBySchematic"] = _item_ref(unlock)
        if techs:
            entry["technologies"] = [_technology(t) for t in techs]
        out.append(entry)
    return out


def _technology(tech: dict[str, Any]) -> dict[str, Any]:
    """
    A technology row with its display name and the research that precedes it.

    The chain answers "what do I need first" in one go — walking it in the UI
    would need the whole 588-row table client-side.
    """
    tech_id = str(tech.get("technologyId") or "")
    catalogue = gamedata.technology(tech_id) or {}
    chain = [t for t in gamedata.technology_chain(tech_id) if t != tech_id]
    return {
        "technologyId": tech_id,
        "name": catalogue.get("name") or gamedata.humanize(tech_id),
        "icon": catalogue.get("icon"),
        "cost": tech.get("cost"),
        "levelCap": tech.get("levelCap"),
        # Boss technologies are bought with Ancient Technology Points. Summing
        # the two currencies would misstate what a chain costs, so the flag
        # travels per step and nothing here adds them up.
        "isBossTechnology": bool(tech.get("isBossTechnology")),
        "requiresBoss": tech.get("requiresBoss") or "",
        "requires": [
            {
                "technologyId": t,
                "name": (gamedata.technology(t) or {}).get("name")
                or gamedata.humanize(t),
                "cost": (gamedata.technology_unlocks(t) or {}).get("cost"),
                "isBossTechnology": bool(
                    (gamedata.technology_unlocks(t) or {}).get("isBossTechnology")
                ),
            }
            for t in chain
        ],
    }


def _drops(item_id: str) -> dict[str, Any]:
    """
    Which species drop this item, best rate first.

    Truncated rather than paged: the answer to "where do I get Leather" is the
    handful of Pals with the best rate, and a list of 300 is not an answer. The
    total travels beside it so the truncation is visible rather than silent.
    """
    rows = _sources(item_id)["drops"]
    shown = rows[:MAX_DROP_SOURCES]
    return {
        "total": len(rows),
        "shown": [
            {
                **_species_ref(str(r.get("speciesId") or "")),
                # A band, not a level. Named `levelFrom` all the way through so
                # nothing downstream reads it as an exact figure.
                "levelFrom": r.get("levelFrom"),
                "rate": r.get("rate"),
                "min": r.get("min"),
                "max": r.get("max"),
            }
            for r in shown
        ],
    }


def _shops(item_id: str) -> list[dict[str, Any]]:
    """Merchants stocking the item. `overridePrice` 0 means the item's own."""
    catalogue = gamedata.item(item_id) or {}
    out = []
    for row in _sources(item_id)["shops"]:
        override = int(row.get("overridePrice") or 0)
        out.append({
            "shop": row.get("shop"),
            "count": row.get("count"),
            "stock": row.get("stock"),
            "type": row.get("type"),
            # 0 is "use the item's own price", not free — a distinction that
            # would otherwise put a Legendary Sphere in the shop for nothing.
            "price": override or catalogue.get("price"),
            "priceIsOverride": bool(override),
        })
    return out


def describe(item_id: str) -> dict[str, Any]:
    """
    Everything the bundled tables know about where an item comes from.

    An id that is not in the catalogue returns `known: false` rather than an
    empty answer: "nothing produces this" and "there is no such item" are
    different statements and a caller must be able to tell them apart.
    """
    item_id = str(item_id or "")
    entry = gamedata.item(item_id)
    if entry is None:
        return {"itemId": item_id, "known": False}

    sources = _sources(item_id)
    crafting = _crafting(item_id)
    drops = _drops(item_id)
    loot = sources["loot"]
    shops = _shops(item_id)
    production = sources["production"]
    food = gamedata.food_effect(item_id)
    used_in = [
        {**_item_ref(str(r.get("productId") or "")), "needs": r.get("needs")}
        for r in sources["usedIn"]
    ]

    result: dict[str, Any] = {
        "itemId": item_id,
        "known": True,
        "name": entry.get("name") or gamedata.humanize(item_id),
        "icon": entry.get("icon"),
        "description": entry.get("description"),
        "crafting": crafting,
        "drops": drops,
        "loot": loot,
        "shops": shops,
        "production": [
            {
                "structureId": p.get("structureId"),
                "name": gamedata.structure_name(str(p.get("structureId") or "")),
                "requiredWork": p.get("requiredWork"),
                "autoWorkPerSecond": p.get("autoWorkPerSecond"),
            }
            for p in production
        ],
        "usedIn": used_in,
        # THE ABSENCE IS PART OF THE ANSWER. An item no table produces is not a
        # failed lookup — 'Ancient Civilization Parts comes from nowhere in
        # these tables' is worth saying out loud, because the alternative is a
        # blank panel that reads as broken.
        "hasSource": bool(crafting or drops["total"] or loot or shops or production),
    }
    if food:
        result["food"] = food
    return result


def available() -> bool:
    """Whether the economy bundle loaded at all."""
    return bool(gamedata.economy())


def craftable_from(
    stock: dict[str, int], limit: Optional[int] = None
) -> list[dict[str, Any]]:
    """
    What a given pile of materials can make, and how many of each.

    `stock` is `{itemId: count}` — the caller supplies it, because the totals it
    comes from are privacy-scoped per guild and this module has no business
    fetching them. Same separation `_scope_pals` enforces: the filter takes the
    list rather than going and getting one.

    **Each recipe is costed on its own, against the full stock.** Crafting one
    thing consumes materials another needs, so these counts are not
    simultaneously achievable and the payload says so rather than implying a
    plan. Working out the best combination is a knapsack problem the game does
    not pose and the player solves by looking.
    """
    normalised = {str(k).lower(): int(v or 0) for k, v in (stock or {}).items()}
    out = []
    for product, rows in (gamedata.economy().get("recipes") or {}).items():
        for row in rows:
            materials = row.get("materials") or []
            if not materials:
                continue
            batches = min(
                normalised.get(str(m.get("itemId") or "").lower(), 0)
                // max(int(m.get("count") or 1), 1)
                for m in materials
            )
            if batches <= 0:
                continue
            out.append({
                **_item_ref(product),
                "recipeId": row.get("recipeId"),
                "batches": batches,
                "count": batches * int(row.get("count") or 1),
                "materials": [
                    {
                        **_item_ref(str(m.get("itemId") or "")),
                        "count": m.get("count"),
                        "held": normalised.get(str(m.get("itemId") or "").lower(), 0),
                    }
                    for m in materials
                ],
            })
    out.sort(key=lambda r: (-r["batches"], r["name"]))
    return out[:limit] if limit else out
