"""
The request-path caches.

A cache is only worth having if it is *correct at the moment the underlying thing
changes*, so most of these tests are about invalidation rather than about speed.
The two keys are tested separately because they answer different questions: a
parse generation, and one file's stamp.
"""

from __future__ import annotations

import os
import time

import pytest

import savecache
import savefiles
import viewcache


@pytest.fixture(autouse=True)
def clean_cache():
    viewcache.clear()
    yield
    viewcache.clear()


@pytest.fixture
def parsed(monkeypatch):
    """Pretend a parse has happened, and hand back a way to bump the generation."""
    state = {"gen": 1}
    monkeypatch.setattr(savecache, "generation", lambda: state["gen"])
    return state


# ─── derived(): keyed on the parse ───────────────────────────────


def test_a_derived_view_is_built_once_per_parse(parsed):
    calls = []

    def build():
        calls.append(1)
        return ["value"]

    assert viewcache.derived("k", build) == ["value"]
    assert viewcache.derived("k", build) == ["value"]
    assert viewcache.derived("k", build) == ["value"]
    assert len(calls) == 1


def test_a_new_parse_rebuilds_it(parsed):
    calls = []
    build = lambda: (calls.append(1), len(calls))[1]  # noqa: E731

    assert viewcache.derived("k", build) == 1
    parsed["gen"] = 2
    assert viewcache.derived("k", build) == 2


def test_keys_do_not_collide(parsed):
    assert viewcache.derived("a", lambda: "A") == "A"
    assert viewcache.derived("b", lambda: "B") == "B"
    assert viewcache.derived("a", lambda: "changed") == "A"


def test_nothing_is_cached_before_the_first_parse(monkeypatch):
    """
    Generation 0 means no parse has completed. Caching against it would pin
    whatever an empty world produced until the first parse landed.
    """
    monkeypatch.setattr(savecache, "generation", lambda: 0)
    calls = []
    build = lambda: (calls.append(1), "x")[1]  # noqa: E731

    viewcache.derived("k", build)
    viewcache.derived("k", build)
    assert len(calls) == 2


def test_a_parse_finishing_mid_build_is_not_cached(monkeypatch):
    """
    The builder ran against generation 1 but generation 2 landed while it worked,
    so what it produced describes a world that is already gone. Storing it would
    serve the old world until the *next* parse, which is strictly worse than
    building again.
    """
    state = {"gen": 1}
    monkeypatch.setattr(savecache, "generation", lambda: state["gen"])

    def build():
        state["gen"] = 2          # a parse completes underneath us
        return "stale"

    assert viewcache.derived("k", build) == "stale"   # this caller still gets it
    assert viewcache.derived("k", lambda: "fresh") == "fresh"   # but it was not kept


# ─── per_file(): keyed on the value AND the file ─────────────────


def test_a_file_view_is_built_once(tmp_path):
    path = tmp_path / "player.sav"
    path.write_bytes(b"one")
    calls = []

    def build():
        calls.append(1)
        return path.read_bytes()

    assert viewcache.per_file("k", str(path), build) == b"one"
    assert viewcache.per_file("k", str(path), build) == b"one"
    assert len(calls) == 1


def test_two_views_of_one_file_do_not_collide(tmp_path):
    """
    THE REGRESSION THAT MADE `key` REQUIRED. `crafting` and `itemsource` both
    cached an index built from `economy.json.gz`, keyed on the path alone —
    whichever endpoint ran first seeded the entry and the other was handed a
    dict of the wrong shape. In production that was a 500 on every crafting
    tree once the Items panel had loaded; in isolation both passed.
    """
    path = tmp_path / "economy.json.gz"
    path.write_bytes(b"shared source")

    assert viewcache.per_file("crafting", str(path), lambda: {"byProduct": 1}) \
        == {"byProduct": 1}
    assert viewcache.per_file("itemsource", str(path), lambda: {"recipes": 2}) \
        == {"recipes": 2}
    # And each key keeps its own value on the second read.
    assert viewcache.per_file("crafting", str(path), lambda: "REBUILT") \
        == {"byProduct": 1}


def test_rewriting_the_file_invalidates_it(tmp_path):
    """
    This is what makes the player-save cache safe without an invalidation call:
    the editor writing, the game autosaving and a backup restore all move the
    mtime, and none of them has to know this cache exists.
    """
    path = tmp_path / "player.sav"
    path.write_bytes(b"one")
    build = lambda: path.read_bytes()  # noqa: E731

    assert viewcache.per_file("k", str(path), build) == b"one"

    time.sleep(0.01)
    path.write_bytes(b"two!")          # different size as well as mtime
    os.utime(path, (time.time() + 1, time.time() + 1))

    assert viewcache.per_file("k", str(path), build) == b"two!"


def test_a_missing_file_is_built_but_never_cached(tmp_path):
    """No stamp means no way to notice a change, so caching it would be a leak."""
    missing = str(tmp_path / "gone.sav")
    calls = []
    build = lambda: (calls.append(1), None)[1]  # noqa: E731

    viewcache.per_file("k", missing, build)
    viewcache.per_file("k", missing, build)
    assert len(calls) == 2
    assert viewcache.stats()["cachedFiles"] == 0


def test_the_file_cache_is_bounded(tmp_path, monkeypatch):
    """Players who have ever played is unbounded in principle; the cache is not."""
    monkeypatch.setattr(viewcache, "MAX_FILES", 4)
    for i in range(10):
        p = tmp_path / f"{i}.sav"
        p.write_bytes(b"x")
        viewcache.per_file("k", str(p), lambda: i)
    assert viewcache.stats()["cachedFiles"] == 4


# ─── The player-save index ───────────────────────────────────────


def test_the_player_index_matches_across_uid_spellings(tmp_path):
    """
    Level.sav says `22b22b02-0000-...`; the file is `22B22B02000...0.sav`. Both
    have to resolve, which is the whole reason the index is normalised rather
    than a plain filename lookup.
    """
    world = tmp_path / "world"
    players = world / "Players"
    players.mkdir(parents=True)
    (players / "22B22B02000000000000000000000000.sav").write_bytes(b"x")

    dashed = "22b22b02-0000-0000-0000-000000000000"
    undashed = "22B22B02000000000000000000000000"
    for spelling in (dashed, undashed, undashed.lower()):
        assert savefiles.get_player_sav_path(spelling, str(world)) is not None, spelling


def test_a_new_player_save_is_picked_up(tmp_path):
    """A first-time player must not need a restart to appear."""
    world = tmp_path / "world"
    players = world / "Players"
    players.mkdir(parents=True)

    uid = "AABBCCDD000000000000000000000000"
    assert savefiles.get_player_sav_path(uid, str(world)) is None

    (players / f"{uid}.sav").write_bytes(b"x")
    os.utime(players, (time.time() + 1, time.time() + 1))
    assert savefiles.get_player_sav_path(uid, str(world)) is not None


def test_a_traversing_uid_is_still_rejected(tmp_path):
    """
    The index is a lookup table, not a permission check. Sanitisation runs on the
    raw uid before it, and must keep running there.
    """
    world = tmp_path / "world"
    (world / "Players").mkdir(parents=True)
    (world / "secret.sav").write_bytes(b"x")

    assert savefiles.get_player_sav_path("../secret", str(world)) is None
    assert savefiles.get_player_sav_path("a/b", str(world)) is None
