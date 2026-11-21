"""Q3 reservation rehearsal: training, synthetic, and Q75-only validation.

Not a competition model or a rerun of the nine validation FCFS/EDD pairs.
Default: training + fabricated diagnostics. `validation-q75`: reconstruct the
official deterministic assignments and execute ONLY nine new Q75 trials; compare
against nine archived EDD metric rows, not an authenticated historical ledger.
Validation details and all source/payload hashes remain in ignored runs/ only.
Existing cached Q75 trials are re-audited, NEVER reexecuted automatically.

AI-assisted implementation/audit: OpenCode harness, Sonus / Qoder / Remote
magpie, provider ID magpie, model remote-magpie/qoder/smodel, 2026-11-21.
Developer organization and model release date not independently available.
Team must independently verify, interpret and rewrite any adopted material.
Uses the existing candidate Q75 and EDD controller, not a new model selection.

Run: python3 -B experiments/c_feas_reservation.py
Public CSVs contain aggregates only; labels, predictions and all trial details
stay under the git-ignored runs/feas_reservation directory. Standard library.
"""

import argparse
import ast
from collections import Counter
from copy import deepcopy
import csv
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import random
import re
from statistics import fmean, stdev
import subprocess
import sys
from types import FunctionType
from zipfile import ZipFile
from zlib import crc32

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.q3_replay.acceptance import verify_ledger
from experiments.q3_replay.engine import Source, Trial, run_trial
from experiments.q3_replay.order_baseline import TRAIN_MEDIAN_D, order_policy

CASES = ('S1', 'S2', 'S3')
SEEDS = (2026111700, 2026111701, 2026111702)
MAPPINGS = ((1., .5), (.75, .5), (1.5, .5), (1., 0.), (1., 1.))
RECIPES = ('short_flat', 'long_flat', 'heterogeneous_cycle')
PRIVATE = ROOT / 'runs/feas_reservation'
OUT = ROOT / 'research/feas_reservation'
EPS = 1e-9


def rows(path):
    with path.open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def write_csv(path, values):
    values = list(values)
    assert values
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(values[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(values)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True,
                               indent=2, allow_nan=False) + '\n', encoding='utf-8')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def same(a, b):
    assert math.isclose(a, b, rel_tol=0, abs_tol=EPS), (a, b)


def quantile(values, probability):
    """Type 7: h=(n-1)q, linear interpolation, ZERO-based order statistics.

    Decimal endpoints avoid accidental rounding at annotated decimal times.
    Equivalent definition to numpy.quantile(..., method='linear'); no NumPy.
    """
    ordered = sorted(Decimal(str(v)) for v in values)
    assert ordered and Decimal(0) <= Decimal(str(probability)) <= Decimal(1)
    h = (len(ordered) - 1) * Decimal(str(probability))
    lo = int(h)
    fraction = h - lo
    hi = min(lo + 1, len(ordered) - 1)
    return float(ordered[lo] + fraction * (ordered[hi] - ordered[lo]))


def public_inputs(public):
    rules = json.loads((public / 'common_conditions.json').read_text())
    assert rules['version'] == 'q3-20261117-v1'
    assert rules['validation_seeds'][:3] == list(SEEDS)
    assert rules['max_actions'] == 200 and rules['terminal_on_invalid']
    assert rules['movement'] == dict(speed=1., changeover_s=.5,
                                     coordinates='synthetic service-area coordinates, not object positions')
    assert rules['fees'] == dict(service_fixed=2., reserve_per_second=.1,
                                 distance_per_unit=.2, wait=0.)
    assert rules['service']['reserve_min_s'] == .2
    assert not rules['service']['reservation_released_early']
    zones = {r['zone_id']: [float(r['x']), float(r['y'])]
             for r in rows(public / 'service_zones.csv')}
    jobs = {}
    for r in rows(public / 'tasks.csv'):
        jobs.setdefault(r['case_id'], {})[r['job_id']] = dict(
            job_id=r['job_id'], family_id=r['family_id'], zone_id=r['zone_id'],
            release_s=float(r['release_s']), due_s=float(r['due_s']),
            priority_weight=float(r['priority_weight']),
            predecessors=r['predecessors'].split('|') if r['predecessors'] else [])
    assert [len(jobs[c]) for c in CASES] == [8, 12, 16]
    assert [(c['horizon_s'], c['cost_budget'], c['observation_limit'])
            for c in rules['cases']] == [(100, 32, 4), (100, 34, 6), (125, 40, 8)]
    return rules, zones, jobs


def legacy_gate():
    """Inspect old metric rows, never re-create their source draws or schedules."""
    old = ROOT / 'experiments/q3_replay/order_results/order_trials.csv'
    table = rows(old)
    expected = {(c, str(s), arm) for c in CASES for s in SEEDS for arm in ('FCFS', 'EDD')}
    assert len(table) == 18
    assert {(r['case'], r['seed'], r['order']) for r in table} == expected
    for r in table:
        assert (r['split'], float(r['alpha']), float(r['beta_s'])) == ('validation', 1., .5)
        assert r['valid_protocol'] == 'True' and float(r['observations']) == 0
    # Known result locations in this checkout and the adjacent original checkout.
    locations = [ROOT / 'runs/q3_order', old.parent,
                 ROOT.parent / 'SWBMCM2026/runs/q3_order']
    inventories = {str(p): sorted(x.name for x in p.iterdir()) if p.is_dir() else []
                   for p in locations}
    adjacent = locations[-1] / 'order_trials.csv'
    matched_copy = adjacent.exists() and adjacent.read_bytes() == old.read_bytes()
    return dict(mode='train_and_synthetic_only', legacy_metric_pairs=9,
                legacy_edd_ledgers_authenticated=0, legacy_assignments_authenticated=0,
                empirical_new_runs=0, empirical_paired_comparisons=0,
                reason='No archived nine-pair assignment/callback ledgers found; old pilot writes only metrics.',
                old_metrics_sha256=digest(old), adjacent_metrics_byte_equal=matched_copy,
                inspected_result_directories=inventories)


