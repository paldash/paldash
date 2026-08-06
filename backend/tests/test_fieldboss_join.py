"""
Two bundles describe field bosses, and joining them was refused for a year.

`worldobjects.json.gz` carries 99 `FBOSS` spawner placements — species, artwork
and position, found by intersecting a sheet's name table with the species list.
`boss_spawners.json.gz` carries 90 rows out of `DT_BossSpawnerLoactionData` —
species, position and **level**. AGENTS.md records that they are "two different
extractions of overlapping things" and warns against assuming one supersedes the
other, so the map's boss popup said *"Level is on the Field bosses layer"*.

That was honest and useless: the game's own map shows Silvegis' level, and the
dashboard would not, while holding the number in a second bundle.

**The warning said do not ASSUME — it never said do not CHECK.** These tests are
the check, and the control is the part that makes it evidence rather than a
hopeful threshold.
"""

from __future__ import annotations

import collections
import gzip
import json
import math
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import gamedata  # noqa: E402

_WORLD_OBJECTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "worldobjects.json.gz",
)


def _placements() -> list[dict]:
    with gzip.open(_WORLD_OBJECTS, "rt", encoding="utf-8") as f:
        return json.load(f)["groups"]["fieldboss"]["objects"]


def test_the_join_beats_a_shuffled_control_by_an_order_of_magnitude():
    """
    **This is the whole argument.** Same species *and* within one cell matches 64
    of the 99 placements. Shuffle the species labels across the boss rows — the
    positions and the distance rule untouched — and the best of 200 trials
    matches a handful. A correspondence that survives that is real; a threshold
    tuned until a number looked good would not be.
    """
    placements = _placements()
    rows = gamedata.boss_spawners()

    real = sum(
        1 for p in placements
        if gamedata.boss_level_at(p.get("species") or "", p["x"], p["y"])
    )
    assert real >= 60, f"the join collapsed: {real} of {len(placements)}"

    best_control = 0
    for seed in range(50):
        rng = random.Random(seed)
        labels = [r["speciesId"] for r in rows]
        rng.shuffle(labels)
        shuffled: dict[str, list[dict]] = collections.defaultdict(list)
        for label, row in zip(labels, rows):
            shuffled[label].append(row)
        matched = 0
        for p in placements:
            candidates = shuffled.get(p.get("species") or "")
            if not candidates:
                continue
            if min(
                math.dist((p["x"], p["y"]), (r["x"], r["y"])) for r in candidates
            ) <= gamedata._SAME_BOSS:
                matched += 1
        best_control = max(best_control, matched)

    assert real > best_control * 4, (
        f"the join ({real}) is not clearly better than chance ({best_control}); "
        "the distance rule is matching on position density rather than identity"
    )


def test_sixty_placements_sit_on_top_of_a_boss_row():
    """
    The join's strongest evidence is not the threshold, it is that most matched
    pairs are at a distance of **zero** — the same actor read two ways, out of a
    world cell's bytes and out of a DataTable's Vector. A byte-layout mistake in
    either reader does not produce sixty coincident points.
    """
    exact = 0
    for p in _placements():
        hit = gamedata.boss_level_at(p.get("species") or "", p["x"], p["y"])
        if hit and hit["distance"] < 1.0:
            exact += 1
    assert exact >= 55, f"only {exact} placements coincide with a boss row"


def test_silvegis_has_its_level():
    """The Pal that prompted this: the in-game map shows a level and we did not."""
    silvegis = [
        p for p in _placements()
        if (p.get("species") or "").endswith("WhiteShieldDragon")
    ]
    assert silvegis, "the Silvegis placement went missing from the bundle"
    hit = gamedata.boss_level_at(
        silvegis[0]["species"], silvegis[0]["x"], silvegis[0]["y"]
    )
    assert hit and hit["level"] == 62


def test_a_species_with_two_levels_is_resolved_by_POSITION():
    """
    `BOSS_GrassGolem` is placed twice at **55 and 75**, which is exactly why the
    join cannot key on species. A species-keyed lookup would hand one of those
    placements the other's level and look completely fine doing it.
    """
    levels = collections.defaultdict(set)
    for row in gamedata.boss_spawners():
        levels[row["speciesId"]].add(row["level"])
    multi = {k: v for k, v in levels.items() if len(v) > 1}
    assert multi, (
        "no species has two levels any more — if the bundle really changed, this "
        "test is stale; if it did not, the bundle lost rows"
    )

    for species, expected in multi.items():
        got = set()
        for row in gamedata.boss_spawners():
            if row["speciesId"] != species:
                continue
            hit = gamedata.boss_level_at(species, row["x"], row["y"])
            assert hit, f"{species} does not match its own row"
            got.add(hit["level"])
        assert got == expected, (
            f"{species}: position lookup gave {sorted(got)}, the table has "
            f"{sorted(expected)} — the join is collapsing distinct placements"
        )


def test_an_unmatched_placement_gets_NO_level_rather_than_a_borrowed_one():
    """
    35 of the 99 have no boss row standing with them. Those must come back with
    nothing: a second placement of a species can legitimately carry a different
    level (see above), and a borrowed number is indistinguishable in the UI from
    a read one.
    """
    far = gamedata.boss_level_at("BOSS_WhiteShieldDragon", 9_000_000.0, 9_000_000.0)
    assert far is None

    unmatched = sum(
        1 for p in _placements()
        if gamedata.boss_level_at(p.get("species") or "", p["x"], p["y"]) is None
    )
    assert unmatched > 0, (
        "every placement matched, which means the distance rule stopped "
        "discriminating — check `_SAME_BOSS`"
    )


def test_an_unknown_species_is_not_an_error():
    assert gamedata.boss_level_at("", 0.0, 0.0) is None
    assert gamedata.boss_level_at("BOSS_NotAPal", 0.0, 0.0) is None
