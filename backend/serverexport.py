"""Dedicated-server copies with selected player identities removed.

This module edits only private snapshot trees. It never remaps a retained player
identity, never writes the source, and never falls back to an unpruned archive.
An unfamiliar surviving reference to a removed identity or object refuses export.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import exportscope
import savefiles
import soloexport

ZERO = '00000000-0000-0000-0000-000000000000'
_UID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
_v = exportscope._v


class ServerExportError(Exception):
    pass


def guid(node: Any) -> str:
    if isinstance(node, dict):
        if node.get('struct_type') == 'Guid':
            node = node.get('value')
        else:
            return ''
    if not isinstance(node, str) and type(node).__name__ != 'UUID':
        return ''
    value = str(node).lower()
    return value if _UID.fullmatch(value) and value != ZERO else ''


def references(node: Any, path: str = ''):
    """Yield GUIDs and their schema paths without stringifying whole subtrees."""
    found = guid(node)
    if found:
        yield path, found
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from references(value, f'{path}.{key}')
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from references(value, path + '[]')


def ids(node: Any) -> set[str]:
    return {value for _, value in references(node)}


def map_entries(world, name):
    entries = _v(world, name, 'value', default=[])
    if not isinstance(entries, list):
        raise ServerExportError(f'Unsupported {name} shape')
    return entries


def array_entries(world, name):
    entries = _v(world, name, 'value', 'values', default=[])
    if not isinstance(entries, list):
        raise ServerExportError(f'Unsupported {name} shape')
    return entries


def player_containers(tree):
    save = _v(tree.properties, 'SaveData', 'value', default={})
    containers = ids(save.get('InventoryInfo', {}))
    for key in ('PalStorageContainerId', 'OtomoCharacterContainerId'):
        containers.update(ids(save.get(key, {})))
    return containers


def parameter(entry):
    return _v(entry, 'value', 'RawData', 'value', 'object', 'SaveParameter', 'value', default={})


def character_id(entry):
    return guid(_v(entry, 'key', 'InstanceId', default=None))


def _key_id(entry):
    key = entry.get('key')
    return guid(key) or guid(_v(key, 'value')) or guid(_v(key, 'ID'))


def _set_uid(container, key, value):
    current = container[key]
    if isinstance(current, dict) and current.get('struct_type') == 'Guid':
        return _set_uid(current, 'value', value)
    if isinstance(current, str):
        container[key] = value
    elif type(current).__name__ == 'UUID' and hasattr(type(current), 'from_str'):
        container[key] = type(current).from_str(value)
    else:
        raise ServerExportError('Could not preserve the stored identity field type')


def _scrub_history(node, removed):
    # Only metadata whose semantics are known. Unknown references are refused by
    # the final scan, rather than being silently rewritten into a different owner.
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if key == 'OldOwnerPlayerUIds':
                values = _v(value, 'value', 'values', default=None)
                if not isinstance(values, list):
                    raise ServerExportError('Unsupported ownership-history shape')
                values[:] = [v for v in values if guid(v) not in removed]
            elif key in ('LastNickNameModifierPlayerUid', 'last_guild_name_modifier_player_uid') and guid(value) in removed:
                _set_uid(node, key, ZERO)
            else:
                _scrub_history(value, removed)
    elif isinstance(node, list):
        for value in node:
            _scrub_history(value, removed)


def _assert_absent(node, removed, label):
    for path, value in references(node):
        if value in removed:
            raise ServerExportError(f'Unresolved removed reference in {label}{path}; no export was produced')
    # Opaque native payloads cannot hide a known removed GUID. UE GUIDs use four
    # little-endian 32-bit words; UUID.bytes_le additionally covers other readers.
    needles = set()
    for value in removed:
        u = uuid.UUID(value); raw = u.bytes
        needles.update((raw, u.bytes_le, b''.join(raw[i:i+4][::-1] for i in range(0,16,4))))
    def opaque(value):
        if isinstance(value, (bytes, bytearray)):
            # Every encoding is exactly 16 bytes. A regex with thousands of
            # alternatives retried them at every byte of a large native block;
            # fixed-width set lookups preserve the same match rule in linear work.
            data = bytes(value)
            if needles and any(data[i:i+16] in needles for i in range(len(data) - 15)):
                raise ServerExportError(f'An opaque {label} field contains a removed identity; no export was produced')
        elif isinstance(value, dict):
            for v in value.values(): opaque(v)
        elif isinstance(value, list):
            # Undecoded byte arrays are often represented as integer lists.
            if len(value) >= 16 and all(type(v) is int and 0 <= v <= 255 for v in value): opaque(bytes(value))
            else:
                for v in value: opaque(v)
    opaque(node)


def _guilds(world):
    result = []
    seen = set()
    for group in map_entries(world, 'GroupSaveDataMap'):
        raw = _v(group, 'value', 'RawData', 'value', default={})
        if 'players' not in raw:
            continue
        gid = guid(raw.get('group_id'))
        members = [guid(p.get('player_uid')) for p in raw['players']]
        if not gid or gid in seen or any(not u for u in members) or len(set(members)) != len(members):
            raise ServerExportError('Unsupported guild membership data')
        seen.add(gid)
        result.append({'guildId': gid, 'playerUids': members, 'adminUid': guid(raw.get('admin_player_uid'))})
    return result


def _prune_stored_characters(world, removed, known_containers, active_characters):
    """Lost Pals have independent records, outside the live character map."""
    if 'CharacterParameterStorageSaveData' not in world:
        return set(), set(), set(), set()
    import storedcharacter
    entries = _v(world, 'CharacterParameterStorageSaveData', 'value',
                 'StoredParameterInfoSaveData', 'value', 'values', default=None)
    if not isinstance(entries, list):
        raise ServerExportError('Unsupported stored-character list')
    kept = []; record_ids = set(); instances = set()
    dropped_records = set(); dropped_characters = set()
    dropped_items = set(); kept_items = set()
    item_containers = {_key_id(c) for c in map_entries(world, 'ItemContainerSaveData')}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ServerExportError('Unsupported stored-character record')
        owner = guid(entry.get('LostPlayerUId'))
        record_id = guid(_v(entry, 'ID', 'value', 'ID'))
        instance = guid(_v(entry, 'InstanceId', 'value', 'InstanceId'))
        if not owner or not record_id or not instance or record_id in record_ids or instance in instances:
            raise ServerExportError('Unsupported stored-character identity')
        if instance in active_characters:
            raise ServerExportError('A stored character also exists in the live character map')
        attached_player = guid(_v(entry, 'InstanceId', 'value', 'PlayerUId'))
        if attached_player and attached_player != owner:
            raise ServerExportError('Stored-character ownership disagrees with its instance record')
        record_ids.add(record_id); instances.add(instance)
        try:
            decoded = storedcharacter.decode(entry)
        except storedcharacter.StoredCharacterError as error:
            raise ServerExportError(str(error)) from error
        parameter = decoded['object']['SaveParameter']['value']
        if guid(parameter.get('OwnerPlayerUId')) != owner:
            raise ServerExportError('Stored-character ownership disagrees with its lost-player record')
        inventory = ids(parameter.get('EquipItemContainerId', {})) | ids(parameter.get('ItemContainerId', {}))
        if inventory - item_containers or (ids(parameter) & known_containers) - inventory:
            raise ServerExportError('A stored character has an unresolved container reference')
        if owner in removed:
            dropped_records.add(record_id); dropped_characters.add(instance)
            dropped_items.update(inventory)
        else:
            kept_items.update(inventory)
            _scrub_history(decoded, removed)
            try:
                storedcharacter.update(entry, decoded)
            except storedcharacter.StoredCharacterError as error:
                raise ServerExportError(str(error)) from error
            kept.append(entry)
    entries[:] = kept
    return dropped_records, dropped_characters, dropped_items, kept_items


def prune(world: dict, players: dict, remove_uids: list[str], leaders: dict | None = None) -> dict:
    """Modify a disposable parsed snapshot and independently verify its scope."""
    removed = {soloexport._fmt_uid(u) for u in remove_uids}
    if not removed or ZERO in removed:
        raise ServerExportError('Select at least one valid player to remove')
    if not removed <= set(players):
        raise ServerExportError('A selected player has no recognized player save')
    retained = set(players) - removed
    if not retained:
        raise ServerExportError('Keep at least one player in the exported server world')
    chosen = {soloexport._fmt_uid(k): soloexport._fmt_uid(v) for k, v in (leaders or {}).items()}
    guilds = _guilds(world)
    known_members = {u for g in guilds for u in g['playerUids']}
    if set(players) - known_members:
        raise ServerExportError('An orphan player save needs explicit review before exporting')
    # An already-empty guild is unrelated to the selected players. Do not turn
    # this targeted export into a general orphan-guild cleanup.
    drop_guilds = {g['guildId'] for g in guilds if set(g['playerUids']) & removed and set(g['playerUids']) <= removed}
    leader_by_guild = {}
    transitions = []
    for g in guilds:
        if set(g['playerUids']) & removed and set(g['playerUids']) - set(players):
            raise ServerExportError('An affected guild has a member without a player save; review it before exporting')
        remaining = sorted(set(g['playerUids']) - removed)
        if not remaining:
            continue
        leader = g['adminUid'] if g['adminUid'] in remaining else chosen.get(g['guildId'], remaining[0])
        if leader not in remaining:
            raise ServerExportError('A replacement guild leader must be a retained member')
        leader_by_guild[g['guildId']] = leader
        if leader != g['adminUid']:
            transitions.append({'guildId': g['guildId'], 'newLeaderUid': leader, 'retainedMembers': remaining})
    if set(chosen) - set(leader_by_guild):
        raise ServerExportError('A leader choice names an unknown or removed guild')

    from saveedit import _totals
    original_items = _totals(map_entries(world, 'ItemContainerSaveData'))
    private = set().union(*(player_containers(players[u][0]) for u in removed))
    kept_private = set().union(*(player_containers(players[u][0]) for u in retained))
    if private & kept_private:
        raise ServerExportError('Selected and retained players share a private container')
    dropped_ids = set(removed) | drop_guilds
    dropped_containers = set(private)
    shared_containers = set()
    dropped_bases = set()
    for base in map_entries(world, 'BaseCampSaveData'):
        gid = guid(_v(base, 'value', 'RawData', 'value', 'group_id_belong_to'))
        bucket = dropped_containers if gid in drop_guilds else shared_containers
        for path, value in references(base):
            if 'container' in path.lower(): bucket.add(value)
        if gid in drop_guilds:
            dropped_bases.add(_key_id(base) or guid(_v(base, 'value', 'RawData', 'value', 'id')))
    dropped_bases.discard(''); dropped_ids.update(dropped_bases)
    # The full writer decoder exposes named container IDs; the display parser
    # intentionally leaves these fields opaque. Require a real container join.
    known_containers = {_key_id(c) for name in ('CharacterContainerSaveData', 'ItemContainerSaveData') for c in map_entries(world, name)}
    for base in map_entries(world, 'BaseCampSaveData'):
        worker = _v(base, 'value', 'WorkerDirector', 'value', 'RawData', 'value', default=None)
        if worker is None:
            continue
        cid = guid(worker.get('container_id')) if isinstance(worker, dict) else ''
        if cid not in known_containers:
            raise ServerExportError('A base worker container could not be resolved safely')
        gid = guid(_v(base, 'value', 'RawData', 'value', 'group_id_belong_to'))
        (dropped_containers if gid in drop_guilds else shared_containers).add(cid)
    for entry in map_entries(world, 'GuildExtraSaveDataMap'):
        storage = _v(entry, 'value', 'GuildItemStorage', 'value', 'RawData', 'value', default=None)
        if storage is None:
            continue
        cid = guid(storage.get('container_id')) if isinstance(storage, dict) else ''
        if cid not in known_containers:
            raise ServerExportError('A guild chest container could not be resolved safely')
        (dropped_containers if _key_id(entry) in drop_guilds else shared_containers).add(cid)

    stored_ids, dropped_characters, stored_items, kept_stored_items = _prune_stored_characters(
        world, removed, known_containers,
        {character_id(c) for c in map_entries(world, 'CharacterSaveParameterMap')},
    )
    dropped_ids.update(stored_ids | dropped_characters)
    dropped_containers.update(stored_items)
    kept_private.update(kept_stored_items)

    objects = array_entries(world, 'MapObjectSaveData'); objects_keep = []; reassigned = 0
    for obj in objects:
        raw = _v(obj, 'Model', 'value', 'RawData', 'value', default={})
        gid = guid(raw.get('group_id_belong_to'))
        drop = guid(raw.get('base_camp_id_belong_to')) in dropped_bases or gid in drop_guilds
        # A dropped player's loose structure or death bag has no surviving
        # shared guild to own it. Shared structures transfer to that guild's leader.
        if guid(raw.get('build_player_uid')) in removed and gid not in leader_by_guild: drop = True
        if any(value in removed and path.endswith(('.LostPlayerUId', '.LostPlayerUId.value'))
               for path, value in references(obj)): drop = True
        for path, value in references(obj):
            if 'target_container_id' in path:
                (dropped_containers if drop else shared_containers).add(value)
        if drop:
            for field in ('instance_id', 'concrete_model_instance_id'):
                value = guid(raw.get(field))
                if value: dropped_ids.add(value)
            continue
        for field in ('build_player_uid', 'private_lock_player_uid'):
            if guid(raw.get(field)) in removed:
                if gid not in leader_by_guild:
                    raise ServerExportError('A shared structure needs a retained guild owner')
                _set_uid(raw, field, leader_by_guild[gid]); reassigned += 1
        objects_keep.append(obj)
    objects[:] = objects_keep
    if dropped_containers & (kept_private | shared_containers):
        raise ServerExportError('A removed asset shares a container with retained data')

    characters = map_entries(world, 'CharacterSaveParameterMap'); character_keep = []
    for entry in characters:
        sp = parameter(entry)
        raw = _v(entry, 'value', 'RawData', 'value', default={})
        gid = guid(raw.get('group_id'))
        owner = guid(sp.get('OwnerPlayerUId'))
        uid = guid(_v(entry, 'key', 'PlayerUId'))
        slot = ids(sp.get('SlotId', {}))
        is_player = _v(sp, 'IsPlayer', 'value', default=False) is True
        shared = bool(slot & shared_containers) and gid in leader_by_guild
        drop = gid in drop_guilds or bool(slot & private) or (is_player and uid in removed) or (owner in removed and not shared)
        if drop:
            dropped_characters.add(character_id(entry))
            for field in ('EquipItemContainerId', 'ItemContainerId'):
                dropped_containers.update(ids(sp.get(field, {})))
        else:
            if owner in removed:
                _set_uid(sp, 'OwnerPlayerUId', leader_by_guild[gid]); reassigned += 1
            character_keep.append(entry)
    dropped_characters.discard(''); dropped_ids.update(dropped_characters)
    characters[:] = character_keep
    dropped_item_refs = set().union(*(ids(c) for c in map_entries(world, 'ItemContainerSaveData') if _key_id(c) in dropped_containers))
    for name in ('ItemContainerSaveData', 'CharacterContainerSaveData'):
        entries = map_entries(world, name)
        entries[:] = [e for e in entries if _key_id(e) not in dropped_containers]
    dropped_ids.update(dropped_containers)
    for stage in map_entries(world, 'MapObjectSpawnerInStageSaveData'):
        for spawner in _v(stage, 'value', 'SpawnerDataMapByLevelObjectInstanceId', 'value', default=[]):
            items = _v(spawner, 'value', 'ItemMap', 'value', default=[])
            if not isinstance(items, list):
                raise ServerExportError('Unsupported staged-spawner item map')
            items[:] = [e for e in items if guid(_v(e, 'value', 'MapObjectInstanceId')) not in dropped_ids]

    for container in map_entries(world, 'CharacterContainerSaveData'):
        for slot in _v(container, 'value', 'Slots', 'value', 'values', default=[]):
            raw = _v(slot, 'RawData', 'value', default={})
            if guid(raw.get('instance_id')) in dropped_characters:
                _set_uid(raw, 'instance_id', ZERO)
                if 'player_uid' in raw: _set_uid(raw, 'player_uid', ZERO)
            elif guid(raw.get('player_uid')) in removed:
                _set_uid(raw, 'player_uid', ZERO)

    groups = map_entries(world, 'GroupSaveDataMap')
    groups[:] = [e for e in groups if guid(_v(e, 'value', 'RawData', 'value', 'group_id')) not in drop_guilds]
    for entry in groups:
        raw = _v(entry, 'value', 'RawData', 'value', default={})
        gid = guid(raw.get('group_id'))
        if 'players' in raw:
            raw['players'][:] = [p for p in raw['players'] if guid(p.get('player_uid')) not in removed]
            if guid(raw.get('admin_player_uid')) in removed:
                _set_uid(raw, 'admin_player_uid', leader_by_guild[gid])
                for player in raw['players']:
                    if guid(player.get('player_uid')) == leader_by_guild[gid]: player['role'] = 1
        if 'individual_character_handle_ids' in raw:
            handles = raw['individual_character_handle_ids']
            handles[:] = [h for h in handles if guid(h.get('instance_id')) not in dropped_characters]
            kept_keys = {character_id(c): guid(_v(c, 'key', 'PlayerUId')) or ZERO for c in characters}
            for handle in handles:
                if guid(handle.get('guid')) in removed:
                    instance = guid(handle.get('instance_id'))
                    if instance not in kept_keys:
                        raise ServerExportError('A guild character handle could not be resolved')
                    _set_uid(handle, 'guid', kept_keys[instance])
    for name in ('BaseCampSaveData', 'GuildExtraSaveDataMap', 'InvaderSaveData'):
        entries = map_entries(world, name)
        entries[:] = [e for e in entries if _key_id(e) not in dropped_ids]
    works = array_entries(world, 'WorkSaveData')
    def removed_work(work):
        raw = _v(work, 'RawData', 'value', default={})
        return any(guid(raw.get(k)) in dropped_ids for k in ('base_camp_id_belong_to', 'owner_map_object_model_id', 'owner_map_object_concrete_model_id')) or guid(_v(raw, 'transform', 'map_object_instance_id')) in dropped_ids
    works[:] = [w for w in works if not removed_work(w)]
    for work in works:
        assignments = _v(work, 'WorkAssignMap', 'value', default=[])
        if isinstance(assignments, list):
            assignments[:] = [a for a in assignments if not ids(a) & dropped_characters]
    # Dynamic item records belong to slots, so preserve anything still referenced
    # and remove only records that were referenced by the containers we dropped.
    import dynamicitem
    live_item_ids = set().union(*(ids(c) for c in map_entries(world, 'ItemContainerSaveData')))
    dynamic = array_entries(world, 'DynamicItemSaveData')
    dynamic[:] = [e for e in dynamic if dynamicitem._local_id(e) not in (dropped_item_refs - live_item_ids)]
    expected_items = {k: v for k, v in original_items.items() if k not in dropped_containers}
    if _totals(map_entries(world, 'ItemContainerSaveData')) != expected_items:
        raise ServerExportError('Retained items changed during pruning')
    _scrub_history(world, removed)
    _assert_absent(world, dropped_ids, 'world')
    for uid in retained:
        tree, _ = players[uid]
        _scrub_history(tree.properties, removed)
        _assert_absent(tree.properties, dropped_ids, 'retained player')
    return {
        'retainedPlayers': len(retained), 'removedPlayers': len(removed),
        'removedGuilds': len(drop_guilds), 'removedBases': len(dropped_bases),
        'removedCharacters': len(dropped_characters), 'removedContainers': len(dropped_containers),
        'reassignedSharedReferences': reassigned, 'leaderChanges': transitions,
        '_removedIds': sorted(dropped_ids), '_retainedUids': sorted(retained),
    }


_ID = re.compile(r'^[0-9a-f]{32}$')
PLAYER_FILE = re.compile(r'^Players/([0-9a-fA-F]{32})(_dps)?\.sav$', re.IGNORECASE)
PLAN_TTL = 3600
ARCHIVE_TTL = 7 * 86400
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024


def _base() -> Path:
    root = Path(soloexport.EXPORT_DIR or os.path.join(savefiles.BACKUP_DIR, 'exports')) / 'server'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


@contextmanager
def _operation():
    import selfexport
    if not selfexport._RUNNING.acquire(blocking=False):
        raise ServerExportError('Another export is running; try again after it finishes')
    try:
        sweep()
        yield
    finally:
        selfexport._RUNNING.release()


def sweep():
    """Only expire this feature's generated directories, never arbitrary paths."""
    now = time.time()
    for entry in _base().iterdir():
        if not _ID.fullmatch(entry.name) or not entry.is_dir() or entry.is_symlink():
            continue
        archive = entry / 'world.tar.gz'
        ttl = ARCHIVE_TTL if archive.is_file() else PLAN_TTL
        if now - entry.stat().st_mtime > ttl:
            shutil.rmtree(entry)


