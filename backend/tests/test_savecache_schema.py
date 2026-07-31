"""
The on-disk parse cache outlives the code that wrote it.

`level_cache.json` survives an upgrade, and a newer dashboard reading an older
payload does not raise — it reads a field that is not there. Renaming the
per-base Pal count produced `undefined` in the API and a literal **"NaN"** on the
Bases tab of a server whose only mistake was upgrading without re-parsing.

**Discarding the cache was only half the fix, and shipping only that half was
worse than the bug.** `PARSE_AUTO` is false by default, so nothing re-parses on
its own: the discard left the whole dashboard empty — no Pals, no bases, no
breeding, for every role — with no error and no path back except someone
happening to press Refresh. These tests pin both halves together, because either
alone is a broken state:

  1. a mismatched cache is not served, and
  2. something rebuilds it, and says so meanwhile.
"""

from __future__ import annotations

import importlib
import json
import os

import pytest


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    """A savecache module bound to an empty cache directory."""
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    import savefiles

    importlib.reload(savefiles)
    yield tmp_path


def _reload_savecache():
    import savecache

    return importlib.reload(savecache)


def _write_cache(path, **extra):
    payload = {
        "ok": True,
        "parsedAt": 1_700_000_000.0,
        "sourceMtime": 1.0,
        "counts": {"pals": 1905, "bases": 11},
        "bases": [{"id": "b1", "guildPalCount": 7}],
        "pals": [{"instanceId": "p1"}],
        **extra,
    }
    with open(os.path.join(path, "level_cache.json"), "w") as f:
        json.dump(payload, f)


# ─── Half one: it is not served ───────────────────────────


def test_a_cache_with_no_schema_is_discarded(cache_dir):
    """What every pre-upgrade cache looks like: the field did not exist."""
    _write_cache(cache_dir)
    savecache = _reload_savecache()
    assert savecache._state["data"] is None
    assert savecache._state["schemaStale"] is True


def test_a_cache_from_a_newer_build_is_discarded_too(cache_dir):
    """
    Downgrades count. A rollback reading a *newer* payload has the same problem
    in the other direction — fields it does not understand, and possibly missing
    ones it needs.
    """
    _write_cache(cache_dir, schema=999)
    savecache = _reload_savecache()
    assert savecache._state["data"] is None
    assert savecache._state["schemaStale"] is True


def test_a_matching_cache_loads_normally(cache_dir):
    import parse_worker

    _write_cache(cache_dir, schema=parse_worker.SCHEMA_VERSION)
    savecache = _reload_savecache()
    assert savecache._state["data"] is not None
    assert savecache._state["schemaStale"] is False
    assert savecache.generation() == 1


def test_no_cache_at_all_is_not_flagged_stale(cache_dir):
    """
    A fresh install has never parsed, which is a different state from "your
    cache was thrown away" and must not borrow its message.
    """
    savecache = _reload_savecache()
    assert savecache._state["data"] is None
    assert savecache._state["schemaStale"] is False


# ─── Half two: something rebuilds it ──────────────────────


def test_status_says_why_there_is_no_data(cache_dir):
    _write_cache(cache_dir)
    savecache = _reload_savecache()
    status = savecache.status()
    assert status["hasData"] is False
    assert status["schemaStale"] is True


def test_recovery_forces_a_parse_even_though_parse_auto_is_off(cache_dir, monkeypatch):
    """
    The half whose absence broke a live server.

    `PARSE_AUTO=false` means "do not parse speculatively". Rebuilding a cache we
    just deleted is not speculative — there is nothing to serve, so the choice is
    one parse now or an empty dashboard indefinitely.
    """
    _write_cache(cache_dir)
    savecache = _reload_savecache()
    assert savecache.PARSE_AUTO is False

    calls = []
    monkeypatch.setattr(
        savecache, "request_parse",
        lambda force=False: calls.append(force) or {"started": True, "reason": "ok"},
    )
    savecache.recover_stale_schema()
    assert calls == [True], "recovery must force, not merely request"


def test_recovery_does_nothing_when_the_cache_was_fine(cache_dir, monkeypatch):
    import parse_worker

    _write_cache(cache_dir, schema=parse_worker.SCHEMA_VERSION)
    savecache = _reload_savecache()

    calls = []
    monkeypatch.setattr(
        savecache, "request_parse",
        lambda force=False: calls.append(force) or {"started": True},
    )
    assert savecache.recover_stale_schema() is None
    assert calls == []


def test_a_refused_recovery_leaves_the_flag_set(cache_dir, monkeypatch):
    """
    A deferred parse — a struggling game server, a missing Level.sav — must not
    clear the flag. The next start tries again, and until then the UI keeps
    saying why the world is empty rather than falling silent.
    """
    _write_cache(cache_dir)
    savecache = _reload_savecache()
    monkeypatch.setattr(
        savecache, "request_parse",
        lambda force=False: {"started": False, "reason": "server is busy"},
    )
    savecache.recover_stale_schema()
    assert savecache._state["schemaStale"] is True
    assert savecache.status()["schemaStale"] is True


def test_the_worker_stamps_the_schema_it_writes():
    """
    The two halves have to agree, and they live in different processes. If the
    worker stopped writing the field, every cache it produced would be discarded
    on the next boot — a permanent re-parse loop.
    """
    import parse_worker
    import savecache

    assert isinstance(parse_worker.SCHEMA_VERSION, int)
    assert savecache._cache_schema() == parse_worker.SCHEMA_VERSION
    assert savecache._cache_schema() != 0, (
        "a failed import here would silently discard every valid cache forever"
    )