def training(root):
    """Filter split BEFORE using endpoints; authenticate all eligible train labels."""
    public = root / 'Restocking_Tasks_and_Constraints'
    membership = {r['episode_uuid']: r for r in rows(public / 'record_split.csv')
                  if r['split'] == 'train'}
    selected, annotations, paths, seen = [], {}, set(), set()
    indexed = 0
    for r in rows(public / 'source_segment_index.csv'):
        if r['split'] != 'train':
            continue
        indexed += 1
        if r['eligible'] != '1':
            continue
        assert r['unit_id'] not in seen
        seen.add(r['unit_id'])
        meta = membership[r['episode_uuid']]
        assert all(meta[k] == r[k] for k in ('environment_id', 'split', 'dataset'))
        folder = root / 'Raw_Data' / r['dataset'] / r['sample']
        demo = r['dataset'] == 'EgoDemo'
        path = folder / ('annotation.json' if demo else 'tables/annotation_semantic_segments.csv')
        if path not in annotations:
            annotations[path] = (json.loads(path.read_text())['subtask_annotations']
                                 if demo else rows(path))
        annotation = annotations[path][int(r['source_segment_index'])]
        start = Decimal(str(annotation['start_time' if demo else 'segment.start_time']))
        end = Decimal(str(annotation['end_time' if demo else 'segment.end_time']))
        assert start == Decimal(r['start_s']) and end == Decimal(r['end_s'])
        d = end - start
        assert d >= Decimal('1.2')
        same(float(d), float(r['duration_s']))
        selected.append(dict(unit_id=r['unit_id'], record=r['episode_uuid'],
                             environment=r['environment_id'], family=r['family_id'], d_s=float(d)))
        paths.add(path)
    assert len(selected) == 246
    support = {r['family']: r for r in rows(ROOT / 'research/q3_reservation_support.csv')}
    quantiles, support_rows, predictions = {}, [], []
    for family in ('P', 'A', 'L'):
        group = [r for r in selected if r['family'] == family]
        envs = sorted({r['environment'] for r in group})
        ds = [r['d_s'] for r in group]
        q50, q75 = quantile(ds, .5), quantile(ds, .75)
        quantiles[family] = q75
        same(q50, TRAIN_MEDIAN_D[family])
        for key, value in (('q25_s', quantile(ds, .25)), ('median_s', q50), ('q75_s', q75)):
            same(value, float(support[family][key]))
        assert (len(group), len(envs), len({r['record'] for r in group})) == tuple(
            int(support[family][k]) for k in ('segments', 'environments', 'records'))
        omitted = []
        for environment in envs:
            retained = [r['d_s'] for r in group if r['environment'] != environment]
            left_out = [r for r in group if r['environment'] == environment]
            assert retained and left_out
            loo50, loo75 = quantile(retained, .5), quantile(retained, .75)
            omitted.append(loo75)
            for r in left_out:
                for scope, a, b in (('resubstitution', q50, q75),
                                    ('leave_one_environment_out', loo50, loo75)):
                    for arm, estimate in (('M50', a), ('Q75', b)):
                        predictions.append(dict(**r, scope=scope, arm=arm, prediction_d_s=estimate))
        same(min(omitted), float(support[family]['omit_env_q75_min_s']))
        same(max(omitted), float(support[family]['omit_env_q75_max_s']))
        support_rows.append(dict(family=family, segments=len(group), records=len({r['record'] for r in group}),
                                 environments=len(envs), q50_d_s=q50, q75_d_s=q75,
                                 b50_nominal_s=q50 + .5, b75_nominal_s=q75 + .5,
                                 leave_env_q75_min_s=min(omitted), leave_env_q75_max_s=max(omitted),
                                 raw_annotation_endpoint_matches=len(group), quantile_algorithm='type7_linear'))
    calibration = []
    for family in ('P', 'A', 'L'):
        for scope in ('resubstitution', 'leave_one_environment_out'):
            for arm in ('M50', 'Q75'):
                group = [r for r in predictions if (r['family'], r['scope'], r['arm']) == (family, scope, arm)]
                envs = sorted({r['environment'] for r in group})
                coverage = [fmean(r['d_s'] <= r['prediction_d_s'] + EPS for r in group
                                  if r['environment'] == e) for e in envs]
                errors = [r['d_s'] - r['prediction_d_s'] for r in group]
                calibration.append(dict(family=family, scope=scope, arm=arm, segments=len(group),
                    environments=len(envs), covered_units=sum(e <= EPS for e in errors),
                    coverage_micro=fmean(e <= EPS for e in errors), coverage_env_macro=fmean(coverage),
                    coverage_env_min=min(coverage), coverage_env_max=max(coverage),
                    coverage_env_sd=stdev(coverage), mae_d_s=fmean(abs(e) for e in errors),
                    pinball_q75_source_s=fmean(.75 * e if e >= 0 else -.25 * e for e in errors),
                    mean_underreserve_source_s=fmean(max(e, 0) for e in errors),
                    mean_overreserve_source_s=fmean(max(-e, 0) for e in errors)))
    audit = dict(training_indexed=indexed, training_eligible=len(selected),
                 training_records=len({r['record'] for r in selected}),
                 training_environments=len({r['environment'] for r in selected}),
                 raw_annotation_files=len(paths), raw_endpoint_matches=len(selected),
                 validation_test_label_reads=0,
                 annotation_file_hashes={str(p.relative_to(root)): digest(p) for p in sorted(paths)})
    return quantiles, support_rows, calibration, predictions, audit, paths


def candidate_audit(view, estimates):
    """Only public state + training frozen constants; no engine or truth calls."""
    result = []
    for j, job in view['jobs'].items():
        b = max(view['rules']['reserve_min_s'], view['alpha'] * estimates[job['family_id']] + view['beta'])
        distance = math.dist(view['zones'][view['zone']], view['zones'][job['zone_id']])
        s = view['time'] + distance / view['rules']['movement']['speed'] + view['rules']['movement']['changeover_s']
        fees = view['rules']['fees']
        fee = fees['service_fixed'] + fees['reserve_per_second'] * b + fees['distance_per_unit'] * distance
        eligible = (job['release_s'] <= view['time'] and j not in view['completed']
                    and all(p in view['completed'] for p in job['predecessors']))
        result.append(dict(job_id=j, due=job['due_s'], b=b, start=s, fee=fee,
                           eligible=eligible, horizon_ok=s + b <= view['case']['horizon_s'],
                           budget_ok=fee <= view['budget_left'],
                           horizon_margin=view['case']['horizon_s'] - s - b,
                           budget_margin=view['budget_left'] - fee))
    return result


