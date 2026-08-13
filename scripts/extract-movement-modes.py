#!/usr/bin/env python3
"""
Bundle `EPalMonsterMovementType` — whether a Pal flies, swims or walks.

AGENTS.md recorded this as unavailable across five checked avenues, and the
conclusion was drawn from a sixth that was never tried: a search for `BP_Pal_*`
found nothing, and the game names its species blueprints `BP_<Species>`.

    Pal/Content/Pal/Blueprint/Character/Monster/PalActorBP/<Species>/BP_<Species>

1,831 assets, in the SERVER pak, therefore tagged and decodable. The value sits
on a component export rather than the actor CDO:

    BP_BirdDragon -> StaticCharacterParameterComponent
                       MovementType = EPalMonsterMovementType::Fly

## Two denominators, and confusing them is the "159 field bosses" mistake

**31 of the 772 `BP_<Species>` files override it**; everything else inherits.
Resolved onto the species ids the bundle uses, that is **52 non-ground species**
— because a `BOSS_` form has no blueprint of its own and inherits its base's
mode, so one file can cover two ids.

## What is read, and what is inferred

Read: the 31 overrides. Inferred: that the native default is `GroundOnly`.
Nothing states it. The inference rests on the overrides being *exactly* the
non-walkers — Melpaca and every other walking Pal declines to override — and it
is the one soft spot here, so `defaultIsInferred` travels in the bundle.

## The control is two pairs the game overrides the other way

`Serpent` (Surfent) is `Swim` and `Serpent_Ground` (Surfent Terra) is explicitly
reset to `GroundOnly`; `Umihebi` (Jormuntide) is `Swim` and `Umihebi_Fire`
(Jormuntide Ignis) explicitly `GroundOnly`. A field that merely correlated with
something would not have the land variants of two swimmers individually reset.
`verify()` refuses the build if that stops holding.
"""

from __future__ import annotations

import collections
import gzip
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import palpak            # noqa: E402
import uassettable       # noqa: E402
import upackage          # noqa: E402
from jsonout import write_json  # noqa: E402

BASE = "../../../Pal/Content/Pal/Blueprint/Character/Monster/PalActorBP/"
OUT = os.path.join(os.path.dirname(HERE), "backend", "data", "movement_modes.json.gz")
GAMEDATA = os.path.join(os.path.dirname(HERE), "backend", "data", "gamedata.json.gz")

SPECIES_BP = re.compile(r"/BP_([^/]+)\.uasset$")
COMPONENT = "StaticCharacterParameterComponent"
ENUM = "EPalMonsterMovementType"

DEFAULT_MODE = "GroundOnly"
AIRBORNE = {"Fly", "FlyAndLanding"}

TOLERANCE = 8

#: The control. Each land variant must be explicitly reset while its base swims.
CONTROL = [("Serpent", "Swim"), ("Serpent_Ground", "GroundOnly"),
           ("Umihebi", "Swim"), ("Umihebi_Fire", "GroundOnly")]

#: Known flyers, as an independent check that the field means what it says.
KNOWN_FLYERS = ["BirdDragon", "HawkBird", "Eagle", "JetDragon", "IceHorse",
                "BlackGriffon", "RedArmorBird", "HadesBird", "Horus",
                "ThunderBird", "SkyDragon", "BlackMetalDragon"]

#: Known GROUND mounts. These must NOT appear — Necromus and Paladius are fast
#: ground legendaries, and a rule that swept them in would be wrong.
KNOWN_GROUND = ["Alpaca", "BlackCentaur", "SaintCentaur", "CaptainPenguin"]


class MovementError(Exception):
    """Raised when the decode or the control does not hold."""


def _walk(body, names) -> tuple[dict, int]:
    reader = uassettable._Reader(body, names)
    props: dict = {}
    while reader.o < len(body):
        try:
            tag = uassettable._tag(reader)
        except Exception:
            break
        if tag is None:
            break
        name, typ, size, extra = tag
        start = reader.o
        try:
            value = uassettable._value(reader, typ, size, extra)
        except Exception:
            value = None
        if typ != "BoolProperty":
            reader.o = start + size
        props[name] = value
    return props, reader.o


