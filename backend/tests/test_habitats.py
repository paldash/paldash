"""
Per-species spawn habitats, bundled from the game pak.

The claim this data makes is narrower than it looks, and the tests pin the
narrow version: a habitat says "spawners placed in these cells reference this
species", not "this species spawns here at this rate". See
`scripts/extract-pal-habitats.py` for why — the blueprints are cooked with
unversioned properties, so the species list comes from the name table.
"""

import pytest

import habitats


pytestmark = pytest.mark.skipif(
    not habitats.available(), reason="habitat data not bundled"
)


def test_bundle_has_the_documented_shape():
    """
    If these move, the docs and the Paldeck's footer are stale.

    **The figures changed on 2026-08-05 when the source did**, and the change is
    the feature: the name-table workaround reported 348 species and an
    *attribution rate* of 13,440 guessed out of 13,851 spawners, because its
    whole difficulty was not knowing which species a spawner held.
    `DT_PalWildSpawner` says outright, so every placement naming a defined
    spawner is attributed exactly — 478 species, and the count here is coverage
    rather than a success rate.
    """
    s = habitats.summary()
    assert s["available"] is True
    assert s["species"] == 478
    assert s["spawnersTotal"] == 8_253
    # 71 placements name a spawner absent from the definition table.
    assert s["spawnersMatched"] == 8_182
    # Same cell size as every other spatial figure in this project.
    assert s["cellSize"] == 25600.0


def test_habitats_now_carry_a_level_range_which_the_workaround_could_not():
    """
    The old source could say *where* and never *what level*. This is the single
    biggest gain: "Melpaca, levels 5-17" is a different answer from a shaded
    blob, and it comes from the game rather than from an inference.
    """
    entry = habitats.for_species("Alpaca")
    assert entry is not None
    assert entry["levelMin"] > 0
    assert entry["levelMax"] >= entry["levelMin"]


def test_weight_is_labelled_as_within_group_only():
    """
    A weight is a real relative rate **inside one spawner group** and is not a
    global spawn rate — two groups' weights are not comparable and nothing says
    how often a spawner fires. The bundle says so, so a caller cannot quietly
    treat it as one.
    """
    assert habitats.load()["weightIsWithinGroup"] is True


def test_lookup_is_case_insensitive():
    """
    Third module to need this. The save says `Sheepball`, palcalc `SheepBall`,
    the game data its own spelling; callers pass whatever their source gave them.
    """
    canonical = habitats.for_species("SheepBall")
    assert canonical["known"] is True
    for spelling in ("sheepball", "SHEEPBALL", "SheepBall"):
        assert habitats.for_species(spelling)["cells"] == canonical["cells"]


def test_unknown_species_is_empty_not_an_error():
    """Plenty of Pals have no spawner: tower bosses, raid-only, breeding-only."""
    entry = habitats.for_species("NotARealPal")
    assert entry["known"] is False
    assert entry["cells"] == []
    assert entry["regions"] == []
    assert entry["spawnerCount"] == 0


def test_regions_are_cell_sized_boxes_in_world_space():
    entry = habitats.for_species("SheepBall")
    assert entry["regions"]
    for region in entry["regions"]:
        assert region["width"] == habitats.CELL_SIZE
        assert region["height"] == habitats.CELL_SIZE
        assert region["landmass"] in ("palpagos", "worldtree")
        # The landmass split has to agree with the x threshold the map uses.
        expected = "worldtree" if region["x"] > 300_000 else "palpagos"
        assert region["landmass"] == expected


def test_regions_match_cells_one_for_one():
    entry = habitats.for_species("SheepBall")
    assert len(entry["regions"]) == len(entry["cells"])


def test_merge_unions_location_variants_rather_than_picking_one():
    """
    `HadesBird` and `HadesBird_Electric` are one Paldeck entry (Helzephyr) whose
    forms spawn in different places. Showing either alone hides part of the range.
    """
    base = habitats.for_species("HadesBird")
    variant = habitats.for_species("HadesBird_Electric")
    assert base["known"] and variant["known"]
    assert base["cells"] != variant["cells"]

    merged = habitats.merged(["HadesBird", "HadesBird_Electric"])
    cells = {tuple(c) for c in merged["cells"]}
    assert cells == {tuple(c) for c in base["cells"]} | {tuple(c) for c in variant["cells"]}
    assert merged["spawnerCount"] == base["spawnerCount"] + variant["spawnerCount"]
    assert merged["mergedFrom"] == ["HadesBird", "HadesBird_Electric"]


def test_encounter_only_variants_have_no_habitat_and_that_is_correct():
    """
    `_Oilrig` and `_Tower` forms are placed by encounter logic, not by the
    spawners scattered through the streaming cells — so they have no habitat and
    must not be read as missing data. This is why the Paldeck merges variants:
    the entry still gets a map, from the form that does spawn wild.
    """
    for species in ("HadesBird_Oilrig", "Baphomet_Dark_Oilrig",
                    "GrassPanda_Electric_Tower"):
        assert habitats.for_species(species)["known"] is False

    # ...and merging them in changes nothing, rather than emptying the result.
    merged = habitats.merged(["HadesBird", "HadesBird_Oilrig"])
    assert merged["known"] is True
    assert merged["cells"] == habitats.for_species("HadesBird")["cells"]


def test_merge_deduplicates_overlapping_cells():
    """Variants share ground; a cell counted twice would shade twice as dark."""
    merged = habitats.merged(["SheepBall", "SheepBall"])
    single = habitats.for_species("SheepBall")
    assert merged["cells"] == single["cells"]


def test_merge_of_unknown_ids_is_empty_not_an_error():
    merged = habitats.merged(["NotARealPal", "AlsoNotReal"])
    assert merged["known"] is False
    assert merged["cells"] == []


def test_a_common_pal_has_a_wider_range_than_a_rare_one():
    """
    A weak but real sanity check on the extraction: if these ever invert, the
    sheet-to-species join has gone wrong in a way counts alone would not show.
    """
    common = habitats.for_species("SheepBall")      # Lamball, a starter
    rare = habitats.for_species("Anubis")
    assert len(common["cells"]) > len(rare["cells"])


def test_reload_rereads_from_disk():
    before = habitats.summary()["species"]
    result = habitats.reload()
    assert result["loaded"] is True
    assert result["species"] == before
