"""
Friendly-name resolution and reference totals.

The bundle is committed, so these run everywhere — no archive, no save file.
"""

from __future__ import annotations

import gzip
import json

import pytest

import gamedata
from gamedata import GameDataUnavailable


@pytest.fixture(autouse=True)
def _clean_cache():
    gamedata._reset_cache()
    yield
    gamedata._reset_cache()


# ─── The bundle itself ───────────────────────────────────────────


def test_bundle_is_present_and_loads():
    data = gamedata.load()
    assert data["items"] and data["pals"] and data["technology"]
    assert gamedata.available() is True


def test_missing_bundle_raises_a_useful_error(monkeypatch, tmp_path):
    monkeypatch.setattr(gamedata, "DATA_PATH", str(tmp_path / "absent.json.gz"))
    gamedata._reset_cache()
    with pytest.raises(GameDataUnavailable, match="build-gamedata"):
        gamedata.load()
    assert gamedata.available() is False


def test_corrupt_bundle_raises_rather_than_crashing(monkeypatch, tmp_path):
    bad = tmp_path / "bad.json.gz"
    with gzip.open(bad, "wt", encoding="utf-8") as f:
        f.write("{not json")
    monkeypatch.setattr(gamedata, "DATA_PATH", str(bad))
    gamedata._reset_cache()
    with pytest.raises(GameDataUnavailable):
        gamedata.load()


# ─── Case-insensitivity: the thing that silently lost 8 Pals ─────


@pytest.mark.parametrize(
    "save_id,expected",
    [
        ("Sheepball", "Lamball"),          # data spells it SheepBall
        ("OctopusGirl", "Gloopie"),        # data has a typo: OctopusGIrl
        ("SwordCutlassfish", "Skutlass"),  # data: SwordCutlassFish
        ("CowPal", "Mozzarina"),           # data: Cowpal
        ("BluePlatypus", "Fuack"),         # data: Blueplatypus
        ("VolcanicMonster", "Reptyro"),    # data: Volcanicmonster
        ("BadCatgirl", "Nyafia"),          # data: BadCatGirl
    ],
)
def test_pal_lookup_is_case_insensitive(save_id, expected):
    """
    These IDs appear verbatim in a real save's PaldeckUnlockFlag but are spelled
    differently in the reference data. Exact matching loses all seven.
    """
    assert gamedata.pal_name(save_id) == expected


def test_item_lookup_is_case_insensitive():
    assert gamedata.item_name("aicore") == gamedata.item_name("AIcore")


# ─── Species prefixes ────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,species,variants",
    [
        ("Anubis", "Anubis", []),
        ("BOSS_Anubis", "Anubis", ["BOSS"]),
        ("PREDATOR_Anubis", "Anubis", ["PREDATOR"]),
        ("BOSS_PREDATOR_Anubis", "Anubis", ["BOSS", "PREDATOR"]),
    ],
)
def test_species_prefixes_are_stripped(raw, species, variants):
    assert gamedata.normalise_species(raw) == (species, variants)
    assert gamedata.pal_name(raw) == "Anubis"
    assert gamedata.describe_pal(raw)["variants"] == variants


def test_alpha_pal_resolves_to_its_species():
    described = gamedata.describe_pal("BOSS_Anubis")
    assert described["name"] == "Anubis"
    assert described["known"] is True
    assert "BOSS" in described["variants"]


# ─── Fallbacks ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("PalEgg_Dragon", "Pal Egg Dragon"),
        ("AIcore", "AIcore"),
        ("some_thing", "some thing"),
        ("", ""),
    ],
)
def test_humanize(raw, expected):
    assert gamedata.humanize(raw) == expected


def test_unknown_ids_fall_back_rather_than_failing():
    assert gamedata.item_name("MysteryWidget_02") == "Mystery Widget 02"
    assert gamedata.pal_name("UnknownCreature") == "Unknown Creature"
    assert gamedata.describe_item("MysteryWidget")["known"] is False


def test_humanize_leaves_acronyms_alone():
    """
    Documented limitation: an uppercase run followed by a word is not split,
    because no rule can tell `AIcore` from `NotAReal`. Anything with a reference
    entry resolves before reaching humanize, so this only affects unknown IDs.
    """
    assert gamedata.humanize("AIcore") == "AIcore"
    assert gamedata.humanize("NotAReal") == "Not AReal"


def test_empty_id_is_handled():
    assert gamedata.item_name("") == ""
    assert gamedata.item("") is None


# ─── Known-good resolutions ──────────────────────────────────────


def test_resolves_real_item_names():
    assert gamedata.item_name("AIcore") == "AI Core"
    assert gamedata.item_name("Wood") == "Wood"


def test_describe_item_shape():
    described = gamedata.describe_item("AIcore")
    assert described["name"] == "AI Core"
    assert described["known"] is True
    assert described["maxStack"] > 0
    assert set(described) >= {"id", "name", "icon", "rarity", "typeA", "maxStack"}


def test_max_stack_is_available():
    """The authoritative ceiling the sorter currently has to infer."""
    assert gamedata.max_stack("Wood") > 1
    assert gamedata.max_stack("NotAnItem") == 0


