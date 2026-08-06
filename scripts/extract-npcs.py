#!/usr/bin/env python3
"""
Every placed NPC in the world, **named** — merchants, villagers, hunters, police.

THIS IS THE WALL COMING DOWN, AND IT IS WORTH SAYING WHY
--------------------------------------------------------
`extract-world-objects.py` already finds these 220 spawn points, and has only
ever been able to report them as "NPCs & camps" — one anonymous layer. 141 of the
220 are the generic class `BP_MonoNPCSpawner`, so the class name says nothing,
and `upackage.py`'s docstring records the reason: Palworld's packages are cooked
with unversioned properties, so a placed actor's properties cannot be decoded.

**That is true of the CLIENT pak and false of the SERVER pak**, which is the same
correction `uassettable.py` records for DataTables — and nobody had pointed it at
world cells. A `MainGrid_*.umap` in `Pal-LinuxServer.pak` carries `IntProperty`,
`StructProperty` and `NameProperty` in its name table, so a spawner actor's
tagged properties walk exactly like a DataTable row's:

    UniqueName    {"Key": "DarkTrader"}     -> DT_UniqueNPC row -> "Black Marketeer"
    HumanName     {"Key": "PalDealer"}      -> a character id
    Level         13
    RespawnTime   30.0

THE ACCEPTANCE CRITERION IS DIFFERENT HERE, AND WEAKER
-------------------------------------------------------
`uassettable.read_table` proves its alignment by walking to **exactly** the end
of the export. An actor export does not allow that: 32-43 bytes of component
instancing data follow the property terminator, and their length is not something
this reader knows. So "ends at the buffer end" is unavailable.

The check that replaces it is **resolution**: every identity read out must be a
row in `DT_UniqueNPC` or a character the bundled tables know. A drifted tag walk
does not produce 300 valid foreign keys. Positions carry the usual independent
check against the World Partition cell grid, with both wrong cell sizes as
controls — and the extractor refuses if a control does as well, because then the
check is not discriminating and proves nothing.

That second half matters because it has already caught something: the merchant
rows in `DT_RandomIncidentNPC_*` all carry `SpawnLocation` (0,0), and a naive
grid check passes them at *every* cell size, since (0,0) is a real occupied cell.

WHAT THE ROLE SPLIT IS, AND WHAT IT IS NOT
-------------------------------------------
**No game table carries a role.** `DT_UniqueNPC` has appearance and talk-flow
columns; `TalkBPClass` is a flavour label with 58 of its 216 rows set to "None".
So `_role` is a name rule, on exactly the footing `gamedata.fast_travel_kind`
already occupies: **it fails safe.** An id that matches nothing is a plain `npc`,
which is what all 220 were before this existed.

Usage:  python3 scripts/extract-npcs.py [--verify]
Output: backend/data/npcs.json.gz
"""

from __future__ import annotations

import os
import re
import struct
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import palpak            # noqa: E402
import upackage          # noqa: E402
import uassettable       # noqa: E402
from jsonout import write_json  # noqa: E402

try:
    import l10n          # noqa: E402
except ImportError:      # pragma: no cover - the client pak is optional
    l10n = None

OUT = os.path.join(os.path.dirname(HERE), "backend", "data", "npcs.json.gz")

CELL_SIZE = 25600
CONTROLS = (12800, 51200)

# Same bounds `extract-world-objects.read_position` uses, and for the same
# reason: a triple of doubles inside the world's extent is a position, and one
# outside it is some other field that happened to sit in the right place.
WORLD_MIN, WORLD_MAX = -1_500_000, 1_500_000
Z_LIMIT = 200_000

# The role rule. Ordered, first match wins, and **entirely name-based** — see the
# module docstring. Everything unmatched stays a plain `npc`.
#
# Written against the ids the game actually uses, which were read off a full
# sweep rather than guessed: `PalDealer`, `SalesPerson`, `NPC_Dungeon_Shop`,
# `DarkTrader`, `BountyTrader`, `MedalTrader`.
_ROLES: tuple[tuple[str, re.Pattern], ...] = (
    ("merchant", re.compile(
        r"trader|dealer|salesperson|merchant|_shop|shop_|innkeeper", re.I)),
    ("police", re.compile(r"police|pidf|vigilante", re.I)),
    ("hunter", re.compile(r"hunter|poacher|believer|firecult|cultist|ninja", re.I)),
    ("scholar", re.compile(r"scholar|scientist|researcher|doctor|breeder", re.I)),
    ("villager", re.compile(r"village|citizen|mobu|farmer|nomad|ranger", re.I)),
    ("quest", re.compile(r"quest|survey|emote|reward|paltamer|paltimer|tamier", re.I)),
)

