"""Tabulate overlap of task, family, source and environment in official train.

AI-assisted descriptive tabulation in OpenCode / OpenAI GPT family, GPT-6.2
Astra routing group, 2026-11-20. Exact model release date unavailable.
"""

import argparse
from collections import defaultdict
import csv
from decimal import Decimal
import itertools
import json
import math
from pathlib import Path
from statistics import median


ROOT = Path(__file__).resolve().parents[1]


def rows(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def groups(data, key):
    result = defaultdict(list)
    for row in data:
        result[row[key]].append(row)
    return result


def budget_bounds(rules, summary):
    family_duration = {r['family']: Decimal(str(r['median_annotation_s'])) for r in summary
                       if r['scope'] == 'train' and r['environment'] == 'all'}
    tasks = rows(rules/'tasks.csv')
    zones = {r['zone_id']: (float(r['x']), float(r['y'])) for r in rows(rules/'service_zones.csv')}
    config = json.loads((rules/'common_conditions.json').read_text(), parse_float=Decimal)
    costs, service = config['fees'], config['service']
    start = config['initial_zone']
    for case in config['cases']:
        jobs = [r for r in tasks if r['case_id'] == case['case_id']]
        required = {r['zone_id'] for r in jobs} - {start}
        route_lower = min(sum(math.dist(zones[a], zones[b]) for a, b in zip((start,)+route, route))
                          for route in itertools.permutations(required))
        medians = [max(service['reserve_min_s'], service['alpha']*family_duration[r['family_id']]
                       + service['beta_s']) for r in jobs]
        for label, reserved in [('minimum', len(jobs)*service['reserve_min_s']), ('family_median', sum(medians))]:
            fee = (len(jobs)*costs['service_fixed'] + reserved*costs['reserve_per_second']
                   + Decimal(str(route_lower))*costs['distance_per_unit'])
            available = case['cost_budget']-fee
            cap = min(case['observation_limit'], math.floor(available/config['observation']['cost'])) if available >= 0 else None
            print(case['case_id'], label, 'all-job execution cost lower bound', fee, 'review cap under full execution', cap)


def analyze(rules, accounts):
    records = [r for r in rows(rules/'record_split.csv') if r['split'] == 'train']
    index = {r['episode_uuid']: r for r in records}
    segments = [r for r in rows(rules/'source_segment_index.csv') if r['split'] == 'train']
    windows = {r['record']: r for r in rows(accounts) if r['method'] == 'no_request'}
    segments_by_record = groups(segments, 'episode_uuid')
    assert segments_by_record.keys() == index.keys() and len(records) == len(windows)
    record_rows = []
    for i, record in enumerate(records, 1):
        obs = windows[f'T{i:02}']
        assert (obs['dataset'], obs['environment']) == (record['dataset'], record['environment_id'])
        members = segments_by_record[record['episode_uuid']]
        if any((r['environment_id'], r['dataset']) != (record['environment_id'], record['dataset']) for r in members):
            raise ValueError('Inconsistent environment or dataset')
        family, = {r['family_id'] for r in members}
        task, = {r['task'] for r in members}
        record_rows.append(dict(**record, family_id=family, task=task, windows=int(obs['windows'])))

    result = []
    for scope, subset in [('train', record_rows), ('with_wrist_windows', [r for r in record_rows if r['windows']])]:
        included = {r['episode_uuid'] for r in subset}
        seg = [r for r in segments if r['episode_uuid'] in included]
        families = groups(subset, 'family_id')
        envs = groups(subset, 'environment_id')
        tasks = groups(subset, 'task')
        rich = {e for e,g in envs.items() if len({r['family_id'] for r in g}) > 1}
        strata = defaultdict(set)
        for r in subset:
            strata[r['environment_id'], r['dataset'], r['operator_id']].add(r['family_id'])
        print(scope, 'records', len(subset), 'segments', len(seg), 'eligible', sum(r['eligible']=='1' for r in seg),
              'environments', len(envs), 'multi-family environments', len(rich), 'tasks', len(tasks),
              'tasks in >1 environment', sum(len({r['environment_id'] for r in g})>1 for g in tasks.values()),
              'environment/source/operator strata', len(strata), 'multi-family strata', sum(len(f)>1 for f in strata.values()))
        for family, recs in sorted(families.items()):
            cells = [('all', 'all', recs)]
            for env in sorted(rich):
                source_groups = groups([r for r in recs if r['environment_id'] == env], 'dataset')
                cells.extend((env, ds, g) for ds, g in sorted(source_groups.items()))
            for env, dataset, group in cells:
                ids = {r['episode_uuid'] for r in group}
                units = [r for r in seg if r['episode_uuid'] in ids and r['eligible']=='1']
                result.append(dict(scope=scope, environment=env, dataset=dataset, family=family, records=len(ids),
                                   environments=len({r['environment_id'] for r in group}),
                                   operators=len({r['operator_id'] for r in group}), tasks=len({r['task'] for r in group}),
                                   eligible_segments=len(units), windows=sum(r['windows'] for r in group),
                                   median_annotation_s=round(median(float(r['duration_s']) for r in units),6) if units else ''))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT/'data/raw/C')
    parser.add_argument('--accounts', type=Path, default=ROOT/'research/q2_diagnostic/train_accounts.csv')
    parser.add_argument('--out', type=Path, default=Path('runs/c_design_support/summary.csv'))
    args = parser.parse_args()
    result = analyze(args.root/'Restocking_Tasks_and_Constraints', args.accounts)
    budget_bounds(args.root/'Restocking_Tasks_and_Constraints', result)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=result[0], lineterminator='\n')
        writer.writeheader()
        writer.writerows(result)
