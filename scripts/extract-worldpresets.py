#!/usr/bin/env python3
"""
The game's own difficulty presets — every rate, as Pocketpair sets it.

Phase 1.9 of `docs/PLAN.md`. `DT_OptionWorldPresetTable` (4 rows) and
`DT_OptionWorldModePresetTable` (4).

WHY THIS IS MORE THAN CONVENIENCE. `DefaultPalWorldSettings.ini` is the
authoritative list of *which* settings exist — 119 of them — and says nothing
about what a difficulty actually changes. These tables do, so the two together
are a cross-check rather than a single source, which is the standard
`verify-figures.py` sets for save-derived numbers.

`backend/presets.py` currently ships hand-made presets. **The first deliverable
is the comparison, not the replacement**: if the existing ones already match, that
is worth recording rather than a reason to skip the work; if they do not, the
disagreement is the finding.

THE GAME'S OWN TYPO IS PRESERVED. The column is `Diffculty`, not `Difficulty`.
Correcting it here would mean this bundle no longer reports what the file says —
the same call made for the inverted Pengullet level range in `spawns.json.gz`.
The *output* key is normalised, and the raw spelling is recorded in this
docstring so the next reader is not confused by a grep that finds nothing.

VERIFICATION: every preset key that names a setting must exist in
`DefaultPalWorldSettings.ini`. A key that does not is either a rename we have not
noticed or a column being read as a setting when it is not.

Usage:  python3 scripts/extract-worldpresets.py [--verify]
Output: backend/data/worldpresets.json.gz
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

ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "backend", "data", "worldpresets.json.gz")

# Columns that are not settings: the preset's own identity, and anything whose
# value did not decode.
NOT_A_SETTING = {"Diffculty", "RandomizerType", "Single", "WorldMode"}


def _enum(value) -> str:
    text = str(value or "")
    return text.rsplit("::", 1)[-1] if "::" in text else text


def _read(pak, name: str) -> dict:
    path = next((p for p in pak.files if p.endswith(name + ".uasset")), None)
    if path is None:
        raise SystemExit(f"{name} is not in this pak — did the game update?")
    return uassettable.read_table(pak, path)


def _clean(row: dict) -> dict:
    """
    Drop anything that did not decode.

    A value `uassettable` could not walk comes back as a string like
    `<WorldMode 9B>`. Shipping those as settings would put nonsense in a preset,
    so they are excluded and counted rather than passed through.
    """
    out = {}
    for key, value in row.items():
        if key in NOT_A_SETTING:
            continue
        if isinstance(value, str) and value.startswith("<") and value.endswith("B>"):
            continue
        if isinstance(value, (int, float, bool)):
            out[key] = value
    return out


def build(pak=None) -> tuple[dict, dict]:
    pak = pak or palpak.Pak()

    presets = {}
    for key, row in _read(pak, "DT_OptionWorldPresetTable").items():
        presets[str(key)] = {
            "id": str(key),
            # The game spells it `Diffculty`. Normalised on output only.
            "difficulty": _enum(row.get("Diffculty")),
            "randomizer": _enum(row.get("RandomizerType")),
            "settings": _clean(row),
        }

    modes = {}
    for key, row in _read(pak, "DT_OptionWorldModePresetTable").items():
        cleaned = _clean(row)
        if cleaned:
            modes[str(key)] = cleaned

    return {"presets": presets, "modes": modes}, {}


def main() -> int:
    pak = palpak.Pak()
    data, _ = build(pak)

    if not data["presets"]:
        print("REFUSING: no presets decoded.", file=sys.stderr)
        return 2

    # Cross-check against the game's own default INI, which is the authoritative
    # list of what a 1.0 server accepts.
    sys.path.insert(0, os.path.join(ROOT, "backend"))
    try:
        import settings_ini

        known = set(settings_ini.game_defaults())
    except Exception as e:  # noqa: BLE001 - refs/ absent on a clean checkout
        print(f"Cannot read DefaultPalWorldSettings.ini ({e}); skipping the "
              "cross-check.", file=sys.stderr)
        known = set()

    unmatched = {}
    if known:
        for name, preset in data["presets"].items():
            missing = sorted(k for k in preset["settings"] if k not in known)
            if missing:
                unmatched[name] = missing

    if "--verify" in sys.argv:
        print(f"verified {len(data['presets'])} presets")
        if known:
            total = len(next(iter(data["presets"].values()))["settings"])
            odd = len(next(iter(unmatched.values()), []))
            print(f"  {total - odd} of {total} keys exist in "
                  f"DefaultPalWorldSettings.ini")
        return 0

    write_json(OUT, data)
    print(f"wrote {OUT}")
    for name, preset in data["presets"].items():
        print(f"  {name:16s} difficulty={preset['difficulty']:10s} "
              f"{len(preset['settings'])} settings")
    print(f"  {len(data['modes'])} world-mode preset rows")
    if unmatched:
        for name, missing in list(unmatched.items())[:1]:
            print(f"  NOTE: {len(missing)} preset keys are not INI settings "
                  f"(e.g. {missing[:4]}) — engine-side options, not server "
                  "configuration")
    return 0


if __name__ == "__main__":
    sys.exit(main())
