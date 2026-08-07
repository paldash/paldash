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


def test_egg_weights_are_read_and_the_old_refusal_is_retired():
    """
    **This test used to assert the opposite, and both versions were right at the
    time.** `EggPalIDAndWeight` is a `MapProperty`; `uassettable` decoded none,
    so the extractor reported the field unread rather than shipping `[]` —
    because an empty egg table reads as "this raid drops no eggs", a claim about
    the game rather than about the reader. Before that it was worse: the code
    iterated the map as a list, walking the characters of the string
    `"<MapProperty 98B>"`, and produced `[]` for every boss silently.

    The map decoder landed 2026-08-07 and the premise expired. Kept as an
    assertion on the *new* answer rather than deleted, because a regression here
    would look exactly like the original bug.
    """
    for boss in gamedata.raid_bosses().values():
        assert boss["eggWeightsRead"] is True, (
            "a raid reporting its egg table unread means the reader lost a "
            "capability it had — that is the failure this pins"
        )


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


# ─── Two operator-reported bugs, 2026-08-07 ──────────────────────


def test_the_raid_egg_table_is_read_now_that_MapProperty_decodes():
    """
    **The refusal this replaces was correct and its premise expired.**
    `EggPalIDAndWeight` is a MapProperty, `uassettable` decoded none, so the
    extractor reported `eggWeightsRead: False` rather than shipping `[]` —
    because an empty egg table reads as "this raid drops no eggs", a claim about
    the game rather than about the reader. The map decoder landed 2026-08-07 and
    nothing re-ran the extractor, so the Progression tab showed raid rewards
    with the boss egg missing.
    """
    import gzip, json, os

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "raidbosses.json.gz")
    with gzip.open(path, "rt", encoding="utf-8") as f:
        bosses = json.load(f)["bosses"]

    assert all(b["eggWeightsRead"] for b in bosses.values()), (
        "every raid must report its egg table as read — an unread one is a "
        "reader regression, not a game fact"
    )
    with_eggs = [b for b in bosses.values() if b["eggWeights"]]
    assert len(with_eggs) == 9

    # Two entries per raid: the alpha form at 0.1 and the ordinary at 0.9.
    for boss in with_eggs:
        alphas = [e for e in boss["eggWeights"] if e["isBoss"]]
        assert len(alphas) == 1, f"{boss['id']} should offer exactly one alpha form"
        assert alphas[0]["weight"] == 0.1
        assert alphas[0]["speciesId"].startswith("BOSS_")


def test_an_empty_egg_map_is_READ_not_unread():
    """
    `YakushimaBoss002` and its `_2` ship `{}` — the game says those raids have
    no egg table. That is an answer, and reporting it as unread would conflate
    it with a reader failure, which is the distinction the missing ban list and
    the unparsed world already turn on.
    """
    import gzip, json, os

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "raidbosses.json.gz")
    with gzip.open(path, "rt", encoding="utf-8") as f:
        bosses = json.load(f)["bosses"]

    empty = [b for b in bosses.values() if not b["eggWeights"]]
    assert len(empty) == 2
    assert all(b["eggWeightsRead"] for b in empty), (
        "an empty map is the game's answer; only a non-map is unread"
    )


def test_raid_tiers_are_NAMED_not_humanised():
    """
    Reported: the `_2` raids showed as "Night Lady Dark 2" while their summon
    items were named correctly.

    `pal()` strips `RAID_` — right for alphas, since `BOSS_Alpaca` -> `Alpaca`
    exists — but the raid tiers exist ONLY in prefixed form, so stripping looks
    up `NightLady_Dark_2`, finds nothing, and humanises the id. Their own rows
    carried the name the whole time.
    """
    import gamedata

    assert gamedata.pal_name("RAID_NightLady_Dark_2") == "Bellanoir Libero (Raid)"
    assert gamedata.pal_name("RAID_YakushimaBoss002") == "Moon Lord"
    assert gamedata.pal_name("RAID_LegendDeer_2") == "Hartalis (Raid)"
    for species in ("RAID_NightLady_Dark_2", "RAID_KingBahamut_Dragon_2"):
        assert "Night Lady" not in gamedata.pal_name(species)


def test_the_fix_does_NOT_put_the_archive_s_Boss_suffix_back():
    """
    THE REASON THE FALLBACK ORDER IS NORMALISED-FIRST. 66 prefixed rows carry
    the bundled archive's own `(Boss)` editorialising in their name, and
    AGENTS.md is explicit that the game calls `BOSS_Alpaca` "Melpaca" and that
    `isBoss` travels separately. Preferring the exact row would have fixed six
    raid names and broken sixty-six alpha ones.
    """
    import gamedata

    for species in ("BOSS_Alpaca", "BOSS_JetDragon", "BOSS_ArmorWoodlouse",
                    "BOSS_KingCrab", "BOSS_CandleWitch"):
        assert "(Boss)" not in gamedata.pal_name(species)
    assert gamedata.pal_name("BOSS_Alpaca") == gamedata.pal_name("Alpaca")


def test_prefixed_forms_with_no_base_row_are_rescued():
    """The same fallback, on 40 rows that were humanised for no reason."""
    import gamedata

    assert gamedata.pal_name("BOSS_BadCatgirl") == "Nyafia"
    assert gamedata.pal_name("BOSS_CowPal") == "Mozzarina"
    assert gamedata.pal_name("BOSS_Kirin_Ice") == "Univolt Cryst"
