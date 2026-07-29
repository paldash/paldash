"""
End-to-end tests against a real Palworld 1.0 world.

These need `refworld/` and skip without it. They are the only tests that prove
the parser handles the actual 1.0 format, and the only ones that exercise the
full mutation pipeline: guard -> backup -> mutate -> verify -> write -> re-read.

Every test works on a *copy*. Nothing here ever touches the original.
"""

from __future__ import annotations

import os
import shutil
import time

import pytest

pytestmark = pytest.mark.integration


# ─── Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def sandbox(refworld, tmp_path, monkeypatch):
    """
    A disposable copy of the world, wired up so the backend operates on it and
    believes the server is stopped.
    """
    import backup as backup_module
    import safety
    import savefiles

    base = tmp_path / "SaveGames" / "0"
    world = base / "0123456789ABCDEF0123456789ABCDEF"
    shutil.copytree(refworld, world)

    # Age every file. The activity probe reads real mtimes, and freshly copied
    # files look exactly like a server that just autosaved.
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

    # No live server to probe in a test environment.
    monkeypatch.setattr(
        safety, "_probe_rest_api", lambda: safety.Signal("rest_api", "stopped", "test")
    )
    monkeypatch.setattr(
        safety, "_probe_tcp", lambda: safety.Signal("tcp_port", "stopped", "test")
    )
    # _probe_save_activity is deliberately left real — it reads the aged mtimes.

    return {"world": str(world), "base": str(base), "backups": str(backups)}


# ─── Format handling ─────────────────────────────────────────────


def test_level_sav_is_palworld_1_0_oodle(level_sav):
    """1.0 changed the magic bytes from PlZ (zlib) to PlM (Oodle)."""
    with open(level_sav, "rb") as f:
        header = f.read(16)
    assert b"PlM" in header or b"PlZ" in header
    if b"PlZ" in header:
        pytest.skip("reference world predates the 1.0 Oodle format")


def test_parses_a_real_world(palsav_available, level_sav):
    from parser import load_gvas

    gvas = load_gvas(level_sav)
    assert gvas is not None, "1.0 save failed to parse"
    world = gvas.properties["worldSaveData"]["value"]
    assert world["CharacterSaveParameterMap"]["value"]
    assert world["MapObjectSaveData"]["value"]["values"]


def test_decompress_recompress_is_byte_identical(palsav_available, level_sav):
    """
    Round-tripping the container must reproduce the file exactly. If this drifts,
    every write is silently rewriting bytes the game did not ask us to change.
    """
    from palsav.core import compress_gvas_to_sav, decompress_sav_to_gvas

    original = open(level_sav, "rb").read()
    raw, save_type = decompress_sav_to_gvas(original)
    assert compress_gvas_to_sav(raw, save_type) == original


def test_gvas_read_write_is_byte_identical(palsav_available, level_sav):
    """Parsing to a tree and serialising it back must not perturb anything."""
    from palsav.core import decompress_sav_to_gvas
    from palsav.gvas import GvasFile
    from palsav.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS

    raw, _ = decompress_sav_to_gvas(open(level_sav, "rb").read())
    gvas = GvasFile.read(raw, PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES)
    assert gvas.write(PALWORLD_CUSTOM_PROPERTIES) == raw


# ─── Extraction ──────────────────────────────────────────────────


def test_extracts_bases_guilds_and_characters(palsav_available, level_sav):
    from parser import extract_base_camps, extract_characters, extract_guilds, load_gvas

    gvas = load_gvas(level_sav)
    guilds = extract_guilds(gvas)
    bases = extract_base_camps(gvas)
    players, pals = extract_characters(gvas)

    assert guilds, "no guilds extracted"
    assert bases, "no base camps extracted"
    assert players, "no players extracted"
    assert pals, "no pals extracted"

    for base in bases:
        assert isinstance(base.get("x"), (int, float))
        assert isinstance(base.get("y"), (int, float))