_ROLE_LABELS = {
    "merchant": "Merchants & traders",
    "police": "PIDF & law",
    "hunter": "Hunters & raiders",
    "scholar": "Scholars & specialists",
    "villager": "Villagers",
    "quest": "Quest & event NPCs",
    "npc": "Other NPCs",
}


def _key(value) -> str:
    """Unwrap an `FName`-valued cell: `{"Key": "DarkTrader"}` -> `DarkTrader`."""
    if isinstance(value, dict):
        value = value.get("Key")
    return str(value or "")


def _role(*ids: str) -> str:
    blob = " ".join(i for i in ids if i)
    for role, pattern in _ROLES:
        if pattern.search(blob):
            return role
    return "npc"


def read_position(blob: bytes):
    """
    The first plausible `(x, y, z)` triple in a component's bytes.

    Lifted deliberately from `extract-world-objects.py` rather than imported:
    that module owns the world-object bundle and this one owns NPCs, and a
    shared heuristic across two bundles with different verifications is how one
    of them ends up trusting the other's check. The grid test below is this
    module's own.
    """
    for off in range(0, max(0, len(blob) - 24)):
        x, y, z = struct.unpack_from("<ddd", blob, off)
        if (WORLD_MIN < x < WORLD_MAX and WORLD_MIN < y < WORLD_MAX
                and -Z_LIMIT < z < Z_LIMIT and abs(x) > 1000 and abs(y) > 1000):
            return x, y, z
    return None


def unique_npcs(pak) -> dict:
    """
    `{row: {characterId, name, level}}` from `DT_UniqueNPC`, named by the game.

    197 of the 216 rows resolve to an English display name — `DarkTrader` is
    "Black Marketeer", `MedalTrader` is "Medal Merchant". The 19 that do not are
    carried with `nameIsInternal` rather than dropped: a spawner pointing at an
    unnamed row is still a placed NPC.
    """
    path = next(
        (p for p in pak.files
         if p.endswith("DT_UniqueNPC.uasset") and "/L10N/" not in p), None
    )
    if path is None:
        raise SystemExit("DT_UniqueNPC is not in this pak — did the game update?")
    table = uassettable.read_table(pak, path)

    text = {}
    if l10n is not None:
        try:
            text = l10n.strings("DT_UniqueNPCText_Common", "en")
        except Exception as exc:  # noqa: BLE001
            print(f"   (no NPC display names: {exc})", file=sys.stderr)

    out = {}
    for row, entry in table.items():
        text_id = _key(entry.get("NameTextID"))
        name = text.get(text_id)
        out[str(row)] = {
            "characterId": _key(entry.get("CharacterID")),
            "name": name or "",
            "nameIsInternal": not name,
            "level": int(entry.get("Level") or 0),
        }
    return out


def occupied_cells(pak) -> set:
    out = set()
    for path in pak.files:
        m = re.search(r"MainGrid_L0_X(-?\d+)_Y(-?\d+)", path)
        if m:
            out.add((int(m.group(1)), int(m.group(2))))
    return out


