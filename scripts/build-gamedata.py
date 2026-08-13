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

    # ── The partner skill's own name and description, onto the Pal ──
    #
    # A separate pass because it is keyed on the SPECIES id but is not the
    # species' own name or Paldeck text: `PARTNERSKILL_WhiteShieldDragon` is
    # "Aegis Shield", and what it does is written in the first-catch info text
    # one table over.
    #
    # **The description keeps its `{Passive1_EffectValue1}` placeholders.** They
    # cannot be filled here: the numbers come from `partner_skills.json.gz`
    # joined to `passive_effects.json.gz`, and they differ per condenser rank —
    # Silvegis reduces shield damage by 65% at one star and 80% at five, so
    # there is no single string to bake. `gamedata.partner_skill_text` fills
    # them at a named rank and refuses to half-fill.
    partner_names = {k.lower(): v for k, v in cat.names("partnerSkills").items()}
    partner_descs = {k.lower(): v for k, v in cat.descriptions("partnerSkills").items()}
    partner_counts = {"total": len(data.get("pals") or {}), "named": 0,
                      "described": 0}
    for ident, entry in (data.get("pals") or {}).items():
        name = partner_names.get(ident.lower())
        description = partner_descs.get(ident.lower())
        if not name and not description:
            continue
        skill: dict = {}
        if name:
            skill["name"] = name
            partner_counts["named"] += 1
        if description:
            skill["description"] = description
            partner_counts["described"] += 1
        entry["partnerSkill"] = skill
    stats["partnerSkills"] = partner_counts

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


# The movement and stamina columns, and the key each becomes in the bundle.
# `Stamina` travels with them because it is the other half of the question a
# mount is picked for: top speed against how long it holds.
_MOVEMENT_COLUMNS = {
    "RideSprintSpeed": "rideSprint",
    "RunSpeed": "run",
    "WalkSpeed": "walk",
    "SlowWalkSpeed": "slowWalk",
    "SwimSpeed": "swim",
    "SwimDashSpeed": "swimDash",
    "TransportSpeed": "transport",
    "Stamina": "stamina",
}


def _movement(row: dict) -> dict:
    """
    A species' movement figures, with the game's own "not applicable" dropped.

    **`-1` is a sentinel, not a speed**, and it is not rare: 60 species carry it
    on `RideSprintSpeed`, 52 on `SwimSpeed` and 105 on `TransportSpeed`. Carrying
    it through would put those species at the bottom of every ranking as though
    they were merely slow, so the key is omitted and a caller's `.get()` returns
    None — the same absent-means-absent rule `bestWorkSuitability` follows.

    **A `-1` ride speed does NOT mean "cannot be ridden", and the two must not be
    conflated.** Caprity reads 960 and is not rideable; the mount list is
    `rideable`, from an entirely different table. See `_mounts`.
    """
    out = {}
    for column, key in _MOVEMENT_COLUMNS.items():
        value = row.get(column)
        if isinstance(value, (int, float)) and value >= 0:
            out[key] = int(value)
    return out


