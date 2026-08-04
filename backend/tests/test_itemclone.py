"""
Creating equipment and eggs.

This is the only code in the project that ADDS a `DynamicItemSaveData` record, so
the tests are about shape rather than values: a new item is two things — a record
and a container slot pointing at it — and every interesting failure is having
written one of them.

The refusals matter as much as the writes here. `can_create()` refused entirely
until the copy count was measured (see AGENTS.md), and the refusals that remain
are each guarding against a specific plausible-looking wrong result.
"""

from __future__ import annotations

import os
import shutil
import time

import pytest

import itemclone


# ─── Fixtures ────────────────────────────────────────────


@pytest.fixture
def sandbox(refworld, tmp_path, monkeypatch):
    """A disposable world the backend will write to, believing the server is down."""
    import backup as backup_module
    import safety
    import savefiles

    base = tmp_path / "SaveGames" / "0"
    world = base / "0123456789ABCDEF0123456789ABCDEF"
    shutil.copytree(refworld, world)

    old = time.time() - 7200
    for dirpath, _dirs, files in os.walk(world):
        for name in files:
            os.utime(os.path.join(dirpath, name), (old, old))
    os.utime(world, (old, old))

    backups = tmp_path / "backups"
    backups.mkdir()

    monkeypatch.setattr(savefiles, "SAVE_BASE_DIR", str(base))
    monkeypatch.setattr(savefiles, "BACKUP_DIR", str(backups))
    monkeypatch.setattr(backup_module, "BACKUP_DIR", str(backups))
    monkeypatch.setattr(safety, "SAVE_BASE_DIR", str(base))
    monkeypatch.setattr(safety, "SAVE_READ_ONLY", False)
    monkeypatch.setattr(safety, "ALLOW_UNVERIFIED_EDITS", False)
    monkeypatch.setattr(
        safety, "_probe_rest_api", lambda: safety.Signal("rest_api", "stopped", "test")
    )
    monkeypatch.setattr(
        safety, "_probe_tcp", lambda: safety.Signal("tcp_port", "stopped", "test")
    )
    return str(world)


def _load(world_dir):
    from palsav.core import decompress_sav_to_gvas
    from palsav.gvas import GvasFile
    from palsav.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS
    from parser import _custom_properties

    props = {**PALWORLD_CUSTOM_PROPERTIES, **_custom_properties(include_items=True)}
    with open(os.path.join(world_dir, "Level.sav"), "rb") as f:
        raw = f.read()
    return GvasFile.read(decompress_sav_to_gvas(raw)[0], PALWORLD_TYPE_HINTS, props)


def _a_container_with_space(gvas):
    """A container id and a free slot index in it."""
    import saveedit

    for entry in itemclone._containers(gvas):
        capacity = itemclone._slot_num(entry)
        used = {
            itemclone._slot_index(saveedit._slot_raw(s))
            for s in itemclone._slots(entry)
            if saveedit._slot_raw(s) is not None
        }
        if capacity and itemclone._slots(entry):
            free = next((i for i in range(capacity) if i not in used), None)
            if free is not None:
                return itemclone._container_id(entry), free
    return None, None


# ─── Catalogue translation ───────────────────────────────


def test_an_egg_is_unknown_in_the_catalogue_and_egg_in_the_save():
    """
    The two sources disagree and both are internally consistent — `gamedata` says
    `dynamic.type == "unknown"` for all 56 PalEgg items, the save's record says
    `"egg"`. Reading either one raw is how a valid request gets refused.
    """
    kind, entry = itemclone._item_kind("PalEgg_Fire_01")
    assert kind == "egg"
    assert entry.get("name")


def test_weapons_and_armour_map_straight_through():
    assert itemclone._item_kind("Katana")[0] == "weapon"


def test_an_item_with_no_durability_record_is_sent_elsewhere():
    """
    Wood does not need this path, and saying so beats a generic refusal — the
    slot editor handles plain items and is the safer of the two writers.
    """
    assert itemclone._item_kind("Wood")[0] == ""


# ─── Planning refusals ───────────────────────────────────


@pytest.mark.integration
def test_planning_refuses_an_unknown_item(refworld, palsav_available):
    gvas = _load(refworld)
    plan = itemclone.plan_item_create(gvas, "x", 0, "NotAnItemAtAll")
    assert plan["ok"] is False
    assert "catalogue" in plan["problems"][0]


@pytest.mark.integration
def test_planning_refuses_a_plain_item(refworld, palsav_available):
    gvas = _load(refworld)
    container_id, slot = _a_container_with_space(gvas)
    plan = itemclone.plan_item_create(gvas, container_id, slot, "Wood")
    assert plan["ok"] is False
    assert "slot editor" in plan["problems"][0]