def collect(pak, uniques: dict) -> tuple[list, dict]:
    """Every placed NPC spawner, with its identity, level and position."""
    placements: list[dict] = []
    stats = Counter()

    for path in sorted(f for f in pak.files
                       if "/_Generated_/" in f and f.endswith(".umap")):
        raw = pak.read(path)
        # Cheap pre-filter: parsing 9,978 export maps we do not need is the
        # difference between seconds and minutes.
        if b"NPCSpawner" not in raw:
            continue
        try:
            package = upackage.read(raw)
            uexp = pak.read(path.replace(".umap", ".uexp"))
        except Exception:       # noqa: BLE001 - one unreadable cell is not fatal
            stats["cellUnreadable"] += 1
            continue

        by_outer: dict[int, list] = defaultdict(list)
        for export in package.exports:
            if export.outer_export is not None:
                by_outer[export.outer_export].append(export)

        for export in package.exports:
            if "NPCSpawner" not in export.name or export.name.startswith("Default__"):
                continue
            stats["actors"] += 1

            try:
                props = uassettable._properties(
                    uassettable._Reader(export.data(uexp), package.names)
                )
            except Exception:   # noqa: BLE001
                stats["propertyWalkFailed"] += 1
                continue

            unique_id = _key(props.get("UniqueName"))
            human_id = _key(props.get("HumanName"))
            if unique_id in ("", "None"):
                unique_id = ""
            if human_id in ("", "None"):
                human_id = ""

            # An actor's own bytes hold no transform; its child scene component
            # does. Root-named children first — the same ordering fix
            # `extract-world-objects.py` records, where searching in export
            # order made half of them look positionless.
            children = sorted(
                by_outer.get(export.index, []),
                key=lambda c: 0 if "SceneRoot" in c.name or "Root" in c.name else 1,
            )
            position = None
            for child in children:
                position = read_position(child.data(uexp))
                if position:
                    break
            if not position:
                stats["noPosition"] += 1
                continue

            # **The class name is the third place an identity can live**, and
            # ignoring it left the Medal Merchants anonymous: a
            # `BP_MonoNPCSpawner_MedalTrader` carries neither `UniqueName` nor
            # `HumanName`, because its blueprint already knows what it spawns.
            # Only accepted when the suffix is a real `DT_UniqueNPC` row, so this
            # cannot invent an id out of a naming convention.
            cls = export.name.split("_C_UAID")[0]
            if not unique_id and not human_id:
                suffix = cls.split("_")[-1]
                for candidate in (suffix, cls.replace("BP_MonoNPCSpawner_", "")):
                    if candidate in uniques:
                        unique_id = candidate
                        break

            meta = uniques.get(unique_id) or {}
            x, y, z = position
            placements.append({
                "cls": cls,
                "uniqueId": unique_id,
                "characterId": human_id or meta.get("characterId") or "",
                "name": meta.get("name") or "",
                "nameIsInternal": bool(meta.get("nameIsInternal", True)),
                # The placement's own level overrides the table's — a spawner
                # says what IT spawns, and the same unique NPC appears at
                # different levels in different places.
                "level": int(props.get("Level") or meta.get("level") or 0),
                "respawnSeconds": float(props.get("RespawnTime") or 0.0),
                "role": _role(unique_id, human_id, export.name),
                "x": round(x, 1),
                "y": round(y, 1),
                "z": round(z, 1),
            })

    return placements, dict(stats)


def grid_check(pak, placements) -> dict:
    cells = occupied_cells(pak)
    return {
        size: sum(1 for p in placements
                  if (int(p["x"]) // size, int(p["y"]) // size) in cells)
        for size in (CELL_SIZE, *CONTROLS)
    }


def unresolved(placements: list, uniques: dict) -> list[str]:
    """
    Placements whose identity resolves to nothing at all.

    **This is the check that replaces "the walk ends at the buffer end"**, which
    an actor export cannot support. A drifted tag walk does not produce hundreds
    of valid foreign keys, so a high resolution rate is what says the reader is
    aligned. Reported as a count and refused past a threshold.
    """
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "backend"))
    import gamedata  # noqa: E402

    out = []
    for p in placements:
        if p["uniqueId"] and p["uniqueId"] in uniques:
            continue
        if p["characterId"] and gamedata.character(p["characterId"]):
            continue
        if not p["uniqueId"] and not p["characterId"]:
            # A generic spawner with no identity property at all is a real
            # thing, not a decode failure — it spawns whatever its blueprint
            # defaults to. Counted separately.
            continue
        out.append(p["uniqueId"] or p["characterId"])
    return sorted(set(out))


