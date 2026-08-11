"""
Writing into an EMPTY chest slot, which used to be impossible.

Reported as "I can see the empty spots but I can't write to them". The save
stores only occupied slots; `extract_containers` pads the read side up to
`SlotNum` because the slots genuinely exist, and the planner plans a change for
a padded index — but the apply loop walked the raw `Slots` array looking for an
entry that was never written, found nothing, and refused the whole import.

The fix appends, under `palclone`'s rule: deep-copy an entry the save already
has, never construct one. These tests exercise that against a real world,
because this is a WRITE path and the unit suite cannot see the encoder.
"""

from __future__ import annotations

import os
import shutil
import time

import pytest

import saveimport
import slotedit


def _load(world_dir):
    from palsav.core import decompress_sav_to_gvas
    from palsav.gvas import GvasFile
    from palsav.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS
    from parser import _custom_properties

    props = {**PALWORLD_CUSTOM_PROPERTIES, **_custom_properties(include_items=True)}
    with open(os.path.join(world_dir, "Level.sav"), "rb") as f:
        raw = f.read()
    return GvasFile.read(decompress_sav_to_gvas(raw)[0], PALWORLD_TYPE_HINTS, props)


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
    return str(world)


def _container_with_a_free_slot(gvas):
    """`(container_id, padded_slots, free_index)` — a real chest with room."""
    import parser as P

    containers = P.extract_containers(gvas)
    for container_id, slots in containers.items():
        free = next((s["slotIndex"] for s in slots if s.get("isEmpty")), None)
        occupied = any(not s.get("isEmpty") for s in slots)
        # Needs an occupied slot too: that is the shape the append copies.
        if free is not None and occupied:
            return container_id, slots, free
    return None, None, None


@pytest.mark.integration
@pytest.mark.slow
def test_an_empty_slot_can_be_written_and_the_array_grows(
    sandbox, palsav_available, stopped_server
):
    """
    **The reported bug.** The container's raw array has no entry for a padded
    index, so this used to fail the completeness check and refuse.

    Asserts the array actually GREW — the point is that an entry was appended,
    not that a value changed somewhere.
    """
    import parser as P

    before = _load(sandbox)
    container_id, slots, free = _container_with_a_free_slot(before)
    assert container_id, "no container with both a free slot and an occupied one"

    raw_before = len(P.extract_containers(before)[container_id])
    stored_before = sum(
        1 for s in P.extract_containers(before)[container_id] if not s["isEmpty"]
    )

    document = slotedit.build_document(
        container_id, [{"slotIndex": free, "itemId": "Wood", "stackCount": 7}], slots
    )
    plan = saveimport.plan_container_import(document, slots)
    assert plan["ok"], plan["problems"]

    result = saveimport.apply_container_import(
        document, expected_plan_hash=plan["planHash"]
    )
    assert result["ok"] is True

    after = P.extract_containers(_load(sandbox))[container_id]
    written = next(s for s in after if s["slotIndex"] == free)
    assert written["itemId"] == "Wood"
    assert written["stackCount"] == 7
    assert written["isEmpty"] is False

    # Padding means the padded length is unchanged; what grew is the number of
    # slots the save actually stores.
    assert len(after) == raw_before
    assert sum(1 for s in after if not s["isEmpty"]) == stored_before + 1


@pytest.mark.integration
@pytest.mark.slow
def test_clearing_a_slot_the_save_never_wrote_is_a_no_op_not_a_refusal(
    sandbox, palsav_available, stopped_server
):
    """
    Asking for empty on a slot that is already absent is already true. It used
    to fall foul of the same completeness check, which is a refusal for a
    request the world already satisfies.
    """
    import parser as P

    before = _load(sandbox)
    container_id, slots, free = _container_with_a_free_slot(before)
    assert container_id

    document = slotedit.build_document(
        container_id, [{"slotIndex": free, "itemId": "", "stackCount": 0}], slots
    )
    plan = saveimport.plan_container_import(document, slots)
    # Nothing to do: before and after are both empty, so the planner finds no
    # change at all. That is the correct answer and must not be an error.
    assert plan["ok"]
    assert plan["changes"] == []


@pytest.mark.integration
@pytest.mark.slow
def test_no_other_container_is_touched_by_an_append(
    sandbox, palsav_available, stopped_server
):
    """
    The scope guarantee is what stands in for conservation on an import, and an
    appended entry must not weaken it: the target matches the plan, everything
    else is byte-for-byte the same shape it was.
    """
    import parser as P

    before_all = P.extract_containers(_load(sandbox))
    container_id, slots, free = _container_with_a_free_slot(_load(sandbox))
    assert container_id

    document = slotedit.build_document(
        container_id, [{"slotIndex": free, "itemId": "Stone", "stackCount": 3}], slots
    )
    plan = saveimport.plan_container_import(document, slots)
    saveimport.apply_container_import(document, expected_plan_hash=plan["planHash"])

    after_all = P.extract_containers(_load(sandbox))
    assert set(after_all) == set(before_all), "a container appeared or vanished"
    for cid, rows in after_all.items():
        if cid == container_id:
            continue
        assert rows == before_all[cid], f"container {cid} changed and should not have"
