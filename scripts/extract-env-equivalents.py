#!/usr/bin/env python3
"""
Per-key env-var equivalents for the two Palworld server images.

**The mapping comes from each image's own INI template — the file its
container actually envsubst's on boot** — not from README tables, which drift.
Every line of those templates is literally `IniKey=$ENV_NAME` (thijsvanloef)
or `IniKey=${ENV_NAME}` (jammsen), so parsing them IS the ground truth, and a
key absent from a template is a fact worth as much as a mapping: on a
regenerating deployment, that key resets to the image's default on every
start and has NO env var to reach for.

Why this exists: a thijsvanloef deployment regenerates PalWorldSettings.ini
from env on every boot (unless DISABLE_GENERATE_SETTINGS=true), so a dashboard
INI write survives only until the next restart. The honest advice per key is
"set THIS variable in your compose file instead" — which needs this mapping.
`iniwatch` detects the revert after the fact; this tells the operator the fix.

Build-time fetch, bundled output, never fetched at runtime — the same rule as
settings_help. Sources:

  https://github.com/thijsvanloef/palworld-server-docker
      scripts/files/PalWorldSettings.ini.template
  https://github.com/jammsen/docker-palworld-dedicated-server
      configs/PalWorldSettings.ini.template

Usage:
    python3 scripts/extract-env-equivalents.py            # fetch + write bundle
    python3 scripts/extract-env-equivalents.py --local A B  # parse local copies
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "backend", "data", "env_equivalents.json")

SOURCES = {
    "thijsvanloef": (
        "https://raw.githubusercontent.com/thijsvanloef/palworld-server-docker/"
        "main/scripts/files/PalWorldSettings.ini.template"
    ),
    "jammsen": (
        "https://raw.githubusercontent.com/jammsen/docker-palworld-dedicated-server/"
        "master/configs/PalWorldSettings.ini.template"
    ),
}

# IniKey=$ENV / IniKey=${ENV} / IniKey="${ENV}" / IniKey=\"$ENV\"
PAIR = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)=\\?"?\$\{?([A-Z][A-Z0-9_]*)\}?')


def parse(text: str) -> dict[str, str]:
    return {key: env for key, env in PAIR.findall(text)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", nargs=2, metavar=("TVL", "JAMMSEN"),
                    help="parse local template copies instead of fetching")
    args = ap.parse_args()

    texts: dict[str, str] = {}
    if args.local:
        texts["thijsvanloef"] = open(args.local[0], encoding="utf-8").read()
        texts["jammsen"] = open(args.local[1], encoding="utf-8").read()
    else:
        for image, url in SOURCES.items():
            with urllib.request.urlopen(url, timeout=30) as r:
                texts[image] = r.read().decode("utf-8")

    maps = {image: parse(text) for image, text in texts.items()}
    for image, mapping in maps.items():
        if len(mapping) < 50:
            # Both templates carry 100+ pairs; a handful means the template
            # moved or the regex stopped matching — refuse rather than commit
            # a bundle that silently advises on 6 keys.
            raise SystemExit(
                f"!! {image}: only {len(mapping)} pairs parsed — refusing")

    keys = sorted(set(maps["thijsvanloef"]) | set(maps["jammsen"]))
    payload = {
        "_comment": [
            "IniKey -> env var per server image, parsed from each image's own",
            "INI template (the file its container envsubst's on boot).",
            "A key absent for an image means that image has NO env var for it:",
            "on a regenerating deployment it resets to the default every start.",
        ],
        "sources": {img: SOURCES[img] for img in maps},
        "fetched": datetime.date.today().isoformat(),
        "keys": {
            key: {img: maps[img][key] for img in maps if key in maps[img]}
            for key in keys
        },
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
        f.write("\n")

    both = sum(1 for k in keys if len(payload["keys"][k]) == 2)
    print(f"wrote {OUT}")
    print(f"  thijsvanloef: {len(maps['thijsvanloef'])} keys, "
          f"jammsen: {len(maps['jammsen'])} keys, "
          f"union {len(keys)}, both {both}")
    only_tvl = sorted(set(maps["thijsvanloef"]) - set(maps["jammsen"]))
    only_jam = sorted(set(maps["jammsen"]) - set(maps["thijsvanloef"]))
    if only_tvl:
        print(f"  only thijsvanloef: {', '.join(only_tvl[:8])}"
              + (" …" if len(only_tvl) > 8 else ""))
    if only_jam:
        print(f"  only jammsen: {', '.join(only_jam[:8])}"
              + (" …" if len(only_jam) > 8 else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
