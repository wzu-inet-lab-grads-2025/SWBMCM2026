#!/usr/bin/env python3
"""Deterministic representative scenarios, hand oracles, and independent replay."""
import copy
import csv
import hashlib
import json
import math
from pathlib import Path
import random
import subprocess
import sys

from audit import audit, close, ray_hits_box, vector_close
from retention import ROOT, SCENE, SNAPSHOT, _run_plan, interpolate_path, read_scenarios, run, state_at

HERE = Path(__file__).resolve().parent
OUT = ROOT / 'runs/b/q4_retention'
S1 = 1.5320327145575703
S2 = 10.197744025090035
S3 = 10.3521185907042
F3 = 7.564192864578590


def representatives():
    # New-alert distribution is NOT specified by the problem. This is an
    # unweighted factorial test set, not a claim about an expected distribution.
    new_alerts = [('none', None), ('NE30', [45, 30, 50]), ('E40', [0, 40, 50]),
                  ('NW50', [150, 50, 50]), ('S50', [270, 50, 50])]
    recovery_times = [None, 10, 20, 30]
    rows = []
    for label, new_alert in new_alerts:
        for failed in [None, 'V1', 'V2', 'V3', 'V4', 'V5']:
            for recovery in recovery_times:
                rows.append({'id': f'{label}__{failed or "healthy"}__{recovery or "online"}',
                             'new_alert': new_alert, 'failed_vehicle': failed, 'recovery_s': recovery})
    return {'scenarios': rows}


def hand_starts(scenario, mode):
    # Closed-form oracle specific to the fixed q22 topology. Not the simulator's
    # phase transition logic: T1 is locked, T2 uses V2/V4, T3 uses V3/V1.
    failed, rec = scenario.get('failed_vehicle'), scenario.get('recovery_s')
    starts = {'T1': S1, 'T2': None if failed in ('V2', 'V4') else S2,
              'T3': None if failed in ('V1', 'V3') else S3}
    if mode == 'current_only' and rec is not None and starts['T3'] is not None:
        # T3's original V3 leg becomes dispatchable only after T1 reset.
        starts['T3'] = max(F3, rec) + 0.7795042272883554 + 2.008421498837253
        if starts['T3'] > 60:
            starts['T3'] = None
    if scenario.get('new_alert') is not None:
        starts['T4'] = None
    return starts


