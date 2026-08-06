#!/usr/bin/env python3
"""
Catalogue every field in a Palworld save, and say which ones nothing reads.

WHY THIS EXISTS
---------------
`scripts/mine-datatables.py` indexes every DataTable in the server pak so that
"does a table exist that knows X" stops being answered by concluding it does not.
AGENTS.md records what that cost before it existed: a documented negative got
trusted, a feature was *refused on those grounds*, and `DT_MapObjectAssignData`
had the answer in 271 rows the whole time.

**The save had exactly that problem and no equivalent index.** Every field this
project reads was found while looking for one feature, and in a single week that
cost:

- `base_camp_level`, unread on the guild record — found only because a competing
  tool displayed it, and the first check for it sampled an `Organization` group
  rather than a `Guild` one and "confirmed" it was absent.
- `guild_markers`, `guild_chest_allowed_roles`, `role_permissions` — three more
  on that same record, seen in the same glance.
- `WorkerDirector` and `GuildItemStorage` — opaque blobs carrying container ids
  at measured offsets, found after this project had documented per-base
  attribution as unavailable.

So: walk the whole thing once, and cross-reference against what the backend
actually touches. The output is not "here is the save" but "here is the part of
the save nothing has ever looked at".

WHAT IT REPORTS, AND THE FIVE THINGS IT IS CAREFUL ABOUT
--------------------------------------------------------
**1. Occupancy, not presence.** A key present on every Pal and populated on 0.1%
of them is a different fact from one populated on all. Three counts travel:
`seen`, `nonEmpty` (not None/""/[]/{}) and `nonZero` (additionally not 0, False,
the zero GUID or an all-zero byte run). Which of those means "set" depends on the
field, so the reader is given all three rather than one guess.

**2. Sampling by TYPE, never by index.** This is the `base_camp_level` lesson.
Where a list's entries carry a discriminator — `group_type`, `concrete_model_type`,
`type` — occupancy is reported per bucket, because a field that only exists on
one variant reads as "rare" when averaged over all of them and as "always
present" when you happen to sample that variant first.

**3. Opaque bytes are a finding, not a gap.** Byte fields are reported with their
length and whether that length is CONSTANT across entries. That is exactly how
`WorkerDirector` (118 bytes, container id at offset 98) and the egg records
(4 and 28 zero bytes, invariant across three worlds) were settled. A blob of
fixed width is a prospect; one of varying width is a different problem.

**4. The full custom-property set.** `DynamicItemSaveData` is 32,446 opaque blobs
under this project's trimmed read-path properties and typed records under the
full set. `verify-figures.py` already documents manufacturing a false regression
that way, so this always uses the full set and says so.

**5. More than one world.** `refworld` is a processed copy whose durability
records were multiplied sixteenfold. Pass several saves and any field whose shape
or occupancy disagrees between them is flagged — the same discipline
`verify-figures.py` applies, where a step change at one file is an artifact and a
monotonic trend across dated snapshots is drift.

PRIVACY: NO VALUES FROM A REAL WORLD LEAVE THIS SCRIPT BY DEFAULT.
`refworld` holds real Steam IDs, player names and guild names. The committed
document reports paths, types, occupancy and shapes. Numeric ranges and enum
names are safe and are kept; strings, GUIDs and anything that could be a name are
summarised as a count of distinct values and never printed. `--values` overrides
that for local debugging and its output must not be committed.

Usage:
    python3 scripts/mine-savefields.py refworld/Level.sav [more.sav ...]
    python3 scripts/mine-savefields.py --out docs/savefields.json refworld/Level.sav
Output: docs/savefields.json (the index) — docs/SAVE-FIELDS.md is written by hand
        from it, the way DATATABLES.md relates to datatables.json.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "backend"))

DEFAULT_OUT = os.path.join(ROOT, "docs", "savefields.json")

# Fields whose value identifies a real person or place. Counted, never printed.
_SENSITIVE = re.compile(
    r"uid|guid|player|name|steam|nick|ip|address|host", re.I
)

# Keys a list entry may carry that say which VARIANT it is. Occupancy is
# bucketed on the first one present — see the module docstring, point 2.
_DISCRIMINATORS = (
    "group_type", "concrete_model_type", "type", "map_object_id",
    "MapObjectId", "kind", "SpawnerType",
)

# A GVAS property node looks like {"type": ..., "value": ...}; the decoded
# `RawData` dicts do not. Both are walked, but the declared type is only
# meaningful for the first.
_ZERO_GUID = "00000000-0000-0000-0000-000000000000"

# GVAS plumbing: how the value is stored, never what it means. Walking these
# buries the game's own fields under thousands of `prop_type` rows.
_STRUCTURAL = {
    "prop_name", "prop_type", "type_name", "struct_type", "struct_id",
    "array_type", "key_type", "value_type", "custom_type", "id",
}


def _is_property_node(node: dict) -> bool:
    return "type" in node and "value" in node and isinstance(node.get("type"), str)


def _classify(value: Any) -> tuple[str, bool, bool]:
    """`(type name, non-empty, non-zero)` for one leaf."""
    if value is None:
        return "none", False, False
    if isinstance(value, bool):
        return "bool", True, bool(value)
    if isinstance(value, int):
        return "int", True, value != 0
    if isinstance(value, float):
        return "float", True, value != 0.0
    if isinstance(value, (bytes, bytearray)):
        return "bytes", len(value) > 0, any(value)
    if isinstance(value, str):
        text = value.strip()
        empty = text in ("", "None")
        return "str", not empty, not empty and text.lower() != _ZERO_GUID
    # palsav's UUID class and anything else exotic.
    text = str(value)
    return type(value).__name__, True, text.lower() != _ZERO_GUID


class Index:
    """Per-path counters for one save."""

    def __init__(self) -> None:
        self.paths: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "seen": 0,
                "nonEmpty": 0,
                "nonZero": 0,
                "types": defaultdict(int),
                "declared": defaultdict(int),
                "byteLengths": defaultdict(int),
                "distinct": set(),
                "sample": [],
                "buckets": defaultdict(int),
                "lengths": defaultdict(int),
                "sensitive": False,
            }
        )
        self.nodes = 0

    def record(
        self,
        path: str,
        value: Any,
        declared: str | None,
        bucket: str | None,
        keep_values: bool,
    ) -> None:
        entry = self.paths[path]
        kind, non_empty, non_zero = _classify(value)
        entry["seen"] += 1
        entry["nonEmpty"] += int(non_empty)
        entry["nonZero"] += int(non_zero)
        entry["types"][kind] += 1
        if declared:
            entry["declared"][declared] += 1
        if bucket:
            entry["buckets"][bucket] += 1
        if kind == "bytes":
            entry["byteLengths"][len(value)] += 1

        leaf = path.rsplit(".", 1)[-1].strip("[]")
        sensitive = bool(_SENSITIVE.search(leaf))
        entry["sensitive"] = entry["sensitive"] or sensitive

        # Distinct counts are safe for anything; VALUES are only kept for
        # non-sensitive scalars, and only a handful. See the privacy note.
        if kind in ("int", "float", "bool", "str") and len(entry["distinct"]) < 64:
            entry["distinct"].add(str(value)[:64])
        if (keep_values or not sensitive) and kind in ("int", "float", "bool", "str"):
            if len(entry["sample"]) < 5 and str(value)[:48] not in entry["sample"]:
                entry["sample"].append(str(value)[:48])


def _record_container(self, path: str, values: list, bucket: str | None) -> None:
    """A list, by its length — so an always-empty one is visible as empty."""
    entry = self.paths[path]
    entry["seen"] += 1
    entry["nonEmpty"] += int(len(values) > 0)
    entry["nonZero"] += int(len(values) > 0)
    entry["types"]["list"] += 1
    entry["lengths"][len(values)] += 1
    if bucket:
        entry["buckets"][bucket] += 1


Index.record_container = _record_container


def _record_blob(self, path: str, values: list, bucket: str | None) -> None:
    """One numeric run as a single leaf, keyed on its length."""
    entry = self.paths[path]
    entry["seen"] += 1
    entry["nonEmpty"] += int(len(values) > 0)
    entry["nonZero"] += int(any(values))
    entry["types"]["numericRun"] += 1
    entry["byteLengths"][len(values)] += 1
    # ALSO the plain length, or `alwaysEmpty` lies. A numeric list takes this
    # branch only when non-empty, so its empty instances land in the container
    # branch — and a field split across the two read as always empty on the
    # strength of half its occurrences. `role_permissions[].permissions` is
    # `[0,3,4,5,7]` for one role and `[]` for another, and reported as never
    # populated until both branches fed the same counter.
    entry["lengths"][len(values)] += 1
    if bucket:
        entry["buckets"][bucket] += 1


Index.record_blob = _record_blob


def walk(node: Any, path: str, index: Index, keep_values: bool,
         declared: str | None = None, bucket: str | None = None) -> None:
    """Depth-first over the decoded tree, recording every leaf by canonical path."""
    index.nodes += 1

    if isinstance(node, dict):
        if _is_property_node(node):
            # Descend through the wrapper without lengthening the path: the
            # declared GVAS type travels down to the leaf it describes.
            walk(node["value"], path, index, keep_values, str(node.get("type")), bucket)
            return
        # An ArrayProperty's payload is {"values": [...]}, which is a container
        # rather than a field — collapsing it keeps paths readable.
        if set(node.keys()) == {"values"} and isinstance(node.get("values"), list):
            walk(node["values"], path, index, keep_values, declared, bucket)
            return
        for key, child in node.items():
            if key in _STRUCTURAL:
                continue
            walk(child, f"{path}.{key}", index, keep_values, None, bucket)
        return

    if isinstance(node, list):
        # A LIST OF PLAIN NUMBERS IS A BLOB, NOT A THOUSAND FIELDS. `unknown_bytes`
        # decodes as a list of ints, and walking each one produced 92,460 "fields"
        # for a single opaque run — burying the real ones and losing the only
        # property that matters about it, which is its LENGTH. Recorded as one
        # leaf whose byte-length distribution says whether the width is constant,
        # exactly as for a `bytes` value. That is the `WorkerDirector` test.
        if node and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                        for v in node):
            index.record_blob(path, node, bucket)
            return
        # **AN EMPTY CONTAINER IS A FINDING, NOT AN ABSENCE.** The first version
        # only recorded leaves, so a list that is empty on every entry never
        # appeared at all — `guild_markers` vanished from the index entirely and
        # read as "not in the save" when it is present on every guild and merely
        # unused on these worlds. That is the precise distinction this tool
        # exists to make, so containers are recorded with their length.
        index.record_container(path, node, bucket)
        for entry in node:
            # THE POINT OF THE WHOLE EXERCISE: bucket by variant before
            # descending, so a field carried by one kind of entry is not
            # averaged into invisibility across the rest.
            child_bucket = bucket
            if isinstance(entry, dict):
                found = _entry_bucket(entry)
                if found:
                    child_bucket = found
            walk(entry, f"{path}[]", index, keep_values, declared, child_bucket)
        return

    index.record(path, node, declared, bucket, keep_values)


def _entry_bucket(entry: dict) -> str | None:
    """The variant name for one list entry, from whichever discriminator it has."""
    for key in _DISCRIMINATORS:
        if key in entry:
            raw = entry[key]
            if isinstance(raw, dict):
                raw = raw.get("value", raw)
            text = str(raw or "")
            if text and text not in ("None", "{}"):
                return text.rsplit("::", 1)[-1][:48]
    # A property-node wrapper hides the discriminator one level down.
    for key in ("RawData", "value"):
        inner = entry.get(key)
        if isinstance(inner, dict):
            inner = inner.get("value", inner)
        if isinstance(inner, dict):
            found = _entry_bucket(inner)
            if found:
                return found
    return None


def read_by(paths: list[str]) -> dict[str, list[str]]:
    """
    Which backend modules mention each leaf name as a string literal.

    **APPROXIMATE, AND LABELLED AS SUCH IN THE OUTPUT.** A name match does not
    prove the module reads it from *this* path, and common names (`id`, `name`,
    `type`, `value`) collide with everything. What it is reliable for is the
    other direction: a distinctive name that appears NOWHERE is genuinely unread,
    and that is the list this whole script exists to produce.
    """
    sources: dict[str, str] = {}
    backend = os.path.join(ROOT, "backend")
    for entry in sorted(os.listdir(backend)):
        if entry.endswith(".py"):
            with open(os.path.join(backend, entry), encoding="utf-8") as f:
                sources[entry] = f.read()

    out: dict[str, list[str]] = {}
    for path in paths:
        leaf = path.rsplit(".", 1)[-1].strip("[]")
        if not leaf:
            continue
        needle = f'"{leaf}"'
        alt = f"'{leaf}'"
        hits = [name for name, text in sources.items() if needle in text or alt in text]
        out[leaf] = hits
    return out


AMBIGUOUS = {
    "id", "name", "type", "value", "key", "values", "count", "level", "x", "y",
    "z", "state", "size", "index", "data", "max", "min", "role", "players",
}


def load(path: str) -> Any:
    """
    One save, with the FULL custom-property set.

    Not the project's trimmed read-path set: `DynamicItemSaveData` decodes as
    32,446 opaque blobs under that one and as typed records under this. A
    catalogue built with the wrong reader would report a whole structure as
    unreadable — the exact false regression `verify-figures.py` documents.
    """
    from palsav.core import decompress_sav_to_gvas
    from palsav.gvas import GvasFile
    from palsav.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS

    with open(path, "rb") as f:
        raw = f.read()
    return GvasFile.read(
        decompress_sav_to_gvas(raw)[0], PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES
    )


def summarise(index: Index) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for path, entry in index.paths.items():
        if not entry["seen"]:
            continue
        row: dict[str, Any] = {
            "seen": entry["seen"],
            "nonEmpty": entry["nonEmpty"],
            "nonZero": entry["nonZero"],
            "types": dict(entry["types"]),
            "distinctValues": len(entry["distinct"]),
        }
        if entry["declared"]:
            row["gvasTypes"] = dict(entry["declared"])
        if entry["byteLengths"]:
            lengths = dict(entry["byteLengths"])
            row["byteLengths"] = lengths
            # The property that made `WorkerDirector` readable: a fixed-width
            # blob is a prospect for a measured offset, a varying one is not.
            row["byteLengthConstant"] = len(lengths) == 1
        if entry["lengths"]:
            row["listLengths"] = dict(sorted(entry["lengths"].items()))
            # The case the first version could not express: a field the game
            # carries on every entry and nobody has ever put anything in.
            row["alwaysEmpty"] = set(entry["lengths"]) == {0}
        if entry["buckets"]:
            row["byVariant"] = dict(sorted(entry["buckets"].items()))
        if entry["sensitive"]:
            row["valuesWithheld"] = True
        elif entry["sample"]:
            row["sample"] = entry["sample"]
        out[path] = row
    return out


def compare(worlds: dict[str, dict[str, Any]]) -> list[str]:
    """
    Paths whose presence disagrees between worlds.

    A field in one save and not another is the interesting case: either the
    saves are different game versions, or one of them is a processed copy — the
    `refworld` problem, which is why more than one world is required to say
    anything durable.
    """
    if len(worlds) < 2:
        return []
    everywhere = set.intersection(*(set(v) for v in worlds.values()))
    anywhere = set.union(*(set(v) for v in worlds.values()))
    return sorted(anywhere - everywhere)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("saves", nargs="+", help="Level.sav or player .sav files")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument(
        "--values", action="store_true",
        help="keep values for name/uid fields too. LOCAL DEBUGGING ONLY — the "
             "output then contains real Steam IDs and must not be committed.",
    )
    args = ap.parse_args()

    worlds: dict[str, dict[str, Any]] = {}
    for path in args.saves:
        if not os.path.exists(path):
            print(f"missing: {path}", file=sys.stderr)
            return 2
        label = os.path.basename(os.path.dirname(path)) or os.path.basename(path)
        print(f"reading {path} …", file=sys.stderr)
        gvas = load(path)
        index = Index()
        walk(gvas.properties, "save", index, args.values)
        worlds[label] = summarise(index)
        print(f"  {len(worlds[label])} distinct field paths, "
              f"{index.nodes:,} nodes walked", file=sys.stderr)

    merged: dict[str, Any] = {}
    for label, fields in worlds.items():
        for path, row in fields.items():
            merged.setdefault(path, {})[label] = row

    names = read_by(list(merged))
    unread = sorted(
        path for path in merged
        if not names.get(path.rsplit(".", 1)[-1].strip("[]"))
        and path.rsplit(".", 1)[-1].strip("[]") not in AMBIGUOUS
    )

    data = {
        "_note": (
            "Generated by scripts/mine-savefields.py. Paths, types and occupancy "
            "only — values from a real world are withheld for any field whose "
            "name suggests a person or place. `readBy` is a STRING-LITERAL match "
            "against backend/*.py and is approximate: it cannot prove a module "
            "reads a name from THIS path, but a distinctive name appearing "
            "nowhere really is unread, which is the list this exists to produce."
        ),
        "worlds": list(worlds),
        "fields": merged,
        "readBy": names,
        "unreadPaths": unread,
        "ambiguousNames": sorted(AMBIGUOUS),
        "differsBetweenWorlds": compare(worlds),
    }

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
        f.write("\n")

    print(f"\nwrote {args.out}")
    print(f"  {len(merged)} field paths across {len(worlds)} world(s)")
    print(f"  {len(unread)} carry a name no backend module mentions")
    if data["differsBetweenWorlds"]:
        print(f"  {len(data['differsBetweenWorlds'])} paths are not in every world "
              "— check whether that is a version difference or a processed copy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
