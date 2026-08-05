"""
Who should work at this base — from what the base actually contains.

The original request, and the half of #44 that was not a leaderboard: *look at
what is built at a base, work out what work it needs, and say who to put there
while accounting for the Pals already committed to other bases and to parties.*

WHERE THE REQUIREMENTS COME FROM
--------------------------------
`DT_MapObjectAssignData` via `gamedata.work_assign`, keyed on the structure the
save says is standing there. This is the table that the base supply advisor and
the work optimiser both concluded did not exist — twice, in consecutive commits.
It carries the work type, the **minimum rank** a worker needs, and the sanity
drain per tick.

**A structure with no row is not a gap.** `work_assign` returns None for exactly
the things no Pal is ever assigned to: chests, beds, the palbox, the spa, walls
and the food boxes. Treating a missing row as "unknown work" would report every
base as needing thirteen kinds of worker it does not.

**`workerMax == 0` means UNSET, not "no workers allowed".** 178 of the table's
271 rows carry it, and the bundle flags it as `workerMaxIsUnset` for that
reason. Summing it as a capacity gives every base a requirement of zero.

RECOMMEND, NEVER ASSIGN
-----------------------
Nothing here writes. Moving a Pal between containers is `palclone`/`charedit`
territory with its own verification and its own capability, and a recommendation
engine that could also act would be one bug away from rearranging someone's
server. The payload names Pals and reasons; a human moves them in game.

A PAL COMMITTED ELSEWHERE IS REPORTED, NOT HIDDEN
-------------------------------------------------
This is the part the request was actually about. A Pal at another base or in a
party is not free — taking it costs something there. But **excluding it would
hide the best answer**: "your only Pal that can smelt is at Base 3" is a real
and useful thing to be told, and silently omitting it looks like having no
candidate at all.

So every candidate carries `availability`:

    free       in a palbox or a Pal storage — take it, nothing loses out
    base       working at another base, named, so the trade is visible
    party      in someone's party
    committed  at THIS base already, and therefore not a suggestion

Free candidates are ranked first and the rest follow, rather than being merged
on score — a costless option beats a costly one of equal quality, and that is an
ordering rule rather than a fudge factor invented for the score.

CAPACITY IS THE BASE'S OWN, AND MAY BE UNKNOWN
----------------------------------------------
`workerCapacity` comes from the base's worker container (see
`parser.extract_base_worker_capacity`), so it already accounts for the server's
`BaseCampWorkerMaxNum` and the base's level. It is **absent rather than zero**
when the container did not resolve, and this module keeps that distinction:
`freeSlots` is None, not 0, and the UI must not render a full base.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import gamedata
import optimise

logger = logging.getLogger(__name__)

# How many candidates to offer per uncovered work type. Enough to choose from,
# short enough to read; the full ranking is `/api/optimise/work`.
DEFAULT_CANDIDATES = 5

# Availability, best-to-worst. The order is the ranking rule — see the module
# docstring — not a score.
_AVAILABILITY_ORDER = {"free": 0, "party": 1, "base": 2, "committed": 3}


def _work_names() -> dict[str, str]:
    """
    `{work id: what the game calls it}` — `EmitFlame` is "Kindling".

    **The key is `display_name`, not `name`.** That is the bundled table's own
    spelling, and reading `name` returns nothing, which silently labels every
    row with an internal id. It has already cost one round of this feature.
    """
    return {
        str(row.get("id") or ""): str(row.get("display_name") or row.get("id") or "")
        for row in optimise.work_types()
    }


def _structure_demand(
    structures: list[dict[str, Any]], names: dict[str, str]
) -> dict[str, dict[str, Any]]:
    """
    `{work_id: {minRank, maxRank, slots, structures}}` for one base.

    **TWO RANKS, BECAUSE ONE NUMBER CANNOT ANSWER BOTH QUESTIONS**, and picking
    either alone is wrong in a way that still looks right:

      * `minRank` — the lowest rank any station of this work will accept. This
        is what decides whether the base can do the work *at all*, and it is
        what coverage tests against.
      * `maxRank` — the highest. What it takes to staff *every* station.

    A single "required rank" was the first design and the data refuses it.
    `Lab_Fire` carries **ten slots of the same work at ranks 1 through 10** —
    tiered stations, where a rank-1 Pal fills the first — so a max would declare
    the research lab unusable without a rank-10 Kindling Pal. Meanwhile
    `AncientMultiProduct_Mining` carries ten slots **all at rank 6**, where the
    max is exactly right. The same field means different things per structure,
    so both ends travel.

    `slots` is the number of worker positions, which is the real per-structure
    capacity — **not `workerMax`**, which is 0/unset on 178 of the table's 271
    rows and would read as "no workers allowed".

    Verified against a known in-game requirement rather than against itself:
    the Ancient Workbench needs Handiwork 6 and Medicine 6, and that is exactly
    what `AncientWorkBench` decodes to.
    """
    demand: dict[str, dict[str, Any]] = {}
    for structure in structures:
        kind = str(structure.get("kind") or "")
        row = gamedata.work_assign(kind)
        if not row:
            # Not a gap: chests, beds, walls and the palbox legitimately have no
            # row. See the module docstring.
            continue
        if not row.get("baseWorkerWorkable", True):
            # Player-only stations. A Pal is never assigned, so it is not demand.
            continue
        for slot in row.get("slots") or []:
            work = str(slot.get("work") or "")
            if not work:
                continue
            entry = demand.setdefault(work, {
                "work": work,
                "workName": names.get(work) or work,
                "minRank": None,
                "maxRank": 0,
                "slots": 0,
                "structures": [],
                "sanityPerTick": 0.0,
            })
            rank = max(1, int(slot.get("requiredRank") or 1))
            entry["minRank"] = rank if entry["minRank"] is None else min(entry["minRank"], rank)
            entry["maxRank"] = max(entry["maxRank"], rank)
            entry["slots"] += 1
            entry["sanityPerTick"] += float(slot.get("sanityPerTick") or 0.0)
            name = gamedata.structure_name(kind)
            if name not in entry["structures"]:
                entry["structures"].append(name)
    for entry in demand.values():
        entry["sanityPerTick"] = round(entry["sanityPerTick"], 3)
        if entry["minRank"] is None:
            entry["minRank"] = 1
    return demand


def _availability(pal: dict[str, Any], base_id: str, base_names: dict[str, str]) -> dict[str, Any]:
    """Where this Pal currently is, and therefore what taking it would cost."""
    location = str(pal.get("location") or "")
    pal_base = str(pal.get("baseId") or "")

    if pal_base and pal_base == base_id:
        return {"availability": "committed", "where": "This base"}
    if location == "base" and pal_base:
        return {
            "availability": "base",
            "where": base_names.get(pal_base) or "Another base",
        }
    if location == "party":
        return {"availability": "party", "where": "In a party"}
    # Palbox, Pal storage, or anything else uncommitted. `storage` counts as
    # free: a Pal in a Flea Market stand or a Dimensional Pal Storage is not
    # doing a job.
    return {"availability": "free", "where": "Palbox / storage"}


def _covered_by(workers: list[dict[str, Any]], work: str, min_rank: int) -> list[dict[str, Any]]:
    """
    The Pals here who can work this job at all — level >= `minRank`.

    Tested against the **minimum**, not the maximum: a base whose lowest station
    is staffed can do the work. Whether it can staff the *hardest* station is a
    separate statement, and `bestRank` beside this is what says it.
    """
    out = []
    for pal in workers:
        level = optimise.work_level(pal, work)
        if level["level"] >= max(1, min_rank):
            out.append({
                "instanceId": pal.get("instanceId"),
                "name": pal.get("speciesName") or pal.get("nickname") or "",
                "level": level["level"],
            })
    out.sort(key=lambda r: -r["level"])
    return out


def base_report(
    base: dict[str, Any],
    structures: list[dict[str, Any]],
    pals: list[dict[str, Any]],
    base_names: dict[str, str],
    *,
    candidates: int = DEFAULT_CANDIDATES,
) -> dict[str, Any]:
    """
    What this base needs, what it already has, and who could fill the gaps.

    `pals` is the **caller's scoped set**, already filtered for privacy and
    ownership by the route. This function does no scoping of its own, for the
    reason `_scope_pals` exists: one rule, one place.
    """
    base_id = str(base.get("id") or "")
    demand = _structure_demand(structures, _work_names())
    workers = [p for p in pals if str(p.get("baseId") or "") == base_id
               and str(p.get("location") or "") == "base"]

    capacity = base.get("workerCapacity")
    # None, not 0, when the worker container did not resolve — "no cap known"
    # and "no room" must not share a representation.
    free_slots = (int(capacity) - len(workers)) if capacity else None

    needs: list[dict[str, Any]] = []
    for work, entry in demand.items():
        covered = _covered_by(workers, work, entry["minRank"])
        best = max((c["level"] for c in covered), default=0)
        row = {
            **entry,
            "coveredBy": covered,
            "covered": bool(covered),
            # The highest rank standing here. Against `maxRank` this says
            # whether the base's best station is actually being worked — a base
            # can be "covered" and still have its Ancient Workbench idle.
            "bestRank": best,
            "topStationStaffed": best >= entry["maxRank"],
        }
        if not covered:
            # Only look for candidates for work nobody here can do. Ranking
            # replacements for a job already covered is noise, and it is what
            # `/api/optimise/work` is for.
            pool = [p for p in pals if str(p.get("baseId") or "") != base_id]
            ranked = optimise.rank_for_work(pool, work, limit=0)
            scored = []
            for candidate in ranked:
                if candidate["work"]["level"] < max(1, entry["minRank"]):
                    continue
                source = next(
                    (p for p in pals if p.get("instanceId") == candidate.get("instanceId")),
                    None,
                )
                where = _availability(source or {}, base_id, base_names)
                scored.append({**candidate, **where})
            # Availability first, then the ranking `rank_for_work` already
            # produced. A costless Pal beats a costly one of equal quality; this
            # is an ordering rule, not a number added to the score.
            scored.sort(key=lambda r: _AVAILABILITY_ORDER.get(r["availability"], 9))
            row["candidates"] = scored[:candidates] if candidates else scored
            row["candidateCount"] = len(scored)
        else:
            row["candidates"] = []
            row["candidateCount"] = 0
        needs.append(row)

    # Uncovered first, then by how many structures depend on it: an uncovered
    # job blocking four stations matters more than one blocking a single bench.
    needs.sort(key=lambda r: (r["covered"], -len(r["structures"]), r["work"]))

    return {
        "baseId": base_id,
        "baseName": base.get("name"),
        "guildId": base.get("guildId"),
        "guildName": base.get("guildName"),
        "workerCount": len(workers),
        "workerCapacity": capacity,
        "freeSlots": free_slots,
        "needs": needs,
        "uncovered": sum(1 for n in needs if not n["covered"]),
        # Structures standing here that need no worker at all. Reported as a
        # count so "this base has 40 objects and 6 jobs" does not read as data
        # having gone missing.
        "structuresWithoutWork": sum(
            1 for s in structures if not gamedata.work_assign(str(s.get("kind") or ""))
        ),
        # Said in the payload, not only in a docstring: the client is the thing
        # about to render a suggestion next to an Apply-looking button.
        "advisoryOnly": True,
    }


def data_available() -> bool:
    """Whether the requirements table loaded. A false here means no report."""
    return gamedata.work_assign_available()
