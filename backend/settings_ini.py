"""
PalWorldSettings.ini reader/writer.

The file has one meaningful line:

    [/Script/Pal.PalGameWorldSettings]
    OptionSettings=(Difficulty=None,DayTimeSpeedRate=1.000000,...,bIsPvP=False,...)

Everything lives inside that single parenthesised list, so we parse it with a
quote-aware splitter, preserve each value's original formatting, and rewrite only
the keys that changed. The rest of the file is passed through byte for byte.

IMPORTANT: nothing here takes effect until the server restarts. The dedicated
server reads this file at boot only, and the REST API has no settings-write
endpoint, so there is no such thing as a hot-swapped INI setting. Writing the
file is safe while the server runs (it is the config directory, not the save
directory) — it simply will not apply until a restart.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from datetime import datetime, timezone
from typing import Any, Optional

from savefiles import BACKUP_DIR, atomic_write, find_settings_ini

logger = logging.getLogger(__name__)

SECTION = "[/Script/Pal.PalGameWorldSettings]"
_OPTION_RE = re.compile(r"^(\s*OptionSettings\s*=\s*)\((.*)\)(\s*)$")


class SettingsError(Exception):
    """Raised when the INI cannot be located, parsed or written."""


# ─── Parsing ─────────────────────────────────────────────────────


def _split_top_level(body: str) -> list[str]:
    """Split on commas that are not inside quotes or nested parentheses."""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    in_quotes = False

    for ch in body:
        if ch == '"':
            in_quotes = not in_quotes
            buf.append(ch)
        elif ch == "(" and not in_quotes:
            depth += 1
            buf.append(ch)
        elif ch == ")" and not in_quotes:
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0 and not in_quotes:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)

    if buf:
        parts.append("".join(buf))
    return [p for p in parts if p.strip()]


def _classify(raw: str) -> tuple[str, Any]:
    """Infer a value's type from its original text form."""
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"'):
        return "string", raw[1:-1]
    if raw in ("True", "False"):
        return "bool", raw == "True"
    if re.fullmatch(r"-?\d+\.\d+", raw):
        return "float", float(raw)
    if re.fullmatch(r"-?\d+", raw):
        return "int", int(raw)
    return "enum", raw


def _format(value: Any, value_type: str, original_raw: str) -> str:
    """Render a new value in the same style as the original."""
    if value_type == "bool":
        if isinstance(value, str):
            value = value.strip().lower() in ("true", "1", "yes", "on")
        return "True" if value else "False"

    if value_type == "float":
        decimals = 6
        if "." in original_raw:
            decimals = len(original_raw.split(".", 1)[1])
        return f"{float(value):.{decimals}f}"

    if value_type == "int":
        return str(int(float(value)))

    if value_type == "string":
        escaped = str(value).replace('"', "")
        return f'"{escaped}"'

    return str(value).strip()


def read_ini(path: Optional[str] = None) -> dict[str, Any]:
    """Parse the INI into ordered, typed options."""
    path = path or find_settings_ini()
    if not path:
        raise SettingsError(
            "PalWorldSettings.ini not found. Set PALWORLD_CONFIG_INI, or check that the "
            "server directory is bind-mounted."
        )
    if not os.path.exists(path):
        raise SettingsError(f"PalWorldSettings.ini not found at {path}")

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()

    options: dict[str, dict[str, Any]] = {}
    found = False

    for line in lines:
        match = _OPTION_RE.match(line)
        if not match:
            continue
        found = True
        for pair in _split_top_level(match.group(2)):
            if "=" not in pair:
                continue
            key, raw = pair.split("=", 1)
            key = key.strip()
            value_type, value = _classify(raw)
            options[key] = {"value": value, "type": value_type, "raw": raw.strip()}
        break

    if not found:
        raise SettingsError(f"No OptionSettings=(...) line found in {path}")

    return {
        "path": path,
        "writable": os.access(path, os.W_OK),
        "options": options,
        "count": len(options),
    }


# ─── Writing ─────────────────────────────────────────────────────


