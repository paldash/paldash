#!/usr/bin/env python3
"""
Per-key help for PalWorldSettings.ini, from Pocketpair rather than from us.

THE PROBLEM
-----------
The Settings tab shows 119 keys named things like `PalStomachDecreaceRate` — the
game's own misspelling — and gives an operator no way to tell the ones that
matter from the ones that do not. `HIGHLIGHT_GROUPS` curates a subset to the top;
it explains nothing.

Hand-writing 119 explanations was the obvious move and would have been wrong: it
is exactly the "do not hand-write game data that already exists" rule this
project holds, and a confident sentence about a mechanic nobody verified is worse
than a blank tooltip.

TWO SOURCES, NEITHER OF THEM OURS
---------------------------------
**Descriptions come from Pocketpair's own documentation** —
`docs.palworldgame.com/settings-and-operation/configuration`, which documents
**93 of the 119 keys**. The whole category was checked, not just that page:
`arguments` and `commands` add rows about the command line and RCON, `pvp` and
`mod` add no settings tables, and `technologyids` is the `DenyTechnologyList`
vocabulary rather than settings help.

**Labels come from the game itself** — `DT_UI_Common_Text`'s `WORLDSSETTING_*`
rows (note Pocketpair's own double S), which are the strings on the in-game world
settings screen. 31 of them are named for their INI key exactly. That screen also
names the **values** of the enum settings, which is worth more than it sounds:
`DeathPenalty=EquipmentAndItemAndRandomPal` is opaque, and the game calls it
"Drop all items and one random Pal on team".

**THE FETCH IS A BUILD STEP, NEVER A RUNTIME ONE.** The container must work
offline on a LAN, so the page is fetched once here and the result is bundled —
same rule as `refs/`. `--html` takes an already-saved copy so a regeneration can
be diffed rather than trusted.

THE ALIAS MAP IS HAND-WRITTEN AND EACH ENTRY IS CHECKED, NOT FUZZY-MATCHED
--------------------------------------------------------------------------
Fifteen of the game's UI rows are plainly about an INI key under a different
name — `WORLDSSETTING_HatchingEggTime` for `PalEggDefaultHatchingTime`. Matching
those by string similarity is precisely the failure this repository keeps
recording: a plausible mapping that attaches the wrong text to a setting and is
invisible until somebody acts on it.

So they are listed by hand, and **the acceptance test is agreement between two
unrelated sources**: the pak string and Pocketpair's doc row must be recognisably
about the same thing. `HatchingEggTime` reads *"Time (h) to incubate Massive Egg.
Note: Other eggs also require time to incubate"* and the doc row reads *"Time to
hatch a Huge Egg (hours). Note: Other eggs also require time to incubate."* —
the same sentence, arrived at from a pak and from a website. `--show-aliases`
prints every pair so the next person can re-check them rather than trust this
docstring.

WHAT THIS SCRIPT WILL NOT DO
----------------------------
Invent an explanation. A key with no official description and no game label gets
**nothing**, and the UI renders no tooltip. There are 26 of those and they are
listed in the output, because an operator seeing a blank is better served than
one reading a guess — the same line `basesupply.py` holds about mechanics.

**And the PvP page is a recorded negative.** Six of those 26 are PvP keys and
that page names them — inside prose recipes ("set these three to True and players
can harm each other"), never as a description of any one of them. Turning that
into per-key help means deciding which clause belongs to which key, which is the
guess this script exists to avoid. What the page *does* carry is the game's own
recommended PvP configuration as a set of key/value pairs, which belongs in
`settings_ini.PRESETS` rather than here.
"""

from __future__ import annotations

import argparse
import gzip
import html
import json
import os
import re
import sys
from datetime import date
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

OUT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "backend", "data",
    "settings_help.json.gz",
)

DOC_PAGES = {
    "configuration": "https://docs.palworldgame.com/settings-and-operation/configuration/",
    "arguments": "https://docs.palworldgame.com/settings-and-operation/arguments/",
    "commands": "https://docs.palworldgame.com/settings-and-operation/commands/",
}