def _files(root: str):
    files = []
    canonical = set()
    import backupstore
    for absolute, relative in backupstore.collect_world_files(root):
        relative = relative.replace(os.sep, '/')
        if relative not in ('Level.sav', 'LevelMeta.sav', 'WorldOption.sav') and not PLAYER_FILE.fullmatch(relative):
            raise ServerExportError('The world contains an unrecognized save filename; review it before exporting')
        if os.path.islink(absolute) or os.path.commonpath([os.path.realpath(absolute), os.path.realpath(root)]) != os.path.realpath(root):
            raise ServerExportError('A save path leaves the selected world')
        if relative.lower() in canonical:
            raise ServerExportError('Duplicate player filenames need review before exporting')
        canonical.add(relative.lower())
        files.append((absolute, relative))
    if not any(relative == 'Level.sav' for _, relative in files):
        raise ServerExportError('No Level.sav is available')
    return files


def _fingerprint(root: str, destination: Path | None = None) -> str:
    files = sorted(_files(root), key=lambda entry: entry[1].casefold())
    before = {relative: savefiles._stat_key(absolute) for absolute, relative in files}
    digest = hashlib.sha256()
    for absolute, relative in files:
        data = savefiles.read_sav_bytes(absolute)
        if data is None: raise ServerExportError('A save changed during capture; wait for autosave to finish and preview again')
        match = PLAYER_FILE.fullmatch(relative)
        copied_name = ('Players/' + match[1].upper() + ('_dps' if match[2] else '') + '.sav') if match else relative
        digest.update(copied_name.encode()); digest.update(b'\0'); digest.update(hashlib.sha256(data).digest())
        if destination:
            target = destination / copied_name; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    after = {relative: savefiles._stat_key(absolute) for absolute, relative in _files(root)}
    if before != after:
        raise ServerExportError('The world changed during capture; preview again after autosave finishes')
    return digest.hexdigest()