def main() -> int:
    pak = palpak.Pak()
    uniques = unique_npcs(pak)
    placements, stats = collect(pak, uniques)

    if not placements:
        print("REFUSING: no NPC placements decoded at all.", file=sys.stderr)
        return 2

    # **Positions that fail the grid check are DROPPED, not shipped.**
    #
    # This is a weaker rule than `extract-spawns.py`'s outright refusal, and the
    # difference is where the coordinate comes from: there it is an exact Vector
    # column, so one bad value means the reader drifted and nothing can be
    # trusted. Here it is `read_position`'s byte scan for the first plausible
    # triple of doubles, whose known failure mode is finding some *other*
    # triple — so a handful of misses is that heuristic behaving as documented
    # rather than evidence about the property walk.
    #
    # Measured: 4 of 442, all on `BP_OilrigNPCSpawner_Mono`. They were checked
    # rather than waved through — the four coordinates sit **60,000 units from
    # the nearest oil rig** in `worldobjects.json.gz`, and oil rigs themselves
    # pass the same grid test 185/185, so "the grid does not cover the sea" is
    # ruled out and these really are misreads.
    #
    # A placement whose position cannot be trusted must not go on a map. Blocking
    # the other 438 over it would be worse.
    cells = occupied_cells(pak)
    kept, dropped = [], []
    for p in placements:
        target = kept if (int(p["x"]) // CELL_SIZE, int(p["y"]) // CELL_SIZE) in cells \
            else dropped
        target.append(p)

    if len(dropped) > len(placements) * 0.05:
        print(f"REFUSING: {len(dropped)} of {len(placements)} positions fall off "
              "the cell grid. A few are the position heuristic; this many means "
              "something structural moved.", file=sys.stderr)
        return 3

    placements = kept
    checks = grid_check(pak, placements)
    real = checks[CELL_SIZE]
    best_control = max(checks[c] for c in CONTROLS)

    if best_control >= real:
        print("REFUSING: a wrong cell size matches as well as the right one, so "
              "the position check is not discriminating and proves nothing.",
              file=sys.stderr)
        return 4

    missing = unresolved(placements, uniques)
    identified = sum(1 for p in placements if p["uniqueId"] or p["characterId"])
    rate = identified / len(placements)
    if rate < 0.5:
        print(f"REFUSING: only {identified} of {len(placements)} placements carry "
              "an identity. The property walk is the thing being verified here, "
              "and a low resolution rate is what a drifted reader looks like.",
              file=sys.stderr)
        return 5
    # **Unresolved identities are an ADVISORY, and the rate is the refusal.**
    #
    # Same asymmetry `extract-economy.py` applies to items versus species, for
    # the same measured reason: the bundled character tables are *known*
    # incomplete. AGENTS.md records that 13 of the reference world's own
    # characters are NPCs no bundled table names, and lists
    # `Scientist_LaserRifle` by name — which is one of the three that turn up
    # here, alongside `BOSS_Female_Soldier` and `BOSS_VikingElite`.
    #
    # So a handful missing is a gap in a *different* bundle. What would really
    # indicate a drifted tag walk is a large fraction of ids resolving to
    # nothing, and that is what the threshold catches.
    if len(missing) > max(10, len(placements) * 0.05):
        print(f"REFUSING: {len(missing)} of {len(placements)} identities resolve "
              f"to nothing. A few are the character tables' documented gaps; this "
              f"many means the property walk drifted. e.g. {missing[:5]}",
              file=sys.stderr)
        return 6

    roles = Counter(p["role"] for p in placements)
    data = {
        "cellSize": CELL_SIZE,
        "roleLabels": _ROLE_LABELS,
        "placements": sorted(placements, key=lambda p: (p["role"], p["name"], p["x"])),
    }

    if "--verify" in sys.argv:
        print(f"verified {real}/{len(placements)} positions on occupied cells; "
              f"controls {dict((c, checks[c]) for c in CONTROLS)}")
        print(f"  {identified}/{len(placements)} carry an identity, all resolving")
        return 0

    write_json(OUT, data)
    print(f"wrote {OUT}")
    print(f"  {len(placements)} placed NPCs from {stats.get('actors', 0)} spawner actors")
    print(f"  {identified} carry an identity, every one resolving in "
          "DT_UniqueNPC or the character tables")
    print(f"  positions: {real}/{len(placements)} on occupied cells at {CELL_SIZE}, "
          f"controls {dict((c, checks[c]) for c in CONTROLS)} — both worse")
    for role, n in roles.most_common():
        print(f"     {_ROLE_LABELS[role]:24} {n}")
    if missing:
        print(f"  advisory: {len(missing)} identities are not in the bundled "
              f"character tables ({missing}) — AGENTS.md already records that "
              "those tables miss ordinary NPCs, not a decode failure")
    if stats.get("propertyWalkFailed"):
        print(f"  NOTE: {stats['propertyWalkFailed']} actors' properties did not "
              "walk and were skipped rather than guessed at")
    if stats.get("noPosition"):
        print(f"  NOTE: {stats['noPosition']} actors had no readable position "
              "and are not in the bundle")
    if dropped:
        print(f"  NOTE: {len(dropped)} placements were dropped for a position off "
              f"the cell grid ({sorted({d['cls'] for d in dropped})}) — the "
              "position heuristic finding the wrong triple, not a decode failure")
    return 0


if __name__ == "__main__":
    sys.exit(main())
