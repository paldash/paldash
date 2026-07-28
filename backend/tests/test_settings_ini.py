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
    assert opts["AdminPassword"]["value"] == "p@ss,word"


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
    assert opts["AdminPassword"]["value"] == "p@ss,word"
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
        assert isinstance(preset["changes"], dict) and preset["changes"]


def test_apply_unknown_preset_raises(ini):
    with pytest.raises(SettingsError):
        settings_ini.apply_preset("no_such_preset", ini)
