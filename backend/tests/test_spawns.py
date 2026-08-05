"""
Spawn points, rosters and the positions behind them.

This bundle supersedes `habitats.py`'s name-table intersection, and the test that
matters most is `test_it_covers_more_than_the_workaround_it_replaces`: a join
that attributes fewer species than the thing it replaces is wrong, not an update.

The positional claim rests on the cell-grid check with wrong-size controls, the
same one that pinned the field bosses. It is asserted here as well as in the
extractor so a regeneration nobody ran `--verify` on cannot ship silently.
"""

from __future__ import annotations

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gamedata  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh():
    gamedata._reset_cache()
    yield
    gamedata._reset_cache()


# ─── Shape ───────────────────────────────────────────────


def test_the_bundle_has_positions_and_rosters():
    data = gamedata.spawns()
    assert len(data["placements"]) == 8253
    assert len(data["spawners"]) >= 400
    assert data["cellSize"] == 25600


def test_it_covers_more_than_the_workaround_it_replaces():
    """
    The retired name-table workaround reached 348 species by intersecting a
    package's name table with the species list, and could only ever claim "this
    blueprint references this species" — see `backend/habitats.py`. This carries
    real level ranges and weights — so fewer species would mean the join is
    wrong, not that the game changed.
    """
    species = {
        e["speciesId"]
        for variants in gamedata.spawns()["spawners"].values()
        for v in variants
        for e in v["entries"]
        if not e["isNpc"]
    }
    assert len(species) >= 348
    assert len(species) == 482


def test_rosters_carry_real_level_ranges():
    entries = [
        e
        for variants in gamedata.spawns()["spawners"].values()
        for v in variants
        for e in v["entries"]
    ]
    assert len(entries) == 1892
    # Not all zeroes — that would mean the columns were missed.
    assert any(e["levelMax"] > 0 for e in entries)


def test_two_level_ranges_are_inverted_in_the_games_own_data():
    """
    `snow_orange_B` and `snow_orange_D` both list Pengullet at levelMin 35 and
    levelMax 34. Two of 1,892 entries, and it is Pocketpair's data rather than a
    parse error — everything around them reads correctly.

    **Not silently swapped.** Correcting it here would mean this bundle no longer
    reports what the file says, which is the property that makes it checkable at
    all. Same call as the one unresolved passive-prose disagreement
    (`FullStomach_Down_1_BossDefeat`): recorded, not explained away. A UI that
    wants to render "34-35" can sort at display time — that is presentation, not
    data.

    Pinned by count so a third one appearing is a visible change.
    """
    inverted = [
        e
        for variants in gamedata.spawns()["spawners"].values()
        for v in variants
        for e in v["entries"]
        if e["levelMin"] > e["levelMax"]
    ]
    assert len(inverted) == 2
    assert {e["speciesId"] for e in inverted} == {"Penguin"}


def test_the_rowname_placeholder_is_not_treated_as_a_species():
    """
    Unused variant rows carry the literal string `RowName` in their species
    column. It is a placeholder, not a Pal, and would otherwise appear as one.
    """
    species = {
        e["speciesId"]
        for variants in gamedata.spawns()["spawners"].values()
        for v in variants
        for e in v["entries"]
    }
    assert "RowName" not in species
    assert "None" not in species


# ─── The positional verification ─────────────────────────


@pytest.mark.integration
def test_every_position_lands_on_an_occupied_cell_and_controls_do_worse():
    """
    THE CHECK. `Location` is a natively-serialised Vector — 24 bytes, three
    doubles — which is an assumption until the cell grid agrees. It does, and
    both wrong cell sizes agree less, which is what makes it evidence rather
    than a coincidence.

    Needs the pak, so it is an integration test; it skips on a clean checkout.
    """
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
    try:
        import palpak
    except ImportError:
        pytest.skip("palpak not importable")

    try:
        pak = palpak.Pak()
    except Exception:  # noqa: BLE001 - refs/ absent on a clean checkout
        pytest.skip("server pak not present")

    cells = set()
    for path in pak.files:
        m = re.search(r"MainGrid_L0_X(-?\d+)_Y(-?\d+)", path)
        if m:
            cells.add((int(m.group(1)), int(m.group(2))))

    placements = gamedata.spawns()["placements"]

    def hits(size):
        return sum(
            1 for p in placements
            if (int(p["x"]) // size, int(p["y"]) // size) in cells
        )

    real = hits(25600)
    assert real == len(placements)
    # Both controls must be strictly worse or the test does not discriminate.
    assert hits(12800) < real
    assert hits(51200) < real


# ─── The lookup ──────────────────────────────────────────


def test_a_species_resolves_to_real_places_with_levels():
    points = gamedata.spawns_for("Kitsunebi")
    assert points
    first = points[0]
    assert {"x", "y", "z", "spawnerName", "levelMin", "levelMax"} <= set(first)
    assert first["levelMin"] <= first["levelMax"]


def test_alpha_forms_are_not_folded_into_their_base_species():
    """
    `pal()` strips `BOSS_` because an alpha Lamball is still called Lamball.
    Spawn points are different: an alpha spawns where it spawns, so folding
    would put base-form points on the alpha's map.
    """
    alpha = gamedata.spawns_for("BOSS_GrassMammoth")
    base = gamedata.spawns_for("GrassMammoth")
    assert alpha
    assert {(p["x"], p["y"]) for p in alpha} != {(p["x"], p["y"]) for p in base}


def test_lookup_is_case_insensitive():
    assert gamedata.spawns_for("kitsunebi")
    assert gamedata.spawns_for("KITSUNEBI")


def test_an_unspawned_species_is_empty_rather_than_an_error():
    """
    Encounter-only forms legitimately spawn nowhere — `_Oilrig` and `_Tower`
    variants are placed by encounter logic, not by world spawners. An empty list
    is the right answer and must not read as missing data.
    """
    assert gamedata.spawns_for("__not_a_species__") == []
    assert gamedata.spawns_for("") == []


# ─── The recorded discrepancy ────────────────────────────


def test_the_field_boss_disagreement_is_preserved_not_papered_over():
    """
    72 FieldBoss placements here against 90 in `boss_spawners.json.gz`, which
    comes from a different table. Neither is known to supersede the other, and
    assuming one was a superset is the kind of guess that produced "159 field
    bosses". Pinned so a future change to either is deliberate.
    """
    here = sum(
        1 for p in gamedata.spawns()["placements"] if p["type"] == "FieldBoss"
    )
    assert here == 72
    assert len(gamedata.boss_spawners()) == 90


# ─── Absence ─────────────────────────────────────────────


def test_a_missing_bundle_costs_the_layer_not_the_map(monkeypatch):
    monkeypatch.setattr(gamedata, "SPAWNS_PATH", "/nonexistent/spawns.json.gz")
    gamedata._reset_cache()
    assert gamedata.spawns() == {}
    assert gamedata.spawns_for("Kitsunebi") == []
