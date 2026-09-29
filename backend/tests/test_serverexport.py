"""Dedicated-server pruning preserves retained identities and shared assets."""
from copy import deepcopy
from types import SimpleNamespace
import hashlib
import uuid
import pytest
import serverexport as export

pytestmark = pytest.mark.usefixtures('fresh_db')


def uid(n): return str(uuid.UUID(int=n))
A, B, G, BASE, PRIV_A, PRIV_B, SHARED, CA, CB, PAL, WORKER = [uid(n) for n in range(10,21)]
def prop(value): return {'struct_type': 'Guid', 'value': value}
def raw(value): return {'value': {'RawData': {'value': value}}}
def cid(value): return {'value': {'ID': prop(value)}}
def container(value): return {'key': {'ID': prop(value)}, 'value': {'Slots': {'value': {'values': []}}}}
def character(instance, player='', owner='', slot='', is_player=False):
    return {'key': {'InstanceId': prop(instance), 'PlayerUId': prop(player or export.ZERO)},
            **raw({'group_id': G, 'object': {'SaveParameter': {'value': {
                'IsPlayer': {'value': is_player}, 'OwnerPlayerUId': prop(owner or export.ZERO),
                'SlotId': {'value': {'ContainerId': cid(slot or export.ZERO)}},
                'OldOwnerPlayerUIds': {'value': {'values': [A]}},
            }}}})}


@pytest.fixture
def graph():
    group = {'key': G, **raw({'group_id': G, 'guild_name': 'Shared guild', 'admin_player_uid': A,
             'players': [{'player_uid': A, 'role': 1}, {'player_uid': B, 'role': 3}],
             'individual_character_handle_ids': [{'guid': A, 'instance_id': CA}, {'guid': B, 'instance_id': CB}, {'guid': A, 'instance_id': PAL}, {'guid': export.ZERO, 'instance_id': WORKER}]})}
    base = {'key': BASE, 'value': {**raw({'id': BASE, 'group_id_belong_to': G})['value'],
            'WorkerDirector': {'value': {'RawData': {'value': {'container_id': SHARED}}}}}}
    world = {'GroupSaveDataMap': {'value': [group]}, 'BaseCampSaveData': {'value': [base]},
             'CharacterSaveParameterMap': {'value': [character(CA,A,is_player=True), character(CB,B,is_player=True), character(PAL,owner=A,slot=PRIV_A), character(WORKER,owner=A,slot=SHARED)]},
             'CharacterContainerSaveData': {'value': [container(PRIV_A),container(PRIV_B),container(SHARED)]},
             'ItemContainerSaveData': {'value': []},
             'MapObjectSaveData': {'value': {'values': [{'Model': raw({'instance_id': uid(50), 'group_id_belong_to': G, 'base_camp_id_belong_to': BASE, 'build_player_uid': A})}]}}}
    players = {u: (SimpleNamespace(properties={'SaveData': {'value': {'PalStorageContainerId': cid(c)}}}), 0) for u,c in ((A,PRIV_A),(B,PRIV_B))}
    return world, players


def test_removing_leader_preserves_shared_workers_and_retained_id(graph):
    world, players = graph
    result = export.prune(world, players, [A])
    chars = export.map_entries(world,'CharacterSaveParameterMap')
    assert {export.character_id(c) for c in chars} == {CB,WORKER}
    worker = next(c for c in chars if export.character_id(c)==WORKER)
    assert export.guid(export.parameter(worker)['OwnerPlayerUId']) == B
    assert result['retainedPlayers'] == 1 and result['removedBases'] == 0
    assert result['leaderChanges'][0]['newLeaderUid'] == B
    assert len(export.map_entries(world,'BaseCampSaveData')) == 1
    assert export.ids(world).isdisjoint({A,CA,PAL,PRIV_A})
    assert B in export.ids(world)


def test_unknown_removed_reference_refuses_instead_of_guessing(graph):
    world, players = graph
    world['NewGameField'] = prop(A)
    with pytest.raises(export.ServerExportError, match='Unresolved removed reference'):
        export.prune(world,players,[A])


def test_opaque_identity_is_not_silently_copied(graph):
    world, players = graph
    world['Opaque'] = b'header' + uuid.UUID(A).bytes + b'tail'
    with pytest.raises(export.ServerExportError, match='opaque'):
        export.prune(world,players,[A])


def test_invalid_leader_choice_is_refused(graph):
    world, players = graph
    with pytest.raises(export.ServerExportError, match='retained member'):
        export.prune(world,players,[A],{G:A})


