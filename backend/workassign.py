"""
Who is *actually* working where — and where that disagrees with the ranking.

`baseassign.py` answers "who should work at this base": it reads the structures
standing there out of `DT_MapObjectAssignData` and ranks candidates. This module
answers the question an operator asks first — **"who IS mining"** — from
`WorkSaveData`, the game's own record of every assignment it has made.

A SECOND MODULE RATHER THAN A WIDER `baseassign`
------------------------------------------------
Third time this project has made that call, after `palresist` beside `palstats`
and `passiveeffects` beside both. The reason is the same each time: the two have
**opposite policies on the same data**. `baseassign` must exclude a Pal that
cannot do a job, because listing it would be noise in a recommendation. This
must include it, because a Pal the game has assigned to a job it is bad at is
precisely the finding worth surfacing.

Folding them together means one of those policies wins and the other becomes a
blind spot, which is the failure AGENTS.md records three times over.

WHAT THIS WILL AND WILL NOT SAY
-------------------------------
It reports **facts and disagreements**, never instructions. "Nobody is assigned
to this Ranch" is a fact. "This Pal has work rank 0 for the job it is doing" is
a disagreement between two things the game itself states. Neither needs a
mechanic cited.

What it does **not** do is tell anyone to move a Pal — that is `baseassign`'s
job, it already has the ranking to justify it, and nothing here writes.

THE COMPARISON IS THE POINT, AND IT CUTS BOTH WAYS
--------------------------------------------------
Until now the work optimiser had no way to check itself. A ranking that
disagrees with what the game chose is either a better answer or a bug in the
ranking, and there was no third source to break the tie. `mismatches()` is that
source: it is deliberately framed as *"the game and the ranking disagree here"*
rather than *"the game is wrong"*, because on current evidence either side can
be. `unsuitable` — the assigned Pal's work level for its own job is **0** — is
the one case where the disagreement is one-sided, since a rank-0 Pal cannot do
the work at any speed.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import gamedata

logger = logging.getLogger(__name__)


# `WorkableType` values seen on real worlds, with what each means for reading
# the rest of the row. The distinction that matters is whether the job has fixed
# standing positions: the wandering kinds report `fixedPositions: 0` however
# many Pals are on them.
WANDERING_TYPES = frozenset({"MonsterFarm", "OnlyJoinAndWalkAround"})

# `EPalWorkSuitability::Anyone` IS NOT A WORK TYPE, AND READING IT AS ONE
# FLAGGED JETRAGON AS UNFIT TO BREED.
#
# It is the game's pseudo-suitability meaning *no suitability is required*:
# `AGENTS.md` already records it as the eleventh key in
# `WorkSuitabilityDefineDataMap`, a flat 100 at every rank, and the measurement
# that settles it is that **0 of the 753 species carry an `Anyone` rank** while
# 8 structures in `DT_MapObjectAssignData` ask for it — the Breeding Farm, both
# booths, the hand-cranked generator.
#
# So every Pal's level for it is 0, and a naive check called all seven Pals on
# refworld's Breeding Farms unsuitable. A category whose membership disagrees
# with what the game plainly does is wrong however plausible it reads.
#
# It stays in `needs` because it is a true statement about the structure and
# the UI should say "any Pal"; it is excluded from the *suitability* test,
# which is a different question.
ANY_PAL = "Anyone"


def _work_level(pal: dict[str, Any], work: str) -> Optional[int]:
    """
    This Pal's total level for one work type, or None if unknowable.

    `base` + `bought`, the same two terms `optimise.work_level` reports and for
    the same reason they stay separate there: "this species is good at mining"
    and "somebody spent Pal Souls on this one" are different facts.

    **None is not zero.** An NPC or an unrecognised species has no work table at
    all, and reporting it as rank 0 would file it under "assigned to a job it
    cannot do" — a claim about a character this project cannot even name.
    """
    details = gamedata.character(str(pal.get("speciesId") or "")) or {}
    suitabilities = details.get("workSuitabilities")
    if suitabilities is None:
        return None
    base = int((suitabilities or {}).get(work, 0) or 0)
    bought = 0
    for entry in pal.get("workRanks") or []:
        if isinstance(entry, dict) and str(entry.get("work") or "") == work:
            bought = int(entry.get("rank") or 0)
    return base + bought


def _jobs_of(structure_id: str) -> list[str]:
    """
    The work types a structure needs, from `DT_MapObjectAssignData`.

    A structure can need more than one — a farm plot needs Seeding, Watering and
    Collection — so this is a list, and reading only the first answers a third
    of the question while looking complete. `gamedata.work_assign`'s own
    docstring makes the same point.
    """
    row = gamedata.work_assign(structure_id)
    if not row:
        return []
    slots = row.get("slots")
    if not isinstance(slots, list):
        return []
    return [str(s.get("work") or "") for s in slots if isinstance(s, dict) and s.get("work")]


def summarise(
    work: list[dict[str, Any]],
    pals: list[dict[str, Any]],
    bases: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Per-base actual assignments, with the Pals named.

    `work` is `parser.extract_work_assignments`; `pals` and `bases` are the
    already-scoped lists the caller is entitled to see, so privacy is decided
    before this runs rather than inside it.

    **A base with no assignments still appears.** "Nobody is working here" is a
    real and useful answer, and dropping the row makes it indistinguishable from
    a base this failed to read — the `.catch(() => [])` lesson.
    """
    by_instance = {str(p.get("instanceId") or "").lower(): p for p in pals}
    base_names = {str(b.get("id") or ""): b for b in bases}

    per_base: dict[str, dict[str, Any]] = {
        base_id: {
            "baseId": base_id,
            "baseName": entry.get("name") or "",
            "jobs": [],
            "workersAssigned": 0,
            "staleAssignments": 0,
        }
        for base_id, entry in base_names.items()
    }

    # Jobs outside any base — a world-placed structure being repaired. Kept
    # rather than dropped, under a null base, because they are real work the
    # guild's Pals are doing.
    unbased: list[dict[str, Any]] = []
    stale_total = 0

    for job in work:
        jobs_needed = _jobs_of(str(job.get("structureId") or ""))
        assigned = []
        for slot in job.get("assigned") or []:
            pal = by_instance.get(str(slot.get("instanceId") or "").lower())
            if pal is None:
                # Not stale — stale is counted by the extractor against the
                # whole world. This one exists and is simply outside what the
                # caller may see, so it is counted and not named.
                continue
            # `Anyone` is excluded here and only here — see ANY_PAL.
            levels = {w: _work_level(pal, w)
                      for w in jobs_needed if w != ANY_PAL}
            assigned.append({
                "instanceId": pal.get("instanceId"),
                "name": pal.get("nickname") or pal.get("name") or "",
                "speciesId": pal.get("speciesId"),
                "level": pal.get("level"),
                "workLevels": levels,
                "state": slot.get("state"),
            })

        row = {
            "workId": job.get("workId"),
            "structureId": job.get("structureId"),
            "structureName": job.get("structureName"),
            "defineId": job.get("defineId"),
            "workableType": job.get("workableType"),
            "wanders": job.get("workableType") in WANDERING_TYPES,
            "needs": jobs_needed,
            # True when the structure asks for `Anyone`, i.e. any Pal qualifies
            # and no suitability check applies. Travels so the UI can say so
            # rather than rendering an empty requirement list.
            "anyPalQualifies": ANY_PAL in jobs_needed,
            "assigned": assigned,
            "staleAssignments": job.get("staleAssignments", 0),
            "fixedPositions": job.get("fixedPositions", 0),
        }
        stale_total += int(job.get("staleAssignments") or 0)

        base_id = str(job.get("baseId") or "")
        target = per_base.get(base_id)
        if target is None:
            unbased.append(row)
            continue
        target["jobs"].append(row)
        target["workersAssigned"] += len(assigned)
        target["staleAssignments"] += int(job.get("staleAssignments") or 0)

    return {
        "bases": list(per_base.values()),
        "unbased": unbased,
        "totalJobs": len(work),
        "totalAssigned": sum(len(j.get("assigned") or []) for j in work),
        "staleAssignments": stale_total,
        # The client is the thing about to draw a legend, so it is told that the
        # state integers have no names — same reason `roleFromName` and
        # `hasMultiplier` travel in their payloads.
        "stateIsUnnamed": True,
    }


