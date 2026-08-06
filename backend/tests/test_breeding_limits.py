"""
Why a Pal cannot be bred, out of the game's own columns.

**The test that matters most is `test_a_variant_with_a_named_pairing_is_not
_unbreedable`.** This project shipped, for one day, a documented claim that an
element variant is not a breeding outcome at all — and `DT_PalCombiUnique` names
81 of them as children. A UI built on the wrong version tells a player that
Mossanda Lux is unobtainable by breeding when the game ships the pairing.

Everything here reads bundled data, so it runs on a clean checkout with no pak
and no world.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import breeding  # noqa: E402
import gamedata  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh():
    gamedata._reset_cache()
    breeding._named_pairings.cache_clear()
    yield
    gamedata._reset_cache()
    breeding._named_pairings.cache_clear()


# ─── The columns arrived in the bundle ───────────────────


def test_the_two_breeding_columns_are_bundled():
    """
    `zukanSuffix` and `ignoreCombi` come from `DT_PalMonsterParameter` via
    `build-gamedata.py`. Pinned against the shipped blob rather than the
    extractor, so a regeneration that lost them fails here and not in a UI.
    """
    pals = gamedata.load()["pals"]
    variants = [s for s, e in pals.items() if e.get("zukanSuffix") == "B"]
    no_breeding = [s for s, e in pals.items() if e.get("ignoreCombi")]
    assert len(variants) == 90
    assert len(no_breeding) == 226


def test_absent_means_ordinary_rather_than_unread():
    """
    Both fields are written only when they say something, so `Alpaca` carries
    neither. A caller must be able to treat absence as the common case.
    """
    alpaca = gamedata.load()["pals"]["Alpaca"]
    assert "zukanSuffix" not in alpaca
    assert "ignoreCombi" not in alpaca


# ─── The classification ──────────────────────────────────


def test_a_variant_with_a_named_pairing_is_not_unbreedable():
    """
    THE REGRESSION GUARD. Mossanda Lux is Mossanda x Grizzbolt and the game
    says so in `DT_PalCombiUnique`. Any answer here that reads as "cannot be
    bred" is the retracted claim coming back.
    """
    info = breeding.obtainability("GrassPanda_Electric")
    assert info["kind"] == "named_pairing"
    assert info["variant"] is True
    pairs = {(p["aName"], p["bName"]) for p in info["pairings"]}
    assert ("Mossanda", "Grizzbolt") in pairs
    assert "cannot" not in (info.get("note") or "")


def test_every_paldeck_variant_has_a_route_that_is_not_itself():
    """
    A variant paired with itself breeds true, which is real and useless as an
    acquisition answer. If a variant's *only* listed pairing were the
    breeds-true one, the UI would be telling a player to use a Pal they are
    trying to obtain. Measured: none are.
    """
    listed = breeding.unbreedable()["namedPairingOnly"]
    assert listed
    for row in listed:
        assert any(not p.get("breedsTrue") for p in row["pairings"]), row["name"]


def test_the_three_contested_variants_are_reported_as_contested():
    """
    Kelpsea Ignis, Shroomer Noct and Wumpo Botan are Paldeck-listed, breed
    according to `IgnoreCombi`, and appear in no unique combo. palcalc's table
    offers them anyway. That disagreement is reported, not resolved — see
    `scripts/verify-breeding.py`.
    """
    rows = breeding.unbreedable()["unverified"]
    assert {r["name"] for r in rows} == {
        "Kelpsea Ignis", "Shroomer Noct", "Wumpo Botan",
    }
    for row in rows:
        assert row["mutatedEgg"]["quote"] == breeding.MUTATED_EGG_QUOTE


def test_ignore_combi_species_are_never():
    assert breeding.obtainability("NightLady")["kind"] == "never"
    # Frostallion is the canonical "you catch this, you do not breed it".
    assert breeding.obtainability("IceHorse")["kind"] == "never"


def test_an_ordinary_pal_is_standard_with_no_note():
    info = breeding.obtainability("Alpaca")
    assert info["kind"] == "standard"
    assert info.get("note") is None


def test_an_unknown_species_is_not_an_error():
    """A modded or unreleased id is not evidence of anything."""
    info = breeding.obtainability("__not_a_species__")
    assert info["kind"] == "standard"
    assert info["known"] is False


def test_alpha_forms_resolve_to_their_base_species_answer():
    assert (
        breeding.obtainability("BOSS_GrassPanda_Electric")["kind"]
        == breeding.obtainability("GrassPanda_Electric")["kind"]
    )


# ─── The Paldeck-entry grouping ──────────────────────────


def test_an_encounter_form_does_not_make_its_paldeck_entry_unbreedable():
    """
    **This is the bug the grouping exists for.** `GrassPanda_Electric_Tower` is
    the tower-boss form of Mossanda Lux: same Paldeck number, same suffix, same
    display name, and `IgnoreCombi` true because that *form* is not a breeding
    outcome. Rows are Paldeck entries and keep the most permissive answer, so
    Mossanda Lux must never appear under "cannot be bred".

    Nine of the eleven Paldeck collisions are this shape.
    """
    limits = breeding.unbreedable()
    never = {r["name"] for r in limits["never"]}
    for name in ("Mossanda Lux", "Relaxaurus Lux", "Incineram Noct"):
        assert name not in never
    assert name in {r["name"] for r in limits["namedPairingOnly"]}


def test_one_row_per_paldeck_entry():
    limits = breeding.unbreedable()
    rows = limits["never"] + limits["unverified"] + limits["namedPairingOnly"]
    keys = [(r["paldeck"], r["suffix"]) for r in rows]
    assert len(keys) == len(set(keys))
    assert limits["paldeckEntries"] == 288


def test_the_never_list_is_the_bosses_and_legendaries():
    """
    Sanity on the shape rather than the exact membership: the list should be
    tower bosses, raid bosses and legendaries. If it ever fills up with
    ordinary Pals, a filter has inverted.
    """
    never = breeding.unbreedable()["never"]
    assert len(never) == 28
    names = {r["name"] for r in never}
    assert {"Frostallion", "Jetragon", "Bellanoir", "Grizzbolt"} <= names
    assert "Lamball" not in names
    # Every one is Paldeck-listed — an unreleased form would be misleading here,
    # since nobody can obtain it by any means.
    assert all(r["paldeck"] > 0 for r in never)


def test_the_alpha_chance_comes_from_the_game_settings():
    assert breeding.unbreedable()["alphaChance"] == 0.05


# ─── Report facts, not mechanics ─────────────────────────


def test_nothing_here_claims_a_mutation_mechanic():
    """
    `basesupply.py`'s rule, pinned the same way. The game says mutated eggs
    exist and are rare; **no file says what produces one or at what rate**, so
    no string here may imply a method. The quotes are quotes and are labelled.

    When somebody does find a source for the mechanic, this test is the thing
    to change — deliberately.
    """
    limits = breeding.unbreedable()
    prose = " ".join(
        str(r.get("note") or "") + " " + str((r.get("mutatedEgg") or {}).get("note") or "")
        for r in limits["never"] + limits["unverified"] + limits["namedPairingOnly"]
    ).lower()
    for claim in ("chance of", "% of the time", "you can get", "farm a", "guaranteed"):
        assert claim not in prose, claim
    # The absence is stated rather than left for the reader to notice.
    egg = limits["unverified"][0]["mutatedEgg"]
    assert "no game file says" in egg["note"].lower()


def test_the_quotes_are_the_games_own_words():
    """
    Both strings are verbatim from the bundle, so a reworded game update makes
    this fail rather than leaving the dashboard quoting something Pocketpair no
    longer says.

    Whitespace is normalised on both sides and only whitespace: the bundle
    keeps the game's own line break mid-sentence (`...healthy egg.\\nMutations
    are more likely...`), which a UI must not render raw. Every other character
    has to match.
    """
    def flat(text: str) -> str:
        return " ".join((text or "").split())

    egg = gamedata.describe_item("PalEgg_MutationPal_01") or {}
    assert breeding.MUTATED_EGG_QUOTE in flat(egg.get("description"))
    cake = gamedata.describe_item("Cake04") or {}
    assert breeding.MUTATION_CAKE_QUOTE in flat(cake.get("description"))
