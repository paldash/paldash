"""
Every Pal, whether you have caught it, and how to get the ones you have not.

The Paldeck answers what the game *has*. The Progression tab answers five
specific checklists. Neither says **"you are missing these forty, and here is
the nearest one"**, which is the only question that turns a catalogue into
something to go and do.

## The denominator is Paldeck ENTRIES, not species forms

204 against 753. `HadesBird` and `HadesBird_Electric` are one Helzephyr entry
with one number, and `BOSS_`/`GYM_`/`RAID_` rows are encounter forms of Pals
already counted. Counting forms puts 100% permanently out of reach, which is
the surest way to make a completion tracker useless.

**The save writes FORMS, though.** `PaldeckUnlockFlag` holds `GrassPanda`,
`FireKirin_Dark`, `HadesBird_Electric` — 211 keys on one reference player — so
the join has to fold them onto their entry before counting. An entry is caught
when *any* of its forms is flagged, because the game gives them one number.

## What it will not do

- **Invent a route.** "Where to find it" is `habitats`; "how to breed it" is
  `breeding.obtainability`. A Pal with neither gets `route: "unknown"` rather
  than a guess — 24 species are `never` breedable and some of those are raid
  bosses with no world spawner at all, and saying "go catch it" about
  Bellanoir would be wrong in a way that wastes somebody's evening.
- **Report progress for an account with no character.** Every entry would read
  uncaught, which is not an error and must not render as one. `linked: false`
  says so.
- **Decide who may see the uncaught half.** That is `discoveryVisibility`, and
  it is applied by the route rather than here — the same split
  `/api/world/discoveries` and `/api/world/effigies` already use.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import breeding
import gamedata

logger = logging.getLogger(__name__)

# Encounter forms of Pals the Paldeck already counts once.
_ENCOUNTER_PREFIXES = ("BOSS_", "PREDATOR_", "GYM_", "RAID_")


def _entry_key(entry: dict[str, Any]) -> Any:
    """
    What makes two rows the same Paldeck entry.

    The number, where there is one. **Negative and zero numbers are not a
    grouping key** — the game uses -1 for unreleased and -2 for gym bosses, so
    keying on the number alone would merge every unreleased Pal into one row.
    """
    number = entry.get("paldeckNumber")
    if isinstance(number, int) and number > 0:
        return ("n", number, str(entry.get("zukanSuffix") or ""))
    return ("id", str(entry.get("id") or ""))


def _route(entry: dict[str, Any]) -> dict[str, Any]:
    """
    How to get one Pal: catch it, breed it, or neither.

    Both halves are reported when both exist, because "it spawns in the desert
    *and* Blazamut x Anubis makes one" is two different evenings and the player
    picks.
    """
    out: dict[str, Any] = {}
    cells = int(entry.get("habitatCells") or 0)
    if cells:
        out["catch"] = {"cells": cells}

    try:
        limits = breeding.obtainability(str(entry.get("id") or ""))
    except (breeding.BreedingDataError, gamedata.GameDataUnavailable):
        limits = None
    if limits:
        # **`kind`, not `obtainability`.** The first version read the latter —
        # the function's own name rather than the field it returns — so every
        # entry came back with no breeding route at all and the tracker only
        # ever said "catch it". Caught by asserting that *some* entry is
        # breedable, which is the kind of check a shape test would have passed.
        kind = str(limits.get("kind") or "")
        if kind and kind != "never":
            out["breed"] = {
                "kind": kind,
                # A named pairing is the useful case: the game states it, so the
                # planner can print it rather than searching for a route.
                "pairings": limits.get("pairings") or [],
            }
        elif kind == "never":
            out["breed"] = {"kind": "never",
                            "breedsTrue": bool(limits.get("breedsTrue"))}

    if not out:
        # Neither a world spawner nor a pairing. Raid bosses live here, and so
        # do a handful of quest forms — "unknown" rather than a route invented
        # for them.
        out["unknown"] = True
    return out


def tracker(entries: list[dict[str, Any]],
            unlocked: Optional[list] = None,
            captures: Optional[dict[str, Any]] = None,
            linked: bool = True) -> dict[str, Any]:
    """
    One row per Paldeck entry, with `caught` folded from the save's form flags.

    `entries` is `main._paldeck_entries()` — passed in rather than fetched, so
    this module never reaches into the route layer and the caller keeps its one
    cache. `unlocked` is `progress["paldeck"]["keys"]`, which are FORM ids.
    """
    flags = {str(k).lower() for k in (unlocked or [])}
    counts = {str(k).lower(): v for k, v in ((captures or {}).get("perSpecies") or {}).items()}

    grouped: dict[Any, dict[str, Any]] = {}
    for entry in entries:
        key = _entry_key(entry)
        row = grouped.get(key)
        if row is None:
            row = grouped[key] = {
                "id": entry.get("id"),
                "name": entry.get("name"),
                "icon": entry.get("icon"),
                "elements": list(entry.get("elements") or []),
                "paldeckNumber": entry.get("paldeckNumber"),
                "forms": [],
                "caught": False,
                "habitatCells": 0,
            }
        # Every id this entry answers to, including the ones the listing merged.
        for form in [entry.get("id"), *(entry.get("speciesIds") or []),
                     *(entry.get("variants") or [])]:
            form_id = str(form or "")
            if not form_id or form_id in row["forms"]:
                continue
            row["forms"].append(form_id)
        row["habitatCells"] = max(row["habitatCells"],
                                  int(entry.get("habitatCells") or 0))

    for row in grouped.values():
        # AN ENTRY IS CAUGHT WHEN ANY OF ITS FORMS IS FLAGGED, because the game
        # gives them one Paldeck number. Requiring all of them would leave
        # Helzephyr permanently incomplete for somebody who has one.
        row["caught"] = any(f.lower() in flags for f in row["forms"])
        captured = sum(int(counts.get(f.lower()) or 0) for f in row["forms"])
        if captured:
            row["captured"] = captured
        if not row["caught"]:
            row["route"] = _route(row)

    rows = sorted(
        grouped.values(),
        key=lambda r: (r.get("paldeckNumber") if isinstance(r.get("paldeckNumber"), int)
                       and r["paldeckNumber"] > 0 else 9999, str(r.get("name") or "")),
    )
    caught = sum(1 for r in rows if r["caught"])

    return {
        "entries": rows,
        "total": len(rows),
        "caught": caught,
        "missing": len(rows) - caught,
        # **Never a percentage of 753.** The denominator is entries, and saying
        # which it is stops a client re-deriving it against the wrong number.
        "denominator": "paldeckEntries",
        # No linked character means every row reads uncaught, which is not a
        # score of zero — it is no score at all.
        "linked": bool(linked),
    }


def strip_missing(report: dict[str, Any]) -> dict[str, Any]:
    """
    Drop the not-yet-caught entries, for a viewer the policy does not allow them.

    Server-side, like every other discovery filter here: a UI that received the
    full list and hid part of it would be handing out the answers in the network
    tab. The **counts stay** — how many you are missing is not a spoiler, and
    removing it would make the panel look broken rather than restricted.
    """
    return {
        **report,
        "entries": [r for r in report.get("entries") or [] if r.get("caught")],
        "missingHidden": True,
    }
