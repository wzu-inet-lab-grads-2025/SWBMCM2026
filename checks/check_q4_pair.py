"""Audit two formal policies against each other and the archived Q4 panel, for the frozen numerical plan."""

import argparse
import csv
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.b.q4_audit.audit import Context, audit_pair
from experiments.b.q4.compare import compare, index


def load_records(path):
    with path.open(encoding='utf-8') as stream:
        return index((json.loads(line) for line in stream if line.strip()), 'scenario_id')


def check(keep_path, online_path, panel):
    inputs = ROOT / 'experiments/b'
    name = 'manifest' if panel == 'main' else 'sensitivity'
    scenarios = index(json.loads((inputs / f'q4_scenarios/{name}.json').read_text())['scenarios'], 'id')
    keep, online = load_records(keep_path), load_records(online_path)
    rows, _ = compare(scenarios, keep, online)
    with (inputs / f'snapshots/q4_comparison/{panel}.csv').open() as stream:
        reference = index(csv.DictReader(stream), 'scenario_id')
    if reference.keys() != scenarios.keys():
        raise ValueError('Archived scenario IDs differ from the panel')
    context = Context.default()
    numeric = {'N', 'retained_k', 'revised_k', 'retained_t_last_s', 'revised_t_last_s', 'retained_eta',
               'revised_eta', 'additional_count', 'max_common_delay_s'}
    for row in rows:
        sid = row['scenario_id']
        scenario = scenarios[sid]
        for key in ('plan_sha256', 'scene_sha256', 'manifest_sha256'):
            if keep[sid]['provenance'][key] != online[sid]['provenance'][key]:
                raise ValueError(f'{sid}: different {key} for the two policies')
        if (scenario['recovery_s'] or 0) > 30 and panel == 'sensitivity':
            scenario = dict(scenario, recovery_scope='sensitivity')
        audit = audit_pair(keep[sid], online[sid], scenario, context=context)
        if audit['status'] != 'pass' or audit['prefix_status'] != 'pass':
            raise ValueError(f"{sid}: {audit['status']}, {audit['pair_errors']}, {audit['left']['errors']}, {audit['right']['errors']}")
        for key, value in reference[sid].items():
            matches = math.isclose(row[key], float(value), rel_tol=0, abs_tol=1e-9) if key in numeric else row[key] == value
            if not matches:
                raise ValueError(f'{sid}: archived {key}={value}, replayed {row[key]}')
    return len(rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--panel', choices=['main', 'sensitivity'], default='main')
    parser.add_argument('--retain', type=Path)
    parser.add_argument('--reschedule', type=Path)
    args = parser.parse_args()
    name = 'manifest' if args.panel == 'main' else 'sensitivity'
    directory = ROOT / 'runs/b/formal'
    try:
        count = check(args.retain or directory / f'q4-retain-q22-numerical-{name}.jsonl',
                      args.reschedule or directory / f'q4-reschedule-q22-numerical-{name}.jsonl', args.panel)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    print(f'{count} {args.panel} scenario pairs: independent audit passed, archived comparison reproduced.')
