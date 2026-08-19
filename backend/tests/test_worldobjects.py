"""
The static world-object layer.

Two things are load-bearing here and neither is "does it return points":

  * **A huge bounding box must not be enumerated cell by cell.** The first
    benchmark of this module OOM-killed the process: a box spanning the coordinate
    space is ~6 billion cell tuples. The grid is only ever a shortcut, so past a
    budget it has to fall back to walking the buckets that exist.
  * **A capped response has to say so.** Returning 2,000 of 24,000 silently would
    make the map quietly misrepresent the world, so `inView` counts everything
    that matched and `truncated` reports the difference.
"""

from __future__ import annotations

import pytest

import worldobjects


@pytest.fixture
def fake(monkeypatch):
    """A small synthetic world, so the assertions are about the code not the data."""
    groups = {
        "ore": {
            "label": "Ore & mineral nodes",
            "count": 4,
            "byClass": {"Rock": 3, "Coal": 1},
            "objects": [
                {"cls": "Rock", "x": 0.0, "y": 0.0, "z": 0.0, "landmass": "palpagos"},
                {"cls": "Rock", "x": 100.0, "y": 100.0, "z": 0.0, "landmass": "palpagos"},
                {"cls": "Rock", "x": 30_000.0, "y": 0.0, "z": 0.0, "landmass": "palpagos"},
                {"cls": "Coal", "x": -30_000.0, "y": -30_000.0, "z": 0.0, "landmass": "palpagos"},
            ],
        },
        "treasure": {
            "label": "Treasure chests",
            "count": 1,
            "byClass": {"Box": 1},
            "objects": [
                {"cls": "Box", "x": 50.0, "y": 50.0, "z": 0.0, "landmass": "palpagos"},
            ],
        },
    }
    monkeypatch.setattr(
        worldobjects, "_data",
        {"groups": groups, "cellsParsed": 7, "skipped": {"offGrid": 2}},
    )
    monkeypatch.setattr(worldobjects, "_index", None)
    yield groups
    worldobjects.reset_for_tests()


# ─── Queries ─────────────────────────────────────────────


def test_a_box_returns_only_what_is_inside_it(fake):
    result = worldobjects.query(min_x=-1_000, min_y=-1_000, max_x=1_000, max_y=1_000)
    assert result["inView"] == 3          # two ore, one chest
    assert {p["cls"] for p in result["points"]} == {"Rock", "Box"}


def test_each_point_carries_its_category(fake):
    result = worldobjects.query(min_x=-1_000, min_y=-1_000, max_x=1_000, max_y=1_000)
    assert {p["category"] for p in result["points"]} == {"ore", "treasure"}


def test_a_category_filter_narrows_it(fake):
    result = worldobjects.query(
        category="ore", min_x=-1_000, min_y=-1_000, max_x=1_000, max_y=1_000
    )
    assert result["inView"] == 2


def test_a_kind_filter_narrows_it_further(fake):
    """
    "Ore" is 17 different rocks on the real data, and someone hunting coal does
    not want copper.
    """
    result = worldobjects.query(kinds=["Coal"])
    assert result["inView"] == 1
    assert result["points"][0]["cls"] == "Coal"


def test_a_category_prefixed_kind_filters_only_that_category(fake):
    """
    The bug a single flat kind set produces: filtering ore to Coal would also
    filter chests to nothing, because no chest class is in the list. A category
    with no entry has to stay unfiltered, which is why an absent entry and an
    empty set mean different things.
    """
    result = worldobjects.query(kinds=["ore:Coal"])
    by_category: dict[str, int] = {}
    for point in result["points"]:
        by_category[point["category"]] = by_category.get(point["category"], 0) + 1

    assert by_category == {"ore": 1, "treasure": 1}   # one Coal, the chest untouched


def test_prefixed_kinds_in_two_categories_are_independent(fake):
    result = worldobjects.query(kinds=["ore:Rock", "treasure:Box"])
    assert result["inView"] == 4          # 3 rocks + 1 box


