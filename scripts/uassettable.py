#!/usr/bin/env python3
"""
Decode a Palworld **server** pak DataTable — rows, columns and numbers.

    python3 scripts/uassettable.py DT_PalShopCreateData
    python3 scripts/uassettable.py --list                # every DataTable in the pak
    python3 scripts/uassettable.py DT_ItemLotteryDataTable --out drops.json.gz

WHY THIS IS POSSIBLE, WHEN `upackage.py` SAYS IT IS NOT
-------------------------------------------------------
`upackage.py` documents that Palworld's packages are cooked with **unversioned
properties**, so property names are absent and only name tables are readable.
That is true — of `refs/Pal-Windows.pak`, the **client** pak, which is where it
was measured.

The **server** pak is cooked differently. `Pal-LinuxServer.pak` writes tagged
properties: every property carries its name, type and size inline, so a
DataTable decodes completely — including every number that was previously
written off as locked.

The tell is a name-table diff, not a version field. `FileVersionUE4` and
`FileVersionUE5` are **0 in both paks**, so they distinguish nothing. What
differs is that the server's name tables contain type names (`IntProperty`,
`ArrayProperty`) and column names (`Cost`, `MinCharacterLevel`), while the
client's are a strict subset with those removed — for four tables checked, zero
names were unique to the client.

THE FORMAT, MEASURED
--------------------
A property tag:

    FName   name              (int32 index, int32 number; number>0 => "_N" suffix of N-1)
    FName   type
    int32   size              bytes of value that follow the tag
    int32   arrayIndex
    ...     type-specific extra (see `_tag`)
    uint8   hasPropertyGuid
    [16]    guid, only when that byte is set
    size    bytes of value

`None` as a name terminates a property list. A DataTable is: the object's own
tagged properties (`RowStruct`), the terminator, a short table header, then for
each row an FName row name followed by that row's tagged properties.

THE VERIFICATION, WHICH IS NOT OPTIONAL
---------------------------------------
**A correct walk ends exactly at the last byte.** A drifted reader does not — it
runs off the end or stops early. This matters more than it sounds, because a
drifted tagged-property reader produces *plausible* output rather than an
exception: two earlier attempts yielded row names that were real Pal ids with
nonsense suffixes (`CactusDoll_100`) and column values that looked like data.

So `read_table` refuses to return a partial decode, and the row-section offset is
**found by trying candidates and keeping the one whose walk terminates exactly at
the end** rather than by hardcoding a measured constant. That makes the
acceptance criterion the verification itself, and it means a table whose header
differs by a few bytes is decoded rather than silently mangled.

READ-ONLY.
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
from typing import Any, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import upackage  # noqa: E402
from palpak import Pak  # noqa: E402


class TableError(Exception):
    """A decode that could not be verified. Never a partial result."""


class _Reader:
    __slots__ = ("b", "o", "names")

    def __init__(self, body: bytes, names: list[str]) -> None:
        self.b = body
        self.o = 0
        self.names = names

    def i32(self) -> int:
        v = struct.unpack_from("<i", self.b, self.o)[0]
        self.o += 4
        return v

    def u8(self) -> int:
        v = self.b[self.o]
        self.o += 1
        return v

    def u32(self) -> int:
        v = struct.unpack_from("<I", self.b, self.o)[0]
        self.o += 4
        return v

    def fstring(self) -> str:
        """
        An FString. **A negative length means UTF-16**; reading it as UTF-8
        yields plausible mojibake rather than an error, so the sign is
        interpreted in exactly one place.
        """
        n = self.i32()
        if n == 0:
            return ""
        if n < 0:
            raw = self.b[self.o:self.o - n * 2]
            self.o += -n * 2
            return raw.decode("utf-16-le", "replace").rstrip("\0")
        raw = self.b[self.o:self.o + n]
        self.o += n
        return raw.decode("utf-8", "replace").rstrip("\0")

    def name(self) -> str:
        idx, num = struct.unpack_from("<ii", self.b, self.o)
        self.o += 8
        if not 0 <= idx < len(self.names):
            raise TableError(f"name index {idx} out of range at byte {self.o - 8}")
        base = self.names[idx]
        return f"{base}_{num - 1}" if num else base


def _tag(r: _Reader) -> Optional[tuple[str, str, int, dict]]:
    """One property tag, or None at the list terminator."""
    name = r.name()
    if name == "None":
        return None
    typ = r.name()
    size = r.i32()
    r.i32()                                   # arrayIndex

    extra: dict[str, Any] = {}
    if typ == "StructProperty":
        # The struct name AND a 16-byte GUID. Missing either misplaces
        # everything after it, and the result reads as data rather than raising.
        extra["struct"] = r.name()
        r.o += 16
    elif typ == "BoolProperty":
        # A bool's value lives in the TAG, not in the value block — its `size`
        # is 0, so reading it like other types silently consumes the next tag.
        extra["bool"] = bool(r.u8())
    elif typ in ("ByteProperty", "EnumProperty"):
        extra["enum"] = r.name()
    elif typ in ("ArrayProperty", "SetProperty"):
        extra["inner"] = r.name()
    elif typ == "MapProperty":
        extra["key"] = r.name()
        extra["value"] = r.name()

    if r.u8():                                # hasPropertyGuid
        r.o += 16
    return name, typ, size, extra


def _text(r: _Reader, size: int) -> Any:
    """
    An `FText`, which the reader previously skipped as opaque — and that skip was
    hiding every display name in the game.

    Layout for `HistoryType::Base`, which is what a DataTable's text column uses:

        flags        u32
        historyType  i8   (0 = Base)
        namespace    FString
        key          FString
        sourceString FString

    **The source strings are Japanese**, because that is Palworld's source
    language. English is a translation and is not in here, so this does not
    supersede the reference archive for names — see `docs/GAMEDATA-SOURCES.md`.
    What it *does* give is the `namespace`/`key` pair, which is the identity a
    localisation lookup needs, and the Japanese original.

    Any other history type is skipped rather than guessed at: the tag carries its
    own size, so an unhandled variant costs one property instead of the table.
    That is the same discipline `_value` applies to unknown struct interiors.
    """
    end = r.o + size
    try:
        flags = r.u32()
        history = struct.unpack_from("<b", r.b, r.o)[0]
        r.o += 1
        if history != 0:
            r.o = end
            return {"_text": f"history {history}"}
        namespace = r.fstring()
        key = r.fstring()
        source = r.fstring()
    except Exception:  # noqa: BLE001 - the tag's size is the recovery
        r.o = end
        return {"_text": "undecodable"}
    r.o = end
    return {"namespace": namespace, "key": key, "source": source, "flags": flags}


#: Upper bound on a map's entry count and on `NumKeysToRemove`. Not a guess
#: about the data — the largest map in either pak is a few dozen entries — but a
#: tripwire: a value above this means the read offset is wrong, and refusing is
#: the whole contract of this reader.
_MAX_MAP_ENTRIES = 100_000


def _map_half(r: _Reader, typ: str) -> Any:
    """
    One key or one value inside a MapProperty, serialised **without a tag**.

    This is why a map needs its own reader rather than reusing `_value`: there
    is no tag, so there is no `size` to snap to and no way to skip a type we do
    not understand. An unknown type therefore RAISES, which the caller turns
    into an opaque label for the whole map — the honest outcome, because a map
    decoded up to the first unfamiliar entry is a dict that looks complete.

    A struct here is a tagged property list terminated by `None`, which is the
    ordinary case and the one that carries the interesting data.
    """
    if typ == "StructProperty":
        return _properties(r)
    if typ in ("NameProperty", "ByteProperty", "EnumProperty"):
        return r.name()
    if typ == "IntProperty":
        return r.i32()
    if typ == "BoolProperty":
        return bool(r.u8())
    if typ == "FloatProperty":
        value = struct.unpack_from("<f", r.b, r.o)[0]
        r.o += 4
        return round(value, 6)
    if typ == "DoubleProperty":
        value = struct.unpack_from("<d", r.b, r.o)[0]
        r.o += 8
        return value
    if typ == "StrProperty":
        length = r.i32()
        if length < 0 or length > 1 << 20:
            raise TableError(f"implausible string length {length} in map")
        text = r.b[r.o:r.o + max(length - 1, 0)].decode("utf-8", "replace")
        r.o += length
        return text
    raise TableError(f"unhandled map element type {typ!r}")


def _value(r: _Reader, typ: str, size: int, extra: dict) -> Any:
    """
    One property value.

    Scalars are read then the cursor is snapped to `start + size`, which keeps a
    misjudged scalar from cascading. Arrays cannot do that — their element
    layout is what is being walked — so they are the place a drift will show, and
    the end-of-buffer check is what catches it.
    """
    start = r.o

    if typ == "TextProperty":
        return _text(r, size)

    if typ == "BoolProperty":
        return extra.get("bool", False)

    if typ == "ArrayProperty":
        count = r.i32()
        inner = extra.get("inner")
        if inner == "StructProperty":
            _tag(r)                           # the array's own element tag
            # ELEMENTS MAY NOT BE TAGGED. UE serialises a handful of structs
            # natively — Vector, Rotator, Guid, LinearColor — writing their
            # fields raw with no property tags. Walking those as tagged reads
            # coordinates as name indices and drifts the rest of the table.
            #
            # There is no reliable way to know the element size for an arbitrary
            # native struct, so a failure here gives up on THIS COLUMN only:
            # snap to the end of its value block and mark it, leaving every
            # other column of every row intact. Returning a wrong list of
            # numbers would be far worse than saying which column was skipped.
            mark = r.o
            out = []
            try:
                for _ in range(count):
                    out.append(_properties(r))
            except (TableError, struct.error, IndexError):
                r.o = start + size
                return f"<{count} x {extra.get('struct') or 'struct'}, not tagged>"
            if r.o > start + size:
                r.o = start + size
                return f"<{count} x {extra.get('struct') or 'struct'}, overran>"
            del mark
            return out
        out = []
        for _ in range(count):
            if inner == "NameProperty":
                out.append(r.name())
            elif inner == "IntProperty":
                out.append(r.i32())
            elif inner == "FloatProperty":
                out.append(struct.unpack_from("<f", r.b, r.o)[0]); r.o += 4
            elif inner in ("ByteProperty", "EnumProperty"):
                out.append(r.name())
            else:
                r.o = start + size
                return f"<array of {inner}, {count} items, undecoded>"
        r.o = start + size
        return out

    if typ == "MapProperty":
        # THE ONE CONTAINER THAT WAS STILL OPAQUE, and it was standing in front
        # of the work-suitability curves for ten of the thirteen work types, the
        # breeding item effects and the egg hatching temperature table.
        #
        # Layout: `NumKeysToRemove` (0 on everything measured here), then
        # `NumEntries`, then each entry's key and value written RAW — no tags,
        # because the tag already named both types.
        #
        # Same recovery posture as the struct case below: on anything unexpected
        # snap to `start + size` and label it. The map's own tag carries its
        # length, so one undecodable map costs that property and never the row.
        try:
            # BOTH counts are bounds-checked, and the first one is the trap.
            # `NumKeysToRemove` is 0 on every map measured here, so it looked
            # like a formality — but on a misaligned read it is four arbitrary
            # bytes, and an unchecked `range()` over ~2 billion spins for hours
            # instead of raising. Found by pointing `mine-assets.py` at 7,643
            # blueprints, where the first asset whose layout this reader does not
            # expect hung the whole sweep with no error.
            #
            # A cap is not a guess about the data: nothing in this pak has a map
            # anywhere near it, so exceeding it means the offset is wrong, which
            # is exactly what the refusal is for.
            to_remove = r.i32()
            if not (0 <= to_remove <= _MAX_MAP_ENTRIES):
                raise TableError(f"implausible NumKeysToRemove {to_remove}")
            for _ in range(to_remove):
                _map_half(r, extra.get("key") or "")
            count = r.i32()
            if not (0 <= count <= _MAX_MAP_ENTRIES):
                raise TableError(f"implausible map entry count {count}")
            pairs = []
            for _ in range(count):
                key = _map_half(r, extra.get("key") or "")
                val = _map_half(r, extra.get("value") or "")
                pairs.append((key, val))
            # THE ACCEPTANCE CRITERION, and it is the one this reader already
            # uses everywhere else: the walk must land exactly on the end of the
            # value block. A decoder that stopped early or overran produced a
            # plausible dict, and a plausible dict is the failure mode this
            # module exists to refuse.
            if r.o != start + size:
                raise TableError(
                    f"map walk ended at {r.o - start} of {size} bytes"
                )
            return {str(k): v for k, v in pairs}
        except (TableError, struct.error, IndexError, UnicodeDecodeError) as e:
            r.o = start + size
            return f"<MapProperty {size}B, undecoded: {e}>"

    if typ == "StructProperty":
        # A NATIVELY-SERIALISED STRUCT HAS NO TAGS AT ALL. UE writes Vector,
        # Rotator, Guid, LinearColor and friends raw, so `_properties` reads the
        # first bytes as a name index, gets nonsense, and raises — which used to
        # abort the whole table and cost **243 of 912** DataTables in the server
        # pak, taking coverage from 98.6% down to 72%.
        #
        # Skipping is safe here in a way that a partial ROW decode is not, and
        # the distinction is the whole justification: the struct's length is in
        # its own tag, so `start + size` lands exactly on the next tag whatever
        # the interior turns out to be. Every surrounding property stays
        # correctly placed, `read_table`'s "the walk must end at the buffer end"
        # check still applies, and the unread interior is **labelled opaque**
        # rather than given a plausible value. Nothing is guessed; one field
        # says "not read" instead of the table saying nothing.
        try:
            fields: Any = _properties(r)
        except Exception:  # noqa: BLE001 - the tag's size is the recovery
            fields = {"_opaque": f"{extra.get('struct') or 'struct'} {size}B"}
        r.o = start + size
        return fields

    if typ == "IntProperty":
        value: Any = r.i32()
    elif typ == "FloatProperty":
        value = round(struct.unpack_from("<f", r.b, r.o)[0], 6)
    elif typ == "DoubleProperty":
        value = struct.unpack_from("<d", r.b, r.o)[0]
    elif typ in ("NameProperty", "ByteProperty", "EnumProperty"):
        value = r.name()
    elif typ == "StrProperty":
        length = struct.unpack_from("<i", r.b, start)[0]
        value = r.b[start + 4:start + 4 + max(length - 1, 0)].decode("utf-8", "replace")
    else:
        value = f"<{typ} {size}B>"

    r.o = start + size
    return value


def _properties(r: _Reader) -> dict:
    """Tagged properties up to the `None` terminator."""
    out: dict[str, Any] = {}
    while True:
        tag = _tag(r)
        if tag is None:
            return out
        name, typ, size, extra = tag
        out[name] = _value(r, typ, size, extra)


def _walk_rows(body: bytes, names: list[str], start: int) -> Optional[dict]:
    """
    Try to read rows from `start`. Returns None unless the walk is clean.

    "Clean" means it consumed the body **exactly** — see the module docstring.
    Anything else is a drifted reader producing plausible nonsense, and returning
    it would be worse than returning nothing.
    """
    r = _Reader(body, names)
    r.o = start
    rows: dict[str, Any] = {}
    try:
        while r.o < len(body) - 4:
            row_name = r.name()
            if row_name == "None":
                break
            rows[row_name] = _properties(r)
    except (TableError, struct.error, IndexError):
        return None

    # The export body carries a 4-byte trailing marker after the rows.
    if r.o not in (len(body), len(body) - 4):
        return None
    return rows if rows else None


def read_table(pak: Pak, asset_path: str) -> dict:
    """
    `{row name: {column: value}}` for one DataTable, or raise.

    The row section does not begin immediately after the object properties'
    terminator — there is a short table header whose length is not obviously
    fixed (8 bytes on `DT_PalShopCreateData`). Rather than hardcode it, every
    plausible offset in a small window is tried and the one that walks cleanly to
    the end is kept.
    """
    package = upackage.read(pak.read(asset_path))
    uexp = pak.read(asset_path.replace(".uasset", ".uexp"))
    if not package.exports:
        raise TableError(f"{asset_path}: no exports")

    body = package.exports[0].data(uexp)
    names = package.names

    # Object properties first — `RowStruct` and friends — so we know where the
    # table header starts.
    head = _Reader(body, names)
    try:
        _properties(head)
    except (TableError, struct.error, IndexError) as e:
        raise TableError(f"{asset_path}: object properties did not decode ({e})") from e

    for offset in range(head.o, min(head.o + 32, len(body)), 4):
        rows = _walk_rows(body, names, offset)
        if rows is not None:
            return rows

    raise TableError(
        f"{asset_path}: no row offset produced a walk ending at the buffer end. "
        f"This is a refusal, not an empty table — a partial decode of tagged "
        f"properties reads as real data and must never be returned."
    )


def data_tables(pak: Pak) -> list[str]:
    """Every `DT_*.uasset` in the pak, localisation copies excluded."""
    return sorted(
        p for p in pak.files
        if p.endswith(".uasset")
        and os.path.basename(p).startswith("DT_")
        and "/L10N/" not in p
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("table", nargs="?", help="table name or path fragment")
    ap.add_argument("--pak", default=None, help="defaults to the server pak")
    ap.add_argument("--list", action="store_true", help="list every DataTable")
    ap.add_argument("--out", help="write JSON (honours a .gz suffix)")
    ap.add_argument("--rows", type=int, default=5, help="rows to print (0 = all)")
    args = ap.parse_args()

    pak = Pak(args.pak) if args.pak else Pak()

    if args.list:
        tables = data_tables(pak)
        print(f"{len(tables)} DataTables")
        for t in tables:
            print("  " + os.path.basename(t)[:-7])
        return 0

    if not args.table:
        ap.error("give a table name, or --list")

    matches = [p for p in data_tables(pak) if args.table.lower() in p.lower()]
    if not matches:
        raise SystemExit(f"No DataTable matching {args.table!r}")
    if len(matches) > 1 and not any(
        os.path.basename(m)[:-7].lower() == args.table.lower() for m in matches
    ):
        print(f"{len(matches)} matches:", file=sys.stderr)
        for m in matches[:20]:
            print("  " + os.path.basename(m)[:-7], file=sys.stderr)
        return 1
    path = next(
        (m for m in matches if os.path.basename(m)[:-7].lower() == args.table.lower()),
        matches[0],
    )

    rows = read_table(pak, path)
    print(f"{os.path.basename(path)[:-7]}: {len(rows)} rows")

    shown = list(rows.items()) if args.rows == 0 else list(rows.items())[:args.rows]
    for name, fields in shown:
        print(f"\n  {name}")
        for column, value in fields.items():
            text = str(value)
            print(f"    {column:26s} = {text[:120]}")

    if args.out:
        from jsonout import write_json

        write_json(args.out, {"table": os.path.basename(path)[:-7], "rows": rows})
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
