"""
Species lookups in `breeding.py` must be case-insensitive.

The sources disagree on capitalisation and always have: a save stores
`Sheepball`, `OctopusGirl`, `SwordCutlassfish`; palcalc's table spells them
`SheepBall`, `OctopusGirl`, `SwordCutlassFish`. `gamedata.py` was fixed for this
early — see its module docstring — but `breeding.py` kept doing exact `dict.get`
until 2026-07-30.

The visible symptom was a breeding path rendering internal ids ("Sheepball +
ElecCat" instead of "Lamball + Sparkit"). The **worse**, invisible symptom was
that `_breedable` used the same exact match, so those Pals were classified
unbreedable and dropped from the palbox summary entirely — and `_pair_key` joins
raw ids, so every pair involving them missed in the table.

That is why canonicalisation happens at the boundary rather than in `pal_info`:
fixing only the display would have left the names right and the breeding maths
wrong.
"""

import pytest

import breeding


pytestmark = pytest.mark.skipif(
    not breeding.data_available(), reason="breeding data not bundled"
)

# left: the spelling a real save uses. right: what a player should see.
SAVE_SPELLINGS = [
    ("Sheepball", "Lamball"),
    ("OctopusGirl", "Gloopie"),
    ("SwordCutlassfish", "Skutlass"),
    ("ElecCat", "Sparkit"),
]


@pytest.mark.parametrize("species_id,display", SAVE_SPELLINGS)
def test_save_spellings_resolve_to_friendly_names(species_id, display):
    info = breeding.pal_info(species_id)
    assert info["name"] == display
    assert info["known"] is True


@pytest.mark.parametrize("species_id,_display", SAVE_SPELLINGS)
def test_save_spellings_count_as_breedable(species_id, _display):
    """The regression that mattered: these were being skipped, not just misnamed."""
    assert breeding._breedable({"speciesId": species_id}) is True


def test_lookup_is_case_insensitive_in_both_directions():
    assert breeding.pal_info("eleccat")["name"] == "Sparkit"
    assert breeding.pal_info("ELECCAT")["name"] == "Sparkit"
    assert breeding.pal_info("ElecCat")["name"] == "Sparkit"


def test_pair_lookup_works_from_a_save_spelling():
    """`_pair_key` joins raw ids, so an uncanonicalised parent misses the table."""
    canonical = breeding.predict_child("SheepBall", "ElecCat")
    assert canonical is not None
    assert breeding.predict_child("sheepball", "eleccat") == canonical


def test_canonical_species_passes_through_unknown_ids():
    """An id we cannot place must survive unchanged, so callers can still test it."""
    assert breeding.canonical_species("NotARealPal") == "NotARealPal"


def test_unknown_species_still_gets_a_readable_name():
    """
    palcalc covers breedable species only, so NPCs and boss forms legitimately
    miss. Falling back to the game database beats echoing `Male_Soldier`.
    """
    info = breeding.pal_info("Male_Soldier")
    assert info["known"] is False
    assert info["name"] != "Male_Soldier"


def test_breeding_path_steps_carry_display_names():
    owned = ["sheepball", "eleccat"]
    child = breeding.predict_child("SheepBall", "ElecCat")
    result = breeding.breeding_paths(child, owned)
    assert result["reachable"] is True
    for step in result["steps"]:
        for role in ("parentA", "parentB", "child"):
            assert not step[role]["name"].islower() or " " in step[role]["name"]
            assert step[role]["name"] != step[role]["internalName"]
