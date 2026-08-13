#!/usr/bin/env python3
"""
Catalogue every string in the dashboard's own chrome, for translation.

#109's honest shape. The dashboard's buttons and headings are **ours** —
Pocketpair never wrote "Breeding planner" or "Show the other 92 settings" — so
they cannot come out of the game's localisation tables the way Pal and item
names do (#107). Measured: of 239 distinct chrome strings, **8 have a checkable
equivalent** among the game's 405 concept-keyed `common_*` rows, which is 10 of
304 occurrences. **3%.**

So the two honest options are to ship English, or to let people contribute
translations. This script exists for the second: it produces the list a
contributor works from, and it is GENERATED rather than hand-written because a
hand-written list silently stops covering the UI the first time somebody adds a
component.

## What it deliberately does NOT do

**It does not machine-translate anything.** That is the failure this project
refuses everywhere else: a sentence I wrote renders identically to one
Pocketpair published and gets trusted the same way. A string with no
contributed translation stays English, visibly.

**It does not touch game nouns.** Pal, item and structure names are #107's job
and come from the game's own `L10N/` tables in fifteen languages. A contributor
must never translate "Lamball" — the overlay already has Pocketpair's word for
it, and a hand translation would *overwrite a better source*. The report names
the 8 concept-matched strings for the same reason: take the game's.

## What counts as a chrome string

A JSX text node or a user-facing string literal in `src/`. The heuristics are
deliberately loose and over-collect: it is a work list for a human, so a false
positive costs someone three seconds and a false negative costs an untranslated
button nobody notices. Every entry carries its file and line so the caller can
throw one out by looking at it.

Usage:
    python3 scripts/extract-chrome-strings.py                 # write the catalogue
    python3 scripts/extract-chrome-strings.py --report        # summarise, write nothing
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
OUT = os.path.join(ROOT, "docs", "chrome-strings.json")

# The eight our chrome shares with the game BY CONCEPT — not by English value.
# Joining on the value is unsound and this project has the counterexample:
# "Clear" matched a quest string meaning "Completed!". These are keyed on what
# they MEAN in `DT_UI_Common_Text`, so a match is checkable.
GAME_PROVIDES = {
    "Cancel": "common_cancel",
    "Clear": "common_clear",
    "Level": "common_level",
    "Attack": "common_attack",
    "Defense": "common_defense",
    "Total": "common_total",
    "Partner skill": "common_partner_skill",
    "Work suitability": "common_work_suitability",
}

# TypeScript type names. `Promise<T>` and `Awaited<ReturnType<…>>` contain the
# literal `>Name<` sequence a JSX text node has, so a generic annotation reads
# as chrome. Six occurrences of "Promise" in the first run were this.
TS_TYPES = {
    "Promise", "Record", "Awaited", "ReturnType", "Partial", "Required",
    "Readonly", "Array", "Map", "Set", "Omit", "Pick", "Exclude", "Extract",
    "NonNullable", "Parameters", "React", "JSX", "Element", "Error",
}

# Text that is not prose: identifiers, classes, paths, format specifiers.
SKIP_EXACT = {"", "-", "—", "·", "/", "|", ":", "×", "…", "→", "%", "+", "(", ")"}
SKIP_PATTERNS = [
    re.compile(r"^[a-z]+(-[a-z0-9]+)+$"),        # css-class-name, kebab ids
    re.compile(r"^[a-z][a-zA-Z0-9]*$"),           # camelCase identifiers
    re.compile(r"^[A-Z][A-Z0-9_]+$"),             # CONSTANT_CASE
    re.compile(r"^[/.#]"),                        # paths, selectors
    re.compile(r"^https?://"),
    re.compile(r"^[\d\s.,:%+×/-]+$"),             # pure punctuation/number
    re.compile(r"^\W+$"),
    re.compile(r"^var\(--"),                      # css custom properties
    re.compile(r"^\d+px$|^\d+%$"),
]

# A JSX text node: >text< with no tags or braces inside.
JSX_TEXT = re.compile(r">\s*([A-Z][^<>{}\n]{2,120}?)\s*<")
# A prose-looking string literal: starts with a capital, has a space or is a
# short Title Case word. Attribute values (placeholder=, title=, label=) matter
# as much as body text and are caught by the same rule.
LITERAL = re.compile(r"""['"]([A-Z][^'"\\\n]{2,120})['"]""")


def load_game_nouns() -> set[str]:
    """
    Every display name the game itself ships, lowercased.

    **A contributor must never translate one of these.** "Dimensional Pal
    Storage" and "Fast travel" are Pocketpair's words, already available in
    fifteen languages through #107's overlay — a hand translation here would
    overwrite a better source with a worse one, and would disagree with the
    same noun rendered two panels over.

    Checked against the bundled data rather than guessed at, which is the only
    way this stays right as the catalogue grows.
    """
    sys.path.insert(0, os.path.join(ROOT, "backend"))
    try:
        import gamedata  # noqa: PLC0415
        data = gamedata.load()
    except Exception as exc:                      # noqa: BLE001
        # A missing bundle must not silently produce a catalogue that tells
        # contributors to translate every Pal name. Say so and stop.
        print(f"cannot read the game data ({exc}) -- refusing to build a "
              "catalogue that cannot tell a game noun from our own chrome",
              file=sys.stderr)
        raise SystemExit(2) from exc

    nouns: set[str] = set()
    for section in ("items", "pals", "structures", "technology", "activeSkills"):
        for row in (data.get(section) or {}).values():
            name = (row or {}).get("name")
            if name and len(name) >= 3:
                nouns.add(name.strip().lower())
    return nouns


GAME_NOUNS: set[str] = set()


def is_prose(text: str) -> bool:
    """Loose, and biased towards over-collecting — see the module docstring."""
    t = text.strip()
    if t in SKIP_EXACT or len(t) < 3:
        return False
    if t in TS_TYPES:
        return False
    if any(p.search(t) for p in SKIP_PATTERNS):
        return False
    # Must contain a letter, and must not look like a bare identifier.
    if not re.search(r"[A-Za-z]{2}", t):
        return False
    return True


def walk_sources():
    for base, dirs, files in os.walk(SRC):
        dirs[:] = [d for d in dirs if d not in {"node_modules", ".next"}]
        for name in sorted(files):
            if not name.endswith((".tsx", ".ts")):
                continue
            # Tests are not chrome. Their strings never reach a user.
            if name.endswith((".test.ts", ".test.tsx")):
                continue
            yield os.path.join(base, name)


def collect():
    """`{string: [(relpath, line), ...]}` — every occurrence, not a set."""
    found: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for path in walk_sources():
        rel = os.path.relpath(path, ROOT)
        with open(path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                # A line importing or typing something is not chrome.
                stripped = line.strip()
                if stripped.startswith(("import ", "export type", "from ")):
                    continue
                for match in JSX_TEXT.finditer(line):
                    text = match.group(1).strip()
                    if is_prose(text):
                        found[text].append((rel, lineno))
                for match in LITERAL.finditer(line):
                    text = match.group(1).strip()
                    # A literal needs a space to count as prose — single Title
                    # Case words are usually enum values, and the ones that are
                    # real chrome get caught as JSX text instead.
                    if " " in text and is_prose(text):
                        found[text].append((rel, lineno))
    return found


def build(found):
    strings = []
    for text, places in sorted(found.items(), key=lambda kv: -len(kv[1])):
        entry = {
            "text": text,
            "occurrences": len(places),
            "files": sorted({p for p, _ in places}),
        }
        if text in GAME_PROVIDES:
            # DO NOT TRANSLATE. The game already ships this word in all fifteen
            # languages, keyed by concept, and Pocketpair's is better than ours
            # will be — it is the word the player already reads in-game.
            entry["useGameString"] = GAME_PROVIDES[text]
        elif text.strip().lower() in GAME_NOUNS:
            # Same rule, reached from the data instead of a hand list: this is
            # a name the game itself ships, so #107's overlay already has it in
            # fifteen languages.
            entry["gameNoun"] = True
        strings.append(entry)
    return strings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true",
                    help="print the summary and write nothing")
    args = ap.parse_args()

    global GAME_NOUNS
    GAME_NOUNS = load_game_nouns()

    found = collect()
    strings = build(found)
    total_occurrences = sum(s["occurrences"] for s in strings)
    concept = [s for s in strings if "useGameString" in s]
    nouns = [s for s in strings if s.get("gameNoun")]
    ours = [s for s in strings
            if "useGameString" not in s and not s.get("gameNoun")]

    print(f"game display names loaded {len(GAME_NOUNS):>6}")
    print(f"distinct strings found    {len(strings):>6}")
    print(f"total occurrences         {total_occurrences:>6}")
    print(f"files scanned             {len(list(walk_sources())):>6}")
    print(f"  the game names these    {len(nouns):>6}  "
          "-- #107's overlay already has them in 15 languages")
    print(f"  concept-matched         {len(concept):>6}  "
          "-- take Pocketpair's common_* string")
    print(f"  OURS, need a translator {len(ours):>6}")

    print("\nmost-repeated of OURS (one string, many screens -- do these first):")
    for s in ours[:12]:
        print(f"  {s['occurrences']:>3}x  {s['text'][:66]}")

    if nouns:
        print("\nexcluded as game nouns (a sample -- do NOT hand-translate):")
        for s in nouns[:6]:
            print(f"  {s['occurrences']:>3}x  {s['text'][:66]}")

    if args.report:
        return 0

    payload = {
        "_comment": [
            "GENERATED by scripts/extract-chrome-strings.py -- do not hand-edit.",
            "The dashboard's OWN strings. Game nouns (Pal, item and structure",
            "names) are NOT here: they come from the game's own L10N tables in",
            "fifteen languages, see backend/data/lang/ and task #107.",
            "",
            "An entry with `useGameString` must NOT be translated. The game",
            "ships that word in every language, keyed by concept, and",
            "Pocketpair's wording is what the player already reads in-game.",
            "",
            "Nothing here is machine-translated, deliberately. An untranslated",
            "string stays visibly English rather than becoming a sentence this",
            "project wrote and attributed to nobody.",
        ],
        "distinct": len(strings),
        "occurrences": total_occurrences,
        "strings": strings,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    print(f"\nwrote {os.path.relpath(OUT, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