def choose(view, estimates):
    candidates = candidate_audit(view, estimates)
    feasible = [(r['due'], r['job_id'], r['b']) for r in candidates
                if r['eligible'] and r['horizon_ok'] and r['budget_ok']]
    if feasible:
        _, j, b = min(feasible)
        return dict(action='EXECUTE', job_id=j, b_s=b)
    future = [j['release_s'] for j in view['jobs'].values()
              if view['time'] < j['release_s'] < view['case']['horizon_s']]
    if future:
        return dict(action='WAIT', seconds=min(future) - view['time'])
    return dict(action='STOP')


def edd_policy(estimates):
    frozen = dict(estimates)
    return lambda view: choose(view, frozen)


def truth_check(trial, log):
    """Authenticate settlement from external fabricated d, including censoring.

    verify_ledger alone cannot authenticate actual service time; this check can.
    No score/quote function is used to calculate its expected quantities.
    """
    completed, attempted = {}, set()
    H, B = trial.case['horizon_s'], trial.case['cost_budget']
    margins = []
    for e in log['ledger']:
        assert e['cost_after'] <= B + EPS and e['end'] <= H + EPS
        if e['action'] != 'EXECUTE':
            continue
        j = e['job_id']
        assert j not in attempted
        attempted.add(j)
        source = trial.sources[j]
        p = trial.alpha * source.duration + trial.beta
        C = e['start'] + p
        assert e['source_ref_offline'] == source.ref
        if C <= H:
            same(e['completion'], C)
            completed[j] = C
            same(e['idle'], max(e['b'] - p, 0))
        else:
            assert e['completion'] is None and e['censored']
            same(e['idle'], 0)
        same(e['end'], min(H, e['start'] + max(p, e['b'])))
        same(e['overrun'], max(min(p, H - e['start']) - e['b'], 0))
        same(e['end'] - e['start'], e['b'] + e['overrun'])
        margins.append(H - e['start'] - e['b'])
    W = sum(j['priority_weight'] for j in trial.jobs.values())
    unfinished = set(trial.jobs) - set(completed)
    unfinished_tardiness = sum(trial.jobs[j]['priority_weight'] * max(H - trial.jobs[j]['due_s'], 0)
                              for j in unfinished)
    tardiness = unfinished_tardiness + sum(trial.jobs[j]['priority_weight'] * max(C - trial.jobs[j]['due_s'], 0)
                                          for j, C in completed.items())
    same(log['metrics']['weighted_tardiness_s'], tardiness)
    same(log['metrics']['weighted_completion_rate'], sum(trial.jobs[j]['priority_weight'] for j in completed) / W)
    same(log['metrics']['weighted_on_time_rate'], sum(trial.jobs[j]['priority_weight'] for j, C in completed.items()
                                                      if C <= trial.jobs[j]['due_s']) / W)
    assert len(log['calls']) <= trial.rules['max_actions']
    return dict(executed=len(attempted), completed=len(completed), unfinished=len(unfinished),
                unattempted=len(trial.jobs) - len(attempted), unfinished_tardiness_s=unfinished_tardiness,
                min_admission_horizon_margin_s=min(margins, default=H),
                final_budget_left=B - log['metrics']['cost'], all_jobs_weight=W)


def action_key(action):
    return {k: v for k, v in action.items() if k != 'b_s'}


def forks(left, right, q75):
    detail = {}
    for i, (a, b) in enumerate(zip(left['ledger'], right['ledger'])):
        if a.get('job_id') == b.get('job_id') and a['action'] == b['action'] == 'EXECUTE':
            if 'first_occupancy' not in detail and not math.isclose(a['end'], b['end'], abs_tol=EPS, rel_tol=0):
                detail['first_occupancy'] = dict(index=i, median_event=a, q75_event=b)
    for i in range(max(len(left['calls']), len(right['calls']))):
        a = left['calls'][i] if i < len(left['calls']) else None
        b = right['calls'][i] if i < len(right['calls']) else None
        if a is None or b is None or action_key(a['action']) != action_key(b['action']):
            detail['first_selection'] = dict(index=i, median_call=a, q75_call=b)
            for name, call in (('at_median_state', a), ('at_q75_state', b)):
                if call:
                    view = call['input']
                    detail['first_selection'][name] = dict(
                        median_action=choose(view, TRAIN_MEDIAN_D), q75_action=choose(view, q75),
                        median_candidates=candidate_audit(view, TRAIN_MEDIAN_D),
                        q75_candidates=candidate_audit(view, q75))
            break
    detail['first_reservation_action'] = dict(index=0, median=left['calls'][0]['action'],
                                             q75=right['calls'][0]['action'])
    return detail


def fabricated_trial(rules, zones, jobs, case, mapping, recipe):
    sources = {}
    for i, j in enumerate(sorted(jobs)):
        d = 2. if recipe == 'short_flat' else 12. if recipe == 'long_flat' else (2., 6., 14., 3., 9.)[i % 5]
        sources[j] = Source('FABRICATED/' + recipe + '/' + j, d,
                             [dict(timestamp_s=0.), dict(timestamp_s=.2)])
    return Trial(deepcopy(jobs), sources, deepcopy(zones), deepcopy(case), deepcopy(rules), *mapping)


def metric_summary(pairs):
    output = []
    for case in CASES:
        for alpha, beta in MAPPINGS:
            group = [p for p in pairs if (p['case'], p['alpha'], p['beta']) == (case, alpha, beta)]
            assert len(group) == len(RECIPES)
            for name in group[0]['median']['metrics']:
                a = [float(p['median']['metrics'][name]) for p in group]
                b = [float(p['q75']['metrics'][name]) for p in group]
                differences = [y - x for x, y in zip(a, b)]
                output.append(dict(scope='fabricated_design_not_validation', case=case, alpha=alpha, beta_s=beta,
                                   pairs=len(group), metric=name, median_mean=fmean(a), q75_mean=fmean(b),
                                   mean_q75_minus_median=fmean(differences), sd_difference=stdev(differences),
                                   min_difference=min(differences), max_difference=max(differences)))
    return output


