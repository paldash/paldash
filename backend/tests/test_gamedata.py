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


# ─── Effigy kind names ───────────────────────────────────


def test_an_effigy_kind_resolves_through_the_pal_table_not_a_string_tidy():
    """
    `BP_LevelObject_Relic_SheepBall` is a Lamball effigy, and only the Pal table
    knows that. The map's generic class prettifier rendered it "Relic Sheep
    Ball" — de-underscoring is not naming, and that is what players saw in the
    effigy filter list.
    """
    assert gamedata.effigy_kind_name("BP_LevelObject_Relic_SheepBall") == "Lamball Effigy"


def test_effigy_species_lookup_is_case_insensitive():
    """
    The extraction yields `SheepBall`; the Pal table spells it `Sheepball`. An
    exact match falls through to the raw name for exactly the entries this
    exists to fix.
    """
    assert gamedata.effigy_kind_name("BP_LevelObject_Relic_sheepball") == "Lamball Effigy"


def test_the_unsuffixed_classes_are_lifmunk_effigies():
    """
    **This test used to assert "Effigy" and that was the bug it was protecting.**
    The reasoning was that the two unsuffixed classes are "genuinely not
    species-tied", which is true of the *class name* and says nothing about the
    thing. 155 of 396 markers were therefore labelled with their own category
    word, which reads as a failed lookup rather than as an answer.

    The catalogue has always had the name: `Relic` is "Lifmunk Effigy". See
    `test_effigy_names.py`, where the suffix rule is checked against all thirteen
    catalogue entries rather than against this file's memory of them.

    The empty-kind case keeps the old answer, because there no id was supplied at
    all — that is a caller with nothing to resolve, not a plain relic.
    """
    assert gamedata.effigy_kind_name("BP_LevelObject_Relic") == "Lifmunk Effigy"
    assert gamedata.effigy_kind_name("BP_RelicObject") == "Lifmunk Effigy"
    assert gamedata.effigy_kind_name("") == "Effigy"


def test_every_bundled_effigy_gets_a_name_with_no_class_left_showing():
    names = {e["kindName"] for e in gamedata.effigies()}
    assert names, "the effigy bundle should be present"
    assert not any("BP_" in n or "_" in n for n in names), sorted(names)


# ── bestWorkSuitability ───────────────────────────────────────────────────


def test_every_species_carries_its_best_work_suitability():
    """
    `DT_PalMonsterParameter.BestWorkSuitability`, 753 of 753 resolved.

    Bundled because it is the **per-species half of the condenser mechanic** —
    raising a Pal's condenser rank raises its suitability for this work type
    only. The *size* of that increase is in no data file (see the accessor's
    docstring for where it was searched), so this ships the fact and no
    arithmetic.
    """
    pals = gamedata.load()["pals"]
    with_best = [k for k, v in pals.items() if v.get("bestWorkSuitability")]
    # Not all 753: species with an entirely empty work table have no best, which
    # is the game's answer rather than a gap.
    assert len(with_best) > 700, len(with_best)


def test_best_work_matches_the_species_own_strongest_work():
    """
    A cross-check against a *different* column. `BestWorkSuitability` is a
    separate field from the thirteen `WorkSuitability_*` values, so the two
    agreeing is evidence the column was read correctly rather than the
    extraction restating itself.
    """
    assert gamedata.best_work_suitability("Umihebi_Fire") == "EmitFlame"
    entry = gamedata._lookup("pals", "Umihebi_Fire")
    assert entry["workSuitabilities"]["EmitFlame"] == 7
    # The water form is the same species line with a different best work.
    assert gamedata.best_work_suitability("Umihebi") == "Watering"


def test_boss_forms_resolve_to_their_species_best_work():
    """An alpha is the same species — the same rule `pal_name` follows."""
    assert (
        gamedata.best_work_suitability("BOSS_Umihebi_Fire")
        == gamedata.best_work_suitability("Umihebi_Fire")
    )


def test_no_best_work_and_unknown_id_both_return_None():
    """
    Both mean "do not claim a condenser bonus applies". Panthalus genuinely has
    an empty work table — that is the game's answer, documented elsewhere in
    this repo — and an unknown id must not fabricate one either.
    """
    assert gamedata.best_work_suitability("Panthalus") is None
    assert gamedata.best_work_suitability("NoSuchPalAnywhere") is None
    assert gamedata.best_work_suitability("") is None


# ── species moves ─────────────────────────────────────────────────────────


