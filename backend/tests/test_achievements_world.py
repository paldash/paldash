"""
The counter mapping, validated against five real players.

`test_achievements.py` pins the policy. This is the evidence — and it is the
only evidence there is that `PalDex` is driven by `TribeCaptureCount` rather
than by something else that happens to look similar.

THE CHECK: nobody may have **claimed** a tier their counter has not reached.
Claimed comes from the save (`NPCAchivementRewardFlag`, exact) and the threshold
comes from the DataTable, so the two are independent of each other and of the
counter. If the mapping were wrong, a player would be claiming a 100-species
reward with a counter reading 40.
"""

import glob
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytestmark = [pytest.mark.integration]

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="module")
def players():
    paths = sorted(glob.glob(os.path.join(ROOT, "refworld", "Players", "*.sav")))
    if not paths:
        pytest.skip("refworld/ not present — integration test skipped")
    try:
        import palsav  # noqa: F401
    except ImportError:
        pytest.skip("palsav not installed")
    import parser as pparser

    out = []
    for path in paths:
        gvas = pparser.load_gvas(path)
        if gvas is not None:
            out.append((os.path.basename(path)[:8], pparser.extract_player_progress(gvas)))
    if not out:
        pytest.skip("no player save parsed")
    return out


def test_every_claimed_key_is_a_real_table_row(players):
    """
    26 of 26 across five players. This is what makes `claimed` a **join** rather
    than an inference — the save names the row outright, so no category-to-
    counter guess is involved in the half that matters.
    """
    import achievements

    known = {t["id"] for c in achievements.catalogue().values() for t in c["tiers"]}
    total = 0
    for name, progress in players:
        claimed = progress.get("achievementsClaimed") or []
        total += len(claimed)
        assert set(claimed) <= known, f"{name} claims an unknown row"
    assert total == 26


def test_nobody_claimed_a_tier_their_counter_has_not_reached(players):
    """
    **THE VALIDATION OF THE COUNTER MAPPING.** Claimed is read from the save and
    the threshold from the DataTable; the counter is the only guess. A player
    holding `PalDex_10` (100 species) with a counter reading 40 would refute it.

    Zero violations across all five players and both mapped categories.
    """
    import achievements

    violations = []
    for name, progress in players:
        result = achievements.for_player(progress)
        for category, entry in result.items():
            if entry["value"] is None:
                continue
            for tier in entry["tiers"]:
                if tier["state"] == "claimed" and entry["value"] < tier["requireCount"]:
                    violations.append(
                        f"{name} {tier['id']} needs {tier['requireCount']} "
                        f"but {entry['counter']} reads {entry['value']}"
                    )
    assert violations == []


def test_the_species_counter_is_no_longer_zero_for_everyone(players):
    """
    `TribeCaptureCount` is a plain **int**, and `_flag_entries` returns `[]` for
    anything that is not a list — so `speciesCaptured` read `{total: 0}` on
    every player from the day it was added until 2026-08-12. Nothing rendered
    it, which is why it survived.

    Real figures on the reference world: 210, 149, 128, 109, 8 and one empty
    save.
    """
    values = sorted(
        (p.get("speciesCaptured") or {}).get("total") or 0 for _, p in players
    )
    assert values.count(0) <= 1, "only the empty save should read zero"
    assert max(values) > 100

    # ...and a scalar counter has no per-key breakdown, so `distinct` is None
    # rather than a number copied from `total`.
    for _, progress in players:
        entry = progress.get("speciesCaptured") or {}
        if entry.get("total"):
            assert entry.get("distinct") is None


def test_the_species_counter_never_exceeds_the_paldeck(players):
    """
    An independent sanity check on the mapping from a *different* field.
    `PaldeckUnlockFlag` includes Pals seen but not caught, so it must be greater
    than or equal to captured species on every player: 211/210, 157/149,
    129/128, 109/109. A counter that outran the Paldeck would not be species.
    """
    for name, progress in players:
        species = (progress.get("speciesCaptured") or {}).get("total") or 0
        paldeck = (progress.get("paldeck") or {}).get("obtained") or 0
        assert species <= paldeck, f"{name}: {species} species > {paldeck} paldeck"


def test_bossdefeat_reports_no_progress_on_a_real_player(players):
    """
    The refusal, exercised against real data rather than a fixture: one player
    has claimed `BossDefeat_1` and the category still reports no value and no
    locked tiers, because no counter is established for it.
    """
    import achievements

    saw_claim = False
    for _, progress in players:
        boss = achievements.for_player(progress)["BossDefeat"]
        assert boss["value"] is None
        assert boss["hasProgress"] is False
        assert not any(t["state"] == "locked" for t in boss["tiers"])
        saw_claim = saw_claim or boss["claimed"] > 0
    assert saw_claim, "a player should have claimed a BossDefeat tier"
