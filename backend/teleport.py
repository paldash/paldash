"""
Move a player to a coordinate, by editing their save.

Palworld's own teleport commands cannot do this. The shipped server binary carries
exactly one player-facing teleport, `TeleportToPlayerByIndex`, and it is anchored to
the *issuing admin's in-game character* — "teleport me to that player". A headless
dashboard has no character in the world, so there is no anchor and nothing useful to
send even with RCON. (The `Debug_TeleportTo*` and `Dev_RequestTeleportTo*` symbols in
the binary are development RPCs, not admin commands.)

A save edit can do what the game cannot, at a price worth stating plainly: **it only
works while the server is stopped.** That rules out the case people usually want
teleport for — unsticking a player who is stuck right now — and leaves the cases
where it is genuinely the only tool: recovering a character wedged in terrain after
the fact, or pulling someone back from a place they can no longer travel from.

WHERE THE POSITION LIVES
------------------------
`Players/<UID>.sav` -> `SaveData.LastTransform.Translation` -> `{x, y, z}`.

**Not in `Level.sav`.** A player's `CharacterSaveParameterMap` entry carries `Exp`,
`Level`, `NickName` and a `LastJumpedLocation`, but no live position — checked
directly rather than assumed. That is the good news here: a teleport rewrites three
floats in one small player file and never touches the 55 MB world at all, which is a
far smaller blast radius than any other write in this project.

Rotation is deliberately left alone. Facing is not worth the risk of writing a
malformed quaternion, and the game corrects it on the first input.
"""

from __future__ import annotations

import logging
import math
import os
from typing import Any, Optional

import savefiles

logger = logging.getLogger(__name__)


class TeleportError(Exception):
    pass


# Generous bounds, sized to catch a typo rather than to police destinations.
#
# The 174 bundled fast-travel points span x ∈ [-984,034, 628,792],
# y ∈ [-757,915, 589,410], z ∈ [-2,107, 67,279]. These limits sit well outside that
# so no legitimate spot is refused, while still catching the failure that actually
# happens: an extra digit, which drops a character outside the world to fall
# forever.
MIN_XY = -1_500_000.0
MAX_XY = 1_500_000.0
MIN_Z = -50_000.0
MAX_Z = 200_000.0


def _translation(player_gvas) -> dict:
    node = player_gvas.properties
    for key in ("SaveData", "value", "LastTransform", "value", "Translation", "value"):
        if not isinstance(node, dict) or key not in node:
            raise TeleportError(
                "This player save has no LastTransform.Translation — it may predate "
                "the position format, or the file is not a Palworld player save."
            )
        node = node[key]
    if not isinstance(node, dict) or not {"x", "y", "z"} <= set(node):
        raise TeleportError("LastTransform.Translation is not an {x, y, z} vector")
    return node


def _validate(x: float, y: float, z: float) -> tuple[float, float, float]:
    try:
        x, y, z = float(x), float(y), float(z)
    except (TypeError, ValueError):
        raise TeleportError("Coordinates must be numbers")

    for name, value in (("x", x), ("y", y), ("z", z)):
        if math.isnan(value) or math.isinf(value):
            raise TeleportError(f"{name} is not a finite number")

    if not (MIN_XY <= x <= MAX_XY and MIN_XY <= y <= MAX_XY):
        raise TeleportError(
            f"({x:,.0f}, {y:,.0f}) is outside the world. Palworld coordinates run to "
            f"roughly ±1,000,000 — check for an extra digit."
        )
    if not (MIN_Z <= z <= MAX_Z):
        raise TeleportError(
            f"Height {z:,.0f} is outside the world. Ground level runs from about "
            f"-2,100 to 67,300."
        )
    return x, y, z


def current_position(uid: str, world_dir: Optional[str] = None) -> dict[str, Any]:
    """Where a player is now, read from their save."""
    path = savefiles.get_player_sav_path(uid, world_dir)
    if not path or not os.path.isfile(path):
        raise TeleportError(f"No player save for {uid}")

    from palsav.core import decompress_sav_to_gvas
    from palsav.gvas import GvasFile
    from palsav.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS

    raw = savefiles.read_sav_bytes(path)
    if raw is None:
        raise TeleportError("Could not read the player save")
    decompressed, _ = decompress_sav_to_gvas(raw)
    gvas = GvasFile.read(decompressed, PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES)

    translation = _translation(gvas)
    return {
        "x": float(translation["x"]),
        "y": float(translation["y"]),
        "z": float(translation["z"]),
    }


