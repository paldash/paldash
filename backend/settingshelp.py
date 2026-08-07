"""
What each PalWorldSettings.ini key does, in Pocketpair's words rather than ours.

`backend/data/settings_help.json.gz`, built by `scripts/extract-settings-help.py`
— see that module for where every string comes from and what the extractor
refuses to invent. The short version:

    description   Pocketpair's official documentation      93 of 119 keys
    label         the game's own world-settings UI strings 50 of 119
    note          this project's own measurements           6, tagged as such
    values        the game's names for an enum's VALUES     DeathPenalty et al

**A KEY WITH NO HELP GETS NOTHING, AND THAT IS THE FEATURE.** 19 of the 119 have
no official description and no game label. They come back absent, the UI renders
no tooltip, and an operator sees a plain field — which is what they had before.
A generated sentence would look exactly like the 93 real ones and be trusted the
same way, which is the failure `basesupply.py` refuses in the same words.

**`values` is worth more than the key descriptions.** `DeathPenalty=Item` and
`DeathPenalty=EquipmentAndItemAndRandomPal` are opaque to anyone who has not
memorised them; the game calls them "Drop all items except equipment" and "Drop
all items and one random Pal on team". Those are the strings a player already
reads on the world-settings screen.

Separate from `settings_ini` for the reason `elements.py` is separate from
`gamedata`: this is reference text about the file, not the file, and mixing them
would put a data-loading failure on the path that writes a server's config.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

HELP_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "settings_help.json.gz"
)

_bundle: Optional[dict[str, Any]] = None


def reload() -> int:
    """Drop the cache. Counterpart to `gamedata.reload()`, and returns the count."""
    global _bundle
    _bundle = None
    return len(load().get("settings") or {})


def load() -> dict[str, Any]:
    """
    The bundle, or an empty one.

    Empty rather than raising, like `gamedata.effigies()`: missing help should
    cost the tooltips, never the Settings tab. An operator who cannot read the
    explanation can still read and write the file, which is the actual job.
    """
    global _bundle
    if _bundle is None:
        try:
            with gzip.open(HELP_PATH, "rt", encoding="utf-8") as f:
                _bundle = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(
                "Settings help unavailable (%s); the Settings tab will show no "
                "explanations", e
            )
            _bundle = {"settings": {}, "values": {}, "sources": {}}
    return _bundle


def describe(key: str) -> dict[str, Any]:
    """
    Help for one key: `{}` when there is none.

    Every text field travels with its `*Source`, because the three do not carry
    the same authority and the UI has to be able to say which is which. A note
    this project wrote about a trap it measured is a different kind of claim from
    a sentence Pocketpair published, and presenting them identically would launder
    the second into the first.
    """
    entry = (load().get("settings") or {}).get(key)
    if not entry:
        return {}
    out = dict(entry)
    values = (load().get("values") or {}).get(key)
    if values:
        out["values"] = values
    return out


def annotate(options: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """
    Attach help to a parsed INI's options, in place.

    Called from the read path rather than baked into the parse, so that a
    regenerated bundle takes effect without a re-read — the same reason
    `/api/mapobjects` resolves species names at request time.

    Keys with no help are left untouched rather than given an empty `help` key:
    absent and "we have nothing" are the same thing here, and an empty object is
    the kind of value a UI truthiness check gets wrong.
    """
    for key, option in options.items():
        help_ = describe(key)
        if help_:
            option["help"] = help_
    return options


def coverage() -> dict[str, Any]:
    """
    How much of the file is explained, for the Settings tab's own footnote.

    Shown rather than hidden: "19 of these settings have no official
    documentation" is a fact about Pocketpair's docs, and an operator hunting for
    a missing tooltip should learn that instead of assuming the dashboard is
    broken.
    """
    bundle = load()
    return {
        "iniKeys": bundle.get("iniKeys"),
        "documented": bundle.get("documented"),
        "labelled": bundle.get("labelled"),
        "undocumented": bundle.get("undocumented") or [],
        "sources": bundle.get("sources") or {},
    }