def win_counts(pairs):
    output = []
    groups = [(case, mapping) for mapping in ('all_five', 'nominal') for case in (*CASES, 'ALL_DESIGN')]
    for case, mapping in groups:
        group = [p for p in pairs if (case == 'ALL_DESIGN' or p['case'] == case)
                 and (mapping == 'all_five' or (p['alpha'], p['beta']) == (1., .5))]
        counts = Counter()
        for p in group:
            a, b = p['median']['metrics'], p['q75']['metrics']
            on = b['weighted_on_time_rate'] - a['weighted_on_time_rate']
            td = b['weighted_tardiness_s'] - a['weighted_tardiness_s']
            on_result = 'tie' if abs(on) <= EPS else 'improve' if on > 0 else 'worsen'
            td_result = 'tie' if abs(td) <= EPS else 'improve' if td < 0 else 'worsen'
            lex = on_result if on_result != 'tie' else td_result
            for metric, result in (('on_time', on_result), ('tardiness', td_result), ('lexicographic', lex)):
                counts[metric + '_' + result] += 1
            counts['selection_forks'] += 'first_selection' in p['forks']
            counts['occupancy_forks'] += 'first_occupancy' in p['forks']
        output.append(dict(scope='fabricated_design_not_probability', case=case, mapping=mapping, pairs=len(group),
                           **{k: counts[k] for k in [m + '_' + r for m in ('on_time', 'tardiness', 'lexicographic')
                                                    for r in ('improve', 'tie', 'worsen')]},
                           selection_forks=counts['selection_forks'], occupancy_forks=counts['occupancy_forks']))
    return output


def boundary_tests(view, q75):
    """Public-state unit probes, NOT extra official task cases or trial outcomes."""
    view = deepcopy(view)
    j1, j2 = 'S1_J01', 'S1_J02'
    assert view['time'] == 0 and view['zone'] == 'Z0'
    tests = []
    # Fee for Q75 J01 is 2 + .1*8.5 + .2*2 = 3.25.
    view['budget_left'] = 3.25
    assert choose(view, q75)['job_id'] == j1
    tests.append('budget_exact_accept')
    view['budget_left'] -= 1e-6
    assert choose(view, q75)['job_id'] == j2
    assert choose(view, TRAIN_MEDIAN_D)['job_id'] == j1
    tests.append('budget_only_selection_fork')
    view['budget_left'] = 32
    view['case']['horizon_s'] = 11.0  # s=2.5; Q75 b=8.5.
    assert choose(view, q75)['job_id'] == j1
    tests.append('horizon_exact_accept')
    view['case']['horizon_s'] -= 1e-6
    assert choose(view, q75)['job_id'] == j2
    assert choose(view, TRAIN_MEDIAN_D)['job_id'] == j1
    tests.append('horizon_only_selection_fork')
    # Demonstrate empty information cannot affect this family-only controller.
    changed = deepcopy(view)
    changed['observations'] = {}
    assert choose(changed, q75) == choose(view, q75)
    tests.append('prefix_not_used')
    assert quantile([0, 10], .75) == 7.5 and quantile([2], .75) == 2
    tests.append('type7_two_point_and_singleton')
    return tests


def synthetic(rules, zones, jobs, q75):
    pairs, audit = [], Counter()
    truth_fault_tested = False
    for case in rules['cases']:
        for mapping in MAPPINGS:
            for recipe in RECIPES:
                trial = fabricated_trial(rules, zones, jobs[case['case_id']], case, mapping, recipe)
                before = deepcopy(trial)
                pair = dict(case=case['case_id'], alpha=mapping[0], beta=mapping[1], recipe=recipe,
                            fabricated_d_s={j: s.duration for j, s in trial.sources.items()})
                for arm, estimates in (('median', TRAIN_MEDIAN_D), ('q75', q75)):
                    log = run_trial(trial, edd_policy(estimates))
                    verify_ledger(log)
                    log['external_truth_audit'] = truth_check(trial, log)
                    assert log['metrics']['valid_protocol'] and not log['metrics']['observations']
                    audit['verified_ledgers'] += 1
                    audit['verified_metric_values'] += len(log['metrics'])
                    audit['external_truth_executions'] += log['external_truth_audit']['executed']
                    audit['unfinished_jobs_scored_at_H'] += log['external_truth_audit']['unfinished']
                    audit['censored_executions'] += log['metrics']['censored_units']
                    for call in log['calls']:
                        v = call['input']
                        # Exact old-controller equivalence on BOTH arms' visited states.
                        assert choose(v, TRAIN_MEDIAN_D) == order_policy('EDD')(v)
                        audit['old_edd_public_state_equivalence_checks'] += 1
                        assert call['action'] == choose(v, estimates)
                        for observation in v['observations'].values():
                            assert observation == dict(prefix_s=.2, rows=[dict(timestamp_s=0.), dict(timestamp_s=.2)])
                        for r in candidate_audit(v, estimates):
                            if r['eligible']:
                                audit['eligible_candidate_checks'] += 1
                                audit['candidate_horizon_rejections'] += not r['horizon_ok']
                                audit['candidate_budget_rejections'] += not r['budget_ok']
                    pair[arm] = log
                    if not truth_fault_tested:
                        wrong = deepcopy(trial)
                        j = log['ledger'][0]['job_id']
                        wrong.sources[j].duration += .125
                        try:
                            truth_check(wrong, log)
                        except AssertionError:
                            truth_fault_tested = True
                        else:
                            raise AssertionError('external truth mismatch was not detected')
                assert trial == before, 'input assignment mutated across arms'
                pair['forks'] = forks(pair['median'], pair['q75'], q75)
                pairs.append(pair)
    assert truth_fault_tested and len(pairs) == 45
    audit['external_truth_fault_injection_detected'] = 1
    audit['synthetic_pairs'] = len(pairs)
    audit['synthetic_runs'] = 2 * len(pairs)
    audit['synthetic_case_mapping_cells'] = len(CASES) * len(MAPPINGS)
    audit['public_state_boundary_tests'] = len(boundary_tests(pairs[0]['median']['calls'][0]['input'], q75))
    audit['max_actions_in_any_trial'] = max(len(p[a]['calls']) for p in pairs for a in ('median', 'q75'))
    audit['min_final_budget_left'] = min(p[a]['external_truth_audit']['final_budget_left']
                                          for p in pairs for a in ('median', 'q75'))
    return pairs, audit


