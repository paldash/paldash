"""
Export a playable copy of the world with one player's uid remapped.

The use case: someone who played on the dedicated server wants their own copy to
carry into co-op or single-player, or a co-op world is moving onto a server and the
host's character no longer matches the uid their client will present. Palworld
identifies a character by uid in a dozen places, and a mismatch in any one of them
produces a world that loads with the character missing, the guild empty, or the
bases unclaimable.

**This never writes to the live world.** Every other writer here goes through
`backup.guarded_save_write` because it mutates the save in place; this one reads the
world and writes a *new* directory, so the running server's files are untouched and
the whole class of "corrupted the world while producing a copy" cannot happen. It is
the one save-editing feature that is safe to run while the server is up.

That is a deliberate departure from the reference implementation
(`PalWorldSaveTools/fix_host_save.py`), which mutates in place. Producing a copy is
what the operator actually wants, and it costs nothing but disk.

**The target uid is supplied, never inferred.** There is no host-uid constant in
Palworld that this project has been able to verify — the reference implementation
asks the user for both uids and hardcodes nothing. Guessing a rule would silently
produce an unloadable world, so the caller names the target and the plan reports
exactly what will change.

**Rename and swap are different operations, and which one applies is detected.**
If the target uid already exists in the world, the two characters exchange uids —
anything else would leave two characters claiming one identity. If it does not
exist, it is a one-way rename. `plan_export` says which, because "your friend's
character and yours have traded places" is not a surprise anyone should get.

WHERE A UID HIDES
-----------------
Missing any one of these leaves a world that loads and is subtly wrong:

  Players/<UID>.sav   `SaveData.PlayerUId`, `SaveData.IndividualId.PlayerUId`,
                      and the filename itself (uppercase, undashed)
  Players/<UID>_dps.sav  dimensional pal storage, keyed by the same filename rule
  Level.sav           `CharacterSaveParameterMap[].key.PlayerUId`, matched via
                      `InstanceId` rather than by uid — the character record is the
                      authority on which entry belongs to whom
  GroupSaveDataMap    guild `individual_character_handle_ids[].guid`,
                      `admin_player_uid`, `players[].player_uid`
  everywhere          `OwnerPlayerUId`, `owner_player_uid`, `build_player_uid`,
                      `private_lock_player_uid` — on bases, containers, chests and
                      locks, reached by walking the tree rather than by listing the
                      places they are known to appear today
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from typing import Any, Optional

import savefiles

logger = logging.getLogger(__name__)


class SoloExportError(Exception):
    pass


# The remap matches on **value, not key name**, and that decision is measured
# rather than stylistic.
#
# The reference implementation rewrites four named keys — `OwnerPlayerUId`,
# `owner_player_uid`, `build_player_uid`, `private_lock_player_uid`. Counting what
# actually holds a real player uid on the reference world says that list is badly
# incomplete:
#
#     LastNickNameModifierPlayerUid          1,817   not in that list
#     OwnerPlayerUId                         1,740
#     build_player_uid                         973
#     SkinAppliedCharacterId                    12   not in that list
#     player_uid / PlayerUId / admin_player_uid / guid   21
#     LostPlayerUId                              4   not in that list
#     last_guild_name_modifier_player_uid        2   not in that list
#     seller_player_uid                          1   not in that list
#
# A key list would have left 1,836 references pointing at a uid that no longer
# exists — the world would load, and nicknames, lost-item ownership and shop
# listings would quietly belong to nobody. Worse, the list is a promise about a
# schema this project does not control: the next content update adds a field and
# the omission is silent again.
#
# Matching on value is exhaustive and cannot mistake one thing for another, because
# a field holding a player's uid *means* that player — there is no field where a
# player's uid carries some other sense. Nor can it collide with the world's other
# GUIDs: a Palworld player uid is a Steam ID32 followed by zeros
# (`11a11a01-0000-0000-0000-000000000000`), while base camp, guild and character
# instance ids are full-entropy GUIDs.
#
# Kept only for the reference-comparison test, which pins the finding above.
REFERENCE_OWNER_KEYS = (
    "OwnerPlayerUId",
    "owner_player_uid",
    "build_player_uid",
    "private_lock_player_uid",
)

EXPORT_DIR = os.environ.get("SOLO_EXPORT_DIR", "")

# Files copied verbatim into the export. `Level.sav` and the player saves are
# rewritten; everything else is carried across untouched.
#
# `backup/` is excluded for the reason `backupstore` learned the hard way: it holds
# the server's own rotating snapshots, and on the reference world sweeping it in
# turned a 2.1 MB world into 66 MB.
VERBATIM = ("LevelMeta.sav", "WorldOption.sav")


def _fmt_uid(uid: str) -> str:
    """
    Dashed lowercase, the form `Level.sav` stores.

    Accepts either spelling on input because both are in circulation — player
    filenames are uppercase undashed, the world's own references are dashed
    lowercase — and a caller pasting one where the other is expected should not
    silently match nothing.
    """
    raw = str(uid or "").replace("-", "").strip().lower()
    if len(raw) != 32:
        raise SoloExportError(
            f"Not a Palworld player uid: {uid!r}. Expected 32 hex characters, "
            "with or without dashes."
        )
    if any(c not in "0123456789abcdef" for c in raw):
        raise SoloExportError(f"Not a Palworld player uid: {uid!r} is not hexadecimal.")
    return f"{raw[0:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:32]}"


def _file_uid(uid: str) -> str:
    """Uppercase undashed, the form player `.sav` filenames use."""
    return _fmt_uid(uid).replace("-", "").upper()


def _load(path: str):
    from palsav.core import decompress_sav_to_gvas
    from palsav.gvas import GvasFile
    from palsav.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS

    raw = savefiles.read_sav_bytes(path)
    if raw is None:
        raise SoloExportError(f"Could not read {os.path.basename(path)}")
    decompressed, save_type = decompress_sav_to_gvas(raw)
    return GvasFile.read(decompressed, PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES), save_type


def _write(gvas, save_type: int, path: str) -> None:
    from palsav.core import compress_gvas_to_sav
    from palsav.paltypes import PALWORLD_CUSTOM_PROPERTIES

    encoded = compress_gvas_to_sav(gvas.write(PALWORLD_CUSTOM_PROPERTIES), save_type)
    with open(path, "wb") as f:
        f.write(encoded)


def _uid_str(value: Any) -> Optional[str]:
    """
    The dashed lowercase uid a node holds, or None if it does not hold one.

    Three shapes are in circulation and all three appear in a real world:

        UUID(...)                                   a bare value inside RawData
        {'struct_type': 'Guid', 'value': UUID(...)}  a wrapped GVAS property
        '22b22b02-...'                               plain text, in a few places

    `palsav` decodes GUIDs as its own `UUID` class, not `str`. An `isinstance(v, str)`
    test therefore matches nothing at all — which is how a first version of this
    module counted 6,455 uid fields and rewrote zero of them.
    """
    if isinstance(value, dict):
        value = value.get("value")
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip().lower()
    else:
        # palsav.archive.UUID and stdlib uuid.UUID both stringify to the dashed form.
        text = str(value).strip().lower()
    if len(text) != 36 or text.count("-") != 4:
        return None
    return text


def _write_uid(container: Any, key: Any, new_uid: str) -> bool:
    """
    Replace a uid in place, preserving both the container shape and the value type.

    Writing a `str` where `palsav` expects its own `UUID` produces a tree that looks
    right and an encoder that either raises or emits wrong bytes, so the replacement
    is constructed with the same class the original used.
    """
    from palsav.archive import UUID as PalUUID

    current = container[key]
    if isinstance(current, dict) and "value" in current:
        original = current["value"]
        current["value"] = (
            PalUUID.from_str(new_uid) if not isinstance(original, str) else new_uid
        )
        return True
    container[key] = (
        PalUUID.from_str(new_uid) if not isinstance(current, str) else new_uid
    )
    return True


def _walk_uids(node: Any, mapping: dict[str, str], apply: bool) -> int:
    """
    Count, or rewrite, every field holding one of the mapped uids. One visit each.

    **A handled field is not descended into**, and that is the whole subtlety. A
    wrapped GVAS property is `{'struct_type': 'Guid', 'value': UUID(...)}`: the outer
    key matches via `_uid_str`, and a plain recursion would then walk *inside* it and
    match the inner `value` as a second, independent field.

    Counting it twice merely inflates a number. Rewriting it twice is a correctness
    failure that only shows up on a swap, where the mapping is its own inverse — the
    second write maps target back to source and silently undoes the remap on every
    wrapped field, which is most of them. Measured on the reference world: 1,176 of
    3,148 apparent matches were this double visit.
    """
    return _walk_uids_inner(node, mapping, apply)


def _walk_uids_inner(node: Any, mapping: dict[str, str], apply: bool) -> int:
    total = 0
    if isinstance(node, dict):
        for key in list(node.keys()):
            value = node[key]
            uid = _uid_str(value)
            if uid is not None and uid in mapping:
                total += 1
                if apply:
                    _write_uid(node, key, mapping[uid])
                continue          # handled: its inner `value` is the same field
            total += _walk_uids_inner(value, mapping, apply)
    elif isinstance(node, list):
        for index in range(len(node)):
            item = node[index]
            uid = _uid_str(item)
            if uid is not None and uid in mapping:
                total += 1
                if apply:
                    _write_uid(node, index, mapping[uid])
                continue
            total += _walk_uids_inner(item, mapping, apply)
    return total


def _v(node: Any, *keys: str, default: Any = None) -> Any:
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def _world_save_data(gvas) -> dict:
    data = _v(gvas.properties, "worldSaveData", "value")
    if not isinstance(data, dict):
        raise SoloExportError("Level.sav has no worldSaveData — is this a Palworld save?")
    return data


# ─── Planning ────────────────────────────────────────────


def _player_identity(player_gvas) -> dict[str, str]:
    save_data = _v(player_gvas.properties, "SaveData", "value", default={})
    individual = _v(save_data, "IndividualId", "value", default={})
    return {
        "playerUid": str(_v(save_data, "PlayerUId", "value") or ""),
        "instanceId": str(_v(individual, "InstanceId", "value") or ""),
        "palStorageContainerId": str(
            _v(save_data, "PalStorageContainerId", "value", "ID", "value") or ""
        ),
    }


def plan_export(
    source_uid: str, target_uid: str, world_dir: Optional[str] = None
) -> dict[str, Any]:
    """
    What an export would do, without doing any of it.

    Reports `mode` as `rename` or `swap`. The distinction is not cosmetic: a swap
    moves a second player's character onto the source uid, and someone who thought
    they were renaming their own character would otherwise discover afterwards that
    a guildmate's identity moved too.
    """
    source = _fmt_uid(source_uid)
    target = _fmt_uid(target_uid)
    if source == target:
        raise SoloExportError("Source and target uid are the same — nothing to remap.")

    root = world_dir or savefiles.get_default_world_dir()
    if not root or not os.path.isdir(root):
        raise SoloExportError("World directory not found.")

    source_sav = os.path.join(root, "Players", f"{_file_uid(source)}.sav")
    if not os.path.isfile(source_sav):
        raise SoloExportError(
            f"No player save for {source} — expected "
            f"Players/{_file_uid(source)}.sav. Check the uid against the roster."
        )

    target_sav = os.path.join(root, "Players", f"{_file_uid(target)}.sav")
    swap = os.path.isfile(target_sav)

    level_gvas, _ = _load(os.path.join(root, "Level.sav"))
    world = _world_save_data(level_gvas)

    source_player, _ = _load(source_sav)
    source_id = _player_identity(source_player)
    target_id = None
    if swap:
        target_player, _ = _load(target_sav)
        target_id = _player_identity(target_player)

    counts = _count_references(world, source, target)

    # A character record is the authority on which CSPM entry is whose, so a
    # source uid with no matching InstanceId means the player file and the world
    # disagree — exportable, but worth saying so rather than producing a copy whose
    # character never appears.
    if not counts["characterEntries"]:
        logger.warning(
            "Player %s has no CharacterSaveParameterMap entry matching instance %s",
            source, source_id["instanceId"],
        )

    return {
        "mode": "swap" if swap else "rename",
        "sourceUid": source,
        "targetUid": target,
        "sourceInstanceId": source_id["instanceId"],
        "targetInstanceId": (target_id or {}).get("instanceId", ""),
        "hasDps": os.path.isfile(
            os.path.join(root, "Players", f"{_file_uid(source)}_dps.sav")
        ),
        "references": counts,
        "warnings": _plan_warnings(counts, swap, source_id),
        "planHash": _plan_hash(source, target, counts),
    }


def _count_references(world: dict, source: str, target: str) -> dict[str, int]:
    """
    How many places each uid appears, for the preview and for verification.

    The named categories are for the preview's benefit — "you are in 1 guild and own
    973 structures" is what an operator can sanity-check. `total` is the one the
    verification uses, and it comes from the same exhaustive walk that does the
    rewriting, so the two can never disagree about what a complete remap means.
    """
    counts = {
        "characterEntries": 0,
        "targetCharacterEntries": 0,
        "guildHandles": 0,
        "guildAdmin": 0,
        "guildPlayers": 0,
        "total": 0,
    }

    for entry in _v(world, "CharacterSaveParameterMap", "value", default=[]) or []:
        uid = _uid_str(_v(entry, "key", "PlayerUId"))
        if uid == source:
            counts["characterEntries"] += 1
        elif uid == target:
            counts["targetCharacterEntries"] += 1

    for group in _v(world, "GroupSaveDataMap", "value", default=[]) or []:
        raw = _v(group, "value", "RawData", "value", default={})
        if not isinstance(raw, dict) or "players" not in raw:
            continue
        for handle in raw.get("individual_character_handle_ids") or []:
            if _uid_str(handle.get("guid")) in (source, target):
                counts["guildHandles"] += 1
        if _uid_str(raw.get("admin_player_uid")) in (source, target):
            counts["guildAdmin"] += 1
        for player in raw.get("players") or []:
            if _uid_str(player.get("player_uid")) in (source, target):
                counts["guildPlayers"] += 1

    counts["total"] = _walk_uids(world, {source: source, target: target}, apply=False)
    return counts


def _plan_warnings(counts: dict, swap: bool, source_id: dict) -> list[str]:
    warnings = []
    if swap:
        warnings.append(
            "The target uid already has a character in this world, so the two will "
            "exchange identities. Both players' characters, Pals and guild "
            "membership move with their uid."
        )
    if not counts["characterEntries"]:
        warnings.append(
            "This player has no character record in Level.sav. The export will "
            "still be produced, but the character may not appear in-game."
        )
    if not counts["guildPlayers"] and not counts["guildAdmin"]:
        warnings.append(
            "This player is not a member of any guild in this world, so no guild "
            "references will be updated."
        )
    return warnings


def _plan_hash(source: str, target: str, counts: dict) -> str:
    """
    Binds an apply to the world the preview was taken against.

    Same guarantee `saveimport` gives: the reference counts change the moment
    anyone plays, so a stale plan is refused rather than applied to a world that
    moved underneath it.
    """
    payload = json.dumps(
        {"source": source, "target": target, "counts": counts}, sort_keys=True
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# ─── Applying ────────────────────────────────────────────


def _remap_level(world: dict, mapping: dict[str, str]) -> dict:
    """
    Apply the uid mapping to `Level.sav`'s tree, in place.

    One exhaustive value-based pass, rather than a list of the places a uid is known
    to appear. See the note on `REFERENCE_OWNER_KEYS` for why: a key list measured
    against this very world was short by 1,836 references.

    A swap is safe in a single pass because the mapping is applied to each field
    exactly once — `_walk_uids` tracks which it has already touched, so a mapping
    that is its own inverse cannot undo itself on an aliased subtree.
    """
    before = {
        "characterEntries": 0, "guildHandles": 0, "guildAdmin": 0, "guildPlayers": 0,
    }
    uids = tuple(mapping)
    for entry in _v(world, "CharacterSaveParameterMap", "value", default=[]) or []:
        if _uid_str(_v(entry, "key", "PlayerUId")) in uids:
            before["characterEntries"] += 1
    for group in _v(world, "GroupSaveDataMap", "value", default=[]) or []:
        raw = _v(group, "value", "RawData", "value", default={})
        if not isinstance(raw, dict) or "players" not in raw:
            continue
        for handle in raw.get("individual_character_handle_ids") or []:
            if _uid_str(handle.get("guid")) in uids:
                before["guildHandles"] += 1
        if _uid_str(raw.get("admin_player_uid")) in uids:
            before["guildAdmin"] += 1
        for player in raw.get("players") or []:
            if _uid_str(player.get("player_uid")) in uids:
                before["guildPlayers"] += 1

    before["total"] = _walk_uids(world, mapping, apply=True)
    return before


def _remap_player_file(player_gvas, new_uid: str, mapping: dict[str, str]) -> None:
    """
    Rewrite a player save's own idea of who it belongs to.

    `SaveData.PlayerUId` and `SaveData.IndividualId.PlayerUId` are the two that must
    change or the file and its filename disagree. The same exhaustive walk runs over
    the rest of the file, because a player save carries its own copies of ownership
    fields and leaving those behind is the same silent breakage.
    """
    save_data = _v(player_gvas.properties, "SaveData", "value", default={})
    if not isinstance(save_data, dict):
        raise SoloExportError("Player save has no SaveData")
    if "PlayerUId" not in save_data:
        raise SoloExportError("Player save has no PlayerUId")

    _walk_uids(player_gvas.properties, mapping, apply=True)

    # Asserted rather than assumed: if the walk did not reach these two, the file
    # would be written claiming its old identity under its new name.
    if _uid_str(save_data.get("PlayerUId")) != new_uid:
        _write_uid(save_data, "PlayerUId", new_uid)
    individual = _v(save_data, "IndividualId", "value", default={})
    if isinstance(individual, dict) and "PlayerUId" in individual:
        if _uid_str(individual.get("PlayerUId")) != new_uid:
            _write_uid(individual, "PlayerUId", new_uid)


def apply_export(
    source_uid: str,
    target_uid: str,
    world_dir: Optional[str] = None,
    destination: Optional[str] = None,
    expected_plan_hash: Optional[str] = None,
) -> dict[str, Any]:
    """
    Write a remapped copy of the world. The source world is never modified.

    Re-plans against the live world rather than trusting the caller's preview, and
    refuses if `expected_plan_hash` no longer matches — a world that moved between
    preview and apply must not be exported blind.

    The copy is assembled in a temporary directory and moved into place only after
    verification, so an interrupted export leaves no half-written world looking like
    a finished one.
    """
    plan = plan_export(source_uid, target_uid, world_dir)
    if expected_plan_hash and expected_plan_hash != plan["planHash"]:
        raise SoloExportError(
            "The world changed since this export was previewed. Preview again — "
            "the uid references it was planned against no longer match."
        )

    source = plan["sourceUid"]
    target = plan["targetUid"]
    root = world_dir or savefiles.get_default_world_dir()

    mapping = {source: target}
    if plan["mode"] == "swap":
        mapping[target] = source

    out_root = destination or _default_destination(source, target)
    staging = tempfile.mkdtemp(prefix="solo-export-", dir=os.path.dirname(out_root))

    try:
        os.makedirs(os.path.join(staging, "Players"), exist_ok=True)

        level_gvas, level_type = _load(os.path.join(root, "Level.sav"))
        applied = _remap_level(_world_save_data(level_gvas), mapping)
        _write(level_gvas, level_type, os.path.join(staging, "Level.sav"))

        _copy_players(root, staging, mapping)
        for name in VERBATIM:
            src = os.path.join(root, name)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(staging, name))

        report = _verify(staging, plan, mapping)
        os.replace(staging, out_root)
        staging = ""
    finally:
        if staging:
            shutil.rmtree(staging, ignore_errors=True)

    logger.info(
        "Exported world copy to %s (%s %s -> %s)", out_root, plan["mode"], source, target
    )
    return {
        "ok": True,
        "mode": plan["mode"],
        "destination": out_root,
        "sourceUid": source,
        "targetUid": target,
        "applied": applied,
        "verification": report,
        "warnings": plan["warnings"],
        "sizeBytes": _tree_size(out_root),
    }


def _default_destination(source: str, target: str) -> str:
    base = EXPORT_DIR or os.path.join(
        os.environ.get("BACKUP_DIR", tempfile.gettempdir()), "exports"
    )
    os.makedirs(base, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return os.path.join(base, f"world-{source[:8]}-to-{target[:8]}-{stamp}")


def _copy_players(root: str, staging: str, mapping: dict[str, str]) -> None:
    """
    Copy every player save, rewriting and renaming the remapped ones.

    Players not involved keep their files byte for byte: this is a copy of the
    world, and dropping the others would be the destructive "solo extraction" this
    module deliberately does not do.

    Renames are computed into the staging directory, so a swap never has to worry
    about one rename clobbering the other's source — the classic bug in an in-place
    implementation.
    """
    players_dir = os.path.join(root, "Players")
    if not os.path.isdir(players_dir):
        raise SoloExportError("World has no Players directory")

    for name in sorted(os.listdir(players_dir)):
        if not name.lower().endswith(".sav"):
            continue
        src = os.path.join(players_dir, name)
        stem = name[:-4]
        is_dps = stem.lower().endswith("_dps")
        file_uid = stem[:-4] if is_dps else stem

        try:
            dashed = _fmt_uid(file_uid)
        except SoloExportError:
            # A filename that is not a uid is carried across untouched rather than
            # dropped: it is not ours to interpret, and losing it silently would be
            # worse than copying something unrecognised.
            shutil.copy2(src, os.path.join(staging, "Players", name))
            continue

        new_uid = mapping.get(dashed)
        if not new_uid:
            shutil.copy2(src, os.path.join(staging, "Players", name))
            continue

        suffix = "_dps" if is_dps else ""
        out_name = f"{_file_uid(new_uid)}{suffix}.sav"
        out_path = os.path.join(staging, "Players", out_name)

        if is_dps:
            # Dimensional pal storage carries no uid inside it — the filename is
            # the whole binding — so it is copied rather than rewritten.
            shutil.copy2(src, out_path)
            continue

        player_gvas, player_type = _load(src)
        _remap_player_file(player_gvas, new_uid, mapping)
        _write(player_gvas, player_type, out_path)


# ─── Verification ────────────────────────────────────────


def _verify(staging: str, plan: dict, mapping: dict[str, str]) -> dict[str, Any]:
    """
    Re-read the written world and check the remap actually landed.

    Verification is on the **re-read**, not the in-memory tree, for the reason
    `saveedit` learned: an encoder bug produces a correct-looking tree and a broken
    file, and only reading it back catches that.

    The assertion is about *completeness*, and its shape depends on the operation.
    A rename must leave zero references to the source uid — anything remaining is a
    reference the walk missed, and the character will be half-detached in game. A
    swap cannot assert that: the source uid is legitimately still present, now
    belonging to the other player.
    """
    level_gvas, _ = _load(os.path.join(staging, "Level.sav"))
    world = _world_save_data(level_gvas)

    source = plan["sourceUid"]
    target = plan["targetUid"]
    after = _count_references(world, source, target)

    problems: list[str] = []

    if plan["mode"] == "rename":
        # The completeness assertion. A rename must leave zero references to the
        # source uid anywhere in the tree — a survivor is a reference the walk
        # missed, and it would leave the character half-detached from its own
        # structures in a way that loads perfectly and is wrong.
        stale = _walk_uids(world, {source: source}, apply=False)
        if stale:
            problems.append(
                f"{stale} reference(s) to the old uid {source} survived the remap"
            )

        expected_file = os.path.join(
            staging, "Players", f"{_file_uid(target)}.sav"
        )
        if not os.path.isfile(expected_file):
            problems.append(f"the renamed player save {os.path.basename(expected_file)} is missing")
        if os.path.isfile(os.path.join(staging, "Players", f"{_file_uid(source)}.sav")):
            problems.append("the old player save is still present under its old name")

    else:
        # A swap must preserve both identities: each uid should still be attached
        # to exactly as many character entries as the other one had before.
        for uid, label in ((target, "target"), (source, "source")):
            if not os.path.isfile(
                os.path.join(staging, "Players", f"{_file_uid(uid)}.sav")
            ):
                problems.append(f"the {label} player save is missing after the swap")

    # The written player file must actually claim its new uid — the rename alone
    # would produce a file whose name and contents disagree, which is how a
    # character loads as a stranger.
    target_file = os.path.join(staging, "Players", f"{_file_uid(target)}.sav")
    if os.path.isfile(target_file):
        written, _ = _load(target_file)
        claimed = _player_identity(written)["playerUid"].lower()
        if claimed != target:
            problems.append(
                f"the exported player save claims uid {claimed}, not {target}"
            )

    if problems:
        raise SoloExportError(
            "Export verification failed, so nothing was kept: " + "; ".join(problems)
        )

    return {"ok": True, "referencesAfter": after, "checked": "re-read from disk"}


def _tree_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


# ─── Packaging ───────────────────────────────────────────


def archive_export(export_dir: str) -> dict[str, Any]:
    """
    Bundle a finished export as `.tar.gz` with a SHA-256, for download.

    Written with an explicit walk rather than `shutil.make_archive` so the contents
    are the ones chosen here — the same lesson `backupstore` records about
    `copytree` sweeping in the server's own rotating snapshots.
    """
    import tarfile

    if not os.path.isdir(export_dir):
        raise SoloExportError(f"No export at {export_dir}")

    archive_path = export_dir.rstrip(os.sep) + ".tar.gz"
    with tarfile.open(archive_path, "w:gz") as tar:
        for root, _dirs, files in os.walk(export_dir):
            for name in sorted(files):
                full = os.path.join(root, name)
                tar.add(full, arcname=os.path.relpath(full, export_dir))

    digest = hashlib.sha256()
    with open(archive_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)

    return {
        "path": archive_path,
        "sizeBytes": os.path.getsize(archive_path),
        "sha256": digest.hexdigest(),
    }
