"""
Guild markers, and why they are the one map layer that is private by default.

`guild_markers` sat in `GroupSaveDataMap` unread until a world turned up with any
in it — `mine-savefields.py` had listed it, nothing had looked. The world that
first carried them has **3 on one guild and 0 on the other four**, which is
itself the reason it took a second save to find: the reference world has none.

**The game's own strings decide the visibility rule, not a judgement call.**
`DT_UI_Common_Text` carries `MAP_MARKER_HEAD_GUILD` = "Guild Marker" and
`MAP_MARKER_GUILD_INFO` = **"Shared with Guild Members"**. So a dashboard that
showed every guild's pins to every viewer would publish something the game
deliberately keeps inside a guild.

That makes this the **opposite default** from base privacy: a base is visible
until its owner hides it; a marker is hidden unless you share the guild.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import parser as save_parser  # noqa: E402


def _guild(raw: dict) -> dict:
    return {"value": {"RawData": {"value": raw}}}


_MARKER = {
    "marker_id": "b41141c1-4b22-1be9-0852-f69f55f81352",
    "icon_location": {"x": -12443.94, "y": 293482.89, "z": 0.0},
    "icon_type": 6,
    "owner_player_uid": "22b22b02-0000-0000-0000-000000000000",
}


def test_a_marker_is_read_with_its_position_owner_and_type():
    out = save_parser._guild_markers({"guild_markers": [_MARKER]})
    assert out == [{
        "id": "b41141c1-4b22-1be9-0852-f69f55f81352",
        "x": pytest.approx(-12443.94),
        "y": pytest.approx(293482.89),
        "iconType": 6,
        "ownerUid": "22b22b02-0000-0000-0000-000000000000",
    }]


def test_the_icon_type_is_carried_as_an_INTEGER_and_never_named():
    """
    **Where the search stopped, recorded so nobody repeats it.** Values 0 and 6
    are observed. There is no marker DataTable in either pak; the client ships
    five `MI_UI_MapMarker_*` materials (`00`, `Camp`, `FTTower`, `Oilrig`,
    `Tower`) which are the *map's own* markers and cannot be this set, because
    the index already exceeds them. The custom-pin sprites live in
    `WBP_MapMarker_Button`, a widget blueprint cooked with unversioned
    properties — the same wall `elements.py` documents.

    Naming them from a guessed ordering would be the `TowerLockBarrier` mistake:
    a category whose vocabulary disagrees with the game's, however plausible.
    """
    out = save_parser._guild_markers({"guild_markers": [_MARKER]})
    assert isinstance(out[0]["iconType"], int)
    assert "iconName" not in out[0] and "icon" not in out[0]


def test_a_guild_with_no_markers_gets_an_empty_list_not_a_missing_key():
    """
    Four of the five guilds on the world that has any carry none. An absent key
    and an empty list are the same thing to a `for` loop and different things to
    a UI counting layers, so the parser always emits the list.
    """
    assert save_parser._guild_markers({}) == []
    assert save_parser._guild_markers({"guild_markers": []}) == []


def test_a_malformed_marker_is_dropped_rather_than_half_read():
    """
    Same rule the measured-offset readers follow: a record that does not resolve
    yields nothing rather than a confident wrong position on a map.
    """
    out = save_parser._guild_markers({"guild_markers": [
        "not a dict",
        {"marker_id": "x"},                      # no location
        {"marker_id": "y", "icon_location": []},  # wrong shape
        _MARKER,
    ]})
    assert len(out) == 1
    assert out[0]["id"] == _MARKER["marker_id"]


def test_extract_guilds_attaches_them():
    # `_world_save_data` reads `.properties` as an ATTRIBUTE (palsav hands back a
    # GvasFile, not a dict), so the fake has to be an object. A dict fake here
    # silently yields no guilds and the test "passes" against broken code the
    # moment its assertion is loosened.
    from types import SimpleNamespace
    guilds = save_parser.extract_guilds(SimpleNamespace(
        properties={"worldSaveData": {"value": {"GroupSaveDataMap": {"value": [
            _guild({
                "group_type": "EPalGroupType::Guild",
                "group_id": "g1", "guild_name": "Greed",
                "guild_markers": [_MARKER],
            }),
            _guild({
                "group_type": "EPalGroupType::Guild",
                "group_id": "g2", "guild_name": "Quiet",
            }),
            # Organization records have a different, smaller key set and must
            # not be mistaken for guilds — the `base_camp_level` trap.
            _guild({"group_type": "EPalGroupType::Organization", "group_id": "o1"}),
        ]}}}}
    ))
    assert [g["name"] for g in guilds] == ["Greed", "Quiet"]
    assert len(guilds[0]["markers"]) == 1
    assert guilds[1]["markers"] == []


def test_positions_are_world_coordinates_not_map_space():
    """
    Checked against the landmass extents rather than assumed: on the world that
    carries them, one marker lands on Palpagos and two on World Tree. Values in
    the hundreds of thousands are world units — a map-space or normalised
    coordinate would be small, and would have been drawn in the sea.
    """
    out = save_parser._guild_markers({"guild_markers": [
        _MARKER,
        {**_MARKER, "marker_id": "wt",
         "icon_location": {"x": 517186.6, "y": -633666.4, "z": 0.0}},
    ]})
    assert abs(out[0]["x"]) < 1_100_000 and abs(out[0]["y"]) < 800_000
    assert out[1]["x"] > 300_000, "the World Tree marker lost its magnitude"