#: A UI row name that is not its INI key, and the key it belongs to.
#:
#: **Hand-written, never inferred.** Each is confirmed by the pak string and the
#: official doc row describing the same thing — run with `--show-aliases` to see
#: both side by side. An entry whose two sources disagree should be deleted, not
#: reworded: half a mapping is a wrong tooltip on a real setting.
UI_ALIASES = {
    "HatchingEggTime": "PalEggDefaultHatchingTime",
    "PalCaptureRateAdd": "PalCaptureRate",
    "BuildObjectDeteriorationRate": "BuildObjectDeteriorationDamageRate",
    "MaxDropItemNum_InWorld": "DropItemMaxNum",
    "MaxPhysicsDropItemNum_InWorld": "PhysicsActiveDropItemMaxNum",
    "MaxDropNum_Poop": "DropItemMaxNum_UNKO",
    "EnablePoop": "bActiveUNKO",
    "EnableRaid": "bEnableInvaderEnemy",
    "FastTravel": "bEnableFastTravel",
    "FastTravelOnlyBaseCamp": "bEnableFastTravelOnlyBaseCamp",
    "Hardcore_Mode": "bHardcore",
    "PalLost": "bPalLost",
    "SUPPLY_DROP_SPAN": "SupplyDropSpan",
    "MAX_BUILDING_LIMIT_NUM": "MaxBuildingLimitNum",
    "GLOBAL_PALSTORAGE_EXPORT": "bAllowGlobalPalboxExport",
    "GLOBAL_PALSTORAGE_IMPORT": "bAllowGlobalPalboxImport",
    "RANDOMIZER_MODE": "RandomizerType",
    "RANDOMIZER_SEED": "RandomizerSeed",
    "AUTOSAVE_INTERVAL": "AutoSaveSpan",
    # DELIBERATELY ABSENT: WorldName -> ServerName. It is the same INI field,
    # but the game calls it "World Name" on the single-player creation screen
    # and this is a dedicated-server dashboard. A correct label that reads
    # wrong in context is worse than no label, which is the one case where
    # the game's own words are not automatically the right ones.
}

#: Enum settings whose *values* the game names, and the row prefix that holds
#: them. The value labels matter more than the key label: nothing about
#: `EquipmentAndItemAndRandomPal` tells an operator what it does.
VALUE_PREFIXES = {
    "DeathPenalty": "DeathPenalty_",
    "RandomizerType": "RANDOMIZER_MODE_",
}

#: The UI row suffix is not always the value the INI stores.
#:
#: `RANDOMIZER_MODE_NO` is the setting `RandomizerType=None`. Uppercasing or
#: title-casing the suffix gets two of the three and silently invents `No` for
#: the one that matters — so the three are written out, and each is confirmed by
#: the official description, which spells the vocabulary out in full:
#: "None = no randomization; Region = randomize per region; All = fully
#: randomized."
VALUE_ALIASES = {
    "RandomizerType": {"NO": "None", "REGION": "Region", "ALL": "All"},
}

#: Longest a game string may be and still be used as a *label*.
#:
#: The aliased rows are not all labels. `WORLDSSETTING_HatchingEggTime` is a
#: whole explanatory sentence, which is why it agreed so well with Pocketpair's
#: description — and rendering it as the field's name gives a form control
#: captioned with a paragraph. Where the game's string is long the description
#: already covers the ground, so the label is dropped rather than truncated.
MAX_LABEL_CHARS = 60