def test_deselecting_every_kind_of_a_category_shows_none_of_it(fake):
    """
    An empty selection is a real request, not a missing one. Someone who unticked
    all 17 ore classes means "no ore", and answering with all of it would be the
    opposite of what they asked.
    """
    result = worldobjects.query(kinds=["ore:", "treasure:Box"])
    assert {p["category"] for p in result["points"]} == {"treasure"}
    assert result["inView"] == 1

    # And it must not be read as a class literally named "ore:", which would leak
    # into the global set and filter every other category to nothing as well.
    assert worldobjects.query(kinds=["ore:"])["inView"] == 1   # the chest survives


def test_an_unknown_category_prefix_filters_nothing_real(fake):
    result = worldobjects.query(kinds=["nonexistent:Whatever"])
    assert result["inView"] == 5          # every real category stays unfiltered


def test_bare_kinds_still_apply_across_categories(fake):
    """
    The older form, kept working: a bare class name filters every category. It is
    what a caller means when it does not care which category a class belongs to.
    """
    assert worldobjects.query(kinds=["Coal"])["inView"] == 1
    assert worldobjects.query(kinds=["Rock", "Box"])["inView"] == 4


def test_class_names_are_unique_per_category_in_the_bundled_data():
    """
    Not relied on — the prefixed form exists precisely so a collision cannot
    silently mis-filter — but worth knowing if it ever stops being true.
    """
    worldobjects.reset_for_tests()
    try:
        owners: dict[str, set[str]] = {}
        for category in worldobjects.categories():
            for kind in category["kinds"]:
                owners.setdefault(kind["cls"], set()).add(category["id"])
        shared = {c: v for c, v in owners.items() if len(v) > 1}
        assert not shared, f"class names shared across categories: {shared}"
    finally:
        worldobjects.reset_for_tests()


def test_an_object_outside_the_box_but_inside_the_cell_is_excluded(fake):
    """
    Cells are 25,600 units wide, so an edge cell is only partly inside the box.
    Bucketing narrows the candidates; the precise bounds check is what decides.
    """
    result = worldobjects.query(min_x=-10.0, min_y=-10.0, max_x=10.0, max_y=10.0)
    assert result["inView"] == 1          # the one at (0, 0), not (50, 50) or (100, 100)


def test_objects_in_a_neighbouring_cell_are_found(fake):
    """A box spanning a cell boundary must not stop at it."""
    result = worldobjects.query(min_x=-40_000, min_y=-1_000, max_x=40_000, max_y=1_000)
    assert result["inView"] == 4         # (0,0) (100,100) (30000,0) chest at (50,50)


def test_no_box_returns_everything(fake):
    assert worldobjects.query()["inView"] == 5


# ─── The cap ─────────────────────────────────────────────


def test_a_capped_response_reports_what_it_left_out(fake):
    result = worldobjects.query(limit=2)
    assert result["returned"] == 2
    assert result["inView"] == 5          # counted past the cap, not stopped at it
    assert result["truncated"] is True


def test_an_uncapped_response_is_not_marked_truncated(fake):
    result = worldobjects.query()
    assert result["truncated"] is False
    assert result["returned"] == result["inView"]


def test_the_limit_is_clamped(fake):
    """A crafted request cannot ask for a million points."""
    assert worldobjects.query(limit=10**9)["limit"] == worldobjects.MAX_POINTS
    assert worldobjects.query(limit=-5)["limit"] == 1


# ─── The bug the benchmark found ─────────────────────────


def test_a_coordinate_space_sized_box_does_not_enumerate_cells(fake):
    """
    This OOM-killed the process the first time it was measured: 2e9 units across
    at 25,600 per cell is 78,125 cells per axis, and the cross product is ~6
    billion tuples.

    The fix is a budget — past it, walk the buckets that exist instead. The
    assertion is simply that this returns, which is exactly what it did not do.
    """
    result = worldobjects.query(min_x=-1e9, min_y=-1e9, max_x=1e9, max_y=1e9)
    assert result["inView"] == 5