def git_bytes(path, revision='1872d79'):
    return subprocess.check_output(['git', 'show', revision + ':' + path], cwd=ROOT)


def syntax_tree(source, function=None):
    tree = ast.parse(source)
    if function:
        tree = next(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == function)
    return ast.dump(tree, include_attributes=False)


def protocol_evidence(root):
    """Historical proof levels, never imply a missing historical input snapshot."""
    public = root / 'Restocking_Tasks_and_Constraints'
    old_data = git_bytes('research/q3_replay/data.py')
    old_order = git_bytes('research/q3_replay/order_baseline.py').decode()
    old_engine = git_bytes('research/q3_replay/engine.py').decode()
    now_data = (ROOT / 'experiments/q3_replay/data.py').read_bytes()
    now_order = (ROOT / 'experiments/q3_replay/order_baseline.py').read_text()
    now_engine = (ROOT / 'experiments/q3_replay/engine.py').read_text()
    assert old_data == now_data
    assert old_order.replace('from research.q3_prefix.data', 'from experiments.q3_prefix.data') == now_order
    for function in ('Source', 'Trial', 'execution_quote', 'score'):
        assert syntax_tree(old_engine, function) == syntax_tree(now_engine, function)
    old_run = syntax_tree(old_engine, 'run_trial')
    # Only newly added projection differs in the scheduling/settlement function.
    new_run = syntax_tree(now_engine.replace('rows=[public_sample(row) for row', 'rows=[row for row'), 'run_trial')
    assert old_run == new_run
    old_metrics = git_bytes('research/q3_replay/order_results/order_trials.csv')
    assert old_metrics == (ROOT / 'experiments/q3_replay/order_results/order_trials.csv').read_bytes()
    report = git_bytes('research/C题数据首轮核查.md').decode()
    pdf_hash = re.search(r'SHA-256 为 `([a-f0-9]{64})`', report).group(1)
    assert digest(public / 'Problem_3_Requirements.pdf') == pdf_hash
    # This one source witness was committed before the pilot. Never publish it anew.
    first_source_witness = re.search(r'该次抽到 `([^`]+)`', report).group(1)
    tree_files = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', '1872d79'], cwd=ROOT, text=True)
    assert 'data/raw/C/Restocking_Tasks_and_Constraints/source_segment_index.csv' not in tree_files
    with ZipFile(root / 'archives/C_Problem_Data.zip') as package:
        entry = next(i for i in package.infolist() if i.filename.endswith('Restocking_Problem_and_Data_EN.zip'))
        encrypted = bool(entry.flag_bits & 1)
    checks = [
        dict(check='sampler_and_adapters_byte_equal_to_1872d79', status='verified', count=1),
        dict(check='controller_only_import_path_changed', status='verified', count=1),
        dict(check='settlement_score_and_quote_AST_equal', status='verified', count=1),
        dict(check='engine_difference_only_public_prefix_projection', status='verified', count=1),
        dict(check='old_18_metric_rows_byte_equal_to_git', status='verified', count=18),
        dict(check='requirements_pdf_matches_pre_pilot_recorded_hash', status='verified', count=1),
        dict(check='old_full_assignment_ledgers_available', status='unavailable', count=0),
        dict(check='old_full_index_or_task_byte_snapshot_available', status='unavailable', count=0),
        dict(check='outer_archive_member_encrypted', status='blocked_no_password_used', count=int(encrypted)),
    ]
    return checks, first_source_witness, dict(
        historical_pilot_commit=subprocess.check_output(['git', 'rev-parse', '1872d79'], cwd=ROOT, text=True).strip(),
        public_input_hashes={p.name: digest(p) for p in sorted(public.iterdir()) if p.is_file()},
        old_metrics_sha256=hashlib.sha256(old_metrics).hexdigest(),
        first_source_witness_private=first_source_witness,
        note='Protocol-derived assignments, conditional on supplied current index/order being the historical input; NOT old-ledger authentication.')


def original_controller_with_quantiles(q75):
    """Reuse the EXACT current/old EDD function code, with isolated globals.

    Only the frozen family table is substituted. The original module and its
    TRAIN_MEDIAN_D are not mutated, and no median/FCFS trial is executed here.
    """
    namespace = dict(order_policy.__globals__)
    namespace['TRAIN_MEDIAN_D'] = dict(q75)
    factory = FunctionType(order_policy.__code__, namespace, order_policy.__name__,
                           order_policy.__defaults__, order_policy.__closure__)
    return factory('EDD')