def _mounts(pak, uassettable) -> dict[str, str]:
    """
    Which species can actually be ridden, from `DT_PartnerSkillParameter`.

    **THIS CORRECTS A NOTE IN AGENTS.md THAT SAID IT COULD NOT BE SETTLED.** That
    note is right that "has PalGear" is not "is a mount" — `DT_ItemDataTable` has
    143 `Essential_PalGear` items and Galeclaw's Gloves is one of them. It then
    carried that doubt across to `RestrictionItems`, naming Galeclaw as the
    counterexample there too. Galeclaw is not in `RestrictionItems`. Measured:

        Essential_PalGear items                       143
        items named by any RestrictionItems           126
        PalGear items named by none                    17

    and the 17 are **exactly** the partner gear you hold or wear rather than
    ride: five gloves (Galeclaw, Celaray, Jolthog, Killamari, Hangyu), three
    necklaces that summon a companion (Daedream, Flopie, Dazzi), two harnesses,
    a headband and a power converter. Every known non-mount checked — Lamball,
    Caprity, Katress, Kitsun, Dumud and Galeclaw itself — is absent, and every
    known mount is present.

    So `RestrictionItems` **is** the mount list. 149 base species; the rest of
    the 290 rows are `BOSS_`/`PREDATOR_`/gym forms of the same Pals.

    **It still says nothing about the MODE.** Whether a mount flies, swims or
    walks is in no file, which AGENTS.md establishes at length and which two
    further avenues checked here did not change: the client pak has no
    `BP_Pal_<species>` blueprint at all, and per-species animation folders
    (213 of 753) do not attribute it either — Jetragon has no fly-named
    animation and every species has an `Idle_Swim`. "Fastest ride" is
    answerable; "fastest flyer" is not.
    """
    path = next(
        (f for f in pak.files if f.endswith("DT_PartnerSkillParameter.uasset")), None
    )
    if path is None:
        raise SystemExit("!! DT_PartnerSkillParameter not in the server pak")

    out: dict[str, str] = {}
    for species, row in uassettable.read_table(pak, path).items():
        for entry in row.get("RestrictionItems") or []:
            # An FName cell decodes as {"Key": ...}; `str()` on that is the trap
            # the Pal-shop rosters shipped for months.
            item = entry.get("Key") if isinstance(entry, dict) else str(entry)
            # `None` is the game's own empty slot and appears in this column.
            if item and item != "None":
                out[str(species).lower()] = str(item)
                break
    return out


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

    **`movement` and `rideable`** are the fourth and fifth, added for the build
    planner — see `_movement` and `_mounts` for what each is and, in the second
    case, for the note it corrects.
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
    mounts = _mounts(pak, uassettable)

    counts = {"total": len(data.get("pals") or {}), "resolved": 0, "unmatched": 0,
              "variants": 0, "noBreeding": 0, "movement": 0, "rideable": 0,
              "maxFullStomach": 0}
    for ident, entry in (data.get("pals") or {}).items():
        row = lowered.get(ident.lower())
        if row is None:
            counts["unmatched"] += 1
            continue

        movement = _movement(row)
        if movement:
            entry["movement"] = movement
            counts["movement"] += 1

        # **The fullness ceiling, which this project recorded as not existing.**
        # AGENTS.md said "fullStomach is still unbounded — that one genuinely
        # has no constant"; `editschema` said it "is not stored anywhere in the
        # save". Only the second is true, and it was read as a general absence:
        # the cap is a column on this very row, on all 753 species.
        #
        # Verified as a CAP rather than a base — 1,635 of refworld's Pals sit
        # inside theirs with zero exceptions and a maximum ratio of exactly
        # 1.000.
        #
        # It is per-FORM, but only just: **302 of the 303 `BOSS_`/base pairs are
        # identical**, and the one that differs is `BOSS_YakushimaBoss001` (320
        # against 240). So `pal_exact` is still the correct reader and the cost
        # of `pal` would be one species — which is worth stating precisely,
        # because "alphas have different caps" would be a much bigger claim than
        # the data supports.
        stomach = row.get("MaxFullStomach")
        if isinstance(stomach, (int, float)) and stomach > 0:
            entry["maxFullStomach"] = int(stomach)
            counts["maxFullStomach"] += 1
        gear = mounts.get(ident.lower())
        if gear:
            entry["rideable"] = True
            entry["mountGearItem"] = gear
            counts["rideable"] += 1
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


