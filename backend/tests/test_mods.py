"""
Mod detection.

This exists to qualify a report, not to manage mods. `palcheck` already treats an
unrecognised character id as an advisory rather than as cheating; on a modded server
that advisory is not just uncertain but *expected*, and saying so is the difference
between a useful caveat and a shrug.

The property that matters most is the one about not knowing: "the game directory is
not mounted" must never be reported as "no mods installed". A dashboard that only
mounts the save path — the normal deployment — would otherwise confidently claim an
unmodded server it has never looked at.
"""

from __future__ import annotations

import os

import pytest

import mods


@pytest.fixture
def install(tmp_path):
    """An install tree with the base game pak and nothing else."""
    paks = tmp_path / "Pal" / "Content" / "Paks"
    paks.mkdir(parents=True)
    (paks / "Pal-LinuxServer.pak").write_bytes(b"x" * 512)
    return tmp_path


def add_mod(install, name: str, subdir: str = "~mods") -> None:
    target = install / "Pal" / "Content" / "Paks" / subdir
    target.mkdir(parents=True, exist_ok=True)
    (target / name).write_bytes(b"mod" * 100)


# ─── Not knowing ─────────────────────────────────────────


def test_a_missing_install_is_not_reported_as_unmodded():
    """
    The failure this module must not have. The normal deployment mounts only the
    save path, so "cannot see the game directory" is the *common* case — collapsing
    it into "no mods" would turn an unexamined server into a confident claim.
    """
    result = mods.detect("/nonexistent/install")
    assert result["checked"] is False
    assert result["modded"] is False
    assert result["mods"] == []
    assert "not visible" in result["reason"]


def test_unknown_ids_are_not_excused_when_detection_did_not_run():
    """`explains_unknown_ids` must be false for "did not look", not just for "clean"."""
    assert mods.explains_unknown_ids("/nonexistent/install") is False


# ─── Finding mods ────────────────────────────────────────


def test_a_clean_install_reports_no_mods(install):
    result = mods.detect(str(install))
    assert result["checked"] is True
    assert result["modded"] is False
    assert result["mods"] == []
    assert "No mods found" in result["reason"]


def test_the_games_own_pak_is_never_a_mod(install):
    """
    Excluded by exact name rather than by directory, because a loose mod pak sits in
    that same directory — skipping the directory would hide it.
    """
    result = mods.detect(str(install))
    assert not any(m["name"] == "Pal-LinuxServer.pak" for m in result["mods"])


def test_a_pak_in_the_mods_directory_is_found(install):
    add_mod(install, "MorePals_P.pak")
    result = mods.detect(str(install))
    assert result["modded"] is True
    assert [m["name"] for m in result["mods"]] == ["MorePals_P.pak"]
    assert result["mods"][0]["category"] == "~mods"


def test_every_known_mod_directory_is_scanned(install):
    add_mod(install, "a_P.pak", "~mods")
    add_mod(install, "b_P.pak", "LogicMods")
    add_mod(install, "c_P.pak", "Mods")
    result = mods.detect(str(install))
    assert result["count"] == 3
    assert {m["category"] for m in result["mods"]} == {"~mods", "LogicMods", "Mods"}


def test_a_loose_pak_beside_the_game_is_found(install):
    add_mod(install, "Sneaky_P.pak", ".")
    result = mods.detect(str(install))
    assert [m["name"] for m in result["mods"]] == ["Sneaky_P.pak"]


def test_non_pak_files_are_ignored(install):
    paks = install / "Pal" / "Content" / "Paks" / "~mods"
    paks.mkdir(parents=True)
    (paks / "readme.txt").write_text("hello")
    (paks / "MorePals_P.ucas").write_bytes(b"x")
    assert mods.detect(str(install))["count"] == 0


def test_unknown_ids_are_excused_on_a_modded_server(install):
    add_mod(install, "MorePals_P.pak")
    assert mods.explains_unknown_ids(str(install)) is True


def test_unknown_ids_are_not_excused_on_a_clean_server(install):
    assert mods.explains_unknown_ids(str(install)) is False


# ─── The loader ──────────────────────────────────────────


def test_ue4ss_is_reported_and_its_limit_stated(install):
    """
    UE4SS loads Lua mods that leave no pak at all, so its presence is a statement
    about what this detection *cannot* see. Saying so is the point.
    """
    binaries = install / "Pal" / "Binaries" / "Win64"
    binaries.mkdir(parents=True)
    (binaries / "ue4ss.dll").write_bytes(b"x")

    result = mods.detect(str(install))
    assert result["modded"] is True
    assert result["loader"]["name"] == "UE4SS"
    assert "cannot be listed" in result["reason"]


def test_a_loader_alone_counts_as_modded(install):
    binaries = install / "Pal" / "Binaries" / "Linux"
    binaries.mkdir(parents=True)
    (binaries / "ue4ss.so").write_bytes(b"x")
    result = mods.detect(str(install))
    assert result["modded"] is True
    assert result["mods"] == []


def test_an_unreadable_directory_does_not_raise(install, monkeypatch):
    def boom(path):
        raise PermissionError(path)

    monkeypatch.setattr(os, "listdir", boom)
    result = mods.detect(str(install))
    assert result["checked"] is True
    assert result["mods"] == []


# ─── Wired into the Pal checker ──────────────────────────


def test_the_scan_report_carries_the_mod_context(install, monkeypatch):
    """
    The reason this module exists. A scan reporting 40 unrecognised species should
    say whether there is an innocent explanation for them.
    """
    import palcheck

    add_mod(install, "MorePals_P.pak")
    monkeypatch.setattr(mods, "detect", lambda install_dir="": {
        "checked": True, "modded": True,
        "mods": [{"name": "MorePals_P.pak"}], "reason": "1 mod pak(s) installed.",
    })

    report = palcheck.scan([], {})
    assert report["mods"]["modded"] is True
    assert report["mods"]["count"] == 1


def test_a_failing_mod_check_does_not_lose_the_scan(monkeypatch):
    """A diagnostic must not be lost because a directory listing failed."""
    import palcheck

    def boom(install_dir=""):
        raise OSError("disk on fire")

    monkeypatch.setattr(mods, "detect", boom)
    report = palcheck.scan([], {})
    assert report["mods"]["checked"] is False
    assert "failed" in report["mods"]["reason"].lower()
