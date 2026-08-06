"""
Raid bosses — and the reward table that shipped empty for a whole release.

Against the bundled file, because the bug this pins was in the *extractor* and a
fixture would have passed happily beside it.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import gamedata  # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    gamedata._reset_cache()
    yield
    gamedata._reset_cache()


def test_every_raid_boss_carries_a_reward():
    """
    THE REGRESSION THIS EXISTS FOR. `_items` read `ItemId`/`StaticItemId`; the
    column is `ItemName` and it is an `FName` needing unwrapping. Every boss came
    out with an empty reward list — no error, and "drops nothing" reads as an
    ordinary answer, so it survived a release.

    A raid the game does not reward is not a thing, so an empty result across the
    board is a reader fault by definition.
    """
    bosses = gamedata.raid_bosses()
    assert len(bosses) == 11
    for key, boss in bosses.items():
        assert boss["rewards"] or boss["rewardsAnyOne"], key


def test_every_reward_item_resolves_in_the_catalogue():
    """The hard half of the asymmetric check — the catalogue is complete."""
    unknown = {
        r["itemId"]
        for boss in gamedata.raid_bosses().values()
        for r in (*boss["rewards"], *boss["rewardsAnyOne"])
        if not gamedata.item(r["itemId"])
    }
    assert unknown == set()


def test_the_row_key_is_the_summon_item():
    """
    `PalSummon_NightLady` is a real catalogue id — "Bellanoir's Slab" — so "what
    do I need to start this raid" is a lookup rather than an inference. Checked
    rather than assumed, because the whole panel leads with it.
    """
    bosses = gamedata.raid_bosses()
    named = [k for k in bosses if gamedata.item(k)]
    assert len(named) == len(bosses)
    assert gamedata.item_name("PalSummon_NightLady") == "Bellanoir's Slab"


def test_egg_weights_are_reported_as_unread_not_as_empty():
    """
    `EggPalIDAndWeight` is a `MapProperty` and `uassettable` decodes none. The
    old code iterated it as a list — over the characters of the string
    `"<MapProperty 98B>"` — and produced `[]` for every boss, which reads as
    "this raid drops no eggs": a claim about the game rather than about the
    reader.
    """
    for boss in gamedata.raid_bosses().values():
        assert boss["eggWeightsRead"] is False
        assert boss["eggWeights"] == []


def test_no_raid_boss_has_a_position_and_none_is_in_the_field_boss_bundle():
    """
    A raid boss is altar-summoned, so a table of *locations* has nothing to say
    about it — `boss_spawners.json.gz` carrying zero `RAID_` ids is correct
    rather than a gap. Inventing a marker would be the `TowerLockBarrier`
    mistake.
    """
    for boss in gamedata.raid_bosses().values():
        for form in boss["forms"]:
            assert "x" not in form and "y" not in form

    placed = {str(b.get("speciesId") or "") for b in gamedata.boss_spawners()}
    assert not any(s.upper().startswith("RAID_") for s in placed)


def test_forms_are_counted_not_rows():
    """
    `AGENTS.md`'s standing rule: a row's InfoList can hold more than one boss, and
    row-counting is what briefly turned 90 field bosses into 159.
    """
    forms = [f for b in gamedata.raid_bosses().values() for f in b["forms"]]
    assert len(forms) >= len(gamedata.raid_bosses())
    levels = [f["level"] for f in forms]
    assert min(levels) == 35 and max(levels) == 80


# ─── Base raids ──────────────────────────────────────────


def test_the_raid_table_makes_no_per_base_claim():
    """
    THE POINT OF THE WHOLE FEATURE'S SCOPE. Two joins would turn this into a
    forecast and neither exists:

    - a raid is bounded by an `InvadeGrade`, and nothing says what a grade is in
      save terms. Base level is the obvious candidate and is **not in the save at
      all** — `BaseCampSaveData` carries no level and neither does the palbox.
    - a base's biome is defined by `BP_PalBiomeTriggerBox` volumes placed in the
      world; `DT_WorldMapAreaData` carries only a `MsgID`.

    So the bundle says `gradeMeaningKnown: false`, and nothing may quietly start
    treating a grade as a level.
    """
    data = gamedata.invaders()
    assert data["gradeMeaningKnown"] is False
    for entries in data["groups"].values():
        for entry in entries:
            assert "baseLevel" not in entry
            assert "playerLevel" not in entry


def test_every_raid_reward_item_resolves():
    unknown = {
        r["itemId"]
        for rows in (gamedata.invaders().get("rewards") or {}).values()
        for r in rows
        if not gamedata.item(r["itemId"])
    }
    assert unknown == set()


def test_a_build_triggered_raid_is_carried_unresolved():
    """
    `ConditionBuildObjectId` names a structure whose presence triggers a raid —
    `Factory_Money` is the one the game ships. Carried as the raw id: nothing
    here has confirmed what the condition *means*, only that the column names it.
    """
    conditions = {
        str(e.get("conditionBuildObjectId") or "")
        for entries in gamedata.invaders()["groups"].values()
        for e in entries
    }
    assert "Factory_Money" in conditions
