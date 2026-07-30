"""
PalWorldSettings.ini parsing and writing.

The whole file is one enormous `OptionSettings=(...)` line containing quoted
strings that themselves contain commas. Splitting it naively corrupts the server
config, and a corrupt config means a server that will not boot.
"""

from __future__ import annotations

import os

import pytest

import settings_ini
from settings_ini import SettingsError

# A realistic line: quoted strings containing commas and parens, bools, floats,
# ints, and a bare enum.
SAMPLE = """[/Script/Pal.PalGameWorldSettings]
OptionSettings=(Difficulty=None,DayTimeSpeedRate=1.000000,ExpRate=1.000000,PalCaptureRate=1.000000,bIsPvP=False,bEnablePlayerToPlayerDamage=False,DeathPenalty=All,ServerName="Nirb's server, with a comma",ServerDescription="Line (with parens), and a comma",AdminPassword="p@ss,word",PublicPort=8211,BaseCampMaxNumInGuild=4,bShowPlayerList=True)
"""


@pytest.fixture
def ini(tmp_path, monkeypatch):
    path = tmp_path / "PalWorldSettings.ini"
    path.write_text(SAMPLE, encoding="utf-8")
    monkeypatch.setattr(settings_ini, "BACKUP_DIR", str(tmp_path / "backups"))
    return str(path)


# ─── Splitting ───────────────────────────────────────────────────


def test_split_ignores_commas_inside_quotes():
    parts = settings_ini._split_top_level('A=1,B="x,y,z",C=2')
    assert parts == ['A=1', 'B="x,y,z"', "C=2"]


def test_split_ignores_commas_inside_parens():
    parts = settings_ini._split_top_level("A=1,B=(x,y),C=2")
    assert parts == ["A=1", "B=(x,y)", "C=2"]


def test_split_handles_parens_inside_quotes():
    parts = settings_ini._split_top_level('A="a (b, c)",B=2')
    assert parts == ['A="a (b, c)"', "B=2"]


def test_split_drops_empty_segments():
    assert settings_ini._split_top_level("A=1,,B=2") == ["A=1", "B=2"]


# ─── Type inference ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected_type,expected_value",
    [
        ('"hello"', "string", "hello"),
        ("True", "bool", True),
        ("False", "bool", False),
        ("1.000000", "float", 1.0),
        ("-2.5", "float", -2.5),
        ("8211", "int", 8211),
        ("-1", "int", -1),
        ("None", "enum", "None"),
        ("All", "enum", "All"),
    ],
)
def test_classify(raw, expected_type, expected_value):
    assert settings_ini._classify(raw) == (expected_type, expected_value)


def test_format_preserves_float_precision():
    """1.000000 must not come back as 1.0 — the game is picky about this file."""
    assert settings_ini._format(2, "float", "1.000000") == "2.000000"
    assert settings_ini._format(0.5, "float", "1.00") == "0.50"


def test_format_bool_accepts_strings():
    assert settings_ini._format("true", "bool", "False") == "True"
    assert settings_ini._format(False, "bool", "True") == "False"


def test_format_string_strips_embedded_quotes():
    assert settings_ini._format('a"b', "string", '"x"') == '"ab"'


# ─── Reading ─────────────────────────────────────────────────────


def test_read_parses_every_option(ini):
    data = settings_ini.read_ini(ini)
    opts = data["options"]
    assert data["count"] == 13
    assert opts["ExpRate"]["value"] == 1.0
    assert opts["bIsPvP"]["value"] is False
    assert opts["PublicPort"]["value"] == 8211
    assert opts["Difficulty"]["value"] == "None"


def test_read_preserves_commas_inside_quoted_values(ini):
    opts = settings_ini.read_ini(ini)["options"]
    assert opts["ServerName"]["value"] == "Nirb's server, with a comma"
    assert opts["ServerDescription"]["value"] == "Line (with parens), and a comma"
    # reveal=True: the point of this assertion is that the comma inside the
    # quoted value survived the split, and the masked read cannot show that.
    assert settings_ini.read_ini(ini, reveal=True)["options"]["AdminPassword"]["value"] == "p@ss,word"


