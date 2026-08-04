"""
Durability records.

The finding this file mostly exists to pin: **a local id does not identify one
record.** On the reference world 32,446 records carry only 2,052 distinct ids —
2,022 of them appearing exactly 16 times, byte-for-byte identical.

The first version of the module assumed one id, one record. Its own smoke test
caught the result immediately: the plan read one copy, the apply looked the id up
again and mutated a *different* copy, and the durability appeared not to change.
Editing 1 of 16 is worse than that visible failure, because the game may read any
of them.

The second finding is why creation is refused: nothing explains the 16, so
appending one record where the game expects sixteen would leave a
half-registered item. Repair is safe because every copy is written together;
creation is not, and `can_create()` says so rather than guessing.
"""

from __future__ import annotations

import pytest

import dynamicitem as D


def _record(kind: str, local_id: str, static_id: str = "Bat",
            durability: float = 50.0, bullets: int = 0) -> dict:
    raw: dict = {
        "type": kind,
        "id": {
            "created_world_id": "00000000-0000-0000-0000-000000000000",
            "local_id_in_created_world": local_id,
            "static_id": static_id,
        },
        "leading_bytes": b"\x00\x00\x00\x00",
        "trailing_bytes": b"\x00\x00\x00\x00",
    }
    if kind in ("weapon", "armor"):
        raw["durability"] = durability
    if kind == "weapon":
        raw["remaining_bullets"] = bullets
        raw["passive_skill_list"] = []
    if kind == "egg":
        raw["character_id"] = "FlowerDinosaur_Electric"
        raw["object"] = {"SaveParameter": {"value": {}}}
    return {"RawData": {"value": raw}, "CustomVersionData": {"value": {"values": b""}}}


def _world(records: list[dict]) -> dict:
    return {"DynamicItemSaveData": {"value": {"values": records}}}


WEAPON = "1a2a152d-66a2-40dc-a1bf-4ce5541d1c74"
ARMOR = "ea4ba81e-04d9-4f1b-bfb1-a3c69e1ccc2f"
EGG = "20767bcf-7b40-4d87-a059-38d6e6f08356"


# ─── The duplicate-record rule ────────────────────────────


def test_an_id_maps_to_every_copy_not_the_first():
    world = _world([_record("weapon", WEAPON) for _ in range(16)])
    found = D.index_by_local_id(world)[WEAPON]
    assert len(found) == 16


def test_a_repair_writes_every_copy():
    """
    The bug this file was written for. Sixteen identical records, one edit — all
    sixteen must move, or the game can read a stale one.
    """
    world = _world([_record("weapon", WEAPON, durability=10.0) for _ in range(16)])
    plan = D.plan_durability(world, WEAPON, durability=250.0)
    assert plan["copies"] == 16
    D.apply_durability(world, plan)

    values = {D._raw(r)["durability"] for r in D._records(world)}
    assert values == {250.0}, "a copy was left behind"


def test_a_repair_does_not_change_the_record_count():
    world = _world([_record("weapon", WEAPON) for _ in range(16)])
    D.apply_durability(world, D.plan_durability(world, WEAPON, durability=1.0))
    assert D.count(world) == 16


def test_a_changed_copy_count_between_plan_and_apply_is_refused():
    """
    The world moving under an approved plan. Same guarantee `planHash` gives the
    slot editor, expressed in the units this module actually knows about.
    """
    world = _world([_record("weapon", WEAPON) for _ in range(16)])
    plan = D.plan_durability(world, WEAPON, durability=99.0)
    D._records(world).pop()
    with pytest.raises(D.DynamicItemError, match="copies"):
        D.apply_durability(world, plan)


def test_only_the_named_item_moves():
    world = _world(
        [_record("weapon", WEAPON, durability=10.0) for _ in range(3)]
        + [_record("armor", ARMOR, durability=20.0) for _ in range(3)]
    )
    D.apply_durability(world, D.plan_durability(world, WEAPON, durability=77.0))
    others = {D._raw(r)["durability"] for r in D._records(world)
              if D._local_id(r) == ARMOR}
    assert others == {20.0}


# ─── What is editable ─────────────────────────────────────


