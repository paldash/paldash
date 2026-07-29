"""
Report rendering.

Pure functions over already-parsed data, so the tests are about the things that
actually break exports: separators inside values, unicode, empty result sets,
and the three formats staying in agreement about what the rows are.
"""

from __future__ import annotations

import csv
import io
import json

import pytest

import reports


def base_row(**overrides):
    row = {
        "baseId": "abc-123", "baseName": "Main Base", "guildId": "g1", "guildName": "Greed",
        "containerCount": 1, "usedSlots": 2, "totalSlots": 4, "fillPercent": 50.0,
        "itemCount": 300, "uniqueItems": 2,
        "items": [
            {"itemId": "Wood", "itemName": "Wood", "count": 200},
            {"itemId": "Stone", "itemName": "Stone", "count": 100},
        ],
        "containers": [{
            "containerId": "c1", "kind": "ItemChest_02", "kindName": "Metal Chest",
            "category": "chest", "usedSlots": 2, "totalSlots": 4, "itemCount": 300,
        }],
    }
    row.update(overrides)
    return row


# ─── Contract ────────────────────────────────────────────────────


def test_every_report_renders_in_every_format():
    data = {"items": [{"itemId": "Wood", "count": 5}], "baseStorage": [base_row()]}
    for report in reports.REPORTS:
        for fmt in reports.FORMATS:
            body = reports.render(report, fmt, data[reports.section_for(report)])
            assert body.strip(), f"{report}/{fmt} rendered nothing"


def test_unknown_report_and_format_are_refused():
    with pytest.raises(reports.ReportError, match="Unknown report"):
        reports.render("not-a-report", "csv", [])
    with pytest.raises(reports.ReportError, match="Unknown format"):
        reports.render("base-summary", "xlsx", [])


def test_every_report_declares_a_section_that_exists():
    for report in reports.REPORTS:
        assert reports.section_for(report) in ("items", "baseStorage")


# ─── CSV ─────────────────────────────────────────────────────────


def test_csv_round_trips_through_a_reader():
    body = reports.render("base-summary", "csv", [base_row()])
    rows = list(csv.DictReader(io.StringIO(body)))

    assert len(rows) == 1
    assert rows[0]["baseName"] == "Main Base"
    assert rows[0]["itemCount"] == "300"


def test_csv_quotes_values_containing_commas():
    """Item and base names contain commas often enough to corrupt a naive join."""
    body = reports.render("base-summary", "csv", [base_row(baseName="Base, the second")])
    rows = list(csv.DictReader(io.StringIO(body)))

    assert rows[0]["baseName"] == "Base, the second"
    assert '"Base, the second"' in body


def test_csv_survives_a_quote_inside_a_value():
    body = reports.render("base-summary", "csv", [base_row(baseName='The "Big" Base')])
    assert list(csv.DictReader(io.StringIO(body)))[0]["baseName"] == 'The "Big" Base'


def test_csv_uses_crlf_for_spreadsheet_compatibility():
    assert reports.render("base-summary", "csv", [base_row()]).endswith("\r\n")


# ─── JSON ────────────────────────────────────────────────────────


def test_json_is_parseable_and_carries_metadata():
    body = reports.render("base-summary", "json", [base_row()], {"generatedAt": "2026-07-28"})
    parsed = json.loads(body)

    assert parsed["rowCount"] == 1
    assert parsed["generatedAt"] == "2026-07-28"
    assert parsed["rows"][0]["baseName"] == "Main Base"


def test_json_keeps_unicode_readable():
    """A Japanese base name must not come back as \\u escapes."""
    body = reports.render("base-summary", "json", [base_row(baseName="拠点")])
    assert "拠点" in body
    assert json.loads(body)["rows"][0]["baseName"] == "拠点"


# ─── Text ────────────────────────────────────────────────────────


def test_txt_has_a_header_underline_and_a_row_count():
    body = reports.render("base-summary", "txt", [base_row()])
    lines = body.splitlines()

    assert lines[0] == "Storage by base"
    assert set(lines[1]) == {"="}
    assert body.rstrip().endswith("1 row")


def test_txt_pluralises_the_row_count():
    body = reports.render("base-summary", "txt", [base_row(), base_row(baseId="d")])
    assert body.rstrip().endswith("2 rows")


def test_txt_columns_are_wide_enough_for_their_widest_value():
    body = reports.render("base-summary", "txt", [base_row(baseName="A Very Long Base Name Indeed")])
    assert "A Very Long Base Name Indeed" in body
    header, underline = body.splitlines()[4], body.splitlines()[5]
    assert len(underline) == len(header.rstrip()) or len(underline) >= len(header)


# ─── Row content ─────────────────────────────────────────────────


def test_base_items_produces_one_row_per_item_per_base():
    rows = list(csv.DictReader(io.StringIO(
        reports.render("base-items", "csv", [base_row(), base_row(baseId="d2", baseName="Second")])
    )))
    assert len(rows) == 4
    assert {r["baseName"] for r in rows} == {"Main Base", "Second"}


def test_world_items_adds_the_authoritative_stack_size():
    rows = list(csv.DictReader(io.StringIO(
        reports.render("world-items", "csv", [{"itemId": "Wood", "count": 25000}])
    )))
    assert rows[0]["itemName"] == "Wood"
    assert rows[0]["maxStack"] == "9999"
    assert rows[0]["fullStacks"] == "2"


def test_unknown_items_leave_the_stack_columns_blank():
    """Blank beats a fabricated 0, which would read as 'cannot be stacked'."""
    rows = list(csv.DictReader(io.StringIO(
        reports.render("world-items", "csv", [{"itemId": "NotARealModItem", "count": 5}])
    )))
    assert rows[0]["maxStack"] == ""
    assert rows[0]["fullStacks"] == ""


def test_containers_report_computes_per_container_fill():
    rows = list(csv.DictReader(io.StringIO(reports.render("containers", "csv", [base_row()]))))
    assert rows[0]["kindName"] == "Metal Chest"
    assert rows[0]["fillPercent"] == "50.0"


# ─── Empty input ─────────────────────────────────────────────────


@pytest.mark.parametrize("fmt", reports.FORMATS)
def test_an_empty_report_still_renders(fmt):
    """A world with no bases parsed yet must export an empty file, not crash."""
    body = reports.render("base-summary", fmt, [])
    assert body.strip()
    if fmt == "json":
        assert json.loads(body)["rows"] == []
    if fmt == "csv":
        assert body.startswith("baseId,")