def endpoint_plan(arrival_s):
    # Deliberately geometry-free fixtures isolate exact-time event precedence.
    # Coincident synthetic stations are not claims of field feasibility.
    scene = json.loads(SCENE.read_text())
    scene['centre_xy_m'] = [0., 0.]
    scene['vehicles_xy_m'] = {f'V{i}': [0., 0.] for i in range(1, 6)}
    scene['alerts_azimuth_deg_range_m_altitude_m']['T1'] = [0., 1000., 2.]
    station = [3. * arrival_s, 0.]
    legs = [{'vehicle': v, 'station': station, 'path': [[0., 0.], station], 'depart_s': 0.}
            for v in ('V1', 'V2')]
    return [{'target': 'T1', 'legs': legs}], scene


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    scenario_set = representatives()
    (HERE / 'scenarios.json').write_text(json.dumps(scenario_set, ensure_ascii=False, indent=2) + '\n')
    scene = json.loads(SCENE.read_text())
    location_evidence, seen_locations = [], set()
    for scenario in scenario_set['scenarios']:
        polar = scenario['new_alert']
        if polar is None or tuple(polar) in seen_locations:
            continue
        seen_locations.add(tuple(polar))
        theta, radius, height = polar
        xyz = [scene['centre_xy_m'][0] + radius * math.cos(math.radians(theta)),
               scene['centre_xy_m'][1] + radius * math.sin(math.radians(theta)), height]
        row = {'name': scenario['id'].split('__')[0], 'polar': polar, 'xyz_m': xyz, 'initial_station_visibility': {}}
        for vehicle, xy in scene['vehicles_xy_m'].items():
            elevation = math.degrees(math.atan2(height - scene['camera_height_m'], math.dist(xy, xyz[:2])))
            blocked = [name for name, box in scene['buildings_xyxy_height_m'].items()
                       if ray_hits_box(xy, xyz, box, scene['camera_height_m'])]
            row['initial_station_visibility'][vehicle] = {'elevation_deg': elevation, 'blocking_buildings': blocked,
                                                          'observable': not blocked and -10 <= elevation <= 80}
        location_evidence.append(row)
    (HERE / 'new_alert_geometry.json').write_text(json.dumps(location_evidence, indent=2) + '\n')
    checks, audit_checks = 0, 0
    named_tests = []
    def require(condition, label):
        nonlocal checks
        checks += 1
        if not condition:
            raise AssertionError(label)
    def done(label):
        named_tests.append(label)
    def check_result(result):
        nonlocal audit_checks
        checked = audit(result)
        audit_checks += checked['checks']
        starts = hand_starts(result['scenario'], result['dispatch_mode'])
        for target, expected in starts.items():
            actual = result['jobs'][target]['start_s']
            require(actual is None if expected is None else actual is not None and close(actual, expected), f'hand start {target}')
        n, k = len(starts), sum(t is not None for t in starts.values())
        last = max((t for t in starts.values() if t is not None), default=0.) if k == n else 60.
        require((result['N'], result['k']) == (n, k), 'hand N/k')
        require(close(result['eta'], k/n) and close(result['t_last_s'], last), 'hand score')
        return checked

    nominal = run({'id': 'nominal'})
    for job in json.loads(SNAPSHOT.read_text())['q22']['jobs']:
        require(close(nominal['jobs'][job['target']]['start_s'], job['start_s']), 'snapshot start replay')
        for leg in job['legs']:
            ev = [e for e in nominal['events'] if e['target'] == job['target'] and e['vehicle'] == leg['vehicle']]
            for kind, start_key, end_key in [('drive', 'depart_s', 'arrival_s'), ('slew', 'arrival_s', 'ready_s'),
                                              ('observe', None, 'observation_end_s'), ('reset', 'observation_end_s', 'reset_end_s')]:
                e = next(e for e in ev if e['kind'] == kind)
                require(close(e['t0'], job['start_s'] if start_key is None else leg[start_key]), 'snapshot phase start')
                require(close(e['t1'], leg[end_key]), 'snapshot phase end')
    done('all q22 starts, arrivals, slew readiness, observation ends and reset ends match snapshot')

    s5 = nominal['state_5s_before']
    require([s5[f'V{i}']['phase'] for i in range(1,6)] == ['wait','drive','observe','slew','observe'], '5s phases')
    dx, dy, length = 72.70860061292497 - 88., 1.5000000000000155 - 10., 17.495053449905157
    p2 = [88. + 15. * dx / length, 10. + 15. * dy / length]
    require(vector_close(s5['V2']['position_xy_m'], p2), 'hand V2 15m travelled')
    require(vector_close(s5['V2']['gimbal_deg'], [0.,0.]), 'moving vehicle pose')
    require(close(s5['V4']['gimbal_deg'][0], (5. - 0.9335681693539041) * 30.), 'V4 incomplete azimuth slew')
    require(close(s5['V4']['gimbal_deg'][1], 23.184672017939654), 'V4 elevation reached first')
    require(s5['V3']['locked_occupancy'] and not s5['V3']['reassignable'], 'T1 lock not vehicle release')
    require(close(s5['V3']['release_after_reset_s'], F3), 'V3 release after reset, not at 5')
    done('5s full-fleet physical state and independent arc-length/gimbal hand calculation')

    # Arc-length tests explicitly use a bend and a duplicate vertex, not only
    # the straight segments present in the given q22 snapshot.
    path = [[0.,0.],[0.,0.],[3.,0.],[3.,4.]]
    for d, expected in [(-1,[0,0]), (0,[0,0]), (2,[2,0]), (3,[3,0]), (5,[3,2]), (7,[3,4]), (9,[3,4])]:
        p, prefix = interpolate_path(path, d)
        require(vector_close(p, expected) and vector_close(prefix[-1], p), f'polyline position {d}')
    done('continuous multi-segment interpolation, exact vertex, zero-length segment and clamps')
    bend_plan, bend_scene = endpoint_plan(8.)
    bend_scene['vehicles_xy_m'] = {f'V{i}': [10.,10.] for i in range(1,6)}
    for leg in bend_plan[0]['legs']:
        leg['path'] = [[10.,10.],[22.,10.],[22.,22.]]
        leg['station'] = [22.,22.]
    bend_result = _run_plan({'id':'bend_failure', 'failed_vehicle':'V1'}, bend_plan, bend_scene)
    failure = next(e for e in bend_result['events'] if e['kind'] == 'fail')
    drive = next(e for e in bend_result['events'] if e['vehicle'] == 'V1' and e['kind'] == 'drive')
    require(vector_close(failure['position_xy_m'], [22.,13.]), 'failure after 12m plus 3m at turn')
    require(drive['path'] == [[10.,10.],[22.,10.],[22.,13.]], 'truncated polyline includes only travelled prefix')
    require(vector_close(state_at(bend_result, 4.5)['V1']['position_xy_m'], [22.,11.5]), 'replay interpolate after bend')
    require(vector_close(state_at(bend_result, 4., side='left')['V1']['position_xy_m'], [22.,10.]), 'bend continuity')
    done('synthetic multi-segment drive truncated by 5s failure, vertex continuity and post-turn state replay')

    # Failures must cut three distinct activities: V1 wait, V2 drive, V3/V5
    # observe, V4 slew. Their healthy partners retain the original dependencies.
    fail_results = {v: run({'id': 'fail_' + v, 'failed_vehicle': v}) for v in s5}
    for v, r in fail_results.items():
        require(r['state_5s_before'] == nominal['state_5s_before'], 'no future failure leak before 5')
        require(r['state_5s_after'][v]['phase'] == 'failed', 'right-limit failure')
        fail = next(e for e in r['events'] if e['kind'] == 'fail')
        require(vector_close(fail['position_xy_m'], s5[v]['position_xy_m']), 'failure physical point')
        require(r['jobs']['T1']['locked_at_5s'], 'T1 score survives any failure')
    fail_v5 = fail_results['V5']
    v3_obs = next(e for e in fail_v5['events'] if e['vehicle'] == 'V3' and e['target'] == 'T1' and e['kind'] == 'observe')
    require(close(v3_obs['t1'], 6.5320327145575705), 'healthy partner retains observation occupancy')
    require(fail_v5['k'] == 3 and fail_results['V3']['k'] == 2, 'reuse identity asymmetry')
    for failed, waiting in [('V1','V3'), ('V2','V4'), ('V3','V1'), ('V4','V2')]:
        r = fail_results[failed]
        e = [e for e in r['events'] if e['vehicle'] == waiting][-1]
        require(e['kind'] == 'wait' and e['t1'] == 60., f'{waiting} waits original failed partner')
    done('all five failure identities: truncate wait/drive/slew/observe, preserve score and healthy occupancy')

    summary, records = [], []
    audit_rows = []
    for mode in ('preloaded', 'current_only'):
        output = OUT / (mode + '.jsonl')
        with output.open('w') as handle:
            for scenario in scenario_set['scenarios']:
                r = run(scenario, dispatch_mode=mode)
                audit_rows.append(check_result(r))
                require(r['state_5s_before'] == nominal['state_5s_before'], 'identical 5-minus across all disturbances')
                require(r == run(scenario, dispatch_mode=mode), 'deterministic rerun')
                if mode == 'preloaded':
                    no_outage = run(dict(scenario, recovery_s=None))
                    require(r['events'] == no_outage['events'], 'preloaded outage invariance including every event')
                handle.write(json.dumps(r, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n')
                summary.append({'scenario_id': r['scenario_id'], 'policy': r['policy'], 'new_alert': json.dumps(scenario['new_alert']),
                                'failed_vehicle': scenario['failed_vehicle'], 'recovery_s': scenario['recovery_s'],
                                'N': r['N'], 'k': r['k'], 'eta': r['eta'], 't_last_s': r['t_last_s'],
                                **{t+'_start_s': j['start_s'] for t, j in r['jobs'].items()}})
                if mode == 'preloaded' and scenario['new_alert'] is None and scenario['recovery_s'] is None:
                    records.append(r)
    done('120 main scenarios and 120 current-only sensitivities checked against hand topology and independent geometry/events')
    done('deterministic duplicate runs, identical 5-minus states, event-for-event preloaded outage invariance')

    # Late restoration is outside the main contract and explicitly labelled.
    ranges = []
    for rec in [8., 35., 60., 70.]:
        for mode in ['preloaded', 'current_only']:
            r = run({'id': f'range_{rec}', 'recovery_s': rec}, dispatch_mode=mode)
            check_result(r)
            require(r['recovery_scope'] == 'range_sensitivity', 'range sensitivity not silently main result')
            ranges.append(r)
    (OUT / 'range_sensitivity.jsonl').write_text(''.join(json.dumps(r, separators=(',', ':')) + '\n' for r in ranges))
    done('restoration 8/35/60/70s outside main range explicitly labelled; no completion after deadline counted')

    # Synthetic equality tests use exactly representable lengths, so no EPS
    # fudge can accidentally change the 5s or 60s semantics.
    boundary = []
    for t in [5., 60., 60. + 1e-8]:
        plan, scene = endpoint_plan(t)
        r = _run_plan({'id': f'endpoint_{t}', 'failed_vehicle': 'V1' if t == 5 else None}, plan, scene)
        expected = 1 if t == 60 else 0
        require(r['k'] == expected, f'exact boundary {t}')
        boundary.append({'arrival_s': t, 'failed_vehicle': r['scenario']['failed_vehicle'], 'k': r['k'], 't_last_s': r['t_last_s']})
    plan, scene = endpoint_plan(5.)
    r = _run_plan({'id': 'start_at_5_unrelated_failure', 'failed_vehicle': 'V5'}, plan, scene)
    require(r['jobs']['T1']['start_s'] == 5. and not r['jobs']['T1']['locked_at_5s'], 'exact 5 not previously locked')
    boundary.append({'arrival_s': 5., 'failed_vehicle': 'V5', 'k': r['k'], 'locked': r['jobs']['T1']['locked_at_5s']})
    done('synthetic 5s disturbance-before-start; unrelated failure permits 5s start; exact 60 succeeds, 60+1e-8 does not')

    # Deadlock/dependency propagation in a variant fixture: do NOT skip an
    # impossible original job to unlock its later per-vehicle assignment.
    plan = json.loads(SNAPSHOT.read_text())['q22']['jobs']
    scene = json.loads(SCENE.read_text())
    v5_leg = plan[0]['legs'][1]
    v5_leg['path'] = [v5_leg['path'][0], [56.,93.], v5_leg['station']]
    r = _run_plan({'id': 'dependency_fixture', 'failed_vehicle': 'V5'}, plan, scene)
    require(r['jobs']['T1']['start_s'] is None and r['jobs']['T3']['start_s'] is None, 'blocked predecessor propagates')
    require(not any(e['vehicle'] == 'V3' and e['target'] == 'T3' for e in r['events']), 'no dispatch of blocked successor')
    done('synthetic broken T1 dependency blocks V3-to-T3 rather than skipping the original task')

    # Property sample: every contract-valid location produces exactly the same
    # retention schedule and score given its failure identity and outage.
    rng = random.Random(20261120)
    for i in range(100):
        scenario = {'id': f'property_{i}', 'new_alert': [rng.random()*360, 30+rng.random()*20, 50],
                    'failed_vehicle': rng.choice([None, 'V1','V2','V3','V4','V5']), 'recovery_s': rng.uniform(10,30)}
        r = run(scenario)
        check_result(r)
        r2 = run(dict(scenario, new_alert=[0,30,50]))
        require(r['events'] == r2['events'], 'no fake T4 assignment or dependence on new location')
    done('100 fixed-seed contract-valid location/recovery property samples, no assumed sampling weights')

    # Mutation testing proves the checker is not merely accepting the author's
    # output. Each falsified physical or scoring invariant must be rejected.
    mutations = []
    mutant = copy.deepcopy(nominal)
    mutant['k'] -= 1
    mutations.append(('wrong_score', mutant))
    mutant = copy.deepcopy(nominal)
    next(e for e in mutant['events'] if e['kind'] == 'drive' and e['vehicle'] == 'V2')['path'][-1][0] += 1.
    mutations.append(('teleport_or_wrong_speed', mutant))
    mutant = copy.deepcopy(nominal)
    next(e for e in mutant['events'] if e['kind'] == 'observe' and e['vehicle'] == 'V3')['t1'] = 5.
    mutations.append(('premature_healthy_release', mutant))
    mutant = run({'id':'bad_offline_command','recovery_s':20.})
    mutant['commands'][0]['effective_s'] = 6.
    mutations.append(('offline_command', mutant))
    for label, mutant in mutations:
        try:
            audit(mutant)
        except AssertionError:
            require(True, label + ' rejected')
        else:
            require(False, label + ' accepted')
    done('independent checker rejects wrong score, mutated path, premature release and offline command')

    for invalid in [{'failed_vehicle':'V6'}, {'failed_vehicle':2}, {'failed_vehicle':[]},  {'recovery_s':5}, {'recovery_s':float('inf')},
                    {'new_alert':[360,40,50]}, {'new_alert':[0,29,50]}, {'new_alert':[0,40,49]}, {'new_alert':[0,40]},
                    {'new_alert':[True,40,50]}, {'recovery_s':True}]:
        try:
            run(invalid)
        except ValueError:
            require(True, 'invalid input rejected')
        else:
            require(False, 'invalid input accepted')
    for encoded in ['{"id":"a"}', '[{"id":"a"}]', '{"scenarios":[{"id":"a"}]}', '{"id":"a"}\n{"id":"b"}\n']:
        require(bool(read_scenarios(encoded)), 'input container')
    done('input contract validation and single/list/container/JSONL parsing')

    proc = subprocess.run([sys.executable, '-B', str(HERE / 'retention.py'), '-'],
                          input=json.dumps({'id':'cli_roundtrip', 'failed_vehicle':'V2', 'new_alert':[150,50,50], 'recovery_s':20}),
                          capture_output=True, text=True, check=True)
    cli = json.loads(proc.stdout)
    require(cli == run(cli['scenario']), 'CLI JSON roundtrip vs API')
    done('CLI stdin -> JSONL independently matches run(scenario)')

    with (OUT / 'summary.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=['scenario_id','policy','new_alert','failed_vehicle','recovery_s','N','k','eta','t_last_s','T1_start_s','T2_start_s','T3_start_s','T4_start_s'])
        writer.writeheader()
        writer.writerows(summary)
    (OUT / 'nominal.json').write_text(json.dumps(nominal, ensure_ascii=False, indent=2) + '\n')
    (HERE / 'state_5s.json').write_text(json.dumps({'before': nominal['state_5s_before'],
                                                  'after_each_failure': {v:r['state_5s_after'] for v,r in fail_results.items()}}, ensure_ascii=False, indent=2) + '\n')
    evidence = {'passed': True, 'representative_scenarios': len(scenario_set['scenarios']), 'protocols': ['preloaded','current_only'],
                'regression_checks': checks, 'independent_audit_checks': audit_checks, 'random_seed':20261120,
                'random_property_scenarios':100, 'named_tests':named_tests, 'exact_time_tests':boundary,
                'failure_oracle': {v:{'k':r['k'],'N':r['N'],'eta':r['eta'],'t_last_s':r['t_last_s'],
                                     'starts':{t:j['start_s'] for t,j in r['jobs'].items()}} for v,r in fail_results.items()},
                'software': {'python':sys.version.split()[0], 'runtime_dependencies':'standard library only'},
                'source_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in [SNAPSHOT,SCENE,HERE/'retention.py',HERE/'audit.py',HERE/'verify.py',HERE/'scenarios.json']},
                'artifacts_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                                    for p in [OUT/'preloaded.jsonl',OUT/'current_only.jsonl',OUT/'range_sensitivity.jsonl',OUT/'summary.csv']}}
    (HERE / 'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
    (OUT / 'audit.json').write_text(json.dumps({'passed':True, 'total_checks':sum(r['checks'] for r in audit_rows), 'scenarios':audit_rows}, indent=2) + '\n')
    print(json.dumps({key:evidence[key] for key in ['passed','representative_scenarios','regression_checks','independent_audit_checks']}, indent=2))


if __name__ == '__main__':
    main()
