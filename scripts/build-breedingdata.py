#!/usr/bin/env python3
"""
Merge the authoritative 1.0 breeding tables from refs/ into the bundled data.

    python3 scripts/build-breedingdata.py

WHY A MERGE AND NOT A REGENERATION
----------------------------------
The bundled table (from the MIT-licensed tylercamp/palcalc project) is a full
precomputed 44,850-pair result set that is known good. `refs/` ships the game's
own tables but NOT that full expansion — `parent_to_children_formula` covers
only 44 parents.

Reimplementing the combi-rank formula to fill the gap was tried and rejected:
against the known-good table the best reconstruction agreed only 77.5% of the
time, across every plausible tie-break. Shipping a formula that is wrong one
time in four would be worse than the stale data it replaces. So palcalc's table
stays as the base and `refs/` is layered on top, which is exactly the data we
can actually justify.

Three layers, lowest priority first:

1. **palcalc pairs** — 44,850 entries, unchanged.
2. **`parent_to_children_formula`** — full 303-partner coverage for the six Pals
   missing from palcalc entirely. Not a formula, just the game's own results.
3. **`unique_combos`** — 253 special pairs from the game's tables, applied last
   because a special combination beats any general result.

Run this only when a new PalWorldSaveTools release lands. The output is
committed, so `refs/` is not needed at runtime.
"""

from __future__ import annotations

import gzip
import json
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REFS_ZIP = os.path.join(ROOT, "refs", "PalWorldSaveTools-main.zip")
MEMBER = "palworldsavetools/resources/game_data/breedingdata.json"
DATA_DIR = os.path.join(ROOT, "backend", "data")
BREEDING_OUT = os.path.join(DATA_DIR, "pal_breeding.json.gz")
DB_OUT = os.path.join(DATA_DIR, "pal_db.json.gz")


def pair_key(a: str, b: str) -> str:
    """Must match breeding._pair_key exactly."""
    return "+".join(sorted([a, b]))


def load_gz(path: str) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def write_gz(path: str, payload: dict) -> int:
    tmp = path + ".tmp"
    # mtime=0 so rebuilding identical data produces an identical file and does
    # not show up as a spurious diff.
    with gzip.GzipFile(tmp, "wb", compresslevel=9, mtime=0) as f:
        f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    os.replace(tmp, path)
    return os.path.getsize(path)


