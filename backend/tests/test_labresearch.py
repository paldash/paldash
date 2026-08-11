"""
The Pal Lab research tree — 168 nodes nothing read until 2026-08-07.

Research is guild-wide and permanent, so it is the one base upgrade that
explains why two identical Pals produce differently on two different servers.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import labresearch  # noqa: E402


def test_the_catalogue_works_with_no_parsed_world():
    """
    168 nodes with prerequisites, costs and effects are worth reading on a fresh
    server. `known: False` says the progress half is absent rather than zero —
    a tree showing "0 of 168 complete" to somebody who has researched plenty
    would be a claim about their save, not about us.
    """
    tree = labresearch.tree()
    assert len(tree["nodes"]) == 168
    assert tree["known"] is False
    assert "completed" not in tree
    assert len(tree["byWork"]) == 9


def test_completion_is_a_COMPARISON_because_the_save_has_no_done_flag():
    """
    Every guild carries all 168 rows whether it has started them or not — one
    live guild's are all `0.0` — so a row's presence says nothing. Only
    `work_amount` against `RequiredWorkAmount` does.
    """
    nodes = {n["id"]: n for n in labresearch.tree()["nodes"]}
    required = nodes["Handcraft1"]["workAmount"]
    assert required == 50000.0

    done = labresearch.tree({"Handcraft1": required + 74.0})
    row = next(n for n in done["nodes"] if n["id"] == "Handcraft1")
    assert row["complete"] is True
    assert row["inProgress"] is False

    partial = labresearch.tree({"Handcraft1": required / 2})
    row = next(n for n in partial["nodes"] if n["id"] == "Handcraft1")
    assert row["complete"] is False
    assert row["inProgress"] is True

    untouched = labresearch.tree({"Handcraft1": 0.0})
    row = next(n for n in untouched["nodes"] if n["id"] == "Handcraft1")
    assert row["complete"] is False
    assert row["inProgress"] is False, "zero work is not started, not in progress"


def test_available_means_the_PREREQUISITE_is_done_and_nothing_about_materials():
    """
    A node whose parent is complete is unlocked. Whether the guild can afford
    its materials is a different question answered by base storage, and
    conflating them would claim a stock check this does not perform.
    """
    nodes = {n["id"]: n for n in labresearch.tree()["nodes"]}
    child = nodes["Handcraft1_2"]
    assert child["requires"] == "Handcraft1"

    blocked = labresearch.tree({"Handcraft1": 0.0})
    assert next(n for n in blocked["nodes"] if n["id"] == "Handcraft1_2")["available"] is False

    unlocked = labresearch.tree({"Handcraft1": 50000.0})
    assert next(n for n in unlocked["nodes"] if n["id"] == "Handcraft1_2")["available"] is True

    # A root has no prerequisite and is available from the start.
    assert next(n for n in unlocked["nodes"] if n["id"] == "Handcraft1")["available"] is False, (
        "already complete, so not 'available' to research again"
    )


def test_effects_reuse_the_passive_classifier_rather_than_a_second_mapping():
    """
    Eleven of the sixteen research effect types appear on NO passive and had
    rules added to `passiveeffects` for exactly this. A second mapping here
    could only ever disagree with that one.
    """
    nodes = {n["id"]: n for n in labresearch.tree()["nodes"]}
    effect = nodes["Handcraft1"]["effect"]
    assert effect["kind"] == "CraftSpeed"
    assert effect["value"] == 10.0
    assert effect["label"] == "work speed"
    assert effect["category"] == "work"


def test_a_technology_unlock_node_says_so_rather_than_showing_no_plus_zero():
    """
    Ten rows carry the game's own `EPalPassiveSkillEffectType::no` and grant no
    rate. The extractor normalises it to null; this must not render "no +0%".
    """
    nodes = {n["id"]: n for n in labresearch.tree()["nodes"]}
    unlock = nodes["Handcraft5"]
    assert unlock["subType"] == "TechnologyUnlock"
    assert unlock["effect"]["kind"] is None
    assert unlock["effect"]["label"] == "Unlocks a technology"


def test_work_types_get_the_game_s_names_not_internal_ids():
    """
    The bundled key is `display_name`, not `name`. Reading the wrong one labels
    every row with an internal id, which `/api/optimise/work` records getting
    wrong once already.
    """
    names = {n["work"]: n["workName"] for n in labresearch.tree()["nodes"]}
    assert names["Handcraft"] == "Handiwork"
    assert names["Deforest"] == "Lumbering"
    assert names["EmitFlame"] == "Kindling"


def test_an_unreadable_bundle_costs_the_tree_and_nothing_else(monkeypatch):
    monkeypatch.setattr(labresearch, "_bundle", {})
    tree = labresearch.tree({"Handcraft1": 999999.0})
    assert tree["nodes"] == []
    assert tree["known"] is False
