"""Finite SYNTHETIC Q3 reservation checks; not a fitted or competition policy.

Reads only public task/zone/condition files and an existing training aggregate.
Imports the engine, never the real-data adapter or the FCFS/EDD pilot. No raw
records, source indices, validation/test assignments, random seeds, or writes.
All source durations and numerical prefixes below are fabricated. Hand results
are closed-form constants, independent of engine.score/execution_quote.

Run from the repository root:
    python3 -B experiments/c_q3_reservation_effect.py
"""
import csv
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.q3_replay.engine import Source, Trial, fixed_actions, run_trial

PUBLIC = ROOT / 'data/raw/C/Restocking_Tasks_and_Constraints'
SQRT2 = math.sqrt(2)
IDS = tuple(f'S1_J{i:02d}' for i in range(1, 9))


def csv_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def inputs():
    rules = json.loads((PUBLIC / 'common_conditions.json').read_text())
    public_jobs = csv_rows(PUBLIC / 'tasks.csv')
    jobs = {
        row['job_id']: dict(job_id=row['job_id'], family_id=row['family_id'],
                            release_s=float(row['release_s']), due_s=float(row['due_s']),
                            priority_weight=float(row['priority_weight']), zone_id=row['zone_id'],
                            predecessors=row['predecessors'].split('|') if row['predecessors'] else [])
        for row in public_jobs if row['case_id'] == 'S1'
    }
    zones = {row['zone_id']: [float(row['x']), float(row['y'])]
             for row in csv_rows(PUBLIC / 'service_zones.csv')}
    support = {row['family']: row for row in csv_rows(ROOT / 'research/q3_reservation_support.csv')}
    # Description-only comparison: these quantiles are NOT newly chosen official policies.
    reserves = {
        name: {family: max(0.2, float(row[column]) + 0.5) for family, row in support.items()}
        for name, column in (('median', 'median_s'), ('q75', 'q75_s'))
    }
    case = next(c for c in rules['cases'] if c['case_id'] == 'S1')
    assert len(public_jobs) == 36 and set(jobs) == set(IDS)
    assert case == dict(case_id='S1', jobs=8, horizon_s=100, cost_budget=32, observation_limit=4)
    assert [(jobs[j]['family_id'], jobs[j]['release_s'], jobs[j]['due_s'],
             jobs[j]['priority_weight'], jobs[j]['zone_id'], jobs[j]['predecessors']) for j in IDS] == [
        ('P', 0, 35, 3, 'Z4', []), ('A', 0, 45, 1, 'Z3', []),
        ('L', 0, 55, 2, 'Z2', []), ('P', 0, 65, 2, 'Z1', []),
        ('A', 20, 55, 3, 'Z4', []), ('L', 20, 65, 1, 'Z3', []),
        ('P', 40, 95, 2, 'Z2', []), ('A', 40, 100, 2, 'Z1', []),
    ]
    assert zones == {'Z0': [0, 0], 'Z1': [1, 0], 'Z2': [0, 1], 'Z3': [1, 1], 'Z4': [2, 0]}
    assert rules['worker_count'] == 1 and rules['initial_zone'] == 'Z0'
    assert rules['movement']['speed'] == 1 and rules['movement']['changeover_s'] == 0.5
    assert rules['fees'] == dict(service_fixed=2, reserve_per_second=0.1, distance_per_unit=0.2, wait=0)
    assert rules['service']['alpha'] == 1 and rules['service']['beta_s'] == 0.5
    assert rules['service']['reserve_min_s'] == 0.2 and not rules['service']['reservation_released_early']
    assert rules['max_actions'] == 200 and rules['terminal_on_invalid']
    assert {f: (int(r['segments']), int(r['records']), int(r['environments'])) for f, r in support.items()} == {
        'P': (124, 23, 9), 'A': (112, 29, 9), 'L': (10, 3, 2)}
    for name, expected in [('median', {'P': 4.5, 'A': 3.7, 'L': 4.1}),
                           ('q75', {'P': 8.5, 'A': 5.35, 'L': 10.85})]:
        for family, b in expected.items():
            assert math.isclose(reserves[name][family], b, abs_tol=1e-12)
    return rules, jobs, zones, case, reserves


def synthetic_trial(config, durations):
    rules, jobs, zones, case, _ = config
    assert set(durations) <= set(jobs)
    # d = p - 0.5; d >= 1.2 respects the length threshold, not an empirical sample.
    sources = {}
    for job in jobs:
        p = durations.get(job, 2.0)
        assert p - 0.5 >= 1.2
        sources[job] = Source('FABRICATED/' + job, p - 0.5,
                              [dict(timestamp_s=0.0), dict(timestamp_s=0.2)])
    return Trial(jobs, sources, zones, case, rules)


def execute(job, b):
    return dict(action='EXECUTE', job_id=job, b_s=b)


def prescribed(order, reservations, start=0):
    actions = [dict(action='WAIT', seconds=start)] if start else []
    actions += [execute(j, reservations[j]) for j in order]
    return fixed_actions(actions + [dict(action='STOP')])