def main() -> int:
    if not os.path.exists(REFS_ZIP):
        print(f"error: {REFS_ZIP} not found. This script needs refs/.", file=sys.stderr)
        return 1

    with zipfile.ZipFile(REFS_ZIP) as z:
        ref = json.loads(z.read(MEMBER))

    breeding = load_gz(BREEDING_OUT)
    db = load_gz(DB_OUT)

    pairs: dict[str, str] = dict(breeding["pairs"])
    pals: dict[str, dict] = dict(db["pals"])
    info = ref["pal_info"]

    base_pairs = len(pairs)
    base_pals = len(pals)

    # `refs/` capitalises inconsistently — `Blueplatypus` as a parent but
    # `BluePlatypus_Fire` as a child, against our `BluePlatypus`. Fold every
    # incoming name onto the spelling already in use, or the merged pairs are
    # keyed on a name no lookup will ever produce.
    canonical_by_lower = {name.lower(): name for name in pals}

    def canonical(name: str) -> str:
        return canonical_by_lower.get(name.lower(), name)

    # ─── Layer 2: full partner tables for Pals we do not have at all ───
    added_pairs = 0
    for parent, entries in ref["parent_to_children_formula"].items():
        for entry in entries:
            key = pair_key(canonical(parent), canonical(entry["partner"]))
            if key not in pairs:
                pairs[key] = canonical(entry["child"])
                added_pairs += 1

    # ─── Layer 3: special combinations always win ───
    #
    # ...except where the game's own table contradicts itself. `unique_combos`
    # lists CatMage + FoxMage twice, in the same parent order, with different
    # children (FoxMage_Dark and CatMage_Fire) — and `child_to_parents_unique`
    # names that pair as the unique parents of *both*. So the pair really can
    # produce either, and picking one by list position would be inventing a
    # certainty the data does not have.
    #
    # Ambiguous pairs are therefore left at whatever the base table says, and
    # reported. `pairs` maps one key to one child, so representing "either of
    # two" needs a schema change, not a build-script tweak.
    by_key: dict[str, set[str]] = {}
    for combo in ref["unique_combos"]:
        key = pair_key(canonical(combo["parent_a"]), canonical(combo["parent_b"]))
        by_key.setdefault(key, set()).add(canonical(combo["child"]))

    corrected = 0
    added_specials = 0
    corrections: list[tuple[str, str, str]] = []
    ambiguous: list[tuple[str, list[str]]] = []

    for key, children in by_key.items():
        if len(children) > 1:
            ambiguous.append((key, sorted(children)))
            continue
        child = next(iter(children))
        if key not in pairs:
            pairs[key] = child
            added_specials += 1
        elif pairs[key] != child:
            corrections.append((key, pairs[key], child))
            pairs[key] = child
            corrected += 1

    # ─── Pal records for anything new ───
    #
    # Enriched from the bundled game database rather than from `pal_info`, which
    # carries "Unidentified Pal" for unreleased content. `zukanIndex` is the
    # Paldeck number and the authoritative released/unreleased signal: -1 means
    # the Pal is in the game files but not in the Paldeck. Five of the seven
    # additions here are -1, so they are marked rather than presented as things
    # a player could go and catch.
    #
    # The existing spelling always wins. `refs/` says `Blueplatypus` where the
    # save and our table say `BluePlatypus` — the same Pal — and adding both
    # would put a duplicate Fuack in every dropdown.
    sys.path.insert(0, os.path.join(ROOT, "backend"))
    import gamedata

    existing_lower = {name.lower(): name for name in pals}
    new_pals: list[str] = []
    aliases: list[tuple[str, str]] = []

    for name in info:
        if name in pals:
            continue
        if name.lower() in existing_lower:
            aliases.append((name, existing_lower[name.lower()]))
            continue

        entry = gamedata.pal(name) or {}
        zukan = entry.get("zukanIndex")
        released = isinstance(zukan, int) and zukan >= 0

        record = {
            "name": entry.get("name") or info[name].get("name") or name,
            "rarity": entry.get("rarity", info[name].get("rarity")),
            "variant": "_" in name,
            "source": "palworldsavetools",
        }
        if released:
            record["dex"] = zukan
        else:
            # Kept because the game's own breeding tables reference it, so the
            # pair data is correct if it is ever released — but flagged so the
            # planner does not offer it as something to breed today.
            record["unreleased"] = True
        pals[name] = record
        new_pals.append(name)

    for alias, existing in aliases:
        print(f"note: refs spells {existing!r} as {alias!r}; keeping the existing spelling")

    referenced = {p for key in pairs for p in key.split("+")} | set(pairs.values())
    unknown = sorted(p for p in referenced if p not in pals)

    breeding["pairs"] = pairs
    breeding["pals"] = sorted(pals)
    breeding["version"] = "palcalc/main + palworldsavetools/game_data"
    db["pals"] = pals
    # Idempotent: re-running must not append a second "+pst".
    base_version = str(db.get("version", "?")).split("+pst")[0]
    db["version"] = f"{base_version}+pst"

    breeding_size = write_gz(BREEDING_OUT, breeding)
    db_size = write_gz(DB_OUT, db)

    print(f"pairs: {base_pairs:,} -> {len(pairs):,}")
    print(f"  added from parent_to_children_formula : {added_pairs:,}")
    print(f"  added from unique_combos              : {added_specials}")
    print(f"  corrected by unique_combos            : {corrected}")
    for key, was, now in corrections:
        print(f"      {key}: {was} -> {now}")
    if ambiguous:
        print(f"  left alone, the game's table gives two answers  : {len(ambiguous)}")
        for key, children in ambiguous:
            print(f"      {key}: either {' or '.join(children)} — keeping {pairs.get(key, '(absent)')}")
    print(f"pals: {base_pals} -> {len(pals)}  (+{len(new_pals)})")
    for name in sorted(new_pals):
        record = pals[name]
        tag = "unreleased" if record.get("unreleased") else f"Paldeck #{record.get('dex')}"
        print(f"      + {name} ({record['name']}) — {tag}")
    if unknown:
        print(f"warning: {len(unknown)} names appear in pairs but have no Pal record:")
        print(f"      {', '.join(unknown[:12])}")
    print(f"wrote {BREEDING_OUT} ({breeding_size:,} B)")
    print(f"wrote {DB_OUT} ({db_size:,} B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
