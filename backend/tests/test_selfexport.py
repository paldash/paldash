"""
Self-serve world copy: every refusal is a feature, so every refusal has a test.

The module's whole contract is that only the caller's own data can leave the
server. The unit tests pin each gate with the world mocked out; the integration
test at the bottom runs the real pipeline on the reference world and inspects
the archive itself — the archive, not the return value, is what a player
actually receives.
"""

from __future__ import annotations

import os
import tarfile
import time

import pytest

import selfexport


@pytest.fixture
def sandbox(fresh_db, tmp_path, monkeypatch):
    """A clean database, a temp export root, and no cooldown unless a test sets one."""
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(selfexport, "ENABLED", True)
    monkeypatch.setattr(selfexport, "MIN_INTERVAL", 3600)
    monkeypatch.setattr(selfexport, "RETENTION_DAYS", 7)
    return tmp_path


def _seed_row(username: str, created_at: float, path: str = "") -> None:
    import db

    selfexport.init()
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO self_exports (username, uid, path, sha256, size_bytes, "
            "created_at) VALUES (?, '', ?, '', 0, ?)",
            (username, path, created_at),
        )


UID = "11a11a01-0000-0000-0000-000000000000"


# ─── Refusals, cheapest first ────────────────────────────


def test_disabled_deployment_refuses(sandbox, monkeypatch):
    monkeypatch.setattr(selfexport, "ENABLED", False)
    with pytest.raises(selfexport.SelfExportError, match="disabled") as e:
        selfexport.create("p1", UID)
    assert e.value.status == 403


def test_an_unlinked_account_is_refused_with_the_fix_named(sandbox):
    with pytest.raises(selfexport.SelfExportError, match="linked"):
        selfexport.create("p1", "")


def test_the_cooldown_refuses_with_429(sandbox):
    _seed_row("p1", time.time())
    with pytest.raises(selfexport.SelfExportError, match="available in") as e:
        selfexport.create("p1", UID)
    assert e.value.status == 429


def test_a_struggling_server_defers(sandbox, monkeypatch):
    import savecache

    monkeypatch.setattr(
        savecache, "load_verdict", lambda force=False: {"busy": True, "reason": "9 fps"}
    )
    with pytest.raises(selfexport.SelfExportError, match="under load") as e:
        selfexport.create("p1", UID)
    assert e.value.status == 503


def test_a_shared_guild_is_refused_and_points_at_a_moderator(sandbox, monkeypatch):
    import exportscope
    import savecache

    monkeypatch.setattr(savecache, "load_verdict", lambda force=False: {"busy": False})
    monkeypatch.setattr(exportscope, "load_world", lambda *a, **k: {})
    monkeypatch.setattr(
        exportscope, "guilds",
        lambda world: [{
            "guildId": "g1", "adminUid": UID,
            "playerUids": [UID, "22b22b02-0000-0000-0000-000000000000"],
        }],
    )
    with pytest.raises(selfexport.SelfExportError, match="other members"):
        selfexport.create("p1", UID)


def test_a_character_in_no_guild_is_refused_not_treated_as_solo(sandbox, monkeypatch):
    import exportscope
    import savecache

    monkeypatch.setattr(savecache, "load_verdict", lambda force=False: {"busy": False})
    monkeypatch.setattr(exportscope, "load_world", lambda *a, **k: {})
    monkeypatch.setattr(exportscope, "guilds", lambda world: [])
    with pytest.raises(selfexport.SelfExportError, match="not found in any guild"):
        selfexport.create("p1", UID)


def test_a_refused_prune_is_a_refused_export(sandbox, monkeypatch):
    """
    The divergence from the moderator flow, pinned. There, a refused prune
    writes the full copy; here the prune IS the permission, so the copy must
    never survive it.
    """
    import savecache
    import soloexport

    monkeypatch.setattr(savecache, "load_verdict", lambda force=False: {"busy": False})
    monkeypatch.setattr(selfexport, "_require_solo_guild_live", lambda uid: None)

    made: list[str] = []

    def fake_apply(source, target, destination=None, keep_guilds=None, **kw):
        os.makedirs(destination, exist_ok=True)
        made.append(destination)
        return {
            "mode": "rename", "applied": {"total": 1},
            "prune": {"requested": True, "pruned": False, "refused": "nope"},
        }

    monkeypatch.setattr(soloexport, "apply_export", fake_apply)
    with pytest.raises(selfexport.SelfExportError, match="could not be scoped"):
        selfexport.create("p1", UID)
    assert made and not os.path.exists(made[0]), "the unpruned copy must be deleted"
    assert selfexport.status("p1", UID)["archive"] is None


# ─── The happy path, with the world mocked ───────────────


