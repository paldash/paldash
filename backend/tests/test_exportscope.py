"""
The export prune PLAN, and the three zeroes that meant "wrong path".

Unit-level with a hand-built world, because the shapes this walks are exactly
where a wrong nesting returns an empty list rather than an error.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import exportscope  # noqa: E402


MINE, THEIRS = "aaaaaaaa-0000-0000-0000-000000000000", "bbbbbbbb-0000-0000-0000-000000000000"
ME, THEM = "11111111-0000-0000-0000-000000000000", "22222222-0000-0000-0000-000000000000"
MY_BASE, THEIR_BASE = "cccccccc-1111-2222-3333-444444444444", "dddddddd-1111-2222-3333-444444444444"


def _group(gid, admin, name):
    return {"value": {"RawData": {"value": {
        "group_id": gid, "guild_name": name, "admin_player_uid": admin,
        "players": [{"player_uid": admin}],
    }}}}


def _world():
    return {
        "GroupSaveDataMap": {"value": [
            _group(MINE, ME, "Mine"), _group(THEIRS, THEM, "Theirs"),
            # An Organization: no `players`, so it is not a guild.
            {"value": {"RawData": {"value": {"group_id": "ffff", "org": True}}}},
        ]},
        "BaseCampSaveData": {"value": [
            {"value": {"RawData": {"value": {
                "id": MY_BASE, "group_id_belong_to": MINE}}}},
            {"value": {"RawData": {"value": {
                "id": THEIR_BASE, "group_id_belong_to": THEIRS}}}},
        ]},
        "MapObjectSaveData": {"value": {"values": [
            {"Model": {"value": {"RawData": {"value": {
                "base_camp_id_belong_to": THEIR_BASE}}}},
             "ConcreteModel": {"value": {"ModuleMap": {"value": [
                 {"value": {"RawData": {"value": {"target_container_id": "x"}}}},
             ]}}}},
            {"Model": {"value": {"RawData": {"value": {
                "base_camp_id_belong_to": MY_BASE}}}}},
        ]}},
        "CharacterSaveParameterMap": {"value": [
            # Theirs, owned.
            {"value": {"RawData": {"value": {"group_id": THEIRS, "object": {
                "SaveParameter": {"value": {"OwnerPlayerUId": {"value": THEM}}}}}}}},
            # Theirs, OWNERLESS — a base worker. The case that proves the filter.
            {"value": {"RawData": {"value": {"group_id": THEIRS, "object": {
                "SaveParameter": {"value": {}}}}}}},
            {"value": {"RawData": {"value": {"group_id": MINE, "object": {
                "SaveParameter": {"value": {"OwnerPlayerUId": {"value": ME}}}}}}}},
        ]},
    }


def test_only_records_with_players_count_as_guilds():
    """An Organization group has no `players`, and 7 of 12 on the reference
    world are Organizations."""
    found = exportscope.guilds(_world())
    assert [g["name"] for g in found] == ["Mine", "Theirs"]


def test_the_exporting_player_keeps_their_own_guild_without_naming_it():
    plan = exportscope.plan(_world(), keep_uid=ME)
    assert plan["keepGuildIds"] == [MINE]
    assert plan["dropGuildIds"] == [THEIRS]


def test_it_counts_every_structure_the_drop_reaches():
    plan = exportscope.plan(_world(), keep_uid=ME)["removes"]
    assert plan == {
        "guilds": 1, "bases": 1, "mapObjects": 1, "containers": 1,
        "characters": 2, "ownerlessCharacters": 1, "playerSaves": 1,
    }


def test_an_ownerless_pal_is_counted_because_it_belongs_to_the_guild():
    """
    THE FILTER MUST KEY ON `group_id`, NOT ON OWNERSHIP. 159 of the reference
    world's 1,905 Pals carry no `OwnerPlayerUId` — base workers and shared
    stores. Filtering on owner strands every one of them, pointing at a guild
    that no longer exists.
    """
    removes = exportscope.plan(_world(), keep_uid=ME)["removes"]
    assert removes["ownerlessCharacters"] == 1
    assert removes["characters"] == 2


def test_keeping_everything_removes_nothing():
    plan = exportscope.plan(_world(), keep_guilds=[MINE, THEIRS])
    assert plan["dropGuildIds"] == []
    assert set(plan["removes"].values()) == {0}


def test_the_plan_now_reports_that_apply_exists_and_says_how_it_fails():
    """
    A REFUSAL THAT GOT ANSWERED. This asserted `applyImplemented is False` and
    "not implemented" in the note, because deletion across six interlinked
    structures was the one thing here where a half-finished implementation
    would be worse than none.

    It is implemented now (`exportscope.apply`, `test_exportscope_apply.py`), so
    the flag flips — but the note still has to carry the failure mode, because
    that is what stops a caller presenting a refused prune as a completed one.

    Rewritten rather than deleted: a refusal that expires should leave a trace,
    or the next reader cannot tell it was answered from it being dropped.
    """
    plan = exportscope.plan(_world(), keep_uid=ME)
    assert plan["applyImplemented"] is True
    assert "refuses" in plan["note"]
    assert "unpruned copy is written instead" in plan["note"]
