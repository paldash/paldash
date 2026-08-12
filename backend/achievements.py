"""
The game's own milestone progress — **not Steam achievements**.

Steam's live on Steam's servers and reading them means `GetPlayerAchievements`,
a publisher key and each player's SteamID: a runtime dependency on an external
API, which this project forbids because the container must work offline on a
LAN. They are also per-account rather than per-server, so they would cover only
players who handed over a key and would report single-player activity too.

This reads the in-game milestone NPC (`DT_AchivementRewardNPC`, 26 rows in three
categories) against each player's own save. Per-player, offline, available for
**every** player on the server, and true while the server is down.

**The UI must never call these Steam achievements.** If they turn out to
correspond that is a nice fact to note; presenting in-game milestones under
Steam's name would be a claim about a system this cannot see.

CLAIMED IS READ, NEVER INFERRED
-------------------------------
The hard part was expected to be matching an `AchivementCategory` enum to a save
counter. It is not needed for the half that matters: `NPCAchivementRewardFlag`
holds the **row names outright** (`PalDex_7`, `BossDefeat_1`), and across the
reference world's five players **26 of 26 claimed keys resolve to a real row**.

WHAT IS INFERRED IS THE PROGRESS BAR, AND ONE CATEGORY REFUSES TO HAVE ONE
--------------------------------------------------------------------------
`PalCapture` and `PalDex` have counters whose names match the category and whose
observed ranges straddle the tiers. **`BossDefeat` does not**: no
`BossDefeatCount` exists, towers max at 7 observed against a top tier of 100 so
it cannot be towers alone, and the claim data cannot separate "field bosses"
from "field plus tower" because every player who claimed the 5-boss tier clears
it under either reading.

So that category ships `counter: null` and reports **claimed tiers with no
progress figure**. A merely plausible match is not a match — the
`role_permissions` lesson, where eight names and eight indices agreed on the
count and nothing established the order.

CLAIMED IS NOT EARNED, AND THE DIFFERENCE IS THE INTERESTING PART
------------------------------------------------------------------
A reward is claimed by walking to the NPC. One reference player has 128 species
and has claimed only the 10-species tier; another has 149 and has claimed none
of them. `unclaimed` is therefore a real, useful state — "you have earned this
and not collected it" — and not a rounding error.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "data", "achievements.json.gz")

_data: Optional[dict[str, Any]] = None


def _load() -> dict[str, Any]:
    global _data
    if _data is None:
        try:
            with gzip.open(DATA_PATH, "rt", encoding="utf-8") as f:
                _data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(
                "Achievement data unavailable (%s); the milestone panel will be "
                "empty rather than wrong", e
            )
            _data = {"categories": {}}
    return _data


def available() -> bool:
    """
    Whether the bundle loaded at all.

    Distinct from a player having claimed nothing. "No milestones exist" and "we
    could not read the table" must not share a representation — the
    `.catch(() => [])` lesson, which this project has recorded four times.
    """
    return bool(_load().get("categories"))


def catalogue() -> dict[str, Any]:
    """Every category and tier, with no player attached."""
    return _load().get("categories") or {}


def _counter_value(progress: dict[str, Any], counter: Optional[str]) -> Optional[int]:
    """
    The player's current figure for a category, or None when unknowable.

    **None is not zero.** It means either that no counter is established for the
    category (`BossDefeat`) or that the save did not carry the field — and a
    progress bar at 0% would be a claim in both cases.
    """
    if not counter:
        return None
    entry = progress.get(counter)
    if isinstance(entry, dict):
        total = entry.get("total")
        return int(total) if isinstance(total, (int, float)) else None
    if isinstance(entry, (int, float)) and not isinstance(entry, bool):
        return int(entry)
    return None


def for_player(progress: dict[str, Any]) -> dict[str, Any]:
    """
    One player's milestone standing. `progress` is `extract_player_progress`.

    Each tier comes back as one of three states, and the third is the point:

    - `claimed`   — the save names this row in `NPCAchivementRewardFlag`
    - `unclaimed` — the counter has passed the threshold and the reward is
                    sitting with the NPC
    - `locked`    — not yet reached

    A tier whose category has no counter can only ever be `claimed` or
    `unknown`, never `locked`, because "not yet reached" is a claim about a
    number this cannot see.
    """
    claimed = set(progress.get("achievementsClaimed") or [])
    out: dict[str, Any] = {}

    for name, entry in (catalogue() or {}).items():
        counter = entry.get("counter")
        value = _counter_value(progress, counter)
        tiers = []
        for tier in entry.get("tiers") or []:
            if tier["id"] in claimed:
                state = "claimed"
            elif value is None:
                state = "unknown"
            elif value >= tier["requireCount"]:
                state = "unclaimed"
            else:
                state = "locked"
            tiers.append({**tier, "state": state})

        out[name] = {
            "counter": counter,
            "value": value,
            "tiers": tiers,
            "claimed": sum(1 for t in tiers if t["state"] == "claimed"),
            "unclaimed": sum(1 for t in tiers if t["state"] == "unclaimed"),
            "total": len(tiers),
            # The client is the thing about to draw a progress bar, so it is
            # told there is no number to draw one from — same reason
            # `hasMultiplier` and `stateIsUnnamed` travel in their payloads.
            "hasProgress": value is not None,
        }
    return out


def summarise(progress: dict[str, Any]) -> dict[str, Any]:
    """Totals across categories, for a one-line standing."""
    per = for_player(progress)
    return {
        "categories": per,
        "claimed": sum(c["claimed"] for c in per.values()),
        "unclaimed": sum(c["unclaimed"] for c in per.values()),
        "total": sum(c["total"] for c in per.values()),
        # Stated in the payload because it is the whole reason this exists and
        # a client could otherwise reasonably label the panel "Achievements".
        "source": "in-game milestone NPC (DT_AchivementRewardNPC)",
        "isSteam": False,
    }
