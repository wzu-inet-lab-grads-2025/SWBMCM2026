"""2026-11-20: tiny SYNTHETIC Q3 information/cost/slack arithmetic checks.

AI-assisted diagnostic, not a competition policy or empirical validation.
Only reads public common conditions, task rows and zones. Imports the existing
engine, not the real-data adapter. No source-index/raw-record/result access,
random seeds, fitting, output files or validation/test runs.

Run from the repository root:
    python3 -B experiments/c_q3_review_slack_check.py
"""
import csv
import json
import math
from copy import deepcopy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.q3_replay.engine import Source, Trial, fixed_actions, run_trial

PUBLIC = ROOT / 'data/raw/C/Restocking_Tasks_and_Constraints'
A, B = 'S1_J01', 'S1_J02'
SQRT2 = math.sqrt(2)


def public_inputs():
    rules = json.loads((PUBLIC / 'common_conditions.json').read_text())
    with (PUBLIC / 'tasks.csv').open(encoding='utf-8-sig', newline='') as handle:
        jobs = {
            row['job_id']: dict(job_id=row['job_id'], family_id=row['family_id'],
                               zone_id=row['zone_id'], release_s=float(row['release_s']),
                               due_s=float(row['due_s']), priority_weight=float(row['priority_weight']),
                               predecessors=row['predecessors'].split('|') if row['predecessors'] else [])
            for row in csv.DictReader(handle) if row['case_id'] == 'S1'
        }
    with (PUBLIC / 'service_zones.csv').open(encoding='utf-8-sig', newline='') as handle:
        zones = {row['zone_id']: [float(row['x']), float(row['y'])] for row in csv.DictReader(handle)}
    case = next(deepcopy(c) for c in rules['cases'] if c['case_id'] == 'S1')
    # Hand arithmetic below is intentionally pinned to these public constants.
    assert case == dict(case_id='S1', jobs=8, horizon_s=100, cost_budget=32, observation_limit=4)
    assert sum(j['priority_weight'] for j in jobs.values()) == 16
    assert (jobs[A]['due_s'], jobs[A]['priority_weight'], jobs[A]['zone_id']) == (35, 3, 'Z4')
    assert (jobs[B]['due_s'], jobs[B]['priority_weight'], jobs[B]['zone_id']) == (45, 1, 'Z3')
    assert all(jobs[j]['release_s'] == 0 and not jobs[j]['predecessors'] for j in (A, B))
    assert zones == {'Z0': [0, 0], 'Z1': [1, 0], 'Z2': [0, 1], 'Z3': [1, 1], 'Z4': [2, 0]}
    assert rules['worker_count'] == 1 and rules['initial_zone'] == 'Z0'
    assert rules['movement']['speed'] == 1 and rules['movement']['changeover_s'] == 0.5
    assert rules['fees'] == dict(service_fixed=2, reserve_per_second=0.1, distance_per_unit=0.2, wait=0)
    assert rules['service']['alpha'] == 1 and rules['service']['beta_s'] == 0.5
    assert rules['service']['reserve_min_s'] == 0.2 and not rules['service']['reservation_released_early']
    assert rules['observation']['prefix_seconds'] == [0.2, 0.6, 1.0]
    assert rules['observation']['delay_s'] == rules['observation']['cost'] == 1
    assert rules['observation']['max_per_job'] == 2
    assert rules['max_actions'] == 200 and rules['terminal_on_invalid']
    assert sum(j['priority_weight'] * (100-j['due_s']) for k, j in jobs.items() if k != A) == 395
    assert sum(j['priority_weight'] * (100-j['due_s']) for k, j in jobs.items() if k not in (A, B)) == 340
    return rules, jobs, zones, case