@pytest.mark.integration
def test_planning_refuses_an_occupied_slot(refworld, palsav_available):
    """Overwriting would orphan whatever record the slot already points at."""
    import saveedit

    gvas = _load(refworld)
    for entry in itemclone._containers(gvas):
        for slot in itemclone._slots(entry):
            raw = saveedit._slot_raw(slot)
            if raw is not None and not saveedit._is_empty(raw):
                plan = itemclone.plan_item_create(
                    gvas,
                    itemclone._container_id(entry),
                    int(raw.get("slot_index", 0) or 0),
                    "Katana",
                )
                assert plan["ok"] is False
                assert "already holds" in plan["problems"][0]
                return
    pytest.skip("no occupied slot found")


@pytest.mark.integration
def test_planning_refuses_a_slot_beyond_capacity(refworld, palsav_available):
    gvas = _load(refworld)
    container_id, _ = _a_container_with_space(gvas)
    plan = itemclone.plan_item_create(gvas, container_id, 99_999, "Katana")
    assert plan["ok"] is False
    assert "outside this container" in plan["problems"][0]


@pytest.mark.integration
def test_equipment_does_not_stack(refworld, palsav_available):
    """
    Each one is individually tracked, so "five swords" is five records in five
    slots. Refusing beats silently creating one of the five.
    """
    gvas = _load(refworld)
    container_id, slot = _a_container_with_space(gvas)
    plan = itemclone.plan_item_create(gvas, container_id, slot, "Katana", count=5)
    assert plan["ok"] is False
    assert "do not stack" in plan["problems"][0]


# ─── The egg asymmetry ───────────────────────────────────


@pytest.mark.integration
def test_an_egg_needs_a_template_of_the_same_egg(refworld, palsav_available):
    """
    THE REFUSAL THAT PREVENTS A PLAUSIBLE WRONG RESULT.

    What an egg hatches lives in the record's `character_id`, and the catalogue
    does not know it. Cloning any old egg to satisfy a request would produce a
    fire egg that hatches a dark Pal — wrong in a way nobody notices until it
    hatches. Equipment has no such field, so it may fall back.
    """
    gvas = _load(refworld)
    by_item = itemclone._records_by_item(gvas)
    eggs = {v for v in by_item.values() if v.startswith("palegg")}
    assert eggs, "the reference world should hold some eggs"

    # The invariant, asserted directly rather than via a refusal: every template
    # an egg request resolves to is referenced by a slot holding THAT SAME egg.
    # A cross-item fallback would show up here as a mismatch, and it is the
    # mismatch — not the refusal — that would ship the wrong hatch.
    for egg_id in sorted(eggs):
        template = itemclone._find_template(gvas, "egg", egg_id)
        if template is None:
            continue
        import dynamicitem

        assert by_item[dynamicitem._local_id(template)] == egg_id, (
            f"template for {egg_id} came from {by_item[dynamicitem._local_id(template)]}"
        )


@pytest.mark.integration
def test_an_egg_this_world_does_not_hold_is_refused(refworld, palsav_available):
    """
    The refusal itself, on a world that holds all 56 eggs — so the id is one the
    catalogue knows and no slot references, which is exactly the state a server
    missing an egg type is in.
    """
    gvas = _load(refworld)
    present = set(itemclone._records_by_item(gvas).values())
    egg = next(
        e for e in ("PalEgg_Dark_01", "PalEgg_Fire_01") if e.lower() in present
    )

    # Hide every record for that egg, leaving the item valid but untemplated.
    hidden = {k: v for k, v in itemclone._records_by_item(gvas).items()
              if v != egg.lower()}
    original = itemclone._records_by_item
    itemclone._records_by_item = lambda _g: hidden
    try:
        container_id, slot = _a_container_with_space(gvas)
        plan = itemclone.plan_item_create(gvas, container_id, slot, egg)
        assert plan["ok"] is False
        assert "hatches" in plan["problems"][0]
    finally:
        itemclone._records_by_item = original


@pytest.mark.integration
def test_an_egg_with_a_pal_inside_is_never_a_template(refworld, palsav_available):
    """
    172 of 180 eggs have an empty interior; the 8 that do not embed a whole Pal.
    Copying one would duplicate that character wholesale, which is `palclone`'s
    job and not something to do while adding an item to a chest.
    """
    import dynamicitem

    gvas = _load(refworld)
    for static_id in {v for v in itemclone._records_by_item(gvas).values()
                      if v.startswith("palegg")}:
        template = itemclone._find_template(gvas, "egg", static_id)
        if template is not None:
            assert not (dynamicitem._raw(template).get("object") or {})


