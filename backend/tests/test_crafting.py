"""
The recursive crafting tree, and the cycle that had to be broken to build it.

Against the **shipped `economy.json.gz`**, not a fixture — the same rule
`test_itemsource.py` and `test_gametext.py` follow. The load-bearing claim here
is a property of the bundle (which 16 recipes convert rather than produce), so a
fixture would pin the walker and let the bundle regress underneath it, which is
precisely the failure that shipped Pal-shop rosters as a stringified dict.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import crafting  # noqa: E402
import gamedata  # noqa: E402
import viewcache  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_caches():
    gamedata._reset_cache()
    viewcache.clear()
    yield
    gamedata._reset_cache()
    viewcache.clear()


def _leaves(node, out=None):
    out = [] if out is None else out
    if node.get("leaf"):
        out.append(node)
    for child in node.get("materials") or []:
        _leaves(child, out)
    return out


def _walk(node, out=None):
    out = [] if out is None else out
    out.append(node)
    for child in node.get("materials") or []:
        _walk(child, out)
    return out


# ─── The cycle, which is the whole reason this module needed care ───


def test_the_shipped_bundle_has_no_cycle_once_conversions_are_excluded():
    """
    THE ACCEPTANCE CRITERION, restated against the artifact.

    `scripts/extract-economy.py` argues that 16 rows convert rather than
    produce. What makes that an answer rather than a story is this: remove
    exactly them and *nothing* requires itself. A predicate that picked the
    wrong direction, or missed a row, leaves a cycle standing here.

    Recursive expansion is the one thing a cycle cannot survive, so this test
    guards the walker as much as the bundle.
    """
    recipes = gamedata.economy()["recipes"]
    graph: dict[str, list[str]] = {}
    for product, rows in recipes.items():
        for row in rows:
            if not row.get("isConversion"):
                graph.setdefault(product, []).extend(
                    m["itemId"] for m in row["materials"]
                )

    def requires_itself(start):
        seen, stack = set(), [start]
        while stack:
            for material in graph.get(stack.pop(), ()):
                if material not in seen:
                    seen.add(material)
                    stack.append(material)
        return start in seen

    offenders = sorted(p for p in graph if requires_itself(p))
    assert offenders == [], f"{len(offenders)} products still cycle: {offenders[:5]}"


def test_the_conversions_are_exactly_the_dismantles_and_soul_trades():
    """
    Which 16, by name — because "16 rows" would still pass if the predicate
    drifted onto a different sixteen.
    """
    flagged = sorted(
        r["recipeId"]
        for rows in gamedata.economy()["recipes"].values()
        for r in rows if r.get("isConversion")
    )
    assert flagged == sorted([
        "CryStal_PalSphere", "CryStal_PalSphere_Ancient_1",
        "CryStal_PalSphere_Ancient_2", "CryStal_PalSphere_Exotic",
        "CryStal_PalSphere_Giga", "CryStal_PalSphere_Legend",
        "CryStal_PalSphere_Master", "CryStal_PalSphere_Mega",
        "CryStal_PalSphere_Tera", "CryStal_PalSphere_Ultimate",
        "PalUpgradeStone1_2", "PalUpgradeStone2_1", "PalUpgradeStone2_3",
        "PalUpgradeStone3_2", "PalUpgradeStone3_4", "PalUpgradeStone4_3",
    ])


def test_zero_craft_exp_alone_would_delete_seventeen_real_crafts():
    """
    Why the predicate is a conjunction, pinned so nobody simplifies it.

    `CraftExpRate == 0` is on every conversion — and on Money, Baked Berries and
    every gym head band, which are ordinary crafts that happen to grant no EXP.
    Dropping on that column alone loses seventeen production paths.
    """
    zero_exp = [
        r for rows in gamedata.economy()["recipes"].values()
        for r in rows if r.get("craftExpRate") == 0.0
    ]
    assert len(zero_exp) == 33
    innocent = sorted(r["productId"] for r in zero_exp if not r["isConversion"])
    assert len(innocent) == 17
    assert "Money" in innocent and "Baked_Berries" in innocent


def test_paldium_is_never_reached_through_a_dismantle():
    """
    The symptom the flag exists to prevent: a Mega Sphere whose tree says
    Paldium Fragment comes from a Pal Sphere, which comes from Paldium.
    """
    tree = crafting.tree("PalSphere_Mega", 1)
    ids = {n["itemId"].lower() for n in _walk(tree["tree"])}
    assert "palsphere" not in ids
    assert {r["itemId"].lower() for r in tree["raw"]} == {"stone", "wood", "copperore"}


def test_a_conversion_is_named_but_not_expanded():
    """
    Dropping the dismantles silently would lose a real answer the bundle holds,
    so they travel as `alsoFrom` — described, with no child nodes.
    """
    node = crafting.tree("Pal_crystal_S", 1)["tree"]
    assert len(node["alsoFrom"]) == 10
    assert all("materials" not in entry for entry in node["alsoFrom"])
    assert any(e["from"][0]["itemId"] == "PalSphere" for e in node["alsoFrom"])


def test_pal_souls_are_not_craftable_at_all():
    """
    All four sizes' recipes are conversions, so the tree correctly reports no
    production path — which is the game: souls are found and traded up.
    """
    for size in ("PalUpgradeStone", "PalUpgradeStone2",
                 "PalUpgradeStone3", "PalUpgradeStone4"):
        tree = crafting.tree(size, 1)
        assert tree["craftable"] is False
        assert tree["tree"]["leafReason"] == "raw"
        assert tree["steps"] == []
        # It is its own shopping list — you go and get one.
        assert [r["itemId"] for r in tree["raw"]] == [size]


# ─── Quantities ─────────────────────────────────


def test_a_batch_rounds_up_and_the_surplus_is_reported():
    """
    One Gold Coin costs 30 Copper Ingots, because the recipe makes 20,000 at a
    time and you cannot run it a fraction of a time. Hiding the remainder would
    give a materials list that does not add up.
    """
    tree = crafting.tree("Money", 1)
    root = tree["tree"]
    assert root["yields"] == 20000
    assert root["batches"] == 1
    assert root["surplus"] == 19999
    assert [(r["itemId"], r["count"]) for r in tree["raw"]] == [("CopperOre", 60.0)]


def test_asking_for_more_multiplies_the_materials():
    one = crafting.tree("PalSphere_Mega", 1)["raw"]
    ten = crafting.tree("PalSphere_Mega", 10)["raw"]
    by_id = {r["itemId"]: r["count"] for r in one}
    for row in ten:
        assert row["count"] == by_id[row["itemId"]] * 10


def test_the_shopping_list_agrees_with_the_tree_it_came_from():
    """
    Two independent walks over the same recipe choices, so they had better
    agree. The module docstring records that this equality is *measured* across
    the catalogue rather than guaranteed by the batching — this pins the case
    that has the most chances to diverge.
    """
    tree = crafting.tree("SkyBeamSword", 1)
    from collections import defaultdict
    summed: dict[str, float] = defaultdict(float)
    for leaf in _leaves(tree["tree"]):
        summed[leaf["itemId"].lower()] += leaf["need"]
    assert {r["itemId"].lower(): r["count"] for r in tree["raw"]} == dict(summed)


def test_steps_come_out_in_an_order_you_can_follow():
    """
    Deepest first: by the time a row is reached, everything it consumes has
    already been made by an earlier row (or is raw).
    """
    tree = crafting.tree("SkyBeamSword", 1)
    made: set[str] = set()
    raw = {r["itemId"].lower() for r in tree["raw"]}
    recipes = gamedata.economy()["recipes"]
    by_row = {r["recipeId"]: r for rows in recipes.values() for r in rows}
    for step in tree["steps"]:
        for material in by_row[step["recipeId"]]["materials"]:
            item = material["itemId"].lower()
            assert item in made or item in raw, f"{step['recipeId']} needs {item} first"
        made.add(step["itemId"].lower())


# ─── Refusals and guards ─────────────────────────────────


def test_an_unknown_item_is_not_a_404():
    tree = crafting.tree("NotAnItem", 1)
    assert tree["known"] is False
    assert "tree" not in tree


def test_lookups_are_case_insensitive():
    """
    The recipe table itself spells the same material `Stone` and `stone`, and
    the catalogue index is lower-cased, so anything exact loses real items.
    """
    assert crafting.tree("palsphere_mega", 1)["raw"] == \
        crafting.tree("PalSphere_Mega", 1)["raw"]


def test_the_depth_cap_truncates_rather_than_failing():
    tree = crafting.tree("SkyBeamSword", 1, max_depth=2)
    assert tree["truncated"] is True
    assert any(n.get("leafReason") == "depth" for n in _walk(tree["tree"]))


def test_it_never_claims_to_know_your_stock_or_how_long_it_takes():
    """
    `basesupply.py`'s rule, carried in the payload rather than only in a
    docstring — the client is the thing about to render a number.
    """
    tree = crafting.tree("SkyBeamSword", 1)
    assert tree["workIsUnits"] is True
    assert tree["checksStock"] is False


def test_the_alternate_recipe_can_be_chosen_at_any_depth():
    """
    Carbon Fibre is Coal *or* Charcoal, and which one you use is a statement
    about how you play rather than about one node — so `prefer` applies wherever
    the product appears, not only at the root.
    """
    default = crafting.tree("CarbonFiber", 1)["tree"]
    assert default["alternatives"] == 2
    other = next(
        r["recipeId"] for r in gamedata.economy()["recipes"]["CarbonFiber"]
        if r["recipeId"] != default["recipeId"]
    )
    chosen = crafting.tree("CarbonFiber", 1, prefer=[other])["tree"]
    assert chosen["recipeId"] == other
    assert chosen["recipeId"] != default["recipeId"]


# ─── Structures ──────────────────────────────────────────
#
# Reported by the operator as "the tree view for build isn't working". It was
# not a view bug: `DT_BuildObjectDataTable` was never extracted, so all 498
# build objects returned `known: False` — which the UI renders as an empty
# panel rather than an error, so a missing table read as a broken screen.


def test_a_structure_has_a_tree():
    """A Breeding Farm is not an item and must still expand to raw materials."""
    tree = crafting.tree("BreedFarm", count=1)
    assert tree["known"] is True
    assert tree["craftable"] is True
    raw = {r["itemId"]: r["count"] for r in tree["raw"]}
    # 10 Processed Wood + 20 Stone + 50 Fiber, with wood and fibre expanded.
    assert raw["Stone"] == 20
    assert raw["Wood"] > 0


def test_every_bundled_structure_expands():
    """
    All 498, not a sample. A partial join here is the failure mode that hid
    for months: an empty tree is a legitimate-looking answer, so a structure
    that silently stopped resolving would look exactly like one with no cost.
    """
    build_objects = gamedata.economy().get("buildObjects") or {}
    assert len(build_objects) == 498
    empty = [s for s in build_objects
             if not (crafting.tree(s, count=1).get("raw") or [])]
    assert not empty, f"{len(empty)} structures expand to nothing, e.g. {empty[:5]}"


def test_the_torch_collision_resolves_to_the_item():
    """
    `Torch` is BOTH an item and a structure — the one collision in 498. Items
    win, so nothing that resolved before this change resolves differently now.
    Merging the two namespaces would have made the winner depend on dict
    insertion order.
    """
    tree = crafting.tree("Torch")
    assert tree["known"] is True
    assert not tree.get("isStructure")


def test_an_unknown_id_is_still_refused():
    """The structure fallback must not turn a typo into a confident answer."""
    tree = crafting.tree("NoSuchThingAtAll")
    assert tree["known"] is False


def test_itemsource_loading_first_does_not_break_the_tree():
    """
    THE PRODUCTION ORDERING. The Items panel fetches `/api/world/items/{id}`
    (itemsource) and the crafting tree together, and itemsource usually wins the
    race. Both modules cache an index built from the same `economy.json.gz`;
    while `viewcache.per_file` keyed on the path alone, itemsource's index was
    handed to crafting, which read `index["byProduct"]` out of a dict that has
    no such key — a 500 on every crafting tree once the panel had loaded, and a
    pass in every test that called crafting first.
    """
    import itemsource

    itemsource.describe("CopperIngot")           # seeds itemsource's index
    tree = crafting.tree("CopperIngot")          # must still get its OWN index
    assert tree["known"] is True
    assert tree.get("raw"), "tree lost its recipes to itemsource's cache entry"