def test_egg_pools_resolve_for_ordinary_species_not_just_alphas():
    """
    **All 283 egg pools are keyed on the `BOSS_` form** — the table contains
    zero unprefixed keys. A first attempt looked the species up exactly and fell
    back to the stem, which meant `Carbunclo` reported 0 egg moves when it has
    9, silently, on every ordinary Pal in the game.

    A feature that works only for alphas is not a feature, and 283 pools against
    753 forms does not divide into "some species have them".
    """
    base = gamedata.species_moves("Carbunclo")
    alpha = gamedata.species_moves("BOSS_Carbunclo")
    assert base["eggCount"] == alpha["eggCount"] > 0
    assert [m["id"] for m in base["egg"]] == [m["id"] for m in alpha["egg"]]


def test_egg_moves_are_flagged_as_egg_only():
    """A caller must be able to say "breed for this" rather than presenting an
    unobtainable move as available on a Pal that already exists."""
    moves = gamedata.species_moves("Anubis")
    assert moves["egg"]
    assert all(m["eggOnly"] is True for m in moves["egg"])


def test_level_up_moves_carry_the_level_they_are_learned_at():
    moves = gamedata.species_moves("Alpaca")
    assert moves["levelUp"]
    first = moves["levelUp"][0]
    assert first["level"] >= 1
    # Named and described, not a bare id — the Pal view shows equipped moves
    # and said nothing about what a species could have.
    assert first["name"] and first["name"] != first["id"]
    assert first["element"]


def test_pools_are_shared_between_species_and_that_is_the_game_not_a_bug():
    """
    283 pools resolve to **78 distinct** sets, so Lamball and Carbunclo really
    do share one. Worth pinning: identical output for two unrelated species is
    exactly what a collapsed lookup looks like, and this says it is not.
    """
    pools = gamedata.moves()["eggMoves"]
    distinct = {tuple(sorted(v)) for v in pools.values()}
    assert len(pools) == 283
    assert 1 < len(distinct) < len(pools)


def test_a_pal_skin_gets_a_readable_label():
    """
    **The game ships no display name for a skin.** `DT_SkinDataTable`'s
    `SkinName` column repeats the id and `item_name` humanises it, so an
    equipped skin rendered as "Jet Dragon Skin001" beside a Pal the dashboard
    correctly calls Jetragon. The species half of the id resolves, so the label
    is derived from that — and says it is.
    """
    import gamedata

    skin = gamedata.skin_label("JetDragon_Skin001")
    assert skin["label"] == "Jetragon — Skin 1"
    assert skin["palName"] == "Jetragon"
    assert skin["variant"] == 1
    assert skin["derived"] is True
    # Variant forms resolve through the same lookup as everything else.
    assert gamedata.skin_label("LilyQueen_Dark_Skin002")["label"] == "Lyleen Noct — Skin 2"


def test_an_unresolvable_skin_id_returns_None_rather_than_a_guess():
    """
    Fails safe to the raw id, which is what the UI showed before. A label built
    on a species that did not resolve would just be the unreadable string this
    exists to replace, dressed up as an answer.
    """
    import gamedata

    assert gamedata.skin_label("Nonsense_Thing") is None
    assert gamedata.skin_label("NoSuchSpecies_Skin001") is None
    assert gamedata.skin_label("") is None
    assert gamedata.skin_label(None) is None


def test_pal_drops_keys_EXACTLY_so_an_alpha_is_not_the_ordinary_form():
    """
    **364 of the 890 drop rows are `BOSS_`-prefixed and they are not a richer
    version of the ordinary table.** Anubis gives Bone and a Large Pal Soul;
    `BOSS_Anubis` gives Ancient Civilization Parts and Precious Entrails.

    `pal()` strips the prefix — right for naming, since an alpha Lamball is
    still called Lamball — and would hand back the wrong loot here. Same trap
    `palstats` documents for stat scaling, where the alpha bonus lives in the
    prefixed row.
    """
    import gamedata

    ordinary = gamedata.pal_drops("Anubis")
    alpha = gamedata.pal_drops("BOSS_Anubis")
    assert ordinary and alpha
    assert ordinary != alpha

    names = {i["name"] for b in ordinary for i in b["items"]}
    alpha_names = {i["name"] for b in alpha for i in b["items"]}
    assert "Bone" in names
    assert "Bone" not in alpha_names


def test_level_bands_are_kept_apart_because_their_contents_differ():
    """
    128 species have more than one band, and they are different tables rather
    than a richer version of the first. Merging them would invent a drop list
    the game does not ship; showing only the first is confidently wrong about
    every endgame Pal.
    """
    import gamedata

    bands = gamedata.pal_drops("Anubis")
    assert [b["levelFrom"] for b in bands] == [0, 80]
    early = {i["name"] for i in bands[0]["items"]}
    late = {i["name"] for i in bands[1]["items"]}
    assert early & late == set(), "the two bands should share nothing here"


def test_a_species_with_no_drop_row_returns_empty_rather_than_raising():
    import gamedata

    assert gamedata.pal_drops("NoSuchPal") == []
    assert gamedata.pal_drops("") == []