def fabricated_rows(long):
    # Entirely fabricated, including the perfect relationship to duration.
    # First 0.2 source seconds are identical. A permitted numerical channel at
    # source t=0.4 discloses a bit only after a paid review has returned.
    return [dict(timestamp_s=t, head_pose=dict(position=[int(long and t > 0.2), 0, 0],
                                             orientation_xyzw=[0, 0, 0, 1]))
            for t in (0.0, 0.2, 0.4, 0.6, 1.0)]


def execute(job, reservation):
    return dict(action='EXECUTE', job_id=job, b_s=reservation)


def prefix(start, arm):
    commands = [dict(action='WAIT', seconds=start)] if start else []
    if arm.startswith('W_'):
        commands.append(dict(action='WAIT', seconds=1))
    elif arm.startswith(('M_', 'R_')):
        commands.append(dict(action='REVIEW', job_id=A))
    return commands


def synthetic_branch(start):
    """One prescribed receipt branch, using no truth or source reference lookup."""
    queue = prefix(start, 'R_choose')
    processed = False

    def decide(view):
        nonlocal processed
        if queue:
            return queue.pop(0)
        assert not processed
        processed = True
        assert view['time'] == start + 1 and view['observations'][A]['prefix_s'] == 0.6
        bit = next(row['head_pose']['position'][0] for row in view['observations'][A]['rows']
                   if row['timestamp_s'] == 0.4)
        assert bit in (0, 1)
        # Loose clock: AB is already sufficient. Tight clock: short -> AB,
        # long -> BA. This is a hand-constructed diagnostic, not a fitted rule.
        order = (B, A) if start == 29 and bit == 1 else (A, B)
        queue.extend([execute(j, 0.2) for j in order] + [dict(action='STOP')])
        return queue.pop(0)
    return decide


def fixed_probe(start, arm, reservation):
    commands = prefix(start, arm)
    suffix = arm.split('_', 1)[1]
    order = {'A': (A,), 'AB': (A, B), 'BA': (B, A), 'stop': ()}[suffix]
    return fixed_actions(commands + [execute(j, reservation) for j in order] + [dict(action='STOP')])


def hand_metrics(completion, on_time, tardiness, cost, observations, elapsed, overrun, idle):
    return dict(weighted_completion_rate=completion, weighted_on_time_rate=on_time,
                weighted_tardiness_s=tardiness, cost=cost, observations=observations,
                elapsed_s=elapsed, observed_overrun_s=overrun, reserved_idle_s=idle,
                censored_units=0, invalid_actions=0, valid_protocol=True)


def hand_single(start, arm, long):
    delay = 0 if arm == 'D_A' else 1
    review = int(arm == 'R_A')
    # Fixed b=20. End of occupancy is independent of which duration occurred.
    completion = start + delay + 2.5 + (20 if long else 2)
    return hand_metrics(3/16, (3/16 if completion <= 35 else 0),
                        395 + 3 * max(completion-35, 0), 4.4+review, review,
                        start+delay+22.5, 0, 0 if long else 18)


def hand_pair(start, arm, long):
    delay = int(arm.startswith(('W_', 'M_', 'R_')))
    review = int(arm.startswith(('M_', 'R_')))
    ba = arm == 'D_BA' or (arm == 'R_choose' and start == 29 and long)
    # Closed-form timeline, independently of the engine's execution_quote/score.
    if ba:
        cb = start + delay + SQRT2 + 2.5
        ca = start + delay + 2*SQRT2 + 3 + (20 if long else 2)
        charge = 4.04 + 0.4*SQRT2 + review
    else:
        ca = start + delay + 2.5 + (20 if long else 2)
        cb = start + delay + 5 + SQRT2 + (20 if long else 2)
        charge = 4.44 + 0.2*SQRT2 + review
    return hand_metrics(4/16, (3*int(ca <= 35)+int(cb <= 45))/16,
                        340+3*max(ca-35, 0)+max(cb-45, 0), charge, review,
                        max(ca, cb), 21.6 if long else 3.6, 0)


