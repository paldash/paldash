"""
`passiveeffects.trainer_buffs` — what a party grants the player (#137).

Against the SHIPPED bundle, like every passive test here. The zero needs its
positive control: refworld's 25 party Pals genuinely grant nothing, which is
indistinguishable from a filter that can never fire — so the control plants
passives the bundle itself marks trainer-facing and must see them, and a
pal-self passive that must NOT appear.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import passiveeffects  # noqa: E402


def _pal(passives, name="Probe"):
    return {"passiveSkills": passives, "nickname": name, "speciesName": name}


def test_a_trainer_facing_passive_is_reported():
    rows = passiveeffects.trainer_buffs([_pal(["GiveAFire"])])
    assert rows, "GiveAFire targets the trainer in the bundle and must appear"
    assert rows[0]["palName"] == "Probe"
    assert rows[0]["whenLabel"], "the condition must travel — riding-only is not always-on"


def test_a_pal_self_passive_is_not_a_trainer_buff():
    # Legend is +20% attack/defence TO THE PAL; reporting it as a player buff
    # would be the exact mislabelling #137 exists to avoid.
    rows = passiveeffects.trainer_buffs([_pal(["Legend"])])
    assert rows == []


def test_rows_are_per_effect_never_summed():
    # Two Vanguard carriers = two rows. Summing would assert a stacking rule
    # no game file states.
    rows = passiveeffects.trainer_buffs(
        [_pal(["GiveAFire"], "A"), _pal(["GiveAFire"], "B")])
    assert len(rows) == 2
    assert {r["palName"] for r in rows} == {"A", "B"}


def test_unknown_passives_grant_nothing_and_do_not_crash():
    assert passiveeffects.trainer_buffs([_pal(["NotARealPassive"])]) == []