def test_pal_levels_are_plausible(palsav_available, level_sav):
    """
    Guards the ByteProperty regression: when Level was read as an IntProperty
    every Pal silently came back level 0.
    """
    from parser import extract_characters, load_gvas

    _players, pals = extract_characters(load_gvas(level_sav))
    levels = [p.get("level", 0) for p in pals]
    assert max(levels) > 1, "all Pal levels are 0 — ByteProperty nesting regressed"
    assert all(0 <= lvl <= 100 for lvl in levels)


def test_map_objects_split_into_world_and_base_placed(palsav_available, level_sav):
    from parser import extract_map_objects, load_gvas

    objects = extract_map_objects(load_gvas(level_sav))
    assert objects
    assert all("x" in o and "y" in o for o in objects)


def test_item_containers_decode_to_real_counts(palsav_available, level_sav):
    """
    Without the Slots.Slots.RawData decoder every container reads as empty and
    the item totals silently come back as zero.
    """
    from parser import extract_containers, load_gvas

    gvas = load_gvas(level_sav, include_items=True)
    containers = extract_containers(gvas)
    assert containers

    occupied = [
        slot
        for slots in containers.values()
        for slot in slots
        if not slot["isEmpty"]
    ]
    assert occupied, "all containers decoded as empty"
    assert sum(s["stackCount"] for s in occupied) > 0
    assert all(s["itemId"] for s in occupied), "occupied slot with no item id"


# ─── The full mutation pipeline ──────────────────────────────────


@pytest.mark.slow
def test_sort_conserves_every_item_end_to_end(palsav_available, sandbox):
    """
    The headline safety property, exercised for real: parse a live world, sort
    every container, write it, re-read from disk, and prove not one item moved
    in or out.
    """
    import saveedit
    from palsav.core import decompress_sav_to_gvas
    from palsav.gvas import GvasFile
    from palsav.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS

    level = os.path.join(sandbox["world"], "Level.sav")

    def totals_on_disk():
        raw, _ = decompress_sav_to_gvas(open(level, "rb").read())
        gvas = GvasFile.read(raw, PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES)
        containers = gvas.properties["worldSaveData"]["value"]["ItemContainerSaveData"]["value"]
        return saveedit._totals(containers)

    before = totals_on_disk()
    assert before, "no containers in the reference world"

    result = saveedit.sort_containers(mode="stackables", merge=True)

    assert result["ok"] is True
    assert result["verified"] is True
    assert result["slotsChanged"] > 0
    assert result["backupId"]

    after = totals_on_disk()
    saveedit._assert_conserved(before, after, "end-to-end")

    grand_before = sum(sum(c.values()) for c in before.values())
    grand_after = sum(sum(c.values()) for c in after.values())
    assert grand_before == grand_after


def test_container_ownership_resolves_against_real_bases(palsav_available, level_sav):
    """
    The base<-object->container join, against the world it was derived from.

    Every base id an object claims must be a base that exists. If this drifts,
    per-base inventory silently files chests under bases nobody has.
    """
    from parser import (
        extract_base_camps, extract_container_ownership, extract_containers, load_gvas,
    )

    gvas = load_gvas(level_sav, include_items=True)
    bases = extract_base_camps(gvas)
    ownership = extract_container_ownership(gvas)
    containers = extract_containers(gvas)

    assert ownership, "no container ownership extracted"

    base_ids = {b["id"] for b in bases}
    claimed = {o["baseCampId"] for o in ownership.values() if o["baseCampId"]}
    assert claimed, "no container attributed to any base"
    assert claimed <= base_ids, f"containers claim unknown bases: {claimed - base_ids}"

    # The relationship is one container to one object; two objects pointing at
    # the same storage would double-count every item in it.
    assert len(ownership) == len({o["objectId"] for o in ownership.values()})

    # Nearly every referenced container should really exist. A handful dangle on
    # a live world (the object outlived its storage), but not many.
    dangling = [cid for cid in ownership if cid not in containers]
    assert len(dangling) < 0.01 * len(ownership), (
        f"{len(dangling)} of {len(ownership)} container references dangle"
    )


