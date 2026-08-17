#!/usr/bin/env python3
"""
Wrap the dashboard's chrome strings in `t()` — the call-site half of #109.

Rewrites exactly two syntactic positions, chosen because they are display-only
and cannot be confused with logic:

    JSX text nodes          >Build planner<     -> >{t('Build planner')}<
    display attributes      placeholder="..."   -> placeholder={t('...')}
                            (placeholder, title, aria-label, label, alt)

String literals anywhere else — comparisons, map keys, ids — are untouched, so
this can never turn `typeA === 'Consumable'` into a translated comparison.
Strings living in object literals ({ label: 'Ride speed' }) are also left
alone in this pass; they stay English until wrapped by hand, which is honest
rather than broken (t() is identity for anything not in the pack anyway).

Only strings present in `docs/chrome-strings.json` are wrapped, minus:
  * `useGameString` / `gameNoun` entries — the game ships those words,
  * strings containing HTML entities (&apos; etc.) — the entity would travel
    into the lookup key and never match a pack.

Writes `docs/chrome-wrapped.json`: the list of strings actually wrapped, which
is exactly the set the language packs need to cover. Safety-critical strings
ARE wrapped (t() is identity until a pack carries them) but are listed
separately so `build-chrome-packs.py` excludes them from machine packs — a
mistranslated "The server must be stopped first" can cost someone a world, so
those wait for a human.

Usage:
    python3 scripts/wrap-chrome-strings.py --dry   # report, change nothing
    python3 scripts/wrap-chrome-strings.py         # rewrite src/ in place
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
CATALOGUE = os.path.join(ROOT, "docs", "chrome-strings.json")
OUT = os.path.join(ROOT, "docs", "chrome-wrapped.json")

DISPLAY_ATTRS = ("placeholder", "title", "aria-label", "label", "alt")

#: Strings a wrong translation could turn into a lost world: preconditions and
#: confirmations on the write paths. Matched as substrings, case-insensitive,
#: against the ENGLISH string. These are wrapped like everything else but kept
#: OUT of machine packs; a human contributor promotes them per language.
SAFETY_MARKERS = (
    "server must be stopped",
    "server is stopped",
    "server is running",
    "backup", "restore", "rollback", "roll back",
    "overwrite", "overwritten", "delete", "irreversib",
    "cannot be undone", "corrupt", "wipe",
)


def is_safety(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in SAFETY_MARKERS)


def esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("'", "\\'")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    cat = json.load(open(CATALOGUE))
    translatable: set[str] = set()
    for entry in cat["strings"]:
        if entry.get("useGameString") or entry.get("gameNoun"):
            continue
        text = entry["text"]
        if "&" in text and ";" in text:      # HTML entity — see module docstring
            continue
        translatable.add(text)

    # Longest first, so 'Pal and item names' wins over a hypothetical 'Pal'.
    ordered = sorted(translatable, key=len, reverse=True)

    wrapped: set[str] = set()
    files_touched = 0
    total_edits = 0

    for dirpath, _dirs, files in os.walk(SRC):
        for fname in files:
            if not fname.endswith(".tsx"):
                continue
            path = os.path.join(dirpath, fname)
            original = open(path, encoding="utf-8").read()
            text = original
            edits = 0

            def in_string(pos: int, text: str) -> bool:
                """Inside a string literal? Two checks, both learned from a
                broken build: backtick parity file-wide (Leaflet popups build
                raw HTML in template literals, where `>Text<` is not JSX),
                and quote parity on the match's own line (the same popups
                also build HTML in plain single-quoted strings)."""
                if text.count("`", 0, pos) % 2 == 1:
                    return True
                line_start = text.rfind("\n", 0, pos) + 1
                line = text[line_start:pos]
                for q in ("'", '"'):
                    unescaped = re.sub(r"\\\\.", "", line).count(q)
                    if unescaped % 2 == 1:
                        return True
                return False

            for s in ordered:
                pat_esc = re.escape(s)

                # JSX text node. Whitespace around the string is re-emitted
                # verbatim: `<Icon /> Build planner<` keeps its separating
                # space, which JSX preserves within a line and which the icon
                # visually depends on.
                def jsx_repl(m: re.Match, s=s) -> str:
                    if in_string(m.start(), m.string):
                        return m.group(0)
                    return f">{m.group(1)}{{t('{esc(s)}')}}{m.group(2)}<"

                new_text, n = re.subn(
                    rf">(\s*){pat_esc}(\s*)<", jsx_repl, text)
                if n and new_text != text:
                    text = new_text
                    edits += n
                    wrapped.add(s)

                # Display attributes, double- or single-quoted.
                for attr in DISPLAY_ATTRS:
                    new_text, n = re.subn(
                        rf'\b{attr}=(["\']){pat_esc}\1',
                        f"{attr}={{t('{esc(s)}')}}",
                        text)
                    if n:
                        text = new_text
                        edits += n
                        wrapped.add(s)

            if edits and text != original:
                # Never wrap inside a line comment — the regexes cannot hit
                # /* */ (braces excluded) but a // line quoting JSX could
                # match. Cheap post-check: revert any line whose wrap landed
                # after a //.
                fixed_lines = []
                orig_lines = original.split("\n")
                new_lines = text.split("\n")
                if len(orig_lines) == len(new_lines):
                    for old_line, new_line in zip(orig_lines, new_lines):
                        code = old_line.split("//")[0]
                        if "//" in old_line and "t('" in new_line \
                                and "t('" not in code:
                            fixed_lines.append(old_line)   # comment-only hit
                        else:
                            fixed_lines.append(new_line)
                    text = "\n".join(fixed_lines)

                if not args.dry:
                    if "from '@/lib/chrome'" not in text:
                        # After the last line of the import BLOCK — which for a
                        # multi-line `import type {` is its closing `} from`
                        # line, not its opening one. Inserting after the
                        # opening line put the new import inside another
                        # statement, which is a syntax error every time.
                        lines = text.split("\n")
                        last_import = max(
                            i for i, ln in enumerate(lines[:80])
                            if re.search(r"from ['\"]", ln))
                        lines.insert(last_import + 1,
                                     "import { t } from '@/lib/chrome';")
                        text = "\n".join(lines)
                    open(path, "w", encoding="utf-8").write(text)
                files_touched += 1
                total_edits += edits
                rel = os.path.relpath(path, ROOT)
                print(f"  {rel}: {edits} wraps")

    # The wrapped set comes from a SCAN of the sources, not from this run's
    # edits — a re-run that changes one file must not shrink the manifest to
    # one string, which is exactly what the first version did.
    call = re.compile(r"\bt\('((?:[^'\\]|\\.)*)'\)")
    wrapped = set()
    for dirpath, _dirs, files in os.walk(SRC):
        for fname in files:
            if fname.endswith(".tsx"):
                body = open(os.path.join(dirpath, fname), encoding="utf-8").read()
                for m in call.finditer(body):
                    wrapped.add(m.group(1).replace("\\'", "'").replace("\\\\", "\\"))

    safety = sorted(s for s in wrapped if is_safety(s))
    payload = {
        "_comment": [
            "Strings wrapped in t() by scripts/wrap-chrome-strings.py — the",
            "exact set a chrome language pack should cover. Regenerated by",
            "re-running the script; do not hand-edit.",
            "safetyCritical entries are wrapped but EXCLUDED from machine",
            "packs; a human contributor promotes them per language.",
        ],
        "wrapped": sorted(wrapped),
        "safetyCritical": safety,
    }
    if not args.dry:
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
            f.write("\n")
    print(f"\n{files_touched} files, {total_edits} wraps, "
          f"{len(wrapped)} distinct strings ({len(safety)} safety-critical)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
