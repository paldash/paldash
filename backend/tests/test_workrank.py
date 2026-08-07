"""
"Mining 3" hid a tenfold difference, and "no cap" was a documented negative.

Two halves, both settled by reading a bundle this project already ships.

**The cap.** `editschema` said no maximum was enforced "and that is measured
rather than lazy", citing `DT_GainWorkSuitabilityRankItem` having no rank column
and no other DataTable carrying one. Both true, and both about DataTables.
`BP_PalGameSetting` carries `WorkSuitabilityMaxRank = 10` in its class-default
object, which nobody had searched for this. That is the exact failure AGENTS.md
warns about in its own voice: a documented negative gets trusted and stops the
next person looking.

**The curve.** `CraftSpeeds` is `[0, 50, 70, 100, 140, 190, 260, 370, 510, 720,
1000]`. Rank 3 is 100 and rank 10 is 1000, and the UI showed a bare integer.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import editschema  # noqa: E402
import gamedata  # noqa: E402
import workrank  # noqa: E402


def test_the_cap_is_ten_and_comes_from_the_game():
    assert workrank.max_rank() == 10
    assert gamedata.game_setting("WorkSuitabilityMaxRank") == 10


def test_the_cap_is_read_not_hardcoded(monkeypatch):
    """
    A second copy is how the file and the code drift apart. Change the bundle's
    answer and the validator must follow.
    """
    monkeypatch.setattr(
        gamedata, "game_setting",
        lambda name, default=None: 4 if name == "WorkSuitabilityMaxRank" else default,
    )
    assert editschema.max_work_rank() == 4
    assert editschema._work_ranks_problem({"Mining": 5}) is not None
    assert editschema._work_ranks_problem({"Mining": 4}) is None


def test_an_unreadable_bundle_drops_the_bound_rather_than_guessing(monkeypatch):
    """
    **None means "no bound", not "no limit".** Refusing an edit against a ceiling
    we cannot cite would be worse than the old unbounded behaviour — the same
    posture `gamedata.server_limit()` takes for an INI it cannot read.
    """
    monkeypatch.setattr(gamedata, "game_setting", lambda name, default=None: None)
    assert editschema.max_work_rank() is None
    assert editschema._work_ranks_problem({"Mining": 99}) is None


def test_the_validator_refuses_above_the_cap_and_allows_it():
    assert editschema._work_ranks_problem({"Mining": 10}) is None
    problem = editschema._work_ranks_problem({"Mining": 11})
    assert problem and "10" in problem


def test_rank_zero_is_still_refused():
    """
    Unchanged and still measured: rank 0 appears on none of the 39 Pals across
    three worlds, so a zero is the parser's default rather than a stored value.
    """
    assert editschema._work_ranks_problem({"Mining": 0}) is not None


# ─── The curve ───


def test_the_curve_is_the_game_s_and_rank_three_is_the_anchor():
    curve = workrank._curve()
    assert curve == [0, 50, 70, 100, 140, 190, 260, 370, 510, 720, 1000]
    assert len(curve) == (workrank.max_rank() or 0) + 1, (
        "the curve must have an entry per rank including 0"
    )
    assert workrank.describe("Mining", 3)["relativeToRank3"] == 1.0
    assert workrank.describe("Mining", 10)["relativeToRank3"] == 10.0


def test_all_three_stated_work_types_agree():
    """
    **This is the evidence for applying it to the rest, and it is only
    evidence.** Three independent copies in the settings object, identical. If
    they ever diverge, `_curve()` returning the first one becomes wrong and this
    is what catches it.
    """
    curves = []
    for work in workrank.STATED:
        entry = gamedata.game_setting(f"WorkSuitabilityDefineData_{work}")
        curves.append(tuple(entry["CommonDefineData"]["CraftSpeeds"]))
    assert len(set(curves)) == 1, f"the stated curves diverged: {curves}"


def test_stated_is_true_only_for_the_three_the_game_names():
    """
    The load-bearing flag. Every other work type's data is in an opaque
    MapProperty, so its curve is an assumption — a well-supported one, and not
    the game saying so. A caller must be able to tell them apart.
    """
    for work in workrank.STATED:
        assert workrank.describe(work, 5)["stated"] is True
    for work in ("Transport", "Handcraft", "Watering", "Generate_Electricity"):
        assert workrank.describe(work, 5)["stated"] is False


def test_mining_gates_on_material_which_is_eligibility_not_speed():
    """
    A rank-2 miner cannot touch Iron at any speed. That is a harder rule than a
    multiplier, and it is why the material travels separately rather than being
    folded into the speed figure.
    """
    assert workrank.describe("Mining", 1)["material"] == "Stone"
    assert workrank.describe("Mining", 2)["material"] == "Copper"
    assert workrank.describe("Mining", 3)["material"] == "Iron"
    assert workrank.describe("Mining", 4)["material"] == "Platinum"
    # Rank 0 unlocks nothing, and `None` says so rather than meaning "unknown".
    assert workrank.describe("Mining", 0)["material"] is None


def test_a_transport_pal_below_rank_four_has_NO_pickup_range():
    """
    Not "slower at transporting" — zero. A bare 0 in a table reads as missing
    data, so the flag says which it is.
    """
    ranges = workrank.transport_range()
    assert ranges[:4] == [0.0, 0.0, 0.0, 0.0]
    for rank in (1, 2, 3):
        assert workrank.describe("Transport", rank)["pickupDisabled"] is True
    assert workrank.describe("Transport", 4)["pickupDisabled"] is False
    assert workrank.describe("Transport", 4)["pickupRange"] == 300.0


def test_collection_carries_a_drop_rate_that_is_not_the_speed():
    assert workrank.describe("Collection", 0)["dropRate"] == 0.0
    assert workrank.describe("Collection", 10)["dropRate"] == 5.5


def test_an_unreadable_bundle_costs_the_detail_and_nothing_else(monkeypatch):
    monkeypatch.setattr(gamedata, "game_setting", lambda name, default=None: None)
    assert workrank.describe("Mining", 5) == {}
    assert workrank.curve_table()["curve"] == []
    assert workrank.curve_table()["note"] == ""


def test_the_curve_table_says_what_it_is_assuming():
    table = workrank.curve_table()
    assert table["maxRank"] == 10
    assert table["statedFor"] == list(workrank.STATED)
    assert "assumed" in table["note"].lower()


def test_a_rank_beyond_the_curve_is_clamped_not_an_index_error():
    """A save holding a rank above the cap must render, not raise."""
    described = workrank.describe("Mining", 99)
    assert described["rank"] == 10
    assert described["speed"] == 1000