def ranked(reservations, skip_infeasible):
    """Only the two finite diagnostic branches, with no hidden-duration access.

    Both retain the same total job-ID rank. Strict mode stops at a blocked head;
    filter mode selects the first currently feasible item. On this construction
    there are no waits: all six prefix jobs are released by their decision time.
    """
    def decide(view):
        for job_id in IDS:
            if job_id in view['completed']:
                continue
            job = view['jobs'][job_id]
            b = reservations[job['family_id']]
            distance = math.dist(view['zones'][view['zone']], view['zones'][job['zone_id']])
            start = view['time'] + distance + 0.5
            fee = 2 + 0.1*b + 0.2*distance
            feasible = (job['release_s'] <= view['time']
                        and all(p in view['completed'] for p in job['predecessors'])
                        and start + b <= view['case']['horizon_s']
                        and fee <= view['budget_left'])
            if feasible:
                return execute(job_id, b)
            if not skip_infeasible:
                return dict(action='STOP')
        return dict(action='STOP')
    return decide


def metrics(completion, on_time, tardiness, cost, elapsed, overrun, idle, censored=0):
    return dict(weighted_completion_rate=completion, weighted_on_time_rate=on_time,
                weighted_tardiness_s=tardiness, cost=cost, elapsed_s=elapsed,
                observed_overrun_s=overrun, reserved_idle_s=idle, observations=0,
                censored_units=censored, invalid_actions=0, valid_protocol=True)


def expected_single(b):
    # Only J01 served: other jobs contribute sum w*(100-due) = 395.
    return metrics(3/16, 3/16, 395, 2.4+0.1*b, 14.5, 12-b, 0)


def expected_pair(arm):
    # Fixed WAIT(29), then J01(p=2), J02(p=4). Other jobs contribute 340.
    return {
        'minimum': metrics(4/16, 4/16, 340, 4.44+0.2*SQRT2, 38+SQRT2, 5.6, 0),
        'median': metrics(4/16, 4/16, 340, 5.22+0.2*SQRT2, 40.5+SQRT2, 0.3, 2.5),
        'q75': metrics(4/16, 3/16, 339.5+SQRT2, 5.785+0.2*SQRT2, 45.85+SQRT2, 0, 7.85),
        'known_p': metrics(4/16, 4/16, 340, 5+0.2*SQRT2, 38+SQRT2, 0, 0),
    }[arm]


def expected_ranked(arm):
    # First six: sum p=81.5; sum distances=4+3*sqrt(2), sum b=24.6 or 49.4.
    # First four are on time (weight 8); first six complete (weight 12).
    # Tardiness: 3*(6.5+2*sqrt(2)) + (23.5+3*sqrt(2)) + 2*(100-95).
    tardiness = 53+9*SQRT2
    return {
        'median_filter': metrics(12/16, 8/16, tardiness, 17.91+0.6*SQRT2,
                                 100, 62.4-3*SQRT2, 0, censored=1),
        'q75_filter': metrics(14/16, 10/16, tardiness, 20.475+0.6*SQRT2,
                              95.35+3*SQRT2, 32.1, 3.35),
        'q75_strict': metrics(12/16, 8/16, tardiness, 17.74+0.6*SQRT2,
                              88.5+3*SQRT2, 32.1, 0),
        'median_frozen_long_trace': metrics(14/16, 10/16, tardiness, 17.83+0.6*SQRT2,
                                           93.7+3*SQRT2, 56.9, 1.7),
    }[arm]


def check_result(result, expected, order, starts, completions):
    assert result['metrics'].keys() == expected.keys()
    for name, value in expected.items():
        assert math.isclose(result['metrics'][name], value, rel_tol=1e-12, abs_tol=1e-10), (
            name, result['metrics'][name], value)
    executed = [e for e in result['ledger'] if e['action'] == 'EXECUTE']
    assert [e['job_id'] for e in executed] == list(order)
    assert len(executed) == len(starts) == len(completions)
    for event, start, completion in zip(executed, starts, completions):
        assert math.isclose(event['start'], start, abs_tol=1e-10)
        if completion is None:
            assert event['completion'] is None and event['censored']
        else:
            assert math.isclose(event['completion'], completion, abs_tol=1e-10)
        # Occupancy identity also holds for H-censored execution.
        assert math.isclose(event['end']-event['start'], event['b']+event['overrun'], abs_tol=1e-10)
        assert event['cost_after'] <= 32 and event['start']+event['b'] <= 100
    # All diagnostic policies see identical, uninformative free prefixes only.
    for call in result['calls']:
        assert 'sources' not in call['input']
        for obs in call['input']['observations'].values():
            assert obs == dict(prefix_s=0.2, rows=[dict(timestamp_s=0.0), dict(timestamp_s=0.2)])
    return len(expected)


