"""
`MapObjectSpawnerInStageSaveData` — the stage walk and the sentinels.

The heavy verification (a 2x4 contingency table with every off-diagonal cell at
zero, across three worlds and ~99,800 slots) lives in
`scripts/decode-spawner-state.py`, which re-derives it on demand. What is pinned
here is the part that would silently regress: **reading every stage rather than
`[0]`**, and not treating a sentinel as a duration.
"""

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_spec = importlib.util.spec_from_file_location(
    "decode_spawner_state",
    os.path.join(ROOT, "scripts", "decode-spawner-state.py"),
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["decode_spawner_state"] = _mod
_spec.loader.exec_module(_mod)

read_stages = _mod.read_stages
OVERWORLD = _mod.OVERWORLD_STAGE


def _guid(value):
    return {"value": value, "type": "StructProperty"}


def _slot(key, when, object_id):
    return {
        "key": key,
        "value": {
            "NextLotteryGameTime": {"value": when, "type": "Int64Property"},
            "MapObjectInstanceId": _guid(object_id),
        },
    }


def _stage(internal_id, spawners):
    return {
        "key": {"InternalId": _guid(internal_id), "bValid": {"value": True}},
        "value": {
            "SpawnerDataMapByLevelObjectInstanceId": {
                "value": [
                    {"key": spawner_id, "value": {"ItemMap": {"value": slots}}}
                    for spawner_id, slots in spawners
                ]
            }
        },
    }


def _world(*stages):
    return {"MapObjectSpawnerInStageSaveData": {"value": list(stages)}}


def test_every_stage_is_read_not_just_the_first():
    """
    THE ONE THAT MATTERS. The outer map is keyed by stage: refworld has one
    entry, and a later snapshot of the same server has **three** — the overworld
    plus two instanced dungeon stages with five spawners each.

    `outer[0]` gets the overworld and silently drops the dungeons. That is the
    `base_camp_level` mistake, which went unnoticed for months because a check
    sampled `GroupSaveDataMap[0]`.
    """
    world = _world(
        _stage(OVERWORLD, [("spawn-a", [_slot(0, -1, "obj-a")])]),
        _stage("f5e9cad7-2a3e-4f88-b5c4-a6e548ac8117", [("spawn-b", [_slot(0, 500, "")])]),
        _stage("2c3b9e42-8b43-4510-84cb-8b29b079a791", [("spawn-c", [_slot(0, 900, "")])]),
    )
    stages = read_stages(world)
    assert len(stages) == 3
    assert sum(len(s["slots"]) for s in stages) == 3
    assert [s["isOverworld"] for s in stages] == [True, False, False]


def test_the_overworld_is_identified_by_the_all_zero_id():
    """Not by position. A stage list that reorders must not change the answer."""
    world = _world(
        _stage("f5e9cad7-2a3e-4f88-b5c4-a6e548ac8117", [("spawn-b", [_slot(0, 5, "")])]),
        _stage(OVERWORLD, [("spawn-a", [_slot(0, -1, "obj-a")])]),
    )
    stages = read_stages(world)
    assert [s["isOverworld"] for s in stages] == [False, True]


def test_a_spawner_with_several_slots_yields_several_rows():
    world = _world(_stage(OVERWORLD, [
        ("spawn-a", [_slot(0, -1, "obj-a"), _slot(1, 700, "")]),
    ]))
    stages = read_stages(world)
    assert stages[0]["spawners"] == 1
    assert len(stages[0]["slots"]) == 2


def test_an_absent_structure_is_not_an_error():
    """
    A world with no spawner state at all must read as nothing, not raise. Same
    reason `worldSaveData` keys differ between saves — 26 top-level structures
    across three worlds and no single save has all of them.
    """
    assert read_stages({}) == []
    assert read_stages({"MapObjectSpawnerInStageSaveData": {"value": []}}) == []


def test_the_never_sentinel_is_the_dotnet_maximum():
    """
    `3155378975999999999` is exactly `DateTime.MaxValue.Ticks`. As a duration it
    is 87,637,883 game-hours, and an unguarded summary prints that as the
    respawn range — nonsense wearing a number.

    Asserted against the arithmetic rather than the literal, so the constant
    cannot be quietly changed to something that merely looks right.
    """
    import datetime

    assert _mod.NEVER_TICKS == 3_155_378_975_999_999_999
    # .NET ticks are 100ns since 0001-01-01; DateTime.MaxValue is the last tick
    # of 9999-12-31. Reconstructing it here is the independent check.
    span = datetime.datetime(9999, 12, 31, 23, 59, 59, 999999) - datetime.datetime(1, 1, 1)
    reconstructed = (span.days * 86400 + span.seconds) * 10_000_000 + span.microseconds * 10
    assert _mod.NEVER_TICKS // 10 == reconstructed // 10


def test_the_three_sentinels_are_distinct():
    """
    `-1` (idle, object standing), `0` (never written) and `DateTime.MaxValue`
    (never respawns) mean three different things. Collapsing any two loses a
    real state — the same care `parser` takes over the all-zero player uid.
    """
    assert len({_mod.NO_TIMER, _mod.UNSET, _mod.NEVER_TICKS}) == 3
