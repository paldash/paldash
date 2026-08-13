"""
Pruning other guilds out of an exported world — the half that removes things.

This is the one destructive operation here that does not go through
`guarded_save_write`, and the reason is structural rather than a concession:
`soloexport` reads the live world and writes a **new directory**, so `apply`
only ever mutates the in-memory tree of a copy. A bad result is a folder you
delete.

**That argument only holds if a bad result is WHOLE.** A half-pruned world loads
today and fails when somebody walks into the cell, so every failure path here
must raise and leave the caller writing the unpruned copy.

The fixtures are hand-built rather than taken from `refworld`: the integration
test below covers the real world, and a synthetic tree is the only way to
exercise the refusal, which a healthy save never triggers.
"""

from __future__ import annotations

import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import exportscope  # noqa: E402


def _guid(n: int) -> str:
    return f"{n:08d}-0000-0000-0000-000000000000"


def _wrapped(value):
    return {"value": value}


def _world(*, guilds, bases, objects, characters, containers=()):
    """A minimal world tree in the shapes the real save uses."""
    return {
        "GroupSaveDataMap": _wrapped([
            {"key": g, "value": {"RawData": _wrapped({
                "group_id": g, "group_type": "EPalGroupType::Guild",
                "individual_character_handle_ids": [
                    {"instance_id": c} for c, gid in characters if gid == g
                ],
                "players": [],
            })}}
            for g in guilds
        ]),
        "BaseCampSaveData": _wrapped([
            {"key": b, "value": {"RawData": _wrapped(
                {"id": b, "group_id_belong_to": g})}}
            for b, g in bases
        ]),
        "MapObjectSaveData": _wrapped({"values": [
            {
                "Model": _wrapped({"RawData": _wrapped(
                    {"base_camp_id_belong_to": b, "group_id_belong_to": ""})}),
                "ConcreteModel": _wrapped({"ModuleMap": _wrapped([
                    {"value": {"RawData": _wrapped({"target_container_id": c})}}
                    for c in cs
                ])}),
            }
            for b, cs in objects
        ]}),
        "CharacterSaveParameterMap": _wrapped([
            {"key": {"InstanceId": _wrapped(c)},
             "value": {"RawData": _wrapped({"group_id": g, "object": _wrapped(
                 {"SaveParameter": _wrapped({})})})}}
            for c, g in characters
        ]),
        "ItemContainerSaveData": _wrapped([
            {"key": {"ID": _wrapped(c)}, "value": {}} for c in containers
        ]),
        "CharacterContainerSaveData": _wrapped([]),
    }


KEEP, DROP = _guid(1), _guid(2)


def _two_guild_world():
    return _world(
        guilds=[KEEP, DROP],
        bases=[(_guid(10), KEEP), (_guid(11), DROP)],
        objects=[(_guid(10), [_guid(20)]), (_guid(11), [_guid(21)])],
        characters=[(_guid(30), KEEP), (_guid(31), DROP), (_guid(32), DROP)],
        containers=[_guid(20), _guid(21)],
    )


def test_a_prune_removes_one_guilds_things_and_nothing_else():
    world = _two_guild_world()
    result = exportscope.apply(world, keep_guilds=[KEEP])

    assert result["pruned"] is True
    assert result["removed"]["guilds"] == 1
    assert result["removed"]["bases"] == 1
    assert result["removed"]["mapObjects"] == 1
    assert result["removed"]["characters"] == 2
    assert result["removed"]["itemContainers"] == 1

    # And the kept guild's things are untouched — the half a count cannot show.
    assert len(world["BaseCampSaveData"]["value"]) == 1
    assert len(world["MapObjectSaveData"]["value"]["values"]) == 1
    assert len(world["CharacterSaveParameterMap"]["value"]) == 1
    assert len(world["ItemContainerSaveData"]["value"]) == 1


def test_keeping_everything_removes_nothing():
    world = _two_guild_world()
    before = copy.deepcopy(world)
    result = exportscope.apply(world, keep_guilds=[KEEP, DROP])
    assert result["pruned"] is False
    assert world == before


def test_keep_uid_cannot_drop_the_exporting_players_own_guild():
    """
    The common case is "just me", with no guild ids given at all. If that
    dropped the exporter's own guild the export would be empty — and it is the
    one mistake a user cannot notice until they load the world.
    """
    world = _two_guild_world()
    world["GroupSaveDataMap"]["value"][0]["value"]["RawData"]["value"]["players"] = [
        {"player_uid": _guid(99)}
    ]
    result = exportscope.apply(world, keep_uid=_guid(99))
    assert result["pruned"] is True
    assert KEEP not in result["dropGuildIds"]
    assert DROP in result["dropGuildIds"]


def test_a_surviving_reference_refuses_rather_than_half_pruning():
    """
    THE REFUSAL, and the reason the whole module is safe.

    A guild that survives while still listing a character we removed is the
    shape a naive prune fails: nothing about the guild looks wrong until the
    game reads its member list. Here the KEPT guild is given a handle pointing
    at a DROPPED guild's character, so removal leaves a dangling id.
    """
    world = _two_guild_world()
    kept_guild = world["GroupSaveDataMap"]["value"][0]["value"]["RawData"]["value"]
    kept_guild["individual_character_handle_ids"].append({"instance_id": _guid(31)})

    with pytest.raises(exportscope.ExportScopeError) as excinfo:
        exportscope.apply(world, keep_guilds=[KEEP])
    assert "guild member handles" in str(excinfo.value)


def test_verify_is_recomputed_rather_than_trusting_the_removal_counts():
    """
    `verify` reads the pruned tree instead of the loop's own arithmetic. A
    removal loop that miscounts is precisely the bug it exists to catch, so
    checking its own numbers would prove nothing.
    """
    world = _two_guild_world()
    ids = exportscope._dropped_ids(world, {DROP})
    assert exportscope.verify(world, ids), "unpruned tree must show dangling refs"
    exportscope.apply(world, keep_guilds=[KEEP])
    assert exportscope.verify(world, ids) == {}


def test_a_container_a_kept_object_still_uses_is_not_removed():
    """
    A container id can be referenced from both sides. Removing one because a
    dropped object mentioned it takes a chest out from under a guild the user
    asked to keep — silently, since the object survives and simply points at
    nothing.
    """
    shared = _guid(20)
    world = _world(
        guilds=[KEEP, DROP],
        bases=[(_guid(10), KEEP), (_guid(11), DROP)],
        objects=[(_guid(10), [shared]), (_guid(11), [shared])],
        characters=[],
        containers=[shared],
    )
    exportscope.apply(world, keep_guilds=[KEEP])
    assert len(world["ItemContainerSaveData"]["value"]) == 1


def test_the_plan_no_longer_claims_apply_is_missing():
    world = _two_guild_world()
    assert exportscope.plan(world, keep_guilds=[KEEP])["applyImplemented"] is True
