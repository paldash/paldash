"""
Per-field validation schema (Phase 7 foundation).

The schema is what stands between "the caller sent a number" and "the world
still loads". These tests pin the bounds, the rejections, and — most
importantly — the cross-field rule that catches edits which look applied but
are silently undone by the game on load.
"""

from __future__ import annotations

import pytest

import editschema


# ─── Shape ───────────────────────────────────────────────────────


def test_both_targets_are_describable():
    for target in ("player", "pal"):
        described = editschema.describe(target)
        assert described
        assert all("name" in f and "kind" in f for f in described)


def test_unknown_targets_are_refused():
    with pytest.raises(editschema.SchemaError, match="Unknown target"):
        editschema.fields_for("guild")


def test_melee_iv_is_not_editable():
    """
    Talent_Melee is in parser._TALENTS but appears on zero of the 1,905 Pals in
    the reference world — Palworld 1.0 has HP, Shot and Defense only. Exposing
    it would write a field the game never reads, which looks like a working
    edit and is not.
    """
    assert "ivs.melee" not in editschema.PAL_FIELDS
    assert set(editschema.IV_FIELDS) == {"hp", "shot", "defense"}


def test_bounds_come_from_the_bundled_data_not_constants():
    tech = editschema.PLAYER_FIELDS["technologyPoints"]

    assert tech.maximum == 1413          # gamedata.totals()
    assert editschema.PLAYER_FIELDS["ancientTechnologyPoints"].maximum == 185


def test_the_level_cap_is_the_playable_one_not_the_exp_table_length():
    """
    `palExpTable` has 100 entries and deriving the cap from it gave 100, which
    is wrong: Palworld 1.0 raised the cap from 65 to 80 and the table carries
    headroom past it. The reference world agrees — highest player 71, highest
    Pal 70.
    """
    assert editschema.PAL_FIELDS["level"].maximum == 80
    assert editschema.PLAYER_FIELDS["level"].maximum == 80

    assert editschema.validate("pal", {"level": 80})["ok"]
    assert not editschema.validate("pal", {"level": 81})["ok"]


# ─── Field validation ────────────────────────────────────────────


def test_a_valid_change_is_accepted():
    report = editschema.validate("pal", {"rank": 3})
    assert report["ok"], report["problems"]
    assert report["changes"] == {"rank": 3}


def test_unknown_fields_are_refused():
    report = editschema.validate("pal", {"isLegendary": True})

    assert not report["ok"]
    assert "not an editable field" in report["problems"][0]["problem"]


def test_empty_change_sets_are_refused():
    assert not editschema.validate("pal", {})["ok"]
    assert not editschema.validate("pal", None)["ok"]


@pytest.mark.parametrize("value", [0, 6, -1, 999])
def test_rank_outside_one_to_five_is_refused(value):
    assert not editschema.validate("pal", {"rank": value})["ok"]


@pytest.mark.parametrize("value", [0, 101, -5])
def test_ivs_outside_zero_to_one_hundred_are_refused(value):
    report = editschema.validate("pal", {"ivs.hp": value})
    if value == 0:
        assert report["ok"]  # 0 is a legal IV
    else:
        assert not report["ok"]


@pytest.mark.parametrize("value", [0, 81, 101, 9999])
def test_levels_outside_the_exp_table_are_refused(value):
    assert not editschema.validate("pal", {"level": value})["ok"]


def test_booleans_are_not_accepted_as_numbers():
    """`True` is an int in Python and would pass every range check unnoticed."""
    assert not editschema.validate("pal", {"rank": True})["ok"]
    assert not editschema.validate("pal", {"level": False})["ok"]


@pytest.mark.parametrize("value", ["3", 3.5, None, [3]])
def test_non_integers_are_refused(value):
    assert not editschema.validate("pal", {"rank": value})["ok"]


def test_technology_points_are_capped_at_what_exists():
    assert editschema.validate("player", {"technologyPoints": 1413})["ok"]
    assert not editschema.validate("player", {"technologyPoints": 1414})["ok"]


def test_gender_is_an_enum():
    assert editschema.validate("pal", {"gender": "Female"})["ok"]
    assert not editschema.validate("pal", {"gender": "Unknown"})["ok"]


def test_nicknames_are_length_limited():
    assert editschema.validate("pal", {"nickname": "Fluffy"})["ok"]
    assert not editschema.validate("pal", {"nickname": "x" * 200})["ok"]


# ─── Passive skills ──────────────────────────────────────────────


def test_at_most_four_passives():
    four = ["PAL_ALLAttack_up2", "PAL_ALLAttack_up1", "Legend", "PAL_CorporateSlave"]
    assert editschema.validate("pal", {"passiveSkills": four[:4]})["ok"]
    assert not editschema.validate("pal", {"passiveSkills": four + ["Noukin"]})["ok"]


