#!/usr/bin/env python3
"""
Partner skills: what a Pal does for *you* while it is in your party or ridden.

`DT_PartnerSkillParameter` was read once, for `RestrictionItems` (the mount
list), and its largest column was walked straight past. `PassiveSkills` is a
**list indexed by condenser rank** — five entries — naming the skills that rank
grants.

That makes it a fifth way to improve a Pal, and the dashboard knew four. Level,
condenser, Statue of Power and work handbooks all change what a Pal *is*; this
changes what having it out does for the player, and it scales with the same
condenser stars nothing here had connected to it:

    Silvegis  ShieldDamageCutRate            65% -> 80%   (rank 1 -> 5)
              PlayerShield_RecoverStartTime  30% -> 60%

Both `ToTrainer` and `InvokeInOtomo` — a buff to the player, for being in the
party, which is exactly the reason to carry a Pal that never fights.

## The join is total, and that is the acceptance criterion

Every skill id here must resolve in `passive_effects.json.gz`, which already
carries type, value, target and invoke for all 1,897 passives. **933 of 933
resolve**, so this bundle is a *mapping* and adds no effect data of its own —
and a miss would mean the two extractions had drifted apart rather than that the
game added something.

Nothing is inferred from a skill id. `GiveAElectricity_Ride` reads obvious and
the effect table is what says it: `ElementElectricity 1.0 -> ToTrainer`,
`InvokeRiding`. That is the game confirming "riding Solmora Lux makes your
damage electric" rather than a name being trusted.

## What it deliberately does not do

- **No effect is re-described here.** The ids point at `passiveeffects`, which
  owns the vocabulary and its 208 types. Two descriptions of one effect is how
  they come to disagree.
- **`ActiveSkill` is carried but not interpreted.** Jetragon's
  `UniqueRideShooting_JetMissile` has `ActiveSkill_MainValueByRank
  [20, 22, 26, 32, 40]`, and what that value *is* ("威力", power) is a label in
  the game's editor-only column. The numbers travel; no unit is claimed.

Usage:  python3 scripts/extract-partner-skills.py [--verify]
Output: backend/data/partner_skills.json.gz
"""

from __future__ import annotations

import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import palpak            # noqa: E402
import uassettable       # noqa: E402
from jsonout import write_json  # noqa: E402

OUT = os.path.join(os.path.dirname(HERE), "backend", "data",
                   "partner_skills.json.gz")
EFFECTS = os.path.join(os.path.dirname(HERE), "backend", "data",
                       "passive_effects.json.gz")

UNSET = {"", "None", None}


def _key(value) -> str:
    """Unwrap an `FName` cell. `str()` on `{"Key": ...}` is the shipped-a-dict trap."""
    if isinstance(value, dict):
        value = value.get("Key")
    return str(value or "")


def _ranks(row: dict) -> list[list[str]]:
    """The skill ids each condenser rank grants, in rank order."""
    out = []
    for rank in row.get("PassiveSkills") or []:
        ids = []
        for entry in rank.get("SkillAndParametersArray") or []:
            skill = _key(entry.get("SkillName"))
            if skill not in UNSET:
                ids.append(skill)
        out.append(ids)
    return out


def _active(row: dict) -> dict:
    """
    The ride action, where there is one.

    `SkillName` is `"Unknown"` on most rows — the game's own placeholder, not a
    decode failure — so it is dropped rather than shipped as a name.
    """
    active = row.get("ActiveSkill") or {}
    name = str(active.get("SkillName") or "")
    if name in UNSET or name == "Unknown":
        return {}
    out: dict = {"skill": name}
    by_rank = active.get("ActiveSkill_MainValueByRank") or []
    if by_rank:
        # No unit is claimed: the game labels this column in Japanese and
        # editor-only. The numbers are the numbers.
        out["valueByRank"] = [float(v) for v in by_rank]
    return out


