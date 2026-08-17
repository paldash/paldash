"""
Server presets, and the bundled Palworld defaults behind the vanilla reset.

The thing worth testing is not that a preset writes values. It is that the **key
names are real**. `DefaultPalWorldSettings.ini` is the only reliable source for
them, and this project has already paid for guessing once: `EggDefaultHatchingTime`
sat in a highlight group matching nothing for months, because the real key is
`PalEggDefaultHatchingTime`. A preset with a wrong key silently does nothing.

Two of the game's own key names are misspelled (`PalStaminaDecreaceRate`,
`PlayerStomachDecreaceRate`). They are used exactly as the game writes them, and a
test pins that so nobody "fixes" one.
"""

from __future__ import annotations

import os

import pytest

import settings_ini


def real_keys() -> set[str]:
    """Every key the bundled defaults know about."""
    return set(settings_ini.game_defaults())


# ─── The bundled defaults ────────────────────────────────


def test_the_defaults_bundle_ships_and_parses():
    defaults = settings_ini.game_defaults()
    assert len(defaults) == 117, "expected the 119 documented settings minus 2 secrets"


def test_credentials_are_excluded_from_the_defaults():
    """
    A "default" for a password is not something this project should hand back, even
    an empty one — it invites a UI that offers to reset it.
    """
    defaults = settings_ini.game_defaults()
    for secret in settings_ini.SECRET_KEYS:
        assert secret not in defaults


def test_a_missing_bundle_degrades_rather_than_raising(monkeypatch):
    monkeypatch.setattr(settings_ini, "_defaults", None)
    monkeypatch.setattr(settings_ini, "DEFAULTS_PATH", "/nonexistent/defaults.json")
    try:
        assert settings_ini.game_defaults() == {}
    finally:
        monkeypatch.setattr(settings_ini, "_defaults", None)


# ─── Key names are real ──────────────────────────────────


def test_every_preset_key_exists_in_the_game(subtests=None):
    """
    The check that matters. A preset key the game does not have writes nothing and
    reports success.
    """
    known = real_keys()
    if not known:
        pytest.skip("bundled defaults unavailable")

    unknown: list[tuple[str, str]] = []
    for preset in settings_ini.PRESETS:
        for key in preset.get("changes", {}):
            if key not in known:
                unknown.append((preset["id"], key))
    assert not unknown, f"preset keys not in DefaultPalWorldSettings.ini: {unknown}"


def test_every_highlight_group_key_exists_in_the_game():
    known = real_keys()
    if not known:
        pytest.skip("bundled defaults unavailable")

    unknown = [
        (group.get("label"), key)
        for group in settings_ini.HIGHLIGHT_GROUPS
        for key in (group.get("keys") or [])
        # The two credential keys are legitimately absent from the defaults bundle.
        if key not in known and key not in settings_ini.SECRET_KEYS
    ]
    assert not unknown, f"highlight keys not in DefaultPalWorldSettings.ini: {unknown}"


def test_the_games_own_misspellings_are_preserved():
    """
    `Decreace`, not `Decrease`. Correcting either would match nothing and silently
    do nothing — the exact failure mode this file exists to prevent.
    """
    known = real_keys()
    assert "PalStaminaDecreaceRate" in known
    assert "PlayerStomachDecreaceRate" in known
    assert "PalStaminaDecreaseRate" not in known


def test_the_egg_hatching_key_is_the_prefixed_one():
    known = real_keys()
    assert "PalEggDefaultHatchingTime" in known
    assert "EggDefaultHatchingTime" not in known


# ─── Preset content ──────────────────────────────────────


def test_preset_ids_are_unique():
    ids = [p["id"] for p in settings_ini.PRESETS]
    assert len(ids) == len(set(ids))


def test_every_preset_is_described():
    for preset in settings_ini.PRESETS:
        assert preset.get("label"), preset["id"]
        assert preset.get("description"), preset["id"]


def test_the_hardcore_preset_does_not_silently_enable_pvp():
    """
    Difficulty and PvP are separate decisions. A preset that flipped `bIsPvP` as a
    side effect of "make it harder" would turn a co-op server hostile without
    saying so — its description promises it does not.
    """
    hardcore = next(p for p in settings_ini.PRESETS if p["id"] == "hardcore")
    assert "bIsPvP" not in hardcore["changes"]


def test_death_penalty_uses_a_value_the_game_accepts():
    """`DeathPenalty` is an enum, not a number — a wrong token is silently ignored."""
    hardcore = next(p for p in settings_ini.PRESETS if p["id"] == "hardcore")
    assert hardcore["changes"]["DeathPenalty"] in ("None", "Item", "ItemAndEquipment", "All")


# ─── The vanilla reset ───────────────────────────────────


def test_vanilla_resets_rates_but_not_the_servers_identity():
    """
    "Undo my tinkering" must not rename the server, change its ports, or close the
    REST API this dashboard talks to.
    """
    changes = settings_ini._vanilla_changes()
    assert changes, "vanilla should reset something"

    forbidden = settings_ini.ENV_MANAGED_KEYS | set(settings_ini.SECRET_KEYS)
    assert not (set(changes) & forbidden)


def test_vanilla_values_come_from_the_game_not_from_memory():
    defaults = settings_ini.game_defaults()
    for key, value in settings_ini._vanilla_changes().items():
        assert value == defaults[key]


