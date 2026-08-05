"""
`BP_PalGameSetting` — Pocketpair's own tuning constants.

WHY THIS BUNDLE IS DIFFERENT FROM EVERY OTHER ONE HERE. It is decoded from a
**Blueprint's class-default object**, not from a DataTable. Until it existed, the
project's position was that only DataTables come out of the pak and everything
else is unversioned-property territory. That was true of the *client* pak and
wrong about the server pak's Blueprints, and the cost of believing it was that
several numbers this project had guessed at were sitting in a file all along.

THE DECODE VERIFIES ITSELF, which is what makes it trustworthy without a second
source: `CharacterMaxLevel` comes out 80 and `CharacterMaxRank` 5 — two constants
already held here from sources that explicitly could not be checked against the
install. A tagged walk that has drifted does not land two independently-known
values in the right places.
"""

from __future__ import annotations

import os
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

import editschema   # noqa: E402
import elements     # noqa: E402
import gamedata     # noqa: E402


def test_the_bundle_reproduces_the_level_cap_this_project_had_guessed():
    """
    `editschema.MAX_LEVEL` is documented as "a community-sourced figure, not one
    read from the game files" that "cannot be verified against the install".
    It can now, and it is right.
    """
    assert gamedata.game_setting("CharacterMaxLevel") == 80
    assert editschema.MAX_LEVEL == 80


def test_the_bundle_reproduces_the_condenser_rank_cap():
    assert gamedata.game_setting("CharacterMaxRank") == 5
    assert editschema.MAX_RANK == 5


def test_a_missing_setting_returns_the_callers_default():
    """
    Callers keep their own documented fallback, so a missing bundle degrades to
    the behaviour that existed before it rather than to a zero.
    """
    assert gamedata.game_setting("NoSuchSetting", "fallback") == "fallback"
    assert gamedata.game_setting("NoSuchSetting") is None


def test_lookup_is_case_sensitive_unlike_the_id_lookups():
    """
    These are UPROPERTY names with one fixed spelling, not ids that three files
    disagree about — so the case-insensitivity elsewhere would be false comfort.
    """
    assert gamedata.game_setting("charactermaxlevel") is None


# ─── The element multiplier ──────────────────────────────────────


def test_the_element_multiplier_comes_from_the_game_not_the_website():
    """
    THE WIDELY CITED FIGURE IS 2x DEALT AND 1/2 TAKEN. The game's own settings
    object says 1.2, and carries no halving or resist constant at all.
    """
    assert gamedata.game_setting("DamageElementMatchRate") == 1.2
    assert elements.match_rate() == 1.2


def test_there_is_no_second_element_damage_constant():
    """
    The basis for saying the "1/2 damage taken" half is not in the files: it is
    not that it was not looked for, it is that the object has exactly one
    element-damage key.
    """
    gamedata.game_setting("CharacterMaxLevel")  # force the bundle to load
    keys = [
        k for k in (gamedata._game_settings or {})
        if "element" in k.lower() and "damage" in k.lower()
    ]
    assert keys == ["DamageElementMatchRate"]


def test_the_multiplier_falls_back_to_no_effect_not_to_a_hardcoded_copy(monkeypatch):
    """
    A second hardcoded 1.2 is how the two drift apart. A matchup that quietly
    does nothing is a better failure than one asserting a number nothing checked.
    """
    monkeypatch.setattr(gamedata, "_game_settings", {})
    assert elements.match_rate() == 1.0


# ─── The welfare threshold ───────────────────────────────────────


def test_the_low_sanity_threshold_is_the_games_own_number():
    """
    `main.LOW_SANITY` was picked as a judgement call at 50. The game ships
    `FriendshipPoint_AutoIncrementRequireSanity = 50` — the sanity a Pal must
    hold to keep gaining trust — so the number is the game's and now comes from
    the file rather than merely agreeing with it.
    """
    assert gamedata.game_setting("FriendshipPoint_AutoIncrementRequireSanity") == 50

    import main  # noqa: PLC0415 - importing at module scope pulls in the app
    assert main.LOW_SANITY == 50.0
