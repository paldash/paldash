"""
The three small reference bundles: raid bosses, base invaders, world presets.

Each carries a refusal that is worth pinning, because in every case a plausible
alternative reading exists and is wrong:

  * raid bosses have **no world position**, and inventing one repeats the
    tower-barrier mistake;
  * an invader grade band **cannot be turned into a per-base forecast**, because
    nothing establishes what a grade means in save terms;
  * the difficulty presets are a **cross-check** on the hand-made ones, not an
    automatic replacement.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gamedata  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh():
    gamedata._reset_cache()
    yield
    gamedata._reset_cache()


# ─── Raid bosses ─────────────────────────────────────────


def test_raid_bosses_carry_levels_and_rewards():
    bosses = gamedata.raid_bosses()
    assert len(bosses) == 11
    night = bosses["PalSummon_NightLady"]
    assert night["forms"][0]["speciesId"] == "RAID_NightLady"
    assert night["forms"][0]["level"] == 35


def test_no_raid_boss_carries_a_position():
    """
    They are altar-summoned. `boss_spawners` holds 90 placed field bosses and
    zero RAID_ ids, which is correct rather than a gap — and a coordinate here
    would be the `BP_LevelObject_TowerLockBarrier` mistake: a plausible category
    corresponding to nothing in the world.
    """
    for boss in gamedata.raid_bosses().values():
        assert "x" not in boss and "y" not in boss
        for form in boss["forms"]:
            assert "x" not in form and "y" not in form


def test_forms_are_counted_not_rows():
    """
    A summon row's `InfoList` can hold more than one boss. Row-counting is what
    briefly turned 90 field bosses into "159".
    """
    forms = [f for b in gamedata.raid_bosses().values() for f in b["forms"]]
    assert len(forms) >= len(gamedata.raid_bosses())
    assert all(f["level"] > 0 for f in forms)


def test_the_raid_and_field_boss_bundles_stay_disjoint():
    raid = {f["speciesId"] for b in gamedata.raid_bosses().values() for f in b["forms"]}
    field = {b["speciesId"] for b in gamedata.boss_spawners()}
    assert raid & field == set()
    assert all(s.startswith("RAID_") for s in raid)


# ─── Invaders ────────────────────────────────────────────


def test_invaders_carry_biome_grade_and_loot():
    data = gamedata.invaders()
    assert len(data["groups"]) == 44
    group = next(iter(data["groups"].values()))[0]
    assert group["gradeMin"] <= group["gradeMax"]
    assert group["biome"]


def test_every_attacker_has_a_reward_table():
    """
    The join, checked in the direction where failure has a consequence: an
    attacker with no rewards is a raid that drops nothing.
    """
    data = gamedata.invaders()
    assert set(data["groups"]) <= set(data["rewards"])


def test_spare_reward_tables_are_tolerated():
    """
    The game ships 32 reward tables with no attacker — mainland biomes the
    invader table does not carry. Harmless, and refusing over it would block the
    extraction because the game has extra data.
    """
    data = gamedata.invaders()
    assert len(set(data["rewards"]) - set(data["groups"])) == 32


def test_the_payload_says_a_grade_cannot_be_resolved_to_a_base():
    """
    The refusal that keeps this a reference table. Nothing establishes what
    `InvadeGrade` means in save terms, so a per-base forecast would be invented.
    """
    assert gamedata.invaders()["gradeMeaningKnown"] is False


# ─── World presets ───────────────────────────────────────


def test_the_four_difficulties_are_present_with_their_settings():
    presets = gamedata.world_presets()["presets"]
    assert set(presets) == {
        "EasyPreset", "NormalPreset", "HardPreset", "HardcorePreset"
    }
    assert presets["EasyPreset"]["difficulty"] == "Easy"
    assert len(presets["EasyPreset"]["settings"]) == 43


def test_the_difficulties_actually_differ():
    """A preset table where every row is identical would mean a bad read."""
    presets = gamedata.world_presets()["presets"]
    easy = presets["EasyPreset"]["settings"]
    hard = presets["HardPreset"]["settings"]
    assert easy != hard


def test_undecodable_values_are_excluded_rather_than_shipped():
    """
    A value `uassettable` could not walk comes back as `<WorldMode 9B>`. Shipping
    those as settings would put nonsense in a preset.
    """
    for preset in gamedata.world_presets()["presets"].values():
        for value in preset["settings"].values():
            assert isinstance(value, (int, float, bool))


# ─── Absence ─────────────────────────────────────────────


def test_missing_bundles_cost_their_panels_only(monkeypatch):
    for attr in ("RAIDBOSS_PATH", "INVADERS_PATH", "WORLDPRESETS_PATH"):
        monkeypatch.setattr(gamedata, attr, "/nonexistent/x.json.gz")
    gamedata._reset_cache()
    assert gamedata.raid_bosses() == {}
    assert gamedata.invaders() == {}
    assert gamedata.world_presets() == {}