def test_base_storage_totals_stay_within_the_world_total(palsav_available, level_sav):
    """Per-base sums are a partition of a subset — they cannot exceed the whole."""
    from parser import (
        extract_base_camps, extract_container_ownership, extract_containers,
        extract_guilds, guild_name_map, load_gvas, summarise_base_storage,
    )

    gvas = load_gvas(level_sav, include_items=True)
    containers = extract_containers(gvas)
    bases = extract_base_camps(gvas, guild_name_map(extract_guilds(gvas)))
    summaries = summarise_base_storage(containers, extract_container_ownership(gvas), bases)

    assert len(summaries) == len(bases), "every base must get a row, even an empty one"

    world_total = sum(
        s["stackCount"] for slots in containers.values() for s in slots if not s["isEmpty"]
    )
    assert sum(s["itemCount"] for s in summaries) <= world_total

    for summary in summaries:
        assert summary["usedSlots"] <= summary["totalSlots"]
        assert sum(i["count"] for i in summary["items"]) == summary["itemCount"]


@pytest.mark.slow
def test_base_scoped_sort_leaves_other_bases_untouched(palsav_available, sandbox):
    """
    The point of scoping: one guild tidies its own base without reorganising
    everyone else's chests. Slot-level equality outside the scope is the check —
    conservation alone would pass even if every other container were reshuffled.
    """
    import saveedit
    from parser import extract_container_ownership, load_gvas

    level = os.path.join(sandbox["world"], "Level.sav")

    gvas = load_gvas(level, include_items=True)
    ownership = extract_container_ownership(gvas)
    per_base: dict[str, set] = {}
    for cid, owner in ownership.items():
        if owner["baseCampId"]:
            per_base.setdefault(owner["baseCampId"], set()).add(cid)

    target = max(per_base, key=lambda b: len(per_base[b]))
    in_scope = per_base[target]

    def slot_snapshot():
        from palsav.core import decompress_sav_to_gvas
        from palsav.gvas import GvasFile
        from palsav.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS

        raw, _ = decompress_sav_to_gvas(open(level, "rb").read())
        tree = GvasFile.read(raw, PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES)
        entries = tree.properties["worldSaveData"]["value"]["ItemContainerSaveData"]["value"]
        snapshot = {}
        for entry in entries:
            cid = saveedit._container_id_of(entry)
            slots = ((entry.get("value") or {}).get("Slots") or {}).get("value", {}).get("values", [])
            snapshot[cid] = [
                (saveedit._static_id(r), saveedit._count(r))
                for r in (saveedit._slot_raw(s) for s in slots)
                if r is not None
            ]
        return snapshot

    before = slot_snapshot()
    result = saveedit.sort_containers(mode="stackables", merge=True, base_id=target)

    assert result["scope"] == "base"
    assert result["baseId"] == target
    assert result["containersInScope"] == len(in_scope)
    assert result["verified"] is True

    after = slot_snapshot()
    changed = {cid for cid in before if before[cid] != after.get(cid)}

    assert changed, "the scoped sort changed nothing at all"
    assert changed <= in_scope, (
        f"sort escaped its scope and modified {len(changed - in_scope)} containers "
        "belonging to other bases or the world"
    )


@pytest.mark.slow
def test_sorting_an_unknown_base_writes_nothing(palsav_available, sandbox):
    import saveedit
    from backup import list_backups

    level = os.path.join(sandbox["world"], "Level.sav")
    original = open(level, "rb").read()

    with pytest.raises(saveedit.SaveEditError, match="owns no item containers"):
        saveedit.sort_containers(mode="stackables", base_id="not-a-real-base")

    assert open(level, "rb").read() == original
    assert list_backups(), "the guard should still have taken its pre-edit backup"


