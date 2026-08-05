"""
Base supply advisor: what each base actually holds, and what is conspicuously
missing.

WHAT THIS REPORTS, AND WHAT IT DELIBERATELY DOES NOT
----------------------------------------------------
`DT_MapObjectMasterDataTable` decodes out of the server pak — 1,034 rows — and it
confirms the *structural* separation this feature rests on. Feed Box, Cold Food
Box, Guild Chest, Breeding Farm and Medicine Rack are five distinct build
objects, all flagged `bBelongToBaseCamp`.

**But its columns are HP, Defense, MaterialType, DeteriorationDamage and
ExtinguishBurnWorkAmount — structure and combat.** Nothing in it says what a
container accepts, or what a structure pulls from. So "Pal food must be in a Feed
Box" and "the Breeding Farm consumes Cake" are, as far as any game file this
project can read is concerned, **unverified** — however obviously true they are
in play.

This module therefore reports **facts, not mechanics**:

    "this base has a Feed Box and it is empty"        <- a fact, and self-evidently worth knowing
    "this base has a Breeding Farm and no cake in it" <- a fact
    "move your food out of the guild chest"           <- a mechanic claim, NOT made

The first framing survives being wrong about the second, which is the whole
reason for it. If someone wants the mechanic settled, the place to look is the
build objects' own class defaults via the CDO technique that decoded
`BP_PalGameSetting` — an accepted-item filter is plausibly a UPROPERTY there.
Nobody has pointed it at them.

THE GUILD CHEST IS NOT A BASE CONTAINER
---------------------------------------
This is what the original "optimise the guild chest per base" framing got wrong,
and the save is unambiguous about it. Every other storage structure hangs an
`ItemContainer` module off its placed object; `GuildChest` hangs only
`GuildSecurity`. Its contents live in `GuildExtraSaveDataMap`, **one 54-slot
container shared by the entire guild** — eight placed chests on the reference
world, five guilds, five containers. Two chests in one guild are two doors into
one box, so "stock every base's guild chest" is not a thing that can be done.

It is reported at guild level for that reason, beside the per-base figures rather
than inside them.

READ-ONLY, AND THAT IS NOT A PHASE
----------------------------------
Nothing here writes. A cross-container *mover* would break `saveimport`'s
guarantee by definition — "the target matches the plan while every other
container is unchanged" is false the moment a second container is the source —
and needs its own invariant (the total of each item across source and destination
unchanged, verified after re-reading from disk). That is a separate piece of work
with a separate safety argument, not an extension of this one.
"""

from __future__ import annotations

from typing import Any, Optional

import gamedata

# ─── What counts as a staple ─────────────────────────────

# The default floor-stock list.
#
# **This is an operator judgement, not game data**, and it is labelled as such
# everywhere it surfaces. There is no table in the game that ranks materials by
# how often a base needs them; what there is, and what this uses, is the item
# catalogue's own ids and names. The rule this project holds is "do not
# hand-write game data *that already exists*" — a curated shortlist of what a
# base should keep in reserve does not exist anywhere, so the obligation here is
# provenance and configurability rather than abstinence.
#
# Callers override it entirely with `materials=`.
#
# **Written as ids, never as display names**, and the catalogue is why: the item
# a player calls "Ore" is `CopperOre`, "Ingot" is `CopperIngot`, "Refined Ingot"
# is `IronIngot` and "Paldium Fragment" is `Pal_crystal_S`. A list keyed on what
# the UI shows would silently miss four of the most basic materials in the game.
DEFAULT_STAPLES: tuple[str, ...] = (
    "Wood",           # Wood
    "Stone",          # Stone
    "Fiber",          # Fiber
    "Pal_crystal_S",  # Paldium Fragment
    "CopperOre",      # Ore
    "CopperIngot",    # Ingot
    "Coal",           # Coal
    "Cloth",          # Cloth
    "Leather",        # Leather
    "Bone",           # Bone
)

