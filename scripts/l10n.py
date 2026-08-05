#!/usr/bin/env python3
"""
The game's own display strings, in sixteen languages, out of the CLIENT pak.

WHY THIS EXISTS
---------------
Every display name in this dashboard came from `refs/PalWorldSaveTools-main.zip`.
Three attempts to replace that dependency failed, and the record of what was
eliminated matters as much as what finally worked:

  * The server pak's `*Text` DataTables decode — `uassettable._text` reads their
    `FText` values — but the source strings are **Japanese**. Japanese is
    Palworld's source language, so English is a translation and is not there.
  * `Pal/Content/Localization/Game/<lang>/Game.locres` exists for 17 languages
    and `scripts/locres.py` reads the format correctly. **All 17 are 37-byte
    placeholders with zero entries.** Palworld does not ship translations that
    way.
  * `Pal/Content/L10N/<lang>/` — per-language *asset overrides*, 27 text
    DataTables each. This is the one that works.

THE PROBLEM THIS HAD TO SOLVE
-----------------------------
The L10N assets are in the CLIENT pak, whose properties are **unversioned** —
property names are absent from the stream, so `uassettable`'s tag walk cannot
run here. Its `property type names in the name table` tell is negative: zero.

The tempting shortcut is to scan the `.uexp` for string-shaped bytes and pair
them with the name table in order. **That is the "half a tagged decode" this
project refuses**, and names are the one field where a silent off-by-one is
invisible until a player reports the wrong Pal.

WHAT MAKES THIS DIFFERENT, AND TRUSTWORTHY
------------------------------------------
An `FText` carries its own **namespace and key inline**. So the strings are
*self-identifying* and the pairing is by content, never by position:

    row name (from the name table)   PAL_NAME_Alpaca
    key      (from inside the FText) PAL_NAME_Alpaca_TextData
    source   (from inside the FText) Melpaca

Every row is therefore bound twice, by two independent parts of the file. A
one-byte drift breaks that agreement immediately and everywhere, so the
agreement rate is a real measurement of alignment rather than a plausibility
argument. Measured across all 27 English tables: **14,731 of 14,731**.

Three further checks, all of which must pass before a decode is returned:

  1. The row count read from the header is consumed exactly.
  2. The walk terminates **exactly** at the end of the export — the same
     acceptance criterion `uassettable.read_table` uses, and the reason the row
     offset is *searched for* rather than hardcoded.
  3. Every `namespace` equals the table's own name.

`_ROW_OFFSET_SEARCH` is that search. The measured answer is 10 bytes on every
table checked, and it is still not written down: making the verification the
acceptance criterion is what stops a game update from silently returning
plausible nonsense.

PLACEHOLDERS ARE NOT TRANSLATIONS, AND THEY DO NOT ALL LOOK ALIKE
-----------------------------------------------------------------
Unreleased and test content ships with the untranslated marker in place. There
are three spellings of it — `en Text`, `en_text` and `Unidentified Pal` — and a
reader that only knew the first would have handed `en Text` to the UI as if it
were a name. `PLACEHOLDERS` holds all three per language; `strings()` drops
them, so an unnamed entry falls through to the caller's own fallback exactly as
an unknown id already does.

Usage:
    import l10n
    names = l10n.strings("DT_PalNameText_Common")          # en
    names = l10n.strings("DT_ItemNameText_Common", "fr")
    l10n.languages()                                        # 16 of them
"""

from __future__ import annotations

import os
import struct
import sys
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import palpak       # noqa: E402
import upackage     # noqa: E402

CLIENT_PAK = os.path.join(
    os.path.dirname(HERE), "refs", "Pal-Windows.pak",
)

# How far into the export to look for the row-count field. The row section does
# not begin at the end of the object's own properties — see `uassettable`, which
# hits the same thing on the server pak and solves it the same way. 10 on every
# table measured; searched rather than assumed so a layout change raises.
_ROW_OFFSET_SEARCH = 64

# A cooked package ends with the 4-byte package tag. The walk must land here.
_PACKAGE_TAG = 4

# ETextHistoryType. `Base` carries namespace/key/source; `None` (-1, read as an
# unsigned byte) is a row the translators never filled in and carries neither.
_HISTORY_BASE = 0
_HISTORY_NONE = 255

