#!/usr/bin/env python3
"""
Build the bundled game-data blob — display strings from the game, numbers
verified against it, artwork paths from the reference archive.

`refs/` holds the paks and a 66 MB third-party zip and is gitignored; the compact
blob this produces (`backend/data/gamedata.json.gz`) IS committed, so the
dashboard needs neither of them, nor any network access, at runtime. The
container has to work offline on a LAN.

    python3 scripts/build-gamedata.py

THREE SOURCES, AND WHICH ONE WINS IS DELIBERATE
-----------------------------------------------
- **Display names and descriptions: the game.** `apply_game_names` overwrites
  every one it can from the client pak's `L10N/en` overrides, via
  `scripts/l10n.py` and `scripts/gametext.py`.
- **Numbers: the game.** Not read here, but `scripts/verify-gamedata.py` checks
  the archive's values against the server pak's DataTables — 13,836 of 13,836
  agree, which is why this still reads them from the archive rather than
  duplicating the extraction.
- **Icon paths and catalogue membership: the archive.** Which ids exist, and
  where their artwork lives. Re-deriving 2,468 textures is task-listed and
  low-value.

**Entries the game does not name keep the archive's value, and the count is
printed per section as "still from archive" rather than being folded into a
coverage figure that looks complete.** Same discipline as the icon report below
it: one aggregate would bury a regression.

Source data is MIT (© 2026 Pylar); see README "Credits". The underlying game
content belongs to Pocketpair — extracting it ourselves changes who we owe
credit to, not the copyright position.
"""

from __future__ import annotations

import gzip
import io
import json
import re
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


# Untranslated markers. The game's spellings, plus the fact that the *archive*
# carries them too — `Scratch` and `Throw` have shipped as the literal string
# "en Text" since this bundle was first built.
_PLACEHOLDER_NAMES = {"en text", "en_text", "unidentified pal", "-", "--", "---", ""}


def _is_placeholder(name: str | None) -> bool:
    return (name or "").strip().lower() in _PLACEHOLDER_NAMES


