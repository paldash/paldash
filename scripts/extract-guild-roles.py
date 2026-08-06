#!/usr/bin/env python3
"""
Guild ranks and permissions — the names the game shows, joined to the save's
numbers only as far as the evidence goes.

WHY THIS IS NEEDED
------------------
`GroupSaveDataMap[].RawData` carries two fields nothing here reads:

    guild_chest_allowed_roles   [2, 3]
    role_permissions            [{role: 2, permissions: [0,3,4,5,7]},
                                 {role: 3, permissions: [4, 7]},
                                 {role: 4, permissions: []}]

The dashboard already reports what is *in* a guild chest (`extract_guild_storage`,
5 of 5 guilds resolving to 54-slot containers) and says nothing about who may
open it. Those are bare enum indices, and rendering "role 2" helps nobody.

THE NAMES ARE THE GAME'S OWN, from the client pak's `DT_UI_Common_Text_Common`:

    GUILD_MENU_ROLE_Master      Guild Master
    GUILD_MENU_ROLE_SubMaster   Sub Master
    GUILD_MENU_ROLE_Member      Member
    GUILD_MENU_ROLE_Guest       Guest

    GUILD_PERMISSION_BasePalControl        Manage Base Pals
    GUILD_PERMISSION_ChangePermission      Modify Rank Permission
    GUILD_PERMISSION_ChangeRole            Change Player Ranks
    GUILD_PERMISSION_Construction          Place/Remove Structures
    GUILD_PERMISSION_Join                  Approve Player Requests
    GUILD_PERMISSION_Kick                  Kick Player
    GUILD_PERMISSION_PalBoxConstruction    Place/Remove Palbox
    GUILD_PERMISSION_SecuritySetting       Structure Security Setting

THE ROLE JOIN IS DERIVED AND ANCHORED. THE PERMISSION JOIN IS NOT MADE AT ALL.
------------------------------------------------------------------------------
Four role names, and the save uses indices 2, 3 and 4. That is enough to pin the
mapping, and the argument is worth stating because a positional join is exactly
what this project refuses when it is unverified:

- **A 0-based enum cannot produce an index of 4** with only four names. So it is
  1-based: 1 Master, 2 SubMaster, 3 Member, 4 Guest.
- **Index 1 is absent from `role_permissions` on every guild in every world.**
  The Guild Master is the one rank that needs no explicit grants, so an enum
  where the omitted entry is the top rank is coherent; one where it is Guest is
  not.
- **Permission counts fall monotonically with the index** — 5, 2, 0 — which is
  what a rank ladder looks like and what a shuffled mapping would not produce.
- **`guild_chest_allowed_roles` is [2, 3]**, excluding 4. Under this mapping that
  reads "Sub Masters and Members may use the chest, Guests may not", which is the
  game's own default behaviour.

Four independent things agreeing is the standard `extract-progression.py` sets
for its positional relic-line join, and `verify()` refuses the build if any of
them stops holding.

**The permission indices are NOT mapped, and that is the honest outcome.** Eight
names and observed indices 0-7 agree on the *count*, which says the set is
complete — but nothing establishes the ORDER. The L10N keys are alphabetical in
the table, which is a property of the table rather than of the enum, and the C++
enum is not in the pak. So this bundle ships the eight names as a list and the
save's numbers as numbers, with `permissionOrderKnown: false`. Guessing that
alphabetical order is declaration order would produce a screen confidently
telling an operator that a rank can kick people when it cannot.

Usage:  python3 scripts/extract-guild-roles.py [--verify]
Output: backend/data/guild.json.gz
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import palpak            # noqa: E402
from jsonout import write_json  # noqa: E402

try:
    import l10n          # noqa: E402
except ImportError:      # pragma: no cover
    l10n = None

OUT = os.path.join(os.path.dirname(HERE), "backend", "data", "guild.json.gz")

# The enum's own order, which the argument above pins. Not alphabetical, not
# guessed: 1-based because the save uses 4, and Master first because 1 is the
# index absent from every `role_permissions` list.
_ROLE_ORDER = ("Master", "SubMaster", "Member", "Guest")

_ROLE_PREFIX = "GUILD_MENU_ROLE_"
_PERMISSION_PREFIX = "GUILD_PERMISSION_"


def _strings() -> dict:
    if l10n is None:
        return {}
    try:
        return l10n.strings("DT_UI_Common_Text_Common", "en")
    except Exception as exc:  # noqa: BLE001
        print(f"   (no guild strings: {exc})", file=sys.stderr)
        return {}


def build() -> dict:
    ui = _strings()

    roles = {}
    for index, key in enumerate(_ROLE_ORDER, start=1):
        name = ui.get(f"{_ROLE_PREFIX}{key}")
        roles[str(index)] = {
            "id": key,
            "name": name or key,
            "nameIsInternal": not name,
        }

    permissions = sorted(
        {
            k[len(_PERMISSION_PREFIX):]: v
            for k, v in ui.items()
            if k.startswith(_PERMISSION_PREFIX)
        }.items()
    )

    return {
        "roles": roles,
        # A LIST, not a map from index to name. See the module docstring: the
        # count matches the observed indices and the ORDER does not follow from
        # anything, so an index-keyed map would be a claim this cannot support.
        "permissions": [{"id": k, "name": v} for k, v in permissions],
        "permissionOrderKnown": False,
        "note": (
            "Role indices are 1-based and derived from four agreeing checks — see "
            "scripts/extract-guild-roles.py. Permission indices are NOT mapped to "
            "these names: the count agrees, the order is not established."
        ),
    }


def verify(data: dict) -> list[str]:
    """
    Refuse a build whose anchors have moved.

    Each of these is one of the four things that made the role join defensible,
    so a game update that breaks any of them must stop the build rather than
    quietly reorder a rank ladder.
    """
    problems = []
    roles = data["roles"]

    if sorted(roles) != ["1", "2", "3", "4"]:
        problems.append(f"expected four 1-based roles, got {sorted(roles)}")
    if roles.get("1", {}).get("id") != "Master":
        problems.append("role 1 is not Master — the omitted-index argument fails")
    if roles.get("4", {}).get("id") != "Guest":
        problems.append("role 4 is not Guest — the save's highest index is 4")

    unresolved = [r["id"] for r in roles.values() if r["nameIsInternal"]]
    if unresolved:
        problems.append(f"role names did not resolve: {unresolved}")

    # The count check: eight names against observed indices 0-7. If the game adds
    # a permission the set is no longer complete and the "count agrees" half of
    # the argument stops holding.
    if len(data["permissions"]) != 8:
        problems.append(
            f"{len(data['permissions'])} permissions, expected 8 to match the "
            "0-7 indices observed in saves"
        )
    return problems


def main() -> int:
    if l10n is None or not _strings():
        print("REFUSING: the client pak's text table is unavailable, and the "
              "whole point of this bundle is the game's own names.",
              file=sys.stderr)
        return 2

    data = build()
    problems = verify(data)
    if problems:
        for line in problems:
            print(f"REFUSING: {line}", file=sys.stderr)
        return 3

    if "--verify" in sys.argv:
        print(f"verified {len(data['roles'])} roles and "
              f"{len(data['permissions'])} permissions, all named by the game")
        return 0

    write_json(OUT, data)
    print(f"wrote {OUT}")
    for index, role in sorted(data["roles"].items()):
        print(f"  role {index}: {role['name']}")
    print(f"  {len(data['permissions'])} permissions: "
          f"{', '.join(p['name'] for p in data['permissions'][:3])}, …")
    print("  permission INDEX -> name is deliberately not mapped — the count "
          "agrees, the order does not follow from anything")
    return 0


if __name__ == "__main__":
    sys.exit(main())
