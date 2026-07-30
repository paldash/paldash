"""
Detect installed Palworld mods.

The point is not a mod manager. It is that **a modded server explains things the
dashboard would otherwise report as problems.**

`palcheck` classifies an unrecognised character id as an *advisory* rather than
evidence of cheating, because the bundled tables are incomplete — 13 of the
reference world's 1,905 characters are ordinary NPCs the 753-Pal table does not
list. Mods widen that gap enormously: a Pal-adding mod puts species in the save
that no bundled table will ever contain, and an item mod does the same for
inventories. Without knowing mods are present, the honest answer to "why does this
Pal show as `unknown_species`" is a shrug.

So this module answers one question — *is this server modded, and with what* — and
the answer qualifies those reports.

**Detection is by file, and it is deliberately shallow.** A `.pak` in a mod
directory is a mod; its name is the only identity available without parsing it, and
parsing arbitrary third-party paks to recover a display name is a lot of risk for a
prettier string. No mod is ever loaded, executed or opened.

WHERE MODS LIVE
---------------
Palworld loads paks from `Pal/Content/Paks/` and its subdirectories. The
conventions, in the order the community settled on them:

  ~mods/          the usual drop-in directory; the `~` sorts it after the base
                  game so its paks win
  LogicMods/      UE4SS blueprint mods
  Mods/           an older convention, still seen
  Paks/*.pak      loose paks beside `Pal-LinuxServer.pak`

`Pal-LinuxServer.pak` is the game itself and is never reported as a mod. That
exclusion is by exact name rather than by directory, because a loose mod pak sits
in the same directory.

UE4SS itself is a script loader rather than a mod, and it lives in
`Pal/Binaries/<platform>/`. It is reported separately: its presence means Lua mods
may be active that leave no `.pak` at all, which is a real limit on what this can
see and worth saying rather than hiding.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# The game's own pak. Matched by exact name: a mod pak can legitimately sit in the
# same directory, so excluding the whole directory would hide it.
BASE_PAKS = {
    "pal-linuxserver.pak",
    "pal-windowsserver.pak",
    "pal-windowsnoeditor.pak",
    "pal.pak",
}

MOD_DIRS = ("~mods", "LogicMods", "Mods")

# UE4SS, which loads Lua mods that may leave no pak behind.
LOADER_MARKERS = (
    "ue4ss.dll",
    "ue4ss.so",
    "dwmapi.dll",       # the usual UE4SS proxy DLL name on Windows
    "Mods/mods.txt",
)


def _paks_dir(install_dir: str) -> str:
    return os.path.join(install_dir, "Pal", "Content", "Paks")


def _scan_dir(path: str, category: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if not os.path.isdir(path):
        return found
    try:
        entries = sorted(os.listdir(path))
    except OSError as e:
        logger.warning("Could not list %s: %s", path, e)
        return found

    for name in entries:
        if not name.lower().endswith(".pak"):
            continue
        if name.lower() in BASE_PAKS:
            continue
        full = os.path.join(path, name)
        try:
            size = os.path.getsize(full)
            mtime = os.path.getmtime(full)
        except OSError:
            continue
        found.append({
            "name": name,
            "category": category,
            # Relative, so an audit record or a screenshot does not leak the
            # operator's directory layout.
            "sizeBytes": size,
            "modifiedAt": int(mtime),
        })
    return found


def detect(install_dir: str = "") -> dict[str, Any]:
    """
    What is installed, or an honest "cannot tell".

    `checked` is false when the game directory is not visible to this container,
    which is the normal case for a dashboard that only mounts the save path. That
    reads very differently from "no mods installed" and must not be collapsed into
    it — an unmodded-looking report from a directory we never saw would make the
    `unknown_species` advisories *more* confusing, not less.
    """
    if not install_dir:
        try:
            import gameversion

            install_dir = gameversion.install_dir()
        except Exception:  # noqa: BLE001
            install_dir = ""

    if not install_dir or not os.path.isdir(install_dir):
        return {
            "checked": False,
            "modded": False,
            "mods": [],
            "loader": None,
            "reason": (
                "The game's install directory is not visible to this container, so "
                "mods cannot be detected. Set PALWORLD_INSTALL_DIR if you want this "
                "checked."
            ),
        }

    paks = _paks_dir(install_dir)
    mods: list[dict[str, Any]] = []
    for sub in MOD_DIRS:
        mods.extend(_scan_dir(os.path.join(paks, sub), sub))
    # Loose paks beside the game's own.
    mods.extend(_scan_dir(paks, "Paks"))

    loader = _find_loader(install_dir)

    return {
        "checked": True,
        "modded": bool(mods or loader),
        "mods": mods,
        "count": len(mods),
        "loader": loader,
        "reason": _describe(mods, loader),
    }


def _find_loader(install_dir: str) -> dict[str, Any] | None:
    binaries = os.path.join(install_dir, "Pal", "Binaries")
    if not os.path.isdir(binaries):
        return None
    for platform in sorted(os.listdir(binaries)):
        base = os.path.join(binaries, platform)
        if not os.path.isdir(base):
            continue
        for marker in LOADER_MARKERS:
            if os.path.exists(os.path.join(base, marker)):
                return {"name": "UE4SS", "platform": platform, "marker": marker}
    return None


def _describe(mods: list[dict], loader: dict | None) -> str:
    if not mods and not loader:
        return "No mods found in the game's pak directories."
    parts = []
    if mods:
        parts.append(f"{len(mods)} mod pak(s) installed")
    if loader:
        parts.append(
            f"{loader['name']} is present, so Lua mods may be active that leave no "
            "pak file and cannot be listed here"
        )
    return ". ".join(parts) + "."


def explains_unknown_ids(install_dir: str = "") -> bool:
    """
    Whether unrecognised species and item ids have an innocent explanation here.

    Used to qualify `palcheck`'s advisories. False when detection could not run —
    "we did not look" is not the same as "there are no mods", and claiming the
    latter would turn a caveat into a false reassurance.
    """
    result = detect(install_dir)
    return bool(result["checked"] and result["modded"])
