"""
Path resolution and corruption-safe file I/O.

Covers the two bugs that actually bit during development: player .sav filenames
are uppercase and undashed while Level.sav stores lowercase dashed GUIDs, and a
save read during an autosave yields a torn buffer.
"""

from __future__ import annotations

import os
import threading
import time

import pytest

import savefiles


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A minimal world directory tree."""
    base = tmp_path / "SaveGames" / "0"
    world_dir = base / "ABCDEF0123456789"
    (world_dir / "Players").mkdir(parents=True)
    (world_dir / "Level.sav").write_bytes(b"level")
    monkeypatch.setattr(savefiles, "SAVE_BASE_DIR", str(base))
    return world_dir


# ─── Locating worlds and players ─────────────────────────────────


def test_find_world_dirs_requires_level_sav(world, tmp_path, monkeypatch):
    base = os.path.dirname(str(world))
    (tmp_path / "SaveGames" / "0" / "NotAWorld").mkdir()

    found = savefiles.find_world_dirs()
    assert found == [str(world)], "a directory without Level.sav is not a world"
    assert base == os.path.dirname(found[0])


def test_missing_save_base_dir_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(savefiles, "SAVE_BASE_DIR", str(tmp_path / "absent"))
    assert savefiles.find_world_dirs() == []
    assert savefiles.get_default_world_dir() is None


def test_world_guid_pin_selects_that_world(world, monkeypatch):
    monkeypatch.setenv("WORLD_GUID", "ABCDEF0123456789")
    assert savefiles.get_default_world_dir() == str(world)


def test_unknown_world_guid_falls_back(world, monkeypatch):
    monkeypatch.setenv("WORLD_GUID", "does-not-exist")
    assert savefiles.get_default_world_dir() == str(world)


def test_player_path_exact_match(world):
    (world / "Players" / "22B22B02000000000000000000000000.sav").write_bytes(b"p")
    got = savefiles.get_player_sav_path(
        "22B22B02000000000000000000000000", str(world)
    )
    assert got is not None and got.endswith("22B22B02000000000000000000000000.sav")


def test_player_path_matches_dashed_lowercase_guid(world):
    """
    Level.sav gives us '22b22b02-0000-0000-0000-000000000000'; the file on disk
    is '22B22B02000000000000000000000000.sav'. Comparing directly finds nothing.
    """
    (world / "Players" / "22B22B02000000000000000000000000.sav").write_bytes(b"p")
    got = savefiles.get_player_sav_path(
        "22b22b02-0000-0000-0000-000000000000", str(world)
    )
    assert got is not None, "dashed lowercase GUID must resolve to the uppercase file"
    assert got.endswith("22B22B02000000000000000000000000.sav")


def test_player_path_missing_returns_none(world):
    assert savefiles.get_player_sav_path("deadbeef", str(world)) is None


@pytest.mark.parametrize(
    "hostile",
    [
        "../../../../etc/passwd",
        "..",
        "../Level",
        "foo/bar",
        "with space",
        "semi;colon",
        "",
    ],
)
def test_player_path_rejects_traversal(world, hostile):
    assert savefiles.get_player_sav_path(hostile, str(world)) is None


# ─── Reading ─────────────────────────────────────────────────────


def test_read_sav_bytes_roundtrip(tmp_path):
    path = tmp_path / "a.sav"
    payload = os.urandom(1024 * 1024 * 5 + 17)  # spans several read chunks
    path.write_bytes(payload)
    assert savefiles.read_sav_bytes(str(path)) == payload


def test_read_sav_bytes_missing_file(tmp_path):
    assert savefiles.read_sav_bytes(str(tmp_path / "nope.sav")) is None


def test_read_sav_bytes_gives_up_on_a_file_that_keeps_changing(tmp_path, monkeypatch):
    """
    A file rewritten on every read is what an autosaving server looks like.
    Returning None is correct — handing a torn buffer to the parser is not.
    """
    path = tmp_path / "busy.sav"
    path.write_bytes(b"a" * 100)

    real_stat_key = savefiles._stat_key
    calls = {"n": 0}

    def moving_target(p):
        calls["n"] += 1
        # Report a different (size, mtime) every call so before != after.
        return (100 + calls["n"], float(calls["n"]))

    monkeypatch.setattr(savefiles, "_stat_key", moving_target)
    monkeypatch.setattr(savefiles.time, "sleep", lambda s: None)

    assert savefiles.read_sav_bytes(str(path)) is None
    assert calls["n"] >= savefiles.STABILITY_RETRIES
    assert real_stat_key is not None  # sanity: we patched the right symbol


def test_read_sav_bytes_succeeds_when_file_is_stable(tmp_path, monkeypatch):
    path = tmp_path / "calm.sav"
    path.write_bytes(b"stable")
    monkeypatch.setattr(savefiles.time, "sleep", lambda s: None)
    assert savefiles.read_sav_bytes(str(path)) == b"stable"


# ─── Writing ─────────────────────────────────────────────────────


def test_atomic_write_creates_file(tmp_path):
    path = tmp_path / "out.bin"
    savefiles.atomic_write(str(path), b"hello")
    assert path.read_bytes() == b"hello"


def test_atomic_write_replaces_existing(tmp_path):
    path = tmp_path / "out.bin"
    path.write_bytes(b"old content that is longer")
    savefiles.atomic_write(str(path), b"new")
    assert path.read_bytes() == b"new"


def test_atomic_write_leaves_no_temp_files(tmp_path):
    path = tmp_path / "out.bin"
    savefiles.atomic_write(str(path), b"data")
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".tmp_")]
    assert leftovers == []


def test_atomic_write_preserves_original_on_failure(tmp_path, monkeypatch):
    """If the rename fails, the original file must survive untouched."""
    path = tmp_path / "precious.bin"
    path.write_bytes(b"original")

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(savefiles.os, "replace", boom)

    with pytest.raises(OSError):
        savefiles.atomic_write(str(path), b"replacement")

    assert path.read_bytes() == b"original"
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".tmp_")]
    assert leftovers == [], "temp file must be cleaned up even when the write fails"


def test_atomic_write_is_observably_atomic(tmp_path):
    """
    A concurrent reader must see either the old bytes or the new bytes, never a
    partial write. Uses a large payload so a non-atomic implementation would be
    caught mid-flight.
    """
    path = tmp_path / "race.bin"
    old = b"o" * (2 * 1024 * 1024)
    new = b"n" * (2 * 1024 * 1024)
    path.write_bytes(old)

    seen: list[bytes] = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                seen.append(path.read_bytes())
            except OSError:
                pass
            time.sleep(0.0005)

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    try:
        for _ in range(5):
            savefiles.atomic_write(str(path), new)
            savefiles.atomic_write(str(path), old)
    finally:
        stop.set()
        t.join(timeout=2)

    assert seen, "reader thread never observed the file"
    assert all(s in (old, new) for s in seen), "a torn read was observable"
