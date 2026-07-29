"""
Backup archives, verification, retention and restore.

A backup you cannot verify is a rumour, so most of this is about proving the
archive really does contain what its manifest claims.
"""

from __future__ import annotations

import os
import tarfile

import pytest

import backup as backup_module
import backupstore
import safety
from backupstore import BackupError


@pytest.fixture
def world(tmp_path, monkeypatch):
    """
    A world directory containing the server's own rotating backups.

    That nested `backup/` directory is the whole point: the previous
    implementation copied it into every dashboard backup.
    """
    import savefiles

    base = tmp_path / "SaveGames" / "0"
    world_dir = base / "ABCDEF0123456789"
    (world_dir / "Players").mkdir(parents=True)

    (world_dir / "Level.sav").write_bytes(b"LEVEL" * 2000)
    (world_dir / "LevelMeta.sav").write_bytes(b"META" * 10)
    (world_dir / "Players" / "AAAA1111.sav").write_bytes(b"PLAYER-A" * 50)
    (world_dir / "Players" / "BBBB2222.sav").write_bytes(b"PLAYER-B" * 50)

    # The server's own snapshots — must never end up inside our archive.
    noise = world_dir / "backup" / "world" / "2026.07.01-00.00.00"
    noise.mkdir(parents=True)
    (noise / "Level.sav").write_bytes(b"OLD" * 100_000)

    backups = tmp_path / "backups"
    backups.mkdir()

    monkeypatch.setattr(savefiles, "SAVE_BASE_DIR", str(base))
    monkeypatch.setattr(savefiles, "BACKUP_DIR", str(backups))
    monkeypatch.setattr(backup_module, "BACKUP_DIR", str(backups))
    backup_module._reset_store_for_tests()

    # No live server in a test environment.
    monkeypatch.setattr(safety, "SAVE_READ_ONLY", False)
    monkeypatch.setattr(safety, "ALLOW_UNVERIFIED_EDITS", False)
    # Signal names matter: safety.get_server_state looks them up by name.
    for probe, name, verdict in (
        ("_probe_rest_api", "rest_api", "stopped"),
        ("_probe_tcp", "tcp_port", "stopped"),
        ("_probe_save_activity", "save_activity", "stopped"),
        ("_probe_process", "process", "unknown"),
    ):
        monkeypatch.setattr(
            safety, probe,
            lambda n=name, v=verdict: safety.Signal(n, v, "test"),
        )

    yield {"world": str(world_dir), "backups": str(backups), "base": str(base)}
    backup_module._reset_store_for_tests()


# ─── What goes into an archive ───────────────────────────────────


def test_collects_only_save_files(world):
    collected = backupstore.collect_world_files(world["world"])
    paths = {relative.replace(os.sep, "/") for _absolute, relative in collected}
    assert paths == {
        "Level.sav", "LevelMeta.sav",
        "Players/AAAA1111.sav", "Players/BBBB2222.sav",
    }


def test_nested_server_backups_are_excluded(world):
    """
    The bug this replaced: `copytree` swept the server's own rotating snapshots
    into every dashboard backup, turning a 2 MB world into a 66 MB archive whose
    contents included copies of all the earlier backups.
    """
    collected = backupstore.collect_world_files(world["world"])
    assert not [rel for _abs, rel in collected if "backup" in rel.lower()]

    meta = backup_module.create_backup(world["world"], "test")
    with tarfile.open(backup_module.store().path_for(meta["id"])) as tar:
        names = tar.getnames()
    assert not [n for n in names if "backup/" in n]

    # And the archive is sized like the world, not like the world plus history.
    on_disk = sum(
        os.path.getsize(a) for a, _r in backupstore.collect_world_files(world["world"])
    )
    assert meta["uncompressedBytes"] == on_disk


def test_archive_includes_the_config_file(world, tmp_path, monkeypatch):
    import savefiles

    ini = tmp_path / "PalWorldSettings.ini"
    ini.write_text("[/Script/Pal.PalGameWorldSettings]\nOptionSettings=(ExpRate=1.0)\n")
    monkeypatch.setattr(savefiles, "find_settings_ini", lambda: str(ini))
    monkeypatch.setattr(backup_module, "find_settings_ini", lambda: str(ini))

    meta = backup_module.create_backup(world["world"], "with config")
    detail = backup_module.describe_backup(meta["id"])
    assert any(f["path"] == "config/PalWorldSettings.ini" for f in detail["files"])


