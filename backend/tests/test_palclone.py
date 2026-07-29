"""
Pal duplication (Phase 7).

This is the only code in the project that *creates* save records rather than
overwriting fields that already exist, so the tests are about the two records
staying in agreement — a Pal in the character map with no container slot is a
ghost, and a slot pointing at nothing is worse.
"""

from __future__ import annotations

import pytest

import charedit
import palclone
from test_charedit import pal_object


def guid(n: int) -> str:
    return f"{n:08x}-0000-0000-0000-000000000000"


CONTAINER = guid(0xC0)


def character_entry(instance_id, obj, container=CONTAINER, slot=0, is_player=False):
    if is_player:
        obj = {**obj, "IsPlayer": {"value": True}}
    obj = {
        **obj,
        "CharacterID": {"value": "Sheepball"},
        "SlotId": {
            "struct_type": "PalCharacterSlotId",
            "value": {
                "ContainerId": {"value": {"ID": {"value": container}}},
                "SlotIndex": {"value": slot},
            },
            "type": "StructProperty",
        },
    }
    return {
        "key": {
            "PlayerUId": {"value": guid(1)},
            "InstanceId": {"value": instance_id},
        },
        "value": {
            "RawData": {
                "value": {
                    "object": {"SaveParameter": {"value": obj}},
                    "group_id": guid(0x61),
                },
                "type": "ArrayProperty",
            }
        },
    }


def container_slot(instance_id, index):
    return {
        "SlotIndex": {"value": index, "type": "IntProperty"},
        "RawData": {
            "array_type": "ByteProperty",
            "value": {
                "player_uid": "00000000-0000-0000-0000-000000000000",
                "instance_id": instance_id,
                "permission_tribe_id": 0,
                "unknown_bytes": [0, 0, 0, 0, 0],
            },
            "type": "ArrayProperty",
        },
        "CustomVersionData": {"array_type": "ByteProperty", "value": {"values": b"\x01"}},
    }


class FakeGvas:
    """Just enough tree for the pure planner."""

    def __init__(self, characters, containers):
        self.properties = {
            "worldSaveData": {
                "value": {
                    "CharacterSaveParameterMap": {"value": characters},
                    "CharacterContainerSaveData": {"value": containers},
                }
            }
        }


def world(pal_count=2, capacity=10, container=CONTAINER):
    chars = [
        character_entry(guid(i), pal_object(level=10 + i), container, i)
        for i in range(pal_count)
    ]
    containers = [{
        "key": {"ID": {"value": container}},
        "value": {
            "SlotNum": {"value": capacity},
            "Slots": {"value": {"values": [container_slot(guid(i), i) for i in range(pal_count)]}},
        },
    }]
    return FakeGvas(chars, containers)


# ─── Reading ─────────────────────────────────────────────────────


def test_free_space_is_capacity_minus_used_not_empty_slots():
    """
    The finding this whole module is shaped by: across the reference world's 23
    character containers there are 1,905 slot entries and 1,905 Pals — zero
    empty slots. `SlotNum` is capacity; the array holds only occupied slots.
    """
    described = palclone.describe_containers(world(pal_count=3, capacity=10))
    assert described == [{
        "containerId": CONTAINER, "capacity": 10, "used": 3, "free": 7,
    }]


# ─── Planning ────────────────────────────────────────────────────


def test_plans_a_single_clone():
    plan = palclone.plan_clone(world(), guid(0), CONTAINER, 1)

    assert plan["ok"], plan["problems"]
    assert plan["count"] == 1
    assert plan["slotIndices"] == [2]      # continues after the two existing
    assert plan["freeAfter"] == 7
    assert plan["planHash"]


def test_plans_several_into_consecutive_slots():
    plan = palclone.plan_clone(world(), guid(0), CONTAINER, 3)
    assert plan["slotIndices"] == [2, 3, 4]


def test_refuses_to_overflow_the_container():
    plan = palclone.plan_clone(world(pal_count=8, capacity=10), guid(0), CONTAINER, 5)
    assert not plan["ok"]
    assert "would overflow" in plan["problems"][0]["problem"]


def test_filling_the_container_exactly_is_allowed():
    plan = palclone.plan_clone(world(pal_count=8, capacity=10), guid(0), CONTAINER, 2)
    assert plan["ok"], plan["problems"]
    assert plan["freeAfter"] == 0


def test_refuses_to_clone_a_player():
    chars = [character_entry(guid(0), pal_object(), is_player=True)]
    containers = world().properties["worldSaveData"]["value"]["CharacterContainerSaveData"]["value"]
    plan = palclone.plan_clone(FakeGvas(chars, containers), guid(0), CONTAINER, 1)

    assert not plan["ok"]
    assert "player character" in plan["problems"][0]["problem"]


