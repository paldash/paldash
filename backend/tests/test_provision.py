"""
Self-maintaining boot (#149): the state machine, not the network.

Every test monkeypatches the module attributes rather than the environment —
backend modules capture env at import (the house rule) — and points the data
directory and cache at tmp_path so nothing here can touch the real bundles.
The artwork test uses the refs/ archive when present and skips otherwise, so
CI (no refs/) stays green without faking a 27 MB download.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import provision  # noqa: E402

_REFS_ZIP = os.path.join(provision._ROOT, "refs", "PalWorldSaveTools-main.zip")


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(provision, "PROVISION_DIR", str(tmp_path / "prov"))
    monkeypatch.setattr(provision, "PUBLIC_DIR", str(tmp_path / "public"))
    monkeypatch.setattr(provision, "_DATA_DIR", str(tmp_path / "data"))
    os.makedirs(tmp_path / "data")
    monkeypatch.setattr(provision, "FETCH_ASSETS", True)
    monkeypatch.setattr(provision, "DATA_REFRESH", "auto")
    return tmp_path


# ─── Artwork ─────────────────────────────────────────────


def test_artwork_disabled_is_a_named_state_not_a_silent_skip(sandbox, monkeypatch):
    monkeypatch.setattr(provision, "FETCH_ASSETS", False)
    provision.ensure_artwork()
    assert provision.state()["assets"]["state"] == "disabled"


@pytest.mark.skipif(not os.path.exists(_REFS_ZIP), reason="refs/ archive absent")
def test_artwork_installs_from_the_local_archive_and_persists(sandbox):
    provision.ensure_artwork()
    got = provision.state()["assets"]
    assert got["state"] == "installed", got
    # Installed where the app serves from…
    assert os.path.exists(os.path.join(provision.PUBLIC_DIR, "maps", "palpagos.webp"))
    assert len(os.listdir(os.path.join(provision.PUBLIC_DIR, "icons", "items"))) > 100
    # …and mirrored into the cache volume for the entrypoint to restore.
    assert os.path.exists(os.path.join(provision.PROVISION_DIR, "public-maps",
                                       "palpagos.webp"))
    # Second run is a no-op "installed", not a refetch.
    provision.ensure_artwork()
    assert provision.state()["assets"]["state"] == "installed"


def test_artwork_failure_is_reported_and_unstamped(sandbox, monkeypatch):
    # No refs archive, and the "download" explodes: the state must carry the
    # error, and no manifest may be written — the next boot should retry.
    monkeypatch.setattr(provision, "_ROOT", str(sandbox / "nowhere"))
    monkeypatch.setattr(provision, "_download",
                        lambda url, dest: (_ for _ in ()).throw(OSError("no net")))
    provision.ensure_artwork()
    got = provision.state()["assets"]
    assert got["state"] == "failed" and "no net" in got["error"]
    assert not os.path.exists(provision._manifest_path())


# ─── Bundles ─────────────────────────────────────────────


def _fake_status(verdict, build="24999999"):
    return lambda: {"verdict": verdict, "buildId": build}


def test_bundles_disabled_and_nonstale_states(sandbox, monkeypatch):
    import gameversion

    monkeypatch.setattr(provision, "DATA_REFRESH", "off")
    provision.ensure_bundles()
    assert provision.state()["bundles"]["state"] == "disabled"

    monkeypatch.setattr(provision, "DATA_REFRESH", "auto")
    monkeypatch.setattr(gameversion, "status", _fake_status("current"))
    provision.ensure_bundles()
    assert provision.state()["bundles"]["state"] == "current"

    # `unknown` is not `current` — gameversion's own distinction, kept.
    monkeypatch.setattr(gameversion, "status", _fake_status("unknown"))
    provision.ensure_bundles()
    assert provision.state()["bundles"]["state"] == "unknown"


def test_stale_without_a_pak_is_reported_not_attempted(sandbox, monkeypatch):
    import gameversion

    monkeypatch.setattr(gameversion, "status", _fake_status("stale"))
    monkeypatch.setattr(provision, "_pak_path", lambda: None)
    provision.ensure_bundles()
    got = provision.state()["bundles"]
    assert got["state"] == "no-pak"
    # No stamp: mounting the install later should trigger a real attempt.
    assert not os.path.exists(provision._stamp_path("24999999"))


def test_stale_rebuild_runs_once_per_build_and_persists_changes(sandbox, monkeypatch):
    import subprocess
    import gameversion

    monkeypatch.setattr(gameversion, "status", _fake_status("stale"))
    monkeypatch.setattr(provision, "_pak_path", lambda: "/tmp/fake.pak")
    monkeypatch.setattr(provision, "_link_refs", lambda: None)
    reloads: list[bool] = []
    monkeypatch.setattr(provision, "_reload_consumers",
                        lambda: reloads.append(True))

    marker = os.path.join(provision._DATA_DIR, "regenerated.json.gz")

    def fake_run(argv, **kw):
        with open(marker, "w") as f:
            f.write("new")

        class R:
            returncode = 0
            stdout = "ok"
            stderr = ""
        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    provision.ensure_bundles()

    got = provision.state()["bundles"]
    assert got["state"] == "rebuilt" and got["changed"] == 1
    # Persisted for the entrypoint overlay, stamped for once-per-build, and
    # the module caches were told.
    assert os.path.exists(os.path.join(provision._persist_dir(),
                                       "regenerated.json.gz"))
    assert os.path.exists(provision._stamp_path("24999999"))
    assert reloads == [True]

    # Second boot on the same build: attempted, no second run.
    def must_not_run(argv, **kw):  # pragma: no cover - the assertion
        raise AssertionError("regeneration ran twice for one build")

    monkeypatch.setattr(subprocess, "run", must_not_run)
    provision.ensure_bundles()
    assert provision.state()["bundles"]["state"] == "attempted"


def test_a_refusing_pipeline_still_persists_what_it_produced(sandbox, monkeypatch):
    import subprocess
    import gameversion

    monkeypatch.setattr(gameversion, "status", _fake_status("stale", "25000001"))
    monkeypatch.setattr(provision, "_pak_path", lambda: "/tmp/fake.pak")
    monkeypatch.setattr(provision, "_link_refs", lambda: None)
    monkeypatch.setattr(provision, "_reload_consumers", lambda: None)

    def fake_run(argv, **kw):
        with open(os.path.join(provision._DATA_DIR, "half.json.gz"), "w") as f:
            f.write("new")

        class R:
            returncode = 1
            stdout = ""
            stderr = "!! refused"
        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    provision.ensure_bundles()
    got = provision.state()["bundles"]
    # A partial refresh of self-verified bundles beats none — each extractor
    # refuses rather than writing junk, so what landed is trustworthy.
    assert got["state"] == "partial" and got["changed"] == 1
    with open(provision._stamp_path("25000001"), encoding="utf-8") as f:
        stamp = json.load(f)
    assert stamp["ok"] is False and "refused" in stamp["log"]
