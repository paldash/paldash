"""
Named NPC placements — and the three things the naming must not do.

Against the shipped bundle, like `test_itemsource.py`: the thing being verified
is a *tag walk over world-cell actors*, and a fixture would pin the accessor
while saying nothing about whether the walk still lands.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import gamedata  # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    gamedata._reset_cache()
    yield
    gamedata._reset_cache()


def test_the_bundle_carries_placed_npcs_with_positions():
    placements = gamedata.npc_placements()
    assert len(placements) > 400
    assert all(p["x"] and p["y"] for p in placements)


def test_the_black_marketeer_is_named_by_the_game():
    """
    The whole point. `DarkTrader` is an internal id; "Black Marketeer" is what a
    player reads, and it comes from `DT_UniqueNPC` -> `DT_UniqueNPCText_Common`
    rather than from humanising the id.
    """
    merchants = gamedata.npc_placements("merchant")
    names = {m["name"] for m in merchants}
    assert "Black Marketeer" in names
    assert "Medal Merchant" in names
    # And it is a real placement, not a table row with no world position.
    dealers = [m for m in merchants if m["name"] == "Black Marketeer"]
    assert all(d["x"] and d["y"] for d in dealers)


def test_every_placement_has_a_name_and_none_is_a_raw_class():
    """
    Three fallbacks deep — unique row, character table, humanised id — so a blank
    name means the chain broke. And nothing may render as `BP_MonoNPCSpawner…`,
    which is what humanising the class name raw produces.
    """
    for p in gamedata.npc_placements():
        assert p["name"], p
        assert not p["name"].startswith("BP ")
        assert "NPCSpawner" not in p["name"]


def test_a_role_filter_returns_only_that_role():
    for role in ("merchant", "hunter", "villager"):
        assert {p["role"] for p in gamedata.npc_placements(role)} == {role}


def test_roles_are_labelled_for_the_layer_switches():
    roles = gamedata.npc_roles()
    assert roles["merchant"] == "Merchants & traders"
    # Every role present in the data must have a label, or a layer toggle
    # renders with no name.
    assert {p["role"] for p in gamedata.npc_placements()} <= set(roles)


def test_the_level_is_the_placements_own_not_the_tables():
    """
    The same unique NPC stands in several places at different levels — a
    spawner says what IT spawns. Taking `DT_UniqueNPC.Level` for all of them
    would report one figure for every Black Marketeer in the world.
    """
    dealers = gamedata.npc_placements("merchant")
    levels = {d["level"] for d in dealers if d["name"] == "Black Marketeer"}
    assert len(levels) > 1


def test_an_unnamed_npc_says_so_rather_than_guessing():
    """
    A generic spawner with no identity property is a real thing — it spawns
    whatever its blueprint defaults to. `nameIsInternal` marks it so the map can
    caveat it instead of presenting a humanised id as the game's own word.
    """
    placements = gamedata.npc_placements()
    internal = [p for p in placements if p["nameIsInternal"]]
    named = [p for p in placements if not p["nameIsInternal"]]
    assert internal and named
    assert all(p["name"] for p in internal)


def test_a_missing_bundle_is_empty_not_an_error(monkeypatch):
    """Every accessor here degrades to empty rather than taking a page down."""
    monkeypatch.setattr(gamedata, "NPCS_PATH", "/nonexistent/npcs.json.gz")
    gamedata._reset_cache()
    assert gamedata.npcs() == {}
    assert gamedata.npc_placements() == []
    assert gamedata.npc_roles() == {}
