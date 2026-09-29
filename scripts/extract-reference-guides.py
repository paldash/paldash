#!/usr/bin/env python3
"""Bundle verified gameplay reference tables with joins and per-table provenance.

Weights stay within their own group. Native defaults, success probabilities and
unverified farm throughput are never synthesized from these reference values.
"""
from collections import defaultdict
import gzip
import json
from pathlib import Path
import struct
import sys

import palpak
import uassettable
import upackage
from sourceprovenance import canonical_path, digest, opacity, archive
from jsonout import write_json

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'backend/data/reference_guides.json.gz'


def build(pak=None):
    pak = pak or palpak.Pak()
    sources = {}
    def read(name):
        path = canonical_path(pak, name)
        rows = uassettable.read_table(pak, path)
        sources[name] = {'path': path, 'rows': len(rows), 'rowDigest': digest(rows), 'opaqueFields': opacity(rows)}
        return rows
    def enum(value):
        return str(value).split('::')[-1]
    def key(value):
        return value.get('Key') if isinstance(value, dict) else value
    with gzip.open(ROOT / 'backend/data/gamedata.json.gz', 'rt') as handle:
        catalogue = json.load(handle)
    known = {kind: {str(k).lower() for k in catalogue[kind]} for kind in ('pals', 'npcs', 'items', 'passives', 'structures')}
    def check(kind, ident):
        if ident in (None, '', 'None'):
            return None
        if str(ident).lower() not in known[kind] and not (kind == 'pals' and str(ident).lower() in known['npcs']):
            raise ValueError(f'Unresolved {kind} ID: {ident}')
        return ident
    with gzip.open(ROOT / 'backend/data/economy.json.gz', 'rt') as handle:
        lottery = json.load(handle)['lottery']
    lottery_keys = {k.lower(): k for k in lottery}
    if len(lottery_keys) != len(lottery):
        raise ValueError('Ambiguous lottery group names')
    def reward(ident):
        if ident.lower() not in lottery_keys:
            raise ValueError(f'Unresolved reward group: {ident}')
        return lottery_keys[ident.lower()]

    fish_groups = read('DT_PalFishingSpotLotteryNameDataTable')
    shadows = read('DT_PalFishShadowDataTable')
    fishing = []
    for ident, row in read('DT_PalFishingSpotLotteryDataTable').items():
        group = row['LotteryName']
        if group not in fish_groups or row['FishShadowId'] not in shadows:
            raise ValueError('Fishing group/shadow join failed')
        shadow = shadows[row['FishShadowId']]
        fishing.append({'id': ident, 'group': group, 'speciesId': check('pals', shadow['PalId']),
                        'weight': row['Weight'], 'time': enum(row['OnlyTime']),
                        'levelMin': row['MinLevel'], 'levelMax': row['MaxLevel'],
                        'difficulty': row['Difficulty'], 'durability': row['DecreaseDurability'],
                        'rewardGroup': reward(row['GainItemLotteryName']),
                        'respawnSeconds': fish_groups[group]['RespawnTime']})
    ponds = []
    pond_names = read('DT_PalFishPondLotteryNameDataTable')
    for ident, row in read('DT_PalFishPondLotteryDataTable').items():
        if row['LotteryName'] not in pond_names:
            raise ValueError('Fish pond size group is unresolved')
        ponds.append({'id': ident, 'group': row['LotteryName'], 'speciesId': check('pals', row['CharacterId']),
                      'weight': row['Weight'], 'levelMin': row['CharacterLevelMin'], 'levelMax': row['CharacterLevelMax'],
                      'rewardGroup': reward(row['GainItemLotteryName'])})
    baits = [{'id': k, 'itemId': check('items', k), **v} for k, v in read('DT_FishingBaitItem').items()]

    challenges = read('DT_CharacterTeamMissionChallengeConditionDataTable')
    import l10n
    mission_names = l10n.strings('DT_CharacterTeamMissionText', pak=pak)
    sources['DT_CharacterTeamMissionText:en'] = {'path': 'L10N/en/Pal/DataTable/Text/DT_CharacterTeamMissionText',
                                              'rows': len(mission_names), 'rowDigest': digest(mission_names), 'opaqueFields': {}}
    missions = []
    for ident, row in read('DT_CharacterTeamMissionDataTable').items():
        condition = row['ChallengeCondition']
        if condition not in challenges:
            raise ValueError('Expedition challenge condition is unresolved')
        if row['TitleTextId'] not in mission_names:
            raise ValueError('Expedition title localization join failed')
        missions.append({'id': ident, 'name': mission_names[row['TitleTextId']], **row, 'challenge': challenges[condition], 'rewardGroup': reward(row['ItemFieldLotteryName'])})
    operations = [{'id': k, 'passiveId': check('passives', v['PassiveSkill']), 'money': v['Price'],
                   'itemId': check('items', v['RequireItemId'])} for k, v in read('DT_OperatingTablePassiveSkillDataTable').items()]
    souls = [{'id': k, 'rank': v['Rank'], 'itemId': check('items', v['RequiredStaticItemId']),
              'count': v['RequiredItemNum'], 'resetMoney': v['ResetRequiredMoney']} for k, v in read('DT_CharacterUpgradeMasterDataTable').items()]
    crops = [{'id': k, 'itemId': check('items', v['CropItemId']), 'count': v['CropItemNum'],
              'growSeconds': v['GrowupTime'], 'seedingWork': v['SeedingWorkAmount'],
              'wateringWork': v['WateringWorkAmount'], 'harvestWork': v['HarvestWorkAmount'],
              'materials': [{'itemId': check('items', v[f'MaterialItem{i}_Id']), 'count': v[f'MaterialItem{i}_Num']}
                            for i in (1, 2) if v[f'MaterialItem{i}_Id'] not in ('None', '')]}
             for k, v in read('DT_MapObjectFarmCrop').items()]
    cages = [{'id': k, 'group': v['FieldName'], 'speciesId': check('pals', v['PalID']), 'weight': v['Weight'],
              'levelMin': v['MinLevel'], 'levelMax': v['MaxLevel']} for k, v in read('DT_CapturedCagePal').items()]

    rosters = {}
    for path in sorted(pak.files):
        name = Path(path).stem
        if path.endswith('.uasset') and name.startswith('DT_PalRecruitMonster_') and '/L10N/' not in path:
            rosters[name] = [{'id': k, 'speciesId': check('pals', key(v['PalName'])), 'weight': v['Weight'],
                             'levelMin': v['LevelMin'], 'levelMax': v['LevelMax']} for k, v in read(name).items()]
    # This four-byte reference is checked against the package's actual import
    # table. Inferring a biome/rank join from row order would be unsound.
    path = canonical_path(pak, 'DT_PalRecruitDataTable')
    package = upackage.read(pak.read(path))
    original = uassettable._value
    def object_ref(reader, typ, size, extra):
        if typ == 'ObjectProperty' and size == 4:
            index = reader.i32()
            if index >= 0 or not 0 <= -index - 1 < len(package.imports):
                raise ValueError('Unsupported recruitment object reference')
            kind, name = package.imports[-index - 1]
            if kind != 'DataTable':
                reader.o -= 4  # RowStruct in the table header is not a roster.
                return original(reader, typ, size, extra)
            if name not in rosters:
                raise ValueError('Recruitment reference is not a known roster table')
            return name
        return original(reader, typ, size, extra)
    uassettable._value = object_ref
    try:
        ranks = read('DT_PalRecruitDataTable')
    finally:
        uassettable._value = original
    recruitment = []
    for rank, row in ranks.items():
        for member in rosters[row['PalRecruitMonsterInfo']]:
            recruitment.append({**member, 'id': rank + ':' + member['id'], 'rank': rank,
                                'baseLevelMin': row['BaseCampLevelMin'], 'baseLevelMax': row['BaseCampLevelMax'],
                                'roster': row['PalRecruitMonsterInfo'].removeprefix('DT_PalRecruitMonster_')})
    appeals = [{'id': k, 'passiveId': check('passives', v['PassiveSkill']), 'textId': v['TextId']}
               for k, v in read('DT_PalRecruitAppealDataTable').items()]
    stacking = [{'id': k, 'highestOnly': v['bIsHighestOnly'], 'fixedValue': v['bIsFixedValue']}
                for k, v in read('DT_PassiveSkillEffectCondition').items()]
    tasks = []
    for ident, row in read('DT_BaseCampTask').items():
        requirements = []
        for i in (1, 2, 3):
            alternatives = row[f'BuildObject{i}']
            if not isinstance(alternatives, list):
                raise ValueError('Base construction alternatives must remain a list')
            if alternatives:
                requirements.append({'alternatives': [check('structures', a) for a in alternatives], 'count': row[f'BuildObjectNum{i}']})
        tasks.append({'id': ident, 'level': row['Level'], 'workers': row['WorkerNum'], 'requirements': requirements})
    return {'source': archive(pak), 'tables': sources, 'fishing': fishing, 'ponds': ponds, 'baits': baits,
            'expeditions': missions, 'operating': operations, 'souls': souls, 'crops': crops, 'cages': cages,
            'recruitment': recruitment, 'appeals': appeals, 'stacking': stacking, 'baseTasks': tasks}


if __name__ == '__main__':
    result = build()
    write_json(str(OUT), result)
    print('Verified and bundled:', {k: len(v) for k, v in result.items() if isinstance(v, list)})
