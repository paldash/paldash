"""
Illegal-Pal detection and repair (Phase 7).

A Palworld server picks these up from three directions: a cheat client, a Pal
traded in from a modded world, and a save edited by an earlier, less careful
tool. The symptom is a stat outside what the game can produce — IV 255, condenser
rank 12, level 100 on a level-80 cap — and the consequences range from an unfair
guild to a client that disconnects on sight of the Pal.

WHAT COUNTS AS ILLEGAL
----------------------
Only what `editschema` already asserts, and nothing invented on top. The bounds
were measured against 1,905 real Pals and derived from the bundled game data, so
this module is a *scan against the existing schema* rather than a second opinion
about what Palworld allows. If a bound is wrong, it is wrong in one place.

REPAIRABLE IS NARROWER THAN DETECTABLE
--------------------------------------
Every issue is reported; only some can be fixed by writing:

- **IV, rank, level** — clamped into range. `_write_property` writes into the
  existing shape, and the property is by definition present (that is how the bad
  value got read), so these are safe.
- **EXP** — moved to the band for the Pal's level, since the pair has to agree
  or the game recomputes the level on load.
- **Passive skills** — reported, never written. They are an ArrayProperty and
  `_write_property` handles scalars only; a partial list write is how a save
  stops loading.
- **Unknown species** — reported, never written. Changing what a Pal *is*
  cascades into the Paldeck and breeding, and the honest fix is to delete the
  Pal, which is the owner's decision and not a repair.

CLAMPING IS NOT NEUTRAL
-----------------------
Pulling IV 255 down to 100 makes a Pal weaker. That is the point, and it is also
why the repair goes through the same preview-approve-apply path as every other
edit rather than running automatically: it is a judgement about other people's
Pals, and someone has to make it deliberately.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import charedit
import editschema
import gamedata

logger = logging.getLogger(__name__)

# Issue codes. Stable strings, because the UI groups by them and the audit log
# records them.
IV_OUT_OF_RANGE = "iv_out_of_range"
RANK_OUT_OF_RANGE = "rank_out_of_range"
LEVEL_OUT_OF_RANGE = "level_out_of_range"
EXP_MISMATCH = "exp_mismatch"
TOO_MANY_PASSIVES = "too_many_passives"
DUPLICATE_PASSIVES = "duplicate_passives"
UNKNOWN_PASSIVE = "unknown_passive"
UNKNOWN_SPECIES = "unknown_species"

# Which codes the repair path can actually act on. Everything else is reported
# and left alone — see the module docstring.
REPAIRABLE = (IV_OUT_OF_RANGE, RANK_OUT_OF_RANGE, LEVEL_OUT_OF_RANGE, EXP_MISMATCH)

# Codes that are informational rather than evidence of anything.
#
# `unknown_species` earned this the hard way. It is not a reliable cheating
# signal, because the bundled tables are *incomplete*, not because the world is
# modded: `CharacterSaveParameterMap` holds humans as well as Pals, and 13 of
# the reference world's 1,905 entries are ordinary NPCs — `Male_Soldier`,
# `Female_DesertPeople`, `Scientist_LaserRifle` — that simply are not in the
# 753-Pal / NPC tables. There is no structural way to tell them apart either;
# they carry IVs and passive skills exactly like a Pal does.
#
# So an unrecognised id means "we do not have this in our data", which is a
# different statement from "someone cheated". Counting it as a violation would
# put roughly a dozen false accusations on every clean world and make the
# headline number worthless. The stat checks have no such problem — they flag 0
# on the reference world — so keeping the two apart is what keeps the scan
# trustworthy.
ADVISORY = (UNKNOWN_SPECIES,)


def _issue(code: str, field: str, found: Any, detail: str, fix: Any = None) -> dict:
    return {
        "code": code,
        "field": field,
        "found": found,
        "detail": detail,
        "repairable": code in REPAIRABLE,
        "advisory": code in ADVISORY,
        "fix": fix,
    }


def inspect_pal(pal: dict) -> list[dict]:
    """
    Every schema violation on one Pal, as parsed by `parser.extract_characters`.

    Pure, and cheap enough to run across every Pal in a world — no save access,
    no game-data lookups beyond the two id checks.
    """
    issues: list[dict] = []
    max_level = editschema._max_level()

    # ─── Species ───
    #
    # `character()` and not `pal()`. CharacterSaveParameterMap holds humans too —
    # guards, merchants, villagers, tower bosses — and 100 of the reference
    # world's 1,905 entries are NPCs. A Pal-table lookup alone reported every one
    # of them as modded content, which would have made a clean world look
    # thoroughly cheated on.
    species = str(pal.get("speciesId") or "")
    if species and not gamedata.character(species):
        issues.append(_issue(
            UNKNOWN_SPECIES, "speciesId", species,
            f"{species!r} is not in the bundled Pal or NPC tables. Most likely an NPC "
            "the reference data does not list — 13 of the reference world's own "
            "characters are like this. Only worth a look if you were not expecting mods.",
        ))

    # ─── IVs ───
    # `parser` reads `melee` too because `_TALENTS` still lists it, but Palworld
    # 1.0 does not store it and the schema has no field for it. Iterating the
    # schema's IV list rather than the Pal's keys keeps the two from disagreeing.
    ivs = pal.get("ivs") or {}
    for name in editschema.IV_FIELDS:
        if name not in ivs:
            continue
        value = ivs[name]
        if not isinstance(value, int) or value < 0 or value > editschema.MAX_IV:
            clamped = max(0, min(editschema.MAX_IV, int(value))) if isinstance(value, int) else 0
            issues.append(_issue(
                IV_OUT_OF_RANGE, f"ivs.{name}", value,
                f"{name.upper()} IV is {value}; the game rolls 0–{editschema.MAX_IV}.",
                fix=clamped,
            ))

    # ─── Condenser rank ───
    rank = pal.get("rank")
    if isinstance(rank, int) and not (editschema.MIN_RANK <= rank <= editschema.MAX_RANK):
        issues.append(_issue(
            RANK_OUT_OF_RANGE, "rank", rank,
            f"Condenser rank {rank} is outside {editschema.MIN_RANK}–{editschema.MAX_RANK}.",
            fix=max(editschema.MIN_RANK, min(editschema.MAX_RANK, rank)),
        ))

    # ─── Level, then EXP against it ───
    level = pal.get("level")
    level_ok = isinstance(level, int) and 1 <= level <= max_level
    if isinstance(level, int) and not level_ok:
        clamped_level = max(1, min(max_level, level))
        issues.append(_issue(
            LEVEL_OUT_OF_RANGE, "level", level,
            f"Level {level} is outside 1–{max_level}.",
            fix=clamped_level,
        ))
        level = clamped_level

    # EXP is checked against whatever level the Pal will *end up* at, so a Pal
    # that is illegal on both counts gets one coherent repair rather than two
    # that contradict each other.
    #
    # Only EXP *above* the band is a problem, and the asymmetry is measured, not
    # assumed — see `editschema._check_exp_matches_level`. Eight Pals on the
    # reference world sit below their band because that is what a freshly caught
    # Pal looks like; flagging them would report a clean world as cheated.
    exp = pal.get("exp")
    if isinstance(level, int) and isinstance(exp, int) and 1 <= level <= max_level:
        low, high = editschema._exp_band(level, "PalTotalEXP")
        if high is not None and exp > high:
            issues.append(_issue(
                EXP_MISMATCH, "exp", exp,
                f"{exp:,} EXP is beyond level {level}, which ends at {high:,}. The game "
                "levels a character up from EXP on load, so this Pal is not really the "
                "level it displays.",
                fix=high,
            ))

    # ─── Passive skills ───
    passives = pal.get("passiveSkills") or []
    if isinstance(passives, list):
        if len(passives) > editschema.MAX_PASSIVES:
            issues.append(_issue(
                TOO_MANY_PASSIVES, "passiveSkills", len(passives),
                f"{len(passives)} passive skills; a Pal can hold "
                f"{editschema.MAX_PASSIVES}.",
            ))
        if len(set(passives)) != len(passives):
            duplicated = sorted({p for p in passives if passives.count(p) > 1})
            issues.append(_issue(
                DUPLICATE_PASSIVES, "passiveSkills", duplicated,
                f"Duplicate passive skill(s): {', '.join(duplicated[:3])}.",
            ))
        unknown = [
            p for p in passives
            if not isinstance(p, str) or not gamedata.describe_passive(p)["known"]
        ]
        if unknown:
            issues.append(_issue(
                UNKNOWN_PASSIVE, "passiveSkills", unknown,
                f"Unrecognised passive skill(s): {', '.join(str(u) for u in unknown[:3])}.",
            ))

    return issues


def scan(pals: list[dict], owners: Optional[dict] = None) -> dict:
    """
    Scan every Pal in the world. Read-only.

    `owners` maps player uid -> name, so the report can say whose Pal it is
    without a second lookup. A Pal with no owner uid is a wild one or one sitting
    in a base, and is reported the same way.
    """
    flagged: list[dict] = []
    advisories: list[dict] = []
    by_code: dict[str, int] = {}
    by_owner: dict[str, int] = {}

    for pal in pals or []:
        issues = inspect_pal(pal)
        if not issues:
            continue

        owner_uid = str(pal.get("ownerUid") or "")
        owner_name = (owners or {}).get(owner_uid) or ""
        record = {
            "instanceId": str(pal.get("instanceId") or ""),
            "speciesId": pal.get("speciesId"),
            "speciesName": gamedata.character_name(str(pal.get("characterId") or "")),
            "nickname": pal.get("nickname") or "",
            "level": pal.get("level"),
            "ownerUid": owner_uid,
            "ownerName": owner_name,
        }

        for issue in issues:
            by_code[issue["code"]] = by_code.get(issue["code"], 0) + 1

        # Violations and advisories are counted separately, because mixing them
        # would put a dozen unrecognised-NPC notices into the headline number on
        # every clean world. See ADVISORY above.
        violations = [i for i in issues if not i["advisory"]]
        if violations:
            flagged.append({
                **record,
                "issues": violations,
                "repairable": any(i["repairable"] for i in violations),
            })
            key = owner_name or owner_uid or "(unowned)"
            by_owner[key] = by_owner.get(key, 0) + 1

        notes = [i for i in issues if i["advisory"]]
        if notes:
            advisories.append({**record, "issues": notes, "repairable": False})

    repairable = [p for p in flagged if p["repairable"]]
    return {
        "palsScanned": len(pals or []),
        "palsFlagged": len(flagged),
        "palsRepairable": len(repairable),
        "issueCount": sum(len(p["issues"]) for p in flagged),
        "byCode": dict(sorted(by_code.items(), key=lambda kv: -kv[1])),
        "byOwner": dict(sorted(by_owner.items(), key=lambda kv: -kv[1])),
        "pals": flagged,
        # Not violations. Reported so an admin can look, never counted as cheating.
        "advisories": advisories,
        "palsUnrecognised": len(advisories),
        # Whether mods are installed, which is the innocent explanation for most
        # unrecognised ids on a modded server — a Pal-adding mod puts species in
        # the save that no bundled table will ever contain. `checked: false` means
        # the game directory was not visible, which must not read as "unmodded".
        "mods": _mod_context(),
        # The bounds this scan used, so a report can be read months later without
        # having to guess which version produced it.
        "bounds": {
            "maxLevel": editschema._max_level(),
            "maxIv": editschema.MAX_IV,
            "rank": [editschema.MIN_RANK, editschema.MAX_RANK],
            "maxPassives": editschema.MAX_PASSIVES,
        },
    }


def _mod_context() -> dict:
    """
    A compact mod summary for the scan report, or an honest "did not look".

    Never raises: a scan is a diagnostic, and losing it because a directory listing
    failed would be a worse outcome than an unqualified advisory count.
    """
    try:
        import mods

        found = mods.detect()
        return {
            "checked": found["checked"],
            "modded": found["modded"],
            "count": len(found["mods"]),
            "reason": found["reason"],
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("Mod detection failed during a scan: %s", e)
        return {"checked": False, "modded": False, "count": 0,
                "reason": "Mod detection failed."}


def scan_current() -> dict:
    """
    Scan the world as the cache currently sees it.

    The repair path calls this rather than accepting a report from the caller.
    A client-supplied report would be carrying the `fix` values, and while
    `editschema` would still reject an out-of-range one, "the client decides what
    each Pal's new stats are" is not a shape worth having at all. The scan is
    cheap — it is pure arithmetic over an already-parsed section.
    """
    import savecache

    data = savecache.get_data()
    if not data:
        raise charedit.EditError(
            "The world has not been parsed yet. Wait for the parse to finish and try again."
        )

    owners = {
        str(p.get("uid") or ""): str(p.get("name") or "")
        for p in (data.get("players") or [])
    }
    return scan(data.get("pals") or [], owners)


def repair_changes(pal_report: dict) -> dict:
    """
    The change set that would fix one flagged Pal, from its scan entry.

    Unrepairable issues contribute nothing — the Pal is still written, just
    without the part that cannot be fixed by a scalar write. Returning `{}` means
    there is nothing to do.
    """
    changes: dict[str, Any] = {}
    for issue in pal_report.get("issues") or []:
        if issue["repairable"] and issue["fix"] is not None:
            changes[issue["field"]] = issue["fix"]
    return changes


def plan_repair(report: dict, instance_ids: Optional[list[str]] = None) -> dict:
    """
    Turn a scan into per-Pal repair change sets. Pure — no writes.

    `instance_ids` narrows the repair to a chosen subset; omitted, every
    repairable Pal in the report is included. Feeding a subset through the same
    function is deliberate — there is no separate "repair one" path to keep in
    step.
    """
    wanted = set(instance_ids) if instance_ids else None

    edits: dict[str, dict] = {}
    skipped: list[dict] = []

    for pal in report.get("pals") or []:
        if wanted is not None and pal["instanceId"] not in wanted:
            continue

        changes = repair_changes(pal)
        if changes:
            edits[pal["instanceId"]] = changes

        remaining = [i for i in pal["issues"] if not i["repairable"]]
        if remaining:
            skipped.append({
                "instanceId": pal["instanceId"],
                "nickname": pal["nickname"],
                "speciesName": pal["speciesName"],
                "issues": remaining,
            })

    return {
        "edits": edits,
        "palsToRepair": len(edits),
        # Named honestly: these Pals keep a problem this build cannot fix by
        # writing. Silently counting them as repaired would be the worst outcome.
        "palsWithUnfixableIssues": len(skipped),
        "unfixable": skipped,
    }


def apply_repair(
    instance_ids: Optional[list[str]] = None,
    expected_plan_hash: Optional[str] = None,
) -> dict:
    """
    Apply a previewed repair.

    Delegates to `charedit.apply_pal_batch`, which means the repair gets the
    guarded write, the fresh re-plan against the live tree, the plan-hash check
    and the all-or-nothing verification without a second write path existing for
    it. The repair itself contributes nothing but the choice of values.
    """
    plan = plan_repair(scan_current(), instance_ids)
    if not plan["edits"]:
        raise charedit.EditError(
            "Nothing to repair — none of the selected Pals have an issue this build "
            "can fix by writing a value."
        )

    result = charedit.apply_pal_batch(
        plan["edits"], label="repair illegal Pals", expected_plan_hash=expected_plan_hash
    )
    return {
        **result,
        "palsWithUnfixableIssues": plan["palsWithUnfixableIssues"],
        "unfixable": plan["unfixable"],
    }


def preview_repair(instance_ids: Optional[list[str]] = None) -> dict:
    """
    A dry run of a repair, with the plan hash the apply will require.

    Needs the live Pal objects to produce a real diff, so unlike `plan_repair`
    this one reads `Level.sav`.
    """
    from parser import load_gvas
    from savefiles import get_level_sav_path

    plan = plan_repair(scan_current(), instance_ids)
    if not plan["edits"]:
        return {"ok": True, "problems": [], "pals": [], "palsChanged": 0,
                "planHash": "", **plan}

    level_path = get_level_sav_path()
    if not level_path:
        raise charedit.EditError("Level.sav not found")
    gvas = load_gvas(level_path)
    if gvas is None:
        raise charedit.EditError("Could not parse Level.sav")

    found = charedit._index_pals(gvas, set(plan["edits"]))
    missing = [i for i in plan["edits"] if i not in found]
    if missing:
        raise charedit.EditError(
            f"{len(missing)} flagged Pal(s) are no longer in this world — the scan is "
            "stale. Re-scan and preview again."
        )

    batch = charedit.plan_pal_batch(
        [(i, found[i], changes) for i, changes in plan["edits"].items()]
    )
    return {**batch, **plan, "applied": False}
