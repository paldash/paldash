#!/usr/bin/env python3
"""
Bundle the *numeric* effects of every passive skill, from the server pak.

WHY THIS IS SEPARATE FROM `build-gamedata.py`. That script has exactly one
source — `refs/PalWorldSaveTools-main.zip` — and the discipline is worth keeping:
a build with two sources is a build where a disagreement between them has no
obvious owner. This reads the pak instead, the same way `extract-effigies.py`
does, and writes its own bundle.

WHAT THE PST ARCHIVE HAS, AND WHAT IT DOES NOT. It carries each passive's name,
rank, icon and an English *sentence*: "Attack +5%". That is enough to show a
player what a skill does and useless for computing anything, because the number
is prose. `gamedata`'s `passives` section is therefore display-only, and
`palstats.describe` took `passive_bonus` as a caller-supplied float defaulting to
**zero** — which meant every stat the dashboard has ever shown ignored passive
skills entirely.

`DT_PassiveSkill_Main` decodes completely out of the SERVER pak (tagged
properties — see AGENTS.md), giving per-skill:

    EffectType1..4   e.g. EPalPassiveSkillEffectType::ShotAttack
    EffectValue1..4  e.g. 20.0        (a percentage)
    TargetType1..4   ToSelf / ToTrainer / ToBaseCamp ...
    Invoke*          when it applies: always, as a worker, while riding ...

**A passive's bonus is per stat, not one number.** `Legend` is ShotAttack +20,
Defense +20 and MoveSpeed +20 together; `Noukin` is ShotAttack +30 and CraftSpeed
**-50**. Anything that folds these into a single multiplier is wrong for at least
one stat of every multi-effect skill, and there are hundreds.

**The invoke flags are not decoration.** A skill with `InvokeWorker` applies to a
Pal working at a base and to nothing else, so applying it to a party Pal's attack
is a confidently wrong number. They travel with the effects so a caller can ask
the question rather than assume the answer.

Usage:  python3 scripts/extract-passive-effects.py
Output: backend/data/passive_effects.json.gz
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import palpak            # noqa: E402
import uassettable       # noqa: E402
from jsonout import write_json  # noqa: E402

TABLE = "../../../Pal/Content/Pal/DataTable/PassiveSkill/DT_PassiveSkill_Main.uasset"
OUT = os.path.join(os.path.dirname(HERE), "backend", "data", "passive_effects.json.gz")

# The four effect slots every row carries, whether or not it uses them.
SLOTS = (1, 2, 3, 4)

# `EPalPassiveSkillEffectType::no` is the game's own "this slot is unused".
# Empty and the literal string "None" are treated the same way rather than
# becoming an effect named "None".
UNUSED = {"", "no", "None"}

INVOKE_FLAGS = (
    "InvokeAlways",
    "InvokeActiveOtomo",
    "InvokeWorker",
    "InvokeRiding",
    "InvokeReserve",
    "InvokeInOtomo",
    "InvokeInBaseCamp",
)


def _bare(value: object) -> str:
    """`EPalPassiveSkillEffectType::ShotAttack` -> `ShotAttack`."""
    return str(value or "").split("::")[-1]


def build() -> dict:
    pak = palpak.Pak()
    rows = uassettable.read_table(pak, TABLE)

    out: dict[str, dict] = {}
    for name, row in rows.items():
        # The table ships eight `TestSkill*` rows — one per effect type, all
        # `SortNotDisplayable`. They are the developers' fixtures, not skills a
        # Pal can roll, and shipping them would put them in every search box.
        if name.startswith("TestSkill"):
            continue

        effects = []
        for slot in SLOTS:
            kind = _bare(row.get(f"EffectType{slot}"))
            if kind in UNUSED:
                continue
            value = float(row.get(f"EffectValue{slot}") or 0.0)
            # A DECLARED SLOT WITH VALUE 0 IS NOT AN EFFECT, and this is what
            # the prose cross-check found. `GrassMinotaur_PartnerSkill_2` reads
            # "Attack +12%" and carries ShotAttack 12.0 *plus* a Defense 0.0 —
            # the slot is wired up and contributes nothing. Keeping it made the
            # skill look like it touched defence.
            if value == 0.0:
                continue
            effects.append({
                "type": kind,
                # A percentage, and signed — a trade-off skill really does carry
                # a negative here and dropping the sign would turn Noukin's -50%
                # craft speed into a bonus.
                "value": float(row.get(f"EffectValue{slot}") or 0.0),
                "target": _bare(row.get(f"TargetType{slot}")),
            })

        if not effects:
            # Real rows with no numeric effect exist — the skill does something
            # the stat system does not express. Recorded with an empty list
            # rather than omitted, so "no effects" is distinguishable from "this
            # skill is not in the bundle".
            pass

        out[name] = {
            "rank": int(row.get("Rank") or 0),
            "effects": effects,
            # When it applies. See the module docstring: a worker-only skill
            # applied to a party Pal is a confidently wrong number.
            "invoke": [f for f in INVOKE_FLAGS if row.get(f) is True],
            # Drop weight — how likely this is to roll. Useful for saying which
            # passives are actually attainable rather than merely defined.
            "lotteryWeight": int(row.get("LotteryWeight") or 0),
            "element": _bare(row.get("TargetElementType")) or "",
        }

        # `AddMutationPal` — the game's own flag, true on exactly five rows.
        # Carried only when set, so its absence stays the ordinary case rather
        # than a `false` on 1,900 entries.
        #
        # **Four of the five have ids beginning `MutationPal_`; Skymarcher's is
        # `RideJumpCount_Increase2`.** That mismatch is what makes this a data
        # column worth reading rather than a rule anybody could have derived
        # from the naming, and it is why the flag is read instead of the prefix.
        #
        # WHAT IT MEANS IS NOT CLAIMED HERE. "These can appear on a mutated Pal"
        # fits the ids and the binary's `MutationPalAssignableSkillMap`, and no
        # file states it. Callers must present the flag, never a drop rate.
        if row.get("AddMutationPal") is True:
            out[name]["addMutationPal"] = True

    return out


def main() -> int:
    try:
        data = build()
    except Exception as e:  # noqa: BLE001 - report, do not write a partial bundle
        print(f"Extraction failed: {type(e).__name__}: {e}", file=sys.stderr)
        print(
            "This needs refs/palworld/.../Pal-LinuxServer.pak — the SERVER pak. "
            "The client pak cooks properties unversioned and cannot be decoded.",
            file=sys.stderr,
        )
        return 1

    write_json(OUT, data)

    with_effects = sum(1 for v in data.values() if v["effects"])
    multi = sum(1 for v in data.values() if len(v["effects"]) > 1)
    negative = sum(
        1 for v in data.values() if any(e["value"] < 0 for e in v["effects"])
    )
    print(f"wrote {OUT}")
    print(f"  {len(data)} passives, {with_effects} with numeric effects")
    print(f"  {multi} affect more than one stat — which is why a single float is wrong")
    print(f"  {negative} carry a negative value (trade-off skills)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