def test_the_budget_path_still_applies_the_bounds(fake):
    """
    Falling back to a full bucket walk must not silently become "return
    everything" — the box still has to be honoured, just checked per object.
    """
    result = worldobjects.query(min_x=-1e9, min_y=-1e9, max_x=-20_000, max_y=-20_000)
    assert result["inView"] == 1
    assert result["points"][0]["cls"] == "Coal"


# ─── Metadata ────────────────────────────────────────────


def test_categories_report_counts_and_kinds(fake):
    cats = {c["id"]: c for c in worldobjects.categories()}
    assert cats["ore"]["count"] == 4
    assert cats["ore"]["label"] == "Ore & mineral nodes"
    # Kinds ordered by frequency: the common thing first is what a legend wants.
    assert [k["cls"] for k in cats["ore"]["kinds"]] == ["Rock", "Coal"]


def test_totals_sum_the_groups(fake):
    totals = worldobjects.totals()
    assert totals["objects"] == 5
    assert totals["categories"] == 2
    assert totals["cellSize"] == worldobjects.CELL_SIZE


def test_a_missing_bundle_degrades_instead_of_raising(monkeypatch, tmp_path):
    """
    The map should lose a layer, not break. Same rule the effigy loader follows.
    """
    monkeypatch.setattr(worldobjects, "_data", None)
    monkeypatch.setattr(worldobjects, "_index", None)
    monkeypatch.setattr(worldobjects, "DATA_PATH", str(tmp_path / "nope.json.gz"))
    try:
        assert worldobjects.query()["points"] == []
        assert worldobjects.categories() == []
        assert worldobjects.totals()["objects"] == 0
    finally:
        worldobjects.reset_for_tests()


def test_a_malformed_row_is_dropped_not_fatal(monkeypatch):
    monkeypatch.setattr(worldobjects, "_data", {"groups": {"ore": {"objects": [
        {"cls": "Good", "x": 1.0, "y": 1.0},
        {"cls": "NoPosition"},
        {"cls": "Rubbish", "x": "over there", "y": None},
    ]}}})
    monkeypatch.setattr(worldobjects, "_index", None)
    try:
        result = worldobjects.query()
        assert [p["cls"] for p in result["points"]] == ["Good"]
    finally:
        worldobjects.reset_for_tests()


# ─── Against the bundled data ────────────────────────────