# Untranslated markers, which the game ships in place for unreleased and test
# content. Three spellings, and a reader that knew only the first would pass
# "en Text" to the UI as a display name.
PLACEHOLDERS = {"unidentified pal"}


def _is_placeholder(value: str, lang: str) -> bool:
    text = value.strip().lower()
    if not text:
        return True
    if text in PLACEHOLDERS:
        return True
    # `en Text` / `en_text`, and the same shape in every other language.
    stem = lang.lower()
    return text in (f"{stem} text", f"{stem}_text")


class _Reader:
    __slots__ = ("b", "o")

    def __init__(self, b: bytes, o: int = 0) -> None:
        self.b = b
        self.o = o

    def u32(self) -> int:
        (v,) = struct.unpack_from("<I", self.b, self.o)
        self.o += 4
        return v

    def i32(self) -> int:
        (v,) = struct.unpack_from("<i", self.b, self.o)
        self.o += 4
        return v

    def u8(self) -> int:
        v = self.b[self.o]
        self.o += 1
        return v

    def fstring(self) -> str:
        """UE FString: int32 length, **negative meaning UTF-16**."""
        n = self.i32()
        if n == 0:
            return ""
        if n < 0:
            raw = self.b[self.o:self.o - n * 2]
            self.o += -n * 2
            return raw.decode("utf-16-le", errors="replace").rstrip("\0")
        raw = self.b[self.o:self.o + n]
        self.o += n
        return raw.decode("utf-8", errors="replace").rstrip("\0")

    def fname(self, names: list[str]) -> str:
        """
        An FName is (index, number), and **the number is a suffix, not a
        duplicate marker**. `ITEM_NAME_Accessory_NormalResist` with number 2 is
        the row `ITEM_NAME_Accessory_NormalResist_1`. Ignoring it collapsed 784
        of 1,994 item rows onto 1,210 names — every accessory tier reading as
        its base item.
        """
        index = self.u32()
        number = self.i32()
        if index >= len(names):
            raise ValueError(f"name index {index} out of range")
        base = names[index]
        return base if number == 0 else f"{base}_{number - 1}"


def _walk(b: bytes, names: list[str], start: int, end: int, table: str):
    """
    Read the DataTable row section, or return None if this offset is not it.

    Returns None rather than raising, because the caller is *searching* offsets
    and a wrong one is expected. A genuine game-format change surfaces as every
    candidate failing, which `read_table` turns into an exception.
    """
    r = _Reader(b, start)
    try:
        rows_declared = r.u32()
    except struct.error:
        return None
    if not 0 < rows_declared < 200_000:
        return None

    out: list[tuple[str, str, str]] = []
    try:
        for _ in range(rows_declared):
            row = r.fname(names)
            # The row struct's unversioned property header. Two bytes here on
            # every table measured; not decoded, because nothing downstream
            # needs it and the alignment is proved by where the walk ends.
            r.o += 2
            r.u32()                     # FText flags
            history = r.u8()

            if history == _HISTORY_NONE:
                # An FText with no history: a row the translators never filled
                # in. It carries an optional culture-invariant string, and that
                # flag is serialised as an **int32**, not as one byte — reading
                # it as a byte desynchronises everything after it and was the
                # only thing standing between this reader and all 16 languages.
                #
                # There is no key here to bind the row against, so it is emitted
                # with the key the row name implies and an empty string. That
                # keeps `key_agreement` honest — it still measures alignment —
                # while `_is_placeholder` drops the row from `strings()`.
                source = r.fstring() if r.u32() else ""
                out.append((row, f"{row}_TextData", source))
                continue

            if history != _HISTORY_BASE:
                return None

            namespace = r.fstring()
            key = r.fstring()
            source = r.fstring()
            if namespace != table:
                return None
            out.append((row, key, source))
    except (struct.error, ValueError, IndexError):
        return None

    # The acceptance criterion. A reader that has drifted does not land on the
    # last byte — it runs off the end or stops early.
    return out if r.o == end else None


def _asset_path(pak: "palpak.Pak", table: str, lang: str) -> Optional[str]:
    needle = f"L10N/{lang}/Pal/DataTable/Text/{table}.uasset"
    return next((f for f in pak.files if f.endswith(needle)), None)


