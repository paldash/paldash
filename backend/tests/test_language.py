"""
Serving the game's own display names in fifteen more languages.

`l10n.py` and `gametext.py` decoded all sixteen months ago and only English was
ever bundled. These test the serving half: per-language files, loaded on demand,
resolved from localisation row names into the ids the API speaks.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gamedata  # noqa: E402


def test_fifteen_languages_ship_plus_english():
    langs = gamedata.languages()
    assert langs[0] == "en"
    assert len(langs) == 16
    for expected in ("de", "fr", "ja" if "ja" in langs else "ko", "zh-Hans"):
        assert expected in langs


def test_english_is_deliberately_not_a_pack():
    """
    It is already inside `gamedata.json.gz`. A second copy would be a second
    source of truth for the names every other bundle is keyed against.
    """
    assert gamedata.language("en") == {}
    assert gamedata.language_names("en") == {}


def test_a_language_resolves_row_names_into_ids():
    """
    The bundles are keyed on localisation ROW names (`pal_name_sheepball`).
    Callers speak ids, so the prefix is stripped once on the server rather than
    left as a convention every client reimplements.
    """
    names = gamedata.language_names("de")
    assert names["pals"]["alpaca"] == "Melpaca"
    assert names["pals"]["sheepball"] == "Lamball"


def test_item_tiers_survive_the_prefix_strip():
    """
    `item_name_accessory_normalresist_1` is the item `Accessory_NormalResist_1`,
    so the tier is part of the id and comes through. The game distinguishes
    these three where the third-party archive calls all of them "Attack
    Pendant" — flattening them here would undo that.
    """
    items = gamedata.language_names("de")["items"]
    tiers = [items.get(f"accessory_normalresist_{n}") for n in (1, 2, 3)]
    assert all(tiers), tiers
    assert len(set(tiers)) == 3


def test_skills_are_absent_rather_than_guessed():
    """
    Their rows are `passive_craftspeed_up1`, which is not `<prefix><id>`.
    Inventing a mapping for them would be exactly the guess this avoids
    everywhere else, so the section is simply not offered.
    """
    assert "skills" not in gamedata.language_names("de")


def test_an_unknown_or_traversing_code_returns_empty_rather_than_reading_a_file():
    """
    The code comes from a URL. `../` must not escape the language directory —
    and the fallback is English, which is a working dashboard rather than an
    error.
    """
    assert gamedata.language("xx") == {}
    assert gamedata.language("../../../etc/passwd") == {}
    assert gamedata.language("") == {}
    assert gamedata.language_names("../gamedata") == {}


def test_each_language_is_cached_separately():
    """
    Not a single slot. A dict keeps one operator's language from evicting
    another's on a mixed-language team, which a single cached pack would do on
    every alternating request.
    """
    gamedata.language("de")
    gamedata.language("fr")
    assert "de" in gamedata._languages and "fr" in gamedata._languages
    assert gamedata._languages["de"] is not gamedata._languages["fr"]