def _backup_ini(path: str) -> str:
    """Timestamped copy of the INI before we touch it."""
    dest_dir = os.path.join(BACKUP_DIR, "config")
    os.makedirs(dest_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(dest_dir, f"PalWorldSettings_{stamp}.ini")
    shutil.copy2(path, dest)
    logger.info("Backed up INI to %s", dest)
    return dest


def write_ini(changes: dict[str, Any], path: Optional[str] = None) -> dict[str, Any]:
    """
    Apply `changes` to the OptionSettings line.

    Unknown keys are rejected rather than appended — a typo'd key silently added
    to the line is how people end up with a server that will not boot.
    """
    current = read_ini(path)
    path = current["path"]
    options = current["options"]

    if not changes:
        raise SettingsError("No changes supplied")

    unknown = [k for k in changes if k not in options]
    if unknown:
        raise SettingsError(
            f"Unknown setting key(s): {', '.join(sorted(unknown))}. "
            "Only keys already present in your INI can be changed."
        )

    applied: list[dict[str, Any]] = []
    replacements: dict[str, str] = {}

    for key, new_value in changes.items():
        meta = options[key]
        new_raw = _format(new_value, meta["type"], meta["raw"])
        if new_raw == meta["raw"]:
            continue
        replacements[key] = new_raw
        applied.append(
            {"key": key, "from": meta["raw"], "to": new_raw, "type": meta["type"]}
        )

    if not applied:
        return {"applied": [], "changed": False, "path": path, "restartRequired": False}

    # newline="" disables universal-newline translation. Without it Python
    # silently converts CRLF to LF while reading, the check below could never
    # fire, and a Windows server's INI came back rewritten with LF endings.
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
        original = f.read()

    newline = "\r\n" if "\r\n" in original else "\n"
    lines = original.splitlines()
    rewritten = False

    for idx, line in enumerate(lines):
        match = _OPTION_RE.match(line)
        if not match:
            continue

        pairs = _split_top_level(match.group(2))
        rebuilt = []
        for pair in pairs:
            if "=" in pair:
                key = pair.split("=", 1)[0].strip()
                if key in replacements:
                    rebuilt.append(f"{key}={replacements[key]}")
                    continue
            rebuilt.append(pair.strip())

        lines[idx] = f"{match.group(1)}({','.join(rebuilt)}){match.group(3)}"
        rewritten = True
        break

    if not rewritten:
        raise SettingsError("OptionSettings line vanished between read and write")

    backup_path = _backup_ini(path)
    atomic_write(path, (newline.join(lines) + newline).encode("utf-8"))
    logger.info("Wrote %d setting(s) to %s", len(applied), path)

    return {
        "applied": applied,
        "changed": True,
        "path": path,
        "backupPath": backup_path,
        "restartRequired": True,
    }


# ─── Presets ─────────────────────────────────────────────────────

PRESETS: list[dict[str, Any]] = [
    {
        "id": "pvp_players_only",
        "label": "PvP — players only, bases protected",
        "description": (
            "Players can damage each other anywhere, but rival guilds cannot raid "
            "bases or chests, and death drops stay with their owner."
        ),
        "changes": {
            "bIsPvP": True,
            "bEnablePlayerToPlayerDamage": True,
            "bEnableDefenseOtherGuildPlayer": False,
            "bCanPickupOtherGuildDeathPenaltyDrop": False,
            "bEnableFriendlyFire": False,
        },
    },
    {
        "id": "pvp_full_raid",
        "label": "PvP — full raiding",
        "description": "Player damage plus base raiding and looting of rival death drops.",
        "changes": {
            "bIsPvP": True,
            "bEnablePlayerToPlayerDamage": True,
            "bEnableDefenseOtherGuildPlayer": True,
            "bCanPickupOtherGuildDeathPenaltyDrop": True,
        },
    },
    {
        "id": "pve",
        "label": "PvE — peaceful",
        "description": "No player damage, no base raiding. The default cooperative setup.",
        "changes": {
            "bIsPvP": False,
            "bEnablePlayerToPlayerDamage": False,
            "bEnableFriendlyFire": False,
            "bEnableDefenseOtherGuildPlayer": False,
            "bCanPickupOtherGuildDeathPenaltyDrop": False,
        },
    },
]

# Curated keys the UI surfaces first, grouped for a sane layout.
HIGHLIGHT_GROUPS: list[dict[str, Any]] = [
    {
        "label": "PvP & Guilds",
        "keys": [
            "bIsPvP",
            "bEnablePlayerToPlayerDamage",
            "bEnableFriendlyFire",
            "bEnableDefenseOtherGuildPlayer",
            "bCanPickupOtherGuildDeathPenaltyDrop",
            "DeathPenalty",
            "GuildPlayerMaxNum",
        ],
    },
    {
        "label": "Bases & Building",
        "keys": [
            "BuildObjectDamageRate",
            "BuildObjectDeteriorationDamageRate",
            "bEnableInvaderEnemy",
            "BaseCampMaxNumInGuild",
            "BaseCampWorkerMaxNum",
            "bBuildAreaLimit",
        ],
    },
    {
        "label": "Rates & Difficulty",
        "keys": [
            "Difficulty",
            "ExpRate",
            "PalCaptureRate",
            "PalSpawnNumRate",
            "DayTimeSpeedRate",
            "NightTimeSpeedRate",
            "CollectionDropRate",
            "EggDefaultHatchingTime",
        ],
    },
    {
        "label": "Server",
        "keys": [
            "ServerName",
            "ServerDescription",
            "ServerPlayerMaxNum",
            "bIsMultiplay",
            "RESTAPIEnabled",
            "bShowPlayerList",
        ],
    },
]


def apply_preset(preset_id: str, path: Optional[str] = None) -> dict[str, Any]:
    """Apply a named preset, skipping keys absent from this server's INI."""
    preset = next((p for p in PRESETS if p["id"] == preset_id), None)
    if not preset:
        raise SettingsError(f"Unknown preset: {preset_id}")

    current = read_ini(path)
    present = {k: v for k, v in preset["changes"].items() if k in current["options"]}
    skipped = sorted(set(preset["changes"]) - set(present))

    if not present:
        raise SettingsError(
            f"None of this preset's keys exist in your INI ({', '.join(skipped)}). "
            "Your Palworld version may use different setting names."
        )

    result = write_ini(present, path)
    result["preset"] = preset_id
    result["skippedKeys"] = skipped
    return result
