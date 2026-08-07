"""
155 of the 396 effigies rendered as the bare word "Effigy".

Which is 39% of the layer, and a marker labelled with its own category name
reads as data that failed to load rather than as the answer — the same failure
shape as the empty work-suitability panel and the empty base list.

They are not unnamed. They are the plain relic, and the game's catalogue calls
the plain relic **"Lifmunk Effigy"**.
"""

from __future__ import annotations

import collections
import gzip
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import gamedata  # noqa: E402

_GAMEDATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "gamedata.json.gz",
)


def _catalogue_effigy_names() -> set[str]:
    with gzip.open(_GAMEDATA, "rt", encoding="utf-8") as f:
        items = json.load(f)["items"]
    rows = items.values() if isinstance(items, dict) else items
    return {
        str(row.get("name") or "")
        for row in rows
        if str(row.get("name") or "").endswith("Effigy")
    }


def test_every_placed_kind_resolves_to_a_name_the_game_itself_uses():
    """
    **The suffix rule is checked against the catalogue, not merely trusted.**
    `BP_LevelObject_Relic_IceCrocodile` -> "Munchill Effigy" is a two-hop guess:
    the suffix is a species id, `pal_name` resolves it, and " Effigy" is appended.
    That is precisely the sort of derivation that reads right and is wrong — so
    the assertion is that all ten placed classes land on a name the item table
    ships for `Relic`/`Relic_01`..`Relic_12`.
    """
    catalogue = _catalogue_effigy_names()
    assert len(catalogue) >= 13, "the effigy items went missing from the bundle"

    kinds = {e["kind"] for e in gamedata.effigies()}
    wrong = {
        kind: gamedata.effigy_kind_name(kind)
        for kind in kinds
        if gamedata.effigy_kind_name(kind) not in catalogue
    }
    assert wrong == {}, (
        "these effigy classes resolve to a name the game does not use:\n  "
        + "\n  ".join(f"{k} -> {v!r}" for k, v in sorted(wrong.items()))
    )


def test_the_unsuffixed_classes_are_lifmunk_effigies():
    """
    The 155, by name. Both plain classes, and neither may fall back to the bare
    category word while the catalogue has a real name for them.
    """
    for kind in ("BP_LevelObject_Relic", "BP_RelicObject"):
        assert gamedata.effigy_kind_name(kind) == "Lifmunk Effigy"

    counts = collections.Counter(e["kindName"] for e in gamedata.effigies())
    assert counts["Lifmunk Effigy"] == 155
    assert "Effigy" not in counts, (
        "an effigy is still labelled with its own category name"
    )


def test_no_effigy_is_labelled_with_a_raw_game_id():
    """
    `prettyClass` used to turn these into "Relic Sheep Ball" and "BP Relic
    Object". De-underscoring a game id is not the same as knowing what it means,
    and the tell is that the raw token survives into the label.
    """
    for effigy in gamedata.effigies():
        name = effigy["kindName"]
        assert "_" not in name
        assert not name.startswith("BP ")
        assert "Relic" not in name
        assert name.endswith("Effigy")


def test_a_missing_catalogue_degrades_to_the_category_word(monkeypatch):
    """
    The plain name is resolved through the catalogue rather than hardcoded, so a
    bundle that fails to load must give back "Effigy" — not the literal id
    "Relic", which is a game-file token and the thing this whole module exists to
    keep out of the UI.
    """
    monkeypatch.setattr(gamedata, "item_name", lambda item_id: item_id)
    assert gamedata.effigy_kind_name("BP_LevelObject_Relic") == "Effigy"


def test_every_placed_effigy_carries_the_game_s_own_artwork():
    """
    **155 of the 396 drew as a bare shape**, and the reason was a hardcoded
    nine-entry table in `map-inner.tsx`. It covered the kinds whose class names
    a species — `…_IceCrocodile` and friends — and had nothing for the two
    unsuffixed ones, `BP_LevelObject_Relic` (89) and `BP_RelicObject` (66),
    because they name no Pal to borrow a portrait from.

    They are the plain Lifmunk relic, and the game ships artwork for it. The
    join is on the NAME, which `test_effigy_names` above already pins: nothing
    connects `…_IceCrocodile` to `Relic_03` directly, but both resolve to
    "Munchill Effigy".
    """
    import gamedata

    points = gamedata.effigies()
    assert len(points) == 396
    without = [p for p in points if not p.get("icon")]
    assert without == [], (
        f"{len(without)} effigies have no artwork; the first is "
        f"{without[0]['kind'] if without else ''}"
    )

    # The two generic kinds get the plain relic, not a Pal portrait.
    generic = [p for p in points if p["kind"] in ("BP_LevelObject_Relic", "BP_RelicObject")]
    assert len(generic) == 155
    assert {p["icon"] for p in generic} == {"/icons/items/T_itemicon_Relic.webp"}


def test_an_unknown_effigy_class_gets_no_icon_rather_than_a_wrong_one():
    """Degrades to the shape every effigy had before, not to a stand-in."""
    import gamedata

    assert gamedata.effigy_kind_icon("BP_LevelObject_Relic_NoSuchPal") == ""
    assert gamedata.effigy_kind_icon("") == ""