def hand_budget(arm, long):
    if arm == 'R_stop':
        return hand_metrics(0, 0, 590, 1, 1, 30, 0, 0)
    delay = int(arm == 'W_A')
    completion = 31.5+delay+(20 if long else 2)
    return hand_metrics(3/16, 0 if long else 3/16, 395+3*max(completion-35, 0),
                        2.42, 0, completion, 19.8 if long else 1.8, 0)


def check_views(result, start, arm):
    for call in result['calls']:
        obs = call['input']['observations'][A]
        returned = arm.startswith(('M_', 'R_')) and call['time'] >= start+1
        assert obs['prefix_s'] == (0.6 if returned else 0.2)
        assert all(row['timestamp_s'] <= obs['prefix_s'] for row in obs['rows'])
    if arm.startswith(('M_', 'R_')):
        review = next(row for row in result['ledger'] if row['action'] == 'REVIEW')
        assert review['time'] == start and review['end'] == start+1
        assert review['fee'] == 1 and review['zone_before'] == review['zone_after'] == 'Z0'
        assert not any(start < call['time'] < start+1 for call in result['calls'])


def main():
    rules, jobs, zones, official_case = public_inputs()
    probes = []
    for start, label in ((0, 'A_loose'), (12, 'A_tight')):
        probes.extend((label, start, arm, 20, 32) for arm in ('D_A', 'W_A', 'R_A'))
    for start, label in ((0, 'B_loose'), (29, 'B_tight')):
        probes.extend((label, start, arm, 0.2, 32)
                      for arm in ('D_AB', 'D_BA', 'W_AB', 'M_AB', 'R_choose'))
    # Explicit counterfactual resource boundary; NOT a claimed reachable S1
    # remainder or official case budget. No action history fabricates that claim.
    probes.extend(('budget_counterfactual', 29, arm, 0.2, 2.42)
                  for arm in ('D_A', 'W_A', 'R_stop'))
    summary = []
    checks = 0
    for label, start, arm, reservation, budget in probes:
        worlds = []
        for long in (False, True):
            case = deepcopy(official_case)
            if budget != 32:
                case.update(case_id='SYNTHETIC_RESOURCE_BOUNDARY_NOT_S1', cost_budget=budget)
            sources = {j: Source('fabricated_unused', 1.5, fabricated_rows(False)) for j in jobs}
            sources[A] = Source('fabricated_long' if long else 'fabricated_short',
                                19.5 if long else 1.5, fabricated_rows(long))
            policy = synthetic_branch(start) if arm == 'R_choose' else fixed_probe(start, arm, reservation)
            result = run_trial(Trial(jobs, sources, zones, case, rules), policy)
            if label.startswith('A_'):
                expected = hand_single(start, arm, long)
            elif label.startswith('B_'):
                expected = hand_pair(start, arm, long)
            else:
                expected = hand_budget(arm, long)
            assert result['metrics'].keys() == expected.keys()
            for key, value in expected.items():
                actual = result['metrics'][key]
                assert math.isclose(actual, value, rel_tol=1e-12, abs_tol=1e-10), (label, arm, long, key, actual, value)
                checks += 1
            check_views(result, start, arm)
            if label == 'budget_counterfactual' and arm == 'R_stop':
                assert result['calls'][-1]['input']['budget_left'] < 2
            worlds.append(result['metrics'])
        means = {key: sum(world[key] for world in worlds)/2 for key in expected if key != 'valid_protocol'}
        means['valid_protocol'] = all(world['valid_protocol'] for world in worlds)
        summary.append(dict(probe=label, start_s=start, arm=arm, budget=budget,
                            mean_metrics={k: round(v, 9) if isinstance(v, float) else v for k, v in means.items()}))
    print(json.dumps(dict(scope='SYNTHETIC ONLY; two equiprobable fabricated worlds, not empirical trials',
                          engine_hand_metric_checks=checks, synthetic_runs=2*len(probes),
                          view_and_delay_assertions_passed=True, results=summary), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
