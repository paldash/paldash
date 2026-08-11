#!/usr/bin/env python3
"""
Every display name and description, resolved, from the game's own files.

`l10n.py` reads the raw strings; this turns them into the `{id: {name,
description}}` maps the dashboard speaks, which needs two things the raw tables
do not give you: a **join rule per section**, and a **rich-text resolver**.

THE JOINS ARE CONVENTIONS, AND THEY WERE MEASURED
-------------------------------------------------
There are no `FText` columns on the master tables — checked against
`DT_ItemDataTable`, `DT_PalMonsterParameter`, `DT_TechnologyRecipeUnlock_Common`,
`DT_MapObjectMasterDataTable`, `DT_PassiveSkill_Main` and `DT_WazaDataTable`.
The game resolves names by naming convention instead, so each section needs its
own rule and each rule is a claim that has to be checked.

The check is agreement with the catalogue this replaces. **`activeSkills` and
`passives` agree 100% — 326 of 326 and 420 of 420, zero disagreements.** That is
what says the conventions are right rather than merely productive.

TIER VARIANTS: EXACT FIRST, BASE SECOND — NEVER BASE FIRST
-----------------------------------------------------------
`AncientArmor_2` has no row of its own and inherits `AncientArmor`. But
`Accessory_AT_2` **does** have one, and it is "Attack Pendant +1". A base-first
rule silently deletes every `+1` and `+2` in the game; the bundled archive
effectively does exactly that, which is why the dashboard has been showing three
different accessories under one name.

THE RICH-TEXT RESOLVER
----------------------
A technology that unlocks an item is *named after that item, by reference*:

    NAME_RECIPE_AIcore  ->  "<itemName id=|AIcore|/>"

Descriptions use the same tags. There are two kinds and they are treated
differently:

  * **Reference tags** carry an `id=|X|` that names another catalogue entry.
    `itemName`, `characterName`, `mapObjectName`, `activeSkillName`, `uiCommon`.
  * **Presentational tags** carry styling — `<NumRed_12>…</>`, `<Blue_16>…</>`,
    `<Status_Up>` — and are dropped, keeping their inner text.

**`keyGuideIcon` and `img` are dropped entirely, contents and all.** They name a
controller glyph or an inline sprite; there is no text they stand for, and
substituting the id would put `PadCircle` in the middle of a sentence.

**Tag names are matched case-insensitively because the game's own data is
inconsistent**: `mapObjectName` appears 1,234 times, `MapObjectName` 17 and
`mapObjectname` 27. Exactly the situation `gamedata`'s case-insensitive id
lookups already exist for.

**AN UNRESOLVABLE REFERENCE IS A REFUSAL, NOT A PASSTHROUGH.** If `id=|X|` names
something with no display name, `resolve()` returns None and the caller falls
back — because the alternative is leaking `<itemName id=|AIcore|/>` into the UI
as if it were a product name. Failing to name a thing is recoverable; naming it
with markup is not.

Usage:
    import gametext
    text = gametext.Catalogue()            # en
    text.names("items")["AIcore"]          # 'AI Core'
    text.descriptions("pals")["Alpaca"]    # the Paldeck entry
"""

from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import l10n         # noqa: E402
import palpak       # noqa: E402

# A reference to another catalogue entry. Case-insensitive: the game ships three
# spellings of `mapObjectName`.
# A REFERENCE TAG MAY CARRY FURTHER ATTRIBUTES AFTER ITS ID, and missing that
# was a silent hole rather than a visible failure. The game writes
# `<uiCommon id=|COMMON_ELEMENT_NAME_Electricity| style=|Elem_Electric|/>` —
# the older pattern demanded `/>` immediately after the id, did not match, and
# `_STYLE_OPEN` then stripped the whole tag as presentational. Solmora Lux's
# partner skill read "changes the player's attack type to  and increases
# Attack by 20%": a sentence with a gap where the element should be, which is
# worse than the markup this module exists to keep out, because it looks fine.
_REFERENCE = re.compile(r"<(\w+)\s+id=\|([^|]*)\|[^>]*?/>", re.I)