def test_read_missing_file_raises(tmp_path):
    with pytest.raises(SettingsError, match="not found"):
        settings_ini.read_ini(str(tmp_path / "nope.ini"))


def test_read_without_option_line_raises(tmp_path):
    path = tmp_path / "bad.ini"
    path.write_text("[/Script/Pal.PalGameWorldSettings]\n", encoding="utf-8")
    with pytest.raises(SettingsError, match="No OptionSettings"):
        settings_ini.read_ini(str(path))


# ─── Writing ─────────────────────────────────────────────────────


def test_write_changes_only_the_named_key(ini):
    before = settings_ini.read_ini(ini)["options"]
    result = settings_ini.write_ini({"ExpRate": 3.0}, ini)
    after = settings_ini.read_ini(ini)["options"]

    assert result["changed"] is True
    assert after["ExpRate"]["value"] == 3.0
    assert after["ExpRate"]["raw"] == "3.000000", "float format must be preserved"

    changed = {k for k in after if after[k]["raw"] != before[k]["raw"]}
    assert changed == {"ExpRate"}


def test_write_survives_quoted_commas(ini):
    """The dangerous case: rewriting must not split ServerName on its comma."""
    settings_ini.write_ini({"bIsPvP": True}, ini)
    opts = settings_ini.read_ini(ini)["options"]

    assert opts["bIsPvP"]["value"] is True
    assert opts["ServerName"]["value"] == "Nirb's server, with a comma"
    # reveal=True: the point of this assertion is that the comma inside the
    # quoted value survived the split, and the masked read cannot show that.
    assert settings_ini.read_ini(ini, reveal=True)["options"]["AdminPassword"]["value"] == "p@ss,word"
    assert len(opts) == 13, "no option may be lost or invented"


def test_write_rejects_unknown_keys(ini):
    with pytest.raises(SettingsError, match="Unknown setting key"):
        settings_ini.write_ini({"TotallyMadeUpSetting": 1}, ini)


def test_write_rejects_empty_changes(ini):
    with pytest.raises(SettingsError, match="No changes"):
        settings_ini.write_ini({}, ini)


def test_write_makes_a_backup_first(ini, tmp_path):
    settings_ini.write_ini({"ExpRate": 5.0}, ini)
    backups = list((tmp_path / "backups" / "config").glob("PalWorldSettings_*.ini"))
    assert len(backups) == 1
    assert "ExpRate=1.000000" in backups[0].read_text(), "backup must predate the change"


def test_write_backup_lands_outside_the_save_directory(ini, tmp_path):
    settings_ini.write_ini({"ExpRate": 5.0}, ini)
    backup_dir = tmp_path / "backups" / "config"
    assert backup_dir.is_dir()
    assert not list(tmp_path.glob("PalWorldSettings_*.ini")), (
        "backups must not be written next to the live config"
    )


def test_write_noop_when_value_is_unchanged(ini):
    result = settings_ini.write_ini({"ExpRate": 1.0}, ini)
    assert result["changed"] is False
    assert result["applied"] == []


def test_write_preserves_the_section_header(ini):
    settings_ini.write_ini({"ExpRate": 2.0}, ini)
    text = open(ini, encoding="utf-8").read()
    assert text.startswith("[/Script/Pal.PalGameWorldSettings]")
    assert text.count("OptionSettings=(") == 1


def test_write_preserves_crlf_line_endings(tmp_path, monkeypatch):
    path = tmp_path / "crlf.ini"
    path.write_bytes(SAMPLE.replace("\n", "\r\n").encode())
    monkeypatch.setattr(settings_ini, "BACKUP_DIR", str(tmp_path / "b"))

    settings_ini.write_ini({"ExpRate": 2.0}, str(path))
    raw = path.read_bytes()
    assert b"\r\n" in raw
    # Every LF must be part of a CRLF pair — no bare LF may be introduced.
    assert raw.count(b"\n") == raw.count(b"\r\n")


def test_write_preserves_lf_line_endings(ini):
    """The mirror case: a Linux INI must not gain carriage returns."""
    settings_ini.write_ini({"ExpRate": 2.0}, ini)
    raw = open(ini, "rb").read()
    assert b"\r" not in raw