def test_the_bundled_data_has_the_documented_shape():
    """
    Not an integration test — this file ships with the dashboard. If the counts
    move, the docs and the roadmap figures are stale.

    Regenerated 2026-07-30 to add `palspawner` and `dungeon`: 35,687 -> 51,701.
    These placements are the **map layer** — where spawners physically stand.
    They are no longer what the Paldeck's habitat map is built from: that moved
    to `DT_PalSpawnerPlacement` on 2026-08-05, which carries the roster and level
    range as well as the position. Dungeons were already extractable but had
    never been included.

    Grew to **59,396** after a coverage check over all 916 placeable actor
    classes in the pak: the ore, treasure and fishing patterns were each too
    narrow. `BP_PalMapObjectSpawnerTreasureBox*` and
    `BP_PalMapObjectSpawner_Treasure_*` are different families one underscore
    apart, and matching only the first still returned a plausible-looking 8,386 —
    a count that looks right is not coverage. Five categories were added at the
    same time (skill fruit, lotuses, junk piles, coins/pots, supply drops).

    Regenerated earlier the same day to add `npc` (merchants and hostile camps):
    51,701 -> 51,921. And once more to split `fieldboss` out of `palspawner`
    (13,851 -> 13,752 + 99) — the alpha Pals that drop Ancient Technology Points
    were already extracted and simply indistinguishable from ordinary spawn
    points. The split is by negative lookahead rather than target ordering, so an
    object lands in exactly one group whatever `--targets` says. Every pre-existing category came back **byte-identical**,
    which is the property `jsonout.write_json` exists to give — a regeneration
    can be diffed rather than trusted.

    **Tower bosses are not a category here** and looking for them in the pak is
    a dead end: they are fast-travel points, the eight named `… Tower Entrance`.
    See `gamedata.fast_travel_kind`.

    **`effigy` is deliberately absent** even though the extractor can produce it.
    Effigies have their own bundle carrying the instance GUIDs saves key on,
    which is what makes "which have I not found" answerable; the world-object
    copy has positions only, and including both would draw every effigy twice.
    """
    worldobjects.reset_for_tests()
    try:
        totals = worldobjects.totals()
        # 59,396 -> 61,201 on 2026-08-18: the #141 GUID sweep added the
        # `palegg` category (1,805 wild egg spawn points) the moment it
        # enumerated every class the save references.
        assert totals["objects"] == 61_201
        by_category = {c["id"]: c["count"] for c in worldobjects.categories()}
        assert by_category == {
            "oilrig": 185,
            "ore": 27_408, "treasure": 9_923, "fishing": 3_503,
            "palspawner": 13_752, "dungeon": 2_163, "npc": 220,
            "fieldboss": 99, "skillfruit": 68, "junk": 794,
            "lotus": 705, "collectible": 103, "supply": 473,
            "palegg": 1_805,
        }
        assert "effigy" not in by_category
    finally:
        worldobjects.reset_for_tests()


def test_a_realistic_viewport_query_is_cheap():
    """
    The reason the grid exists. A pan happens constantly, so the per-request cost
    has to be a rounding error rather than a scan of 59,396 objects.
    """
    import time

    worldobjects.reset_for_tests()
    try:
        worldobjects.index()          # built once, not per request
        start = time.perf_counter()
        for _ in range(20):
            worldobjects.query(min_x=-260_000, min_y=-30_000, max_x=-200_000, max_y=30_000)
        per_call_ms = (time.perf_counter() - start) / 20 * 1000
        # Measured at ~0.15 ms; the bound is loose so a slow CI box does not fail
        # it, and tight enough that losing the grid would.
        assert per_call_ms < 2.0, f"{per_call_ms:.2f} ms per viewport query"
    finally:
        worldobjects.reset_for_tests()


def test_field_bosses_are_named_and_disjoint_from_spawners():
    """
    99 placements, and **no field boss may also be a plain spawner**.

    The two patterns overlap by construction — every field boss sheet is a
    `BP_PalSpawner_Sheets_…` — so `palspawner` carries a negative lookahead. The
    alternative was relying on first-match-wins over `--targets` order, which
    would have made the split depend on a command line.

    The naming is the other half: `BP_PalSpawner_Sheets_81_1_grass_FBOSS_23` on a
    popup tells a player nothing and `Univolt` tells them everything. 97 of 99
    resolve; the two that do not are left unnamed rather than guessed at.
    """
    worldobjects.reset_for_tests()
    try:
        bosses = worldobjects.load()["groups"]["fieldboss"]["objects"]
        spawners = worldobjects.load()["groups"]["palspawner"]["objects"]

        assert len(bosses) == 99
        assert all("FBOSS" in o["cls"].upper() for o in bosses)
        assert not any("FBOSS" in o["cls"].upper() for o in spawners), (
            "a field boss must not also be counted as a plain spawn point"
        )

        named = [o for o in bosses if o.get("species")]
        assert len(named) == 97
        # Every resolved species is a boss form. This is the check that the
        # `FBOSS` naming convention means what it appears to — it was found by
        # class name, and confirmed by what the name tables independently say.
        assert all(o["species"].lower().startswith("boss_") for o in named)
        assert len({o["species"] for o in named}) == 69
    finally:
        worldobjects.reset_for_tests()
