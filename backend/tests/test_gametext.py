"""
The bundle's display strings, now that they come from the game rather than the
third-party archive.

Two of these test the **bundle on disk** and need no pak, so they run in CI: the
point of the swap is what shipped, not what the extractor is capable of. The
rest exercise the resolver and skip without the 40.5 GB client pak.

The failure this suite exists to catch is not "no name" — that is visible and
someone reports it. It is a name that looks like a name and is wrong: markup
rendered as a product title, a tier marker silently deleted so three items share
one label, or a placeholder dash presented as what a thing is called.
"""

from __future__ import annotations

import gzip
import json
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPTS = os.path.join(PROJECT_ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

BUNDLE = os.path.join(PROJECT_ROOT, "backend", "data", "gamedata.json.gz")
CLIENT_PAK = os.path.join(PROJECT_ROOT, "refs", "Pal-Windows.pak")

NAMED_SECTIONS = ("items", "pals", "technology", "structures",
                  "activeSkills", "passives")


@pytest.fixture(scope="module")
def bundle():
    if not os.path.exists(BUNDLE):
        pytest.skip("gamedata.json.gz not built")
    with gzip.open(BUNDLE, "rt", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def catalogue():
    if not os.path.exists(CLIENT_PAK):
        pytest.skip("client pak not present — integration test skipped")
    try:
        import gametext
    except ImportError:
        pytest.skip("gametext unavailable")
    return gametext.Catalogue("en")


# ── what shipped ──────────────────────────────────────────────────────────


def test_no_markup_survives_into_a_display_name(bundle):
    """
    **The resolver exists to prevent exactly this, and it failed once.**

    Two items shipped with `<characterName id=|FlowerPrince|/>'s Petal` as their
    literal name, because the overlay called the raw lookup instead of the one
    entry point that joins *and* resolves. A missing name is recoverable; a name
    that is markup is not, because it reads as data the game provided.
    """
    offenders = [
        (section, ident, value)
        for section in NAMED_SECTIONS
        for ident, entry in bundle[section].items()
        for value in (entry.get("name"), entry.get("description"))
        if value and ("<" in value or "id=|" in value)
    ]
    assert not offenders, offenders[:5]


def test_a_placeholder_is_never_presented_as_a_name(bundle):
    """
    The game ships `-` for six real entries and for `NAME_TEST_NPC`, which is
    what identifies it as a null marker rather than a name. Nothing in Palworld
    is called "-", so rendering one is worse than falling back to the id.
    """
    bad = {"-", "--", "---", "en text", "en_text", "unidentified pal", ""}
    offenders = [
        (section, ident, entry.get("name"))
        for section in NAMED_SECTIONS
        for ident, entry in bundle[section].items()
        if (entry.get("name") or "").strip().lower() in bad
    ]
    assert not offenders, offenders[:5]


def test_accessory_tiers_are_three_different_names(bundle):
    """
    The archive gave all three tiers one name, so the dashboard showed three
    different items as "Attack Pendant". This is the swap's most user-visible
    correction, and it is the case a base-first fallback rule would undo.
    """
    names = [bundle["items"][f"Accessory_AT_{i}"]["name"] for i in (1, 2, 3)]
    assert names == ["Attack Pendant", "Attack Pendant +1", "Attack Pendant +2"]
    assert len(set(names)) == 3


def test_boss_forms_are_named_for_their_species(bundle):
    """An alpha Lamball is still called Lamball — this module's own rule, which
    the game agrees with and the archive decorated with "(Boss)"."""
    assert bundle["pals"]["BOSS_Alpaca"]["name"] == "Melpaca"
    assert bundle["pals"]["Alpaca"]["name"] == "Melpaca"


def test_regions_and_dungeons_are_named_at_all(bundle):
    """
    `extract-progression.py` deliberately carried `REGION_Grass_1` unresolved
    rather than inventing "Grass 1". These sections are the game's own answer
    and did not exist in the bundle before.
    """
    assert bundle["regions"]["Grass_1"] == "Windswept Island"
    assert len(bundle["regions"]) > 100
    assert "Sealed Realm" in " ".join(bundle["dungeons"].values())


def test_paldeck_descriptions_shipped(bundle):
    described = [k for k, v in bundle["pals"].items() if v.get("description")]
    assert len(described) > 250
    assert "fluffy" in bundle["pals"]["Alpaca"]["description"]


def test_gamedata_accessors_resolve_the_new_sections(bundle):
    sys.path.insert(0, PROJECT_ROOT)
    from backend import gamedata

    assert gamedata.region_name("Grass_1") == "Windswept Island"
    # Callers hold either form of the id; both must work.
    assert gamedata.region_name("REGION_Grass_1") == "Windswept Island"
    # An unknown region falls back to humanize(), never to an empty string.
    assert gamedata.region_name("Nonexistent_9") == "Nonexistent 9"


# ── the resolver ──────────────────────────────────────────────────────────
#
# These need the client pak, so they are `integration`. The bundle tests above
# deliberately are not: **what shipped is the thing that matters**, and it must
# be checkable in CI without a 40.5 GB archive.


@pytest.mark.integration
def test_an_unresolvable_reference_is_refused_not_leaked(catalogue):
    """
    The whole contract. `resolve()` returns None so the caller falls back to
    what it already does for an unknown id.
    """
    assert catalogue.resolve("<itemName id=|NoSuchItemAnywhere|/>") is None
    assert catalogue.resolve("plain text") == "plain text"


@pytest.mark.integration
def test_styling_is_dropped_and_its_text_kept(catalogue):
    assert catalogue.resolve("<NumRed_12>500</> left") == "500 left"


@pytest.mark.integration
def test_a_glyph_reference_is_dropped_whole(catalogue):
    """`keyGuideIcon` and `img` name a controller button or a sprite. There is no
    text they stand for, so substituting the id would put `PadCircle` mid-sentence."""
    assert catalogue.resolve("Press <keyGuideIcon id=|PadCircle|/> now") == "Press  now"


@pytest.mark.integration
def test_technology_names_resolve_through_their_item_reference(catalogue):
    """A technology that unlocks an item is named after it, by reference. This is
    why 410 apparent disagreements collapsed to 3 once the resolver ran."""
    assert catalogue.name("technology", "AIcore") == "AI Core"


@pytest.mark.integration
def test_tier_fallback_is_exact_first(catalogue):
    # Has its own row: must not fall back and lose the "+1".
    assert catalogue.name("items", "Accessory_AT_2") == "Attack Pendant +1"
    # Has no row: must inherit the base rather than resolve to nothing.
    assert catalogue.name("items", "AncientArmor_2") == "Ancient Armor"
