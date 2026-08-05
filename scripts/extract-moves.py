#!/usr/bin/env python3
"""
Moves, what learns them, what inherits them through an egg, and the game's own
unique breeding combinations.

Phase 1.5 of `docs/PLAN.md`. Four tables:

    DT_WazaDataTable      384  element, category, power, cooldown, range
    DT_WazaMasterLevel  5,772  which species learns which move at which level
    DT_WazaMasterTamago 7,111  which moves a species can inherit via an egg
    DT_PalCombiUnique     258  the special parent pairs

WHY THIS MATTERS BEYOND COMPLETENESS. The dashboard shows `activeSkills` as bare
names — no element, no power, no indication of where a move came from. And the
breeding planner answers "what species do I get", which is the easy half; people
breed past the species chart for the *moves*, and `WazaMasterTamago` is the table
that answers it.

THE PREFIX RULE, already documented for `EquipWaza` and applying identically
here: the game writes `EPalWazaID::PowerBall` and the bundled `activeSkills`
table speaks `PowerBall`. The API speaks bare ids everywhere — parser, editor,
validation — so the prefix is stripped once, at this boundary, exactly as
`charedit` re-attaches it only on write.

TRIBES ARE NOT SPECIES, and `DT_PalCombiUnique` is keyed on tribes.
`EPalTribeID::LazyDragon` has to be resolved through
`DT_PalMonsterParameter.Tribe` before a row means anything about two Pals in a
palbox. A tribe covers every variant of a species, which is why the table needs
only 258 rows to describe every special pairing.

GENDER IS LOAD-BEARING ON EXACTLY ONE PAIR, and that pair is the only thing the
palcalc comparison disagrees about. `ParentGenderA/B` is `EPalGenderType::None`
— "either" — on 256 of 258 rows. The other two are:

    Male CatMage   x Female FoxMage  ->  FoxMage_Dark
    Female CatMage x Male   FoxMage  ->  CatMage_Fire

So one tribe pair yields **two different children depending on which parent is
which gender**, and palcalc's table (keyed on an unordered pair) can only hold
one of them — it reports `FoxMage_Dark` and loses `CatMage_Fire` entirely.

Reading `None` as a literal gender requirement would make the other 256
impossible; ignoring the two that are set loses a real outcome. Both halves
matter, which is why `genderA`/`genderB` travel per row.

THE PALCALC COMPARISON runs here and is **reported, not acted on**. The breeding
planner currently rests on palcalc's 46,655-pair table with nothing checking it,
and this is the first independent source. Disagreements are printed for #64 to
decide; this script does not change what the planner uses.

VERIFICATION, asymmetric for the same reason `extract-economy.py`'s is. Every
`WazaID` must resolve in `DT_WazaDataTable` after prefix stripping, or the write
is refused — that table is the complete list of what a move can be. Unknown
*species* are an advisory: 29 are absent and all are real (`_BossRush` arena
variants, `_otomo` partner forms), matching the incompleteness `palcheck`
already documents.

Usage:  python3 scripts/extract-moves.py [--verify]
Output: backend/data/moves.json.gz
"""

from __future__ import annotations

import gzip
import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import palpak            # noqa: E402
import uassettable       # noqa: E402
from jsonout import write_json  # noqa: E402

ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "backend", "data", "moves.json.gz")
PALCALC = os.path.join(ROOT, "backend", "data", "pal_breeding.json.gz")

UNSET = {"", "None", None}


def _enum(value) -> str:
    text = str(value or "")
    return text.rsplit("::", 1)[-1] if "::" in text else text


def _read(pak, name: str) -> dict:
    path = next((p for p in pak.files if p.endswith(name + ".uasset")), None)
    if path is None:
        raise SystemExit(f"{name} is not in this pak — did the game update?")
    return uassettable.read_table(pak, path)


def _moves(pak) -> dict:
    out = {}
    for row in _read(pak, "DT_WazaDataTable").values():
        waza = _enum(row.get("WazaType"))
        if waza in UNSET:
            continue
        out[waza] = {
            "id": waza,
            "element": _enum(row.get("Element")),
            "category": _enum(row.get("Category")),
            # `DisplayPower` is what the game shows a player; `Power` is the
            # internal figure. Both travel because they differ and the UI wants
            # the first while anything computing wants the second.
            "power": int(row.get("Power") or 0),
            "displayPower": int(row.get("DisplayPower") or 0),
            "cooldown": float(row.get("CoolTime") or 0.0),
            "rangeMin": int(row.get("MinRange") or 0),
            "rangeMax": int(row.get("MaxRange") or 0),
        }
    return out


