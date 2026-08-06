"""
"Where does this item come from" — the assembled answer, and the claims it must
not make.

Every test here runs against the **shipped bundles on disk**, not against
fixtures. That is deliberate and follows `test_gametext.py`: a fixture pins the
assembler and would have passed happily while `economy.json.gz` shipped Pal-shop
rosters as the literal string `"{'Key': 'SheepBall'}"`, which is what it did.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import gamedata  # noqa: E402
import itemsource  # noqa: E402
import viewcache  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_caches():
    gamedata._reset_cache()
    viewcache.clear()
    yield
    gamedata._reset_cache()
    viewcache.clear()


# ─── The headline answer ─────────────────────────────────


def test_ancient_civilization_parts_names_its_sources():
    """
    The example the task was written around, and the one nothing could answer
    before: a material with no recipe that comes entirely from alphas and chests.
    """
    result = itemsource.describe("PalCrystal_Ex")

    assert result["known"] is True
    assert result["name"] == "Ancient Civilization Parts"
    assert result["hasSource"] is True
    assert result["crafting"] == []
    assert result["drops"]["total"] > 300
    assert result["loot"]
    # It is a material for a great many things, which is half the answer to
    # "why do I need this".
    assert len(result["usedIn"]) > 100


def test_an_unknown_id_is_not_an_item_with_no_sources():
    """
    "Nothing produces this" and "there is no such item" are different answers and
    a caller must be able to tell them apart. The catalogue is complete at 2,466,
    so a miss really does mean the id is not an item.
    """
    result = itemsource.describe("__not_an_item__")
    assert result["known"] is False
    assert "crafting" not in result
    assert "hasSource" not in result


def test_a_crafted_item_carries_its_materials_named():
    result = itemsource.describe("AIcore")
    assert len(result["crafting"]) == 1
    materials = {m["name"]: m["count"] for m in result["crafting"][0]["materials"]}
    assert "Ancient Civilization Core" in materials
    # Ids resolve to names AND icons — a row with a bare id is the failure mode
    # `install-icons.py` documents, since nothing turns `AIcore` into its texture.
    assert all(m["itemId"] for m in result["crafting"][0]["materials"])


def test_every_way_of_making_something_is_offered():
    """
    Paldium Fragment is the case the old one-recipe-per-product shape lost: 13
    rows, one per kind of Pal Sphere dismantled, of which it kept one.
    """
    result = itemsource.describe("Pal_crystal_S")
    assert len(result["crafting"]) == 13
    assert len({r["recipeId"] for r in result["crafting"]}) == 13


# ─── Technology ──────────────────────────────────────────


def test_a_recipe_names_the_technology_that_unlocks_it():
    result = itemsource.describe("PalSphere")
    techs = result["crafting"][0]["technologies"]
    assert [t["name"] for t in techs] == ["Pal Sphere"]
    assert techs[0]["cost"] >= 1


def test_a_technology_chain_lists_what_must_come_first_in_order():
    """
    Feed bags are the deepest chain the game ships, and the order is the point —
    a set of prerequisites is not an answer to "what do I research first".
    """
    chain = gamedata.technology_chain("AutoMealPouch_Tier5")
    assert [gamedata.technology_name(t) for t in chain] == [
        "Small Feed Bag",
        "Average Feed Bag",
        "Large Feed Bag",
        "Huge Feed Bag",
        "Giant Feed Bag",
    ]


def test_a_technology_chain_cannot_hang_on_a_cycle():
    """
    The walk is unbounded over data this project does not control, so a
    self-referential row must terminate rather than spin.
    """
    economy = gamedata.economy()
    economy["techUnlocks"]["_loop_a"] = {
        "technologyId": "_loop_a", "requiresTechnology": "_loop_b", "cost": 1,
    }
    economy["techUnlocks"]["_loop_b"] = {
        "technologyId": "_loop_b", "requiresTechnology": "_loop_a", "cost": 1,
    }
    assert set(gamedata.technology_chain("_loop_a")) == {"_loop_a", "_loop_b"}


def test_boss_technology_points_are_never_summed_with_ordinary_ones():
    """
    Boss technologies are bought with Ancient Technology Points — a different
    currency. A total across a chain would misstate what it costs, so the flag
    travels per step and no total is offered.
    """
    result = itemsource.describe("AIcore")
    for tech in result["crafting"][0]["technologies"]:
        assert "isBossTechnology" in tech
        assert "totalCost" not in tech
        assert "points" not in tech


# ─── What it must not claim ──────────────────────────────


def test_no_recipe_says_which_bench_crafts_it():
    """
    `WorkableAttribute` is 0 on all 1,414 rows, so the recipe-to-workstation link
    has no source. `basesupply`'s rule: report facts, not mechanics.
    """
    for item_id in ("AIcore", "PalSphere", "Pal_crystal_S", "CarbonFiber"):
        for recipe in itemsource.describe(item_id)["crafting"]:
            assert "workstation" not in recipe
            assert "bench" not in recipe
            assert "workType" not in recipe


def test_loot_weight_never_travels_as_a_bare_rate():
    """
    `WeightInSlot` is relative within one field's slot and nothing says how often
    a field is rolled. `slotShare` divides by that slot's own total and IS a
    probability given the roll; anything named like a per-hour rate would not be.
    """
    rows = itemsource.describe("PalCrystal_Ex")["loot"]
    assert rows
    for row in rows:
        assert "chance" not in row
        assert "rate" not in row
        assert "perHour" not in row
        assert row["slotShare"] is None or 0 < row["slotShare"] <= 1


def test_a_drop_band_is_never_presented_as_a_level():
    """
    The `Level` column holds only 0, 10, 20 … 80. Naming the field `levelFrom`
    all the way through is what stops something downstream reading it as exact.
    """
    for row in itemsource.describe("Leather")["drops"]["shown"]:
        assert "level" not in row
        assert row["levelFrom"] % 10 == 0


def test_a_truncated_drop_list_says_so():
    """
    Leather comes from hundreds of species. Truncating is right; truncating
    silently is how a partial answer reads as a complete one.
    """
    drops = itemsource.describe("Leather")["drops"]
    assert drops["total"] > len(drops["shown"])
    assert len(drops["shown"]) == itemsource.MAX_DROP_SOURCES


# ─── Names ───────────────────────────────────────────────


def test_drop_sources_resolve_humans_as_well_as_pals():
    """
    `CharacterSaveParameterMap` and these drop tables both hold humans. `pal_name`
    alone leaves merchants, hunters and soldiers showing internal ids.
    """
    names = {r["name"] for r in itemsource.describe("PalSphere")["drops"]["shown"]}
    assert not any(name.startswith("BOSS_") for name in names)
    assert not any("_" in name for name in names)


def test_an_alpha_keeps_its_own_drop_table_and_is_flagged_not_renamed():
    """
    An alpha drops differently from the ordinary form, so the `BOSS_` prefix is
    NOT stripped from the id the way `pal()` strips it. The game still calls the
    Pal by its plain name, so `isBoss` travels separately rather than being
    folded into the name as an editorialised suffix.
    """
    rows = itemsource.describe("PalCrystal_Ex")["drops"]["shown"]
    bosses = [r for r in rows if r["isBoss"]]
    assert bosses
    for row in bosses:
        assert row["speciesId"].startswith("BOSS_")
        assert "(Boss)" not in row["name"]
        assert "(Alpha)" not in row["name"]


def test_a_pal_merchant_roster_holds_ids_not_stringified_dicts():
    """
    `CharacterIDArray` decodes as `{"Key": "SheepBall"}` and `str()` on that
    serialises perfectly as `"{'Key': 'SheepBall'}"` — an id-shaped string that
    resolves to nothing. It shipped that way, invisibly, because nothing read it.
    """
    shops = gamedata.economy().get("palShops") or {}
    assert shops
    for shop in shops.values():
        for species in shop["species"]:
            assert "{" not in species
            assert gamedata.character(species) is not None


# ─── The redirect table, which is not a rename map ───────


def test_accessory_tiers_keep_their_own_names():
    """
    `DT_PalStaticItemIDRedirectData` reads exactly like "these old ids now mean
    this one" and is not: all 29 rows point an accessory's `_2` and `_3` at its
    `_1`, and all 58 sources already resolve to distinct names. Applying it to a
    lookup would replace 58 correct names with 29 wrong ones.
    """
    assert gamedata.item_name("Accessory_AT_1") == "Attack Pendant"
    assert gamedata.item_name("Accessory_AT_2") == "Attack Pendant +1"
    assert gamedata.item_name("Accessory_AT_3") == "Attack Pendant +2"

    redirects = gamedata.economy().get("redirects") or {}
    assert len(redirects) == 29
    for row in redirects.values():
        # Every source and destination is a real, separately named item — which
        # is the evidence that nothing here needs resolving.
        assert gamedata.item(row["to"])
        for source in row["from"]:
            assert gamedata.item(source)
            assert gamedata.item_name(source) != gamedata.item_name(row["to"])


# ─── Craftable-from-stock ────────────────────────────────


def test_craftable_from_counts_batches_against_the_scarcest_material():
    recipes = itemsource.craftable_from({"Pal_crystal_S": 10})
    spheres = [r for r in recipes if r["itemId"] == "PalSphere"]
    assert spheres and spheres[0]["batches"] == 10


def test_craftable_from_ignores_a_recipe_missing_any_material():
    """One material at zero is a recipe you cannot start, not a partial one."""
    recipes = itemsource.craftable_from({"Coal": 1000})
    assert not any(r["itemId"] == "CarbonFiber" for r in recipes)


def test_craftable_from_is_case_insensitive_on_the_stock_keys():
    """
    The upstream data is inconsistently capitalised and so is anything derived
    from it, which is why every lookup in this project folds case.
    """
    assert itemsource.craftable_from({"pal_CRYSTAL_s": 5})[0]["batches"] >= 1


def test_craftable_from_takes_the_stock_rather_than_fetching_it():
    """
    The totals it works from are privacy-scoped per guild, so this module must
    not be able to reach them. Same separation `_scope_pals` enforces: the filter
    takes the list rather than going and getting one.
    """
    assert itemsource.craftable_from({}) == []


def test_a_recipe_with_no_materials_is_not_infinitely_craftable():
    """
    `min()` over an empty material list would raise, and a default of infinity
    would report every free-to-make row as unlimited. Skipped instead.
    """
    free = [
        product
        for product, rows in (gamedata.economy().get("recipes") or {}).items()
        for row in rows
        if not row["materials"]
    ]
    result = {r["itemId"] for r in itemsource.craftable_from({"Wood": 100})}
    assert result.isdisjoint(free)
