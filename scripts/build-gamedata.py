#!/usr/bin/env python3
"""
Build the bundled game-data blob from the PalworldSaveTools reference archive.

`refs/` holds 66 MB of third-party zips and is gitignored; the compact blob this
produces (`backend/data/gamedata.json.gz`) IS committed, so the dashboard needs
neither the archive nor any network access at runtime. The container has to work
offline on a LAN.

Run after dropping in a newer PalworldSaveTools release:

    python3 scripts/build-gamedata.py

Source data is MIT (© 2026 Pylar); see README "Credits". The underlying game
content belongs to Pocketpair.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE = os.path.join(ROOT, "refs", "PalWorldSaveTools-main.zip")
OUT_PATH = os.path.join(ROOT, "backend", "data", "gamedata.json.gz")

# Marker for a Pal/skill/tech field the game leaves unset.
NONE_VALUES = {"None", "EPalElementType::None", ""}


def _load(zf: zipfile.ZipFile, name: str) -> dict:
    """Read one JSON file out of resources/game_data/."""
    matches = [
        n for n in zf.namelist() if n.endswith(f"resources/game_data/{name}")
    ]
    if not matches:
        raise SystemExit(f"!! {name} not found in {ARCHIVE}")
    with zf.open(matches[0]) as f:
        return json.load(io.TextIOWrapper(f, encoding="utf-8"))


def _clean(text: str) -> str:
    """Game descriptions carry literal CRLF escapes from the data tables."""
    return " ".join((text or "").replace("\r\n", " ").replace("\n", " ").split())


def _elements(pal: dict) -> list[str]:
    return [e.get("name", key) for key, e in (pal.get("elements") or {}).items()]


def build() -> dict:
    if not os.path.exists(ARCHIVE):
        raise SystemExit(
            f"!! {ARCHIVE} not found.\n"
            "   Download PalworldSaveTools-main.zip from\n"
            "   https://github.com/deafdudecomputers/PalworldSaveTools into refs/"
        )

    with zipfile.ZipFile(ARCHIVE) as zf:
        items_raw = _load(zf, "items.json")
        chars_raw = _load(zf, "characters.json")
        skills_raw = _load(zf, "skills.json")
        world_raw = _load(zf, "world.json")
        fast_travel = _load(zf, "fast_travel_points.json")
        work_raw = _load(zf, "work_suitability.json")
        relic_raw = _load(zf, "relic_data.json")
        boss_raw = _load(zf, "boss_mapping.json")
        exp_raw = _load(zf, "pal_exp_table.json")

    dynamic = items_raw.get("items_dynamic") or {}

    # ─── Items ───────────────────────────────────────────
    items: dict[str, dict] = {}
    for entry in items_raw["items"]:
        asset = entry.get("asset")
        if not asset:
            continue
        record = {
            "name": entry.get("name") or asset,
            "icon": entry.get("icon") or "",
            "rarity": entry.get("rarity", 0),
            "typeA": entry.get("type_a_display") or "",
            "typeB": entry.get("type_b_display") or "",
            "sortId": entry.get("sort_id", 0),
            # The real per-item stack ceiling. The container sorter currently has
            # to infer this from the largest stack it can find in the save.
            "maxStack": entry.get("max_stack", 0),
            "weight": entry.get("weight", 0),
            "price": entry.get("price", 0),
        }
        description = _clean(entry.get("description", ""))
        if description:
            record["description"] = description
        if asset in dynamic:
            record["dynamic"] = dynamic[asset].get("dynamic", {})
        items[asset] = record

    # ─── Pals & NPCs ─────────────────────────────────────
    pals: dict[str, dict] = {}
    for entry in chars_raw["pals"]:
        asset = entry.get("asset")
        if not asset:
            continue
        stats = entry.get("stats") or {}
        record = {
            "name": entry.get("name") or asset,
            "icon": entry.get("icon") or "",
            "elements": _elements(entry),
            "rarity": stats.get("rarity", 0),
            "zukanIndex": stats.get("zukan_index", 0),
            "size": (stats.get("size") or "").replace("EPalSizeType::", ""),
            "stats": {
                "hp": stats.get("hp", 0),
                "meleeAttack": stats.get("melee_attack", 0),
                "shotAttack": stats.get("shot_attack", 0),
                "defense": stats.get("defense", 0),
                "craftSpeed": stats.get("craft_speed", 0),
            },
            # Per-species trust coefficients, used by `backend/palstats.py`.
            #
            # They live at the *entry* root rather than under `stats`, which is
            # where they sit in the reference data too. Without them the trust
            # term of the stat formula cannot be evaluated at all — and unlike
            # every other term there is no sensible default, since the whole
            # point is that species differ (Melpaca 4.5/3.5/2.9).
            #
            # `scaling` exists in the reference data as well and is deliberately
            # NOT carried: it duplicates `stats` for ordinary Pals and PST's own
            # formula reads `hp`/`defense` from whichever is present, so having
            # both here would be two sources of truth for one number.
            "friendship": {
                "hp": entry.get("friendship_hp", 0) or 0,
                "shotAttack": entry.get("friendship_shotattack", 0) or 0,
                "defense": entry.get("friendship_defense", 0) or 0,
            },
            "workSuitabilities": {
                k: v for k, v in (entry.get("work_suitabilities") or {}).items() if v
            },
        }
        description = _clean(entry.get("description", ""))
        if description:
            record["description"] = description
        pals[asset] = record

    npcs = {
        entry["asset"]: {"name": entry.get("name") or entry["asset"],
                         "icon": entry.get("icon") or ""}
        for entry in chars_raw.get("npcs", [])
        if entry.get("asset")
    }

    # ─── Skills ──────────────────────────────────────────
    passives = {}
    for entry in skills_raw["passives"]:
        asset = entry.get("asset")
        if not asset:
            continue
        passives[asset] = {
            "name": entry.get("name") or asset,
            "rank": entry.get("rank", 0),
            "icon": entry.get("icon") or "",
            "description": _clean(entry.get("description", "")),
        }

    actives = {}
    for entry in skills_raw["skills"]:
        asset = entry.get("asset")
        if not asset:
            continue
        actives[asset] = {
            "name": entry.get("name") or asset,
            "element": entry.get("element") or "",
            "power": entry.get("power", 0),
            "cooldown": entry.get("cooldown", 0),
            "description": _clean(entry.get("description", "")),
        }

    # ─── Technology ──────────────────────────────────────
    technology = {}
    for entry in world_raw["technology"]:
        asset = entry.get("asset")
        if not asset:
            continue
        technology[asset] = {
            "name": entry.get("name") or asset,
            "icon": entry.get("icon") or "",
            "type": entry.get("type") or "standard",
            "cost": entry.get("cost", 0),
            "tier": entry.get("tier", 0),
            "levelCap": entry.get("level_cap", 0),
            "isBossTech": bool(entry.get("is_boss_tech")),
        }

    structures = {
        entry["asset"]: {
            "name": entry.get("name") or entry["asset"],
            "icon": entry.get("icon") or "",
            "type": entry.get("type_a_display") or "",
        }
        for entry in world_raw["structures"]
        if entry.get("asset") and entry.get("name") != "---"
    }

    # ─── Fast travel ─────────────────────────────────────
    # Coordinates are in the same world space the save uses, so these feed the
    # existing worldToGameMap transform directly.
    travel = {
        key: {
            "x": entry["x"],
            "y": entry["y"],
            "z": entry.get("z", 0),
            "name": entry.get("localized_name") or entry.get("id") or key,
            "id": entry.get("id") or "",
        }
        for key, entry in fast_travel.items()
        if "x" in entry and "y" in entry
    }

    # ─── Exact progression denominators ──────────────────
    standard = [t for t in technology.values() if not t["isBossTech"]]
    boss = [t for t in technology.values() if t["isBossTech"]]

    totals = {
        "technologyPoints": sum(t["cost"] for t in standard),
        "ancientTechnologyPoints": sum(t["cost"] for t in boss),
        "technologyCount": len(standard),
        "ancientTechnologyCount": len(boss),
        "fastTravelPoints": len(travel),
        # Two different denominators, and the distinction matters. A player's
        # PaldeckUnlockFlag keys on individual *forms* (Kitsun and Kitsun Noct
        # are separate entries), so that is the number to measure completion
        # against. The count of distinct Paldeck numbers is smaller because
        # variants share a number with a letter suffix.
        "paldeckForms": sum(1 for p in pals.values() if p["zukanIndex"] > 0),
        "paldeckNumbers": len({p["zukanIndex"] for p in pals.values() if p["zukanIndex"] > 0}),
        "itemTypes": len(items),
        "palForms": len(pals),
        "passives": len(passives),
        "activeSkills": len(actives),
        "structures": len(structures),
    }

    return {
        "_source": "PalworldSaveTools resources/game_data (MIT, (c) 2026 Pylar)",
        "_note": "Generated by scripts/build-gamedata.py — do not edit by hand.",
        "totals": totals,
        "items": items,
        "pals": pals,
        "npcs": npcs,
        "passives": passives,
        "activeSkills": actives,
        "technology": technology,
        "structures": structures,
        "fastTravel": travel,
        "workSuitability": (work_raw.get("work_types") or []),
        "relics": relic_raw,
        "bossFlagMap": boss_raw.get("boss_defeat_flag_map") or {},
        "palExpTable": exp_raw,
    }


PUBLIC_ICONS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "public"
)


def resolve_icons(data: dict) -> dict[str, int]:
    """
    Point every icon path at the file that is actually on disk, case and all.

    **The reference data's capitalisation does not always match its own
    filenames.** It records `T_Thunderdog_Ice_icon_normal.webp` for a file that
    ships as `T_ThunderDog_Ice_icon_normal.webp`, and that is the whole bug: it
    resolves on a case-insensitive filesystem (macOS, Windows) and 404s in the
    Linux container, so it survived every developer's local run and broke only
    where it matters. Two Pals had no artwork on a live server for that reason.

    Same class of problem `gamedata`'s case-insensitive lookups exist for — the
    upstream data is inconsistently capitalised — fixed here, at build time,
    against the ground truth of what is in `public/icons/`.

    Anything still unresolved is **reported rather than blanked**. An empty icon
    path and a wrong one both render as no artwork, but only one of them tells
    you a regeneration lost something.
    """
    index: dict[str, dict[str, str]] = {}

    def lookup(icon: str) -> str:
        directory, base = os.path.split(icon.lstrip("/"))
        if directory not in index:
            full = os.path.join(PUBLIC_ICONS, directory)
            try:
                index[directory] = {n.lower(): n for n in os.listdir(full)}
            except OSError:
                index[directory] = {}
        actual = index[directory].get(base.lower())
        return f"/{directory}/{actual}" if actual else ""

    stats: dict[str, dict[str, int]] = {}
    for section in ("items", "pals", "npcs", "technology", "structures"):
        counts = stats.setdefault(section, {"checked": 0, "corrected": 0, "missing": 0})
        for key, entry in (data.get(section) or {}).items():
            icon = entry.get("icon") or ""
            if not icon:
                continue
            counts["checked"] += 1
            if os.path.exists(os.path.join(PUBLIC_ICONS, icon.lstrip("/"))):
                continue
            corrected = lookup(icon)
            if corrected:
                entry["icon"] = corrected
                counts["corrected"] += 1
                print(f"  icon case corrected: {section}/{key}: {icon} -> {corrected}")
            else:
                counts["missing"] += 1
    return stats


def main() -> int:
    data = build()

    icons = resolve_icons(data)

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with gzip.open(OUT_PATH, "wb", compresslevel=9) as f:
        f.write(payload)

    totals = data["totals"]
    print(f"Wrote {OUT_PATH}")
    # Per section, because one aggregate is unreadable here. `technology` and
    # `structures` are ~100% "missing" **by design** — `scripts/install-icons.py`
    # deliberately does not ship them (994 files, 6.4 MB, and nothing renders a
    # tech tree). Rolling them into one total would bury a real regression in
    # items or Pals under a number that is supposed to be large.
    print("  icons:")
    for section, counts in icons.items():
        if not counts["checked"]:
            continue
        resolved = counts["checked"] - counts["missing"]
        note = (
            "  (not installed by design — see scripts/install-icons.py)"
            if section in ("technology", "structures") else ""
        )
        print(
            f"    {section:12s} {resolved:5,}/{counts['checked']:,} resolve"
            f"  {counts['corrected']} case-corrected{note}"
        )
    print(f"  {len(payload):,} bytes raw -> {os.path.getsize(OUT_PATH):,} bytes gzipped")
    print()
    for key, value in totals.items():
        print(f"  {key:26s} {value:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