def _humanize(ident: str) -> str:
    """`Accessory_AirDash1` -> `Accessory Air Dash1`. Mirrors `gamedata.humanize`."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", ident.replace("_", " "))
    return re.sub(r"\s+", " ", spaced).strip() or ident


def apply_game_names(data: dict) -> dict[str, dict[str, int]]:
    """
    Replace every display name and description with the **game's own**.

    This is the last piece of `docs/PLAN.md`'s attribution work: the numbers in
    this bundle already come from the server pak (verified 13,836 of 13,836),
    and after this the strings come from the client pak's `L10N/` overrides. The
    third-party archive is left supplying artwork and catalogue membership only.

    **The archive is kept as a fallback where the game names nothing**, and the
    count of those is printed rather than hidden. An id the game does not name
    is usually cut or unreleased content; silently dropping to an internal id
    would be a visible regression, and silently keeping the archive's value
    without saying so would make this look more complete than it is.

    Joins are case-insensitive throughout, which is the same decision
    `gamedata.py` documents: the upstream data is inconsistently capitalised and
    exact matching silently loses real entries.
    """
    import gametext  # local: only needed when regenerating, and it wants the pak

    cat = gametext.Catalogue("en")
    stats: dict[str, dict[str, int]] = {}

    for section in ("items", "pals", "structures", "technology",
                    "activeSkills", "passives"):
        entries = data.get(section) or {}
        names = {k.lower(): v for k, v in cat.names(section).items()}
        try:
            descs = {k.lower(): v for k, v in cat.descriptions(section).items()}
        except KeyError:
            descs = {}

        counts = stats.setdefault(
            section, {"total": len(entries), "named": 0, "renamed": 0,
                      "fellBack": 0, "described": 0}
        )

        for ident, entry in entries.items():
            # One entry point, which joins *and* resolves. Calling the raw
            # lookups here shipped `<characterName id=|FlowerPrince|/>'s Petal`
            # as a literal item name.
            text = cat.name(section, ident)

            if text:
                counts["named"] += 1
                if entry.get("name") != text:
                    counts["renamed"] += 1
                entry["name"] = text
            else:
                counts["fellBack"] += 1
                # **The archive ships placeholders too**, and this was already
                # true before the swap: `Scratch` and `Throw` have shipped as
                # the literal string "en Text" for as long as the bundle has
                # existed. Falling back to it is worse than falling back to the
                # id, which at least reads as an id.
                if _is_placeholder(entry.get("name")):
                    counts["placeholderDropped"] = counts.get("placeholderDropped", 0) + 1
                    entry["name"] = _humanize(ident)

            description = descs.get(ident.lower())
            if description:
                counts["described"] += 1
                entry["description"] = description

        stats[section] = counts

    # Region and dungeon names were not in this bundle at all before — the
    # progression extractor deliberately carried `REGION_Grass_1` unresolved
    # rather than inventing "Grass 1". The game's own answer is
    # "Windswept Island".
    for section, source in (("regions", "regions"), ("dungeons", "dungeons")):
        resolved = cat.names(source)
        if resolved:
            data[section] = resolved
            stats[section] = {"total": len(resolved), "named": len(resolved),
                              "renamed": 0, "fellBack": 0, "described": 0}

    data["_source"] = (
        "Numbers from Pal-LinuxServer.pak DataTables; display strings from "
        "Pal-Windows.pak L10N/en overrides. See docs/GAMEDATA-SOURCES.md."
    )
    return stats


def apply_species_fields(data: dict) -> dict[str, int]:
    """
    Species fields the reference archive does not carry, read from the game.

    Three, all from `DT_PalMonsterParameter`.

    **`bestWorkSuitability`**, from the `BestWorkSuitability` column. One work
    type per species — `Umihebi_Fire` (Jormuntide Ignis) is `EmitFlame`.

    **This is the per-species half of the condenser mechanic.** Raising a Pal's
    condenser rank raises its work suitability for *this* work type only, which
    is why the field matters even though the magnitude of the increase is not in
    any file — searched exhaustively, including all 471 DataTables, the game
    settings CDO, the condenser build object's CDO, and the name tables of all
    10,286 server-pak blueprints, which reference `BestWorkSuitability` **zero**
    times. That logic is in the binary. See task #74.

    So this ships the fact and not the arithmetic: nothing here claims what the
    bonus *is*. `optimise.work_level` continues to report `base` + `bought` and
    is knowingly low for condensed Pals until the magnitude is measured.

    The enum prefix is stripped (`EPalWorkSuitability::EmitFlame` -> `EmitFlame`)
    so it matches the ids `workSuitabilities` already uses — a caller must be
    able to look one up with the other.

    **`zukanSuffix`** and **`ignoreCombi`** are the two breeding-eligibility
    columns, carried so `breeding.obtainability` can answer "why can I not breed
    this" out of the game's own data rather than a hand-written list.

    - `ZukanIndexSuffix == "B"` marks an element variant — 90 of 753, the `B` a
      player already reads on Paldeck entry #98B. It does **not** mean
      unbreedable: `DT_PalCombiUnique` names 81 of them as children. It means
      the pairing must be one the game lists outright, never the rank fallback.
    - `IgnoreCombi` — 226 of 753. It means the **rank fallback** never produces
      this species; it does *not* mean unbreedable and does *not* mean it
      cannot be a parent. A named pairing overrides it (Frostallion Noct is
      Frostallion + Helzephyr), and all 28 Paldeck-listed ones are productive
      parents of 70-100 species each. Same constraint the suffix expresses.

    Both are written **only when they say something** (suffix non-empty,
    `ignoreCombi` true), so a caller's `.get()` default is the common case and
    the blob does not grow by 753 falses.
    """
    import palpak
    import uassettable

    pak = palpak.Pak()
    path = next(
        (f for f in pak.files if f.endswith("DT_PalMonsterParameter.uasset")), None
    )
    if path is None:
        raise SystemExit("!! DT_PalMonsterParameter not in the server pak")

    rows = uassettable.read_table(pak, path)
    lowered = {str(k).lower(): v for k, v in rows.items()}

    counts = {"total": len(data.get("pals") or {}), "resolved": 0, "unmatched": 0,
              "variants": 0, "noBreeding": 0}
    for ident, entry in (data.get("pals") or {}).items():
        row = lowered.get(ident.lower())
        if row is None:
            counts["unmatched"] += 1
            continue
        best = str(row.get("BestWorkSuitability") or "")
        best = best.rsplit("::", 1)[-1] if "::" in best else best
        # `None` is the game's own "no best work", and several species genuinely
        # have an empty work table — Panthalus and Astralym among them. Absent
        # rather than empty-string, so a caller can tell "no best" from "not read".
        if best and best != "None":
            entry["bestWorkSuitability"] = best
            counts["resolved"] += 1

        suffix = str(row.get("ZukanIndexSuffix") or "")
        if suffix:
            entry["zukanSuffix"] = suffix
            counts["variants"] += 1
        if row.get("IgnoreCombi"):
            entry["ignoreCombi"] = True
            counts["noBreeding"] += 1
    return counts


def main() -> int:
    data = build()

    species = apply_species_fields(data)
    names = apply_game_names(data)
    icons = resolve_icons(data)

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with gzip.open(OUT_PATH, "wb", compresslevel=9) as f:
        f.write(payload)

    totals = data["totals"]
    print(f"Wrote {OUT_PATH}")
    # Per section for the same reason the icon report is: one aggregate would
    # bury a regression. `fellBack` is the number that matters — those entries
    # still carry the third-party archive's name.
    print(
        f"  bestWorkSuitability: {species['resolved']:,}/{species['total']:,} species"
        f"  ({species['unmatched']} not in DT_PalMonsterParameter)"
    )
    print(
        f"  breeding columns:    {species['variants']} element variants"
        f" (ZukanIndexSuffix), {species['noBreeding']} with IgnoreCombi"
    )
    print("  names (from the game's own L10N tables):")
    for section, counts in names.items():
        print(
            f"    {section:13s} {counts['named']:5,}/{counts['total']:,} named"
            f"  {counts['renamed']:5,} changed"
            f"  {counts['fellBack']:5,} still from archive"
            f"  {counts['described']:5,} described"
        )
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