def destinations() -> list[dict[str, Any]]:
    """
    The 174 fast-travel points, as known-good destinations.

    These are the safe answer to the hard part of a coordinate teleport: **nothing
    here knows the terrain height**. A hand-typed `z` can drop a character under the
    map or a kilometre above it, whereas every one of these is a position the game
    itself puts players at, with a `z` that is verified ground.
    """
    try:
        import gamedata

        return [
            {"id": p["id"], "name": p["name"], "x": p["x"], "y": p["y"], "z": p["z"]}
            for p in gamedata.fast_travel_points()
            if "z" in p
        ]
    except Exception as e:  # noqa: BLE001 - a missing bundle loses a convenience
        logger.warning("Fast-travel destinations unavailable: %s", e)
        return []


def plan_teleport(
    uid: str, x: float, y: float, z: float, world_dir: Optional[str] = None
) -> dict[str, Any]:
    """Where the player is, where they would go, and anything worth warning about."""
    target = _validate(x, y, z)
    before = current_position(uid, world_dir)

    distance = math.dist((before["x"], before["y"], before["z"]), target)
    warnings: list[str] = []

    # Distance to the nearest known-good position. A destination far from every
    # fast-travel point is not wrong — most of the map is — but combined with a
    # hand-typed height it is the shape of a mistake worth mentioning.
    points = destinations()
    nearest = None
    if points:
        nearest = min(points, key=lambda p: math.dist((p["x"], p["y"]), target[:2]))
        gap = math.dist((nearest["x"], nearest["y"]), target[:2])
        if gap > 50_000:
            warnings.append(
                f"The nearest fast-travel point ({nearest['name']}) is "
                f"{gap / 1000:,.0f}k units away. Nothing here knows the terrain "
                f"height, so an unverified z can drop the character under the map."
            )

    return {
        "uid": uid,
        "from": before,
        "to": {"x": target[0], "y": target[1], "z": target[2]},
        "distance": distance,
        "nearestPoint": nearest,
        "warnings": warnings,
    }


def apply_teleport(
    uid: str,
    x: float,
    y: float,
    z: float,
    *,
    world_dir: Optional[str] = None,
    label: str = "teleport",
) -> dict[str, Any]:
    """
    Write the new position, behind the save-write guard.

    Goes through `guarded_save_write` like every other write here — the server must
    be provably stopped and a full verified backup is taken first. That it touches
    one small file rather than `Level.sav` is not a reason to skip the guard: a
    player save holds the character, its inventory and its Pal container ids, and
    losing one is losing a player.
    """
    import backup as backup_module

    target = _validate(x, y, z)
    root = world_dir or savefiles.get_default_world_dir()
    if not root:
        raise TeleportError("World directory not found")

    path = savefiles.get_player_sav_path(uid, root)
    if not path or not os.path.isfile(path):
        raise TeleportError(f"No player save for {uid}")

    from palsav.core import compress_gvas_to_sav, decompress_sav_to_gvas
    from palsav.gvas import GvasFile
    from palsav.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS

    with backup_module.guarded_save_write(f"{label} {uid}", root) as backup:
        raw = savefiles.read_sav_bytes(path)
        if raw is None:
            raise TeleportError("Could not read the player save")
        decompressed, save_type = decompress_sav_to_gvas(raw)
        gvas = GvasFile.read(decompressed, PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES)

        translation = _translation(gvas)
        before = {k: float(translation[k]) for k in ("x", "y", "z")}
        translation["x"], translation["y"], translation["z"] = target

        encoded = compress_gvas_to_sav(
            gvas.write(PALWORLD_CUSTOM_PROPERTIES), save_type
        )
        savefiles.atomic_write(path, encoded)

        # Verified on the re-read, not on the tree we just edited. An encoder fault
        # produces a correct-looking structure and a wrong file, and only reading it
        # back catches that.
        verify_raw = savefiles.read_sav_bytes(path)
        if verify_raw is None:
            raise TeleportError("Could not re-read the player save after writing")
        verify = GvasFile.read(
            decompress_sav_to_gvas(verify_raw)[0],
            PALWORLD_TYPE_HINTS,
            PALWORLD_CUSTOM_PROPERTIES,
        )
        written = _translation(verify)
        for axis, expected in zip(("x", "y", "z"), target):
            if abs(float(written[axis]) - expected) > 0.5:
                raise TeleportError(
                    f"Verification failed: {axis} read back as {written[axis]}, "
                    f"expected {expected}. The world was rolled back."
                )

        logger.info(
            "Teleported %s from (%.0f, %.0f, %.0f) to (%.0f, %.0f, %.0f)",
            uid, before["x"], before["y"], before["z"], *target,
        )
        return {
            "ok": True,
            "uid": uid,
            "from": before,
            "to": {"x": target[0], "y": target[1], "z": target[2]},
            "backupId": backup.get("id") if isinstance(backup, dict) else None,
        }