#: **This project's own words, and the only ones in the bundle.** Each records
#: something measured here rather than an explanation of a game mechanic — see
#: AGENTS.md for where every one of them came from. They are additive: a note
#: never replaces Pocketpair's description, it sits under it.
DASHBOARD_NOTES = {
    "PalEggDefaultHatchingTime": (
        "The key is PalEggDefaultHatchingTime, not EggDefaultHatchingTime. "
        "Guides and older tools use the second spelling and it matches nothing "
        "— a value written under it is silently ignored."
    ),
    "BaseCampWorkerMaxNum": (
        "This file is the only authority on the worker cap. The dashboard's "
        "per-level table is the game's default progression, not a bound, and a "
        "server running above or below it is configured, not broken."
    ),
    "BaseCampMaxNumInGuild": (
        "Same as the worker cap: the number here wins over any table. Nothing "
        "bundled with the dashboard limits it."
    ),
    "GuildPlayerMaxNum": (
        "Every difficulty preset the game ships sets this to 20, which makes 20 "
        "look like a rule of the game. It is a default, and this file overrides "
        "it."
    ),
    "AutoSaveSpan": (
        "Seconds. The dashboard never writes a save file while the server is "
        "running, so this does not affect editing safety — it decides how much "
        "play a crash can cost."
    ),
    "bIsUseBackupSaveData": (
        "The server's own rotating snapshots, written inside the world folder. "
        "The dashboard's backups are separate and are not affected by this."
    ),
}


def _cells(row: str) -> list[str]:
    out = []
    for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S):
        # A line break is a sentence boundary, and stripping tags without
        # substituting anything for it produced "(max 50).Increasing this value"
        # and "Death PenaltyNone : No drops" — text that reads as a typo in
        # Pocketpair's documentation when it is one in our parser.
        cell = re.sub(r"<br\s*/?>|</p>|</li>", " ", cell, flags=re.I)
        out.append(re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", cell))).strip())
    return out


def parse_doc_tables(raw: str) -> dict[str, str]:
    """
    `{key: description}` from a docs.palworldgame.com page.

    Parsed from the table markup rather than read out of a summary. That
    distinction is the point: a language model asked for "verbatim" text returns
    something that reads verbatim, and attributing a paraphrase to Pocketpair is
    worse than writing our own sentence and saying so.
    """
    out: dict[str, str] = {}
    for table in re.findall(r"<table.*?</table>", raw, re.S):
        header_seen = False
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S):
            cells = _cells(tr)
            if not header_seen:
                header_seen = True
                continue
            if len(cells) >= 2 and cells[0] and cells[1]:
                out.setdefault(cells[0], cells[1])
    return out


def fetch(url: str) -> str:
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "palworld-dashboard/1.0"})
    with urllib.request.urlopen(req, timeout=45) as r:  # noqa: S310
        return r.read().decode("utf-8", "replace")


def game_strings() -> dict[str, str]:
    """
    `WORLDSSETTING_*` rows from the client pak, or `{}` when it is not installed.

    Absent is not an error: `refs/` is gitignored and most checkouts will not
    have 40 GB of pak. The bundle simply carries no labels, and the descriptions
    — which are the useful half — are unaffected.
    """
    try:
        import l10n
        import palpak
    except ImportError:
        return {}
    pak_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "refs", "Pal-Windows.pak"
    )
    if not os.path.exists(pak_path):
        print("  client pak absent — labels will be omitted, descriptions are unaffected")
        return {}
    rows = l10n.read_table(palpak.Pak(pak_path), "DT_UI_Common_Text_Common", "en")
    return {
        k[len("WORLDSSETTING_"):]: v
        for k, _key, v in ((r[0], r[1], r[2]) for r in rows)
        if k.startswith("WORLDSSETTING_")
    }