# Every cake in the catalogue, found by id prefix rather than listed, so a
# content update that adds one is covered. `Pancake` is deliberately excluded —
# it is a cooked dish that happens to be spelled with "cake" in it, which is
# exactly the trap a substring match falls into.
_CAKE_PREFIX = "Cake"

# The floor a base is measured against, in items.
#
# **The game's own `maxStack` is 9999 for every one of these**, so "at least one
# stack per base" — the shape the request was made in — resolves to 110,000 Wood
# across an eleven-base world. That is not what anyone means by a reserve, so the
# floor is an operator number with a modest default rather than the game's stack
# ceiling dressed up as a rule. `stackSize` travels in the payload beside it so
# the difference is visible rather than buried here.
DEFAULT_FLOOR = 500


def cake_ids() -> list[str]:
    """Every cake item id the bundled catalogue knows about."""
    return sorted(
        entry["id"]
        for entry in gamedata.all_items()
        if str(entry.get("id") or "").startswith(_CAKE_PREFIX)
    )


# ─── Structures we can say something factual about ───────

# MapObjectIds, from the save, checked against `DT_MapObjectMasterDataTable`.
#
# `BreedFarm` is the Breeding Farm. `MonsterFarm` is the **Ranch** and is not in
# this map — they are different structures, and the parser's POI categories had
# them confused until this feature went looking (see `_POI_CATEGORIES`).
FEED_BOXES = ("PalFoodBox", "CoolerPalFoodBox")
BREEDING_FARMS = ("BreedFarm",)
MEDICINE_BOXES = ("PalMedicineBox",)


def _row(item_id: str, count: int) -> dict[str, Any]:
    """
    One item row. The icon path comes straight out of the bundled catalogue —
    `describe_item` already records it, and deriving one from the id is the
    mistake `install-icons.py` documents: item icons are named after their
    *texture*, so nothing turns `AIcore` into `T_itemicon_Material_AIcore`.
    """
    entry = gamedata.describe_item(item_id)
    return {
        "itemId": item_id,
        "itemName": entry["name"],
        "icon": entry["icon"],
        "count": count,
    }


def _container_totals(container: dict) -> dict[str, int]:
    """`{item_id: count}` for one container summary's slots."""
    totals: dict[str, int] = {}
    for slot in container.get("slots") or []:
        if slot.get("isEmpty"):
            continue
        item_id = str(slot.get("itemId") or "")
        if item_id:
            totals[item_id] = totals.get(item_id, 0) + int(slot.get("stackCount") or 0)
    return totals


def _structures(summary: dict, kinds: tuple[str, ...], containers: dict) -> list[dict]:
    """
    Every container of these kinds at one base, with what is in it.

    Reads the base's own `containers` breakdown, which is already privacy-scoped
    by the caller — this module never queries a container by id, which is the
    mistake `/api/inventory/{id}` made.
    """
    out = []
    for entry in summary.get("containers") or []:
        if entry.get("kind") not in kinds:
            continue
        slots = containers.get(entry.get("containerId"), [])
        totals = _container_totals({"slots": slots})
        out.append({
            "containerId": entry.get("containerId"),
            "kind": entry.get("kind"),
            "kindName": entry.get("kindName"),
            "usedSlots": entry.get("usedSlots", 0),
            "totalSlots": entry.get("totalSlots", 0),
            "itemCount": sum(totals.values()),
            "items": [_row(i, n) for i, n in sorted(totals.items(), key=lambda kv: -kv[1])],
        })
    return out