def test_active_skill_id_prefix_is_stripped():
    assert gamedata.skill_name("EPalWazaID::PowerBall") == gamedata.skill_name("PowerBall")
    assert not gamedata.skill_name("EPalWazaID::PowerBall").startswith("EPal")


def test_npc_names_resolve():
    assert gamedata.npc_name("Believer_CrossBow") == "Free Pal Alliance Believer"


def test_character_name_covers_pals_and_npcs():
    """CharacterSaveParameterMap holds humans as well as Pals."""
    assert gamedata.character_name("Sheepball") == "Lamball"
    assert gamedata.character_name("Believer_CrossBow") == "Free Pal Alliance Believer"
    assert gamedata.character_name("BOSS_Anubis") == "Anubis"


# ─── Totals ──────────────────────────────────────────────────────


def test_technology_totals_are_exact():
    """
    Computed from the game's data tables, not sourced from a wiki:
    537 standard technologies worth 1,413 points, 51 boss technologies worth 185.
    """
    totals = gamedata.totals()
    assert totals["technologyPoints"] == 1413
    assert totals["ancientTechnologyPoints"] == 185
    assert totals["technologyCount"] == 537
    assert totals["ancientTechnologyCount"] == 51


def test_technology_costs_sum_to_the_totals():
    """Guard against the totals drifting from the underlying records."""
    data = gamedata.load()
    standard = sum(t["cost"] for t in data["technology"].values() if not t["isBossTech"])
    boss = sum(t["cost"] for t in data["technology"].values() if t["isBossTech"])
    assert standard == gamedata.totals()["technologyPoints"]
    assert boss == gamedata.totals()["ancientTechnologyPoints"]


def test_paldeck_has_both_denominators():
    """
    A save's PaldeckUnlockFlag keys on forms, not Paldeck numbers — variants
    share a number with a letter suffix — so the two differ and the form count
    is the one to measure completion against.
    """
    totals = gamedata.totals()
    assert totals["paldeckForms"] == 303
    assert totals["paldeckNumbers"] == 204
    assert totals["paldeckForms"] > totals["paldeckNumbers"]


# ─── Fast travel ─────────────────────────────────────────────────


def test_fast_travel_points_are_complete_and_positioned():
    points = gamedata.fast_travel_points()
    assert len(points) == 174
    for point in points:
        assert isinstance(point["x"], (int, float))
        assert isinstance(point["y"], (int, float))
        assert point["name"]


def test_fast_travel_covers_both_landmasses():
    """
    Palworld 1.0 has Palpagos and the World Tree region in one continuous
    coordinate space. If a future data drop covered only the original island,
    this catches it.
    """
    points = gamedata.fast_travel_points()
    world_tree = [p for p in points if p["x"] > 300000]
    palpagos = [p for p in points if p["x"] <= 300000]

    assert len(palpagos) == 157
    assert len(world_tree) == 17
    assert any("Root" in p["name"] for p in world_tree)


def test_landmasses_are_cleanly_separated():
    """
    The two regions need separate map images and separate transforms, and the
    code decides which by thresholding world X at 300,000. That only works if
    there is a real gap — verify nothing sits near the boundary.
    """
    points = gamedata.fast_travel_points()
    palpagos_max = max(p["x"] for p in points if p["x"] <= 300000)
    world_tree_min = min(p["x"] for p in points if p["x"] > 300000)

    assert palpagos_max < 300000 < world_tree_min
    assert world_tree_min - palpagos_max > 150000, "landmasses are not clearly separated"


def test_fast_travel_names_are_localized_not_internal():
    names = [p["name"] for p in gamedata.fast_travel_points()]
    assert "Hill of Beginnings" in names
    assert not any(n.startswith("WorldTree_") for n in names)


# ─── Effigies ────────────────────────────────────────────────────
#
# Extracted from the game pak rather than published anywhere. The GUID is the
# part that matters: it is what a save's RelicObtainForInstanceFlag keys on, so
# it is what makes "which have I not found" answerable.


def test_effigies_load():
    points = gamedata.effigies()
    assert len(points) == 396, "the bundled effigy set changed — re-run the extractor"


def test_every_effigy_has_a_position_and_a_guid():
    for effigy in gamedata.effigies():
        assert len(effigy["guid"]) == 32, effigy
        assert effigy["guid"] != "0" * 32
        assert isinstance(effigy["x"], (int, float))
        assert isinstance(effigy["y"], (int, float))


def test_effigy_guids_are_unique():
    """A duplicate would make one effigy permanently unfindable in the join."""
    guids = [e["guid"] for e in gamedata.effigies()]
    assert len(set(guids)) == len(guids)


def test_effigies_split_across_both_landmasses():
    by_land: dict[str, int] = {}
    for effigy in gamedata.effigies():
        by_land[effigy["landmass"]] = by_land.get(effigy["landmass"], 0) + 1
    assert by_land == {"palpagos": 351, "worldtree": 45}


def test_missing_effigy_data_degrades_rather_than_raises(monkeypatch):
    """A missing bundle should cost the map a layer, not break the backend."""
    monkeypatch.setattr(gamedata, "EFFIGY_PATH", "/nonexistent/effigies.json.gz")
    monkeypatch.setattr(gamedata, "_effigies", None)
    assert gamedata.effigies() == []
