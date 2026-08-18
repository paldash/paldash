#!/usr/bin/env python3
"""
Bundle the game's own display strings for ONE language.

`l10n.py` and `gametext.py` decoded all sixteen languages months ago —
235,696 rows, zero refusals — and only English was ever bundled. This is the
part that turns that into files a server can serve.

## The strategy is measured, not argued

Per language, gzipped:

    names + descriptions   ~215 KB   (203-251 across the fifteen)
    names only             ~120 KB

**One file per language, loaded on demand.** The alternatives both lose:

- *All sixteen in `gamedata.json.gz`* adds **3.2 MB** to an image that is 274 KB
  of game data today, and 15/16ths of it is never read by any given operator.
- *Fetching at runtime* is out on this project's oldest rule: the container must
  work offline on a LAN, so anything adopted is fetched once and bundled.

215 KB is smaller than `gamedata.json.gz` itself, so a language is about as
expensive as the English data already is — which is the right price for
something a player actively chose.

## The trap that is NOT about file size

**Localising `name` in place breaks search**, and silently. The Pal and item
search boxes are a client-side substring match against `name`, so a German
bundle turns "Lamball" into "Wollipop" and an English query stops matching a Pal
that is right there on screen. `nameEn` must travel beside the localised name
and the search must consider both — the ids are canonical and unaffected, which
is the half that makes this look safe and is not the half that breaks.

Usage:
    python3 scripts/extract-language.py --lang de
    python3 scripts/extract-language.py --all
    python3 scripts/extract-language.py --measure     # sizes, writes nothing
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import gametext          # noqa: E402
import l10n              # noqa: E402
import palpak            # noqa: E402
from jsonout import write_json  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(HERE), "backend", "data", "lang")

# English is already inside `gamedata.json.gz`; a second copy would be a second
# source of truth for the names every other bundle is keyed against.
SKIP = {"en"}


def build(lang: str, pak) -> dict:
    catalogue = gametext.Catalogue(lang, pak=pak)
    return {
        "lang": lang,
        "names": catalogue._names,
        "descriptions": catalogue._descs,
        # Stated in the payload because a client that localises a name must
        # keep matching an English query — see the module docstring.
        "searchNeedsEnglishToo": True,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--measure", action="store_true",
                    help="report sizes and write nothing")
    args = ap.parse_args()

    try:
        pak = l10n._default_pak()
    except Exception as e:  # noqa: BLE001
        print(f"Needs the client pak: {e}", file=sys.stderr)
        return 2

    available = [l for l in l10n.languages(pak) if l not in SKIP]
    if args.all or args.measure:
        langs = available
    elif args.lang:
        langs = [args.lang]
    else:
        print("languages: " + ", ".join(available))
        return 0

    total = 0
    for lang in langs:
        data = build(lang, pak)
        blob = gzip.compress(
            json.dumps(data, ensure_ascii=False, sort_keys=True).encode(), 9)
        total += len(blob)
        rows = sum(len(v) for v in data["names"].values())
        descs = sum(len(v) for v in data["descriptions"].values())

        if args.measure:
            print(f"  {lang:8s} {rows:6d} names  {descs:5d} descriptions  "
                  f"{len(blob)/1024:6.0f} KB")
            continue

        os.makedirs(OUT_DIR, exist_ok=True)
        path = os.path.join(OUT_DIR, f"{lang}.json.gz")
        write_json(path, data)
        print(f"wrote {path}  ({rows} names, {descs} descriptions, "
              f"{os.path.getsize(path)/1024:.0f} KB)")

    if args.measure:
        print(f"\n  {len(langs)} languages, {total/1024/1024:.1f} MB total — "
              "which is why these are per-language files rather than one blob")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
