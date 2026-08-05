#!/usr/bin/env python3
"""
Catalogue every DataTable in the server pak: name, row count, columns, sample.

WHY THIS EXISTS, AND IT IS A PROCESS FIX RATHER THAN A FEATURE
--------------------------------------------------------------
Twice in a row a feature shipped with a documented claim that some fact was "not
in any game file", and twice the fact was in a sibling table nobody had listed:

  * the base supply advisor recorded that `DT_MapObjectMasterDataTable` carries
    no consumption semantics — true — and concluded the data did not exist;
  * the work optimiser recorded that no build-object-to-work-suitability mapping
    exists, and refused to build base assignment on those grounds.

`DT_MapObjectAssignData` carries exactly that mapping, in 271 rows, and decodes
cleanly. The refusals were honest about what had been *checked* and wrong about
what was *there*, which is the worse of the two failure modes: a documented
negative gets trusted and stops anyone looking again.

The root cause is searching per-feature. `read_table` decodes 899 of 912 tables,
so the whole schema is available at once and there is no reason to keep
rediscovering it one disappointment at a time. **Run this before designing a
feature, not after.**

WHAT IT WRITES
--------------
`docs/DATATABLES.md`  — the human index: every table, its size, its columns.
`docs/datatables.json` — the same thing machine-readable, for grepping columns.

**Columns and a single sample row, not the data.** The point is to answer "does
a table exist that knows X", which needs the schema; bundling contents would be
a 100 MB commit of data the app does not read. Anything actually needed gets its
own extraction script with its own verification, as `extract-boss-spawners.py`
and `extract-passive-effects.py` do.

REFUSALS ARE RECORDED, NOT HIDDEN. The 13 that do not decode are listed with
their error, because "this table exists and we cannot read it" is a different
and more useful statement than silence.

DETECTING A GAME UPDATE'S NEW CONTENT
-------------------------------------
`--check` diffs the pak against the committed `docs/datatables.json` and names
what appeared, vanished, changed columns or changed row count. Exit 1 on any
difference, so a cron or CI step can use it as a signal.

**This is the only thing here that can spot content the dashboard does not know
about.** `gameversion` compares build ids and answers "are the bundles stale",
which is a different question — regenerating them reproduces exactly what was
already known. Every extractor finds its table by exact name, so a renamed table
raises and a new one is simply invisible.

The dashboard's update banner points at this command, so an operator who never
reads this file still gets told.

Usage:  python3 scripts/mine-datatables.py [--grep PATTERN] [--check]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import palpak            # noqa: E402
import uassettable       # noqa: E402

ROOT = os.path.dirname(HERE)
MD_OUT = os.path.join(ROOT, "docs", "DATATABLES.md")
JSON_OUT = os.path.join(ROOT, "docs", "datatables.json")

# A sample row is truncated to this, so one pathological table cannot dominate
# the document.
SAMPLE_CHARS = 300


def _columns(rows: dict) -> list[str]:
    """
    Union of keys across rows, ordered by how often they appear.

    A union rather than the first row's keys: a tagged property that is absent
    on a row is simply not serialised, so the first row is not the schema. On
    `DT_MapObjectAssignData` the farm plots carry three work slots and most
    objects carry one, which the first row would not show.
    """
    counts: Counter = Counter()
    for row in rows.values():
        if isinstance(row, dict):
            counts.update(row.keys())
    return [name for name, _ in counts.most_common()]


def sweep(pak) -> tuple[list[dict], list[dict]]:
    seen: set[str] = set()
    decoded: list[dict] = []
    refused: list[dict] = []

    for path in sorted(pak.files):
        name = path.split("/")[-1]
        if not name.startswith("DT_") or not name.endswith(".uasset"):
            continue
        # The pak ships localised duplicates of many tables under L10N/. One
        # entry per table name: the schema is identical and 20 copies of
        # DT_UI_Common_Text is noise that buries the tables that matter.
        if name in seen:
            continue
        seen.add(name)

        try:
            rows = uassettable.read_table(pak, path)
        except Exception as exc:  # noqa: BLE001 - cataloguing failures is the job
            refused.append({
                "table": name,
                "path": path,
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue

        sample_key = next(iter(rows), None)
        decoded.append({
            "table": name,
            "path": path,
            "rows": len(rows),
            "columns": _columns(rows),
            "sampleKey": str(sample_key) if sample_key is not None else "",
            "sample": json.dumps(rows.get(sample_key), default=str)[:SAMPLE_CHARS]
            if sample_key is not None else "",
        })

    return decoded, refused


def write_markdown(decoded: list[dict], refused: list[dict]) -> None:
    lines = [
        "# Server-pak DataTable index",
        "",
        "Generated by `scripts/mine-datatables.py`. **Do not hand-edit.**",
        "",
        "This is a *schema* index, not data: table name, row count and column",
        "names. It exists so that \"is fact X in a game file?\" is answered by",
        "grepping this document rather than by concluding it is not.",
        "",
        "Two features shipped documented refusals that this index would have",
        "prevented — see the script's docstring. Check here first.",
        "",
        f"**{len(decoded)} tables decode, {len(refused)} refuse.**",
        "",
        "## Decodable",
        "",
        "| Table | Rows | Columns |",
        "|---|---:|---|",
    ]
    for entry in sorted(decoded, key=lambda e: e["table"]):
        cols = ", ".join(f"`{c}`" for c in entry["columns"][:14])
        if len(entry["columns"]) > 14:
            cols += f", … (+{len(entry['columns']) - 14})"
        lines.append(f"| `{entry['table'][:-7]}` | {entry['rows']} | {cols or '—'} |")

    if refused:
        lines += [
            "",
            "## Refused",
            "",
            "Listed rather than omitted: \"this exists and we cannot read it\" is",
            "a different statement from silence, and a future reader deserves the",
            "distinction.",
            "",
            "| Table | Why |",
            "|---|---|",
        ]
        for entry in sorted(refused, key=lambda e: e["table"]):
            lines.append(f"| `{entry['table'][:-7]}` | {entry['error'][:120]} |")

    lines.append("")
    with open(MD_OUT, "w") as f:
        f.write("\n".join(lines))


def check(decoded: list[dict], refused: list[dict]) -> int:
    """
    What changed since the committed index — the answer to "will this notice a
    game update?".

    **Nothing else here detects a NEW table.** Every extractor finds its source
    by exact name (`endswith("DT_Foo.uasset")`), so a renamed table raises and a
    table that did not exist before is simply invisible — no error, no warning,
    just an absence nobody is looking for. `gameversion` compares build ids and
    reports that bundles are *stale*, which is a different question from what the
    game now contains.

    Since `docs/datatables.json` is committed, the pak can be diffed against it.
    Run this after a game update, before deciding which extractors to re-run.

    A changed column list matters as much as a new table: an extractor reading a
    renamed column gets `None` and writes a silent zero, which is the failure
    mode this project keeps meeting.

    Exit 1 on any difference so CI or a cron can use it as a signal.
    """
    try:
        with open(JSON_OUT) as f:
            previous = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"No committed index to compare against ({e}). Run without "
              "--check first.", file=sys.stderr)
        return 2

    was = {e["table"]: e for e in previous.get("decoded") or []}
    was_refused = {e["table"] for e in previous.get("refused") or []}
    now = {e["table"]: e for e in decoded}
    now_refused = {e["table"] for e in refused}

    added = sorted(set(now) - set(was) - was_refused)
    removed = sorted(set(was) - set(now) - now_refused)
    newly_readable = sorted(was_refused & set(now))
    newly_refused = sorted(set(was) & now_refused)

    changed = []
    for name in sorted(set(now) & set(was)):
        before, after = was[name], now[name]
        cols_before, cols_after = set(before["columns"]), set(after["columns"])
        if cols_before != cols_after:
            changed.append((name, sorted(cols_after - cols_before),
                            sorted(cols_before - cols_after)))
        elif before["rows"] != after["rows"]:
            changed.append((name, [], []))

    if not any((added, removed, newly_readable, newly_refused, changed)):
        print(f"No change: {len(now)} tables, same columns and row counts.")
        return 0

    for name in added:
        print(f"NEW TABLE   {name[:-7]}  ({now[name]['rows']} rows)")
        print(f"              {', '.join(now[name]['columns'][:12])}")
    for name in removed:
        print(f"GONE        {name[:-7]}")
    for name in newly_readable:
        print(f"NOW READS   {name[:-7]}  ({now[name]['rows']} rows) — it used to refuse")
    for name in newly_refused:
        print(f"NOW REFUSES {name[:-7]} — it used to decode")
    for name, gained, lost in changed:
        rows_before, rows_after = was[name]["rows"], now[name]["rows"]
        detail = []
        if gained:
            detail.append(f"+{gained}")
        if lost:
            detail.append(f"-{lost}")
        if rows_before != rows_after:
            detail.append(f"rows {rows_before} -> {rows_after}")
        print(f"CHANGED     {name[:-7]}  {'; '.join(detail)}")

    print(
        "\nA changed column is the dangerous one: an extractor reading a renamed "
        "column gets None and writes a silent zero. Re-run the affected "
        "extractors with --verify before regenerating anything."
    )
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--grep", help="print tables whose name or columns match")
    ap.add_argument(
        "--check", action="store_true",
        help="compare the pak against the committed index and report what changed",
    )
    args = ap.parse_args()

    pak = palpak.Pak()
    decoded, refused = sweep(pak)

    if args.grep:
        needle = args.grep.lower()
        for entry in decoded:
            hit = needle in entry["table"].lower() or any(
                needle in c.lower() for c in entry["columns"]
            )
            if hit:
                print(f"{entry['table'][:-7]}  ({entry['rows']} rows)")
                print(f"    {', '.join(entry['columns'])}")
                print(f"    e.g. {entry['sampleKey']}: {entry['sample'][:200]}")
        return 0

    if args.check:
        return check(decoded, refused)

    os.makedirs(os.path.dirname(MD_OUT), exist_ok=True)
    write_markdown(decoded, refused)
    with open(JSON_OUT, "w") as f:
        json.dump({"decoded": decoded, "refused": refused}, f, indent=1, sort_keys=True)

    total_rows = sum(e["rows"] for e in decoded)
    print(f"wrote {MD_OUT}")
    print(f"wrote {JSON_OUT}")
    print(f"  {len(decoded)} tables decoded, {total_rows:,} rows, "
          f"{len(refused)} refused")
    return 0


if __name__ == "__main__":
    sys.exit(main())