def main():
    config = inputs()
    rules, jobs, zones, case, reserves = config
    outputs, checks = [], 0

    def record(probe, arm, result, expected, order, starts, completions):
        nonlocal checks
        checks += check_result(result, expected, order, starts, completions)
        outputs.append(dict(probe=probe, arm=arm, metrics=result['metrics'],
                            executions=[{key: e[key] for key in
                                         ('job_id', 'start', 'b', 'completion', 'end', 'fee', 'overrun', 'idle')}
                                        for e in result['ledger'] if e['action'] == 'EXECUTE']))

    a, b = IDS[:2]
    single = synthetic_trial(config, {a: 12})
    for arm, reservation in [('minimum', 0.2), ('median', reserves['median']['P']),
                              ('q75', reserves['q75']['P']), ('known_p', 12)]:
        result = run_trial(single, prescribed([a], {a: reservation}))
        record('one_job_underreserve', arm, result, expected_single(reservation), [a], [2.5], [14.5])

    pair = synthetic_trial(config, {a: 2, b: 4})
    pair_reserves = {'minimum': {a: 0.2, b: 0.2}, 'known_p': {a: 2, b: 4}}
    pair_reserves.update({arm: {j: values[jobs[j]['family_id']] for j in (a, b)}
                         for arm, values in reserves.items()})
    second_start = {'minimum': 34+SQRT2, 'known_p': 34+SQRT2,
                    'median': 36.5+SQRT2, 'q75': 40.5+SQRT2}
    for arm in ('minimum', 'median', 'q75', 'known_p'):
        result = run_trial(pair, prescribed([a, b], pair_reserves[arm], start=29))
        record('fixed_two_job_sequence', arm, result, expected_pair(arm), [a, b],
               [31.5, second_start[arm]], [33.5, second_start[arm]+4])

    full = synthetic_trial(config, {**{j: 11 for j in IDS[:5]}, IDS[5]: 26.5, IDS[6]: 10, IDS[7]: 2})
    prefix_starts = [2.5, 14+SQRT2, 26.5+SQRT2, 38+2*SQRT2, 50.5+2*SQRT2, 62+3*SQRT2]
    prefix_completions = [s+p for s, p in zip(prefix_starts, [11]*5+[26.5])]
    ranked_logs = {}
    for arm, rule, skip in [('median_filter', 'median', True), ('q75_filter', 'q75', True),
                             ('q75_strict', 'q75', False)]:
        result = run_trial(full, ranked(reserves[rule], skip))
        ranked_logs[arm] = result
        tail = [IDS[6]] if arm == 'median_filter' else [IDS[7]] if arm == 'q75_filter' else []
        starts = prefix_starts + ([90+3*SQRT2] if tail else [])
        completions = prefix_completions + ([None] if arm == 'median_filter' else
                                            [92+3*SQRT2] if arm == 'q75_filter' else [])
        record('full_S1_fixed_rank', arm, result, expected_ranked(arm), IDS[:6]+tuple(tail), starts, completions)
        assert all(e['action'] in ('EXECUTE', 'STOP') for e in result['ledger'])
    # Same source realization and state at the filtering boundary; only budgets differ.
    for key in ('time', 'zone', 'completed', 'observations'):
        assert ranked_logs['median_filter']['calls'][6]['input'][key] == ranked_logs['q75_filter']['calls'][6]['input'][key]
    assert ranked_logs['median_filter']['calls'][6]['input']['zone'] == 'Z3'
    assert 94.5+3*SQRT2 < 100 < 98.5+3*SQRT2  # J07: short fits, long does not.
    assert 95.35+3*SQRT2 < 100                 # J08: even long fits.
    # Offline mechanism attribution, NOT an online prescription: freeze the long
    # run's selected trace and replay it with short reservations on the same world.
    frozen_trace = IDS[:6] + (IDS[7],)
    frozen_reserves = {j: reserves['median'][jobs[j]['family_id']] for j in frozen_trace}
    result = run_trial(full, prescribed(frozen_trace, frozen_reserves))
    record('full_S1_frozen_selection', 'median_frozen_long_trace', result,
           expected_ranked('median_frozen_long_trace'), frozen_trace,
           prefix_starts+[90+3*SQRT2], prefix_completions+[92+3*SQRT2])
    # A service-only upper bound for the restricted two-job action class is attained.
    pair_results = {r['arm']: r['metrics'] for r in outputs if r['probe'] == 'fixed_two_job_sequence'}
    assert pair_results['minimum']['weighted_on_time_rate'] == 4/16
    assert pair_results['known_p']['weighted_on_time_rate'] == 4/16
    assert pair_results['minimum']['cost'] < pair_results['known_p']['cost']

    def rounded(value):
        if isinstance(value, dict):
            return {k: rounded(v) for k, v in value.items()}
        if isinstance(value, list):
            return [rounded(v) for v in value]
        return round(value, 9) if isinstance(value, float) else value

    print(json.dumps(rounded(dict(
        scope='SYNTHETIC ONLY: official S1 tasks/limits, fabricated durations; no empirical replay',
        synthetic_runs=len(outputs), engine_hand_metric_checks=checks,
        timing_occupancy_feasibility_and_view_assertions_passed=True, results=outputs)),
        ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
