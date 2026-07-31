"""
Breeding routes will not propose a pair the player cannot make.

`possible_offspring` always enforced gender. `breeding_paths` and
`indirect_targets` did not, and a docstring said so — which was survivable while
the planner ran over a whole server's Pals and stopped being so when it was
scoped to one palbox, where owning a single gender of a species is common.

THE RULE, AND WHY IT ONLY BINDS ON OWNED SPECIES
------------------------------------------------
Parents are not consumed by breeding, so any pair that works once works again.
An **intermediate** species can therefore be re-bred until it comes out the
gender the next step needs, which makes it effectively available in both. An
**owned** species cannot: if your only Relaxaurus is male, no amount of breeding
turns it female.

So a route is blocked exactly when a step pairs two species you already own whose
genders do not oppose — or self-pairs a species you own only one gender of.
Anything stricter would refuse achievable plans, which is the more annoying half
of being wrong here.
"""

from __future__ import annotations

import pytest

import breeding


def pal(species: str, gender: str, instance: str = "x") -> dict:
    return {
        "instanceId": instance, "speciesId": species, "gender": gender,
        "level": 10, "rank": 1, "ivs": {}, "passiveSkills": [], "isBoss": False,
        "nickname": "",
    }


@pytest.fixture(autouse=True)
def _needs_tables():
    try:
        breeding._db()
    except breeding.BreedingDataError:
        pytest.skip("breeding tables unavailable")


# ─── gender_pool ──────────────────────────────────────────


def test_gender_pool_counts_both_sexes():
    pool = breeding.gender_pool([
        pal("SheepBall", "Male", "a"),
        pal("SheepBall", "Female", "b"),
        pal("ChickenPal", "Male", "c"),
    ])
    assert pool["SheepBall"] == {"male": 1, "female": 1}
    assert pool["ChickenPal"] == {"male": 1, "female": 0}


def test_gender_pool_canonicalises_species():
    """
    The same boundary rule `_owned_pool` follows. The save spells eight species
    differently from the pair table, and an exact match here would read as "you
    own no female Sheepball" and block routes that are perfectly achievable.
    """
    pool = breeding.gender_pool([pal("Sheepball", "Female")])
    assert breeding.canonical_species("Sheepball") in pool


def test_an_unknown_gender_counts_as_neither():
    """
    Guessing would produce a plan the player cannot follow, and this is the half
    of the planner where being wrong is expensive — a plan gets acted on, a
    listing only gets read.
    """
    pool = breeding.gender_pool([pal("SheepBall", "Unknown")])
    assert pool[breeding.canonical_species("SheepBall")] == {"male": 0, "female": 0}


# ─── _pairable ────────────────────────────────────────────


def test_two_owned_males_cannot_pair():
    genders = {"A": {"male": 1, "female": 0}, "B": {"male": 1, "female": 0}}
    assert breeding._pairable("A", "B", genders) is False


def test_opposite_genders_pair():
    genders = {"A": {"male": 1, "female": 0}, "B": {"male": 0, "female": 1}}
    assert breeding._pairable("A", "B", genders) is True


def test_a_self_pair_needs_both_genders_of_that_species():
    assert breeding._pairable("A", "A", {"A": {"male": 2, "female": 0}}) is False
    assert breeding._pairable("A", "A", {"A": {"male": 1, "female": 1}}) is True


def test_an_unowned_species_is_unrestricted():
    """
    The load-bearing case. A species absent from the pool is an intermediate the
    player will breed, and re-rolling it until the gender is right is exactly
    what a player does — so constraining it would refuse real plans.
    """
    genders = {"A": {"male": 1, "female": 0}}
    assert breeding._pairable("A", "IntermediateNotOwned", genders) is True
    assert breeding._pairable("IntermediateNotOwned", "AlsoNotOwned", genders) is True


# ─── The route search ─────────────────────────────────────


def _two_species_route():
    """
    Find a target one breeding away from two distinct species, so the test has a
    concrete pair to make single-gendered. Picked from the table rather than
    hardcoded, because a game update can change any given pair.
    """
    pairs = breeding._breeding()["pairs"]
    for key, child in pairs.items():
        a, b = key.split("+", 1) if "+" in key else (None, None)
        if a and b and a != b and child not in (a, b):
            return a, b, child
    pytest.skip("no two-species pair in the table")


def test_a_reachable_target_stays_reachable_with_both_genders():
    a, b, child = _two_species_route()
    pals = [pal(a, "Male", "1"), pal(b, "Female", "2")]
    result = breeding.breeding_paths(
        child, [a, b], genders=breeding.gender_pool(pals)
    )
    assert result["reachable"] is True
    assert result["genderAware"] is True


def test_the_same_target_is_refused_when_both_parents_are_male():
    a, b, child = _two_species_route()
    pals = [pal(a, "Male", "1"), pal(b, "Male", "2")]
    result = breeding.breeding_paths(
        child, [a, b], genders=breeding.gender_pool(pals)
    )
    # It may still be reachable by some longer route that avoids the blocked
    # pair — the guarantee is only that the *blocked pair itself* is not used.
    if result["reachable"]:
        used = {(s["parentA"]["internalName"], s["parentB"]["internalName"])
                for s in result["steps"]}
        assert (a, b) not in used and (b, a) not in used
    else:
        assert "gender" in result["reason"].lower()


def test_omitting_genders_keeps_the_old_species_only_behaviour():
    """
    Not a deprecated path: asking "is this species reachable at all" is the right
    question when the pool is a whole server's Pals, where both genders of
    everything are almost always present.
    """
    a, b, child = _two_species_route()
    result = breeding.breeding_paths(child, [a, b])
    assert result["reachable"] is True
    assert result["genderAware"] is False


def test_an_unreachable_target_says_which_kind_of_unreachable():
    """
    "Reachable by species but not with your genders" and "not reachable at all"
    call for completely different actions — catch the opposite gender, versus
    give up. Reporting one as the other sends the player after the wrong thing.
    """
    a, b, child = _two_species_route()
    both = breeding.breeding_paths(
        child, [a, b], genders=breeding.gender_pool([pal(a, "Male"), pal(b, "Female")])
    )
    if not both["reachable"]:
        pytest.skip("this pair is not a one-step route in the current table")

    blocked = breeding.breeding_paths(
        child, [a, b],
        genders=breeding.gender_pool([pal(a, "Male", "1"), pal(b, "Male", "2")]),
        max_depth=1,
    )
    assert blocked["reachable"] is False
    assert "gender" in blocked["reason"].lower()


def test_indirect_targets_reports_whether_it_was_gender_aware():
    a, b, _ = _two_species_route()
    pals = [pal(a, "Male", "1"), pal(b, "Female", "2")]
    aware = breeding.indirect_targets([a, b], genders=breeding.gender_pool(pals))
    assert aware["genderAware"] is True
    assert breeding.indirect_targets([a, b])["genderAware"] is False
