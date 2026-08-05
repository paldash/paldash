"""
The element chart — the one hand-entered piece of game data in this project.

It is in neither source (all 480 server-pak DataTables were listed and read; the
PST archive's 78 "element" entries are all icons), so unlike everything else here
it cannot be re-derived and cannot be regression-tested against an extraction.
What CAN be tested is that it is internally coherent and that it speaks the same
vocabulary as the data it will be used with — which is what these do.
"""

from __future__ import annotations

import os
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

import elements     # noqa: E402
import gamedata     # noqa: E402


def test_there_are_exactly_nine_elements():
    assert len(elements.ELEMENTS) == 9
    assert len(set(elements.ELEMENTS)) == 9


def test_the_relation_is_exactly_reciprocal():
    """
    THE CHECK THAT MAKES A TRANSCRIBED CHART TRUSTWORTHY. Every "strong against"
    must have its matching "weak to" and vice versa, with no orphans in either
    direction. A chart copied with an error almost certainly breaks this.
    """
    strong = {
        (a, d) for a, targets in elements.STRONG_AGAINST.items() for d in targets
    }
    weak = {
        (a, d) for d, attackers in elements.WEAK_TO.items() for a in attackers
    }
    assert strong == weak
    assert len(strong) == 9


def test_the_element_list_comes_from_the_game_not_from_this_file():
    """
    THE VOCABULARY IS DERIVED. `ELEMENTS` is read off the bundled Pal data, so
    the game decides what exists and the chart only claims to know how they
    interact. The hardcoded tuple is a fallback for a missing bundle.
    """
    in_data = {
        e
        for pal in (gamedata.load().get("pals") or {}).values()
        for e in (pal.get("elements") or [])
    }
    assert set(elements.game_elements()) == in_data
    assert set(elements.ELEMENTS) == in_data


def test_the_chart_covers_every_element_the_game_ships():
    """
    Empty is the healthy state. A content update adding a tenth element would
    otherwise make every matchup involving it read as a confident "neutral"
    rather than as a visible gap — and this chart is the one thing here that
    cannot be regenerated, so it is the one thing that can silently rot.
    """
    assert elements.unknown_to_chart() == ()
    assert elements.chart_is_current()


def test_a_new_game_element_is_reported_rather_than_answered(monkeypatch):
    monkeypatch.setattr(
        elements, "game_elements", lambda: elements.ELEMENTS + ("Plasma",)
    )
    assert elements.unknown_to_chart() == ("Plasma",)
    assert not elements.chart_is_current()


def test_neutral_is_strong_against_nothing():
    """
    The game's design, not a hole in the transcription: Neutral Pals trade
    combat matchups for base work. It is still weak to Dark.
    """
    assert elements.STRONG_AGAINST["Neutral"] == ()
    assert elements.WEAK_TO["Neutral"] == ("Dark",)


def test_every_other_element_has_exactly_one_weakness():
    for element in elements.ELEMENTS:
        assert len(elements.WEAK_TO[element]) == 1, element


def test_fire_is_the_only_element_strong_against_two():
    doubles = [e for e, t in elements.STRONG_AGAINST.items() if len(t) == 2]
    assert doubles == ["Fire"]
    assert set(elements.STRONG_AGAINST["Fire"]) == {"Grass", "Ice"}


# ─── Vocabulary ──────────────────────────────────────────────────


def test_ground_maps_to_earth():
    """The one name the source spells differently from the game's own data."""
    assert elements.canonical("Ground") == "Earth"


def test_the_passive_tables_third_vocabulary_also_resolves():
    """
    `DT_PassiveSkill_Main` says `ElementBoost_Leaf`, `_Electricity`, `_Normal`.
    A caller holding one of those must not silently get "no effect".
    """
    assert elements.canonical("Leaf") == "Grass"
    assert elements.canonical("Electricity") == "Electric"
    assert elements.canonical("Normal") == "Neutral"


def test_lookup_is_case_insensitive_like_everything_else_here():
    assert elements.canonical("fIrE") == "Fire"


def test_an_unknown_element_is_none_rather_than_a_guess():
    assert elements.canonical("Plastic") is None


# ─── Matchups ────────────────────────────────────────────────────


def test_the_basic_relations():
    assert elements.effectiveness("Water", "Fire") == "strong"
    assert elements.effectiveness("Fire", "Water") == "weak"
    assert elements.effectiveness("Fire", "Dragon") == "neutral"


def test_a_modded_element_costs_the_matchup_not_the_answer():
    assert elements.effectiveness("Plastic", "Fire") == "neutral"
    assert elements.effectiveness("Fire", "Plastic") == "neutral"


def test_a_dual_element_pal_uses_its_best_option():
    """
    Strong beats weak when both apply: the player picks which move to use, so
    having a strong option available is what decides the encounter.
    """
    assert elements.matchup(["Water", "Grass"], ["Fire"]) == "strong"
    assert elements.matchup(["Grass"], ["Fire"]) == "weak"
    assert elements.matchup(["Dragon"], ["Water"]) == "neutral"


def test_an_empty_element_list_is_neutral_rather_than_an_error():
    assert elements.matchup([], ["Fire"]) == "neutral"
    assert elements.matchup(["Fire"], []) == "neutral"


def test_no_multiplier_is_exposed_anywhere():
    """
    The source presents its damage values as an IMAGE, so the numbers were never
    available as text. Shipping a relation and letting a caller invent a
    coefficient is the failure this guards against.
    """
    assert not hasattr(elements, "MULTIPLIERS")
    assert elements.effectiveness("Water", "Fire") == "strong"  # not 1.5
