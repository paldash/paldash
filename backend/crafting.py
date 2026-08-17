"""
Expand any item into the raw materials it is made of, with quantities.

`itemsource` answers "where does this come from" one level deep — a Legendary
Assault Rifle needs 5 Refined Ingots and 20 Carbon Fibre, and then you go and
look up Refined Ingot. This walks the whole way down and adds the quantities up,
which is the question people actually have in front of a workbench.

## Which way is down

Sixteen products sit in a **cycle**: a Pal Sphere is crafted from Paldium
Fragment and dismantles back into Paldium; the four Pal Soul sizes trade both
up and down. Recursive expansion cannot survive that, and no column says
"this recipe is a dismantle".

`scripts/extract-economy.py` settles it at build time and flags the 16 rows.
The test is the conjunction of a structural property and one of the game's own
columns, and **the acceptance criterion is that excluding exactly those rows
leaves a graph with zero cycles** — see `_mark_conversions` there for why either
half alone is wrong.

So this module reads a flag rather than deciding anything, and still refuses to
descend into an item already on the path. That guard should be unreachable; it
is kept because a game update lands as new bundle data and the failure it
prevents is an unbounded walk inside a request.

## The tree and the shopping list are two walks, and the reason is `steps`

A tree shows a material once per branch. A list of things to craft must name
each one **once**, in an order you can actually follow, so `raw` and `steps`
come from a second pass that totals demand across the whole tree before
dividing it into batches.

**The obvious justification for that pass is not the true one, and it was
measured rather than assumed.** Batching per branch can in principle overstate —
two branches each rounding half a batch up — and 44 intermediates do yield more
than one per craft. But only **two** products in the catalogue put such an
intermediate under two consumers at all (both reach Fibre twice), and the two
totals come out **identical on every one of the 1,399 products** at counts 1, 7
and 100, and for those two at every count from 1 to 59. The demands land even.

So the pass earns its place on `steps` and not on arithmetic, and this paragraph
says so instead of claiming a discrepancy nothing here can produce.

## What it will not say

- **How long it takes.** `workAmount` is work units. What converts them into
  minutes is which Pals are assigned, which no game file states —
  `basesupply.py`'s rule, and `labresearch.py` makes the same refusal.
- **Which bench crafts it.** `WorkableAttribute` is 0 on all 1,414 rows.
- **Whether you can afford it.** Nothing here reads a world; the base storage
  endpoints answer that and this one is catalogue-only, which is the distinction
  `/api/world/items` and `/api/items` are one letter apart on.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import Any, Optional

import gamedata
import viewcache

logger = logging.getLogger(__name__)

# Deeper than anything the game ships. Measured on the bundle: the longest
# production chain is **5** (the Sky Island weapons), so this only ever fires on
# data that has changed shape, and then it truncates one branch rather than
# hanging a request.
MAX_DEPTH = 12

# A recipe can name five materials, so a full tree is bounded well below this.
# It exists because the *caller* supplies the quantity: asking for 10,000 of
# something with a five-deep chain is a legitimate request that should produce a
# large answer, and asking for a tree with a million nodes is not.
MAX_NODES = 4000


def _index() -> dict[str, Any]:
    """Recipes keyed by product and by row id, rebuilt when the bundle changes."""
    # `itemsource` builds a *different* index from this same file — the key is
    # what keeps the two from trading shapes. See `viewcache.per_file`.
    return viewcache.per_file("crafting:index", gamedata.ECONOMY_PATH, _build_index)


def _build_index() -> dict[str, Any]:
    economy = gamedata.economy()
    recipes = (economy.get("recipes") or {})
    by_product: dict[str, list] = {}
    by_row: dict[str, dict] = {}
    for product, rows in recipes.items():
        # Lower-cased for the same reason every other lookup here is: the save,
        # the catalogue and the recipe table disagree about capitalisation and
        # an exact match silently loses real items.
        by_product[product.lower()] = list(rows)
        for row in rows:
            by_row[str(row.get("recipeId") or "").lower()] = row

    # ── Structures ────────────────────────────────────────
    #
    # A build object's cost is shaped like a recipe — materials in, one thing
    # out — so it is projected into the same row shape and the existing
    # traversal walks it unchanged. Before this, `DT_BuildObjectDataTable` was
    # not extracted at all and every one of the 1,088 structures returned an
    # EMPTY tree rather than an error, which is why it was reported as a broken
    # view rather than as missing data.
    #
    # **A SEPARATE NAMESPACE, and that is not tidiness.** `Torch` is both an
    # item and a structure — the one collision in 498 — so merging the two maps
    # would make one silently shadow the other, and which one won would depend
    # on dict insertion order. Items are consulted first so nothing that
    # resolved before resolves differently now.
    by_structure: dict[str, list] = {}
    for key, row in (economy.get("buildObjects") or {}).items():
        by_structure[str(key).lower()] = [{
            "recipeId": f"build:{key}",
            "productId": str(key),
            # You place one. There is no batch size for a structure, and
            # inventing one would change the arithmetic in `_batches`.
            "count": 1,
            "workAmount": float(row.get("workAmount") or 0.0),
            "materials": list(row.get("materials") or []),
            "unlockItemId": str(row.get("blueprintItemId") or ""),
            "craftExpRate": 0.0,
            # A structure is never a conversion: nothing dismantles into one,
            # so it cannot take part in the cycles `_mark_conversions` breaks.
            "isConversion": False,
            "isStructure": True,
        }]
        for r in by_structure[str(key).lower()]:
            by_row[r["recipeId"].lower()] = r
    return {"byProduct": by_product, "byRow": by_row, "byStructure": by_structure}


def _recipes_for(item_id: str) -> list[dict]:
    key = str(item_id or "").lower()
    index = _index()
    # Items first — see `_build_index` on the `Torch` collision.
    return index["byProduct"].get(key) or index["byStructure"].get(key, [])


def _production_recipes(item_id: str) -> list[dict]:
    """Recipes that make this item, excluding the ones that merely convert it."""
    return [r for r in _recipes_for(item_id) if not r.get("isConversion")]


def _conversion_recipes(item_id: str) -> list[dict]:
    return [r for r in _recipes_for(item_id) if r.get("isConversion")]


def _is_structure(thing_id: str) -> bool:
    """True for a build object that is not also an item — `Torch` is both."""
    key = str(thing_id or "").lower()
    return (not gamedata.item(thing_id)) and key in _index()["byStructure"]


def _item_ref(item_id: str) -> dict[str, Any]:
    entry = gamedata.item(item_id) or {}
    if entry:
        return {
            "itemId": item_id,
            "name": entry.get("name") or gamedata.humanize(item_id),
            "icon": entry.get("icon"),
        }
    # A structure is not in the item catalogue, so it needs its own name
    # lookup. `structure_name` falls back to `humanize` itself, so an id the
    # tables do not carry still renders as words rather than as a raw id.
    if _is_structure(item_id):
        return {
            "itemId": item_id,
            "name": gamedata.structure_name(item_id),
            "icon": None,
            "isStructure": True,
        }
    return {
        "itemId": item_id,
        "name": gamedata.humanize(item_id),
        "icon": None,
    }


def _choose(item_id: str, prefer: dict[str, str]) -> Optional[dict]:
    """
    The recipe to expand, and the caller's choice wins where they made one.

    Only four products have more than one production recipe (Paldium Fragment
    from Stone, Meteorite or Copper Ore; Carbon Fibre from Coal or Charcoal), so
    the default is the whole answer for 1,395 of 1,399 — but `alternatives`
    travels on every node regardless, because a default that is invisible reads
    as the only option.
    """
    rows = _production_recipes(item_id)
    if not rows:
        return None
    wanted = prefer.get(item_id.lower())
    if wanted:
        for row in rows:
            if str(row.get("recipeId") or "").lower() == wanted.lower():
                return row
    return rows[0]


def _summarise(recipe: dict) -> dict[str, Any]:
    """A recipe named by what it takes and yields, with nothing expanded."""
    return {
        "recipeId": recipe.get("recipeId"),
        "yields": recipe.get("count"),
        "from": [
            {**_item_ref(str(m.get("itemId") or "")), "count": m.get("count")}
            for m in recipe.get("materials") or []
        ],
    }


def _batches(need: float, per_batch: int) -> int:
    """
    How many times the recipe must run.

    Rounded **up**, always: a recipe that yields 20,000 Gold from 30 Copper
    Ingots costs 30 ingots to produce one coin. `surplus` carries the remainder
    rather than hiding it, because the alternative is a materials list that does
    not add up.
    """
    return max(1, math.ceil(need / max(1, per_batch)))


def tree(item_id: str, count: int = 1, prefer: Optional[list[str]] = None,
         max_depth: int = MAX_DEPTH) -> dict[str, Any]:
    """
    The full crafting tree for `count` of `item_id`, plus the raw-material total.

    `prefer` is a list of recipe row ids; any product whose chosen recipe appears
    there uses it, at every depth rather than only at the root — "make Carbon
    Fibre from Charcoal" is a statement about how you play, not about one node.
    """
    item_id = str(item_id or "")
    # A STRUCTURE is a legitimate root, and rejecting one here is what made the
    # build tree look broken: `gamedata.item()` is None for all 498 build
    # objects, so every structure returned `known: False` — which the UI
    # renders as an empty panel rather than as an error, so it read as a view
    # that did not work rather than as an id that was refused.
    if not gamedata.item(item_id) and not _is_structure(item_id):
        return {"itemId": item_id, "known": False,
                "note": "No such item or structure in the catalogue."}

    count = max(1, int(count or 1))
    chosen: dict[str, str] = {}
    for recipe_id in prefer or []:
        row = _index()["byRow"].get(str(recipe_id).lower())
        if row:
            chosen[str(row.get("productId") or "").lower()] = str(row.get("recipeId"))

    budget = {"nodes": 0, "truncated": False}
    root = _expand(item_id, count, chosen, (), 0, max_depth, budget)
    totals = _totals(item_id, count, chosen, max_depth)

    return {
        **_item_ref(item_id),
        "known": True,
        "count": count,
        "craftable": bool(_production_recipes(item_id)),
        "tree": root,
        # The shopping list, and NOT the sum of the tree's leaves — see the
        # module docstring. `raw` is what you have to gather; `steps` is what you
        # have to craft, deepest first so each row's materials already exist.
        "raw": totals["raw"],
        "steps": totals["steps"],
        "totalWork": totals["work"],
        "maxDepth": budget.get("depth", 0),
        "truncated": budget["truncated"],
        # The client is the thing about to render a number, so it is the thing
        # that has to be told what the number is not.
        "workIsUnits": True,
        "checksStock": False,
    }


def _expand(item_id: str, need: float, prefer: dict[str, str],
            path: tuple, depth: int, max_depth: int,
            budget: dict) -> dict[str, Any]:
    """One node of the display tree. `path` is the ancestor chain, for the guard."""
    budget["nodes"] += 1
    budget["depth"] = max(budget.get("depth", 0), depth)

    node: dict[str, Any] = {
        **_item_ref(item_id),
        "need": need,
        "materials": [],
        "leaf": True,
    }

    conversions = _conversion_recipes(item_id)
    if conversions:
        # Named, never expanded. "You can also get Paldium by dismantling a Pal
        # Sphere" is a real answer that the bundle holds, and dropping it
        # silently would lose it; walking into it is the cycle.
        node["alsoFrom"] = [_summarise(r) for r in conversions]

    recipe = _choose(item_id, prefer)
    if recipe is None:
        node["leafReason"] = "raw"
        return node

    options = _production_recipes(item_id)
    node["alternatives"] = len(options)
    if len(options) > 1:
        # The alternates by their MATERIALS, not only their row ids. A chooser
        # offering `CarbonFiber` against `CarbonFiber_2` asks somebody to pick
        # between two strings; "from Coal" against "from Charcoal" is the
        # decision they actually have.
        node["otherRecipes"] = [_summarise(r) for r in options]

    lowered = item_id.lower()
    if lowered in path:
        # Unreachable while the bundle's conversion flags are right, which
        # `verify` asserts at build time. Kept because the cost of being wrong
        # is an unbounded walk inside a request, not a wrong number.
        node["leafReason"] = "cycle"
        return node
    if depth >= max_depth or budget["nodes"] >= MAX_NODES:
        budget["truncated"] = True
        node["leafReason"] = "depth"
        return node

    per_batch = int(recipe.get("count") or 1)
    batches = _batches(need, per_batch)
    made = batches * per_batch

    node.update({
        "leaf": False,
        "recipeId": recipe.get("recipeId"),
        "yields": per_batch,
        "batches": batches,
        "made": made,
        "surplus": made - need,
        "workPerBatch": float(recipe.get("workAmount") or 0.0),
        "work": batches * float(recipe.get("workAmount") or 0.0),
        "materials": [
            _expand(
                str(m.get("itemId") or ""),
                batches * int(m.get("count") or 0),
                prefer, path + (lowered,), depth + 1, max_depth, budget,
            )
            for m in recipe.get("materials") or []
        ],
    })
    node.pop("leafReason", None)
    return node


def _totals(item_id: str, count: int, prefer: dict[str, str],
            max_depth: int) -> dict[str, Any]:
    """
    The shopping list, and the craft order: total demand per item, batched once.

    Items are settled in **dependency order** — an item is expanded only once
    nothing left in the tree can still ask for more of it — which is what makes
    a single batching pass correct rather than merely cheaper, and what lets
    `steps` come out in an order somebody can follow.

    See the module docstring for what this does *not* buy: the totals are
    measured identical to summing the display tree's leaves.
    """
    demand: dict[str, float] = defaultdict(float)
    demand[item_id.lower()] = float(count)
    display: dict[str, str] = {item_id.lower(): item_id}

    # Consumers of each item within this tree, so an item can be recognised as
    # settled. Built by a plain reachability walk, which terminates because the
    # production graph is acyclic — asserted at build time, and re-guarded by
    # `seen` here so a bad bundle costs a wrong list rather than a hung request.
    waiting: dict[str, set] = defaultdict(set)
    seen: set = set()
    frontier = [item_id.lower()]
    while frontier:
        current = frontier.pop()
        if current in seen or len(seen) >= MAX_NODES:
            continue
        seen.add(current)
        recipe = _choose(current, prefer)
        if recipe is None:
            continue
        for material in recipe.get("materials") or []:
            child = str(material.get("itemId") or "").lower()
            display.setdefault(child, str(material.get("itemId") or ""))
            waiting[child].add(current)
            frontier.append(child)

    raw: dict[str, float] = {}
    steps: list[dict[str, Any]] = []
    total_work = 0.0
    settled: set = set()

    for _ in range(len(seen) + 1):
        ready = [
            i for i in seen
            if i not in settled and not (waiting.get(i, set()) - settled)
        ]
        if not ready:
            break
        for current in sorted(ready):
            settled.add(current)
            recipe = _choose(current, prefer)
            need = demand.get(current, 0.0)
            if recipe is None or need <= 0:
                if need > 0:
                    raw[current] = need
                continue
            per_batch = int(recipe.get("count") or 1)
            batches = _batches(need, per_batch)
            work = batches * float(recipe.get("workAmount") or 0.0)
            total_work += work
            steps.append({
                **_item_ref(display.get(current, current)),
                "recipeId": recipe.get("recipeId"),
                "need": need,
                "batches": batches,
                "yields": per_batch,
                "made": batches * per_batch,
                "surplus": batches * per_batch - need,
                "work": work,
            })
            for material in recipe.get("materials") or []:
                child = str(material.get("itemId") or "").lower()
                demand[child] += batches * int(material.get("count") or 0)

    # Anything demand reached that never got a turn — only possible if the guard
    # above stopped the walk. Reported rather than dropped.
    for key, amount in demand.items():
        if key not in settled and amount > 0:
            raw[key] = amount

    return {
        "raw": sorted(
            ({**_item_ref(display.get(k, k)), "count": v} for k, v in raw.items()),
            key=lambda r: (-r["count"], r["name"]),
        ),
        # Deepest first: following the list in order, every material a row needs
        # has already been made by an earlier row.
        "steps": list(reversed(steps)),
        "work": total_work,
    }
