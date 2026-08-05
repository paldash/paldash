"""
Map object categorisation and the world/base split.

The split is what lets the map separate "what is out there to find" from "what my
guild built" — two layers that want completely different default visibility.
"""

from __future__ import annotations

import pytest

from parser import ZERO_GUID, _categorise


@pytest.mark.parametrize(
    "object_id,expected",
    [
        # World loot, most specific first.
        ("TreasureBox_FishingJunk_RequiredLongHold", "fishingJunk"),
        ("TreasureBox_FishingJunk_RequiredLongHold2", "fishingJunk"),
        ("TreasureBox_Oilrig", "oilrigChest"),
        ("TreasureBox", "chest"),
        ("TreasureBox_RequiredLongHold", "chest"),
        ("TreasureBox_Electric", "chest"),
        ("ItemChest_02", "chest"),
        ("GuildChest", "chest"),
        # Natural resource nodes.
        ("DamagableRock0009", "oreNode"),
        ("DamagableWood0001", "oreNode"),
        ("MeteorDrop_Damagable", "oreNode"),
        # Base structures.
        ("PalBoxV2", "palbox"),
        # Two structures, and this row used to hold the wrong one. `MonsterFarm`
        # is the Ranch; the Breeding Farm is `BreedFarm` and matched no category
        # at all, so every one of them was dropped before reaching the map.
        ("MonsterFarm", "ranch"),
        ("BreedFarm", "breeding"),
        ("FarmBlockV2_tomato", "farm"),
        ("DefenseWall_Wood", "defense"),
        ("CoalPit", "production"),
        ("CopperPit_2", "production"),
        ("BlastFurnace3", "production"),
        ("CoolerBox", "storage"),
        ("MedicalPalBed_02", "comfort"),
        ("MultiHatchingPalEgg", "egg"),
        ("CommonDropItem3D", "drop"),
        # Not points of interest — walls, floors, decoration.
        ("Wooden_roof", None),
        ("JapaneseStyle_Pillar", None),
        ("Stone_Stair", None),
        ("", None),
    ],
)
def test_categorisation(object_id, expected):
    assert _categorise(object_id) == expected


def test_fishing_junk_is_not_lumped_in_with_chests():
    """
    Ordering matters: `TreasureBox_FishingJunk...` also matches the generic
    chest pattern, and a real world has ~600 of them. Letting them fall into
    `chest` would bury the ~2,300 chests that are actually worth visiting.
    """
    assert _categorise("TreasureBox_FishingJunk_RequiredLongHold") == "fishingJunk"
    assert _categorise("TreasureBox_Oilrig") == "oilrigChest"


@pytest.mark.integration
def test_world_and_base_placement_split(palsav_available, level_sav):
    """
    An object belongs to a base camp, or the world placed it. Both groups must be
    non-empty and every object must land in exactly one of them.
    """
    from parser import extract_map_objects, load_gvas

    objects = extract_map_objects(load_gvas(level_sav))
    assert objects

    world = [o for o in objects if o["worldPlaced"]]
    based = [o for o in objects if not o["worldPlaced"]]

    assert world and based
    assert len(world) + len(based) == len(objects)

    # World-placed objects carry no base camp; base-placed ones always do.
    assert all(o["baseCampId"] == "" for o in world)
    assert all(o["baseCampId"] not in ("", "None", ZERO_GUID) for o in based)


@pytest.mark.integration
def test_world_placed_objects_are_mostly_loot_and_resources(palsav_available, level_sav):
    """Chests and ore nodes are placed by the world, not by players."""
    from parser import extract_map_objects, load_gvas

    objects = extract_map_objects(load_gvas(level_sav))
    world = [o for o in objects if o["worldPlaced"]]

    categories = {o["category"] for o in world}
    assert {"chest", "oreNode"} <= categories

    # Palboxes are always player-built, so none may appear as world-placed.
    assert not [o for o in world if o["category"] == "palbox"]


@pytest.mark.integration
def test_every_object_has_usable_coordinates(palsav_available, level_sav):
    from parser import extract_map_objects, load_gvas

    objects = extract_map_objects(load_gvas(level_sav))
    for o in objects:
        assert isinstance(o["x"], float) and isinstance(o["y"], float)
    # A world where everything sits at the origin means the transform cache was
    # read wrongly.
    assert sum(1 for o in objects if o["x"] == 0.0 and o["y"] == 0.0) < len(objects) * 0.01
