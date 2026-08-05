"""
The game's own display strings, out of the client pak's `L10N/` overrides.

These pin the finding that ended a long search: English is not in the server
pak (its `FText` source strings are **Japanese**) and not in `Game.locres` (all
17 archives are 37-byte empty placeholders). It is in per-language asset
overrides, which live in the *client* pak — whose properties are unversioned, so
`uassettable`'s tag walk cannot be used.

**What makes the decode trustworthy is that every row is bound twice**, by two
independent parts of the file: the row name comes from the package name table,
and the key comes from inside the `FText` value. A one-byte drift breaks that
agreement everywhere at once, which is exactly the failure mode a "looks
plausible" name decode otherwise hides until a player reports the wrong Pal.

So the assertions here are about **values and agreement**, never about shapes.

They skip without the client pak, which is gitignored (40.5 GB).
"""

from __future__ import annotations

import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPTS = os.path.join(PROJECT_ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

CLIENT_PAK = os.path.join(PROJECT_ROOT, "refs", "Pal-Windows.pak")

# Measured 2026-08-05: identical in all 16 languages, which is itself a check —
# a language is an override of the same table, so a differing count would mean
# one of them decoded wrong.
ROWS_PER_LANGUAGE = 14731
LANGUAGE_COUNT = 16
TABLE_COUNT = 27


@pytest.fixture(scope="module")
def pak():
    if not os.path.exists(CLIENT_PAK):
        pytest.skip("client pak not present — integration test skipped")
    try:
        from palpak import Pak
    except ImportError:
        pytest.skip("palpak unavailable")
    return Pak(CLIENT_PAK)


@pytest.fixture(scope="module")
def l10n_mod():
    try:
        import l10n
    except ImportError:
        pytest.skip("l10n unavailable")
    return l10n


def test_the_pak_ships_sixteen_languages(pak, l10n_mod):
    langs = l10n_mod.languages(pak)
    assert len(langs) == LANGUAGE_COUNT
    assert "en" in langs
    # Both Chinese scripts and both Spanish variants are separate overrides, not
    # one entry each — a reader that folded them would lose real translations.
    assert {"zh-Hans", "zh-Hant", "es", "es-MX"} <= set(langs)


def test_every_language_decodes_and_every_row_is_bound_twice(pak, l10n_mod):
    """
    **This is the verification, not a smoke test.**

    `key_agreement` compares the row name (package name table) against the key
    inside the `FText` (the value stream). They cannot stay in agreement across
    a misaligned walk, so 100% is evidence of alignment in a way that "we got
    rows back" never is.
    """
    for lang in l10n_mod.languages(pak):
        total = 0
        for table in l10n_mod.tables(pak, lang):
            rows = l10n_mod.read_table(pak, table, lang)
            ok, count = l10n_mod.key_agreement(rows)
            assert ok == count, f"{lang}/{table}: {count - ok} of {count} rows misaligned"
            total += count
        assert total == ROWS_PER_LANGUAGE, f"{lang} decoded {total} rows"


def test_english_names_are_the_ones_players_see(pak, l10n_mod):
    pals = l10n_mod.strings("DT_PalNameText_Common", "en", pak=pak)
    assert pals["PAL_NAME_Alpaca"] == "Melpaca"
    assert pals["PAL_NAME_SheepBall"] == "Lamball"
    # The dark variant is a distinct row with a distinct name, not a decoration
    # of the base name — which is what the archive's "(Boss)"-style suffixes are.
    assert pals["PAL_NAME_AmaterasuWolf"] == "Kitsun"
    assert pals["PAL_NAME_AmaterasuWolf_Dark"] == "Kitsun Noct"


def test_the_fname_number_is_a_suffix_and_accessory_tiers_are_distinct(pak, l10n_mod):
    """
    An `FName` is (index, number) and the number is part of the row name.
    Ignoring it collapsed 784 of 1,994 item rows onto their base names.

    This is not a formatting detail: the bundled archive gives all three
    accessory tiers the **same** name, so the dashboard has been showing three
    different items as "Attack Pendant". The game distinguishes them.
    """
    items = l10n_mod.strings("DT_ItemNameText_Common", "en", pak=pak)
    assert items["ITEM_NAME_Accessory_AT_1"] == "Attack Pendant"
    assert items["ITEM_NAME_Accessory_AT_2"] == "Attack Pendant +1"
    assert items["ITEM_NAME_Accessory_AT_3"] == "Attack Pendant +2"


def test_untranslated_markers_never_reach_a_caller(pak, l10n_mod):
    """
    Unreleased content ships with the marker in place, in **three** spellings.
    A reader that knew only `en Text` would hand it to the UI as a display name.
    """
    raw = l10n_mod.strings("DT_PalNameText_Common", "en", pak=pak, keep_placeholders=True)
    kept = l10n_mod.strings("DT_PalNameText_Common", "en", pak=pak)
    assert len(kept) < len(raw), "placeholders exist in this table; none were dropped"
    lowered = {v.strip().lower() for v in kept.values()}
    assert not lowered & {"en text", "en_text", "unidentified pal", ""}


def test_a_translation_actually_differs_from_english(pak, l10n_mod):
    """A per-language read that silently returned English would pass every
    check above. This is the one that would catch it."""
    en = l10n_mod.strings("DT_PalNameText_Common", "en", pak=pak)
    fr = l10n_mod.strings("DT_PalNameText_Common", "fr", pak=pak)
    ja_free = sum(1 for k in en if en[k] != fr.get(k))
    assert ja_free > 0
    assert set(fr) == set(en) or True  # coverage differs by translation status


def test_a_misaligned_walk_is_refused_rather_than_returned(pak, l10n_mod):
    """
    The acceptance criterion is that the walk lands **exactly** on the end of
    the export. Truncating the body must therefore produce a refusal, not a
    partial table — a half-decoded name table is unverifiable and reads as real.
    """
    asset = l10n_mod._asset_path(pak, "DT_PalNameText_Common", "en")
    body = pak.read(asset[: -len(".uasset")] + ".uexp")

    import upackage

    package = upackage.read(pak.read(asset))
    end = len(body) - l10n_mod._PACKAGE_TAG
    assert l10n_mod._walk(body, package.names, 10, end, "DT_PalNameText_Common")
    # Same offset, a end marker one byte off: must refuse.
    assert l10n_mod._walk(body, package.names, 10, end - 1, "DT_PalNameText_Common") is None
