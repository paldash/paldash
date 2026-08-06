"""
Named progression checklists — and the denominators this refuses to invent.

Runs against the shipped bundles, like `test_itemsource.py` and for the same
reason: a fixture would happily pass while `progression.json.gz` shipped without
the boss-encounter names it needs.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import gamedata  # noqa: E402
import progresscheck  # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    gamedata._reset_cache()
    yield
    gamedata._reset_cache()


# ─── Tower bosses ────────────────────────────────────────


def test_the_game_names_the_towers_and_this_uses_its_names():
    """
    `TowerBossDefeatFlag` is keyed on a localisation key. Humanising it gives
    "Grass Boss"; the game's own text table says "Rayne Syndicate Tower".
    """
    result = progresscheck.tower_bosses(["BOSS_BATTLE_NAME_GrassBoss"])
    assert [h["name"] for h in result["have"]] == ["Rayne Syndicate Tower"]


def test_there_are_eight_towers_and_the_map_agrees():
    """
    The count check, and it is worth its own test because the two sources are
    unrelated: the text table is the client pak's localisation, the fast-travel
    points come from the world cells. A category whose size disagrees with what
    the game has is wrong however plausible its rows read.
    """
    battles = gamedata.progression().get("bossBattles") or {}
    towers = [k for k, v in battles.items() if v["kind"] == "tower"]
    entrances = [
        entry for entry in (gamedata.load().get("fastTravel") or {}).values()
        if gamedata.fast_travel_kind(str(entry.get("name") or "")) == "tower"
    ]
    assert len(towers) == 8
    assert len(entrances) == 8


def test_the_king_whale_arena_is_not_scored_as_a_boss():
    """
    `BOSS_BATTLE_NAME_KingWhaleRoom` is "Eternal Sea" — the arena, not the
    encounter. Counting the whole text table would put a room in the
    denominator.
    """
    result = progresscheck.tower_bosses([])
    ids = {row["id"] for row in result["missing"]}
    assert "BOSS_BATTLE_NAME_KingWhaleRoom" not in ids
    assert "BOSS_BATTLE_NAME_GrassBoss" in ids


def test_a_name_the_game_withholds_is_flagged_not_repaired():
    """
    Two encounters carry `？？？` in the game's own table — a deliberate spoiler
    placeholder, not a decode failure. It travels as `nameHidden` so the UI can
    say so instead of printing full-width question marks or, worse, humanising
    the key and inventing a name the game refused to give.
    """
    hidden = [
        row for row in progresscheck.tower_bosses([])["missing"] if row["nameHidden"]
    ]
    assert len(hidden) == 2
    assert all(row["name"] == "？？？" for row in hidden)
    assert all(row["kind"] == "endgame" for row in hidden)


# ─── Field bosses: two key kinds in one flag map ─────────


def test_field_bosses_split_because_the_flag_map_holds_two_kinds_of_key():
    """
    Measured on the reference world: 82 distinct keys, 59 spawner ids and 23
    `BOSS_`-prefixed NPC ids. Neither resolves as the other.
    """
    result = progresscheck.field_bosses(
        ["yamijima_IceLand_pink_D_BOSS", "BOSS_Hunter_Rifle"]
    )
    assert result["pals"]["obtained"] == 1
    assert result["humans"]["obtained"] == 1
    assert result["pals"]["have"][0]["speciesId"] == "BOSS_Horus_Water"


def test_the_human_boss_total_is_not_invented_from_the_npc_catalogue():
    """
    THE POINT OF THIS TEST. The catalogue lists 34 `BOSS_` NPCs and adding that
    to the 90 Pal spawners would give a confident "124 field bosses" — on
    evidence that includes `BOSS_DarkTrader`, a merchant, and a quest NPC. So
    `of` is None and the source says `discovered`.
    """
    result = progresscheck.field_bosses(["BOSS_Hunter_Rifle"])
    assert result["humans"]["of"] is None
    assert result["humans"]["totalSource"] == "discovered"


def test_one_spawner_listed_twice_is_one_checkbox():
    """
    `remainsIsland_1_GrassGolem_FBOSS` has two rows, level 55 and 75. The defeat
    flag keys on the spawner, so taking the 90-row count as the denominator
    would leave every player permanently one short of completion.
    """
    result = progresscheck.field_bosses([])
    assert result["pals"]["of"] == 89
    golem = [
        row for row in result["pals"]["missing"]
        if row["id"] == "remainsIsland_1_GrassGolem_FBOSS"
    ]
    assert len(golem) == 1
    assert golem[0]["level"] == 55 and golem[0]["levelMax"] == 75


# ─── Regions: the denominator that did not exist ─────────


def test_areas_found_finally_has_a_real_denominator():
    """
    `areasFound` sat in `reference_totals.json`'s `unverified` list, so the tab
    could say "92 discovered" against nothing at all. `DT_WorldMapAreaData` has
    123 rows and every one resolves to a display name.
    """
    result = progresscheck.areas_found(["Grass_001"])
    assert result["of"] == 123
    assert result["obtained"] == 1
    assert not any(row["nameIsInternal"] for row in result["missing"])


def test_the_region_join_folds_case_because_one_row_disagrees():
    """
    The save writes `BOSS_KingWhale`; `DT_WorldMapAreaData` says
    `Boss_KingWhale`. One row of 104 — an exact join drops it silently while
    everything else looks fine, which is how a case bug survives review.
    """
    result = progresscheck.areas_found(["BOSS_KingWhale"])
    assert result["unlisted"] == []
    assert result["obtained"] == 1


def test_region_names_come_from_the_game_not_from_the_id():
    names = {row["id"]: row["name"] for row in progresscheck.areas_found([])["missing"]}
    assert names["Grass_001"] == "Windswept Island"


# ─── Fast travel ─────────────────────────────────────────


def test_fast_travel_joins_on_the_guid_not_the_readable_id():
    """
    The bundle is keyed on the instance GUID and each entry ALSO carries an `id`
    (`WorldTree_MiddleBoss_1`). The save's unlock flags name the GUID; joining
    on `id` matches nothing.
    """
    guid = next(iter(gamedata.load()["fastTravel"]))
    result = progresscheck.fast_travel([guid])
    assert result["obtained"] == 1
    assert result["of"] == 174


# ─── What it refuses ─────────────────────────────────────


def test_dungeons_cleared_says_why_it_has_no_checklist():
    """
    `FixedDungeonClearCount` is empty on every save examined, so there is no
    observed key shape to join dungeon names against. Reported as unavailable
    with a reason rather than shipped as an empty checklist, which would read as
    "you have cleared none of 23".
    """
    result = progresscheck.describe({})["dungeonsCleared"]
    assert result["available"] is False
    assert "FixedDungeonClearCount" in result["reason"]
    assert "of" not in result


def test_an_id_the_bundle_does_not_list_is_reported_not_absorbed():
    """
    A key we cannot name still means the player did the thing. Counting it as
    obtained is right; folding it silently into `have` would hide that the
    bundle is incomplete.
    """
    result = progresscheck.tower_bosses(["BOSS_BATTLE_NAME_SomethingNew"])
    assert result["unlisted"] == ["BOSS_BATTLE_NAME_SomethingNew"]
    assert result["obtained"] == 1
    assert result["have"] == []


def test_describe_takes_the_progress_dict_rather_than_fetching_one():
    """
    This is discovery data. A module that could fetch a player's progress itself
    would be one refactor away from going around the filter that decides who may
    see it — the same separation `_scope_pals` enforces.
    """
    empty = progresscheck.describe({})
    assert empty["towerBosses"]["obtained"] == 0
    assert empty["areasFound"]["of"] == 123