def apply_item_legality(data: dict) -> dict[str, int]:
    """
    `DT_ItemDataTable.bLegalInGame`, and the one claim it actually supports.

    575 of the 2,466 items are `False`. The tempting reading is "unobtainable",
    and it is **wrong**: ten of them are sitting in refworld's containers right
    now — all seven `KeySphere_01..07`, `WhaleWhistle`, `Blueprint_WhaleWhistle`
    and `MachingunBullet`. A badge saying "unobtainable" would be a confident
    falsehood about items a player owns, which is worse than no badge.

    The id convention does not rescue it either. "The `2` suffix is the live
    one" holds for `Gunpowder`/`Gunpowder2` and fails for `Leather`, which is a
    crafting material while `Leather2` is the illegal one.

    So only one thing is written, and only where it is checkable: **an illegal
    item that shares its display name with exactly one legal item**. That is the
    case the item creator actually gets wrong — someone types "Gunpowder", sees
    two identical rows, and spawns the dead one. 95 of the 575 qualify:

        Gunpowder          -> Gunpowder2
        PalSphere_Debug    -> PalSphere
        Glider_Legendary   -> Glider_Tera
        Head001_2..05      -> Head001

    **89 of those 95 collide only because of OUR naming rule**, and that is a
    feature rather than a caveat. `apply_game_names` is exact-first,
    base-fallback (AGENTS.md, "Display names come from the CLIENT pak"), so
    `Head001_2` shows "Monarch's Crown" because the game never named it at all.
    An illegal item the game declined to name, wearing a live item's name, is
    exactly a dead tier. The other 6 the game names twice itself, and they are
    the same shape — `PalSphere_Debug` is a debug sphere called "Pal Sphere".

    **The 474 with no legal namesake get `legalInGame` and nothing more**, which
    is where the ten held items land, so none of them is ever badged. The 6 with
    *two* legal namesakes (`OctaviaRevolver_2..4`, whose tiers 1 and 5 are both
    live) get no twin either: there is no unique answer and guessing one would
    be inventing data.

    Case is folded on the join for the reason `gamedata.py` documents and the
    technology join already needed — an `FName` compares case-insensitively and
    a `dict` does not.
    """
    import palpak
    import uassettable

    pak = palpak.Pak()
    path = next(
        (f for f in pak.files if f.endswith("DT_ItemDataTable.uasset")), None
    )
    if path is None:
        raise SystemExit("!! DT_ItemDataTable not in the server pak")

    rows = uassettable.read_table(pak, path)
    legal = {str(k).lower(): bool(r.get("bLegalInGame")) for k, r in rows.items()}

    items = data.get("items") or {}
    counts = {"total": len(items), "unmatched": 0, "illegal": 0,
              "twin": 0, "noTwin": 0, "ambiguous": 0}

    # Group by display name so the twin lookup is one pass. Only entries the
    # table actually covers take part: an item with no row has no opinion to
    # contribute in either direction.
    by_name: dict[str, list[tuple[str, bool]]] = {}
    for ident, entry in items.items():
        state = legal.get(ident.lower())
        if state is None:
            continue
        by_name.setdefault(str(entry.get("name") or ""), []).append((ident, state))

    for ident, entry in items.items():
        state = legal.get(ident.lower())
        if state is None:
            counts["unmatched"] += 1
            continue
        if state:
            continue
        # Written only when it says something, like `zukanSuffix` above: the
        # blob does not grow by 1,891 trues.
        entry["legalInGame"] = False
        counts["illegal"] += 1

        live = [i for i, s in by_name.get(str(entry.get("name") or ""), []) if s]
        if len(live) == 1:
            entry["liveTwin"] = live[0]
            counts["twin"] += 1
        elif not live:
            counts["noTwin"] += 1
        else:
            counts["ambiguous"] += 1
    return counts


def main() -> int:
    data = build()

    species = apply_species_fields(data)
    names = apply_game_names(data)
    # AFTER the names, never before: the twin join keys on the display name, and
    # before this runs those are the third-party archive's rather than the
    # game's. Reordering these two silently changes which items pair up.
    legality = apply_item_legality(data)
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
        f"  movement: {species['movement']:,} species with speed/stamina figures; "
        f"{species['rideable']:,} rideable (RestrictionItems — the mount list, "
        "which is NOT the PalGear item list; see _mounts)"
    )
    print(
        f"  breeding columns:    {species['variants']} element variants"
        f" (ZukanIndexSuffix), {species['noBreeding']} with IgnoreCombi"
    )
    print("  names (from the game's own L10N tables):")
    for section, counts in names.items():
        # `partnerSkills` is counted with a narrower shape than the six text
        # sections — it has no archive value to fall back to, so no `renamed`
        # or `fellBack`. Indexing them unconditionally crashed the report after
        # the bundle had already been written, which is why the failure was
        # invisible: the file on disk was correct and the exit code was not.
        print(
            f"    {section:13s} {counts['named']:5,}/{counts['total']:,} named"
            f"  {counts.get('renamed', 0):5,} changed"
            f"  {counts.get('fellBack', 0):5,} still from archive"
            f"  {counts.get('described', 0):5,} described"
        )
    print(
        f"  item legality:       {legality['illegal']:,} of {legality['total']:,}"
        f" are bLegalInGame=false; {legality['twin']} badged with a live twin"
        f" ({legality['noTwin']} have no legal namesake and are NOT badged,"
        f" {legality['ambiguous']} ambiguous)"
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