def build(pak=None) -> dict:
    pak = pak or palpak.Pak()
    path = next(
        (f for f in pak.files if f.endswith("DT_PartnerSkillParameter.uasset")), None
    )
    if path is None:
        raise SystemExit("!! DT_PartnerSkillParameter is not in this pak")

    species: dict[str, dict] = {}
    for name, row in uassettable.read_table(pak, path).items():
        ranks = _ranks(row)
        active = _active(row)
        gear = ""
        for entry in row.get("RestrictionItems") or []:
            item = _key(entry)
            if item not in UNSET:
                gear = item
                break
        if not ranks and not active and not gear:
            continue
        entry = {}
        if ranks:
            entry["ranks"] = ranks
        if active:
            entry["active"] = active
        if gear:
            entry["mountGearItem"] = gear
        species[str(name)] = entry

    return {
        "species": species,
        # Said in the bundle rather than only in a docstring: a caller indexing
        # `ranks` needs to know what the index means.
        "ranksAreCondenserRank": True,
        "effectsLiveIn": "passive_effects.json.gz",
    }


def verify(data: dict) -> list[str]:
    """
    Every skill id must resolve in the effect bundle.

    THE ONE CHECK THAT MATTERS, because this file is a mapping and nothing else.
    A dangling id means the two extractions drifted, which a row count would
    never show.
    """
    problems = []
    try:
        with gzip.open(EFFECTS, "rt", encoding="utf-8") as f:
            effects = json.load(f)
    except OSError as e:
        return [f"cannot read {EFFECTS}: {e}"]

    ids = {s for entry in data["species"].values()
           for rank in entry.get("ranks") or [] for s in rank}
    missing = sorted(i for i in ids if i not in effects)
    if missing:
        problems.append(
            f"{len(missing)} of {len(ids)} partner-skill ids resolve to no "
            f"passive, e.g. {missing[:5]} — regenerate passive_effects.json.gz"
        )

    # A rank list that is neither 5 nor 1 long would mean the index means
    # something other than a condenser rank, which is the claim this bundle
    # makes. Measured: 477 species at 5 and 2 at 1.
    #
    # **THE TWO ARE NOT A FLAW AND THE CHECK IS WHY WE KNOW.** They are
    # `LongCat`/`BOSS_LongCat` (Valentail), whose only partner skill is
    # `LowGravity` — an on/off effect with a value of 1.0 and nothing to scale.
    # A single entry is the game declining to write four identical rows, not the
    # rank index meaning something else. Anything other than 1 or 5 is still a
    # refusal, and the 1s are reported rather than waved through.
    odd = sorted(k for k, v in data["species"].items()
                 if v.get("ranks") and len(v["ranks"]) not in (1, 5))
    if odd:
        problems.append(
            f"{len(odd)} species have neither 1 nor 5 rank entries, e.g. "
            f"{odd[:5]} — the rank index may not be the condenser rank"
        )
    return problems


def unscaled(data: dict) -> list[str]:
    """Species whose partner skill does not scale with condenser rank."""
    return sorted(k for k, v in data["species"].items()
                  if v.get("ranks") and len(v["ranks"]) == 1)


def main() -> int:
    data = build()
    problems = verify(data)
    if problems:
        for line in problems:
            print(f"REFUSING: {line}", file=sys.stderr)
        return 2

    ids = {s for entry in data["species"].values()
           for rank in entry.get("ranks") or [] for s in rank}
    with_ranks = sum(1 for v in data["species"].values() if v.get("ranks"))
    with_active = sum(1 for v in data["species"].values() if v.get("active"))

    flat = unscaled(data)

    if "--verify" in sys.argv:
        print(f"verified: all {len(ids)} partner-skill ids resolve in "
              "passive_effects.json.gz, and every species has 5 rank entries "
              f"or 1 ({len(flat)} of the latter)")
        return 0

    write_json(OUT, data)
    print(f"wrote {OUT}")
    print(f"  {len(data['species'])} species, {with_ranks} with rank-indexed "
          f"partner skills, {with_active} with a ride action")
    print(f"  {len(ids)} distinct skill ids, all resolving in "
          "passive_effects.json.gz — this bundle is a MAPPING and describes "
          "no effect of its own")
    if flat:
        print(f"  {len(flat)} species carry ONE rank entry rather than five "
              f"({', '.join(flat)}): an on/off effect with nothing to scale, "
              "not a different meaning for the index")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