@pytest.mark.slow
def test_import_writes_only_its_own_container(palsav_available, sandbox):
    """
    The import write path, end to end on a real world.

    Conservation does not apply here — an import changes totals on purpose — so
    the guarantee is scope: the target container reads back exactly as planned,
    and every other container in the world is untouched.
    """
    import saveexport
    import saveimport

    level = os.path.join(sandbox["world"], "Level.sav")

    def read_containers():
        from palsav.core import decompress_sav_to_gvas
        from palsav.gvas import GvasFile
        from palsav.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS
        import saveedit

        raw, _ = decompress_sav_to_gvas(open(level, "rb").read())
        tree = GvasFile.read(raw, PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES)
        entries = tree.properties["worldSaveData"]["value"]["ItemContainerSaveData"]["value"]
        return {saveedit._container_id_of(e): saveimport._live_slots(e) for e in entries}

    before = read_containers()

    # A container holding only plain stackables, with something to change.
    target_id, slots = next(
        (cid, s) for cid, s in before.items()
        if len(s) >= 2
        and not any(x["hasDynamicId"] for x in s)
        and any(not x["isEmpty"] for x in s)
    )

    occupied = next(s for s in slots if not s["isEmpty"])
    new_slots = [
        {**s, "stackCount": s["stackCount"] + 1} if s["slotIndex"] == occupied["slotIndex"] else s
        for s in slots
    ]
    document = saveexport.envelope(
        "container", {"containerId": target_id, "owner": None, "slots": new_slots}, "test"
    )

    plan = saveimport.plan_container_import(document, slots)
    assert plan["ok"], plan["problems"]
    assert plan["slotsChanged"] == 1

    result = saveimport.apply_container_import(document, expected_plan_hash=plan["planHash"])

    assert result["ok"] is True
    assert result["verified"] is True
    assert result["backupId"]

    after = read_containers()
    changed = {cid for cid in before if before[cid] != after.get(cid)}
    assert changed == {target_id}, f"import escaped its scope into {changed - {target_id}}"

    written = {s["slotIndex"]: s for s in after[target_id]}
    assert written[occupied["slotIndex"]]["stackCount"] == occupied["stackCount"] + 1


@pytest.mark.slow
def test_import_refuses_a_stale_plan_hash(palsav_available, sandbox):
    """Approving a preview then applying it to a world that moved must fail."""
    import saveexport
    import saveimport

    level = os.path.join(sandbox["world"], "Level.sav")
    original = open(level, "rb").read()

    from palsav.core import decompress_sav_to_gvas
    from palsav.gvas import GvasFile
    from palsav.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS
    import saveedit

    raw, _ = decompress_sav_to_gvas(original)
    tree = GvasFile.read(raw, PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES)
    entries = tree.properties["worldSaveData"]["value"]["ItemContainerSaveData"]["value"]
    target_id, slots = next(
        (saveedit._container_id_of(e), saveimport._live_slots(e))
        for e in entries
        if len(saveimport._live_slots(e)) >= 2
        and not any(x["hasDynamicId"] for x in saveimport._live_slots(e))
        and any(not x["isEmpty"] for x in saveimport._live_slots(e))
    )

    occupied = next(s for s in slots if not s["isEmpty"])
    new_slots = [
        {**s, "stackCount": s["stackCount"] + 5} if s["slotIndex"] == occupied["slotIndex"] else s
        for s in slots
    ]
    document = saveexport.envelope(
        "container", {"containerId": target_id, "owner": None, "slots": new_slots}, "test"
    )

    with pytest.raises(saveimport.ImportError_, match="no longer matches"):
        saveimport.apply_container_import(document, expected_plan_hash="a-hash-from-another-world")

    assert open(level, "rb").read() == original, "Level.sav was written despite the stale plan"


@pytest.mark.slow
def test_import_refuses_while_the_server_is_up(palsav_available, sandbox, monkeypatch):
    import safety
    import saveexport
    import saveimport

    level = os.path.join(sandbox["world"], "Level.sav")
    original = open(level, "rb").read()

    monkeypatch.setattr(
        safety, "_probe_rest_api", lambda: safety.Signal("rest_api", "running", "test")
    )
    document = saveexport.envelope(
        "container", {"containerId": "whatever", "owner": None, "slots": []}, "test"
    )

    with pytest.raises(safety.ServerRunningError):
        saveimport.apply_container_import(document)

    assert open(level, "rb").read() == original