def base_report(
    summary: dict,
    containers: dict,
    *,
    staples: tuple[str, ...],
    floor: int,
    hungry: int = 0,
    pal_count: int = 0,
) -> dict[str, Any]:
    """
    One base's supply picture. Pure — every input is passed in, so the caller
    owns scoping and this cannot reach past what it was handed.
    """
    feed = _structures(summary, FEED_BOXES, containers)
    farms = _structures(summary, BREEDING_FARMS, containers)
    medicine = _structures(summary, MEDICINE_BOXES, containers)

    held = {row["itemId"]: row["count"] for row in summary.get("items") or []}
    cakes = set(cake_ids())

    stock = [
        {
            **_row(item_id, held.get(item_id, 0)),
            "floor": floor,
            "stackSize": gamedata.max_stack(item_id),
            "below": held.get(item_id, 0) < floor,
        }
        for item_id in staples
    ]

    # Facts only. Each of these is something the save says outright; none of them
    # asserts what a structure does with its contents.
    notes: list[dict[str, str]] = []

    if not feed:
        notes.append({
            "kind": "noFeedBox",
            "text": "No Feed Box or Cold Food Box at this base.",
        })
    else:
        empty = [f for f in feed if f["itemCount"] == 0]
        if empty:
            notes.append({
                "kind": "emptyFeedBox",
                "text": (
                    f"{len(empty)} of {len(feed)} food boxes here are empty."
                    if len(feed) > 1
                    else "The food box at this base is empty."
                ),
            })

    if hungry:
        notes.append({
            "kind": "hungryPals",
            "text": (
                f"{hungry} of this base's {pal_count} Pals are hungry or starving."
                if pal_count
                else f"{hungry} Pals here are hungry or starving."
            ),
        })

    for farm in farms:
        if not {row["itemId"] for row in farm["items"]} & cakes:
            notes.append({
                "kind": "breedingFarmNoCake",
                "text": "There is a Breeding Farm here with no cake in it.",
            })
            break

    low = [s for s in stock if s["below"]]
    if low:
        notes.append({
            "kind": "lowStaples",
            "text": (
                f"{len(low)} staple material(s) below {floor}: "
                + ", ".join(s["itemName"] for s in low[:5])
                + ("…" if len(low) > 5 else "")
            ),
        })

    return {
        "baseId": summary.get("baseId"),
        "baseName": summary.get("baseName"),
        "guildId": summary.get("guildId"),
        "guildName": summary.get("guildName"),
        "palCount": pal_count,
        "hungryPals": hungry,
        "feedBoxes": feed,
        "breedingFarms": farms,
        "medicineBoxes": medicine,
        "staples": stock,
        "notes": notes,
    }


def guild_report(
    guild: dict, container_id: str, containers: dict, *, staples: tuple[str, ...]
) -> dict[str, Any]:
    """
    One guild's chest — shared across every base that guild owns.

    Reported separately from the bases for the reason in the module docstring:
    there is exactly one of these per guild, so folding it into a per-base figure
    would count the same items once for every base and invent stock that is not
    there. That is the same mistake `guildPalCount` exists to prevent.
    """
    slots = containers.get(container_id, [])
    totals = _container_totals({"slots": slots})
    used = sum(1 for s in slots if not s.get("isEmpty"))

    return {
        "guildId": guild.get("id"),
        "guildName": guild.get("name"),
        "containerId": container_id,
        "usedSlots": used,
        "totalSlots": len(slots),
        "itemCount": sum(totals.values()),
        "items": [_row(i, n) for i, n in sorted(totals.items(), key=lambda kv: -kv[1])],
        "staples": [_row(i, totals.get(i, 0)) for i in staples],
    }


def parse_materials(raw: Optional[str]) -> tuple[str, ...]:
    """
    A caller-supplied staple list, or the default.

    Unknown ids are kept rather than dropped: an operator asking about a modded
    item should see it reported as zero, not silently removed from their own
    list. `itemName` falls back to `humanize()` for them, which is what every
    other lookup here does.
    """
    if not raw:
        return DEFAULT_STAPLES
    ids = tuple(part.strip() for part in raw.split(",") if part.strip())
    return ids or DEFAULT_STAPLES
