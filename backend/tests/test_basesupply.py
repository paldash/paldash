"""
The base supply advisor, and the guild-chest join it rests on.

Two things are pinned here that cost real debugging to establish:

  * the Guild Chest is a **guild-level** container reached through
    `GuildExtraSaveDataMap`, not a base container reached through an
    `ItemContainer` module — a per-base walk finds it holding nothing;
  * `BreedFarm` is the Breeding Farm and `MonsterFarm` is the Ranch. The POI
    categories had these confused, which made all five Breeding Farms on the
    reference world invisible.
"""

from __future__ import annotations

import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import basesupply  # noqa: E402
from parser import _categorise, extract_guild_storage  # noqa: E402


# ─── The guild chest join ────────────────────────────────


def _guid_bytes(guid: str) -> bytes:
    """A GUID as Palworld writes it: four little-endian uint32s."""
    hexed = guid.replace("-", "")
    return b"".join(
        struct.pack("<I", int(hexed[i:i + 8], 16)) for i in (0, 8, 16, 24)
    )


CONTAINER = "0c3ad68a-4d20-065d-b07f-a3a58a4be2b9"


class FakeGvas:
    def __init__(self, world):
        self.properties = {"worldSaveData": {"value": world}}


def _gvas(*, blob: bytes, known: list[str], guild_id: str = "guild-1"):
    return FakeGvas({
        "ItemContainerSaveData": {
            "value": [{"key": {"ID": {"value": c}}} for c in known]
        },
        "GuildExtraSaveDataMap": {
            "value": [
                {
                    "key": {"value": guild_id},
                    "value": {
                        "GuildItemStorage": {
                            "value": {"RawData": {"value": {"values": blob}}}
                        }
                    },
                }
            ]
        },
    })


def test_guild_chest_resolves_from_offset_zero():
    gvas = _gvas(blob=_guid_bytes(CONTAINER) + b"\x00\x00\x00\x00", known=[CONTAINER])
    assert extract_guild_storage(gvas) == {"guild-1": CONTAINER}


def test_unresolvable_id_is_dropped_not_guessed():
    """
    The verification that makes a measured-offset read defensible. A layout
    change must yield nothing, never a confident wrong answer about what a guild
    is holding.
    """
    gvas = _gvas(
        blob=_guid_bytes(CONTAINER) + b"\x00\x00\x00\x00",
        known=["ffffffff-0000-0000-0000-000000000000"],
    )
    assert extract_guild_storage(gvas) == {}


def test_short_blob_is_skipped():
    gvas = _gvas(blob=b"\x01\x02\x03", known=[CONTAINER])
    assert extract_guild_storage(gvas) == {}


def test_no_guilds_is_not_an_error():
    assert extract_guild_storage(FakeGvas({})) == {}


# ─── The category confusion ──────────────────────────────


def test_breedfarm_is_breeding_and_monsterfarm_is_a_ranch():
    """
    These were the same category, and the one it was named for matched nothing —
    so the reference world's five Breeding Farms were dropped by `_categorise`
    and never reached the map at all.
    """
    assert _categorise("BreedFarm") == "breeding"
    assert _categorise("MonsterFarm") == "ranch"


# ─── The advisor ─────────────────────────────────────────


def _summary(containers):
    return {
        "baseId": "base-1",
        "baseName": "Base Camp 1",
        "guildId": "guild-1",
        "guildName": "Greed",
        "containers": containers,
        "items": [{"itemId": "Wood", "itemName": "Wood", "count": 20}],
    }


def _slots(pairs):
    return [
        {"itemId": i, "stackCount": n, "isEmpty": False} for i, n in pairs
    ]


def test_empty_feed_box_is_reported():
    summary = _summary([
        {"containerId": "c1", "kind": "PalFoodBox", "kindName": "Feed Box",
         "usedSlots": 0, "totalSlots": 10},
    ])
    report = basesupply.base_report(
        summary, {"c1": []}, staples=("Wood",), floor=10
    )
    assert [n["kind"] for n in report["notes"]] == ["emptyFeedBox"]
    assert report["feedBoxes"][0]["itemCount"] == 0


def test_stocked_feed_box_raises_nothing():
    summary = _summary([
        {"containerId": "c1", "kind": "PalFoodBox", "kindName": "Feed Box",
         "usedSlots": 1, "totalSlots": 10},
    ])
    report = basesupply.base_report(
        summary, {"c1": _slots([("Berries", 500)])}, staples=("Wood",), floor=10
    )
    assert report["notes"] == []


def test_absent_feed_box_is_a_different_note():
    report = basesupply.base_report(
        _summary([]), {}, staples=("Wood",), floor=10
    )
    assert [n["kind"] for n in report["notes"]] == ["noFeedBox"]