def test_private_container_shared_with_retained_player_is_refused(graph):
    world, players = graph
    players[B] = deepcopy(players[A])
    with pytest.raises(export.ServerExportError, match='share a private container'):
        export.prune(world,players,[A])


def test_orphan_save_is_not_silently_included(graph):
    world, players = graph
    players[uid(90)] = deepcopy(players[B])
    with pytest.raises(export.ServerExportError, match='orphan player'):
        export.prune(world,players,[A])


def test_unknown_save_filename_refuses_capture(tmp_path):
    (tmp_path/'Level.sav').write_bytes(b'world')
    (tmp_path/'Players').mkdir();(tmp_path/'Players'/'mystery.sav').write_bytes(b'private')
    with pytest.raises(export.ServerExportError, match='unrecognized'):
        export._fingerprint(str(tmp_path))


def test_capture_never_follows_save_symlinks(tmp_path):
    (tmp_path/'Level.sav').symlink_to('/etc/passwd')
    with pytest.raises(export.ServerExportError, match='leaves'):
        export._fingerprint(str(tmp_path))


@pytest.mark.integration
@pytest.mark.slow
def test_real_server_export_roundtrip_is_pruned_and_source_is_unchanged(refworld,palsav_available,tmp_path,monkeypatch):
    import savefiles,soloexport,tarfile
    monkeypatch.setattr(savefiles,'get_default_world_dir',lambda:refworld)
    monkeypatch.setattr(soloexport,'EXPORT_DIR',str(tmp_path/'exports'))
    source = export._fingerprint(refworld)
    files = sorted(p for p in __import__('pathlib').Path(refworld,'Players').glob('*.sav') if not p.stem.endswith('_dps'))
    removed = soloexport._fmt_uid(files[0].stem)
    plan = export.preview(42,[removed])
    result = export.create(42,plan['artifactId'],plan['planHash'])
    archive = export.download(42,result['artifactId'])
    with tarfile.open(archive) as tar:
        names = tar.getnames()
        assert 'Level.sav' in names
        assert not any(removed.replace('-','').lower() in n.lower() for n in names)
        assert all(n in ('Level.sav','LevelMeta.sav') or export.PLAYER_FILE.fullmatch(n) for n in names)
    assert result['removedPlayers']==1 and result['retainedPlayers']==len(files)-1
    assert export._fingerprint(refworld)==source
    with pytest.raises(export.ServerExportError,match='another account'):
        export.download(43,result['artifactId'])


def test_retained_shared_worker_keeps_its_guild_handle(graph):
    world, players = graph
    handles = world['GroupSaveDataMap']['value'][0]['value']['RawData']['value']['individual_character_handle_ids']
    handles[-1]['guid'] = A
    export.prune(world, players, [A])
    assert any(h['instance_id']==WORKER for h in handles)
    assert all(h['guid']!=A for h in handles)


def test_affected_guild_with_missing_member_file_is_refused(graph):
    world, players = graph
    raw = world['GroupSaveDataMap']['value'][0]['value']['RawData']['value']
    raw['players'].append({'player_uid':uid(901)})
    with pytest.raises(export.ServerExportError,match='without a player save'):
        export.prune(world,players,[A])


def test_changed_plan_is_refused_before_loading_a_world(tmp_path, monkeypatch):
    import json
    import exportidentity
    monkeypatch.setattr(export,'_base',lambda:tmp_path)
    folder=tmp_path/('a'*32);folder.mkdir()
    payload={'ownerId':1,'ownerGeneration':exportidentity.current(),'worldDir':'unused','sourceHash':'original','removeUids':[A],'leaders':{},'summary':{},'createdAt':export.time.time()}
    plan_hash=export._hash(payload)
    payload['sourceHash']='tampered'
    (folder/'plan.json').write_text(json.dumps({'planHash':plan_hash,**payload}))
    with pytest.raises(export.ServerExportError,match='does not match'):
        export.create(1,'a'*32,plan_hash)
    assert not (folder/'world.tar.gz').exists()


def test_unrelated_already_empty_guild_is_preserved(graph):
    world, players = graph
    orphan = {'key': uid(902), **raw({'group_id': uid(902), 'players': [], 'admin_player_uid': export.ZERO})}
    world['GroupSaveDataMap']['value'].append(orphan)
    result = export.prune(world,players,[A])
    assert orphan in world['GroupSaveDataMap']['value']
    assert result['removedGuilds']==0