# ─── Presets ─────────────────────────────────────────────────────


def test_presets_are_well_formed():
    assert settings_ini.PRESETS
    for preset in settings_ini.PRESETS:
        assert preset["id"] and preset["label"]
        assert isinstance(preset["changes"], dict)

        if preset.get("derived"):
            # A derived preset computes its changes at apply time from the bundled
            # game defaults — `vanilla` cannot be a literal without drifting from
            # both the game's own values and the keys the other presets touch. It
            # still has to resolve to something, or it would report success while
            # writing nothing.
            assert not preset["changes"], "a derived preset holds no literal changes"
            assert settings_ini._vanilla_changes(), "derived preset resolved to nothing"
        else:
            assert preset["changes"]


def test_apply_unknown_preset_raises(ini):
    with pytest.raises(SettingsError):
        settings_ini.apply_preset("no_such_preset", ini)


# ─── Against a real dedicated server install ─────────────────────
#
# `refs/palworld/DefaultPalWorldSettings.ini` is what the 1.0 dedicated server
# ships with: the authoritative list of every setting it accepts. Checking
# against it rather than against memory is what caught `EggDefaultHatchingTime`,
# a highlight-group key that does not exist — the real one is
# `PalEggDefaultHatchingTime`, so that highlight silently matched nothing.

import shutil


@pytest.fixture
def real_ini(reference_default_ini, tmp_path, monkeypatch):
    path = tmp_path / "PalWorldSettings.ini"
    shutil.copy(reference_default_ini, path)
    # write_ini backs the file up first, and BACKUP_DIR defaults to /palworld.
    monkeypatch.setattr(settings_ini, "BACKUP_DIR", str(tmp_path / "backups"))
    return str(path)


def test_parses_every_setting_a_real_1_0_server_ships_with(real_ini):
    data = settings_ini.read_ini(real_ini)
    assert data["count"] == 119, "the 1.0 default set changed — re-check the presets too"


def test_the_awkward_value_shapes_all_classify(real_ini):
    """
    The four shapes that a naive comma-split gets wrong: a nested parenthesised
    list, an empty value, a negative integer, and a bare unquoted enum.
    """
    options = settings_ini.read_ini(real_ini)["options"]

    assert options["CrossplayPlatforms"]["value"] == "(Steam,Xbox,PS5,Mac)"
    assert options["DenyTechnologyList"]["value"] == ""
    assert options["PhysicsActiveDropItemMaxNum"]["value"] == -1
    assert options["LogFormatType"]["value"] == "Text"
    assert options["PublicIP"]["type"] == "string"
    assert options["AutoSaveSpan"]["value"] == 30.0
    assert options["RESTAPIEnabled"]["value"] is False


def test_rewriting_a_value_as_itself_is_byte_identical(real_ini):
    """
    119 settings on one line: a rewrite that reformats anything is a rewrite
    that can lose a setting. The file must come back out exactly as it went in.
    """
    before = open(real_ini, encoding="utf-8").read()
    current = settings_ini.read_ini(real_ini)["options"]["AutoSaveSpan"]["value"]
    settings_ini.write_ini({"AutoSaveSpan": current}, real_ini)

    assert open(real_ini, encoding="utf-8").read() == before


def test_every_highlighted_key_actually_exists(real_ini):
    options = settings_ini.read_ini(real_ini)["options"]
    for group in settings_ini.HIGHLIGHT_GROUPS:
        unknown = [k for k in group["keys"] if k not in options]
        assert not unknown, f"{group['label']} highlights non-existent settings: {unknown}"


def test_every_preset_writes_only_real_keys(real_ini):
    """A preset naming a key the server does not have would write a dead setting."""
    options = settings_ini.read_ini(real_ini)["options"]
    for preset in settings_ini.PRESETS:
        unknown = [k for k in preset["changes"] if k not in options]
        assert not unknown, f"preset {preset['id']} writes non-existent settings: {unknown}"


# ─── Secrets ─────────────────────────────────────────────────────
#
# `OptionSettings` is one line and the reader returns all of it, so the server's
# admin and join passwords were being handed to every caller of the settings
# endpoint in cleartext — and into the audit log on every change.