@pytest.mark.slow
def test_sort_takes_a_backup_before_writing(palsav_available, sandbox):
    import backup as backup_module
    import saveedit

    assert backup_module.list_backups() == []
    result = saveedit.sort_containers(mode="stackables", merge=True)

    backups = backup_module.list_backups()
    assert len(backups) == 1
    assert backups[0]["id"] == result["backupId"]
    assert backups[0]["trigger"] == "pre-edit"

    # The rollback point must really contain the world, and verify clean.
    detail = backup_module.describe_backup(result["backupId"])
    assert any(f["path"] == "Level.sav" for f in detail["files"])
    assert backup_module.verify_backup(result["backupId"])["ok"] is True


@pytest.mark.slow
def test_sort_refuses_and_writes_nothing_when_server_is_up(palsav_available, sandbox, monkeypatch):
    import safety
    import saveedit
    from backup import list_backups

    level = os.path.join(sandbox["world"], "Level.sav")
    original = open(level, "rb").read()

    monkeypatch.setattr(
        safety, "_probe_rest_api", lambda: safety.Signal("rest_api", "running", "test")
    )

    with pytest.raises(safety.ServerRunningError):
        saveedit.sort_containers(mode="stackables")

    assert open(level, "rb").read() == original, "Level.sav was modified anyway"
    assert list_backups() == [], "a backup was taken despite refusing the write"


@pytest.mark.slow
def test_read_only_lock_blocks_the_sort(palsav_available, sandbox, monkeypatch):
    import safety
    import saveedit

    level = os.path.join(sandbox["world"], "Level.sav")
    original = open(level, "rb").read()

    monkeypatch.setattr(safety, "SAVE_READ_ONLY", True)

    with pytest.raises(safety.ServerRunningError, match="SAVE_READ_ONLY"):
        saveedit.sort_containers(mode="stackables")

    assert open(level, "rb").read() == original


@pytest.mark.slow
def test_backup_restore_round_trip(palsav_available, sandbox):
    """A real 2 MB world, archived and restored byte-for-byte."""
    import backup as backup_module

    level = os.path.join(sandbox["world"], "Level.sav")
    original = open(level, "rb").read()

    meta = backup_module.create_backup(sandbox["world"], "test snapshot")

    # An archive of a real world should be about the size of the world, not the
    # size of the world plus the server's own rotating backups beside it.
    assert meta["uncompressedBytes"] < 10 * 1024 * 1024, (
        "archive swept up files it should have excluded"
    )
    assert backup_module.verify_backup(meta["id"])["ok"] is True

    with open(level, "wb") as f:
        f.write(b"corrupted rubbish")

    # Our own write just bumped the mtime, and the activity probe correctly
    # reads that as a live server. Age it back so the restore is allowed.
    old = time.time() - 7200
    os.utime(level, (old, old))

    result = backup_module.restore_backup(meta["id"])
    assert result["success"] is True
    assert open(level, "rb").read() == original
    assert result["rollbackId"], "a restore must leave its own rollback point"


# ─── Player saves ────────────────────────────────────────────────


def test_player_saves_resolve_and_parse(palsav_available, refworld, monkeypatch):
    import savefiles
    from parser import extract_player_progress, load_gvas

    monkeypatch.setattr(savefiles, "SAVE_BASE_DIR", os.path.dirname(refworld))

    players_dir = os.path.join(refworld, "Players")
    names = [f for f in os.listdir(players_dir) if f.endswith(".sav") and "_dps" not in f]
    assert names, "no player saves in the reference world"

    uid = os.path.splitext(names[0])[0]
    path = savefiles.get_player_sav_path(uid, refworld)
    assert path is not None

    gvas = load_gvas(path)
    assert gvas is not None
    progress = extract_player_progress(gvas)
    assert isinstance(progress, dict) and progress


def test_dashed_lowercase_uid_finds_the_uppercase_file(refworld):
    """The real-world casing mismatch, against real filenames."""
    import savefiles

    players_dir = os.path.join(refworld, "Players")
    names = [f for f in os.listdir(players_dir) if f.endswith(".sav") and "_dps" not in f]
    stem = os.path.splitext(names[0])[0]

    dashed = f"{stem[:8]}-{stem[8:12]}-{stem[12:16]}-{stem[16:20]}-{stem[20:32]}".lower()
    assert savefiles.get_player_sav_path(dashed, refworld) is not None