def _fake_success(monkeypatch):
    import savecache
    import soloexport

    monkeypatch.setattr(savecache, "load_verdict", lambda force=False: {"busy": False})
    monkeypatch.setattr(selfexport, "_require_solo_guild_live", lambda uid: None)

    def fake_apply(source, target, destination=None, keep_guilds=None, **kw):
        assert target == selfexport.HOST_UID
        assert keep_guilds == []
        os.makedirs(destination, exist_ok=True)
        with open(os.path.join(destination, "Level.sav"), "wb") as f:
            f.write(b"world bytes")
        return {
            "mode": "rename", "applied": {"total": 123},
            "prune": {"requested": True, "pruned": True, "dropGuildIds": ["g2"]},
        }

    monkeypatch.setattr(soloexport, "apply_export", fake_apply)


def test_success_stores_one_archive_and_starts_the_cooldown(sandbox, monkeypatch):
    _fake_success(monkeypatch)
    result = selfexport.create("p1", UID)
    assert result["ok"] is True
    assert result["prune"]["guildsRemoved"] == 1

    meta = selfexport.archive_for_download("p1")
    assert os.path.isfile(meta["path"])
    # The unpacked directory is gone — only the archive is kept.
    assert not os.path.isdir(meta["path"][: -len(".tar.gz")])

    with pytest.raises(selfexport.SelfExportError) as e:
        selfexport.create("p1", UID)
    assert e.value.status == 429


def test_a_new_export_replaces_the_old_archive(sandbox, monkeypatch):
    _fake_success(monkeypatch)
    monkeypatch.setattr(selfexport, "MIN_INTERVAL", 0)
    selfexport.create("p1", UID)
    old_path = selfexport.archive_for_download("p1")["path"]
    # Archive names carry a timestamp so the new file can never collide with —
    # and accidentally delete — the old one. One-second resolution needs a
    # nudge in a test that runs both exports inside the same second.
    time.sleep(1.1)
    selfexport.create("p1", UID)
    new_path = selfexport.archive_for_download("p1")["path"]
    assert new_path != old_path
    assert os.path.isfile(new_path)
    assert not os.path.exists(old_path), "the replaced archive must be deleted"
    slot_dir = os.path.dirname(new_path)
    archives = [n for n in os.listdir(slot_dir) if n.endswith(".tar.gz")]
    assert len(archives) == 1, f"exactly one archive per account, found {archives}"


def test_expired_archives_are_swept(sandbox, monkeypatch):
    stale = sandbox / "old.tar.gz"
    stale.write_bytes(b"x")
    _seed_row("ghost", time.time() - 8 * 86400, path=str(stale))

    assert selfexport.status("someone-else", UID)["enabled"] is True
    assert not stale.exists()
    assert selfexport._row("ghost") is None


def test_download_with_no_archive_is_a_404(sandbox):
    with pytest.raises(selfexport.SelfExportError) as e:
        selfexport.archive_for_download("p1")
    assert e.value.status == 404


def test_two_usernames_that_sanitise_alike_get_different_directories():
    assert selfexport._slug("user name") != selfexport._slug("user_name")


# ─── Against the real world ──────────────────────────────


@pytest.mark.integration
@pytest.mark.slow
def test_the_archive_holds_exactly_one_players_world(
    sandbox, refworld, palsav_available, monkeypatch
):
    """
    End to end on the reference world: the archive a player downloads contains
    their own guild remapped to the host uid — and nobody else's player save.
    That last assertion reads the tar itself, because the return value saying
    "pruned" and the archive actually being pruned are different facts.
    """
    import savefiles

    monkeypatch.setattr(savefiles, "get_default_world_dir", lambda: refworld)

    names = sorted(
        n for n in os.listdir(os.path.join(refworld, "Players"))
        if n.endswith(".sav") and not n.endswith("_dps.sav")
    )
    raw = names[0][: -len(".sav")].lower()
    uid = f"{raw[0:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:32]}"

    result = selfexport.create("tester", uid)
    assert result["ok"] is True
    assert result["prune"]["guildsRemoved"] >= 1

    meta = selfexport.archive_for_download("tester")
    host_file = selfexport.HOST_UID.replace("-", "").upper()
    with tarfile.open(meta["path"]) as tar:
        players = [
            m.name for m in tar.getmembers()
            if m.name.startswith("Players/") and m.name.endswith(".sav")
        ]
    assert players, "the archive carries no player save at all"
    for name in players:
        assert os.path.basename(name).startswith(host_file), (
            f"a player save that is not the exported character survived: {name}"
        )

    with pytest.raises(selfexport.SelfExportError) as e:
        selfexport.create("tester", uid)
    assert e.value.status == 429