def test_vanilla_covers_what_the_other_presets_change():
    """
    Otherwise a preset could move a setting that the reset cannot move back, which
    is the one thing a reset must not do.
    """
    changes = set(settings_ini._vanilla_changes())
    forbidden = settings_ini.ENV_MANAGED_KEYS | set(settings_ini.SECRET_KEYS)
    defaults = settings_ini.game_defaults()

    for preset in settings_ini.PRESETS:
        if preset["id"] == "vanilla":
            continue
        for key in preset["changes"]:
            if key in forbidden or key not in defaults:
                continue
            assert key in changes, f"{preset['id']} changes {key}, vanilla cannot reset it"


def test_vanilla_refuses_when_the_defaults_are_missing(tmp_path, monkeypatch):
    """
    A reset that quietly wrote nothing would be the worst outcome: the operator
    believes their tuning is undone.
    """
    monkeypatch.setattr(settings_ini, "_defaults", None)
    monkeypatch.setattr(settings_ini, "DEFAULTS_PATH", str(tmp_path / "gone.json"))
    try:
        with pytest.raises(settings_ini.SettingsError, match="missing"):
            settings_ini.apply_preset("vanilla", str(tmp_path / "PalWorldSettings.ini"))
    finally:
        monkeypatch.setattr(settings_ini, "_defaults", None)


def test_an_unknown_preset_is_refused():
    with pytest.raises(settings_ini.SettingsError, match="Unknown preset"):
        settings_ini.apply_preset("nope")


# ─── Against the real game file ──────────────────────────


@pytest.mark.integration
def test_the_bundle_matches_the_installed_games_ini():
    """
    Regenerate the bundle if this fails — the game added or renamed settings, which
    is exactly what `gameversion.py` raises a banner for.
    """
    import re

    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "refs", "palworld", "DefaultPalWorldSettings.ini",
    )
    if not os.path.exists(path):
        pytest.skip("refs/palworld not present")

    body = re.search(r"OptionSettings=\((.*)\)", open(path).read(), re.S).group(1)
    live = {k for k, _ in re.findall(r"(\w+)=(\"[^\"]*\"|[^,\)]*)", body)}
    bundled = real_keys() | set(settings_ini.SECRET_KEYS)

    assert live == bundled, (
        f"missing from the bundle: {sorted(live - bundled)}; "
        f"stale in the bundle: {sorted(bundled - live)}"
    )


# ─── The game's own difficulty presets ───────────────────


def test_the_game_ships_four_difficulties_and_normal_is_the_baseline():
    """
    `DT_OptionWorldPresetTable`. Normal is every rate at 1.0, so it produces no
    changes and is not offered — "apply Normal" would be a no-op that looked like
    a rewrite of forty settings.
    """
    presets = settings_ini.game_presets()
    ids = {p["id"] for p in presets}
    assert ids == {"game_easy", "game_hard", "game_hardcore"}
    assert all(p["source"] == "game" for p in presets)


def test_only_keys_that_differ_from_normal_are_emitted():
    """
    Each preset row carries all 43 settings. Writing them all would set forty to
    values they already hold, bury the three that matter in the audit diff, and
    make a difficulty change read as a full reconfiguration.
    """
    for preset in settings_ini.game_presets():
        assert 0 < len(preset["changes"]) < 12, preset["id"]


def test_the_hand_made_hardcore_was_misnamed_and_no_longer_claims_to_be_hardcore():
    """
    **THE CROSS-CHECK THAT JUSTIFIED EXTRACTING THESE.** Against the game's own
    `HardcorePreset`, the hand-made `hardcore` agreed on `PalCaptureRate`,
    `PlayerDamageRateAttack` and `PlayerDamageRateDefense`, differed on `ExpRate`
    (0.5 vs 0.8) and `PlayerStaminaDecreaceRate` (1.5 vs 1.0) — defensible taste —
    and **omitted `bHardcore` and `bPalLost`**, which are what the game means by
    hardcore: player permadeath and losing your Pals.

    Those are not settings to add silently to a preset operators may already have
    applied, so the rates stayed and the *name* changed.
    """
    ours = next(p for p in settings_ini.PRESETS if p["id"] == "hardcore")
    assert "Hardcore" not in ours["label"]
    assert "bHardcore" not in ours["changes"]

    real = next(p for p in settings_ini.game_presets() if p["id"] == "game_hardcore")
    assert real["changes"]["bHardcore"] is True
    assert real["changes"]["bPalLost"] is True


def test_the_agreeing_rates_still_agree():
    """
    The half of the cross-check that passed, pinned so a future edit to either
    side surfaces as a disagreement rather than passing silently.
    """
    ours = next(p for p in settings_ini.PRESETS if p["id"] == "hardcore")["changes"]
    game = next(
        p for p in settings_ini.game_presets() if p["id"] == "game_hardcore"
    )["changes"]
    for key in ("PalCaptureRate", "PlayerDamageRateAttack", "PlayerDamageRateDefense"):
        assert ours[key] == game[key], key


def test_a_game_preset_is_applicable_and_resettable():
    """
    Offered in the same list, so it must be findable by `apply_preset` — and its
    keys must be in the vanilla reset, or applying Hardcore would leave
    `bHardcore` on with no way back through the UI.
    """
    assert {p["id"] for p in settings_ini.all_presets()} >= {"game_hardcore", "vanilla"}
    reset = settings_ini._vanilla_changes()
    assert "bHardcore" in reset and "bPalLost" in reset