def validation_pool_audit(root):
    """Current pool/source integrity, distinct from historical identity proof."""
    public = root / 'Restocking_Tasks_and_Constraints'
    pool = [r for r in rows(public / 'source_segment_index.csv')
            if r['split'] == 'validation' and r['eligible'] == '1']
    assert Counter(r['family_id'] for r in pool) == dict(P=15, A=13, L=7)
    assert len({r['unit_id'] for r in pool}) == len(pool) == 35
    membership = {r['episode_uuid']: r for r in rows(public / 'record_split.csv')
                  if r['split'] == 'validation'}
    files, annotations = set(), {}
    for r in pool:
        meta = membership[r['episode_uuid']]
        assert all(meta[k] == r[k] for k in ('environment_id', 'dataset', 'split'))
        folder = root / 'Raw_Data' / r['dataset'] / r['sample']
        demo = r['dataset'] == 'EgoDemo'
        path = folder / ('annotation.json' if demo else 'tables/annotation_semantic_segments.csv')
        if path not in annotations:
            annotations[path] = json.loads(path.read_text())['subtask_annotations'] if demo else rows(path)
        a = annotations[path][int(r['source_segment_index'])]
        start = Decimal(str(a['start_time' if demo else 'segment.start_time']))
        end = Decimal(str(a['end_time' if demo else 'segment.end_time']))
        assert start == Decimal(r['start_s']) and end == Decimal(r['end_s'])
        same(float(end - start), float(r['duration_s']))
        assert end - start >= Decimal('1.2')
        files.add(path)
        if demo:
            files.add(folder / 'bad_frame_ratio.json')
            files.update((folder / 'data').rglob('*.parquet'))
        else:
            files.update(folder / 'tables' / (name + '.csv') for name in (
                'pose_left_hand', 'pose_right_hand', 'pose_head', 'pose_headcam',
                'pose_right_eye_cam', 'annotation_bad_frame_pose_hand'))
    assert len(membership) == 7 and len({r['environment_id'] for r in pool}) == 4
    # Recheck ONLY validation files used by the adapter against local raw ZIP CRCs.
    archive_checks = []
    for dataset in sorted({r['dataset'] for r in pool}):
        with ZipFile(root / 'Raw_Data' / (dataset + '.zip')) as archive:
            for path in sorted(files):
                relative = path.relative_to(root / 'Raw_Data')
                if relative.parts[0] != dataset:
                    continue
                info = archive.getinfo(relative.as_posix())
                data = path.read_bytes()
                assert len(data) == info.file_size and crc32(data) == info.CRC
                archive_checks.append(dict(path=str(relative), bytes=len(data), crc32=info.CRC))
    return pool, dict(pool_units=35, pool_records=7, pool_environments=4,
                     raw_endpoint_matches=35, raw_files_crc_matches=len(archive_checks),
                     raw_file_checks_private=archive_checks)


def unpack_private(document):
    log = deepcopy(document['log'])
    for call in log['calls']:
        for observation in call['input']['observations'].values():
            key = observation.pop('rows_sha256')
            payload = document['payloads'][key]
            raw = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
            assert hashlib.sha256(raw).hexdigest() == key
            observation['rows'] = payload
    return log


def compare_archived_metrics(private_rows):
    output = []
    names = ('weighted_completion_rate', 'weighted_on_time_rate', 'weighted_tardiness_s',
             'elapsed_s', 'cost', 'observations', 'observed_overrun_s', 'reserved_idle_s',
             'censored_units', 'invalid_actions', 'valid_protocol')
    assert set(names) == set(private_rows[0]['metrics'])
    for case in CASES:
        group = [r for r in private_rows if r['case'] == case]
        assert len(group) == 3
        for metric in names:
            a = [float(r['old_edd_metrics'][metric]) for r in group]
            b = [float(r['metrics'][metric]) for r in group]
            delta = [y - x for x, y in zip(a, b)]
            output.append(dict(scope='real_validation_protocol_pair_not_historical_ledger_pair',
                case=case, split='validation', alpha=1., beta_s=.5, pairs=3, metric=metric,
                archived_edd_mean=fmean(a), new_q75_mean=fmean(b), mean_q75_minus_edd=fmean(delta),
                sd_paired_difference=stdev(delta), min_paired_difference=min(delta),
                max_paired_difference=max(delta)))
    return output


def validation_wins(private_rows):
    output = []
    for case in (*CASES, 'ALL_THREE_CASES'):
        group = [r for r in private_rows if case == 'ALL_THREE_CASES' or r['case'] == case]
        counts = Counter()
        for r in group:
            a, b = r['old_edd_metrics'], r['metrics']
            on, td = b['weighted_on_time_rate'] - a['weighted_on_time_rate'], b['weighted_tardiness_s'] - a['weighted_tardiness_s']
            on_result = 'tie' if abs(on) <= EPS else 'improve' if on > 0 else 'worsen'
            td_result = 'tie' if abs(td) <= EPS else 'improve' if td < 0 else 'worsen'
            lex = td_result if on_result == 'tie' else on_result
            for metric, result in (('on_time', on_result), ('tardiness', td_result), ('lexicographic', lex)):
                counts[metric + '_' + result] += 1
        output.append(dict(scope='real_validation_protocol_pair_not_historical_ledger_pair', case=case,
                           pairs=len(group), **{m + '_' + r: counts[m + '_' + r]
                           for m in ('on_time', 'tardiness', 'lexicographic') for r in ('improve', 'tie', 'worsen')}))
    return output


