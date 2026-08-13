"""
Whether a Pal flies, swims or walks.

AGENTS.md recorded this as unavailable across five checked avenues. The sixth
was never tried: the search looked for `BP_Pal_*` and the game names its species
blueprints `BP_<Species>`, in the server pak, where they are decodable.

Asserted against the **shipped bundle** rather than the extractor, because a
test of the generator passes happily beside a stale `.json.gz`.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import buildplanner  # noqa: E402
import gamedata  # noqa: E402


def test_the_bundle_ships_only_the_non_ground_species():
    """
    52 of 753. Storing 701 `GroundOnly` entries would triple the file to restate
    an inference.
    """
    data = gamedata.movement_modes()
    assert data, "movement_modes.json.gz is missing from the shipped bundle"
    assert len(data["species"]) == 52
    assert data["default"] == "GroundOnly"


def test_the_control_is_two_swimmers_whose_land_variants_are_reset():
    """
    THE VERIFICATION, and the reason this is a field rather than a correlation.

    A property that merely happened to track something would not have the land
    variants of two swimming Pals individually overridden back to ground.
    """
    assert gamedata.movement_mode("Serpent") == "Swim"          # Surfent
    assert gamedata.movement_mode("Serpent_Ground") == "GroundOnly"   # Surfent Terra
    assert gamedata.movement_mode("Umihebi") == "Swim"          # Jormuntide
    assert gamedata.movement_mode("Umihebi_Fire") == "GroundOnly"     # Jormuntide Ignis


def test_known_flyers_fly_and_known_ground_legendaries_do_not():
    """
    Necromus and Paladius are the ones worth pinning: they are legendary mounts,
    they are very fast, and they do NOT fly. A rule that swept them in on
    plausibility would be wrong in exactly the way this project keeps recording.
    """
    for species in ("JetDragon", "HawkBird", "Eagle", "IceHorse", "BlackGriffon"):
        assert gamedata.is_airborne(species), species
    for species in ("BlackCentaur", "SaintCentaur", "Alpaca", "CaptainPenguin"):
        assert not gamedata.is_airborne(species), species


def test_a_boss_form_inherits_rather_than_defaulting_to_ground():
    """
    `BOSS_HawkBird` has no blueprint of its own. Reading the override table raw
    calls every alpha flyer a ground Pal — `pal_exact`'s lesson, one asset type
    over, and the single most likely way to get this wrong.
    """
    for base in ("JetDragon", "HawkBird", "IceHorse", "Umihebi"):
        assert gamedata.movement_mode(f"BOSS_{base}") == gamedata.movement_mode(base)
    assert gamedata.is_airborne("BOSS_JetDragon")


def test_the_inferred_default_is_labelled_as_inferred():
    """
    THE REFUSAL. Nothing in any file states the native default; it rests on the
    31 overrides being exactly the non-walking Pals. That is good evidence and
    it is not a read value, so it must not be presented as one.
    """
    assert gamedata.movement_modes()["defaultIsInferred"] is True
    assert "inferred" in gamedata.movement_modes()["defaultNote"].lower()


def test_an_absent_bundle_gives_none_rather_than_ground():
    """
    "We could not ask" and "it walks" must not share a representation — the
    `.catch(() => [])` lesson. A missing bundle makes every Pal look terrestrial
    otherwise, which is a confident wrong answer about 52 species.
    """
    saved = gamedata._movement_modes
    try:
        gamedata._movement_modes = {}
        assert gamedata.movement_mode("JetDragon") is None
        assert gamedata.is_airborne("JetDragon") is False
    finally:
        gamedata._movement_modes = saved


def test_the_planner_answers_fastest_rideable_flyer():
    """
    The question AGENTS.md called unanswerable from files. `mountMode` was a
    hardcoded `None` in `buildplanner.rank` with a comment saying so.
    """
    rows = buildplanner.rank("rideSprint", rideable_only=True, limit=300)["rows"]
    airborne = [r for r in rows if r["mountMode"] in ("Fly", "FlyAndLanding")]
    assert airborne, "no rideable flyer — mountMode is not reaching the rows"
    assert airborne[0]["name"] == "Jetragon"
    assert airborne[0]["value"] == 3300

    # Every row carries whether its mode was read or inherited, because a row is
    # what gets rendered and the two must not look equally authoritative.
    assert all("mountModeInferred" in r for r in rows)
    jetragon = next(r for r in rows if r["name"] == "Jetragon")
    assert jetragon["mountModeInferred"] is False