def test_weapons_and_armour_are_editable():
    world = _world([_record("weapon", WEAPON), _record("armor", ARMOR)])
    assert D.plan_durability(world, WEAPON, durability=5.0)["changed"]
    assert D.plan_durability(world, ARMOR, durability=5.0)["changed"]


def test_an_egg_is_refused_with_its_reason():
    """
    An egg's record embeds a whole Pal, so this is not durability editing with a
    different field name — creating or altering one is a character edit.
    """
    world = _world([_record("egg", EGG)])
    assert D.describe(D._records(world)[0])["editable"] is False
    with pytest.raises(D.DynamicItemError, match="Pal"):
        D.plan_durability(world, EGG, durability=5.0)


def test_an_egg_never_leaks_its_embedded_pal():
    """`describe` is what reaches the API; the character record must not."""
    world = _world([_record("egg", EGG)])
    described = D.describe(D._records(world)[0])
    assert "object" not in described
    assert described["characterId"] == "FlowerDinosaur_Electric"


def test_bullets_are_weapon_only():
    world = _world([_record("armor", ARMOR)])
    with pytest.raises(D.DynamicItemError, match="weapon"):
        D.plan_durability(world, ARMOR, remaining_bullets=5)


@pytest.mark.parametrize("value", [-1.0, D.MAX_DURABILITY + 1])
def test_absurd_durability_is_refused_before_the_save(value):
    world = _world([_record("weapon", WEAPON)])
    with pytest.raises(D.DynamicItemError, match="between"):
        D.plan_durability(world, WEAPON, durability=value)


def test_a_missing_record_is_refused():
    with pytest.raises(D.DynamicItemError, match="No durability record"):
        D.plan_durability(_world([]), WEAPON, durability=1.0)


# ─── Creation moved out; this module only edits ───────────


def test_creation_is_supported_and_points_at_the_module_that_does_it():
    """
    This asserted a refusal for months, and the refusal was right on the evidence
    then available: `refworld` maps a local id to sixteen identical records, and
    appending one where the game wants sixteen leaves a half-registered item.

    The evidence was a property of that one file. Nine snapshots of the same
    world's own server backups are one-record-per-id, and 2,262 creations were
    later observed directly, each a single record. Creation now lives in
    `itemclone`, which is where the tests for it live too.
    """
    allowed, reason = D.can_create()
    assert allowed is True
    assert "itemclone" in reason


# ─── Against the real world ───────────────────────────────


@pytest.mark.integration
@pytest.mark.slow
def test_refworld_is_the_duplicated_outlier_it_is_documented_as(level_sav):
    """
    Pins `refworld`'s distribution — as a fact about THAT FILE, which is all it
    ever was.

    This used to be described as "the measurement every claim in this module
    rests on", and that framing is what made a property of one file read as a
    property of the save format. It is not: `refworld` is a processed copy of the
    live world, and nine snapshots of that world's own server backups are
    one-record-per-id throughout.

    The test stays, because `index_by_local_id` returning a list and
    `apply_durability` writing every copy are only exercised by a world that HAS
    duplicates — this is the only one available. It is a fixture check now, not
    evidence about Palworld.
    """
    import collections

    from palsav.core import decompress_sav_to_gvas
    from palsav.gvas import GvasFile
    from palsav.paltypes import PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES
    from savefiles import read_sav_bytes

    raw, _ = decompress_sav_to_gvas(read_sav_bytes(level_sav))
    gvas = GvasFile.read(raw, PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES)
    world = gvas.properties["worldSaveData"]["value"]

    records = D._records(world)
    assert len(records) == 32_446

    types = collections.Counter(D._raw(r).get("type") for r in records)
    assert dict(types) == {"weapon": 814, "armor": 766, "egg": 30_866}

    index = D.index_by_local_id(world)
    assert len(index) == 2_052
    spread = collections.Counter(len(v) for v in index.values())
    assert spread[16] == 2_022, "refworld's duplication — an artifact, not the format"

    # Copies of one id are identical, which is why writing all of them keeps the
    # save consistent rather than merely being thorough.
    for copies in list(index.values())[:200]:
        durabilities = {D._raw(r).get("durability") for r in copies}
        assert len(durabilities) == 1