def test_breeding_farm_without_cake():
    summary = _summary([
        {"containerId": "c1", "kind": "PalFoodBox", "kindName": "Feed Box",
         "usedSlots": 1, "totalSlots": 10},
        {"containerId": "c2", "kind": "BreedFarm", "kindName": "Breeding Farm",
         "usedSlots": 0, "totalSlots": 1},
    ])
    report = basesupply.base_report(
        summary,
        {"c1": _slots([("Berries", 500)]), "c2": []},
        staples=("Wood",), floor=10,
    )
    assert [n["kind"] for n in report["notes"]] == ["breedingFarmNoCake"]


def test_breeding_farm_with_cake_is_quiet():
    summary = _summary([
        {"containerId": "c1", "kind": "PalFoodBox", "kindName": "Feed Box",
         "usedSlots": 1, "totalSlots": 10},
        {"containerId": "c2", "kind": "BreedFarm", "kindName": "Breeding Farm",
         "usedSlots": 1, "totalSlots": 1},
    ])
    report = basesupply.base_report(
        summary,
        {"c1": _slots([("Berries", 500)]), "c2": _slots([("Cake", 3)])},
        staples=("Wood",), floor=10,
    )
    assert report["notes"] == []


def test_every_cake_variant_counts_and_pancake_does_not():
    """
    Found by prefix so a content update is covered, and `Pancake` is the reason
    a substring match would have been wrong.
    """
    cakes = basesupply.cake_ids()
    assert "Cake" in cakes and "Cake05" in cakes
    assert "Pancake" not in cakes


def test_staple_below_floor_is_flagged_with_the_game_stack_beside_it():
    report = basesupply.base_report(
        _summary([{"containerId": "c1", "kind": "PalFoodBox", "kindName": "Feed Box",
                   "usedSlots": 1, "totalSlots": 10}]),
        {"c1": _slots([("Berries", 5)])},
        staples=("Wood", "Stone"), floor=100,
    )
    wood = next(s for s in report["staples"] if s["itemId"] == "Wood")
    assert wood["count"] == 20 and wood["below"] is True
    # The distinction the payload has to carry: the floor is ours, 9999 is the
    # game's, and presenting the first as the second would be inventing a rule.
    assert wood["floor"] == 100
    assert wood["stackSize"] == 9999
    stone = next(s for s in report["staples"] if s["itemId"] == "Stone")
    assert stone["count"] == 0 and stone["below"] is True


def test_hungry_pals_are_counted_not_diagnosed():
    report = basesupply.base_report(
        _summary([{"containerId": "c1", "kind": "PalFoodBox", "kindName": "Feed Box",
                   "usedSlots": 1, "totalSlots": 10}]),
        {"c1": _slots([("Berries", 500)])},
        staples=("Wood",), floor=1, hungry=7, pal_count=20,
    )
    note = next(n for n in report["notes"] if n["kind"] == "hungryPals")
    assert "7" in note["text"] and "20" in note["text"]
    # No note anywhere claims a causal link between the box and the hunger.
    assert not any("move" in n["text"].lower() for n in report["notes"])


# ─── Staple list handling ────────────────────────────────


def test_default_staples_all_resolve_in_the_catalogue():
    """
    The list is written as ids because the display names are traps: the item
    players call "Ore" is `CopperOre` and "Paldium Fragment" is `Pal_crystal_S`.
    If one stops resolving, the report silently shows a real material as zero.
    """
    import gamedata

    unknown = [i for i in basesupply.DEFAULT_STAPLES if not gamedata.item(i)]
    assert unknown == [], f"staples not in the catalogue: {unknown}"


def test_custom_material_list_overrides_and_keeps_unknown_ids():
    assert basesupply.parse_materials("Wood, Stone") == ("Wood", "Stone")
    # A modded id is reported as zero rather than dropped, so an operator can see
    # their own list answered in full.
    assert basesupply.parse_materials("ModdedThing") == ("ModdedThing",)
    assert basesupply.parse_materials("") == basesupply.DEFAULT_STAPLES
    assert basesupply.parse_materials(None) == basesupply.DEFAULT_STAPLES


def test_guild_report_is_one_chest_not_one_per_base():
    guild = {"id": "guild-1", "name": "Greed"}
    report = basesupply.guild_report(
        guild, "gc1",
        {"gc1": _slots([("Wood", 30000), ("Stone", 100)]) + [{"isEmpty": True}]},
        staples=("Wood", "Cloth"),
    )
    assert report["itemCount"] == 30100
    assert report["usedSlots"] == 2 and report["totalSlots"] == 3
    assert report["staples"][0]["count"] == 30000
    assert report["staples"][1]["count"] == 0