def _learned(pak) -> dict:
    out: dict[str, list] = defaultdict(list)
    for row in _read(pak, "DT_WazaMasterLevel").values():
        species = str(row.get("PalID") or "")
        waza = _enum(row.get("WazaID"))
        if species in UNSET or waza in UNSET:
            continue
        out[species].append({"moveId": waza, "level": int(row.get("Level") or 0)})
    for rows in out.values():
        rows.sort(key=lambda r: (r["level"], r["moveId"]))
    return dict(out)


def _egg_moves(pak) -> dict:
    out: dict[str, list] = defaultdict(list)
    for row in _read(pak, "DT_WazaMasterTamago").values():
        species = str(row.get("PalID") or "")
        waza = _enum(row.get("WazaID"))
        if species in UNSET or waza in UNSET:
            continue
        out[species].append(waza)
    return {k: sorted(set(v)) for k, v in out.items()}


def _tribe_map(pak) -> dict:
    """
    `EPalTribeID::LazyDragon` -> the species ids in that tribe.

    A tribe covers every variant, which is why 258 rows describe every special
    pairing. Built from `DT_PalMonsterParameter`, not guessed from the id: a
    tribe name usually equals the base species id and *usually* is not a rule.
    """
    out: dict[str, list] = defaultdict(list)
    for key, row in _read(pak, "DT_PalMonsterParameter").items():
        tribe = _enum(row.get("Tribe"))
        if tribe not in UNSET:
            out[tribe].append(str(key))
    return dict(out)


def _combos(pak, tribes: dict) -> list:
    out = []
    for row in _read(pak, "DT_PalCombiUnique").values():
        a, b = _enum(row.get("ParentTribeA")), _enum(row.get("ParentTribeB"))
        child = str(row.get("ChildCharacterID") or "")
        if a in UNSET or b in UNSET or child in UNSET:
            continue
        out.append({
            "parentTribeA": a,
            "parentTribeB": b,
            # Resolved for callers that speak species ids, which is all of them.
            "parentSpeciesA": tribes.get(a, []),
            "parentSpeciesB": tribes.get(b, []),
            "childId": child,
            # `EPalGenderType::None` means "either", not "genderless". Reading it
            # literally would make most of these impossible.
            "genderA": _enum(row.get("ParentGenderA")),
            "genderB": _enum(row.get("ParentGenderB")),
        })
    return out


def compare_with_palcalc(combos: list) -> dict:
    """
    Does the third-party table the breeding planner runs on agree with the game?

    **Reported, never applied.** The planner uses palcalc's 46,655 pairs and
    nothing has ever checked them. This is the first independent source, so the
    first useful output is the size and shape of the disagreement — see #64.
    """
    try:
        with gzip.open(PALCALC, "rt", encoding="utf-8") as f:
            pairs = json.load(f).get("pairs") or {}
    except (OSError, json.JSONDecodeError):
        return {"available": False}

    agree, disagree, missing, gendered = 0, [], [], []
    for combo in combos:
        # A gendered row cannot be compared against a table keyed on an
        # unordered pair: palcalc physically cannot represent two outcomes for
        # one pair, so counting it as a disagreement blames the wrong thing.
        is_gendered = "None" not in (combo["genderA"], combo["genderB"])
        for a in combo["parentSpeciesA"]:
            for b in combo["parentSpeciesB"]:
                key = f"{a}+{b}" if f"{a}+{b}" in pairs else f"{b}+{a}"
                if is_gendered:
                    gendered.append((a, combo["genderA"], b, combo["genderB"],
                                     combo["childId"], pairs.get(key)))
                elif key not in pairs:
                    # Almost all of these are BOSS_/PREDATOR_ variants, which
                    # palcalc does not carry at all — it holds 305 base species.
                    missing.append((a, b, combo["childId"]))
                elif pairs[key] == combo["childId"]:
                    agree += 1
                else:
                    disagree.append((a, b, combo["childId"], pairs[key]))
    return {
        "available": True,
        "agree": agree,
        "disagree": disagree,
        "missing": missing,
        "gendered": gendered,
    }


