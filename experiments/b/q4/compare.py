"""Pair retained and revised schedules by scenario ID after their event audit."""

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path


def index(records, field):
    result = {}
    for record in records:
        key = record[field]
        if key in result:
            raise ValueError(f'duplicate {field}: {key}')
        result[key] = record
    return result


def factor_group(scenario):
    return '+'.join(factor for factor, field in [('A', 'new_alert'), ('F', 'failed_vehicle'), ('C', 'recovery_s')]
                    if scenario[field] is not None) or 'none'


def score(record, expected_total):
    allowed = {f'T{i}' for i in range(1, expected_total + 1)}
    observations = defaultdict(list)
    for event in record['events']:
        if event['kind'] == 'observe':
            observations[event['target']].append(event)

    starts = {}
    for target, pair in observations.items():
        if (target not in allowed or len(pair) != 2 or pair[0]['vehicle'] == pair[1]['vehicle']
                or not math.isclose(pair[0]['t0'], pair[1]['t0'], rel_tol=0, abs_tol=1e-6)):
            raise ValueError(f'{record["scenario_id"]}: inconsistent joint observation of {target}')
        joint_start = max(e['t0'] for e in pair)
        if min(e['t0'] for e in pair) < 0:
            raise ValueError(f'{record["scenario_id"]}: negative observation start')
        if joint_start <= 60:
            starts[target] = joint_start
    count = len(starts)
    latest = max(starts.values()) if count == expected_total else 60.
    if (record['N'] != expected_total or type(record['k']) is not int or record['k'] != count
            or not math.isclose(record['eta'], count / expected_total, rel_tol=0, abs_tol=1e-10)
            or not math.isclose(record['t_last_s'], latest, rel_tol=0, abs_tol=1e-7)):
        raise ValueError(f'{record["scenario_id"]}: claimed score disagrees with observation events')
    return starts, latest


def compare(manifest, retained, revised):
    if manifest.keys() != retained.keys() or manifest.keys() != revised.keys():
        raise ValueError('The scenario IDs must match across the manifest and both sets of timelines')
    rows = []
    groups = defaultdict(Counter)
    for sid, s in manifest.items():
        for record, policy in ((retained[sid], 'retain_original'), (revised[sid], 'reschedule')):
            if record['policy'] != policy or record['dispatch_mode'] != 'preloaded':
                raise ValueError(f'{sid}: expected {policy} under the preloaded protocol')
            if record['scenario'] != s:
                raise ValueError(f'{sid}: recorded disturbance differs from the input manifest')
        total = 3 + int(s['new_alert'] is not None)
        old, t0 = score(retained[sid], total)
        new, t1 = score(revised[sid], total)
        k0, k1 = len(old), len(new)
        if k0 != k1:
            outcome = 'more_starts' if k1 > k0 else 'fewer_starts'
        elif math.isclose(t0, t1, rel_tol=0, abs_tol=1e-7):
            outcome = 'tie'
        else:
            outcome = 'earlier' if t1 < t0 else 'later'
        group = factor_group(s)
        groups[group][outcome] += 1
        differences = {t: new[t] - old[t] for t in old.keys() & new.keys()}
        delayed = sorted(t for t, dt in differences.items() if dt > 1e-7)
        rows.append(dict(scenario_id=sid, group=group, N=total, retained_k=k0, revised_k=k1,
                         retained_t_last_s=t0, revised_t_last_s=t1, outcome=outcome,
                         retained_eta=k0/total, revised_eta=k1/total, additional_count=k1-k0,
                         added_targets=','.join(sorted(new.keys() - old.keys())),
                         lost_targets=','.join(sorted(old.keys() - new.keys())),
                         delayed_common_targets=','.join(delayed),
                         max_common_delay_s=max((differences[t] for t in delayed), default=0.)))
    return rows, dict(groups)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest', 'retained', 'revised', 'output_csv'):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    manifest = index(json.loads(args.manifest.read_text(encoding='utf-8'))['scenarios'], 'id')
    def load(path):
        with path.open(encoding='utf-8') as stream:
            return index((json.loads(line) for line in stream if line.strip()), 'scenario_id')
    rows, groups = compare(manifest, load(args.retained), load(args.revised))
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(groups, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
