"""
The per-key env-equivalents bundle — pinned against the SHIPPED file.

The bundle is parsed from each server image's own INI template, and these are
the claims the settings tab rests on. Its first build caught two errors in the
hand map it replaced (`SERVER_PLAYER_MAX_NUM` not `PLAYERS`, `USEAUTH` not
`USE_AUTH`), which is exactly why the names are pinned here: a stale env name
sends an operator to edit a variable their container never reads — the same
silent revert this feature exists to prevent, one hop removed.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import settings_ini  # noqa: E402


def test_the_bundle_covers_every_ini_key_on_at_least_one_image():
    # 119 settings in DefaultPalWorldSettings.ini; the union of both templates
    # covers all of them. A big drop means a template moved or the regex broke.
    assert len(settings_ini.ENV_EQUIVALENTS) >= 115


def test_every_loud_badge_key_has_an_equivalent():
    """ENV_MANAGED_KEYS decides which rows shout; the names come from the
    bundle. A badge key missing from the bundle would render no name at all."""
    missing = [k for k in settings_ini.ENV_MANAGED_KEYS
               if k not in settings_ini.ENV_EQUIVALENTS]
    assert not missing, missing


def test_the_two_hand_map_errors_stay_fixed():
    eq = settings_ini.ENV_EQUIVALENTS
    assert eq["ServerPlayerMaxNum"]["thijsvanloef"] == "SERVER_PLAYER_MAX_NUM"
    assert eq["bUseAuth"]["thijsvanloef"] == "USEAUTH"
    # jammsen's MAX_PLAYERS is the value the old map wrongly gave thijsvanloef.
    assert eq["ServerPlayerMaxNum"]["jammsen"] == "MAX_PLAYERS"


def test_the_dual_spelling_survives():
    """The one disagreement the old hand map knew about, read from the
    templates rather than remembered."""
    eq = settings_ini.ENV_EQUIVALENTS["RESTAPIPort"]
    assert eq["thijsvanloef"] == "REST_API_PORT"
    assert eq["jammsen"] == "RESTAPI_PORT"


def test_the_one_key_with_no_thijsvanloef_variable():
    """bEnableFastTravelOnlyBaseCamp is in jammsen's template and NOT in
    thijsvanloef's — on that image it resets to the default every start with
    no variable to reach for. If this ever gains a mapping, the settings-tab
    caveat about it is stale."""
    eq = settings_ini.ENV_EQUIVALENTS["bEnableFastTravelOnlyBaseCamp"]
    assert "jammsen" in eq
    assert "thijsvanloef" not in eq


def test_display_joins_only_when_the_images_disagree():
    assert settings_ini._env_display("ExpRate") == "EXP_RATE"
    assert settings_ini._env_display("RESTAPIPort") == "REST_API_PORT / RESTAPI_PORT"
    assert settings_ini._env_display("NoSuchKey") is None
