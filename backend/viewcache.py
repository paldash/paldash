"""
Memoisation for work the request path was repeating without needing to.

Reading the dashboard is overwhelmingly repeated reads of data that has not
changed: the same 1,905 Pals get the same names attached on every page load, and
the same five player saves get decompressed and parsed again for every roster,
progress and discovery request. None of that changes between parses.

TWO KEYS, BECAUSE THERE ARE TWO REASONS THINGS CHANGE
-----------------------------------------------------
`derived()` keys on `savecache.generation()` — anything computed from Level.sav.
The counter moves only when a parse completes, and *replacing the parse result is
itself the invalidation*, so there is no `invalidate()` call anywhere that
someone can forget to add next to a new write.

`per_file()` keys on `(size, mtime)` of the file it came from. A player save
rewritten by the game, by the player editor, or by a backup restore invalidates
itself, for the same reason: the thing that changes the data is the thing that
changes the key. Note this is the same stamp `savefiles.read_sav_bytes` uses for
its torn-read guard, which is not a coincidence — if a stamp is stable enough to
trust a read against, it is stable enough to key a cache on.

WHAT IS DELIBERATELY NOT CACHED HERE
------------------------------------
Authorisation and privacy decisions. Measured on a 20-account database, the
entire per-request privacy filter — the SQLite read, the rank comparisons and
the uid normalisation — costs about **60 microseconds**, against roughly 12 ms
to attach names to a world's Pals. There is no speed argument that pays for the
risk, and the failure mode is not a slow page but a player who asked to be
hidden still being shown to the peer they hid from. A cache whose staleness
window is a privacy leak needs a much better reason than 60 microseconds.

MUTATION
--------
Callers get the cached object itself, not a copy — copying a 1.3 MB payload per
request would give back most of what the cache saves. Everything built through
here is therefore built fresh and treated as read-only afterwards: endpoints that
narrow a cached list (`?owner=`, `?category=`) filter into a new list and never
edit an element in place.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import OrderedDict
from typing import Any, Callable, Optional

import savecache

logger = logging.getLogger(__name__)

# Player saves are small (7–18 KB parsed on the reference world) and bounded by
# how many people have ever played on the server, but "ever played" is unbounded
# in principle, so the per-file cache is an LRU rather than a plain dict.
MAX_FILES = 128

_lock = threading.Lock()
_derived: dict[str, tuple[int, Any]] = {}
_files: "OrderedDict[str, tuple[tuple[int, float], Any]]" = OrderedDict()


def _stamp(path: str) -> Optional[tuple[int, float]]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_size, st.st_mtime


def derived(key: str, build: Callable[[], Any]) -> Any:
    """
    A view computed from the current parse, built at most once per parse.

    `build` runs outside the lock. Two requests arriving together during a cold
    cache may both build it, which wastes one computation and is much better than
    holding a lock across a 12 ms build while every other request queues behind
    it. The result is identical either way — these builders are pure functions of
    the parse data.
    """
    generation = savecache.generation()
    if not generation:
        return build()          # nothing parsed yet; nothing stable to key on

    with _lock:
        hit = _derived.get(key)
        if hit is not None and hit[0] == generation:
            return hit[1]

    value = build()

    with _lock:
        # Re-read the generation: a parse may have finished while we were
        # building, in which case what we hold is already stale and storing it
        # would pin the old world until the *next* parse.
        if savecache.generation() == generation:
            _derived[key] = (generation, value)
    return value


def per_file(path: str, build: Callable[[], Any]) -> Any:
    """
    A value computed from one file, rebuilt when the file changes.

    An unstattable path is built and *not* cached: without a stamp there is no
    way to notice the file changing, and serving a value that can never go stale
    is worse than doing the work.
    """
    stamp = _stamp(path)
    if stamp is None:
        return build()

    with _lock:
        hit = _files.get(path)
        if hit is not None and hit[0] == stamp:
            _files.move_to_end(path)
            return hit[1]

    value = build()

    with _lock:
        # Stamp again from the same read that produced `value`: if the file moved
        # while we were parsing it, the value describes neither version reliably,
        # so cache nothing and let the next request try again.
        if _stamp(path) == stamp:
            _files[path] = (stamp, value)
            _files.move_to_end(path)
            while len(_files) > MAX_FILES:
                _files.popitem(last=False)
    return value


def per_files(paths: list[str], build: Callable[[], Any]) -> Any:
    """
    A value computed from several files, rebuilt when *any* of them changes.

    `per_file`'s multi-input sibling, for views derived from more than one
    bundled artifact — the Paldeck reads both `gamedata.json.gz` and
    `habitats.json.gz`, and keying on either alone would serve a stale answer
    when the other was replaced.

    Nesting two `per_file` calls does **not** work here and the reason is worth
    stating: the outer entry would hold the inner's result, so a change to the
    inner file alone would never invalidate the outer. The stamps have to be
    compared as one tuple.

    Same rule as `per_file` about unstattable paths: if any file cannot be
    stamped, build without caching rather than pin a value that can never go
    stale. That is also what makes the "Reload data packs" action work — it
    replaces files on disk, and the stamps move with them, so there is no
    invalidation call for anyone to forget.
    """
    stamps = tuple(_stamp(p) for p in paths)
    if any(s is None for s in stamps):
        return build()

    key = "\0".join(paths)
    with _lock:
        hit = _files.get(key)
        if hit is not None and hit[0] == stamps:
            _files.move_to_end(key)
            return hit[1]

    value = build()

    with _lock:
        if tuple(_stamp(p) for p in paths) == stamps:
            _files[key] = (stamps, value)
            _files.move_to_end(key)
            while len(_files) > MAX_FILES:
                _files.popitem(last=False)
    return value


def stats() -> dict[str, Any]:
    """Cache occupancy, for the health endpoint."""
    with _lock:
        return {
            "generation": savecache.generation(),
            "derivedViews": len(_derived),
            "cachedFiles": len(_files),
        }


def clear() -> None:
    """Drop everything. For tests, and for a settings change that invalidates names."""
    with _lock:
        _derived.clear()
        _files.clear()