def _load_snapshot(root: Path):
    level, kind = soloexport._load(str(root / 'Level.sav'))
    players = {}
    for path in sorted((root / 'Players').glob('*.sav')):
        if path.stem.lower().endswith('_dps'): continue
        uid = soloexport._fmt_uid(path.stem)
        tree, player_type = soloexport._load(str(path))
        stored = soloexport._player_identity(tree).get('playerUid')
        if not stored or soloexport._fmt_uid(stored) != uid:
            raise ServerExportError('A player filename disagrees with its stored identity')
        individual_uid = guid(_v(tree.properties, 'SaveData', 'value', 'IndividualId', 'value', 'PlayerUId'))
        if individual_uid and individual_uid != uid:
            raise ServerExportError('A player save contains inconsistent identities')
        if uid in players:
            raise ServerExportError('Duplicate player filenames need review before exporting')
        players[uid] = (tree, player_type)
    for path in (root / 'Players').iterdir():
        if not path.name.lower().endswith('_dps.sav'):
            continue
        if soloexport._fmt_uid(path.stem[:-4]) not in players:
            raise ServerExportError('An orphan dimensional-storage file needs review before exporting')
    return level, kind, players


def _public(summary):
    return {k: v for k, v in summary.items() if not k.startswith('_')}


def _hash(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def preview(owner_id: int, remove_uids: list[str], leaders: dict | None = None, world_dir: str | None = None):
    import exportidentity
    with _operation():
        root = world_dir or savefiles.get_default_world_dir()
        if not root: raise ServerExportError('No world directory is selected')
        if os.path.commonpath([str(_base().resolve()), os.path.realpath(root)]) == os.path.realpath(root):
            raise ServerExportError('The export directory must be outside the source world')
        # Bound preview snapshots as well as finished archives. Limit one pending
        # preview per account; replacing it invalidates its old plan identifier.
        for pending in _base().glob('*/plan.json'):
            if pending.parent.is_symlink() or not _ID.fullmatch(pending.parent.name):
                continue
            if not (pending.parent / 'world.tar.gz').exists() and json.loads(pending.read_text()).get('ownerId') == owner_id:
                shutil.rmtree(pending.parent)
        used = sum(p.stat().st_size for p in _base().rglob('*') if p.is_file())
        source_size = sum(os.stat(p).st_size for p, _ in _files(root))
        import exportlimits
        try:
            exportlimits.check_storage(str(_base().parent), source_size * 3)
        except OSError as error:
            raise ServerExportError(str(error)) from error
        if used + source_size * 3 > MAX_ARCHIVE_BYTES:
            raise ServerExportError('Server export storage quota reached; wait for older exports to expire')
        artifact_id = uuid.uuid4().hex
        folder = _base() / artifact_id; folder.mkdir(mode=0o700)
        try:
            snapshot = folder / 'snapshot'; snapshot.mkdir()
            fingerprint = _fingerprint(root, snapshot)
            level, _, players = _load_snapshot(snapshot)
            summary = prune(soloexport._world_save_data(level), players, remove_uids, leaders)
            payload = {'ownerId': owner_id, 'ownerGeneration': exportidentity.current(),
                       'worldDir': os.path.realpath(root), 'sourceHash': fingerprint,
                       'removeUids': sorted({soloexport._fmt_uid(u) for u in remove_uids}),
                       'leaders': leaders or {}, 'summary': _public(summary), 'createdAt': time.time()}
            plan_hash = _hash(payload)
            (folder / 'plan.json').write_text(json.dumps({'planHash': plan_hash, **payload}))
            return {'artifactId': artifact_id, 'planHash': plan_hash, **_public(summary),
                    'expiresInSeconds': PLAN_TTL, 'mode': 'dedicated-server',
                    'note': 'Retained player IDs stay unchanged. Shared assets transfer to the shown guild leader. Empty guilds and their assets are removed. The receiving server supplies its own settings.'}
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise


def _plan(owner_id, artifact_id):
    if not _ID.fullmatch(artifact_id): raise ServerExportError('Invalid export identifier')
    folder = _base() / artifact_id
    if folder.is_symlink(): raise ServerExportError('Invalid export storage')
    try: data = json.loads((folder / 'plan.json').read_text())
    except (OSError, ValueError): raise ServerExportError('Export preview is unavailable or expired') from None
    if data['ownerId'] != owner_id: raise ServerExportError('Export preview belongs to another account')
    import exportidentity
    if data.get('ownerGeneration') != exportidentity.current():
        raise ServerExportError('This export belongs to an earlier dashboard database; preview again after recovery')
    return folder, data


def create(owner_id: int, artifact_id: str, plan_hash: str):
    import jobs
    with _operation():
        jobs.checkpoint('Verifying the captured world and preview')
        folder, plan = _plan(owner_id, artifact_id)
        if plan['planHash'] != plan_hash or _hash({k:v for k,v in plan.items() if k != 'planHash'}) != plan_hash:
            raise ServerExportError('Export plan does not match the preview')
        if time.time() - plan['createdAt'] > PLAN_TTL:
            raise ServerExportError('Export preview expired; preview again')
        root = savefiles.get_default_world_dir()
        if not root or os.path.realpath(root) != plan['worldDir'] or _fingerprint(root) != plan['sourceHash']:
            raise ServerExportError('The source world changed since preview; preview again')
        snapshot = folder / 'snapshot'
        if _fingerprint(str(snapshot)) != plan['sourceHash']:
            raise ServerExportError('The private snapshot failed verification')
        output = folder / 'output'
        output.mkdir(mode=0o700, exist_ok=False)
        try:
            jobs.checkpoint('Parsing and pruning the private copy')
            level, kind, players = _load_snapshot(snapshot)
            world = soloexport._world_save_data(level)
            summary = prune(world, players, plan['removeUids'], plan['leaders'])
            if _public(summary) != plan['summary']:
                raise ServerExportError('The pruning result differs from the preview')
            soloexport._write(level, kind, str(output / 'Level.sav'))
            (output / 'Players').mkdir()
            for uid in summary['_retainedUids']:
                tree, player_type = players[uid]
                name = soloexport._file_uid(uid)
                soloexport._write(tree, player_type, str(output / 'Players' / (name + '.sav')))
                dps = next((p for p in (snapshot / 'Players').iterdir() if p.name.lower() == (name + '_dps.sav').lower()), None)
                if dps is not None:
                    tree, dps_type = soloexport._load(str(dps))
                    _scrub_history(tree.properties, set(plan['removeUids']))
                    _assert_absent(tree.properties, set(summary['_removedIds']), 'retained storage')
                    soloexport._write(tree, dps_type, str(output / 'Players' / dps.name))
            # WorldOption and server INI are deliberately not exported: the new
            # dedicated server owns its configuration and credentials.
            meta = snapshot / 'LevelMeta.sav'
            if meta.is_file():
                tree, meta_type = soloexport._load(str(meta))
                _assert_absent(tree.properties, set(summary['_removedIds']), 'world metadata')
                soloexport._write(tree, meta_type, str(output / meta.name))
            jobs.checkpoint('Re-reading player identities and retained items')
            reread, _, retained = _load_snapshot(output)
            if set(retained) != set(summary['_retainedUids']):
                raise ServerExportError('Retained player identities failed read-back verification')
            _assert_absent(reread.properties, set(summary['_removedIds']), 'serialized world')
            from saveedit import _totals
            if _totals(map_entries(soloexport._world_save_data(reread), 'ItemContainerSaveData')) != _totals(map_entries(world, 'ItemContainerSaveData')):
                raise ServerExportError('Retained item quantities changed during serialization')
            for tree, _ in retained.values(): _assert_absent(tree.properties, set(summary['_removedIds']), 'serialized player')
            # Every save, including dimensional storage, must parse after writing.
            for file in output.rglob('*.sav'):
                tree, _ = soloexport._load(str(file))
                _assert_absent(tree.properties, set(summary['_removedIds']), 'serialized save')
            used = sum(p.stat().st_size for p in _base().glob('*/world.tar.gz'))
            raw_size = sum(p.stat().st_size for p in output.rglob('*.sav'))
            if used + raw_size > MAX_ARCHIVE_BYTES:
                raise ServerExportError('Server export storage quota reached; remove or expire older exports')
            archive = soloexport.archive_export(str(output))
            final = folder / 'world.tar.gz'; os.replace(archive['path'], final)
            result = {'artifactId': artifact_id, 'mode': 'dedicated-server', 'sha256': archive['sha256'],
                      'sizeBytes': archive['sizeBytes'], **_public(summary)}
            (folder / 'result.json').write_text(json.dumps(result)); os.utime(folder, None)
            shutil.rmtree(snapshot)
            return result
        except BaseException:
            for path in (folder / 'output.tar.gz', folder / 'world.tar.gz', folder / 'result.json'):
                path.unlink(missing_ok=True)
            raise
        finally:
            shutil.rmtree(output, ignore_errors=True)


def download(owner_id: int, artifact_id: str):
    sweep()
    folder, _ = _plan(owner_id, artifact_id)
    path = folder / 'world.tar.gz'
    if not path.is_file(): raise ServerExportError('Export archive is unavailable or expired')
    result = json.loads((folder / 'result.json').read_text())
    import backupstore
    if backupstore._sha256_file(str(path)) != result['sha256']:
        raise ServerExportError('Export archive failed checksum verification')
    return path
