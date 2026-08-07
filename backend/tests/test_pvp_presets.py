"""
Pocketpair publishes a PvP recipe, and ours disagreed with it.

`docs.palworldgame.com/settings-and-operation/pvp` says PvP is enabled by setting
**three** parameters to True: `bIsPvP`, `bEnablePlayerToPlayerDamage` and
`bEnableDefenseOtherGuildPlayer`.

The dashboard's hand-made `pvp_players_only` sets the third to **False**, while
calling itself a PvP preset. Its intent is reasonable — "players fight, bases
stay safe" — but whether a partial enable produces that is a claim about game
behaviour no file supports.

So the official pair is added rather than the hand-made pair edited, and the
`source` tag is what keeps them apart. Same discipline as `elements.py`: carrying
something unverified is fine, presenting it as the game's word is not.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import settings_ini  # noqa: E402

#: The three the official page names, verbatim.
REQUIRED = {"bIsPvP", "bEnablePlayerToPlayerDamage", "bEnableDefenseOtherGuildPlayer"}


def _preset(preset_id: str) -> dict:
    for preset in settings_ini.all_presets():
        if preset["id"] == preset_id:
            return preset
    raise AssertionError(f"no preset {preset_id!r}")


def test_the_official_preset_is_exactly_the_three_required_settings():
    """
    Not two, not twenty. The page's own split is between "what enables PvP" and
    "what we recommend alongside it", and collapsing them would apply a dozen
    opinions under a button labelled "enable PvP".
    """
    preset = _preset("pvp_official")
    assert set(preset["changes"]) == REQUIRED
    assert all(v is True for v in preset["changes"].values())


def test_both_official_presets_are_tagged_official():
    for preset_id in ("pvp_official", "pvp_official_recommended"):
        assert _preset(preset_id)["source"] == "official"


def test_the_hand_made_presets_stay_tagged_dashboard():
    """
    **The tag is the whole mechanism.** These two are this project's judgement,
    not Pocketpair's, and one of them contradicts the official recipe outright.
    Presenting all four identically would launder ours into theirs.
    """
    for preset_id in ("pvp_players_only", "pvp_full_raid"):
        assert _preset(preset_id)["source"] == "dashboard"


def test_the_disagreement_is_real_and_still_there():
    """
    Pinned so it cannot be quietly resolved in either direction. If somebody
    later establishes that a partial enable works, this test is the place to
    record it — with evidence, not by deleting the assertion.
    """
    ours = _preset("pvp_players_only")["changes"]
    assert ours["bEnableDefenseOtherGuildPlayer"] is False, (
        "if this changed, the preset and its description need to change together"
    )
    assert _preset("pvp_official")["changes"]["bEnableDefenseOtherGuildPlayer"] is True


def test_the_recommended_preset_contains_the_required_three():
    """A recommendation set that does not enable PvP would be a trap."""
    changes = _preset("pvp_official_recommended")["changes"]
    assert REQUIRED <= set(changes)
    assert all(changes[k] is True for k in REQUIRED)


def test_the_two_self_contradicting_recommendations_are_absent():
    """
    **`bEnableAimAssistPad` is omitted because the source disagrees with
    itself**: the heading says "Disable Gamepad Aim Assist", the prose says "when
    set to False, aim assist is disabled", and the code block says `=True`.
    Picking one is a guess wearing an official label.

    **`DenyTechnologyList` is omitted because it is a bigger act than the button
    says** — thirteen technologies including the Guild Chest — and the page
    presents it as something you *can* restrict rather than part of the recipe.
    It writes correctly through the ordinary editor for anyone who wants it.
    """
    changes = _preset("pvp_official_recommended")["changes"]
    assert "bEnableAimAssistPad" not in changes
    assert "DenyTechnologyList" not in changes


def test_every_preset_key_is_a_real_setting_and_writes_cleanly(tmp_path, monkeypatch, fresh_db):
    """
    A preset naming a key this version does not ship writes nothing and reports
    success, which is the silent-failure shape this project keeps finding. And a
    preset whose *value* the writer cannot render is worse — it writes a
    malformed INI to a live server.

    So every change is applied to a real copy of the 1.0 default file and read
    back.
    """
    reference = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "refs", "palworld", "DefaultPalWorldSettings.ini",
    )
    if not os.path.exists(reference):
        pytest.skip("refs/ not present")

    import shutil

    ini = tmp_path / "PalWorldSettings.ini"
    shutil.copy(reference, ini)
    monkeypatch.setattr(settings_ini, "BACKUP_DIR", str(tmp_path / "backups"))

    known = set(settings_ini.read_ini(str(ini))["options"])
    changes = _preset("pvp_official_recommended")["changes"]
    unknown = sorted(set(changes) - known)
    assert unknown == [], f"preset names settings this version does not have: {unknown}"

    settings_ini.write_ini(dict(changes), str(ini))
    after = settings_ini.read_ini(str(ini))["options"]

    for key, wanted in changes.items():
        got = after[key]["value"]
        if isinstance(wanted, bool):
            assert got is wanted, f"{key}: wrote {wanted}, read back {got}"
        else:
            assert str(got) == str(wanted) or float(got) == float(wanted), (
                f"{key}: wrote {wanted!r}, read back {got!r}"
            )

    # The file must still parse to the same number of settings — a malformed
    # value would take neighbouring keys with it.
    assert len(after) == len(known)


def test_a_technology_list_survives_a_write_round_trip(tmp_path, monkeypatch, fresh_db):
    """
    `DenyTechnologyList` is not in the preset, but an operator following the
    official page will paste one in — and it is the only value here with quotes
    and parentheses inside it, which is exactly what `_split_top_level` exists
    for. If this cannot round-trip, the omission above stops being a judgement
    call and becomes a refusal.
    """
    reference = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "refs", "palworld", "DefaultPalWorldSettings.ini",
    )
    if not os.path.exists(reference):
        pytest.skip("refs/ not present")

    import shutil

    ini = tmp_path / "PalWorldSettings.ini"
    shutil.copy(reference, ini)
    monkeypatch.setattr(settings_ini, "BACKUP_DIR", str(tmp_path / "backups"))

    before = len(settings_ini.read_ini(str(ini))["options"])
    value = '("SkillUnlock_JetDragon", "GrapplingGun", "GuildChest")'
    settings_ini.write_ini({"DenyTechnologyList": value}, str(ini))

    after = settings_ini.read_ini(str(ini))["options"]
    assert len(after) == before, "the parenthesised list swallowed a neighbour"
    assert after["DenyTechnologyList"]["raw"] == value
    # The setting after it in the file must be untouched — that is what a bad
    # split would break first.
    assert after["GuildRejoinCooldownMinutes"]["value"] == 0
