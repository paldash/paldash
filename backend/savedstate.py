"""Small, explicit projections of recorded state; absent fields stay absent.

No GUIDs, arbitrary quest strings or estimated event schedules are exposed.
The caller applies the same ownership/privacy gates as other player/base data.
"""
import math


def value(node):
    while isinstance(node, dict) and 'value' in node:
        node = node['value']
    return node


def field(node, key):
    node = value(node)
    return value(node.get(key)) if isinstance(node, dict) else None


def array(node):
    node = value(node)
    if isinstance(node, dict):
        node = node.get('values')
    return node if isinstance(node, list) else []


def number(node):
    node = value(node)
    return node if isinstance(node, (int, float)) and not isinstance(node, bool) and math.isfinite(node) else None


def player(save):
    record = field(save, 'RecordData')
    out = {}
    for name, key in [('fishing', 'FishingCountMap'), ('arena', 'ArenaSoloClearCount')]:
        raw = field(record, key)
        if raw is None:
            continue
        out[name] = [{'id': str(entry['key']), 'count': number(entry.get('value'))}
                     for entry in array(raw) if isinstance(entry, dict) and entry.get('key')
                     and number(entry.get('value')) is not None]
    completed = field(save, 'CompletedQuestArray_FullRelease')
    if completed is not None:
        out['completedQuests'] = [x for x in array(completed) if isinstance(x, str)]
    active = field(save, 'OrderedQuestArray_FullRelease')
    if active is not None:
        out['activeQuests'] = [{'id': field(row, 'QuestName'), 'block': number(field(row, 'BlockIndex'))}
                               for row in array(active) if isinstance(field(row, 'QuestName'), str)]
    return out


def structure(concrete):
    out = {}
    for key in ('crop_progress_rate', 'crop_progress_rate_value', 'stored_energy_amount',
                'consume_energy_speed', 'generate_energy_rate_by_worker'):
        n = number(field(concrete, key))
        if n is not None:
            out[key] = n
    for key in ('growup_progress_time', 'growup_required_time'):
        n = number(field(field(concrete, 'state_machine'), key))
        if n is not None:
            out[key] = n
    crop = field(concrete, 'crop_data_id')
    if isinstance(crop, str) and crop and crop != 'None':
        out['crop_data_id'] = crop
    return out


def supply(world):
    source = field(world, 'SupplySaveData')
    if source is None:
        return {'available': False, 'events': []}
    out = {'available': True, 'events': []}
    for key in ('LastLotteryTime', 'LastSupplyTime'):
        n = number(field(source, key))
        if n is not None:
            out[key] = n
    for entry in array(field(source, 'SupplyInfos')):
        row = entry.get('value', {}) if isinstance(entry, dict) else {}
        event = {}
        for key in ('SupplyTime', 'SupplyLandedTime'):
            n = number(field(row, key))
            if n is not None:
                event[key] = n
        kind = field(row, 'SupplyType')
        if isinstance(kind, str):
            event['type'] = kind.split('::')[-1]
        for key in ('bWipedOut_NPC', 'bWipedOut_Pal'):
            flag = field(row, key)
            if isinstance(flag, bool):
                event[key] = flag
        if event:
            out['events'].append(event)
    return out