def test_passwords_are_masked_on_read(real_ini):
    options = settings_ini.read_ini(real_ini)["options"]
    for key in settings_ini.SECRET_KEYS:
        assert options[key]["value"] == "", f"{key} leaked"
        assert options[key]["raw"] == ""
        assert options[key]["secret"] is True


def test_masking_still_says_whether_one_is_set(real_ini):
    """An admin needs to know an empty admin password is empty."""
    options = settings_ini.read_ini(real_ini)["options"]
    assert options["AdminPassword"]["isSet"] is False  # the shipped default is blank

    settings_ini.write_ini({"AdminPassword": "hunter2"}, real_ini)
    assert settings_ini.read_ini(real_ini)["options"]["AdminPassword"]["isSet"] is True


def test_the_write_path_can_still_see_the_real_value(real_ini):
    settings_ini.write_ini({"ServerPassword": "letmein"}, real_ini)
    revealed = settings_ini.read_ini(real_ini, reveal=True)["options"]
    assert revealed["ServerPassword"]["value"] == "letmein"


def test_submitting_the_mask_back_does_not_blank_the_password(real_ini):
    """
    The failure this prevents: a settings form loads masked values, the user
    changes something unrelated, and saving writes the empty mask over the real
    password — locking every admin out of their own server.
    """
    settings_ini.write_ini({"ServerPassword": "keepme"}, real_ini)

    result = settings_ini.write_ini(
        {"ServerPassword": "", "AutoSaveSpan": 45.0}, real_ini
    )

    revealed = settings_ini.read_ini(real_ini, reveal=True)["options"]
    assert revealed["ServerPassword"]["value"] == "keepme"
    assert revealed["AutoSaveSpan"]["value"] == 45.0
    assert [a["key"] for a in result["applied"]] == ["AutoSaveSpan"]


def test_a_password_change_does_not_land_in_the_audit_detail(real_ini):
    """`applied` goes straight into audit.record, which is permanent."""
    result = settings_ini.write_ini({"AdminPassword": "s3cret"}, real_ini)

    entry = next(a for a in result["applied"] if a["key"] == "AdminPassword")
    assert entry["from"] == "(hidden)"
    assert entry["to"] == "(hidden)"
    assert "s3cret" not in repr(result)


def test_env_managed_keys_are_flagged(real_ini):
    """
    The common server images rewrite the INI from env vars on every start, so a
    change to one of these lasts until the next restart and is then silently
    reverted. Flagging them is the difference between an edit that fails and one
    that appears to work.
    """
    options = settings_ini.read_ini(real_ini)["options"]

    assert options["AdminPassword"]["envManaged"] == "ADMIN_PASSWORD"
    assert options["ServerName"]["envManaged"] == "SERVER_NAME"
    # Both spellings, because the two popular images disagree: thijsvanloef uses
    # REST_API_PORT and jammsen uses RESTAPI_PORT. Asserted by containment rather
    # than as an exact literal — an operator has to be able to find the name in
    # their own compose file, and which one that is depends on their image.
    rest_port = options["RESTAPIPort"]["envManaged"]
    assert "REST_API_PORT" in rest_port
    assert "RESTAPI_PORT" in rest_port
    # A pure gameplay setting has no container equivalent and must not be flagged.
    assert "envManaged" not in options["ExpRate"]


def test_masking_does_not_drop_the_env_flag(real_ini):
    """AdminPassword is both secret and env-managed; it needs to carry both."""
    admin = settings_ini.read_ini(real_ini)["options"]["AdminPassword"]
    assert admin["secret"] is True
    assert admin["envManaged"] == "ADMIN_PASSWORD"
    assert admin["type"] == "string"


def test_every_env_managed_key_exists_in_a_real_server_config(real_ini):
    """A flag on a key that does not exist would never be shown to anyone."""
    options = settings_ini.read_ini(real_ini)["options"]
    missing = [k for k in settings_ini.ENV_MANAGED if k not in options]
    assert not missing, f"ENV_MANAGED names settings a 1.0 server does not have: {missing}"
