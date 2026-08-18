"""
Egg-move pools (#139) — pinned against the SHIPPED bundle, not the extractor,
so a bundle regenerated before a filter existed cannot pass on the strength
of the generator's current code (the savefields lesson).
"""

import gzip
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import eggmoves  # noqa: E402


def _bundle() -> dict:
    with gzip.open(eggmoves.DATA_PATH, "rt", encoding="utf-8") as f:
        return json.load(f)


def test_every_pool_move_is_randomly_inheritable():
    """
    The two-table agreement is the whole verification: a pool move the skill
    table marks IgnoreRandomInherit would mean one of the two reads drifted.
    47 of 47 distinct moves at extraction.
    """
    data = _bundle()
    inheritable = set(data["inheritableSkills"])
    strays = {
        w for pool in data["pools"].values() for w in pool
        if w not in inheritable
    }
    assert not strays, f"pool moves outside the inheritable set: {sorted(strays)[:5]}"


def test_pools_are_meaningfully_sized():
    data = _bundle()
    assert len(data["pools"]) >= 250
    sizes = [len(v) for v in data["pools"].values()]
    assert min(sizes) >= 1 and max(sizes) <= 103


def test_species_resolution_strips_boss_and_folds_case():
    # The save writes `Sheepball`; an alpha arrives as `BOSS_Anubis`. Both
    # must land on the base species' pool — the eight-real-Pals lesson.
    direct = eggmoves.for_species("Anubis")
    assert direct and direct["moves"], "Anubis should have a pool"
    assert eggmoves.for_species("BOSS_Anubis")["species"] == direct["species"]
    assert eggmoves.for_species("anubis")["species"] == direct["species"]


def test_no_pool_is_none_not_empty():
    # A species with no pool is a real answer; an invented empty list would
    # read as "this Pal hatches with nothing extra", which nothing supports.
    assert eggmoves.for_species("NoSuchSpecies__") is None


def test_payload_claims_a_pool_and_never_odds():
    found = eggmoves.for_species("Anubis")
    assert found["poolOnly"] is True
    # The quote-don't-mechanise guard, walked over every string in the
    # payload: no wording that turns a pool into a probability.
    forbidden = ("chance of", "guaranteed", "% of", "will know", "always rolls")
    def walk(node):
        if isinstance(node, str):
            low = node.lower()
            for marker in forbidden:
                assert marker not in low, f"odds language in payload: {node!r}"
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(found)


def test_moves_carry_display_names():
    found = eggmoves.for_species("Anubis")
    unnamed = [m for m in found["moves"] if not m["name"]]
    assert not unnamed
    # Sorted strongest-first, so the ceiling is visible without scrolling.
    powers = [m["power"] or 0 for m in found["moves"]]
    assert powers == sorted(powers, reverse=True)
