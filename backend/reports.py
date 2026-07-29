"""
Inventory reports — CSV, JSON and plain text.

These exist so people can take what the dashboard knows somewhere else: a
spreadsheet, a Discord message, a diff against last week. Everything here is
read-only and pure; it renders already-parsed data and never touches a save.

Rendering is deliberately kept out of the request handlers so the three formats
cannot drift apart, and so the row set is defined exactly once per report.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Iterable

import gamedata

FORMATS = ("csv", "json", "txt")

MEDIA_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "json": "application/json",
    "txt": "text/plain; charset=utf-8",
}


class ReportError(Exception):
    """Raised for an unknown report or format."""


# ─── Row builders ────────────────────────────────────────


def _world_items_rows(items: list[dict]) -> tuple[list[str], list[list[Any]]]:
    """Server-wide item totals, richest first."""
    headers = ["itemId", "itemName", "count", "maxStack", "fullStacks"]
    rows = []
    for entry in items:
        item_id = entry.get("itemId") or ""
        count = int(entry.get("count") or 0)
        cap = gamedata.max_stack(item_id)
        rows.append([
            item_id,
            gamedata.item_name(item_id),
            count,
            cap or "",
            (count // cap) if cap else "",
        ])
    return headers, rows


def _base_summary_rows(base_storage: list[dict]) -> tuple[list[str], list[list[Any]]]:
    """One row per base: how much it holds and how full it is."""
    headers = [
        "baseId", "baseName", "guildName", "containers",
        "usedSlots", "totalSlots", "fillPercent", "itemCount", "uniqueItems",
    ]
    rows = [
        [
            b["baseId"], b["baseName"], b["guildName"], b["containerCount"],
            b["usedSlots"], b["totalSlots"], b["fillPercent"],
            b["itemCount"], b["uniqueItems"],
        ]
        for b in base_storage
    ]
    return headers, rows


def _base_items_rows(base_storage: list[dict]) -> tuple[list[str], list[list[Any]]]:
    """One row per (base, item) — the join people actually want in a spreadsheet."""
    headers = ["baseId", "baseName", "guildName", "itemId", "itemName", "count"]
    rows = [
        [b["baseId"], b["baseName"], b["guildName"], it["itemId"], it["itemName"], it["count"]]
        for b in base_storage
        for it in b["items"]
    ]
    return headers, rows


def _containers_rows(base_storage: list[dict]) -> tuple[list[str], list[list[Any]]]:
    """One row per container, so a full chest is findable by name."""
    headers = [
        "baseId", "baseName", "containerId", "kind", "kindName",
        "category", "usedSlots", "totalSlots", "fillPercent", "itemCount",
    ]
    rows = []
    for base in base_storage:
        for c in base["containers"]:
            total = c["totalSlots"]
            rows.append([
                base["baseId"], base["baseName"], c["containerId"],
                c["kind"], c["kindName"], c["category"] or "",
                c["usedSlots"], total,
                round(100 * c["usedSlots"] / total, 1) if total else 0.0,
                c["itemCount"],
            ])
    return headers, rows


REPORTS = {
    "world-items": ("Server-wide item totals", _world_items_rows, "items"),
    "base-summary": ("Storage by base", _base_summary_rows, "baseStorage"),
    "base-items": ("Items by base", _base_items_rows, "baseStorage"),
    "containers": ("Every container", _containers_rows, "baseStorage"),
}


# ─── Rendering ───────────────────────────────────────────


def render(report: str, fmt: str, data: Any, meta: dict | None = None) -> str:
    """Render one report in one format. `data` is the section it declares."""
    if report not in REPORTS:
        raise ReportError(
            f"Unknown report '{report}'. Available: {', '.join(sorted(REPORTS))}"
        )
    if fmt not in FORMATS:
        raise ReportError(f"Unknown format '{fmt}'. Available: {', '.join(FORMATS)}")

    title, build, _section = REPORTS[report]
    headers, rows = build(data or [])

    if fmt == "csv":
        return _to_csv(headers, rows)
    if fmt == "json":
        return _to_json(title, headers, rows, meta or {})
    return _to_txt(title, headers, rows, meta or {})


def section_for(report: str) -> str:
    """Which parsed section a report needs, so callers fetch only that."""
    if report not in REPORTS:
        raise ReportError(f"Unknown report '{report}'")
    return REPORTS[report][2]


def _to_csv(headers: list[str], rows: Iterable[list[Any]]) -> str:
    buf = io.StringIO()
    # QUOTE_MINIMAL with \r\n: Excel's expected dialect, and item names contain
    # commas ("Pal Sphere, Mega") often enough to matter.
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return buf.getvalue()


def _to_json(title: str, headers: list[str], rows: list[list[Any]], meta: dict) -> str:
    return json.dumps(
        {
            "report": title,
            "rowCount": len(rows),
            **meta,
            "rows": [dict(zip(headers, row)) for row in rows],
        },
        indent=2,
        ensure_ascii=False,
    )


def _to_txt(title: str, headers: list[str], rows: list[list[Any]], meta: dict) -> str:
    """Fixed-width columns — meant to be pasted into Discord or a terminal."""
    widths = [len(h) for h in headers]
    rendered = [[("" if c is None else str(c)) for c in row] for row in rows]
    for row in rendered:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    # Numeric columns right-align; everything else left-aligns.
    numeric = [
        all(_looks_numeric(row[i]) for row in rendered) if rendered else False
        for i in range(len(headers))
    ]

    def line(cells: list[str]) -> str:
        return "  ".join(
            cell.rjust(widths[i]) if numeric[i] else cell.ljust(widths[i])
            for i, cell in enumerate(cells)
        ).rstrip()

    out = [title, "=" * len(title)]
    for key, value in meta.items():
        out.append(f"{key}: {value}")
    out.append("")
    out.append(line(headers))
    out.append("  ".join("-" * w for w in widths))
    out.extend(line(row) for row in rendered)
    out.append("")
    out.append(f"{len(rows)} row" if len(rows) == 1 else f"{len(rows)} rows")
    return "\n".join(out) + "\n"


def _looks_numeric(cell: str) -> bool:
    if cell == "":
        return True  # blanks do not force a column to become text
    try:
        float(cell)
        return True
    except ValueError:
        return False
