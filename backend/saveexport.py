"""
Structured save exports.

Phase 6, export half. Everything here is **read-only** — it reads the parse cache
and emits a documented, versioned envelope. Nothing in this module writes to a
save, and it must stay that way; the import half lives separately so that the
dangerous code is never one typo away from the safe code.

The envelope exists because an export is only useful if something can later
validate it. Every export carries:

  - `schemaVersion`   — bumped when the shape changes
  - `kind`            — what was exported, so an importer can refuse a mismatch
  - `worldGuid`       — which world it came from, so a player export cannot be
                        silently applied to a different server without a warning
  - `exportedAt`      — UTC, seconds precision
  - `checksum`        — SHA-256 over the canonical payload

The checksum covers `payload` only, never the envelope, so re-serialising the
metadata (pretty-printing, key order) cannot invalidate a file.

PRIVACY: exports contain real Steam IDs and player names. They are gated on
VIEW_DETAIL and audited, same as the reports.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

KINDS = ("world", "player", "guild", "base", "container")


class ExportError(Exception):
    """Raised when the requested target does not exist."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical(payload: Any) -> str:
    """
    Stable serialisation for checksumming.

    Sorted keys and no incidental whitespace, so the same data always produces
    the same digest regardless of how it was assembled.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def checksum(payload: Any) -> str:
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


def envelope(kind: str, payload: Any, world_guid: str = "", **meta: Any) -> dict:
    """Wrap a payload with everything an importer needs to validate it."""
    if kind not in KINDS:
        raise ExportError(f"Unknown export kind '{kind}'. Known: {', '.join(KINDS)}")

    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": kind,
        "worldGuid": world_guid,
        "exportedAt": _now(),
        "generator": "palworld-dashboard",
        **meta,
        "checksum": checksum(payload),
        "payload": payload,
    }


def verify(document: dict) -> dict:
    """
    Check an export document without trusting it.

    Used by the import half and by the round-trip tests. Returns a report rather
    than raising, because "tell me what is wrong with this file" is the useful
    answer for a human staring at a failed import.
    """
    problems: list[str] = []

    if not isinstance(document, dict):
        return {"ok": False, "problems": ["Not a JSON object"], "kind": None}

    version = document.get("schemaVersion")
    if version != SCHEMA_VERSION:
        problems.append(
            f"Schema version {version!r} — this build writes and reads version {SCHEMA_VERSION}"
        )

    kind = document.get("kind")
    if kind not in KINDS:
        problems.append(f"Unknown export kind {kind!r}")

    if "payload" not in document:
        problems.append("No payload")
    else:
        expected = document.get("checksum")
        actual = checksum(document["payload"])
        if not expected:
            problems.append("No checksum")
        elif expected != actual:
            problems.append(
                f"Checksum mismatch — the payload has been modified since export "
                f"(expected {expected[:12]}…, got {actual[:12]}…)"
            )

    return {
        "ok": not problems,
        "problems": problems,
        "kind": kind,
        "schemaVersion": version,
        "worldGuid": document.get("worldGuid", ""),
        "exportedAt": document.get("exportedAt", ""),
    }


# ─── Builders ────────────────────────────────────────────
#
# Each takes already-parsed sections rather than a gvas tree, so exporting never
# triggers a parse and never touches the disk.


def export_world(sections: dict) -> dict:
    """
    Whole-world summary: counts, guilds, bases, per-base storage, item totals.

    Deliberately NOT every container slot — that is hundreds of megabytes on a
    mature world and nobody wants it as one JSON blob. Container detail is a
    separate, targeted export.
    """
    payload = {
        "counts": sections.get("counts", {}),
        "guilds": sections.get("guilds", []),
        "bases": sections.get("bases", []),
        "baseStorage": sections.get("baseStorage", []),
        "items": sections.get("items", []),
        "players": [_public_player(p) for p in sections.get("players", [])],
    }
    return envelope("world", payload, sections.get("worldGuid", ""))


def _public_player(player: dict) -> dict:
    """Player summary without the parts only a full save export should carry."""
    return {
        key: player.get(key)
        for key in ("uid", "name", "level", "guildId", "guildName", "lastOnline")
        if key in player
    }


def export_player(sections: dict, uid: str) -> dict:
    """One player: their record, their Pals, and the containers they own."""
    players = sections.get("players", [])
    key = uid.replace("-", "").lower()
    player = next(
        (p for p in players if str(p.get("uid", "")).replace("-", "").lower() == key),
        None,
    )
    if player is None:
        raise ExportError(f"No player {uid} in the current parse")

    pals = [p for p in sections.get("pals", []) if p.get("ownerUid") == player.get("uid")]

    return envelope(
        "player",
        {"player": player, "pals": pals, "palCount": len(pals)},
        sections.get("worldGuid", ""),
        playerName=player.get("name", ""),
    )


def export_guild(sections: dict, guild_id: str) -> dict:
    """A guild with its members, bases and their storage."""
    guild = next((g for g in sections.get("guilds", []) if g.get("id") == guild_id), None)
    if guild is None:
        raise ExportError(f"No guild {guild_id} in the current parse")

    bases = [b for b in sections.get("bases", []) if b.get("guildId") == guild_id]
    base_ids = {b["id"] for b in bases}
    storage = [s for s in sections.get("baseStorage", []) if s.get("baseId") in base_ids]

    return envelope(
        "guild",
        {"guild": guild, "bases": bases, "baseStorage": storage},
        sections.get("worldGuid", ""),
        guildName=guild.get("name", ""),
    )


def export_base(sections: dict, base_id: str) -> dict:
    """One base, including the full contents of every container it owns."""
    base = next((b for b in sections.get("bases", []) if b.get("id") == base_id), None)
    if base is None:
        raise ExportError(f"No base {base_id} in the current parse")

    storage = next(
        (s for s in sections.get("baseStorage", []) if s.get("baseId") == base_id), None
    )
    containers = sections.get("containers", {})
    detail = {
        c["containerId"]: containers.get(c["containerId"], [])
        for c in (storage or {}).get("containers", [])
    }

    return envelope(
        "base",
        {"base": base, "storage": storage, "containerContents": detail},
        sections.get("worldGuid", ""),
        baseName=base.get("name", ""),
    )


def export_container(sections: dict, container_id: str) -> dict:
    """A single container's slots, plus who owns it."""
    containers = sections.get("containers", {})
    if container_id not in containers:
        raise ExportError(f"No container {container_id} in the current parse")

    ownership = sections.get("containerOwnership", {})

    return envelope(
        "container",
        {
            "containerId": container_id,
            "owner": ownership.get(container_id),
            "slots": containers[container_id],
        },
        sections.get("worldGuid", ""),
    )


BUILDERS = {
    "world": (export_world, False),
    "player": (export_player, True),
    "guild": (export_guild, True),
    "base": (export_base, True),
    "container": (export_container, True),
}


def build(kind: str, sections: dict, target: Optional[str] = None) -> dict:
    """Dispatch to a builder, enforcing whether a target id is required."""
    if kind not in BUILDERS:
        raise ExportError(f"Unknown export kind '{kind}'. Known: {', '.join(KINDS)}")

    builder, needs_target = BUILDERS[kind]
    if needs_target and not target:
        raise ExportError(f"Exporting a {kind} needs an id")
    if not needs_target and target:
        raise ExportError(f"A {kind} export takes no id")

    return builder(sections, target) if needs_target else builder(sections)


def filename_for(document: dict) -> str:
    """A filename that says what the file is without needing to open it."""
    kind = document.get("kind", "export")
    stamp = (document.get("exportedAt") or "")[:10]
    label = (
        document.get("playerName")
        or document.get("guildName")
        or document.get("baseName")
        or ""
    )
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in label).strip("-")
    middle = f"-{safe[:32]}" if safe else ""
    return f"palworld-{kind}{middle}-{stamp}.json"
