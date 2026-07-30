"""
Pals reachable only via an intermediate, with the shortest route to each.

The offspring view answers "what can I breed right now". This is the question
after it, and the invariant that matters is that `depth` really is the *shortest*
route rather than merely a route — a planner that shows a four-step path to
something obtainable in two is worse than showing nothing.
"""

import pytest

import breeding


pytestmark = pytest.mark.skipif(
    not breeding.data_available(), reason="breeding data not bundled"
)

# A small, real starting set. Deliberately not the whole palbox: with enough
# species owned, almost everything is one step away and the interesting cases
# vanish.
OWNED = ["SheepBall", "ElecCat", "ChickenPal", "Deer"]


@pytest.fixture(scope="module")
def result():
    return breeding.indirect_targets(OWNED)


def test_reports_the_owned_pool_it_searched_from(result):
    assert result["ownedSpecies"] == len(OWNED)
    assert result["maxDepth"] == breeding.MAX_PATH_DEPTH


def test_finds_targets_beyond_one_step(result):
    assert result["targets"], "a four-species pool should reach something indirectly"


def test_never_lists_a_one_step_child(result):
    """Depth-1 children have their own view; repeating them buries the rest."""
    direct = {o["internalName"] for o in breeding.possible_offspring(
        [{"speciesId": s, "gender": "Male"} for s in OWNED]
        + [{"speciesId": s, "gender": "Female"} for s in OWNED]
    )}
    listed = {t["internalName"] for t in result["targets"]}
    assert not (listed & direct)
    assert all(t["depth"] >= 2 for t in result["targets"])


def test_step_count_matches_reported_depth(result):
    """`depth` is a claim about the route; the route has to back it up."""
    for target in result["targets"]:
        assert len(target["steps"]) == target["depth"], target["internalName"]


def test_routes_start_from_owned_and_chain_to_the_target(result):
    """Every parent must be owned or produced by an earlier step in the same route."""
    owned = {breeding.canonical_species(s) for s in OWNED}
    for target in result["targets"]:
        available = set(owned)
        for step in target["steps"]:
            a = step["parentA"]["internalName"]
            b = step["parentB"]["internalName"]
            assert a in available, f"{target['internalName']}: {a} not yet available"
            assert b in available, f"{target['internalName']}: {b} not yet available"
            available.add(step["child"]["internalName"])
        assert target["steps"][-1]["child"]["internalName"] == target["internalName"]


def test_depth_is_the_shortest_route_not_merely_a_route(result):
    """
    BFS visits in generation order and records a child only on first sight, so
    the depth it reports must agree with an independent search for that target.
    """
    for target in result["targets"][:12]:
        path = breeding.breeding_paths(target["internalName"], OWNED)
        assert path["reachable"] is True
        assert len(path["steps"]) == target["depth"], target["internalName"]


def test_sorted_fewest_steps_first(result):
    depths = [t["depth"] for t in result["targets"]]
    assert depths == sorted(depths)


def test_targets_carry_display_data(result):
    """It renders a list, so names and icons have to be usable straight off it."""
    for target in result["targets"]:
        assert target["name"]
        assert target["name"] != target["internalName"] or target["known"] is False


def test_unreleased_pals_are_not_offered_as_goals(result):
    """Their pair data stays usable as an intermediate; the goal list excludes them."""
    assert not any(breeding.is_unreleased(t["internalName"]) for t in result["targets"])


def test_no_owned_pals_means_no_targets_rather_than_an_error():
    empty = breeding.indirect_targets([])
    assert empty["targets"] == []
    assert empty["ownedSpecies"] == 0


def test_unknown_species_are_ignored_not_fatal():
    mixed = breeding.indirect_targets(["SheepBall", "NotARealPal", "ElecCat"])
    assert mixed["ownedSpecies"] == 2