def test_refuses_an_unknown_source():
    plan = palclone.plan_clone(world(), guid(0xDEAD), CONTAINER, 1)
    assert not plan["ok"]
    assert "No character with instance id" in plan["problems"][0]["problem"]


def test_refuses_an_unknown_container():
    plan = palclone.plan_clone(world(), guid(0), guid(0xBEEF), 1)
    assert not plan["ok"]
    assert "No character container" in plan["problems"][0]["problem"]


@pytest.mark.parametrize("count", [0, -1, True, 2.5])
def test_refuses_a_nonsense_count(count):
    plan = palclone.plan_clone(world(), guid(0), CONTAINER, count)
    assert not plan["ok"]


def test_refuses_more_than_the_batch_ceiling():
    plan = palclone.plan_clone(
        world(capacity=10_000), guid(0), CONTAINER, palclone.MAX_CLONES + 1
    )
    assert not plan["ok"]
    assert "maximum" in plan["problems"][0]["problem"]


def test_a_clone_time_edit_is_validated_like_any_other():
    """A clone must not be a way around the schema bounds."""
    ok = palclone.plan_clone(world(), guid(0), CONTAINER, 1, {"rank": 4})
    assert ok["ok"], ok["problems"]
    assert ok["changes"][0]["field"] == "rank"

    bad = palclone.plan_clone(world(), guid(0), CONTAINER, 1, {"rank": 99})
    assert not bad["ok"]


def test_plan_hash_covers_the_destination():
    """Approving a clone into one palbox must not authorise another."""
    a = palclone.plan_clone(world(), guid(0), CONTAINER, 1)

    other = guid(0xC1)
    b = palclone.plan_clone(world(container=other), guid(0), other, 1)
    assert a["planHash"] != b["planHash"]


def test_plan_hash_changes_with_the_count():
    a = palclone.plan_clone(world(), guid(0), CONTAINER, 1)
    b = palclone.plan_clone(world(), guid(0), CONTAINER, 2)
    assert a["planHash"] != b["planHash"]


# ─── Record construction ─────────────────────────────────────────


def test_the_clone_gets_a_new_identity_and_the_right_slot():
    source = character_entry(guid(0), pal_object(level=42), CONTAINER, 0)
    clone = palclone._new_character(source, guid(0x99), CONTAINER, 7)

    assert clone["key"]["InstanceId"]["value"] == guid(0x99)
    obj = charedit._save_parameter(clone)
    slot_id = obj["SlotId"]["value"]
    assert slot_id["ContainerId"]["value"]["ID"]["value"] == CONTAINER
    assert slot_id["SlotIndex"]["value"] == 7
    assert charedit.read_pal(obj)["level"] == 42


def test_cloning_does_not_mutate_the_source():
    """A deep copy, or the original Pal gets moved instead of copied."""
    source = character_entry(guid(0), pal_object(level=42), CONTAINER, 0)
    palclone._new_character(source, guid(0x99), guid(0xC1), 7)

    assert source["key"]["InstanceId"]["value"] == guid(0)
    slot_id = charedit._save_parameter(source)["SlotId"]["value"]
    assert slot_id["ContainerId"]["value"]["ID"]["value"] == CONTAINER
    assert slot_id["SlotIndex"]["value"] == 0


def test_the_new_container_slot_points_at_the_clone():
    template = container_slot(guid(0), 0)
    slot = palclone._new_slot(template, guid(0x99), 7)

    assert slot["SlotIndex"]["value"] == 7
    assert slot["RawData"]["value"]["instance_id"] == guid(0x99)
    # Copied, not constructed — these carry values only this save knows.
    assert "permission_tribe_id" in slot["RawData"]["value"]
    assert "CustomVersionData" in slot


def test_the_slot_template_is_not_mutated():
    template = container_slot(guid(0), 0)
    palclone._new_slot(template, guid(0x99), 7)

    assert template["SlotIndex"]["value"] == 0
    assert template["RawData"]["value"]["instance_id"] == guid(0)


def test_an_undecoded_slot_is_refused_rather_than_written_as_bytes():
    """
    Character-container slots are only decoded with the item property set. If
    they came through as a raw byte blob, placing a Pal would mean hand-writing
    binary — which this refuses to do.
    """
    raw = {
        "SlotIndex": {"value": 0},
        "RawData": {"array_type": "ByteProperty", "value": {"values": b"\x00" * 38}},
    }
    with pytest.raises(palclone.CloneError, match="not decoded"):
        palclone._new_slot(raw, guid(0x99), 1)
