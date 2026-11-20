"""Independently certify a formal replay against the archived numerical Q2(2) plan."""

import argparse
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from experiments.b.q4_audit.audit import Context, audit_record, nominal_events


def require(condition, message):
    if not condition:
        raise ValueError(message)


def close(a, b):
    return math.isclose(a, b, abs_tol=1e-9, rel_tol=0)


def check(record):
    context = Context.default()
    jobs = {job['target']: {'start_s': job['start_s'], 'vehicles': [leg['vehicle'] for leg in job['legs']]}
            for job in context.nominal_jobs}
    expected = {v: [] for v in context.scene['vehicles_xy_m']}
    for e in nominal_events(context):
        if e['kind'] != 'wait' or e['t0'] < e['t1']:
            expected[e['vehicle']].append(e)
    # Compare each phase boundary with the independent, frozen timeline.
    for vehicle, phases in expected.items():
        actual = [e for e in record['events'] if e['vehicle'] == vehicle and e['kind'] != 'idle']
        require(len(actual) == len(phases), f'{vehicle}: wrong phase count')
        for e, ref in zip(actual, phases):
            require(all(e[k] == ref[k] for k in ('kind', 'target', 'station'))
                    and close(e['t0'], ref['t0']) and close(e['t1'], ref['t1']), f'{vehicle}: phase differs from {ref}')
            if e['kind'] == 'drive':
                require(e['path'] == ref['path'], f'{vehicle}: drive path differs')
    # The archived auditor reconstructs routes, pointing and scores from telemetry.
    scenario = dict(id='nominal', new_alert=None, failed_vehicle=None, recovery_s=None)
    audit_input = dict(record, scenario_id='nominal', policy='preloaded', jobs=jobs,
                       N=len(jobs), k=len(jobs), eta=1., t_last_s=record['score']['latest_start_s'])
    report = audit_record(audit_input, scenario, context=context)
    require(report['certified'], f"Event audit failed: {report['errors']} {report['missing_evidence']}")
    starts = record['score']['start_s']
    require(starts.keys() == jobs.keys(), 'unexpected scored targets')
    for target in jobs:
        require(close(starts[target], report['tasks'][target]['event_start_s']), f'{target}: score mismatch')
    require(close(report['recomputed']['t_last_s'], record['score']['latest_start_s']), 'latest start differs')

    reference = json.loads((ROOT / 'experiments/b/q4_retention/state_5s.json').read_text())['before']
    snapshot = record['snapshot']
    require(snapshot['t_s'] == 5 and snapshot['vehicles'].keys() == reference.keys(), 'expected all five vehicles at 5 s')
    for vehicle, ref in reference.items():
        actual = snapshot['vehicles'][vehicle]
        require((actual['phase'], actual['target']) == (ref['phase'], ref['target']), f'{vehicle}: phase or target at 5 s')
        for field in ('position_xy_m', 'gimbal_deg'):
            require(len(actual[field]) == 2 and all(close(x, y) for x, y in zip(actual[field], ref[field])),
                    f'{vehicle}: {field} differs at 5 s')
    return report['recomputed']['t_last_s']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('replay', type=Path, nargs='?', default=ROOT / 'runs/b/formal/q22-numerical.json')
    args = parser.parse_args()
    try:
        latest = check(json.loads(args.replay.read_text(encoding='utf-8')))
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    print(f'Independent replay passed: three joint starts, five vehicles at 5 s; latest_start_s={latest:.9f}')