def test_manifest_is_inside_the_archive(world):
    """An archive must be self-describing even if the sidecar is lost."""
    meta = backup_module.create_backup(world["world"], "test")
    manifest = backupstore.read_manifest(backup_module.store().path_for(meta["id"]))
    assert manifest["fileCount"] == 4
    assert all("sha256" in f for f in manifest["files"])


def test_listing_survives_losing_the_sidecar(world):
    meta = backup_module.create_backup(world["world"], "test")
    os.unlink(backup_module.store().manifest_path(meta["id"]))

    listed = backup_module.list_backups()
    assert len(listed) == 1
    assert listed[0]["id"] == meta["id"]


def test_empty_world_is_refused(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(BackupError, match="No save files"):
        backupstore.create_archive(str(empty), str(tmp_path / "out.tar.gz"))


def test_interrupted_archive_leaves_no_partial_file(world, monkeypatch):
    destination = os.path.join(world["backups"], "boom.tar.gz")

    real_add = tarfile.TarFile.add

    def explode(self, name, arcname=None, **kwargs):
        if arcname and arcname.startswith("Players/"):
            raise OSError("disk full")
        return real_add(self, name, arcname=arcname, **kwargs)

    monkeypatch.setattr(tarfile.TarFile, "add", explode)

    with pytest.raises(OSError):
        backupstore.create_archive(world["world"], destination)

    assert not os.path.exists(destination)
    assert not os.path.exists(destination + ".part")


# ─── Verification ────────────────────────────────────────────────


def test_fresh_backup_verifies(world):
    meta = backup_module.create_backup(world["world"], "test")
    verdict = backup_module.verify_backup(meta["id"])
    assert verdict["ok"] is True
    assert verdict["checkedFiles"] == 4
    assert verdict["problems"] == []


def test_verification_detects_a_tampered_archive(world):
    meta = backup_module.create_backup(world["world"], "test")
    path = backup_module.store().path_for(meta["id"])

    with open(path, "r+b") as f:
        f.seek(os.path.getsize(path) // 2)
        f.write(b"\x00\x01\x02\x03")

    verdict = backup_module.verify_backup(meta["id"])
    assert verdict["ok"] is False
    assert verdict["problems"]


def test_verification_detects_a_missing_archive(world):
    meta = backup_module.create_backup(world["world"], "test")
    os.unlink(backup_module.store().path_for(meta["id"]))
    verdict = backup_module.verify_backup(meta["id"])
    assert verdict["ok"] is False


def test_verify_unknown_backup(world):
    assert backup_module.verify_backup("nonexistent")["ok"] is False


# ─── Restore ─────────────────────────────────────────────────────


def test_restore_brings_back_the_original_contents(world):
    level = os.path.join(world["world"], "Level.sav")
    original = open(level, "rb").read()

    meta = backup_module.create_backup(world["world"], "before damage")

    with open(level, "wb") as f:
        f.write(b"corrupted")

    result = backup_module.restore_backup(meta["id"])
    assert result["success"] is True
    assert open(level, "rb").read() == original


def test_restore_creates_a_rollback_point(world):
    meta = backup_module.create_backup(world["world"], "first")
    result = backup_module.restore_backup(meta["id"])

    rollback = backup_module.find_backup(result["rollbackId"])
    assert rollback is not None
    assert rollback["trigger"] == "pre-restore"


def test_restore_refuses_a_corrupt_backup(world):
    """
    Restoring a broken archive over a working world is the worst possible
    outcome, so verification happens before anything is touched.
    """
    level = os.path.join(world["world"], "Level.sav")
    original = open(level, "rb").read()

    meta = backup_module.create_backup(world["world"], "test")
    path = backup_module.store().path_for(meta["id"])
    with open(path, "r+b") as f:
        f.seek(os.path.getsize(path) // 2)
        f.write(b"\x00" * 32)

    with pytest.raises(BackupError, match="failed verification"):
        backup_module.restore_backup(meta["id"])

    assert open(level, "rb").read() == original


def test_restore_refuses_while_the_server_may_be_running(world, monkeypatch):
    meta = backup_module.create_backup(world["world"], "test")
    monkeypatch.setattr(
        safety, "_probe_rest_api", lambda: safety.Signal("rest_api", "running", "test")
    )
    with pytest.raises(safety.ServerRunningError):
        backup_module.restore_backup(meta["id"])


def test_players_scope_leaves_the_world_alone(world):
    level = os.path.join(world["world"], "Level.sav")
    player = os.path.join(world["world"], "Players", "AAAA1111.sav")

    meta = backup_module.create_backup(world["world"], "test")

    with open(level, "wb") as f:
        f.write(b"newer world state")
    with open(player, "wb") as f:
        f.write(b"broken player")

    backup_module.restore_backup(meta["id"], scope="players")

    assert open(player, "rb").read() == b"PLAYER-A" * 50
    assert open(level, "rb").read() == b"newer world state", "world must be untouched"


def test_unknown_scope_is_refused(world):
    meta = backup_module.create_backup(world["world"], "test")
    with pytest.raises(BackupError, match="Unknown restore scope"):
        backup_module.restore_backup(meta["id"], scope="everything")


def test_extract_refuses_path_traversal(world, tmp_path):
    """A tar entry named `../../escape` must not be written outside the target."""
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as tar:
        payload = b"pwned"
        info = tarfile.TarInfo("../../escaped.txt")
        info.size = len(payload)
        import io

        tar.addfile(info, io.BytesIO(payload))

    with pytest.raises(BackupError, match="unsafe archive entry"):
        backupstore.extract_archive(str(evil), str(tmp_path / "dest"))
    assert not (tmp_path.parent / "escaped.txt").exists()


# ─── Restore preview ─────────────────────────────────────────────


def test_preview_reports_identical_when_nothing_changed(world):
    meta = backup_module.create_backup(world["world"], "test")
    preview = backup_module.preview_restore(meta["id"])
    assert preview["summary"]["identical"] == 4
    assert preview["summary"]["replace"] == 0


def test_preview_detects_a_changed_file(world):
    meta = backup_module.create_backup(world["world"], "test")
    with open(os.path.join(world["world"], "Level.sav"), "wb") as f:
        f.write(b"different content entirely")

    preview = backup_module.preview_restore(meta["id"])
    changed = [c for c in preview["changes"] if c["action"] == "replace"]
    assert [c["path"] for c in changed] == ["Level.sav"]


def test_preview_detects_same_size_different_content(world):
    """Same size is not same content — the preview hashes rather than stats."""
    level = os.path.join(world["world"], "Level.sav")
    meta = backup_module.create_backup(world["world"], "test")

    size = os.path.getsize(level)
    with open(level, "wb") as f:
        f.write(b"X" * size)

    preview = backup_module.preview_restore(meta["id"])
    assert any(c["path"] == "Level.sav" and c["action"] == "replace" for c in preview["changes"])


def test_preview_lists_files_a_restore_would_keep(world):
    """A player who joined after the backup is not deleted by restoring it."""
    meta = backup_module.create_backup(world["world"], "test")
    newcomer = os.path.join(world["world"], "Players", "CCCC3333.sav")
    with open(newcomer, "wb") as f:
        f.write(b"NEW PLAYER")

    preview = backup_module.preview_restore(meta["id"])
    assert [k["path"] for k in preview["keptUntouched"]] == ["Players/CCCC3333.sav"]


def test_preview_changes_nothing(world):
    level = os.path.join(world["world"], "Level.sav")
    with open(level, "wb") as f:
        f.write(b"current state")

    meta = backup_module.create_backup(world["world"], "test")
    backup_module.preview_restore(meta["id"])
    assert open(level, "rb").read() == b"current state"


# ─── Retention ───────────────────────────────────────────────────


def _backdate(backup_id: str, iso: str) -> None:
    import json

    path = backup_module.store().manifest_path(backup_id)
    with open(path) as f:
        manifest = json.load(f)
    manifest["createdAt"] = iso
    with open(path, "w") as f:
        json.dump(manifest, f)


def test_prune_keeps_the_newest(world):
    ids = [backup_module.create_backup(world["world"], f"#{i}")["id"] for i in range(8)]
    for offset, backup_id in enumerate(ids):
        _backdate(backup_id, f"2026-01-{20 - offset:02d}T12:00:00+00:00")

    result = backup_module.prune_backups({"keepLatest": 3, "keepDaily": 0, "keepWeekly": 0})
    remaining = {b["id"] for b in backup_module.list_backups()}
    assert len(remaining) == 3
    assert result["removed"]


def test_prune_dry_run_deletes_nothing(world):
    for i in range(5):
        _backdate(
            backup_module.create_backup(world["world"], f"#{i}")["id"],
            f"2026-01-{10 + i:02d}T12:00:00+00:00",
        )

    before = len(backup_module.list_backups())
    result = backup_module.prune_backups({"keepLatest": 1, "keepDaily": 0, "keepWeekly": 0},
                                          dry_run=True)
    assert result["dryRun"] is True
    assert result["removed"]
    assert len(backup_module.list_backups()) == before


def test_prune_protects_recent_safety_backups(world):
    """
    The rollback point taken before an edit is the only way back from a bad
    edit, so retention must not delete it while it is still fresh.
    """
    old = backup_module.create_backup(world["world"], "old manual")
    _backdate(old["id"], "2026-01-01T00:00:00+00:00")

    # Left at its real timestamp: the protection is a grace period, so a rollback
    # point only survives retention while it is still recent.
    safety_backup = backup_module.create_backup(
        world["world"], "before an edit", trigger="pre-edit"
    )

    newest = backup_module.create_backup(world["world"], "newest")

    backup_module.prune_backups({"keepLatest": 1, "keepDaily": 0, "keepWeekly": 0})
    remaining = {b["id"] for b in backup_module.list_backups()}

    assert newest["id"] in remaining
    assert safety_backup["id"] in remaining, "a fresh rollback point must survive retention"


def test_prune_keeps_one_per_day(world):
    for day in range(1, 6):
        for hour in (9, 18):
            _backdate(
                backup_module.create_backup(world["world"], f"d{day}h{hour}")["id"],
                f"2026-03-{day:02d}T{hour:02d}:00:00+00:00",
            )

    backup_module.prune_backups({"keepLatest": 1, "keepDaily": 5, "keepWeekly": 0})
    remaining = backup_module.list_backups()
    days = {b["timestamp"][:10] for b in remaining}
    assert len(days) == len(remaining), "at most one backup should remain per day"


def test_storage_usage_reports_totals(world):
    backup_module.create_backup(world["world"], "one")
    backup_module.create_backup(world["world"], "two")
    usage = backup_module.storage_usage()
    assert usage["count"] == 2
    assert usage["totalBytes"] > 0


# ─── Metadata ────────────────────────────────────────────────────


def test_rename_updates_the_description(world):
    meta = backup_module.create_backup(world["world"], "original name")
    backup_module.rename_backup(meta["id"], "a better name")
    assert backup_module.find_backup(meta["id"])["description"] == "a better name"


def test_rename_does_not_break_verification(world):
    """The sidecar is an index; the archive's own checksum must stay valid."""
    meta = backup_module.create_backup(world["world"], "original")
    backup_module.rename_backup(meta["id"], "renamed")
    assert backup_module.verify_backup(meta["id"])["ok"] is True


def test_delete_removes_archive_and_sidecar(world):
    meta = backup_module.create_backup(world["world"], "test")
    assert backup_module.delete_backup(meta["id"]) is True
    assert not os.path.exists(backup_module.store().path_for(meta["id"]))
    assert not os.path.exists(backup_module.store().manifest_path(meta["id"]))
    assert backup_module.delete_backup(meta["id"]) is False


@pytest.mark.parametrize("hostile", ["../escape", "a/b", "", "with space", "semi;colon"])
def test_store_refuses_unsafe_ids(world, hostile):
    with pytest.raises(BackupError, match="Invalid backup id"):
        backup_module.store().path_for(hostile)


def test_prune_drops_an_expired_safety_backup(world, monkeypatch):
    """The grace period ends: an ancient rollback point is not kept forever."""
    monkeypatch.setattr(backup_module, "SAFETY_GRACE_HOURS", 1)

    stale = backup_module.create_backup(world["world"], "old edit", trigger="pre-edit")
    _backdate(stale["id"], "2026-01-02T00:00:00+00:00")
    newest = backup_module.create_backup(world["world"], "newest")

    backup_module.prune_backups({"keepLatest": 1, "keepDaily": 0, "keepWeekly": 0})
    remaining = {b["id"] for b in backup_module.list_backups()}
    assert remaining == {newest["id"]}