def validation_q75(root):
    """Only nine Q75 executions; archived EDD CSV is the sole baseline result."""
    from experiments.q3_replay import data as adapter
    from experiments.q3_replay.acceptance import packed
    from experiments.q3_replay.engine import public_sample

    assert root == adapter.RULES.parent.resolve(), 'validation uses the unchanged historical adapter paths'
    directory = PRIVATE / 'validation'
    directory.mkdir(exist_ok=True)
    checks, witness, provenance = protocol_evidence(root)
    rules, zones, jobs = public_inputs(adapter.RULES)
    q75, _, _, _, train_audit, _ = training(root)
    assert q75 == dict(P=8., A=4.85, L=10.35)
    # Q75 fixed from training BEFORE reading any validation labels or outcomes.
    pool, pool_audit = validation_pool_audit(root)
    old_rows = {(r['case'], int(r['seed'])): r
                for r in rows(ROOT / 'experiments/q3_replay/order_results/order_trials.csv') if r['order'] == 'EDD'}
    assert set(old_rows) == {(c, s) for c in CASES for s in SEEDS}
    old_constants = ast.parse(git_bytes('research/q3_replay/order_baseline.py'))
    constants = {n.targets[0].id: ast.literal_eval(n.value) for n in old_constants.body
                 if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)}
    assert constants['SEEDS'] == SEEDS and constants['CASES'] == CASES
    assert constants['TRAIN_MEDIAN_D'] == TRAIN_MEDIAN_D
    runtime_signature = dict(script_sha256=digest(Path(__file__)), q75=q75, python=sys.version,
        data_sha256=digest(ROOT / 'experiments/q3_replay/data.py'),
        engine_sha256=digest(ROOT / 'experiments/q3_replay/engine.py'),
        order_sha256=digest(ROOT / 'experiments/q3_replay/order_baseline.py'),
        public_hashes=provenance['public_input_hashes'])
    # A separate stable spec allows later audit/report-only script improvements.
    execution_spec = {k: v for k, v in runtime_signature.items() if k != 'script_sha256'}
    private_rows, private_assignments, summary_checks = [], [], Counter()
    sampled, records_seen, environments_seen = set(), set(), set()
    for case in CASES:
        for seed in SEEDS:
            rng = random.Random(int(seed))
            expected = {}
            for job in sorted(jobs[case]):
                family = jobs[case][job]['family_id']
                unit = rng.choice([r for r in pool if r['family_id'] == family])
                expected[job] = unit
            if case == 'S1' and seed == SEEDS[0]:
                assert expected['S1_J01']['unit_id'] == witness
                summary_checks['prepilot_first_job_source_witness_matches'] += 1
            # This loads input once, using the unmodified byte-identical adapter;
            # it does not run either old policy or call run_pair/pilot.
            trial = adapter.validation_trial(case, seed)
            assert trial.jobs == jobs[case] and trial.zones == zones and trial.rules == rules
            assert (trial.alpha, trial.beta) == (1., .5)
            assignment = []
            for job, unit in expected.items():
                source = trial.sources[job]
                d = float(Decimal(unit['end_s']) - Decimal(unit['start_s']))
                assert source.ref == unit['unit_id'] and source.duration == d
                assert all(public_sample(row) == row for row in source.rows)
                assert [r for r in source.rows if 0 <= r['timestamp_s'] <= .2]
                assignment.append(dict(job_id=job, unit_id=source.ref, d_s=d, p_s=d+.5,
                                       source_index_row=unit))
                sampled.add(source.ref)
                records_seen.add(unit['episode_uuid'])
                environments_seen.add(unit['environment_id'])
                summary_checks['derived_assignment_matches_adapter'] += 1
            path = directory / f'{case}_{seed}_q75.json'
            lock = directory / f'{case}_{seed}.started'
            if path.exists():
                document = json.loads(path.read_text())
                assert document['execution_spec'] == execution_spec
                assert document['assignments'] == assignment
                log = unpack_private(document)
                summary_checks['cached_q75_reaudited_this_invocation'] += 1
            else:
                if lock.exists():
                    raise RuntimeError('Prior Q75 execution interrupted; inspect private marker rather than rerunning automatically')
                # Before run_trial, freeze inputs in the local ignored audit file.
                write_json(directory / f'{case}_{seed}_assignment.json', dict(
                    provenance='DERIVED_FROM_CURRENT_OFFICIAL_PROTOCOL_NOT_OLD_LEDGER',
                    case=case, seed=seed, alpha=1., beta=.5, assignments=assignment))
                lock.write_text('Q75 only; no automatic replay after interrupted execution\n')
                policy = original_controller_with_quantiles(q75)
                log = run_trial(trial, policy)  # The ONLY empirical scheduler call.
                payloads = {}
                document = dict(execution_spec=execution_spec, runtime=runtime_signature,
                                assignments=assignment, log=packed(log, payloads), payloads=payloads)
                write_json(path, document)  # Retain violations too, before assertions.
                summary_checks['new_q75_executions_this_invocation'] += 1
            verify_ledger(log)
            truth = truth_check(trial, log)
            old = old_rows[case, seed]
            assert (old['split'], float(old['alpha']), float(old['beta_s'])) == ('validation', 1., .5)
            assert int(old['matched_jobs']) == len(trial.jobs)
            baseline_metrics = {k: old[k] == 'True' if k == 'valid_protocol' else float(old[k])
                                for k in log['metrics']}
            # Witness the actual new controller actions, public prefixes, and no
            # effect from removal of all (unused) observations, at each call.
            policy = original_controller_with_quantiles(q75)
            for call in log['calls']:
                view = call['input']
                assert policy(view) == choose(view, q75) == call['action']
                no_prefix = deepcopy(view)
                no_prefix['observations'] = {}
                assert policy(no_prefix) == call['action']
                for job, observation in view['observations'].items():
                    assert observation == dict(prefix_s=.2, rows=[r for r in trial.sources[job].rows
                                                                  if 0 <= r['timestamp_s'] <= .2])
                    summary_checks['new_free_prefix_authentications'] += 1
                summary_checks['new_controller_action_checks'] += 1
                for r in candidate_audit(view, q75):
                    if r['eligible']:
                        summary_checks['new_candidate_horizon_rejections'] += not r['horizon_ok']
                        summary_checks['new_candidate_budget_rejections'] += not r['budget_ok']
            assert log['metrics']['observations'] == 0
            summary_checks['new_verified_ledgers'] += 1
            summary_checks['new_metric_values_verified'] += len(log['metrics'])
            summary_checks['new_truth_checked_executions'] += truth['executed']
            summary_checks['new_unfinished_jobs_scored_at_H'] += truth['unfinished']
            summary_checks['new_censored_executions'] += log['metrics']['censored_units']
            private_rows.append(dict(case=case, seed=seed, metrics=log['metrics'], old_edd_metrics=baseline_metrics,
                                     truth_audit=truth, actions=len(log['calls'])))
            private_assignments.append(dict(case=case, seed=seed, assignments=assignment))
    assert len(private_rows) == 9 and summary_checks['derived_assignment_matches_adapter'] == 108
    summary_checks.update(total_q75_trials=9, protocol_metric_pairs=9, old_edd_or_fcfs_executions=0,
        historical_full_ledger_pairs_authenticated=0, empirical_historical_first_forks_authenticated=0,
        unique_sampled_units=len(sampled), sampled_records=len(records_seen), sampled_environments=len(environments_seen),
        validation_pool_endpoint_matches=pool_audit['raw_endpoint_matches'],
        validation_raw_files_crc_matches=pool_audit['raw_files_crc_matches'],
        test_records_read=0, max_q75_actions=max(r['actions'] for r in private_rows),
        min_q75_budget_left=min(r['truth_audit']['final_budget_left'] for r in private_rows))
    checks.extend([
        dict(check='current_pool_counts_match_pre_pilot_report', status='verified_aggregate_only', count=35),
        dict(check='current_validation_raw_endpoints_match_index', status='verified_current_not_historical', count=35),
        dict(check='current_used_raw_files_match_local_ZIP_CRC', status='verified_current_not_historical', count=pool_audit['raw_files_crc_matches']),
        dict(check='pre_pilot_first_source_witness_matches', status='verified_single_witness_not_full_assignment', count=1),
        dict(check='derived_assignments_equal_unchanged_adapter', status='verified_current_protocol_not_old_ledger', count=108),
    ])
    write_csv(OUT / 'validation_paired.csv', compare_archived_metrics(private_rows))
    write_csv(OUT / 'validation_win_counts.csv', validation_wins(private_rows))
    write_csv(OUT / 'validation_provenance.csv', checks)
    # Invocation counts stay private: a read-only audit must not change the
    # aggregate total from nine experiments to zero or eighteen.
    aggregate_checks = {k: v for k, v in summary_checks.items() if not k.endswith('_this_invocation')}
    write_csv(OUT / 'validation_audit.csv', [dict(check=k, count=v) for k, v in sorted(aggregate_checks.items())])
    write_json(directory / 'metric_pairs.json', private_rows)
    write_json(directory / 'derived_assignments.json', private_assignments)
    manifest_path = directory / 'manifest.json'
    first_execution = json.loads(manifest_path.read_text())['first_invocation'] if manifest_path.exists() else dict(summary_checks)
    write_json(manifest_path, dict(first_invocation=first_execution, latest_invocation=dict(summary_checks),
        runtime=runtime_signature, provenance=provenance, pool_audit=pool_audit,
        training_audit=train_audit, checks=checks,
        limitation='No historical full input snapshot or EDD ledger; do not infer empirical event forks.'))
    print(json.dumps(dict(scope='REAL VALIDATION: Q75 only; protocol pairing with archived EDD metrics',
                          audit=dict(summary_checks), wins=validation_wins(private_rows),
                          public_csv_prefix='research/feas_reservation/validation_',
                          private_directory='runs/feas_reservation/validation'), ensure_ascii=False, indent=2))


