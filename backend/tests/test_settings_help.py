"""
119 settings, and the dashboard explained none of them.

`HIGHLIGHT_GROUPS` curates a subset to the top of the page; it never said what
any of them did. So an operator read `PalStomachDecreaceRate` — the game's own
misspelling — and had to go and look it up.

**The temptation was to write 119 sentences.** That is the failure this test file
guards against, because a sentence I wrote about a mechanic nobody measured looks
exactly like one Pocketpair published, and would be trusted the same way. The
descriptions come from Pocketpair's own documentation, the labels from the game's
own world-settings screen, and the handful of lines this project wrote are tagged
so they can never be mistaken for either.

These tests run against the **shipped bundle** rather than the extractor, for the
same reason `test_gametext.py` does: a test of the generator passes happily beside
a bundle built before the generator was fixed.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import settings_ini  # noqa: E402
import settingshelp  # noqa: E402

#: The three sources, and the whole point of tagging them.
_SOURCES = {"official", "game", "dashboard"}


@pytest.fixture(autouse=True)
def _fresh():
    settingshelp.reload()


def test_the_bundle_is_present_and_substantial():
    """A silently empty bundle would pass every assertion phrased as 'no bad rows'."""
    coverage = settingshelp.coverage()
    assert coverage["iniKeys"] == 119, "the 1.0 default set changed — re-run the extractor"
    assert coverage["documented"] >= 90
    assert coverage["labelled"] >= 45


def test_every_text_field_says_where_it_came_from():
    """
    **The load-bearing assertion.** Three sources with three different kinds of
    authority: Pocketpair's documentation, the game's own UI strings, and this
    project's measurements. Presenting them identically would launder the third
    into the first, which is the exact move `elements.py` exists to prevent.
    """
    settings = settingshelp.load()["settings"]
    for key, entry in settings.items():
        for field in ("description", "label", "note"):
            if field in entry:
                tag = entry.get(f"{field}Source")
                assert tag in _SOURCES, f"{key}.{field} has no usable source tag: {tag!r}"


def test_only_the_notes_are_ours():
    """
    Nothing this project wrote may be tagged `official`. A `note` is the only
    field allowed a `dashboard` source, and every one of them must be a real
    sentence rather than a placeholder somebody meant to fill in.
    """
    for key, entry in settingshelp.load()["settings"].items():
        assert entry.get("descriptionSource") != "dashboard", key
        assert entry.get("labelSource") != "dashboard", key
        if "note" in entry:
            assert entry["noteSource"] == "dashboard", key
            assert len(entry["note"]) > 40, f"{key}'s note is a stub"


def test_a_key_with_no_help_is_ABSENT_not_blank():
    """
    19 keys have no official description and no game label. They must come back
    with nothing at all — a generated sentence would be indistinguishable from
    the 93 real ones, and an empty-string description is the kind of value a UI
    truthiness check renders as a heading with no body.
    """
    coverage = settingshelp.coverage()
    assert coverage["undocumented"], "nothing is undocumented, which is implausible"
    for key in coverage["undocumented"]:
        assert settingshelp.describe(key) == {}


def test_every_documented_key_is_a_real_setting():
    """
    A help entry for a key the file does not have is help nobody will ever see,
    and more importantly it is evidence the join drifted — Pocketpair's docs list
    `AllowConnectPlatform`, which 1.0 does not ship.
    """
    real = set(settings_ini.read_ini(_reference_ini())["options"])
    stray = sorted(set(settingshelp.load()["settings"]) - real)
    assert stray == [], f"help exists for keys this version has no setting for: {stray}"


def test_the_enum_values_are_named_not_just_the_key():
    """
    **Worth more than the key descriptions.** `DeathPenalty=EquipmentAndItemAndRandomPal`
    is opaque to anyone who has not memorised it; the game calls it "Drop all
    items and one random Pal on team" on its own settings screen.
    """
    death = settingshelp.describe("DeathPenalty").get("values") or {}
    assert death.get("All") == "Drop all items and all Pals on team"
    assert death.get("Item") == "Drop all items except equipment"
    assert "EquipmentAndItemAndRandomPal" in death


def test_randomizer_values_use_THE_INI_SPELLING():
    """
    The game's UI rows are `RANDOMIZER_MODE_NO` / `_REGION` / `_ALL`; the INI
    stores `None` / `Region` / `All`. Title-casing the suffix gets two of three
    and invents `No` for the one that matters, so the map is written out — and
    this is what stops it silently regressing to a string transform.
    """
    values = settingshelp.describe("RandomizerType").get("values") or {}
    assert set(values) == {"None", "Region", "All"}


def test_the_sentence_joins_survived_the_html():
    """
    Stripping tags without substituting for `<br>` produced "(max 50).Increasing
    this value" — text that reads as a typo in Pocketpair's documentation when it
    is one in our parser. Cheap to reintroduce, invisible in a diff of 93 rows.
    """
    for key, entry in settingshelp.load()["settings"].items():
        description = entry.get("description", "")
        assert ").I" not in description, f"{key}: lost a sentence break"
        # A lowercase letter running straight into a capitalised word is the
        # signature of the same bug on a row with no full stop.
        assert "PenaltyNone" not in description, f"{key}: lost a sentence break"


def test_annotate_leaves_unknown_keys_untouched():
    options = {"ExpRate": {"value": 1.0}, "NotASetting": {"value": 0}}
    settingshelp.annotate(options)
    assert "help" in options["ExpRate"]
    assert "help" not in options["NotASetting"], (
        "an empty help object is the value a UI truthiness check gets wrong"
    )


def test_a_missing_bundle_costs_the_tooltips_and_nothing_else(monkeypatch, tmp_path):
    """
    Missing help must never break the Settings tab. Reading and writing the
    server's config is the job; explaining it is the garnish.
    """
    monkeypatch.setattr(settingshelp, "HELP_PATH", str(tmp_path / "gone.json.gz"))
    settingshelp.reload()
    assert settingshelp.describe("ExpRate") == {}
    assert settingshelp.coverage()["undocumented"] == []
    options = {"ExpRate": {"value": 1.0}}
    assert settingshelp.annotate(options) is options


def test_the_traps_this_project_measured_are_actually_in_there():
    """
    The two the task named, because they are the ones a reader of a stale guide
    gets wrong: the egg key's real spelling, and that the base caps in this file
    beat any table the dashboard bundles.
    """
    egg = settingshelp.describe("PalEggDefaultHatchingTime")
    assert "EggDefaultHatchingTime" in egg["note"]
    for key in ("BaseCampWorkerMaxNum", "BaseCampMaxNumInGuild"):
        assert settingshelp.describe(key).get("note"), key


def _reference_ini() -> str:
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "refs", "palworld", "DefaultPalWorldSettings.ini",
    )
    if not os.path.exists(path):
        pytest.skip("refs/ not present — the reference INI is unavailable")
    return path
