#!/usr/bin/env python3
"""
`DT_AchivementRewardNPC` -> `backend/data/achievements.json.gz`.

    python3 scripts/extract-achievements.py

The game's own milestone system: the NPC who hands out a reward when you pass a
threshold. **Pocketpair's typo is theirs and is kept** — the DataTable and the
save key are both `Achivement`, and correcting it here would break the join.

## THESE ARE NOT STEAM ACHIEVEMENTS

Steam's live on Steam's servers and need `GetPlayerAchievements`, a publisher
key and each player's SteamID — a runtime dependency on an external API, which
this project forbids outright. They are also per-account rather than per-server,
so they would cover only players who handed over a key and would report what
somebody did in single-player too.

This is better for the same question: per-player, offline, available for
**every** player on the server, and true while the server is down. The UI must
not call them Steam achievements.

## The join is exact — the save names the row

Three categories, 26 rows:

| Category | Tiers | Thresholds |
|---|---:|---|
| `PalCapture` | 10 | 100, 200 … 1000 |
| `PalDex` | 10 | 10, 20 … 100 |
| `BossDefeat` | 6 | 5, 10, 20, 30, 50, 100 |

The task that raised this expected the hard part to be matching an
`AchivementCategory` enum to a save counter by name and plausibility. **That
inference is not needed.** `RecordData.NPCAchivementRewardFlag` holds the row
names outright — `PalDex_7`, `BossDefeat_1` — and across the reference world's
five players **26 of 26 claimed keys resolve to a row in this table.**

So "claimed" is read, never guessed. What still needs a counter is the *other*
half: how far along a player is toward a tier they have not claimed.

## Counters, and the one that is NOT settled

- **`PalCapture` -> `PalCaptureCount`.** Name matches the category exactly, and
  the observed range (11 to 1,085 across five players) sits across the 100-1000
  tiers.
- **`PalDex` -> `TribeCaptureCount`.** The game's own count of distinct species
  captured. **This field read as 0 for every player until 2026-08-12** because
  it is a plain int and the parser treated it as a map.
- **`BossDefeat` -> NOT ESTABLISHED, and it is left that way.** No
  `BossDefeatCount` exists. `TowerBossDefeatFlag` maxes at 7 observed against a
  top tier of **100**, so it cannot be towers alone; `NormalBossDefeatFlag`
  (3-58 observed) is the plausible driver, possibly summed with towers. The
  claim data cannot separate those two: every player who claimed `BossDefeat_1`
  (needs 5) clears the bar under either reading.

  A merely plausible match is not a match — the `role_permissions` lesson, where
  eight names and eight indices agreed on the count and nothing established the
  order. So the bundle carries `counter: null` for `BossDefeat` and the payload
  reports claimed tiers with **no progress bar**.

## `PalDex` has a near-twin and they are not quite equal

`PaldeckUnlockFlag` counts 211/157/129/109/8 where `TribeCaptureCount` reads
210/149/128/109/8 — Paldeck is greater or equal on every player, consistent with
it including Pals *seen* but not caught. `TribeCaptureCount` is used because it
is the game's own dedicated counter and the category is about capture; the
difference travels in the payload so nobody has to rediscover it.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

OUT_PATH = os.path.join(ROOT, "backend", "data", "achievements.json.gz")

# Category -> the RecordData counter that advances it, or None where the join is
# not established. **None is a real value here**, not a gap to be filled in
# later by whoever is annoyed by it.
CATEGORY_COUNTER = {
    "PalCapture": "palsCaptured",
    "PalDex": "speciesCaptured",
    "BossDefeat": None,
}

# `RewardItemString` is `((ItemId,Count))` — a stringified struct rather than a
# real one, so it is parsed rather than read. Anything that does not match is
# carried verbatim instead of dropped.
_REWARD = re.compile(r"\(\(([A-Za-z0-9_]+),\s*(\d+)\)\)")


def _rewards(raw: str) -> tuple[list[dict], str]:
    text = str(raw or "")
    found = [{"itemId": m.group(1), "count": int(m.group(2))}
             for m in _REWARD.finditer(text)]
    return found, text


def extract(pak=None) -> dict:
    import palpak
    import uassettable

    pak = pak or palpak.Pak()
    path = next(
        (f for f in pak.files
         if f.endswith(".uasset") and "AchivementRewardNPC" in f), None
    )
    if path is None:
        raise SystemExit("!! DT_AchivementRewardNPC not in the server pak")

    rows = uassettable.read_table(pak, path)
    tiers: dict[str, list[dict]] = {}
    for name, row in rows.items():
        category = str(row.get("AchivementCategory") or "").rsplit("::", 1)[-1]
        if not category:
            continue
        rewards, verbatim = _rewards(row.get("RewardItemString"))
        tiers.setdefault(category, []).append({
            "id": name,
            "requireCount": int(row.get("RequireCount") or 0),
            "expBonusLevel": int(row.get("ExpBonusLevel") or 0),
            "rewards": rewards,
            **({} if rewards else {"rewardRaw": verbatim}),
        })

    for entries in tiers.values():
        # Ascending threshold, which is the order a player meets them in and
        # therefore the only order a progress list should ever render.
        entries.sort(key=lambda e: e["requireCount"])

    unknown = sorted(set(tiers) - set(CATEGORY_COUNTER))
    if unknown:
        raise SystemExit(
            f"!! unmapped achievement categories: {unknown}. Add them to "
            "CATEGORY_COUNTER with a counter or an explicit None — a category "
            "silently missing from the map would render as no progress."
        )

    return {
        "categories": {
            name: {
                "counter": CATEGORY_COUNTER[name],
                "tiers": entries,
            }
            for name, entries in sorted(tiers.items())
        },
        "rows": sum(len(v) for v in tiers.values()),
        "note": (
            "The game's own milestone NPC (DT_AchivementRewardNPC). NOT Steam "
            "achievements — those need an external API this project forbids. "
            "`counter: null` means no save field is established for that "
            "category; claimed tiers are still exact, because "
            "RecordData.NPCAchivementRewardFlag names the row."
        ),
    }


def main() -> int:
    data = extract()
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    # mtime=0 so unchanged input gives byte-identical output and a regeneration
    # can be diffed rather than trusted — scripts/jsonout.py's rule.
    with gzip.GzipFile(OUT_PATH, "wb", compresslevel=9, mtime=0) as f:
        f.write(payload)

    print(f"Wrote {OUT_PATH}  ({os.path.getsize(OUT_PATH):,} bytes)")
    for name, entry in data["categories"].items():
        counter = entry["counter"] or "NO COUNTER ESTABLISHED"
        thresholds = [t["requireCount"] for t in entry["tiers"]]
        print(f"  {name:12s} {len(entry['tiers']):2d} tiers  "
              f"{thresholds[0]}..{thresholds[-1]:<5} -> {counter}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