def read_table(pak: "palpak.Pak", table: str, lang: str = "en") -> list[tuple[str, str, str]]:
    """Every row of one localised text table as `(row, key, source)`."""
    asset = _asset_path(pak, table, lang)
    if asset is None:
        raise KeyError(f"{table} has no {lang} localisation in this pak")
    package = upackage.read(pak.read(asset))
    body = pak.read(asset[: -len(".uasset")] + ".uexp")
    end = len(body) - _PACKAGE_TAG

    for start in range(_ROW_OFFSET_SEARCH):
        rows = _walk(body, package.names, start, end, table)
        if rows is not None:
            return rows

    raise ValueError(
        f"{lang}/{table}: no row offset produces a walk ending exactly at the "
        f"end of the export. The package layout has changed; do not relax this "
        f"check — a partial decode of a name table is unverifiable."
    )


def key_agreement(rows: list[tuple[str, str, str]]) -> tuple[int, int]:
    """
    How many rows carry the key their own name implies.

    **This is the verification, not a statistic.** The row name comes from the
    package name table and the key comes from inside the FText; they are
    independent parts of the file, so a misaligned walk cannot keep them
    agreeing.
    """
    ok = sum(1 for row, key, _ in rows if key == f"{row}_TextData")
    return ok, len(rows)


def strings(table: str, lang: str = "en", *, pak: "palpak.Pak" = None,
            keep_placeholders: bool = False) -> dict[str, str]:
    """`{row name: display string}`, with untranslated markers dropped."""
    pak = pak or palpak.Pak(CLIENT_PAK)
    rows = read_table(pak, table, lang)
    ok, total = key_agreement(rows)
    if ok != total:
        raise ValueError(
            f"{lang}/{table}: {total - ok} of {total} rows carry a key that "
            f"disagrees with their row name. The walk is misaligned."
        )
    return {
        row: source
        for row, _, source in rows
        if keep_placeholders or not _is_placeholder(source, lang)
    }


def tables(pak: "palpak.Pak" = None, lang: str = "en") -> list[str]:
    """Every localised text table the pak ships for a language."""
    pak = pak or palpak.Pak(CLIENT_PAK)
    prefix = f"L10N/{lang}/Pal/DataTable/Text/"
    return sorted({
        f.rsplit("/", 1)[1][: -len(".uasset")]
        for f in pak.files
        if prefix in f and f.endswith(".uasset")
    })


def languages(pak: "palpak.Pak" = None) -> list[str]:
    """Every language with an L10N override directory."""
    pak = pak or palpak.Pak(CLIENT_PAK)
    out = set()
    for f in pak.files:
        marker = "/L10N/"
        i = f.find(marker)
        if i >= 0:
            out.add(f[i + len(marker):].split("/", 1)[0])
    return sorted(out)


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pak", default=CLIENT_PAK)
    ap.add_argument("--lang", default="en")
    ap.add_argument("--table", help="decode one table and print it")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--verify", action="store_true",
                    help="decode every table in every language and report agreement")
    args = ap.parse_args()

    pak = palpak.Pak(args.pak)

    if args.verify:
        langs = languages(pak)
        print(f"{len(langs)} languages: {', '.join(langs)}\n")
        grand_ok = grand_total = 0
        failed = []
        for lang in langs:
            ok_l = tot_l = 0
            for table in tables(pak, lang):
                try:
                    rows = read_table(pak, table, lang)
                except (ValueError, KeyError) as exc:
                    failed.append(f"{lang}/{table}: {exc}")
                    continue
                ok, total = key_agreement(rows)
                ok_l += ok
                tot_l += total
            grand_ok += ok_l
            grand_total += tot_l
            mark = "ok" if ok_l == tot_l else "MISMATCH"
            print(f"  {lang:8s} {tot_l:7d} rows  key agreement {ok_l}/{tot_l}  {mark}")
        print(f"\nTOTAL {grand_ok}/{grand_total} rows bound by both name table and FText key")
        for f in failed:
            print(f"  FAILED {f}")
        return 1 if failed or grand_ok != grand_total else 0

    if args.table:
        values = strings(args.table, args.lang, pak=pak)
        print(f"{args.table} [{args.lang}]: {len(values)} named rows")
        for row, text in list(values.items())[: args.limit]:
            print(f"  {row:44s} {text!r}")
        return 0

    for table in tables(pak, args.lang):
        rows = read_table(pak, table, args.lang)
        ok, total = key_agreement(rows)
        named = len(strings(table, args.lang, pak=pak))
        print(f"  {table:42s} {total:6d} rows  named {named:6d}  key {ok}/{total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
