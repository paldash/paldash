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


@pytest.mark.slow
def test_sort_takes_a_backup_before_writing(palsav_available, sandbox):
    import saveedit
    from backup import list_backups

    assert list_backups() == []
    result = saveedit.sort_containers(mode="stackables", merge=True)

    backups = list_backups()
    assert len(backups) == 1
    assert backups[0]["id"] == result["backupId"]

    saved_level = os.path.join(backups[0]["path"], "Level.sav")
    assert os.path.exists(saved_level), "the backup must contain the world"


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
    from backup import create_backup, restore_backup

    level = os.path.join(sandbox["world"], "Level.sav")
    original = open(level, "rb").read()

    meta = create_backup(sandbox["world"], "test snapshot")

    with open(level, "wb") as f:
        f.write(b"corrupted rubbish")

    # Our own write just bumped the mtime, and the activity probe correctly
    # reads that as a live server. Age it back so the restore is allowed.
    old = time.time() - 7200
    os.utime(level, (old, old))

    assert restore_backup(meta["id"]) is True
    assert open(level, "rb").read() == original


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
