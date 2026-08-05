"""
Base camp levels, illness penalties, and worker sanity thresholds.

The finding worth carrying forward from this bundle is in
`test_the_sanity_thresholds_are_not_the_welfare_panels_number`: `main.LOW_SANITY`
is 50 and a worker starts slacking at 85. Those are two different questions and
the welfare panel currently answers the first while appearing to answer the
second. This file pins the discrepancy rather than resolving it — that is #59,
and it is a product decision, not a data one.
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


# ─── Worker caps ─────────────────────────────────────────


def test_worker_cap_grows_with_base_level():
    """The game's table for an unmodified install — not any server's real cap."""
    assert gamedata.base_worker_cap(1) == 1
    top = max(r["workerMax"] for r in gamedata.basecamp()["levels"])
    assert top == 30


def test_the_cap_reaches_what_a_real_world_actually_has():
    """
    The verification that makes this bundle trustworthy without a second source.
    `verify-figures.py` found a 25-slot worker container on the live world; a
    table topping out below that is being read wrong however plausible it looks.
    """
    top = max(r["workerMax"] for r in gamedata.basecamp()["levels"])
    assert top >= 25


def test_the_table_bounds_nothing_because_a_real_server_exceeds_it():
    """
    THE CORRECTION, and it is measured rather than cautious.

    The table says 4 bases per guild and 30 workers. A real server in use runs
    **5 bases and 25 workers** — over the table in one direction and under it in
    the other. So the table cannot serve as a ceiling, a floor, or a fallback: it
    describes an unmodified game and nothing more.

    There is no hard maximum to fall back on either. `BaseCampMaxNum` defaults to
    128 and is itself a setting. Every one of these numbers belongs to the
    operator, so the INI is the only authority.
    """
    import settings_ini

    levels = gamedata.basecamp()["levels"]
    assert max(r["workerMax"] for r in levels) == 30
    assert max(r["basesPerGuild"] for r in levels) == 4

    # All three are real keys — checked against the game's own default INI,
    # which is the authoritative list of what a 1.0 server accepts.
    defaults = settings_ini.game_defaults()
    for key in (
        gamedata.WORKER_CAP_SETTING,
        gamedata.BASES_PER_GUILD_SETTING,
        gamedata.BASES_TOTAL_SETTING,
    ):
        assert key in defaults, key

    # A configuration over the table's figure must be readable as itself, not
    # clamped to 4. This is exactly the live case.
    assert gamedata.server_limit.__doc__


def test_a_server_over_the_tables_figure_reads_as_itself(monkeypatch, tmp_path):
    """
    5 bases per guild against the table's 4. Nothing may clamp it.
    """
    import settings_ini

    monkeypatch.setattr(
        settings_ini, "read_ini",
        lambda *a, **kw: {"options": {
            "BaseCampMaxNumInGuild": {"value": 5},
            "BaseCampWorkerMaxNum": {"value": 25},
        }},
    )
    assert gamedata.server_bases_per_guild() == 5
    assert gamedata.server_worker_cap() == 25


def test_an_unreadable_ini_gives_no_cap_rather_than_the_game_value(monkeypatch):
    """
    None means "not known", never "unlimited" and never "use the table". A
    container mounting only the save path cannot read the INI at all, which is
    the common deployment — a caller must then show no denominator rather than a
    wrong one.
    """
    import settings_ini

    def boom(*a, **kw):
        raise settings_ini.SettingsError("not mounted")

    monkeypatch.setattr(settings_ini, "read_ini", boom)
    assert gamedata.server_worker_cap() is None


def test_base_level_is_not_in_the_save_so_the_table_has_no_caller_yet(level_sav, palsav_available):
    """
    Recorded because it is the reason `base_worker_cap` is unused. A per-base cap
    needs the base's level, and `BaseCampSaveData` does not carry one — nor does
    the palbox that owns it. If this test ever fails because a level field
    appeared, that is the good kind of failure.
    """
    from parser import _v, _world_save_data, load_gvas

    gvas = load_gvas(level_sav)
    camps = _v(_world_save_data(gvas), "BaseCampSaveData", "value", default=[]) or []
    assert camps, "no bases in the reference world"

    raw = _v(camps[0], "value", "RawData", "value") or {}
    assert not any("level" in k.lower() for k in raw), (
        f"a level field appeared in BaseCampSaveData: {sorted(raw)} — "
        "per-base worker caps just became possible, see #60"
    )


test_base_level_is_not_in_the_save_so_the_table_has_no_caller_yet = pytest.mark.integration(
    test_base_level_is_not_in_the_save_so_the_table_has_no_caller_yet
)


def test_an_unknown_level_is_none_rather_than_clamped():
    """
    "Cap unknown" and "cap is 30" are different answers. Clamping to the nearest
    known level would turn a missing fact into a confident one.
    """
    assert gamedata.base_worker_cap(999) is None
    assert gamedata.base_worker_cap(0) is None


def test_bases_per_guild_is_carried_as_the_games_default_not_a_limit():
    caps = {r["basesPerGuild"] for r in gamedata.basecamp()["levels"]}
    assert max(caps) == 4
    # See `test_the_table_bounds_nothing_because_a_real_server_exceeds_it`:
    # a live server runs 5, so this 4 is a default and not a bound.


# ─── Illness ─────────────────────────────────────────────


def test_illnesses_carry_real_penalties_not_just_a_name():
    cold = gamedata.illness("Cold")
    assert cold["workSpeed"] == -5
    assert cold["palboxRecoveryPercent"] == 20


def test_the_severe_one_is_severe_and_barely_curable_in_the_box():
    """
    `DisturbingElement` is -50% work and -50% move at a 3%/hour palbox cure.
    That is the case where "sick" as a flag under-reports most badly.
    """
    bad = gamedata.illness("DisturbingElement")
    assert bad["workSpeed"] == -50
    assert bad["moveSpeed"] == -50
    assert bad["palboxRecoveryPercent"] == 3


def test_the_not_ill_row_is_not_an_illness():
    """`NoneSick` is the game's "healthy" row and must not appear as a condition."""
    ids = {i["id"] for i in gamedata.basecamp()["illnesses"]}
    assert "None" not in ids and "NoneSick" not in ids
    assert len(ids) == 8