def main():
    if not __debug__:
        raise RuntimeError('Run without -O: audit assertions are required')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('training-synthetic', 'validation-q75'), nargs='?', default='training-synthetic')
    parser.add_argument('--root', type=Path, default=ROOT / 'data/raw/C')
    args = parser.parse_args()
    root = args.root.resolve()
    # No caller-specified detail path can accidentally put labels in tracked files.
    result = subprocess.run(['git', 'check-ignore', str(PRIVATE / 'training_predictions.csv')],
                            cwd=ROOT, check=True, capture_output=True, text=True)
    assert result.stdout.strip()
    OUT.mkdir(parents=True, exist_ok=True)
    PRIVATE.mkdir(parents=True, exist_ok=True)
    if args.mode == 'validation-q75':
        validation_q75(root)
        return
    public = root / 'Restocking_Tasks_and_Constraints'
    opened = set()

    def audit_open(event, arguments):
        if event == 'open' and isinstance(arguments[0], (str, bytes)):
            name = arguments[0].decode() if isinstance(arguments[0], bytes) else arguments[0]
            path = Path(name).resolve()
            if path.is_relative_to(root):
                opened.add(path)
    sys.addaudithook(audit_open)
    gate = legacy_gate()
    rules, zones, jobs = public_inputs(public)
    q75, support, calibration, predictions, train_audit, annotation_paths = training(root)
    pairs, audit = synthetic(rules, zones, jobs, q75)
    allowed = annotation_paths | {public / f for f in (
        'common_conditions.json', 'tasks.csv', 'service_zones.csv', 'record_split.csv', 'source_segment_index.csv')}
    assert opened <= allowed, 'unexpected raw data access'
    audit.update(training_eligible=train_audit['training_eligible'],
                 training_endpoint_matches=train_audit['raw_endpoint_matches'],
                 train_environment_folds=sum(r['environments'] for r in support),
                 empirical_new_runs=0, empirical_verified_pairs=0, old_nominal_runs_reexecuted=0,
                 validation_test_annotation_reads=0, actual_opened_data_files=len(opened))
    write_csv(OUT / 'training_support.csv', support)
    write_csv(OUT / 'training_calibration.csv', calibration)
    write_csv(OUT / 'synthetic_paired.csv', metric_summary(pairs))
    wins = win_counts(pairs)
    write_csv(OUT / 'synthetic_win_counts.csv', wins)
    write_csv(OUT / 'audit_coverage.csv', [dict(check=k, count=v) for k, v in sorted(audit.items())])
    write_csv(PRIVATE / 'training_predictions.csv', predictions)
    write_json(PRIVATE / 'synthetic_trials.json', pairs)
    write_json(PRIVATE / 'legacy_gate.json', gate)
    tracked_inputs = [ROOT / 'experiments/q3_replay' / f for f in
                      ('engine.py', 'acceptance.py', 'order_baseline.py', 'data.py')]
    manifest = dict(python=sys.version, base_commit=subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        script_sha256=digest(Path(__file__)), training=train_audit, gate=gate,
        file_hashes={str(p.relative_to(ROOT)): digest(p) for p in tracked_inputs},
        public_file_hashes={p.name: digest(p) for p in sorted(allowed - annotation_paths)},
        data_files_opened=[str(p.relative_to(root)) for p in sorted(opened)],
        recipes=dict(short_flat='d=2 for all jobs', long_flat='d=12 for all jobs',
                     heterogeneous_cycle='sorted job_id, cyclic d=(2,6,14,3,9)'),
        mappings=MAPPINGS, q75_frozen=q75, audit=dict(audit))
    write_json(PRIVATE / 'manifest.json', manifest)
    print(json.dumps(dict(scope='TRAIN + FABRICATED ONLY; zero empirical reruns',
                          gate={k: gate[k] for k in ('legacy_metric_pairs', 'legacy_edd_ledgers_authenticated',
                                                   'empirical_paired_comparisons')},
                          audit=dict(audit), win_counts=wins,
                          aggregates=str(OUT.relative_to(ROOT)), private_details=str(PRIVATE.relative_to(ROOT))),
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
