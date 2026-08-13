"""
The fullness ceiling that this project recorded as not existing.

AGENTS.md said "`fullStomach` is still unbounded — that one genuinely has no
constant", and `editschema` said the ceiling "is not stored anywhere in the
save". Only the second is true, and it was read as a general absence:
`DT_PalMonsterParameter.MaxFullStomach` is a column on all 753 species.

Asserted against the **shipped bundle**, not the extractor.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gamedata  # noqa: E402


def _cap(species_id: str):
    return (gamedata.pal_exact(species_id) or {}).get("maxFullStomach")


def test_every_species_carries_a_ceiling():
    pals = gamedata.load()["pals"]
    with_cap = [k for k, v in pals.items() if v.get("maxFullStomach")]
    assert len(with_cap) == len(pals) == 753


def test_the_range_is_the_games_own():
    assert _cap("SheepBall") == 100      # Lamball, the floor
    assert _cap("Alpaca") == 150         # Melpaca
    assert _cap("JetDragon") == 600      # Jetragon
    values = [v["maxFullStomach"] for v in gamedata.load()["pals"].values()]
    assert (min(values), max(values)) == (100, 730)


def test_it_is_read_per_form_and_exactly_one_alpha_differs():
    """
    `pal_exact`, not `pal` — but the reason is one species, not a pattern.

    An earlier note claimed `BOSS_IceHorse` was 620 against `IceHorse`'s 600.
    Both are 620; 600 is Jetragon's. Measured: **302 of 303 alpha/base pairs are
    identical** and the sole exception is `BOSS_YakushimaBoss001`.

    Pinned at 1 rather than "some", because "alphas have different caps" is a
    much larger claim than the data supports and is what the wrong figure
    implied.
    """
    pals = gamedata.load()["pals"]
    pairs = [
        (k, pals[k]["maxFullStomach"], pals[k[5:]]["maxFullStomach"])
        for k in pals if k.startswith("BOSS_") and k[5:] in pals
    ]
    assert len(pairs) == 303
    differing = [p for p in pairs if p[1] != p[2]]
    assert [p[0] for p in differing] == ["BOSS_YakushimaBoss001"]
    assert differing[0][1:] == (320, 240)


def test_an_npc_gets_no_ceiling_rather_than_zero():
    """
    99 of refworld's characters are NPCs with no species row. A zero
    denominator renders as infinitely hungry, which is a confident wrong answer
    where absent is the true one.
    """
    assert _cap("Male_Soldier") is None
    assert _cap("") is None
    assert _cap("NoSuchSpecies") is None