def build(html_dir: Optional[str], show_aliases: bool) -> dict:
    docs: dict[str, str] = {}
    pages_used = []
    for name, url in DOC_PAGES.items():
        if html_dir:
            path = os.path.join(html_dir, f"pal_{name}.html")
            if not os.path.exists(path):
                path = os.path.join(html_dir, "palconf.html") if name == "configuration" else ""
            if not path or not os.path.exists(path):
                continue
            raw = open(path, encoding="utf-8").read()
        else:
            print(f"  fetching {url}")
            raw = fetch(url)
        found = parse_doc_tables(raw)
        before = len(docs)
        for k, v in found.items():
            docs.setdefault(k, v)
        pages_used.append({"page": name, "url": url, "newRows": len(docs) - before})

    ui = game_strings()

    # Exact key match first, then the hand-written aliases. Exact wins, so an
    # alias can never shadow a row the game already named correctly.
    exact_labels = {k: v for k, v in ui.items() if k not in UI_ALIASES}
    aliased = {UI_ALIASES[k]: v for k, v in ui.items() if k in UI_ALIASES}

    if show_aliases:
        print("\n  alias cross-check — the pak string and the official row must agree:")
        for row, key in sorted(UI_ALIASES.items()):
            print(f"    {row} -> {key}")
            print(f"       pak : {ui.get(row, '(absent)')[:100]!r}")
            print(f"       docs: {docs.get(key, '(absent)')[:100]!r}")

    values: dict[str, dict[str, str]] = {}
    for key, prefix in VALUE_PREFIXES.items():
        found = {
            row[len(prefix):]: text
            for row, text in ui.items()
            if row.startswith(prefix) and row != prefix.rstrip("_")
        }
        alias = VALUE_ALIASES.get(key, {})
        found = {alias.get(v, v): text for v, text in found.items()}
        if found:
            values[key] = found

    import settings_ini

    ini_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "refs", "palworld",
        "DefaultPalWorldSettings.ini",
    )
    keys = list(settings_ini.read_ini(ini_path)["options"]) if os.path.exists(ini_path) else []

    settings = {}
    for key in keys or sorted(set(docs) | set(exact_labels) | set(aliased)):
        entry = {}
        if key in docs:
            entry["description"] = docs[key]
            entry["descriptionSource"] = "official"
        label = exact_labels.get(key) or aliased.get(key)
        if label and len(label) > MAX_LABEL_CHARS:
            label = None
        if label:
            entry["label"] = label
            entry["labelSource"] = "game"
        if key in DASHBOARD_NOTES:
            entry["note"] = DASHBOARD_NOTES[key]
            entry["noteSource"] = "dashboard"
        if entry:
            settings[key] = entry

    return {
        "settings": settings,
        "values": values,
        "sources": {
            "description": {
                "name": "Palworld official documentation",
                "url": "https://docs.palworldgame.com/settings-and-operation/configuration/",
                "pages": pages_used,
                "fetched": date.today().isoformat(),
            },
            "label": {
                "name": "Pal-Windows.pak, DT_UI_Common_Text (English L10N override)",
                "rows": "WORLDSSETTING_*",
                "available": bool(ui),
            },
            "note": {"name": "This dashboard's own measurements — see AGENTS.md"},
        },
        "iniKeys": len(keys),
        "documented": sum(1 for k in keys if k in docs) if keys else None,
        "labelled": sum(1 for k in keys if k in exact_labels or k in aliased) if keys else None,
        "undocumented": [k for k in keys if k not in settings],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", help="directory of already-saved doc pages, to avoid refetching")
    ap.add_argument("--show-aliases", action="store_true",
                    help="print each alias with both sources, for re-checking by hand")
    ap.add_argument("--out", default=OUT_PATH)
    args = ap.parse_args()

    bundle = build(args.html, args.show_aliases)

    n = len(bundle["settings"])
    print(f"\n  settings with help  : {n}")
    if bundle["iniKeys"]:
        print(f"  of the INI's        : {bundle['iniKeys']} keys")
        print(f"  official description: {bundle['documented']}")
        print(f"  game label          : {bundle['labelled']}")
        print(f"  no help at all      : {len(bundle['undocumented'])}")
        for key in bundle["undocumented"]:
            print(f"      {key}")

    # A build that produced almost nothing is a broken parse, not a game that
    # removed its documentation. Refuse rather than ship an empty bundle over a
    # good one — the same posture every extractor here takes.
    if bundle["iniKeys"] and bundle["documented"] < 60:
        print("\n  REFUSING: fewer than 60 keys documented — the page layout probably changed")
        return 1

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    from jsonout import write_json

    write_json(args.out, bundle)
    print(f"\n  wrote {args.out} ({os.path.getsize(args.out) / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
