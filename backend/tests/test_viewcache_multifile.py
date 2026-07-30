"""
`viewcache.per_files` — a value derived from several bundled artifacts.

Added for the Paldeck, which reads both `gamedata.json.gz` and
`habitats.json.gz`. Rebuilding it measured 20 ms and it is entirely static, so
it was being recomputed on every listing request *and* every detail request.

The test that matters is the last one: nesting two `per_file` calls looks like it
would work and does not, because the outer entry holds the inner's result and
therefore never notices the inner file changing. The stamps must be compared as
one tuple.
"""

import os

import pytest

import viewcache


@pytest.fixture
def files(tmp_path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text("1")
    b.write_text("2")
    viewcache.clear()
    return str(a), str(b)


def test_builds_once_then_serves_from_cache(files):
    calls = []

    def build():
        calls.append(1)
        return "value"

    assert viewcache.per_files("k", list(files), build) == "value"
    assert viewcache.per_files("k", list(files), build) == "value"
    assert len(calls) == 1


@pytest.mark.parametrize("which", (0, 1))
def test_a_change_to_any_input_rebuilds(files, which):
    """The whole point: either file moving must invalidate."""
    calls = []
    viewcache.per_files("k", list(files), lambda: calls.append(1))

    target = files[which]
    with open(target, "w") as f:
        f.write("changed, and longer so the size differs too")
    # mtime resolution is coarse enough that a same-second write can look
    # unchanged; nudge it explicitly rather than sleeping.
    stat = os.stat(target)
    os.utime(target, (stat.st_atime + 10, stat.st_mtime + 10))

    viewcache.per_files("k", list(files), lambda: calls.append(1))
    assert len(calls) == 2


def test_an_unstattable_input_builds_without_caching(files, tmp_path):
    """
    Same rule as `per_file`: with no stamp there is no way to notice a change,
    and serving a value that can never go stale is worse than redoing the work.
    """
    calls = []
    paths = [files[0], str(tmp_path / "missing.json")]
    for _ in range(3):
        viewcache.per_files("k", paths, lambda: calls.append(1))
    assert len(calls) == 3


def test_different_input_sets_do_not_share_an_entry(files, tmp_path):
    c = tmp_path / "c.json"
    c.write_text("3")
    assert viewcache.per_files("k", list(files), lambda: "AB") == "AB"
    assert viewcache.per_files("k", [files[0], str(c)], lambda: "AC") == "AC"


def test_two_values_from_the_same_files_do_not_collide(files):
    """
    The bug this key exists for. Keying on paths alone made the Paldeck listing
    and its siblings index share an entry — the second caller was handed the
    first's value, and a `.get()` on a list is a 500. It passed in isolation and
    failed only once both had been requested, which is the worst shape a caching
    bug can have.
    """
    a = viewcache.per_files("entries", list(files), lambda: ["a", "b"])
    b = viewcache.per_files("index", list(files), lambda: {"a": 1})
    assert a == ["a", "b"]
    assert b == {"a": 1}
    # And again, now that both are warm.
    assert viewcache.per_files("entries", list(files), lambda: "rebuilt") == ["a", "b"]
    assert viewcache.per_files("index", list(files), lambda: "rebuilt") == {"a": 1}


def test_nested_per_file_would_miss_the_inner_change(files):
    """
    Documents *why* this function exists rather than composing `per_file`.

    Nesting caches the inner result inside the outer entry, so a change to the
    inner file alone never invalidates anything. This test asserts the broken
    behaviour of the tempting alternative, so nobody refactors back to it.
    """
    outer, inner = files
    calls = []

    def nested():
        return viewcache.per_file(inner, lambda: (calls.append(1), "v")[1])

    viewcache.per_file(outer, nested)

    with open(inner, "w") as f:
        f.write("inner changed substantially")
    stat = os.stat(inner)
    os.utime(inner, (stat.st_atime + 10, stat.st_mtime + 10))

    viewcache.per_file(outer, nested)
    assert len(calls) == 1, "nesting noticed the inner change; the docstring is now wrong"

    # per_files, given the same two inputs, does notice.
    calls.clear()
    viewcache.per_files("k", [outer, inner], lambda: calls.append(1))
    with open(inner, "w") as f:
        f.write("inner changed again, differently")
    stat = os.stat(inner)
    os.utime(inner, (stat.st_atime + 20, stat.st_mtime + 20))
    viewcache.per_files("k", [outer, inner], lambda: calls.append(1))
    assert len(calls) == 2