def test_snapshot_fingerprint_is_stable_after_filename_canonicalization(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'Level.sav').write_bytes(b'world')
    (source / 'Players').mkdir()
    (source / 'Players' / ('abcdef12' * 4 + '.sav')).write_bytes(b'player')
    target = tmp_path / 'copy'
    target.mkdir()
    assert export._fingerprint(str(source), target) == export._fingerprint(str(target))


def _stored(owner, instance, record_id):
    from test_storedcharacter import make_native_record
    return {**make_native_record(owner), 'LostPlayerUId': prop(owner),
            'ID': {'value': {'ID': prop(record_id)}},
            'InstanceId': {'value': {'PlayerUId': prop(export.ZERO), 'InstanceId': prop(instance)}}}


def _stored_list(world, entries):
    world['CharacterParameterStorageSaveData'] = {'value': {
        'StoredParameterInfoSaveData': {'value': {'values': entries}},
    }}


def test_lost_character_records_and_their_private_inventory_are_pruned(graph):
    import storedcharacter
    pytest.importorskip('palsav')
    from palsav.archive import UUID
    world, players = graph
    dropped = _stored(A, uid(1001), uid(1002))
    retained = _stored(B, uid(1003), uid(1004))
    expected_retained = deepcopy(retained)
    inventory = uid(1005)
    decoded = storedcharacter.decode(dropped)
    field = deepcopy(decoded['object']['SaveParameter']['value']['OwnerPlayerUId'])
    field['value'] = UUID.from_str(inventory)
    decoded['object']['SaveParameter']['value']['ItemContainerId'] = field
    storedcharacter.update(dropped, decoded)
    world['ItemContainerSaveData']['value'].append(container(inventory))
    entries = [dropped, retained]; _stored_list(world, entries)
    export.prune(world, players, [A])
    assert entries == [expected_retained]
    assert not export.map_entries(world, 'ItemContainerSaveData')
    assert export.ids(world).isdisjoint({A, uid(1001), uid(1002), inventory})


def test_retained_stored_character_history_is_scrubbed_without_changing_owner(graph):
    import storedcharacter
    pytest.importorskip('palsav')
    from palsav.archive import UUID
    world, players = graph
    retained = _stored(B, uid(1003), uid(1004))
    decoded = storedcharacter.decode(retained)
    decoded['object']['SaveParameter']['value']['LastNickNameModifierPlayerUid']['value'] = UUID.from_str(A)
    storedcharacter.update(retained, decoded)
    _stored_list(world, [retained])
    export.prune(world, players, [A])
    parameter = storedcharacter.decode(retained)['object']['SaveParameter']['value']
    assert export.guid(parameter['OwnerPlayerUId']) == B
    assert str(parameter['LastNickNameModifierPlayerUid']['value']) == export.ZERO


def test_disagreeing_stored_character_owner_refuses(graph):
    world, players = graph
    record = _stored(B, uid(1003), uid(1004))
    record['LostPlayerUId'] = prop(A)
    _stored_list(world, [record])
    with pytest.raises(export.ServerExportError, match='ownership disagrees'):
        export.prune(world, players, [A])


def test_stored_character_that_is_also_live_refuses(graph):
    world, players = graph
    _stored_list(world, [_stored(A, PAL, uid(1002))])
    with pytest.raises(export.ServerExportError, match='live character map'):
        export.prune(world, players, [A])


def test_typed_lost_player_reference_removes_the_personal_object(graph):
    world, players = graph
    objects = export.array_entries(world, 'MapObjectSaveData')
    objects[0]['LostPlayerUId'] = prop(A)
    export.prune(world, players, [A])
    assert not objects


def test_stored_inventory_shared_with_retained_character_refuses(graph):
    import storedcharacter
    pytest.importorskip('palsav')
    from palsav.archive import UUID
    world, players = graph
    records = [_stored(A, uid(1001), uid(1002)), _stored(B, uid(1003), uid(1004))]
    inventory = uid(1005)
    world['ItemContainerSaveData']['value'].append(container(inventory))
    for record in records:
        decoded = storedcharacter.decode(record)
        field = deepcopy(decoded['object']['SaveParameter']['value']['OwnerPlayerUId'])
        field['value'] = UUID.from_str(inventory)
        decoded['object']['SaveParameter']['value']['ItemContainerId'] = field
        storedcharacter.update(record, decoded)
    _stored_list(world, records)
    with pytest.raises(export.ServerExportError, match='shares a container'):
        export.prune(world, players, [A])