def mismatches(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Where the save's own assignment and the game's own work table disagree.

    Two kinds, and only two, because these are the ones neither side can
    explain away:

    - **`unsuitable`** — an assigned Pal's work level for the job is **0**.
      `workrank.py` reads the game's own curve, whose entry for rank 0 is 0
      output on every work type except the Ranch. So this is not a matter of
      being slow; the Pal contributes nothing.
    - **`empty`** — a job with a work requirement and nobody on it.

    **`unknown` is neither.** A character with no work table — an NPC sharing
    `CharacterSaveParameterMap` with the Pals, of which the reference world has
    99 — is skipped rather than flagged, exactly as `palcheck` treats an
    unrecognised species as an advisory. A confident "this merchant cannot mine"
    would be the same error one module over.

    Deliberately NOT included: "a better Pal is available". That is a ranking
    claim, it belongs to `baseassign`, and putting it here would make this
    module a second recommender whose advice could differ from the first one's.
    """
    out: list[dict[str, Any]] = []
    for base in summary.get("bases") or []:
        for job in base.get("jobs") or []:
            needs = job.get("needs") or []
            if not needs:
                # No row in DT_MapObjectAssignData. Not a gap — chests, beds and
                # the palbox are legitimately assigned to nobody.
                continue
            assigned = job.get("assigned") or []
            if not assigned:
                out.append({
                    "kind": "empty",
                    "baseId": base.get("baseId"),
                    "baseName": base.get("baseName"),
                    "structureName": job.get("structureName"),
                    "needs": needs,
                })
                continue
            if job.get("anyPalQualifies"):
                # Nothing to be unsuitable for. The structure takes any Pal.
                continue
            for pal in assigned:
                levels = pal.get("workLevels") or {}
                # Every job this structure needs, at rank 0. A Pal that can do
                # one of three is doing useful work and is not a mismatch.
                known = [v for v in levels.values() if v is not None]
                if known and not any(known):
                    out.append({
                        "kind": "unsuitable",
                        "baseId": base.get("baseId"),
                        "baseName": base.get("baseName"),
                        "structureName": job.get("structureName"),
                        "needs": needs,
                        "instanceId": pal.get("instanceId"),
                        "name": pal.get("name"),
                        "speciesId": pal.get("speciesId"),
                    })
    return out


def data_available() -> bool:
    """
    Whether `DT_MapObjectAssignData` loaded at all.

    Distinct from a structure simply not being in it. Without the bundle every
    job reports `needs: []` and `mismatches` returns nothing — which reads as a
    perfectly-run base rather than as a missing file.
    """
    return gamedata.work_assign_available()