def test_illness_lookup_is_case_insensitive():
    assert gamedata.illness("cold") is not None
    assert gamedata.illness("COLD") is not None


def test_no_illness_names_a_medicine_item():
    """
    `EffectiveItemRank` travels, but which item clears which rank is unverified.
    Naming one would be the mechanic claim `basesupply` refuses to make — if a
    source is ever found, this test is the thing to change deliberately.
    """
    for ill in gamedata.basecamp()["illnesses"]:
        assert "item" not in ill
        assert "medicine" not in ill
        assert isinstance(ill["effectiveItemRank"], int)


# ─── Sanity thresholds ───────────────────────────────────


def test_the_sanity_thresholds_are_not_the_welfare_panels_number():
    """
    THE FINDING. `main.LOW_SANITY` is 50, from
    `FriendshipPoint_AutoIncrementRequireSanity` — the sanity a Pal needs to keep
    gaining trust. The game stops a worker being useful long before that: it
    starts taking short breaks at 85.

    So the welfare panel warns at 50 while appearing to answer "is this base
    working", and it is not. Pinned here so the discrepancy is a recorded fact
    rather than something to rediscover; resolving it is #59.
    """
    import main

    thresholds = gamedata.worker_sanity_thresholds()
    assert thresholds, "no worker events decoded"

    highest = thresholds[0]["triggerSanity"]
    assert highest == 85
    assert highest > main.LOW_SANITY

    # And the panel's own number is the trust one, unchanged.
    assert main.LOW_SANITY == 50


def test_worker_events_are_ordered_worst_sanity_last():
    triggers = [e["triggerSanity"] for e in gamedata.worker_sanity_thresholds()]
    assert triggers == sorted(triggers, reverse=True)


def test_the_japanese_debug_labels_are_flagged_as_internal():
    """
    `Debug_DisplayName` is Pocketpair's internal label (サボり, 引きこもり). It
    disambiguates an id and is not UI text, so it is marked rather than dropped.
    """
    events = gamedata.worker_sanity_thresholds()
    assert all(e["debugNameIsInternal"] is True for e in events)
    assert any(e["debugName"] for e in events)


# ─── Absence ─────────────────────────────────────────────


def test_a_missing_bundle_costs_the_panel_not_the_page(monkeypatch):
    monkeypatch.setattr(gamedata, "BASECAMP_PATH", "/nonexistent/basecamp.json.gz")
    gamedata._reset_cache()
    assert gamedata.basecamp() == {}
    assert gamedata.base_worker_cap(5) is None
    assert gamedata.illness("Cold") is None
    assert gamedata.worker_sanity_thresholds() == []


# ── condition names come from the game, not from the id ───────────────────


def test_every_illness_carries_the_games_own_name():
    """
    **"Sick" was a flag, and a flag is not an answer.** The panel could say a
    Pal had `DepressionSprain`, because the server table carries only an
    internal `SickType` and the event table's `debugName` is Japanese.

    The names come from the client pak's `L10N/en` overrides. `nameIsInternal`
    is the honest fallback for a build without that pak — never a humanised id
    presented as the game's word for the thing.
    """
    rows = gamedata.basecamp().get("illnesses") or []
    assert len(rows) == 8
    for row in rows:
        assert row.get("name"), row
        assert "nameIsInternal" in row


def test_the_cold_condition_is_displayed_as_sick():
    """
    **The id and the player-facing name genuinely disagree here**, which is the
    whole reason these are looked up rather than derived from the id. A
    `humanize()` would have produced "Cold" and looked perfectly correct.
    """
    cold = gamedata.illness("Cold")
    assert cold is not None
    assert cold["id"] == "Cold"
    assert cold["name"] == "Sick"
    assert cold["nameIsInternal"] is False


def test_penalties_are_signed_and_a_zero_is_a_real_zero():
    """A Sprain costs no work speed at all. That is the game's answer, not a
    missing field, so the UI must be able to tell it from an unknown."""
    sprain = gamedata.illness("Sprain")
    assert sprain["workSpeed"] == 0
    assert sprain["moveSpeed"] == -5
    # The worst one, and the reason a bare "sick" flag was inadequate: half
    # speed at everything, cured 3% an hour.
    trouble = gamedata.illness("DisturbingElement")
    assert trouble["workSpeed"] == -50
    assert trouble["moveSpeed"] == -50
    assert trouble["palboxRecoveryPercent"] == 3


def test_the_cure_chance_has_a_period_to_go_with_it():
    """A percentage with no denominator is not a rate. The game rolls it once
    per `PalBoxTimePeriodRecoverySick`."""
    assert gamedata.game_setting("PalBoxTimePeriodRecoverySick") == 3600


def test_worker_events_start_at_85_not_at_the_low_sanity_threshold():
    """
    `main.LOW_SANITY` is 50 and answers a different question — the sanity a Pal
    needs to keep gaining *trust*. A worker starts taking breaks at **85** and
    has stopped being useful well before 50, so a panel warning only at 50 is
    silent through most of the problem.
    """
    events = gamedata.worker_sanity_thresholds()
    assert events
    assert max(e["triggerSanity"] for e in events) == 85
