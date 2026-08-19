#!/usr/bin/env python3
"""
Build the chrome language packs — the labelled-beta half of #109.

Inputs, in priority order (highest wins per string):

  1. **The game's own words**, for the handful of chrome strings the game has
     a concept-keyed row for (`CONCEPTS` below — key name and English value
     both agree, the two-source rule). Read per language from the SERVER pak's
     `L10N/<lang>/.../DT_UI_Common_Text_Common`, so "Paldeck" renders as
     whatever Pocketpair calls the Palpedia in that language — and in English
     too, which is how the tab picks up the game's own 1.0 word.
  2. **Machine translations** from `scripts/chrome-mt/<code>.json` — reviewed
     prose written for this project, shipped as `provenance: "machine"` until
     a human verifies a language (`docs/TRANSLATING.md`).

Excluded from every machine pack: the safety-critical strings listed in
`docs/chrome-wrapped.json` — the save-editing preconditions and backup and
restore confirmations. A mistranslated "The server must be stopped first" can
cost someone a world, so those stay visibly English until a human who knows
the language promotes them.

Output: `src/lib/chrome-langs/<code>.json`, bundled by the frontend as
code-split chunks (`src/lib/chrome.ts`). The `en` pack carries ONLY the
game-provided strings — English chrome is already English; what it gains is
consistency with the game's own vocabulary.

Works without `refs/` too: the concept overlay is skipped with a warning and
packs build from the MT dictionaries alone.
"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MT_DIR = os.path.join(ROOT, "scripts", "chrome-mt")
OUT_DIR = os.path.join(ROOT, "src", "lib", "chrome-langs")
WRAPPED = os.path.join(ROOT, "docs", "chrome-wrapped.json")

#: The tab labels live in page.tsx's TABS array and are wrapped at the render
#: site (`t(tab.label)`), so the source scan cannot see them. Keep in step
#: with page.tsx — a label missing here simply stays English.
TAB_LABELS = [
    "Overview", "Map", "Bases", "Items", "Players", "Breeding", "Paldeck",
    "Progression", "My Pals", "Settings", "Access", "Backups", "Users",
    "Audit log", "My account", "Save Tools",
]

#: our chrome string -> row in DT_UI_Common_Text_Common. Every entry was
#: verified two ways: the row's English VALUE matches (or names the same
#: concept as) our string, and the KEY reads as that concept — the rule that
#: kept "Clear" from matching a quest string meaning "Completed!".
#: INGAME_MAIN_MENU_PALDEX is the interesting one: the game's 1.0 word is
#: "Palpedia", so the Paldeck tab picks up the game's own current name in
#: every language including English.
CONCEPTS = {
    "Paldeck": "INGAME_MAIN_MENU_PALDEX",
    "Items": "TECHNOLOGY_CATEGOFY_ITEM",       # the typo is Pocketpair's
    "Party": "INGAME_MAIN_MENU_PAL",
    "Fast travel": "INTERACT_INDICATOR_FastTravel",
    # Extended 2026-08-18 ("use game words when available"). Each passed the
    # same two-source check: the KEY names the concept and the English VALUE
    # matches our string. Not taken, and why: COMMON_LEVEL is "LEVEL" (a
    # styling artifact, not a word); COMMON_TOTAL_MONEY is "Gold"; "Bases"
    # only exists as the singular MAP_FILTER_CAMP "Base"; and the "Detail"
    # value-match is INTERACT_INDICATOR_NotImpl_OpenDetailMenu — the literal
    # "(not implemented)" trap AGENTS.md records for value joins.
    "Defense": "COMMON_STATUS_DEFENCE",
    "Sick": "COMMON_CONDITION_NAME_Cold",          # the game's word IS "Sick"
    "Hungry": "COMMON_CONDITION_NAME_Hunger",
    "Starving": "COMMON_CONDITION_NAME_Starvation",
    "Oil rig": "MAP_FILTER_OILRIG",
    "Field bosses": "MAP_FILTER_BOSS",             # the game's map filter name
    "Guild markers": "MAP_MARKER_HEAD_GUILD",
    "Production": "COMMON_WORKSPACE_Product",
}

#: Language codes as the game's L10N folders spell them; the dashboard's
#: picker uses the same codes.
LANGS = ["de", "es", "es-MX", "fr", "id", "it", "ko", "pl", "pt-BR",
         "ru", "th", "tr", "vi", "zh-Hans", "zh-Hant"]


def game_concepts() -> dict[str, dict[str, str]]:
    """{lang: {our string: game's string}} — empty when refs/ is absent."""
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    try:
        from palpak import Pak
        import uassettable
        pak = Pak()
    except Exception as exc:                    # noqa: BLE001
        print(f"  (no server pak — concept overlay skipped: {exc})")
        return {}

    out: dict[str, dict[str, str]] = {}
    for lang in ["en", *LANGS]:
        path = (f"../../../Pal/Content/L10N/{lang}/Pal/DataTable/Text/"
                "DT_UI_Common_Text_Common.uasset")
        try:
            rows = uassettable.read_table(pak, path)
        except Exception as exc:                # noqa: BLE001
            print(f"  !! {lang}: could not read UI text table ({exc})")
            continue
        found: dict[str, str] = {}
        for ours, key in CONCEPTS.items():
            cell = rows.get(key) or {}
            src = ((cell.get("TextData") or {}).get("source") or "").strip()
            # A row nobody translated ships a literal marker, and the game
            # spells it at least three ways (`gametext.py` knows "en Text",
            # "en_text", "Unidentified Pal"; INGAME_MAIN_MENU_QUEST adds
            # "en-hant text"). Shipping one as a button label would be the
            # placeholder-as-name failure — skip, so MT or English fills in.
            if src and src.lower() not in (
                "en text", "en_text", "en-hant text", "unidentified pal",
            ):
                found[ours] = src
        out[lang] = found
    return out


def main() -> int:
    wrapped = json.load(open(WRAPPED))
    safety = set(wrapped["safetyCritical"])
    translatable = (set(wrapped["wrapped"]) | set(TAB_LABELS)) - safety

    concepts = game_concepts()
    os.makedirs(OUT_DIR, exist_ok=True)

    # English: the game's own vocabulary only.
    en_strings = dict(sorted((concepts.get("en") or {}).items()))
    en_pack = {
        "language": "en",
        "provenance": "game",
        "verified": True,
        "strings": en_strings,
    }
    with open(os.path.join(OUT_DIR, "en.json"), "w", encoding="utf-8") as f:
        json.dump(en_pack, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"  en: {len(en_strings)} game-provided strings")

    for lang in LANGS:
        mt_path = os.path.join(MT_DIR, f"{lang}.json")
        if not os.path.exists(mt_path):
            print(f"  -- {lang}: no MT dictionary, skipped")
            continue
        mt = json.load(open(mt_path, encoding="utf-8"))

        strings: dict[str, str] = {}
        untranslated = []
        for s in sorted(translatable):
            if s in mt and mt[s] and mt[s] != s:
                strings[s] = mt[s]
            else:
                untranslated.append(s)
        # The game's words win over the machine's, per string.
        game = concepts.get(lang) or {}
        strings.update(game)

        pack = {
            "language": lang,
            "provenance": "machine",
            "verified": False,
            "gameProvided": sorted(game),
            "strings": dict(sorted(strings.items())),
        }
        with open(os.path.join(OUT_DIR, f"{lang}.json"), "w",
                  encoding="utf-8") as f:
            json.dump(pack, f, ensure_ascii=False, indent=1)
            f.write("\n")
        note = f", {len(untranslated)} untranslated" if untranslated else ""
        print(f"  {lang}: {len(strings)} strings "
              f"({len(game)} from the game{note})")
    print(f"\nsafety-critical (English until a human verifies): {len(safety)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
