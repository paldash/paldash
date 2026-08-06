"""
Recipes, drops, loot, shops, food and production yields.

These close #35 and #36, both of which were filed against the *client* pak and
were impossible there — unversioned properties give a name table and nothing
else. The server pak's are tagged and all seven tables decode completely.

Two rules this file pins because getting either wrong produces a plausible wrong
answer rather than an error:

  * a drop row is a level **band** (0, 10, 20 … 80), not a level;
  * `WorkableAttribute` is 0 on every recipe row, so which bench crafts what is
    still unsourced and must not be inferred from here.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gamedata  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh():
    gamedata._reset_cache()
    yield
    gamedata._reset_cache()


# ─── Recipes ─────────────────────────────────────────────


def test_a_recipe_names_its_materials():
    sphere = gamedata.recipe("PalSphere")
    assert sphere["materials"] == [{"itemId": "Pal_crystal_S", "count": 1}]
    assert sphere["workAmount"] > 0


def test_a_multi_material_recipe_keeps_every_slot():
    mega = gamedata.recipe("PalSphere_Mega")
    ids = {m["itemId"] for m in mega["materials"]}
    assert len(mega["materials"]) >= 3
    assert "Pal_crystal_S" in ids


def test_no_recipe_claims_to_know_which_bench_crafts_it():
    """
    `WorkableAttribute` is present on all 1,414 rows and is 0 on every one. It
    looked like the link to `DT_MapObjectAssignData` and is not, so it is not
    bundled — a caller must not be able to read a work type off a recipe.
    """
    for rows in (gamedata.economy().get("recipes") or {}).values():
        for entry in rows:
            assert "workableAttribute" not in entry
            assert "work" not in entry
            assert "bench" not in entry


def test_every_recipe_material_resolves_in_the_catalogue():
    """
    The hard half of the extractor's asymmetric check, re-asserted here so it
    survives a regeneration nobody ran `--verify` on.
    """
    unknown = {
        m["itemId"]
        for rows in (gamedata.economy().get("recipes") or {}).values()
        for entry in rows
        for m in entry["materials"]
        if not gamedata.item(m["itemId"])
    }
    assert unknown == set()


def test_a_product_keeps_every_way_of_making_it():
    """
    The bundle used to hold one recipe per product and threw fifteen rows away.

    Paldium Fragment is the case that makes it matter: **thirteen** rows, one for
    dismantling each kind of Pal Sphere, of which the old shape kept one. An
    answer to "where does this come from" that names a twelfth of the ways is
    worse than no answer, because nothing about it looks incomplete.
    """
    rows = gamedata.recipes_for("Pal_crystal_S")
    assert len(rows) == 13
    assert len({r["recipeId"] for r in rows}) == 13

    # And the alternate that is a genuine choice rather than a tier: Carbon Fibre
    # from Coal or from Charcoal.
    carbon = {
        m["itemId"]
        for row in gamedata.recipes_for("CarbonFiber")
        for m in row["materials"]
    }
    assert {"Coal", "Charcoal"} <= carbon


def test_recipe_returns_one_of_them_and_recipes_for_returns_all():
    """`recipe()` survives as the single-answer helper; it must agree."""
    single = gamedata.recipe("Pal_crystal_S")
    assert single in gamedata.recipes_for("Pal_crystal_S")
    assert gamedata.recipe("__not_an_item__") is None
    assert gamedata.recipes_for("__not_an_item__") == []


# ─── Drops ───────────────────────────────────────────────


def test_drops_come_back_with_rates_and_ranges():
    bands = gamedata.drops_for("Alpaca")
    assert bands
    items = {i["itemId"]: i for i in bands[0]["items"]}
    assert "Wool" in items
    assert items["Wool"]["rate"] == 100.0
    assert items["Wool"]["min"] <= items["Wool"]["max"]


def test_the_lowercase_min_column_was_read():
    """
    The table spells it `min1` and `Max1`. Reading `Min1` finds nothing and
    yields a silent zero, which would make every drop look like "0 to N".
    """
    nonzero = [
        i for bands in (gamedata.economy().get("drops") or {}).values()
        for b in bands for i in b["items"] if i["min"] > 0
    ]
    assert nonzero, "every drop minimum is zero — the lowercase column was missed"


def test_level_is_a_band_and_only_ever_a_multiple_of_ten():
    """
    A row covers "level 30-39". Treating `levelFrom` as an exact level, or
    interpolating between bands, invents numbers the game does not have.
    """
    seen = {
        b["levelFrom"]
        for bands in (gamedata.economy().get("drops") or {}).values()
        for b in bands
    }
    assert seen <= {0, 10, 20, 30, 40, 50, 60, 70, 80}
    assert len(seen) > 1


def test_alpha_variants_keep_their_own_drop_table():
    """
    `pal()` strips `BOSS_` because an alpha Lamball is still called Lamball.
    Drops are different: an alpha drops different things, so the prefix stands.
    """
    table = gamedata.economy().get("drops") or {}
    assert any(k.startswith("BOSS_") for k in table)


def test_the_reverse_lookup_answers_where_do_i_get_this():
    sources = gamedata.dropped_by("Leather")
    assert sources
    assert all(s["itemId"] == "Leather" for s in sources)
    # Best rate first — the useful order for the question being asked.
    rates = [s["rate"] for s in sources]
    assert rates == sorted(rates, reverse=True)


def test_an_item_nothing_drops_is_an_empty_list_not_an_error():
    # Not `PalSphere`: 52 human enemies drop those, which is the game being
    # sensible and my first guess being wrong. A crafted structure component is
    # the safe example.
    assert gamedata.dropped_by("__not_an_item__") == []
    assert gamedata.dropped_by("") == []


def test_humans_drop_things_too_and_they_are_in_here():
    """
    `DT_PalDropItem` covers every character, not only Pals — hunters and raiders
    drop spheres, ammunition and gold. Reading it as a Pal-only table would lose
    the answer to "where do I get X" for a lot of X.
    """
    sources = {s["speciesId"] for s in gamedata.dropped_by("PalSphere")}
    assert sources
    assert any("Hunter" in s or "Invader" in s or "Believer" in s for s in sources)


# ─── Loot, shops, food, production ───────────────────────


def test_loot_carries_real_weights():
    """
    `WeightInSlot` is an actual drop rate. AGENTS.md once recorded rates as
    permanently unavailable — that was true of the client pak only.
    """
    lottery = gamedata.economy().get("lottery") or {}
    assert len(lottery) >= 400
    entries = lottery["Grass01"]
    assert any(e["weight"] > 0 for e in entries)
    assert all(e["min"] <= e["max"] for e in entries)


def test_shops_list_their_stock():
    shops = gamedata.economy().get("shops") or {}
    assert shops
    products = next(iter(shops.values()))
    assert all(gamedata.item(p["itemId"]) for p in products)


def test_pal_shops_carry_a_roster_and_a_level_range():
    pal_shops = gamedata.economy().get("palShops") or {}
    assert pal_shops
    shop = next(iter(pal_shops.values()))
    assert shop["species"]
    assert shop["levelMin"] <= shop["levelMax"]


def test_food_says_what_it_does_and_for_how_long():
    salad = gamedata.food_effect("Salad")
    assert salad["durationSeconds"] == 600
    assert salad["effects"][0]["type"] == "WorkSpeed"
    assert salad["effects"][0]["value"] == 30


def test_the_one_undecodable_food_row_is_absent_rather_than_wrong():
    """
    `BaconEggs_53` comes back with name-table strings where its property names
    belong. Dropped and reported, never half-read.
    """
    assert gamedata.food_effect("BaconEggs_53") is None
    assert len(gamedata.economy().get("food") or {}) == 53


def test_production_structures_name_what_they_yield():
    production = gamedata.economy().get("production") or {}
    assert production["StonePit"]["productId"] == "Stone"
    # Non-zero means it ticks without a Pal assigned — the Well and the oil pump.
    assert any(p["autoWorkPerSecond"] > 0 for p in production.values())


# ─── Absence ─────────────────────────────────────────────


def test_a_missing_bundle_costs_the_lookups_not_the_page(monkeypatch):
    monkeypatch.setattr(gamedata, "ECONOMY_PATH", "/nonexistent/economy.json.gz")
    gamedata._reset_cache()
    assert gamedata.economy() == {}
    assert gamedata.recipe("PalSphere") is None
    assert gamedata.drops_for("Alpaca") == []
    assert gamedata.dropped_by("Leather") == []
    assert gamedata.food_effect("Salad") is None
