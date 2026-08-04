"""
Breeding calculator, and specifically the 1.0 data merge.

The merge layers the game's own tables (from `refs/`) over the palcalc pair
table. These tests pin what that merge is *for*: one corrected result, one
genuinely new Pal, and the casing trap. Without them a future regeneration
could silently undo any of it.

`scripts/build-breedingdata.py` documents why this is a merge rather than a
regeneration — reconstructing the combi-rank formula agreed with the known-good
table only 77.5% of the time.
"""

from __future__ import annotations

import pytest

import breeding


@pytest.fixture(autouse=True)
def _data_or_skip():
    if not breeding.data_available():
        pytest.skip("bundled breeding data missing")


# ─── The corrections this merge exists for ───────────────────────


def test_the_contradictory_combination_is_left_alone():
    """
    CatMage x FoxMage is NOT a correction, despite first appearing to be one.

    The game's `unique_combos` lists this pair twice — same parent order —
    producing FoxMage_Dark and CatMage_Fire, and `child_to_parents_unique` names
    the pair as the unique parents of both. It genuinely has two outcomes.

    The build script therefore refuses to touch it rather than picking whichever
    came last in the file, and the base table's answer stands. Changing a
    user-visible result on the strength of self-contradictory data would be
    worse than leaving it.
    """
    assert breeding.predict_child("CatMage", "FoxMage") == "FoxMage_Dark"
    assert breeding.predict_child("FoxMage", "CatMage") == "FoxMage_Dark"


def test_astralym_is_present_and_breedable():
    """Paldeck #204, absent from the palcalc table entirely."""
    info = breeding.pal_info("WorldTreeDragon")

    assert info["known"] is True
    assert info["name"] == "Astralym"
    assert info["dex"] == 204
    assert info["unreleased"] is False


def test_astralym_has_real_pair_coverage():
    """Not just a name — the merge brought its full partner table with it."""
    partners = ["Alpaca", "Anubis", "SheepBall", "Penguin"]
    children = [breeding.predict_child("WorldTreeDragon", p) for p in partners]

    assert all(c for c in children), "Astralym pairs are missing from the table"


# ─── Casing ──────────────────────────────────────────────────────


def test_the_existing_platypus_spelling_is_preserved():
    """
    `refs/` spells this `Blueplatypus`; the save and our table say
    `BluePlatypus`. The merge folds onto the existing spelling — adding both
    would put a duplicate Fuack in every dropdown.
    """
    assert breeding.pal_info("BluePlatypus")["known"] is True
    assert breeding.pal_info("BluePlatypus")["name"] == "Fuack"

    names = [p["internalName"] for p in breeding.all_pals()]
    assert "Blueplatypus" not in names, "both spellings ended up in the Pal list"


def test_no_pair_references_a_pal_we_cannot_name():
    """
    A pair keyed on a name with no Pal record is unreachable — the exact bug the
    canonicalisation step in the build script prevents.
    """
    known = set(breeding._db()["pals"])
    referenced = set()
    for key, child in breeding._breeding()["pairs"].items():
        referenced.update(key.split("+"))
        referenced.add(child)

    assert not (referenced - known), f"pairs reference unknown Pals: {sorted(referenced - known)[:10]}"


# ─── Unreleased content ──────────────────────────────────────────


def test_unreleased_pals_are_flagged_not_dropped():
    """Their pair data is right if they ship; they just are not obtainable now."""
    assert breeding.is_unreleased("CandleWitch") is True
    assert breeding.pal_info("CandleWitch")["known"] is True


def test_unreleased_pals_are_not_offered_as_breeding_goals():
    listed = {p["internalName"] for p in breeding.all_pals()}

    assert "CandleWitch" not in listed
    assert "WorldTreeDragon" in listed, "a released Pal was filtered out"


def test_unreleased_pals_can_still_be_listed_deliberately():
    listed = {p["internalName"] for p in breeding.all_pals(include_unreleased=True)}
    assert "CandleWitch" in listed


# ─── Regression guards on the base table ─────────────────────────


def test_the_table_did_not_shrink():
    """The merge adds; it must never drop what palcalc already had."""
    assert len(breeding._breeding()["pairs"]) >= 44850
    assert len(breeding._db()["pals"]) >= 299


def test_a_well_known_special_combination_still_works():
    """Relaxaurus x Sparkit -> Relaxaurus Lux, using internal names."""
    assert breeding.predict_child("LazyDragon", "ElecCat") == "LazyDragon_Electric"


def test_same_species_breeds_true():
    assert breeding.predict_child("Alpaca", "Alpaca") == "Alpaca"


def test_pair_order_does_not_matter():
    for a, b in [("Alpaca", "Deer"), ("Anubis", "SheepBall"), ("Penguin", "Bastet")]:
        assert breeding.predict_child(a, b) == breeding.predict_child(b, a)


def test_an_unknown_pal_predicts_nothing():
    assert breeding.predict_child("NotARealPal", "Alpaca") is None


# ─── Where a countable parent actually is ────────────────────────


def _pal(species, gender, **extra):
    return {
        "speciesId": species, "gender": gender, "level": 10, "rank": 1,
        "instanceId": f"{species}-{gender}-{extra.get('location', 'palbox')}",
        "ivs": {}, "passiveSkills": [], **extra,
    }


def test_a_species_records_where_its_copies_are():
    """
    The count includes base workers and shared-store Pals because they are
    breedable. The note is what stops that being a surprise: "pair your two
    Lamballs" is a bad instruction if one is standing in a base.
    """
    summary = breeding.summarize_palbox([
        _pal("SheepBall", "Male", location="palbox"),
        _pal("SheepBall", "Female", location="base"),
    ])
    entry = next(s for s in summary["species"] if s["internalName"] == "SheepBall")
    assert entry["count"] == 2
    assert entry["locations"] == {"palbox": 1, "base": 1}


def test_a_stored_pal_is_named_by_its_structure_not_by_the_word_storage():
    """`storage` does not tell anyone where to walk; the structure name does."""
    summary = breeding.summarize_palbox([
        _pal("SheepBall", "Male", location="storage",
             storageKind="Dimensional Pal Storage"),
    ])
    entry = next(s for s in summary["species"] if s["internalName"] == "SheepBall")
    assert entry["locations"] == {"Dimensional Pal Storage": 1}


def test_an_individual_carries_its_own_location():
    summary = breeding.summarize_palbox([_pal("SheepBall", "Male", location="base")])
    entry = next(s for s in summary["species"] if s["internalName"] == "SheepBall")
    assert entry["individuals"][0]["location"] == "base"
