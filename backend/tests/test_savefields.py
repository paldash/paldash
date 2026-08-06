"""
The save-field index, and the one property of it that must never regress.

`docs/savefields.json` is generated from real worlds — `refworld` holds real
Steam IDs, player names and guild names, and the two server saves beside it are a
live deployment. The index is committed, so the privacy filter in
`mine-savefields.py` is not a convenience: it is the thing standing between a
regeneration and a repository full of somebody's identifiers.

These run against the **committed file**, not against the generator, for the same
reason `test_gametext.py` asserts on the shipped bundle: a test of the extractor
passes happily beside a bundle built before the filter existed.
"""

from __future__ import annotations

import json
import os
import re

import pytest

INDEX = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "docs", "savefields.json",
)

ZERO_GUID = "00000000-0000-0000-0000-000000000000"


@pytest.fixture(scope="module")
def index() -> dict:
    if not os.path.exists(INDEX):
        pytest.skip("docs/savefields.json not generated")
    with open(INDEX, encoding="utf-8") as f:
        return json.load(f)


def test_no_guid_from_a_real_world_is_in_the_index():
    """
    The blunt check, over the raw text rather than the parsed structure — a value
    that leaked into a key, a sample or a note is just as exposed as one in a
    field, and walking the structure would miss it.

    The zero GUID is exempt: it is a sentinel the parser writes, not an identity.
    """
    if not os.path.exists(INDEX):
        pytest.skip("docs/savefields.json not generated")
    with open(INDEX, encoding="utf-8") as f:
        blob = f.read()

    found = {
        g.lower() for g in re.findall(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", blob
        )
    }
    found.discard(ZERO_GUID)
    assert found == set(), f"real GUIDs in the committed index: {sorted(found)[:3]}"


def test_no_steam_shaped_uid_is_in_the_index():
    """
    A Palworld player uid is a Steam ID32 followed by zeros — `11a11a01-0000-…`.
    That shape is what `soloexport` matches uids by, and it is exactly what must
    not be published.
    """
    if not os.path.exists(INDEX):
        pytest.skip("docs/savefields.json not generated")
    with open(INDEX, encoding="utf-8") as f:
        blob = f.read()
    found = {
        u.lower()
        for u in re.findall(r"\b[0-9a-fA-F]{8}-0000-0000-0000-0{12}\b", blob)
    }
    found.discard(ZERO_GUID)
    assert found == set(), f"player uids in the committed index: {sorted(found)[:3]}"


def test_name_shaped_fields_have_their_values_withheld(index):
    """
    The filter is name-based, so the assertion is that it FIRED — a field called
    `guild_name` must be marked rather than sampled. Checking only for absent
    GUIDs would pass on an index where the filter had been removed and the worlds
    happened to be anonymous.
    """
    withheld = [
        path for path, worlds in index["fields"].items()
        for row in worlds.values() if row.get("valuesWithheld")
    ]
    assert len(withheld) > 50, "the privacy filter does not appear to have run"

    for path, worlds in index["fields"].items():
        leaf = path.rsplit(".", 1)[-1].strip("[]").lower()
        if any(w in leaf for w in ("uid", "guid", "player", "nick", "steam")):
            for row in worlds.values():
                assert "sample" not in row, f"{path} carries samples"


def test_the_index_records_which_worlds_it_came_from(index):
    """
    A single-world index cannot distinguish the schema from what one save
    happened to contain — which is how `BossSpawnerSaveData`, present only in the
    oldest of three saves, would have been called absent.
    """
    assert len(index["worlds"]) >= 2
    assert "differsBetweenWorlds" in index


def test_ambiguous_names_are_excluded_from_the_unread_list(index):
    """
    `readBy` is a string-literal match. Names like `id`, `name` and `value`
    collide with everything, so they are neither trusted as read nor reported as
    unread — a false "nothing reads `name`" would poison the list this exists to
    produce.
    """
    ambiguous = set(index["ambiguousNames"])
    for path in index["unreadPaths"]:
        assert path.rsplit(".", 1)[-1].strip("[]") not in ambiguous


def test_fixed_width_blobs_are_flagged(index):
    """
    `byteLengthConstant` is the property that made `WorkerDirector` (118 bytes,
    container id at offset 98) and `GuildItemStorage` (20 bytes, id at offset 0)
    readable. It must survive regeneration, because it is the only thing marking
    which opaque runs are worth a measured offset.
    """
    constant = [
        path for path, worlds in index["fields"].items()
        for row in worlds.values() if row.get("byteLengthConstant")
    ]
    assert constant, "no fixed-width blobs recorded — the length stats are missing"