# ─── Writing ─────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.slow
def test_creating_a_weapon_writes_both_halves(sandbox, palsav_available, stopped_server):
    """
    The whole point. A record without a slot is unreachable; a slot without a
    record is an item the game cannot resolve. Both, or neither.
    """
    import dynamicitem
    import saveedit

    before = _load(sandbox)
    container_id, slot = _a_container_with_space(before)
    assert container_id, "no container with a free slot in the reference world"
    records_before = len(itemclone._dynamic_records(before))

    plan = itemclone.plan_item_create(before, container_id, slot, "Katana")
    assert plan["ok"], plan["problems"]

    result = itemclone.apply_item_create(
        container_id, slot, "Katana", expected_plan_hash=plan["planHash"]
    )
    assert result["ok"] is True

    after = _load(sandbox)
    # Exactly one record added — the measured count, and the thing this whole
    # feature was blocked on.
    assert len(itemclone._dynamic_records(after)) == records_before + 1

    index = dynamicitem.index_by_local_id(after.properties["worldSaveData"]["value"])
    assert len(index[result["localId"].lower()]) == 1

    entry = next(
        c for c in itemclone._containers(after)
        if itemclone._container_id(c) == container_id
    )
    raw = next(
        saveedit._slot_raw(s) for s in itemclone._slots(entry)
        if saveedit._slot_raw(s) is not None
        and itemclone._slot_index(saveedit._slot_raw(s)) == slot
    )
    assert saveedit._static_id(raw) == "Katana"
    assert str(
        (raw["item"]["dynamic_id"])["local_id_in_created_world"]
    ).lower() == result["localId"].lower()


@pytest.mark.integration
@pytest.mark.slow
def test_a_created_weapon_carries_no_inherited_passives(
    sandbox, palsav_available, stopped_server
):
    """
    The template is somebody's existing weapon. Its passives would otherwise
    ride along as a silent gift of whatever happened to be copied.
    """
    import dynamicitem

    gvas = _load(sandbox)
    container_id, slot = _a_container_with_space(gvas)
    plan = itemclone.plan_item_create(gvas, container_id, slot, "Katana")
    result = itemclone.apply_item_create(
        container_id, slot, "Katana", expected_plan_hash=plan["planHash"]
    )

    after = _load(sandbox)
    index = dynamicitem.index_by_local_id(after.properties["worldSaveData"]["value"])
    raw = dynamicitem._raw(index[result["localId"].lower()][0])
    assert raw.get("passive_skill_list") == []
    assert raw.get("remaining_bullets") == 0


@pytest.mark.integration
@pytest.mark.slow
def test_durability_defaults_to_factory_fresh(sandbox, palsav_available, stopped_server):
    import dynamicitem

    gvas = _load(sandbox)
    container_id, slot = _a_container_with_space(gvas)
    expected = dynamicitem._max_durability("Katana")
    assert expected > 0, "the bundled data should know a Katana's durability"

    plan = itemclone.plan_item_create(gvas, container_id, slot, "Katana")
    assert plan["durability"] == expected
    result = itemclone.apply_item_create(
        container_id, slot, "Katana", expected_plan_hash=plan["planHash"]
    )
    assert result["durability"] == expected


@pytest.mark.integration
@pytest.mark.slow
def test_a_stale_plan_is_refused_and_nothing_is_written(
    sandbox, palsav_available, stopped_server
):
    gvas = _load(sandbox)
    container_id, slot = _a_container_with_space(gvas)
    level = os.path.join(sandbox, "Level.sav")
    stamp = os.path.getsize(level), os.path.getmtime(level)

    with pytest.raises(itemclone.ItemCloneError, match="changed since"):
        itemclone.apply_item_create(
            container_id, slot, "Katana", expected_plan_hash="not-the-hash"
        )

    assert (os.path.getsize(level), os.path.getmtime(level)) == stamp


@pytest.mark.integration
@pytest.mark.slow
def test_creation_is_refused_while_the_server_is_up(sandbox, palsav_available, monkeypatch):
    """The rule that matters most — this one is about a world that cannot be recovered."""
    import safety

    monkeypatch.setattr(
        safety, "_probe_rest_api",
        lambda: safety.Signal("rest_api", "running", "test says up"),
    )
    gvas = _load(sandbox)
    container_id, slot = _a_container_with_space(gvas)
    level = os.path.join(sandbox, "Level.sav")
    stamp = os.path.getsize(level), os.path.getmtime(level)

    with pytest.raises(Exception):
        itemclone.apply_item_create(container_id, slot, "Katana")

    assert (os.path.getsize(level), os.path.getmtime(level)) == stamp
