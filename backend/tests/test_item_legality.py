"""
`bLegalInGame`, and the three things it must never be turned into.

Asserted against the **shipped bundle** rather than a fixture, for the reason
`test_gametext.py` and `test_palresist.py` already give: a test of the extractor
passes happily beside a bundle built before the extractor changed. What ships is
what callers get.

The claim under test is deliberately small. 575 of the 2,466 items carry
`bLegalInGame: False`, and the only actionable reading of that is the 95 which
share a display name with exactly one legal item — `Gunpowder` -> `Gunpowder2`.
Everything else here exists to stop that being widened into something the data
does not support.
"""

import gzip
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gamedata  # noqa: E402


BUNDLE = json.load(gzip.open(gamedata.DATA_PATH))["items"]


def _illegal():
    return {k: v for k, v in BUNDLE.items() if v.get("legalInGame") is False}


def _twinned():
    return {k: v for k, v in BUNDLE.items() if v.get("liveTwin")}


def test_the_flag_is_written_only_when_it_says_something():
    """1,891 legal items carry no key at all, matching `zukanSuffix`'s rule."""
    assert len(_illegal()) == 575
    legal = [k for k, v in BUNDLE.items() if "legalInGame" not in v]
    assert len(legal) == len(BUNDLE) - 575
    assert all(v.get("legalInGame") is not True for v in BUNDLE.values())


def test_a_twin_is_only_ever_on_the_dead_side():
    """
    The live item must not point back. `liveTwin` marks which of two rows to
    avoid, so putting it on both makes the pair symmetric and says nothing.
    """
    for ident, entry in _twinned().items():
        assert entry.get("legalInGame") is False, ident
        twin = BUNDLE[entry["liveTwin"]]
        assert twin.get("legalInGame") is not False, ident
        assert "liveTwin" not in twin, ident


def test_a_twin_shares_the_display_name_and_that_is_the_whole_join():
    """
    If this ever stops holding, `liveTwin` has become something else — the join
    is on the name and nothing but the name.
    """
    twinned = _twinned()
    assert len(twinned) == 95
    for ident, entry in twinned.items():
        assert entry["name"] == BUNDLE[entry["liveTwin"]]["name"], ident


def test_the_twin_is_unique_so_nothing_was_guessed():
    """
    A name with two legal claimants gets no twin — `OctaviaRevolver_2..4` have
    both `OctaviaRevolver` and `OctaviaRevolver_5` alive, and picking one would
    be inventing data. Six items are in that state and none carries a twin.
    """
    by_name: dict[str, list[str]] = {}
    for ident, entry in BUNDLE.items():
        by_name.setdefault(entry.get("name") or "", []).append(ident)

    ambiguous = 0
    for ident, entry in _illegal().items():
        live = [i for i in by_name[entry.get("name") or ""]
                if BUNDLE[i].get("legalInGame") is not False]
        if len(live) > 1:
            ambiguous += 1
            assert "liveTwin" not in entry, ident
        elif len(live) == 1:
            assert entry.get("liveTwin") == live[0], ident
    assert ambiguous == 6


def test_the_flag_does_not_mean_unobtainable():
    """
    THE LOAD-BEARING ONE. Ten of the 575 are held in refworld's containers, so
    any UI or filter reading this as "cannot be obtained" is wrong about items
    players own. Key Spheres are the clearest case — seven of them.

    Checked against the bundle rather than a save so it runs without refworld;
    the ids are the ones the census actually turned up.
    """
    held_anyway = [
        "KeySphere_01", "KeySphere_02", "KeySphere_03", "KeySphere_04",
        "KeySphere_05", "KeySphere_06", "KeySphere_07",
        "WhaleWhistle", "Blueprint_WhaleWhistle", "MachingunBullet",
    ]
    illegal = _illegal()
    for ident in held_anyway:
        assert ident in illegal, f"{ident} should still carry the flag"
        # ...and none of them is badged, because none has a legal namesake.
        assert "liveTwin" not in illegal[ident], ident


def test_the_two_suffix_is_not_the_rule():
    """
    `Gunpowder2` is the live one and `Leather2` is the dead one. A reader that
    derived liveness from the id would get one of these backwards, which is why
    the flag is read from the table instead.
    """
    assert BUNDLE["Gunpowder"].get("legalInGame") is False
    assert BUNDLE["Gunpowder2"].get("legalInGame") is not False
    assert BUNDLE["Leather"].get("legalInGame") is not False
    assert BUNDLE["Leather2"].get("legalInGame") is False


def test_the_catalogue_carries_both_fields_and_no_restatement():
    """
    `all_items` is what the editor reads. A `liveTwinName` would equal `name` on
    every row by construction, so its absence is asserted rather than assumed.
    """
    rows = {r["id"]: r for r in gamedata.all_items()}
    assert len(rows) == len(BUNDLE)
    assert rows["Gunpowder"]["liveTwin"] == "Gunpowder2"
    assert rows["Gunpowder"]["legalInGame"] is False
    assert "liveTwin" not in rows["Gunpowder2"]
    assert "legalInGame" not in rows["Gunpowder2"]
    assert not any("liveTwinName" in r for r in rows.values())


def test_nothing_here_claims_a_mechanic():
    """
    A first draft of the UI badge said a legacy id would not be recognised by
    crafting. **88 of the 95 appear in `DT_ItemRecipeDataTable`**, so that was
    false — and false in the comfortable direction, because it reads like an
    explanation of the flag.

    This pins the measurement so the claim cannot come back: the flag and recipe
    membership are independent, and anything asserting otherwise is wrong.
    """
    economy = json.load(gzip.open(
        os.path.join(os.path.dirname(gamedata.DATA_PATH), "economy.json.gz")))
    referenced: set[str] = set()
    for product, recipes in (economy.get("recipes") or {}).items():
        referenced.add(product.lower())
        for recipe in (recipes if isinstance(recipes, list) else [recipes]):
            for material in (recipe.get("materials") or []):
                referenced.add(str(material.get("id", "")).lower())

    in_recipes = sum(1 for i in _twinned() if i.lower() in referenced)
    assert in_recipes == 88, (
        "the legacy ids are still overwhelmingly present in the recipe table, "
        "so the flag cannot be described as removing them from crafting"
    )