def extract(pak=None) -> tuple[dict[str, str], dict]:
    pak = pak or palpak.Pak()
    assets = sorted(f for f in pak.files
                    if f.startswith(BASE) and SPECIES_BP.search(f))

    modes: dict[str, str] = {}
    misaligned = 0
    for asset in assets:
        stem = SPECIES_BP.search(asset).group(1)
        try:
            package = upackage.read(pak.read(asset))
            uexp = pak.read(asset[:-7] + ".uexp")
        except Exception:
            continue
        for export in package.exports:
            if COMPONENT not in export.name:
                continue
            body = export.data(uexp)
            props, ended = _walk(body, package.names)
            # A component walk that did not land at the end is not trusted for
            # its VALUES, even though position is restored for the tag stream —
            # AGENTS.md's "the walk ends at the buffer end proves alignment, not
            # values". Skipped and counted rather than read.
            if len(body) - ended > TOLERANCE:
                misaligned += 1
                break
            value = props.get("MovementType")
            if isinstance(value, str) and value.startswith(f"{ENUM}::"):
                modes[stem] = value.split("::")[-1]
            break

    return modes, {"blueprints": len(assets), "overrides": len(modes),
                   "misaligned": misaligned}


def verify(modes: dict[str, str]) -> list[str]:
    """Control failures. Empty is good."""
    problems = []
    for stem, expected in CONTROL:
        got = modes.get(stem, DEFAULT_MODE)
        if got != expected:
            problems.append(f"control {stem}: expected {expected}, got {got}")
    for stem in KNOWN_FLYERS:
        if modes.get(stem) not in AIRBORNE:
            problems.append(f"known flyer {stem} reads {modes.get(stem)!r}")
    for stem in KNOWN_GROUND:
        if stem in modes:
            problems.append(f"known ground Pal {stem} carries an override: {modes[stem]!r}")
    return problems


def _species_ids() -> list[str]:
    with gzip.open(GAMEDATA, "rt", encoding="utf-8") as f:
        return sorted((json.load(f).get("pals") or {}))


def resolve(modes: dict[str, str], species: list[str]) -> dict[str, str]:
    """
    Species id -> mode, with variant inheritance applied.

    **`BOSS_HawkBird` has no blueprint and INHERITS Nitewing's `Fly`.** Reading
    the override table raw calls every alpha flyer a ground Pal — `pal_exact`'s
    lesson, one asset type over.
    """
    out: dict[str, str] = {}
    for species_id in species:
        base = species_id[5:] if species_id.startswith("BOSS_") else species_id
        for key in (species_id, base, f"{base}_Normal"):
            if key in modes:
                out[species_id] = modes[key]
                break
        else:
            out[species_id] = DEFAULT_MODE
    return out


def main() -> int:
    try:
        modes, stats = extract()
    except Exception as e:  # noqa: BLE001
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2

    problems = verify(modes)
    if problems:
        for p in problems:
            print(f"CONTROL FAILED: {p}", file=sys.stderr)
        return 3

    resolved = resolve(modes, _species_ids())
    counts = collections.Counter(resolved.values())

    data = {
        # Only the species that are NOT the default. Shipping 701 GroundOnly
        # entries would triple the bundle to restate an inference.
        "species": {k: v for k, v in sorted(resolved.items()) if v != DEFAULT_MODE},
        "default": DEFAULT_MODE,
        # **The one thing here that is not read from the game.** See the module
        # docstring: nothing states the native default.
        "defaultIsInferred": True,
        "defaultNote": (
            "No file states the native default. It is inferred from the 31 "
            "overrides being exactly the non-walking Pals, and from every known "
            "ground mount declining to override."
        ),
        "modes": sorted({*AIRBORNE, DEFAULT_MODE, "Swim", "SwimGroundDamage"}),
    }

    if "--verify" in sys.argv:
        print(f"verified: {stats['overrides']} overrides of {stats['blueprints']} "
              f"blueprints, controls hold, {len(data['species'])} non-ground species")
        return 0

    write_json(OUT, data)
    print(f"wrote {OUT}")
    print(f"  {stats['overrides']} of {stats['blueprints']} BP_<Species> files "
          f"override MovementType ({stats['misaligned']} walks skipped)")
    print(f"  resolved onto species ids: " + ", ".join(
        f"{k} {v}" for k, v in counts.most_common()))
    print("  controls hold: Serpent/Serpent_Ground and Umihebi/Umihebi_Fire "
          "disagree, and no known ground mount carries an override")
    print("  defaultIsInferred is TRUE — GroundOnly is not a stated value")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
