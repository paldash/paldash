import gzip
import json
import math
from pathlib import Path
import struct
import sys
from types import SimpleNamespace

import pytest

import dungeons
import gamedata
import referenceguides
import savedstate

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import sourceprovenance
import uassettable


def test_reader_refuses_plausible_misaligned_property_name():
    names = ['Row', 'FishingSpot_A_Ocean_Common', 'None']
    body = struct.pack('<iiiiiiB', 0, 0, 2, 0, 0, 0, 0)
    with pytest.raises(uassettable.TableError, match='property type'):
        uassettable._tag(uassettable._Reader(body, names))


def test_complete_row_walk_requires_declared_row_count(monkeypatch):
    names = ['None', 'First']
    body = struct.pack('<iiiiiiii', 0, 0, 0, 2, 1, 0, 0, 0)
    monkeypatch.setattr(uassettable.upackage, 'read', lambda data: SimpleNamespace(names=names, exports=[SimpleNamespace(data=lambda data: body)]))
    pak = SimpleNamespace(read=lambda path: b'')
    with pytest.raises(uassettable.TableError):
        uassettable.read_table(pak, 'test.uasset')
    body = struct.pack('<iiiiiiii', 0, 0, 0, 1, 1, 0, 0, 0)
    assert uassettable.read_table(pak, 'test.uasset') == {'First': {}}


def test_provenance_includes_values_variants_and_all_opaque_shapes():
    pak = SimpleNamespace(files=['Pal/L10N/en/DT_X.uasset', 'Pal/Data/DT_X.uasset'])
    assert sourceprovenance.canonical_path(pak, 'DT_X') == 'Pal/Data/DT_X.uasset'
    assert sourceprovenance.digest({'x': 1}) != sourceprovenance.digest({'x': 2})
    assert sum(sourceprovenance.opacity(['<MapProperty 8B, undecoded: bad>', '<array of Thing, 2 items, undecoded>',
                                        '<2 x Thing, not tagged>', {'_opaque': 'Vector 24B'}]).values()) == 4


@pytest.mark.parametrize('section', referenceguides.SECTIONS)
def test_all_guide_sections_are_complete_serializable_reference_values(section):
    report = referenceguides.report(section)
    assert report['rows'] and report['columns'] and report['note']
    assert len({r['id'] for r in report['rows']}) == len(report['rows'])
    assert all(set(r['cells']) == set(report['columns']) for r in report['rows'])
    assert all(isinstance(value, (str, int, float)) or value is None for r in report['rows'] for value in r['cells'].values())
    json.dumps(report, allow_nan=False)
    assert len(report['source']['sha256']) == 64


def test_corrected_fishing_invaders_and_base_alternatives():
    data = referenceguides.load()
    assert data['tables']['DT_PalFishingSpotLotteryNameDataTable']['rows'] == 115
    assert any(len(r['alternatives']) > 1 for task in data['baseTasks'] for r in task['requirements'])
    with gzip.open(ROOT / 'backend/data/invaders.json.gz', 'rt') as handle:
        invaders = json.load(handle)
    assert len(invaders['groups']) == 76
    assert sum(map(len, invaders['groups'].values())) == 240
    assert set(invaders['groups']) == set(invaders['rewards'])


def test_slot_probability_remains_separate_from_conditional_weight(monkeypatch):
    monkeypatch.setattr(gamedata, 'economy', lambda: {'slotProbabilities': {'known': {'1': 12.5}}})
    rows = [{'itemId': 'Stone', 'slot': 1, 'weight': 3, 'quantity': 1}, {'itemId': 'Wood', 'slot': 1, 'weight': 1, 'quantity': 1}]
    known = dungeons._slot_shares(rows, 'known')
    assert all(r['slotProbabilityPercent'] == 12.5 for r in known)
    assert all(r['slotProbabilityPercent'] is None for r in dungeons._slot_shares(rows, 'unknown'))


def test_saved_state_never_invents_zero_or_exposes_arbitrary_strings():
    assert savedstate.player({}) == {}
    assert savedstate.supply({}) == {'available': False, 'events': []}
    state = savedstate.player({'RecordData': {'value': {'FishingCountMap': {'value': [{'key': 'SheepBall', 'value': 2}]}}},
                              'CompletedQuestArray_FullRelease': {'value': ['Q1']},
                              'OrderedQuestArray_FullRelease': {'value': {'values': [{'QuestName': {'value': 'Q2'}, 'BlockIndex': {'value': 4}, 'StringMap': {'value': ['private text']}}]}}})
    assert state == {'fishing': [{'id': 'SheepBall', 'count': 2}], 'completedQuests': ['Q1'], 'activeQuests': [{'id': 'Q2', 'block': 4}]}
    assert savedstate.structure({'stored_energy_amount': 0, 'crop_progress_rate': math.nan}) == {'stored_energy_amount': 0}
    supply = savedstate.supply({'SupplySaveData': {'value': {'SupplyInfos': {'value': [{'key': 'private guid', 'value': {'SupplyTime': {'value': 42}, 'SupplySpawnerGuid': 'private guid', 'bWipedOut_Pal': {'value': False}}}]}}}})
    assert supply['events'] == [{'SupplyTime': 42, 'bWipedOut_Pal': False}]
    assert 'private' not in json.dumps(supply)
