"""Independently audit the formal retained-policy JSONL for the accepted numerical plan."""

import argparse
import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.b.q4_audit.audit import Context, audit_record


def check(path, panel):
    inputs = ROOT / 'experiments/b'
    name = 'manifest' if panel == 'main' else 'sensitivity'
    cases = json.loads((inputs / f'q4_scenarios/{name}.json').read_text())['scenarios']
    scenarios = {s['id']: s for s in cases}
    with (inputs / f'snapshots/q4_comparison/{panel}.csv').open() as stream:
        expected = {r['scenario_id']: r for r in csv.DictReader(stream)}
    if scenarios.keys() != expected.keys() or len(scenarios) != len(cases):
        raise ValueError('Reference scenario IDs disagree')
    context = Context.default()
    seen = set()
    with path.open() as stream:
        for line in stream:
            row = json.loads(line)
            i = row['scenario_id']
            if i not in scenarios or i in seen or row['scenario'] != scenarios[i]:
                raise ValueError(f'Unexpected, duplicated or mismatched scenario: {i}')
            seen.add(i)
            if (row['policy'], row['dispatch_mode']) != ('retain_original', 'preloaded'):
                raise ValueError(f'{i}: wrong policy or command protocol')
            scenario = scenarios[i]
            if (scenario['recovery_s'] or 0) > 30 and panel == 'sensitivity':
                scenario = dict(scenario, recovery_scope='sensitivity')
            audit = audit_record(row, scenario, context=context)
            if not audit['certified']:
                raise ValueError(f"{i}: {audit['errors']} {audit['missing_evidence']}")
            ref, result = expected[i], audit['recomputed']
            if ((result['N'], result['k']) != (int(ref['N']), int(ref['retained_k']))
                    or abs(result['eta'] - float(ref['retained_eta'])) > 1e-10
                    or abs(result['t_last_s'] - float(ref['retained_t_last_s'])) > 1e-9):
                raise ValueError(f'{i}: independently reconstructed score differs from archived score')
    if seen != scenarios.keys():
        raise ValueError('Missing scenarios')
    return len(seen)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('jsonl', type=Path, nargs='?', default=ROOT / 'runs/b/formal/q4-retain-q22-numerical-manifest.jsonl')
    parser.add_argument('--panel', choices=['main', 'sensitivity'], default='main')
    args = parser.parse_args()
    try:
        count = check(args.jsonl, args.panel)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    print(f'{count} {args.panel} scenarios: independent event audit passed; all archived scores matched.')