# A presentational wrapper: `<NumRed_12>text</>`. The inner text is kept.
_STYLE_OPEN = re.compile(r"<(?!/)[^>]*>")
_STYLE_CLOSE = re.compile(r"</\s*>")

# Reference tags that stand for no text at all — a controller glyph, an inline
# sprite. Dropped whole; substituting the id would put `PadCircle` in a sentence.
_NON_TEXTUAL = {"keyguideicon", "img"}

# Which catalogue each reference tag resolves against.
_REFERENCE_SOURCE = {
    "itemname": "items",
    "charactername": "pals",
    "mapobjectname": "structures",
    "activeskillname": "activeSkills",
    "uicommon": "ui",
}

# How deep a reference chain may go before it is treated as a cycle. Measured
# maximum is 1 (a technology naming an item); 4 is headroom, not a guess at the
# data.
_MAX_DEPTH = 4


def _clean(text: str) -> str:
    """Literal CRLF escapes survive the data tables; the UI wants real breaks."""
    return text.replace("\\r\\n", "\n").replace("\r\n", "\n").replace("\\n", "\n").strip()


class Catalogue:
    """The game's display strings for one language, joined and resolved."""

    def __init__(self, lang: str = "en", pak: "palpak.Pak" = None) -> None:
        self.lang = lang
        self.pak = pak or palpak.Pak(l10n.CLIENT_PAK)
        raw = {t: l10n.strings(t, lang, pak=self.pak) for t in l10n.tables(self.pak, lang)}
        self._raw = raw

        # Lower-cased lookups throughout, matching `gamedata`'s policy: the
        # upstream data is inconsistently capitalised and exact matching
        # silently loses real entries.
        def index(table: str) -> dict[str, str]:
            return {k.lower(): v for k, v in raw.get(table, {}).items()}

        self._names = {
            "items": index("DT_ItemNameText_Common"),
            "pals": index("DT_PalNameText_Common"),
            "structures": index("DT_MapObjectNameText_Common"),
            "skills": index("DT_SkillNameText_Common"),
            "technology": index("DT_TechnologyNameText_Common"),
            "humans": index("DT_HumanNameText_Common"),
            "uniqueNpcs": index("DT_UniqueNPCText_Common"),
            "regions": index("DT_WorldMap_Common_Text_Common"),
            "dungeons": index("DT_DungeonNameText"),
            "ui": index("DT_UI_Common_Text_Common"),
        }
        self._descs = {
            "items": index("DT_ItemDescriptionText_Common"),
            "pals": index("DT_PalLongDescriptionText"),
            "skills": index("DT_SkillDescText_Common"),
            "technology": index("DT_TechnologyDescText_Common"),
        }
        self.unresolved: list[tuple[str, str]] = []

    # ── raw lookup ────────────────────────────────────────────────────────

    def _lookup(self, table: str, *keys: str) -> str | None:
        index = self._names.get(table) or {}
        for key in keys:
            hit = index.get(key.lower())
            if hit:
                return hit
        return None

    def _lookup_desc(self, table: str, *keys: str) -> str | None:
        index = self._descs.get(table) or {}
        for key in keys:
            hit = index.get(key.lower())
            if hit:
                return hit
        return None

    # ── rich text ─────────────────────────────────────────────────────────

    def _reference(self, tag: str, ident: str, depth: int) -> str | None:
        tag = tag.lower()
        if tag in _NON_TEXTUAL:
            return ""
        source = _REFERENCE_SOURCE.get(tag)
        if source is None:
            # An unknown reference tag is not a style tag — it claims to name
            # something. Refuse rather than drop it silently.
            return None
        if source == "ui":
            raw = self._lookup("ui", ident)
        elif source == "items":
            raw = self.item_name(ident)
        elif source == "pals":
            raw = self.pal_name(ident)
        elif source == "structures":
            raw = self._lookup("structures", f"MAPOBJECT_NAME_{ident}")
        else:
            raw = self._lookup("skills", f"ACTION_SKILL_{ident}")
        if raw is None:
            return None
        return self.resolve(raw, depth + 1)

    def resolve(self, text: str | None, depth: int = 0) -> str | None:
        """
        Substitute references, drop styling. **None on any unresolvable id.**

        The refusal is the point: a name that failed to resolve must fall back
        to whatever the caller already does for an unknown id, never appear in
        the UI as `<itemName id=|AIcore|/>`.
        """
        if text is None:
            return None
        if depth > _MAX_DEPTH:
            return None

        out: list[str] = []
        cursor = 0
        for match in _REFERENCE.finditer(text):
            out.append(text[cursor:match.start()])
            replacement = self._reference(match.group(1), match.group(2), depth)
            if replacement is None:
                self.unresolved.append((match.group(1), match.group(2)))
                return None
            out.append(replacement)
            cursor = match.end()
        out.append(text[cursor:])

        joined = "".join(out)
        joined = _STYLE_CLOSE.sub("", joined)
        joined = _STYLE_OPEN.sub("", joined)
        return _clean(joined) or None

    # ── per-section joins ─────────────────────────────────────────────────

    @staticmethod
    def _base(ident: str) -> str | None:
        """`AncientArmor_2` -> `AncientArmor`; None when there is no tier suffix."""
        stem, _, tail = ident.rpartition("_")
        return stem if stem and tail.isdigit() else None

    def item_name(self, ident: str) -> str | None:
        # Exact first. `Accessory_AT_2` has its own row and it is "+1"; a
        # base-first rule deletes every tier marker in the game.
        base = self._base(ident)
        keys = [f"ITEM_NAME_{ident}"]
        if base:
            keys.append(f"ITEM_NAME_{base}")
        return self._lookup("items", *keys)

    def pal_name(self, ident: str) -> str | None:
        # `BOSS_`/`SUMMON_`/`GYM_` forms fall back to the base species, which is
        # this project's own documented rule: an alpha Lamball is still called
        # Lamball. The archive's "(Boss)" suffix is its editorialising, not the
        # game's, and `isBoss` already travels separately.
        keys = [f"PAL_NAME_{ident}"]
        stem = ident.split("_", 1)[1] if "_" in ident else ""
        if stem and ident.split("_", 1)[0].upper() in {"BOSS", "SUMMON", "GYM", "RAID"}:
            keys.append(f"PAL_NAME_{stem}")
        return self._lookup("pals", *keys)

    # Which raw table and row prefix each dashboard section is named from.
    # **These are the measured joins**, not guesses — `activeSkills` and
    # `passives` agree with the previous catalogue 100%, which is what says the
    # conventions are right.
    NAME_SOURCE = {
        "items": ("DT_ItemNameText_Common", "ITEM_NAME_"),
        "pals": ("DT_PalNameText_Common", "PAL_NAME_"),
        "structures": ("DT_MapObjectNameText_Common", "MAPOBJECT_NAME_"),
        "technology": ("DT_TechnologyNameText_Common", "NAME_RECIPE_"),
        "activeSkills": ("DT_SkillNameText_Common", "ACTION_SKILL_"),
        "passives": ("DT_SkillNameText_Common", "PASSIVE_"),
        "npcs": ("DT_HumanNameText_Common", "NAME_"),
        "uniqueNpcs": ("DT_UniqueNPCText_Common", "NAME_"),
        "regions": ("DT_WorldMap_Common_Text_Common", "REGION_"),
        "dungeons": ("DT_DungeonNameText", "NAME_"),
        # Keyed on the SPECIES id, not on a skill id — the game names a partner
        # skill after the Pal that has it (`PARTNERSKILL_WhiteShieldDragon` ->
        # "Aegis Shield"), so this section joins against the same ids `pals`
        # does. 311 rows.
        "partnerSkills": ("DT_SkillNameText_Common", "PARTNERSKILL_"),
    }

    DESC_SOURCE = {
        "items": ("DT_ItemDescriptionText_Common", "ITEM_DESC_"),
        "pals": ("DT_PalLongDescriptionText", "PAL_LONG_DESC_"),
        "technology": ("DT_TechnologyDescText_Common", "DESC_RECIPE_"),
        "activeSkills": ("DT_SkillDescText_Common", "ACTION_SKILL_"),
        "passives": ("DT_SkillDescText_Common", "PASSIVE_"),
        # **NOT `DT_SkillDescText_Common`, which has no PARTNERSKILL_ rows.**
        # What a partner skill does is written in the text the game shows when
        # you first catch the Pal — "While mounted, changes the player's attack
        # type to Electric and increases Attack by..." — so the description
        # lives one table over from its name. Also keyed on the species id.
        "partnerSkills": ("DT_PalFirstActivatedInfoText", "PAL_FIRST_SPAWN_DESC_"),
    }

    def _strip(self, table: str, prefix: str) -> dict[str, str]:
        """
        `{id: resolved text}` for one table, **keeping the game's own casing**.

        Iterating the raw table rather than the lower-cased lookup index is not
        a detail: the ids this returns are joined against the save's and the
        API's, which speak `AIcore` and `SheepBall`. Lower-casing them here
        would push the case problem onto every caller instead of leaving it
        where `gamedata` already solves it.
        """
        out: dict[str, str] = {}
        for key, value in (self._raw.get(table) or {}).items():
            if not key.upper().startswith(prefix.upper()):
                continue
            resolved = self.resolve(value)
            if resolved:
                out[key[len(prefix):]] = resolved
        return out

    def names(self, section: str) -> dict[str, str]:
        """Every id in a section that the game names, resolved."""
        table, prefix = self.NAME_SOURCE[section]
        return self._strip(table, prefix)

    def name(self, section: str, ident: str) -> str | None:
        """
        One id's display name: the section's join **and** the resolver.

        **This is the only entry point callers should use.** `item_name` and
        `pal_name` return the raw row because the resolver calls them while
        expanding a reference and must not recurse through a second resolve —
        but that made them a trap for everyone else. Two entries shipped with
        `<characterName id=|FlowerPrince|/>'s Petal` as their literal name
        because the overlay called the raw form, which is exactly the markup
        leak the resolver exists to prevent.
        """
        if section == "items":
            raw = self.item_name(ident)
        elif section == "pals":
            raw = self.pal_name(ident)
        else:
            table, prefix = self.NAME_SOURCE[section]
            raw = self._lookup_raw(table, f"{prefix}{ident}")
        return self.resolve(raw)

    def _lookup_raw(self, table: str, key: str) -> str | None:
        index = {k.lower(): v for k, v in (self._raw.get(table) or {}).items()}
        return index.get(key.lower())

    def descriptions(self, section: str) -> dict[str, str]:
        table, prefix = self.DESC_SOURCE[section]
        return self._strip(table, prefix)


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Resolved game display strings")
    ap.add_argument("--lang", default="en")
    ap.add_argument("--section", default=None)
    ap.add_argument("--limit", type=int, default=15)
    args = ap.parse_args()

    cat = Catalogue(args.lang)
    sections = [args.section] if args.section else [
        "items", "pals", "structures", "technology", "activeSkills", "passives",
    ]
    for section in sections:
        names = cat.names(section)
        descs = {}
        try:
            descs = cat.descriptions(section)
        except KeyError:
            pass
        print(f"{section}: {len(names)} names, {len(descs)} descriptions")
        for ident, text in list(names.items())[: args.limit]:
            print(f"   {ident:38s} {text!r}")
    if cat.unresolved:
        print(f"\n{len(cat.unresolved)} unresolved references (these entries were refused):")
        seen = {}
        for tag, ident in cat.unresolved:
            seen.setdefault(tag, set()).add(ident)
        for tag, ids in seen.items():
            print(f"   <{tag}>: {len(ids)} distinct, e.g. {sorted(ids)[:5]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
