"""
Per-field validation schema for the general save editor (Phase 7).

This is the foundation the editor is built on, and it comes first deliberately.
The write path has been proven twice over (sorting, container import); what has
never existed is a statement of *which values are legal*. Without that, "edit a
player" means "write whatever the caller sent", and a world the game refuses to
load is the one failure this project exists to prevent.

WHERE THE BOUNDS COME FROM
--------------------------
Every limit here is derived from bundled data or measured against the reference
world. None are invented, and the module refuses to guess:

- **Max level (80)** — the one number here that is NOT derived, and the one that
  caught a real bug. `palExpTable` has 100 entries, so deriving the cap from it
  gave 100; Palworld 1.0 actually raised the cap from 65 to **80** and the table
  merely carries headroom. See `MAX_LEVEL` below.
- **EXP bands** — `TotalEXP` / `PalTotalEXP` per level, from that same table.
  This gives a real cross-field rule: an EXP value must belong to the level it
  is stored with, or the game recomputes the level and the edit silently undoes
  itself.
- **Technology points (1,413) and ancient (185)** — `gamedata.totals()`.
- **Passive skills** — the 1,905 known ids; unknown ones are rejected.
- **Species** — the 753 known Pal forms.
- **IVs 0-100, rank 1-5, max 4 passives** — measured across 1,905 real Pals.

`Talent_Melee` is deliberately absent. `parser._TALENTS` still lists it, but it
appears on **zero** of the 1,905 Pals in the reference world — Palworld 1.0 has
HP, Shot and Defense only. Offering it would write a field the game does not
read, which looks like a working edit and is not.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not write, and it does not read save files. It validates a proposed
change set against bounds and against the current values. Applying comes later,
through `guarded_save_write`, the same as everything else.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Optional

import gamedata

# Measured across 1,905 Pals in the reference world; these are the game's caps,
# not observations that happened to be the maximum.
MAX_IV = 100
MIN_RANK = 1
MAX_RANK = 5
MAX_PASSIVES = 4
MAX_NICKNAME = 32

# Equipped active moves. Measured, not assumed: across 1,905 Pals the
# `EquipWaza` list holds 3 at most (1,221 Pals), and never more.
MAX_EQUIPPED_SKILLS = 3

# `EquipWaza` stores moves as an EnumProperty with this prefix — every one of
# the 216 distinct values on the reference world carries it. The API speaks bare
# ids, because that is what the bundled `activeSkills` table is keyed by, and
# `charedit` re-attaches the prefix on write.
WAZA_PREFIX = "EPalWazaID::"

# The IVs Palworld 1.0 actually stores. See the module docstring.
IV_FIELDS = ("hp", "shot", "defense")

GENDERS = ("Male", "Female")


class SchemaError(Exception):
    """Raised for an unknown target type or field."""


# The playable level cap, which is NOT the size of the EXP table.
#
# `palExpTable` has 100 entries, and deriving the cap from it gave 100 — wrong.
# Palworld 1.0 (10 July 2026) raised the cap from 65 to **80**; the table simply
# carries headroom past the cap. The reference world agrees: the highest player
# is 71 and the highest Pal 70, neither near 100.
#
# This number is not in `refs/` in any form, so it is a documented constant
# rather than a derived one, and it is overridable for a future cap raise.
# 80 is a **community-sourced figure, not one read from the game files.**
#
# There is no level cap in `DefaultPalWorldSettings.ini` and no datatable
# carrying one, so unlike almost everything else here it cannot be verified
# against the install. The bundled EXP table has 100 rows, which `_max_level`
# treats as headroom rather than as the cap. Highest actually observed on the
# reference world: **71** (a player) and 70 (a Pal) — consistent with 80, but
# consistent with more.
#
# The environment override exists precisely because this is the one bound here
# that is not derivable. If a server reports a higher level than this, raise it
# rather than assuming the save is wrong.
MAX_LEVEL = int(os.environ.get("PALWORLD_MAX_LEVEL", "80"))


def _max_level() -> int:
    """
    The lower of the playable cap and what the EXP table can actually express.

    Taking the minimum means a bundled table that is somehow shorter than the
    cap cannot produce a level with no EXP band to validate against.
    """
    table = gamedata.load().get("palExpTable") or {}
    levels = [int(k) for k in table if str(k).isdigit()]
    return min(MAX_LEVEL, max(levels)) if levels else MAX_LEVEL


def _exp_band(level: int, key: str) -> tuple[int, Optional[int]]:
    """
    (minimum, maximum) total EXP for a level. Maximum is None at the cap.

    `key` is "TotalEXP" for players or "PalTotalEXP" for Pals — the two curves
    differ, and using the wrong one produces edits the game quietly reverts.
    """
    table = gamedata.load().get("palExpTable") or {}
    this = (table.get(str(level)) or {}).get(key)
    nxt = (table.get(str(level + 1)) or {}).get(key)
    low = int(this) if isinstance(this, (int, float)) else 0
    high = (int(nxt) - 1) if isinstance(nxt, (int, float)) else None
    return low, high


# ─── Field specifications ────────────────────────────────


class Field:
    """One editable field: what it is, and what values it will accept."""

    def __init__(
        self,
        name: str,
        kind: str,
        *,
        label: str,
        minimum: Optional[int] = None,
        maximum: Optional[Callable[[], int] | int] = None,
        choices: Optional[tuple[str, ...]] = None,
        validator: Optional[Callable[[Any], Optional[str]]] = None,
        note: str = "",
    ) -> None:
        self.name = name
        self.kind = kind
        self.label = label
        self.minimum = minimum
        self._maximum = maximum
        self.choices = choices
        self.validator = validator
        self.note = note

    @property
    def maximum(self) -> Optional[int]:
        return self._maximum() if callable(self._maximum) else self._maximum

    def describe(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "label": self.label,
            "min": self.minimum,
            "max": self.maximum,
            "choices": list(self.choices) if self.choices else None,
            "note": self.note,
        }

    def check(self, value: Any) -> Optional[str]:
        """Return a problem description, or None when the value is acceptable."""
        if self.kind == "int":
            # bool is an int in Python and would sail through every range check.
            if not isinstance(value, int) or isinstance(value, bool):
                return "must be a whole number"
            if self.minimum is not None and value < self.minimum:
                return f"must be at least {self.minimum}"
            top = self.maximum
            if top is not None and value > top:
                return f"must be at most {top}"
        elif self.kind == "string":
            if not isinstance(value, str):
                return "must be text"
            if len(value) > MAX_NICKNAME:
                return f"must be {MAX_NICKNAME} characters or fewer"
        elif self.kind == "enum":
            if value not in (self.choices or ()):
                return f"must be one of: {', '.join(self.choices or ())}"
        elif self.kind == "list":
            if not isinstance(value, list):
                return "must be a list"
        elif self.kind == "float":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return "must be a number"
            if self.minimum is not None and value < self.minimum:
                return f"must be at least {self.minimum}"
            if self.maximum is not None and value > self.maximum:
                return f"must be at most {self.maximum}"
        elif self.kind == "bool":
            if not isinstance(value, bool):
                return "must be true or false"
        elif self.kind == "clear":
            # A `clear` field has exactly one legal request: remove it. There is
            # no healthy value to write — a Pal that is not sick has no
            # `WorkerSick` property at all — so accepting anything else would be
            # accepting an instruction this writer cannot carry out.
            if value is not None:
                return "this field can only be cleared; send null"

        return self.validator(value) if self.validator else None


def _passives_problem(value: Any) -> Optional[str]:
    if not isinstance(value, list):
        return "must be a list"
    if len(value) > MAX_PASSIVES:
        return f"a Pal can hold at most {MAX_PASSIVES} passive skills"
    if len(set(value)) != len(value):
        return "duplicate passive skills"
    unknown = [p for p in value if not isinstance(p, str) or not gamedata.describe_passive(p)["known"]]
    if unknown:
        return f"unknown passive skill(s): {', '.join(str(u) for u in unknown[:3])}"
    return None


def strip_waza_prefix(value: str) -> str:
    """`EPalWazaID::PowerShot` -> `PowerShot`. Idempotent."""
    text = str(value)
    return text[len(WAZA_PREFIX):] if text.startswith(WAZA_PREFIX) else text


def _active_skills_problem(value: Any) -> Optional[str]:
    if not isinstance(value, list):
        return "must be a list"
    if len(value) > MAX_EQUIPPED_SKILLS:
        return f"a Pal can equip at most {MAX_EQUIPPED_SKILLS} active skills"

    bare = [strip_waza_prefix(v) for v in value if isinstance(v, str)]
    if len(bare) != len(value):
        return "every active skill must be text"
    if len(set(bare)) != len(bare):
        return "duplicate active skills"

    unknown = [b for b in bare if not gamedata._lookup("activeSkills", b)]
    if unknown:
        return f"unknown active skill(s): {', '.join(unknown[:3])}"
    return None


def _mastered_skills_problem(value: Any) -> Optional[str]:
    """
    The learned-move pool. Unlike `EquipWaza` there is no size cap to enforce —
    the live world has a Pal with six — so this checks only that every entry is
    a move the game knows.
    """
    if not isinstance(value, list):
        return "must be a list"
    bare = [strip_waza_prefix(v) for v in value if isinstance(v, str)]
    if len(bare) != len(value):
        return "every learned move must be text"
    if len(set(bare)) != len(bare):
        return "duplicate learned moves"
    unknown = [b for b in bare if not gamedata._lookup("activeSkills", b)]
    if unknown:
        return f"unknown move(s): {', '.join(unknown[:3])}"
    return None


def _species_problem(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value:
        return "must be a species id"
    if not gamedata.pal(value):
        return f"unknown species {value!r}"
    return None


PLAYER_FIELDS: dict[str, Field] = {
    "nickname": Field("nickname", "string", label="Name"),
    "level": Field("level", "int", label="Level", minimum=1, maximum=_max_level),
    "exp": Field("exp", "int", label="Experience", minimum=0),
    "technologyPoints": Field(
        "technologyPoints", "int", label="Technology points", minimum=0,
        maximum=lambda: int(gamedata.totals().get("technologyPoints") or 0),
        note="Total earnable across all 537 technologies.",
    ),
    "ancientTechnologyPoints": Field(
        "ancientTechnologyPoints", "int", label="Ancient technology points", minimum=0,
        maximum=lambda: int(gamedata.totals().get("ancientTechnologyPoints") or 0),
    ),
}

PAL_FIELDS: dict[str, Field] = {
    "nickname": Field("nickname", "string", label="Nickname"),
    "level": Field("level", "int", label="Level", minimum=1, maximum=_max_level),
    "exp": Field("exp", "int", label="Experience", minimum=0),
    "rank": Field(
        "rank", "int", label="Condenser rank (1 = no stars, 5 = ★★★★)",
        minimum=MIN_RANK, maximum=MAX_RANK,
        note="The save counts from 1, the game shows stars from 0 — so rank 5 is "
             "a four-star Pal, not five. Rank 5 is real: one Pal on the reference "
             "world has it. The game recomputes HP/Attack/Defense from this at "
             "load, so raising it does raise the stats in game; the multipliers "
             "themselves are in no data this project bundles, which is why the "
             "new values are not previewed here.",
    ),
    "gender": Field("gender", "enum", label="Gender", choices=GENDERS),
    "speciesId": Field("speciesId", "string", label="Species", validator=_species_problem),
    "passiveSkills": Field(
        "passiveSkills", "list", label="Passive skills", validator=_passives_problem,
        note=f"At most {MAX_PASSIVES}, no duplicates, each must be a known skill.",
    ),
    "activeSkills": Field(
        "activeSkills", "list", label="Active skills", validator=_active_skills_problem,
        note=f"The moves this Pal has equipped. At most {MAX_EQUIPPED_SKILLS}, "
             "no duplicates. Moves it has learned but not equipped are not editable.",
    ),
    **{
        f"ivs.{iv}": Field(
            f"ivs.{iv}", "int", label=f"IV — {iv.upper()}", minimum=0, maximum=MAX_IV,
        )
        for iv in IV_FIELDS
    },
    # ── Condition ────────────────────────────────────────────────
    #
    # The three afflictions are `clear` fields: there is no healthy value to
    # write, because a healthy Pal has no such property at all. See
    # `charedit.PAL_CLEARABLE` — curing is a deletion, and inflicting is not
    # offered.
    "workerSick": Field(
        "workerSick", "clear", label="Sickness",
        note="Clearing removes the property, which is exactly what a Pal that "
             "was never sick looks like. Observed on 54 of the live world's "
             "2,963 Pals — depression, sprains, fractures, weakness, bulimia.",
    ),
    "physicalHealth": Field(
        "physicalHealth", "clear", label="Injury",
        note="Minor or severe. Clearing heals it.",
    ),
    "hungerType": Field(
        "hungerType", "clear", label="Hunger status",
        note="Hunger or starvation. Clear this AND raise Fullness, or the game "
             "will simply put it back at the next tick.",
    ),
    "sanity": Field(
        "sanity", "float", label="Sanity (SAN)", minimum=0, maximum=100,
        note="The game shows this as the Pal's mood. Below about 50 it starts "
             "refusing to work; at zero it panics and runs off.",
    ),
    "fullStomach": Field(
        "fullStomach", "float", label="Fullness", minimum=0,
        note="No maximum is enforced because the ceiling is per species and per "
             "level and is not stored anywhere in the save — the live world "
             "ranges from 150 to 620. The game clamps an overshoot itself.",
    ),
    "favoriteIndex": Field(
        "favoriteIndex", "int", label="Favourite slot", minimum=0, maximum=9,
        note="The player's own favourites marking. 0 is not a favourite.",
    ),
    "skinName": Field(
        "skinName", "string", label="Applied skin",
        note="Editable only on a Pal that already has a skin applied — the save "
             "has no property to write into otherwise.",
    ),
    "isImported": Field(
        "isImported", "bool", label="Imported from another save",
        note="Set by the game on a Pal brought in from elsewhere. 136 of the "
             "live world's Pals carry it, which is why it is an advisory rather "
             "than evidence of anything.",
    ),
    "masteredSkills": Field(
        "masteredSkills", "list", label="Learned moves",
        validator=_mastered_skills_problem,
        note="The move pool this Pal has learned, as opposed to the three it has "
             "equipped. Editable only where the save already carries the "
             "property, which is 738 of the live world's 2,963 Pals.",
    ),
}

TARGETS = {"player": PLAYER_FIELDS, "pal": PAL_FIELDS}


def fields_for(target: str) -> dict[str, Field]:
    if target not in TARGETS:
        raise SchemaError(f"Unknown target {target!r}. Known: {', '.join(sorted(TARGETS))}")
    return TARGETS[target]


def describe(target: str) -> list[dict]:
    """The schema, for a UI to render an editor from."""
    return [field.describe() for field in fields_for(target).values()]


def exp_for_level(target: str, level: int) -> int:
    """
    The lowest EXP that sits at `level` — i.e. "just reached it".

    Bulk edits need this. Setting 200 Pals to level 50 without also moving their
    EXP means the game recomputes every one of them back to whatever their EXP
    says, so a bulk level change that does not carry EXP with it does nothing at
    all. Taking the band *minimum* is the conservative choice: it grants the
    level and not a single point of progress towards the next.
    """
    key = "TotalEXP" if target == "player" else "PalTotalEXP"
    return _exp_band(level, key)[0]


def exp_bands(target: str) -> dict[str, list[Optional[int]]]:
    """
    `{level: [minimum, maximum]}` for every level up to the cap.

    Exposed so an editor can offer the right EXP when someone changes a level,
    rather than letting them submit an inconsistent pair and bounce off the
    cross-field rule. 80 levels is a trivial payload and keeps one source of
    truth — the UI must not carry its own copy of the curve.
    """
    key = "TotalEXP" if target == "player" else "PalTotalEXP"
    return {
        str(level): list(_exp_band(level, key))
        for level in range(1, _max_level() + 1)
    }


# ─── Cross-field rules ───────────────────────────────────


def _check_exp_matches_level(target: str, merged: dict) -> list[dict]:
    """
    EXP must not be *beyond* the level it is stored with.

    THE RULE IS ONE-SIDED, AND THE MEASUREMENT IS WHY
    -------------------------------------------------
    This started as a symmetric rule — EXP had to sit inside its level's band —
    on the reasoning that the game derives level from total EXP on load. Checked
    against the reference world, that is only half true:

        above the band:  0 of 1,905 Pals, 0 of 5 players
        below the band:  8 Pals (levels 4-11, freshly caught)
        inside:          1,897 Pals, 5 players

    A caught Pal arrives at its wild level with almost no EXP, and the game
    leaves it there. So low EXP is a state the game itself produces and keeps —
    rejecting it would refuse an edit for being in a condition Palworld creates
    on its own, and flag eight legitimate Pals on a clean world.

    High EXP is the opposite: it never occurs naturally, and it is the direction
    the game acts on. Writing it means the operator asks for level 20 and gets
    something else on next load — an edit that appears to work and does not.
    That half stays a hard rejection.
    """
    level = merged.get("level")
    exp = merged.get("exp")
    if not isinstance(level, int) or not isinstance(exp, int):
        return []

    key = "TotalEXP" if target == "player" else "PalTotalEXP"
    _low, high = _exp_band(level, key)
    if high is not None and exp > high:
        return [{
            "field": "exp",
            "problem": f"{exp:,} EXP is beyond level {level} (it ends at {high:,}); "
                       f"the game would recalculate this character as a higher level",
        }]
    return []


CROSS_FIELD_RULES = (_check_exp_matches_level,)


# ─── Validation ──────────────────────────────────────────


def validate(target: str, changes: dict, current: Optional[dict] = None) -> dict:
    """
    Check a proposed change set. Pure; returns a report.

    `current` is the object as it exists now, and is needed for the cross-field
    rules: changing only `level` still has to agree with the `exp` already
    stored. Without it those rules are skipped rather than guessed at, and the
    report says so.
    """
    fields = fields_for(target)

    if not isinstance(changes, dict) or not changes:
        return {"ok": False, "problems": [{"field": None, "problem": "No changes supplied"}],
                "changes": {}, "crossFieldChecked": False}

    problems: list[dict] = []
    accepted: dict[str, Any] = {}

    for name, value in changes.items():
        field = fields.get(name)
        if field is None:
            problems.append({
                "field": name,
                "problem": f"{name!r} is not an editable field. Editable: "
                           f"{', '.join(sorted(fields))}",
            })
            continue
        issue = field.check(value)
        if issue:
            problems.append({"field": name, "problem": f"{field.label} {issue}"})
            continue
        accepted[name] = value

    cross_checked = False
    if current is not None and not problems:
        merged = {**_flatten(current), **accepted}
        for rule in CROSS_FIELD_RULES:
            problems.extend(rule(target, merged))
        cross_checked = True

    return {
        "ok": not problems,
        "problems": problems,
        "changes": accepted if not problems else {},
        "crossFieldChecked": cross_checked,
    }


def _flatten(obj: dict) -> dict:
    """`{"ivs": {"hp": 1}}` -> `{"ivs.hp": 1}`, matching the field names."""
    flat: dict[str, Any] = {}
    for key, value in (obj or {}).items():
        if isinstance(value, dict):
            for inner, inner_value in value.items():
                flat[f"{key}.{inner}"] = inner_value
        else:
            flat[key] = value
    return flat


def diff(target: str, changes: dict, current: dict) -> list[dict]:
    """
    Per-field before/after, for a preview. Unchanged fields are omitted.

    Validation is the caller's job — this only describes.
    """
    fields = fields_for(target)
    flat = _flatten(current)

    out: list[dict] = []
    for name, value in changes.items():
        before = flat.get(name)
        if before == value:
            continue
        field = fields.get(name)
        out.append({
            "field": name,
            "label": field.label if field else name,
            "before": before,
            "after": value,
        })
    return out