def test_duplicate_passives_are_refused():
    report = editschema.validate("pal", {"passiveSkills": ["Legend", "Legend"]})
    assert not report["ok"]
    assert "duplicate" in report["problems"][0]["problem"].lower()


def test_unknown_passives_are_refused():
    report = editschema.validate("pal", {"passiveSkills": ["NotARealPassive"]})
    assert not report["ok"]
    assert "unknown passive" in report["problems"][0]["problem"].lower()


def test_an_empty_passive_list_is_allowed():
    assert editschema.validate("pal", {"passiveSkills": []})["ok"]


def test_unknown_species_are_refused():
    assert editschema.validate("pal", {"speciesId": "Sheepball"})["ok"]
    assert not editschema.validate("pal", {"speciesId": "NotAPal"})["ok"]


# ─── Cross-field: EXP must match level ───────────────────────────


def test_exp_below_the_level_band_is_allowed():
    """
    The rule is one-sided, and this is the half that measurement removed.

    A symmetric band check looked obviously right and was wrong: on the
    reference world 8 Pals sit *below* their level's band because that is
    exactly what a freshly caught Pal looks like — it arrives at its wild level
    with almost no EXP and the game leaves it there. Rejecting low EXP would
    refuse an edit for producing a state Palworld creates on its own.
    """
    report = editschema.validate("pal", {"level": 50}, current={"level": 1, "exp": 0})
    assert report["ok"], report["problems"]


def test_the_asymmetry_is_the_measured_one():
    """
    Above the band: 0 of 1,905 Pals and 0 of 5 players on the reference world.
    Below it: 8 Pals. The rule follows the data in both directions.
    """
    above = editschema.validate("pal", {"exp": _pal_total_exp(51)}, current={"level": 50, "exp": 0})
    below = editschema.validate("pal", {"exp": 0}, current={"level": 50, "exp": _pal_total_exp(50)})

    assert not above["ok"]
    assert below["ok"], below["problems"]


def test_exp_above_the_level_band_is_refused():
    current = {"level": 50, "exp": 0}
    report = editschema.validate("pal", {"exp": 999_999_999}, current=current)

    assert not report["ok"]
    assert "beyond level 50" in report["problems"][0]["problem"]


def test_a_consistent_level_and_exp_pair_is_accepted():
    report = editschema.validate("pal", {"level": 10, "exp": _pal_total_exp(10)}, current={})
    assert report["ok"], report["problems"]


def test_players_and_pals_use_different_exp_curves():
    """
    TotalEXP and PalTotalEXP diverge; using the wrong one silently produces
    edits the game reverts.
    """
    level = 50
    assert _player_total_exp(level) != _pal_total_exp(level)

    assert editschema.validate(
        "player", {"level": level, "exp": _player_total_exp(level)}, current={}
    )["ok"]
    assert not editschema.validate(
        "player", {"level": level, "exp": _pal_total_exp(level)}, current={}
    )["ok"]


def test_cross_field_rules_are_skipped_without_current_state_and_it_says_so():
    """Guessing at unknown current values would be worse than not checking."""
    report = editschema.validate("pal", {"level": 50})
    assert report["ok"]
    assert report["crossFieldChecked"] is False


def test_cross_field_rules_run_when_current_state_is_given():
    report = editschema.validate("pal", {"level": 1, "exp": 0}, current={"level": 1, "exp": 0})
    assert report["crossFieldChecked"] is True


def test_the_cap_still_has_an_exp_band_because_the_table_runs_past_it():
    """
    Level 80 is the cap but the table has entries to 100, so level 80 still has
    a real upper EXP bound rather than being open-ended.
    """
    assert not editschema.validate(
        "pal", {"level": 80, "exp": 999_999_999_999}, current={}
    )["ok"]
    assert editschema.validate(
        "pal", {"level": 80, "exp": _pal_total_exp(80)}, current={}
    )["ok"]


# ─── Diff ────────────────────────────────────────────────────────


def test_diff_reports_only_what_changes():
    current = {"level": 10, "rank": 1, "ivs": {"hp": 50}}
    changes = {"level": 10, "rank": 3, "ivs.hp": 90}

    out = {d["field"]: d for d in editschema.diff("pal", changes, current)}

    assert "level" not in out
    assert out["rank"]["before"] == 1 and out["rank"]["after"] == 3
    assert out["ivs.hp"]["before"] == 50 and out["ivs.hp"]["after"] == 90


def test_diff_labels_fields_for_display():
    out = editschema.diff("pal", {"rank": 3}, {"rank": 1})
    assert out[0]["label"] == "Condenser rank"


# ─── Helpers ─────────────────────────────────────────────────────


def _pal_total_exp(level: int) -> int:
    import gamedata
    return int(gamedata.load()["palExpTable"][str(level)]["PalTotalEXP"])


def _player_total_exp(level: int) -> int:
    import gamedata
    return int(gamedata.load()["palExpTable"][str(level)]["TotalEXP"])
