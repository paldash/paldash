"""Offline gameplay guides with checked source joins and explicit uncertainty."""
from functools import lru_cache
import gzip
import json
from pathlib import Path

import gamedata

PATH = Path(__file__).parent / 'data/reference_guides.json.gz'
SECTIONS = {
    'fishing': ('Fishing encounters', 'Weights compare encounters within the same fishing group. The recorded respawn interval is a reference value, not a live countdown. Location-to-group assignments are not inferred from similar names.'),
    'ponds': ('Fish ponds', 'Pond size groups and rewards are joined by their game IDs. Weight is relative within the same size group.'),
    'baits': ('Fishing bait', 'These are the game’s recorded modifiers. A multiplier is not an unconditional catch probability.'),
    'expeditions': ('Expeditions', 'Reference requirements and reward groups. Recommended strength does not establish a success probability.'),
    'operating': ('Operating Table', 'Recorded passive acquisition prices and required items. These are separate from breeding inheritance.'),
    'souls': ('Pal Soul upgrades', 'Materials and reset prices for stat upgrades. These are not condenser costs.'),
    'crops': ('Crops', 'Growth time and work requirements are separate stages. These values do not establish total throughput without workers and server modifiers.'),
    'cages': ('Caged Pals', 'Regional rosters, level bands and relative weights. A region group is not a verified assignment to an individual map cage.'),
    'recruitment': ('Recruitment rosters', 'The base-level band points directly to its roster table. Weights are relative within that roster; appeal conditions can affect recruitment.'),
    'appeals': ('Recruitment appeals', 'Passives explicitly listed as appeals by the game. The table does not give recruitment probabilities.'),
    'stacking': ('Passive stacking', 'Flags apply to the exact effect ID shown. They are not applied globally to a passive or used to invent a stat formula.'),
    'baseTasks': ('Base-level tasks', 'Each construction requirement preserves the game’s list of alternatives. Worker counts and building counts are task requirements, not server caps.'),
}


@lru_cache(maxsize=1)
def load():
    with gzip.open(PATH, 'rt') as handle:
        return json.load(handle)


def index():
    data = load()
    return {'sections': [{'id': key, 'label': value[0], 'rows': len(data[key])} for key, value in SECTIONS.items()]}


def _levels(row):
    return f"{row['levelMin']}–{row['levelMax']}"


def _reward(group):
    rows = (gamedata.economy().get('lottery') or {}).get(group)
    if rows is None:
        return 'Reward group unavailable: ' + group
    names = list(dict.fromkeys(gamedata.item_name(r['itemId']) for r in rows))
    return ', '.join(names)


def report(section):
    if section not in SECTIONS:
        raise KeyError(section)
    data = load()
    rows = []
    for row in data[section]:
        if section in ('fishing', 'ponds', 'cages', 'recruitment'):
            fields = {'Pal': gamedata.character_name(row['speciesId']) if row.get('speciesId') else 'Item encounter',
                      'Group': row.get('group', row.get('roster')), 'Levels': _levels(row), 'Relative weight': row['weight']}
            if section == 'fishing':
                fields.update({'Time': 'Any recorded time' if row['time'] == 'Undefined' else row['time'],
                               'Difficulty': row['difficulty'], 'Durability decrease': row['durability'],
                               'Respawn reference (s)': row['respawnSeconds']})
            if section in ('fishing', 'ponds'):
                fields['Reward items'] = _reward(row['rewardGroup'])
            if section == 'recruitment':
                fields['Base levels'] = f"{row['baseLevelMin']}–{row['baseLevelMax']}"
        elif section == 'baits':
            fields = {'Bait': gamedata.item_name(row['itemId']),
                      'Hit bar ×': row['HitBarSizeRate'], 'Search ×': row['SearchProbabilityRate'],
                      'Miss fight ×': row['MissFightAmountRate'], 'Success fight ×': row['SuccessFightAmountRate'],
                      'Initial progress (%)': row['StartProgressAmountPercent'],
                      'Enemy extra drop (%)': row['EnemyAddDropPercent'], 'Item extra drop (%)': row['ItemLotteryAddDropPercent']}
        elif section == 'expeditions':
            condition = row['challenge']
            boss = str(condition['DefeatBossType']).split('::')[-1]
            difficulty = str(condition['DefeatBossDifficulty']).split('::')[-1]
            fields = {'Expedition': row.get('name') or gamedata.humanize(row['id']), 'Duration (s)': row['RequiredSeconds'],
                      'Recommended strength': row['RecommendedStrength'], 'Team limit': row['MaxCharacterNum'],
                      'Required element': str(row['RequiredElementType']).split('::')[-1],
                      'Element count': row['RequiredElementNum'], 'Unlock condition': row['ReleaseCondition'],
                      'Challenge': f"{boss} ({difficulty}); hard bosses: {condition['DefeatHardBossNum']}",
                      'Reward items': _reward(row['rewardGroup'])}
        elif section == 'operating':
            fields = {'Passive': gamedata.passive_name(row['passiveId']), 'Money': row['money'],
                      'Required item': gamedata.item_name(row['itemId']) if row['itemId'] else 'None recorded'}
        elif section == 'souls':
            fields = {'Rank': row['rank'], 'Soul': gamedata.item_name(row['itemId']), 'Count': row['count'], 'Reset money': row['resetMoney']}
        elif section == 'crops':
            fields = {'Crop': gamedata.item_name(row['itemId']), 'Yield': row['count'], 'Growth (s)': row['growSeconds'],
                      'Seeding work': row['seedingWork'], 'Watering work': row['wateringWork'], 'Harvest work': row['harvestWork'],
                      'Materials': ', '.join(f"{gamedata.item_name(m['itemId'])} ×{m['count']}" for m in row['materials'])}
        elif section == 'appeals':
            fields = {'Passive': gamedata.passive_name(row['passiveId']), 'Appeal': 'Flagged by the recruitment table'}
        elif section == 'stacking':
            fields = {'Effect': gamedata.humanize(row['id']), 'Exact effect ID': row['id'],
                      'Highest value only': 'Yes' if row['highestOnly'] else 'No', 'Fixed value': 'Yes' if row['fixedValue'] else 'No'}
        else:
            fields = {'Level': row['level'], 'Workers': row['workers'], 'Construction requirements': '; '.join(
                '(' + ' or '.join(gamedata.structure_name(a) for a in requirement['alternatives']) + f") ×{requirement['count']}"
                for requirement in row['requirements']) or 'None recorded'}
        rows.append({'id': row['id'], 'cells': fields})
    return {'id': section, 'title': SECTIONS[section][0], 'note': SECTIONS[section][1],
            'columns': list(rows[0]['cells']) if rows else [], 'rows': rows,
            'source': data['source'], 'tables': data['tables']}