def build(pak=None) -> tuple[dict, dict]:
    pak = pak or palpak.Pak()
    tribes = _tribe_map(pak)
    data = {
        "moves": _moves(pak),
        "learned": _learned(pak),
        "eggMoves": _egg_moves(pak),
        "uniqueCombos": _combos(pak, tribes),
    }
    return data, {"tribes": len(tribes)}


def verify(data: dict) -> list[str]:
    sys.path.insert(0, os.path.join(ROOT, "backend"))
    import gamedata  # noqa: E402

    problems = []
    known_moves = set(data["moves"])

    for label, table in (("learned", data["learned"]), ("eggMoves", data["eggMoves"])):
        ids = (
            {e["moveId"] for rows in table.values() for e in rows}
            if label == "learned"
            else {m for rows in table.values() for m in rows}
        )
        unknown = sorted(ids - known_moves)
        if unknown:
            problems.append(
                f"{label}: {len(unknown)} moves not in DT_WazaDataTable, "
                f"e.g. {unknown[:5]}"
            )

    unresolved = [
        c for c in data["uniqueCombos"]
        if not c["parentSpeciesA"] or not c["parentSpeciesB"]
    ]
    if unresolved:
        problems.append(
            f"{len(unresolved)} unique combinations have a tribe that resolves "
            f"to no species, e.g. {unresolved[0]['parentTribeA']}"
        )
    return problems


def unknown_species(data: dict) -> list[str]:
    """
    Species with a move list that the bundled character tables do not name.

    **Advisory, exactly as in `extract-economy.py`** and for the same measured
    reason: these are `_BossRush` arena variants and `_otomo` partner forms, all
    real, and the character tables are *known* incomplete — AGENTS.md records
    that even the reference world holds NPCs no bundled table names.

    Move ids stay a hard refusal. `DT_WazaDataTable` is the complete list of what
    a move can be, so an id missing there means the projection drifted.
    """
    sys.path.insert(0, os.path.join(ROOT, "backend"))
    import gamedata  # noqa: E402

    species = set(data["learned"]) | set(data["eggMoves"])
    return sorted(s for s in species if not gamedata.character(s))


def main() -> int:
    pak = palpak.Pak()
    data, stats = build(pak)

    problems = verify(data)
    if problems:
        for line in problems:
            print(f"REFUSING: {line}", file=sys.stderr)
        return 2

    comparison = compare_with_palcalc(data["uniqueCombos"])
    unknown = unknown_species(data)

    if "--verify" in sys.argv:
        print(f"verified {len(data['moves'])} moves; every learned and egg move "
              "resolves, every species is known, every tribe resolves")
        return 0

    write_json(OUT, data)
    print(f"wrote {OUT}")
    print(f"  {len(data['moves'])} moves")
    print(f"  {len(data['learned'])} species with a level-up move list")
    print(f"  {len(data['eggMoves'])} species with inheritable egg moves")
    print(f"  {len(data['uniqueCombos'])} unique breeding combinations "
          f"across {stats['tribes']} tribes")

    if unknown:
        print(f"  advisory: {len(unknown)} species are not in the bundled "
              f"character tables (e.g. {unknown[:3]}) — boss-rush and partner "
              "variants, not a drift")

    if comparison.get("available"):
        print("\n  palcalc comparison (REPORTED ONLY — see #64):")
        print(f"    agree    {comparison['agree']}")
        print(f"    disagree {len(comparison['disagree'])}")
        print(f"    missing  {len(comparison['missing'])}")
        for a, b, game, theirs in comparison["disagree"][:8]:
            print(f"      {a} + {b}: game says {game}, palcalc says {theirs}")
        if comparison["missing"]:
            print(f"    (missing are BOSS_/PREDATOR_ variants palcalc does not "
                  f"carry — it holds 305 base species)")
        if comparison["gendered"]:
            print(f"\n    gender-dependent outcomes palcalc CANNOT represent "
                  f"({len(comparison['gendered'])}):")
            for a, ga, b, gb, game, theirs in comparison["gendered"][:6]:
                print(f"      {ga} {a} + {gb} {b} -> {game}  "
                      f"(palcalc has one answer for this pair: {theirs})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
