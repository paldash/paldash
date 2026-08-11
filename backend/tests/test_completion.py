"""
The Paldeck completion tracker, and the denominator that makes it usable.

Against the shipped bundles, with the paldeck entry list built the way the route
builds it — a fixture would pin the join and let the catalogue drift.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import completion  # noqa: E402
import gamedata  # noqa: E402
import viewcache  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_caches():
    gamedata._reset_cache()
    viewcache.clear()
    yield
    gamedata._reset_cache()
    viewcache.clear()


@pytest.fixture
def entries():
    import main
    return main._paldeck_entries()


def test_the_denominator_is_paldeck_entries_not_species_forms(entries):
    """
    204, not 753. Counting forms puts 100% permanently out of reach, which is
    the surest way to make a completion tracker useless.
    """
    report = completion.tracker(entries, unlocked=[])
    assert report["denominator"] == "paldeckEntries"
    assert report["total"] == len(entries)
    assert report["total"] < 400, "forms leaked into the entry count"


def test_an_entry_is_caught_when_any_of_its_forms_is(entries):
    """
    The save writes FORMS (`HadesBird_Electric`); the Paldeck gives them one
    number. Requiring every form would leave Helzephyr permanently incomplete
    for somebody who owns one.
    """
    helzephyr = next(e for e in entries if e["name"] == "Helzephyr")
    assert len(helzephyr.get("speciesIds") or []) > 1
    variant = [f for f in helzephyr["speciesIds"] if f != helzephyr["id"]][0]

    report = completion.tracker(entries, unlocked=[variant])
    row = next(r for r in report["entries"] if r["name"] == "Helzephyr")
    assert row["caught"] is True
    assert report["caught"] == 1


def test_a_caught_entry_carries_no_route(entries):
    """Telling somebody where to find a Pal they own is noise."""
    report = completion.tracker(entries, unlocked=["Alpaca"])
    caught = [r for r in report["entries"] if r["caught"]]
    assert caught and all("route" not in r for r in caught)


def test_every_missing_entry_says_which_kind_of_missing(entries):
    """
    Catch, breed, or neither — and "neither" is stated rather than smoothed
    over. A raid boss has no world spawner and no pairing, and "go and catch
    it" about Bellanoir would be wrong in a way that wastes an evening.
    """
    report = completion.tracker(entries, unlocked=[])
    routes = [r["route"] for r in report["entries"]]
    assert all(routes), "an entry with no route at all"
    assert all(set(r) <= {"catch", "breed", "unknown"} for r in routes)
    assert any(r.get("catch") for r in routes)
    assert any(r.get("breed") for r in routes)

    # **`unknown` never fires on the shipped catalogue, and that is the finding.**
    # `breeding.obtainability` answers for every entry, so a Pal no pairing
    # produces comes back as `breed: never` — which is a real answer and a more
    # useful one than "unknown". The branch stays as a guard against a future
    # entry the breeding tables do not know; it is not a path.
    assert not any(r.get("unknown") for r in routes)

    # Frostallion: no pairing produces one, and two of them breed true — worth
    # telling somebody who owns one and useless to somebody who does not.
    frostallion = next(r for r in report["entries"] if r["name"] == "Frostallion")
    assert frostallion["route"]["breed"] == {"kind": "never", "breedsTrue": True}

    # And Bellanoir is NOT that case, which is why the two are distinguished:
    # the game names her pairing outright (Bellanoir + Bellanoir Libero), so
    # filing her under "cannot be bred" would be a claim about a pairing players
    # use. Same retraction AGENTS.md records for `IgnoreCombi`.
    bellanoir = next(r for r in report["entries"] if r["name"] == "Bellanoir")
    assert bellanoir["route"]["breed"]["kind"] == "named_pairing"
    assert bellanoir["route"]["breed"]["pairings"]


def test_no_linked_character_is_reported_rather_than_scored_zero(entries):
    report = completion.tracker(entries, unlocked=[], linked=False)
    assert report["linked"] is False
    assert report["caught"] == 0


def test_stripping_the_missing_half_keeps_the_counts(entries):
    """
    How many you are missing is not a spoiler. Removing the number as well as
    the list would make the panel look broken rather than restricted.
    """
    report = completion.tracker(entries, unlocked=["Alpaca"])
    stripped = completion.strip_missing(report)
    assert stripped["missingHidden"] is True
    assert all(r["caught"] for r in stripped["entries"])
    assert stripped["missing"] == report["missing"]
    assert stripped["total"] == report["total"]


def test_unreleased_entries_do_not_merge_into_one_row(entries):
    """
    The game uses -1 for unreleased and -2 for gym bosses, so keying on the
    Paldeck number alone would collapse every unreleased Pal into a single row.
    """
    report = completion.tracker(entries, unlocked=[])
    negatives = [r for r in report["entries"]
                 if isinstance(r.get("paldeckNumber"), int)
                 and r["paldeckNumber"] <= 0]
    assert len({r["id"] for r in negatives}) == len(negatives)
