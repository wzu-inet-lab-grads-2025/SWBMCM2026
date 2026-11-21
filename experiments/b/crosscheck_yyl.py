"""Replay the peer Q2 routes and Q4 main panel using this branch's geometry and clock."""

import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.b.q4_audit.audit import Context, audit_pair
from src.q4.replay import ScenarioReplay, validate_manifest
from src.q4.reschedule import BeamPlanner
from src.shared.geometry import check_route, pointing
from src.shared.plan import Plan
from src.shared.scene import Scene
from src.shared.timeline import joint_observations, replay

PEER = '38bb3c7a899ab8b5d0bc7d66eb8f4724afa4129b'


def peer_file(question, name):
    paths = subprocess.check_output(['git', 'ls-tree', '-rz', '--name-only', PEER, f'results/{question}'], cwd=ROOT)
    path, = [p for p in paths.decode().split('\0') if p.endswith('/' + name)]
    return subprocess.check_output(['git', 'show', f'{PEER}:{path}'], cwd=ROOT).decode()


def close(actual, expected, tol=1e-8):
    if not math.isclose(actual, expected, rel_tol=0, abs_tol=tol):
        raise ValueError(f'{actual} differs from {expected}')


def peer_plan(scene, scene_hash):
    q21 = json.loads(peer_file('q2', 'q21_result.json'))
    ranges, bearings = [], []
    for leg in q21['routes']:
        route, station = leg['waypoints'], leg['site_xy_m']
        if tuple(route[0]) != scene.homes[leg['vehicle']] or route[-1] != station:
            raise ValueError('Q2(1) disconnected route')
        check_route(scene, route)
        travel = sum(math.dist(a, b) for a, b in zip(route, route[1:])) / scene.speed
        close(travel, leg['travel_s'])
        if travel > 30:
            raise ValueError('Q2(1) exceeds travel limit')
        yaw, tilt = pointing(scene, 'T2', station)
        close(tilt, 80)
        bearings.append(yaw)
        ranges.append(math.dist(station, scene.targets['T2'][:2]))
    precision = math.radians(1) * math.hypot(*ranges) / abs(math.sin(math.radians(bearings[0] - bearings[1])))
    close(precision, q21['rmse_m'])

    routes = json.loads(peer_file('q2', 'q22_routes.json'))
    times = list(csv.DictReader(io.StringIO(peer_file('q2', 'q22_timeline.csv'))))
    plans = {}
    for label in ('optimized', 'greedy'):
        jobs = []
        for target in scene.targets:
            legs = [dict(vehicle=r['vehicle'], station=r['destination'], path=r['waypoints'])
                    for r in routes if r['plan'] == label and r['target'] == target]
            jobs.append(dict(target=target, legs=legs))
        method = f'yyl-{label}'
        plan = Plan.load(dict(schema='b.plan.v1', method=method, scene_sha256=scene_hash, jobs=jobs), method, scene_hash)
        events = replay(scene, plan)
        starts = joint_observations(events)
        for r in times:
            if r['plan'] == label:
                close(starts[r['target']][0], float(r['observation_start_s']))
        print(f'{method}:', {t: round(s, 9) for t, (s, _) in starts.items()})
        plans[label] = plan
    plan = plans['optimized']
    # Supply the independent auditor with the peer's archived times, not our replay times.
    by_leg = {(r['vehicle'], r['target']): r for r in times if r['plan'] == 'optimized'}
    audit_jobs = []
    for job in plan.jobs:
        legs = []
        for leg in job.legs:
            r = by_leg[leg.vehicle, job.target]
            legs.append(dict(vehicle=leg.vehicle, station=leg.station, path=leg.path,
                             depart_s=float(r['depart_s']), arrival_s=float(r['arrive_s']),
                             ready_s=float(r['turn_end_s']), observation_end_s=float(r['observation_end_s']),
                             reset_end_s=float(r['reset_end_s'])))
        start = float(by_leg[job.legs[0].vehicle, job.target]['observation_start_s'])
        audit_jobs.append(dict(target=job.target, start_s=start, legs=legs))
    return plan, audit_jobs


def key(alert, failure, recovery):
    return tuple(alert) if alert else None, failure, recovery


def compare(output):
    raw = (ROOT / 'experiments/b/scene.json').read_bytes()
    data = json.loads(raw)
    scene = Scene.from_dict(data)
    plan, audit_jobs = peer_plan(scene, hashlib.sha256(raw).hexdigest())
    context = Context(data, audit_jobs)
    rules = data['q4']
    engine = ScenarioReplay(scene, plan, rules)
    planner = BeamPlanner(scene, plan, json.loads((ROOT / 'configs/b/q4-reschedule.json').read_text()))
    manifest = json.loads((ROOT / 'experiments/b/q4_scenarios/manifest.json').read_text())['scenarios']
    with (ROOT / 'experiments/b/snapshots/q4_comparison/main.csv').open() as f:
        local_scores = {r['scenario_id']: r for r in csv.DictReader(f)}
    local = {key(r['new_alert'], r['failed_vehicle'], r['recovery_s']): local_scores[r['id']] for r in manifest}
    cases, policies = {}, {}
    for r in csv.DictReader(io.StringIO(peer_file('q4', 'scenario_metrics.csv'))):
        if (r['sample'], r['variant']) != ('main', 'main') or r['model'] not in ('strict_frozen', 'original_append', 'recommended'):
            continue
        alert = [float(r[k]) for k in ('theta_deg', 'R_m', 'H_m')] if r['theta_deg'] else None
        case = dict(id=r['scenario_id'], new_alert=alert, failed_vehicle=r['failed_vehicle'] or None,
                    recovery_s=float(r['t_rec_s']) if r['t_rec_s'] else None)
        case_key = key(alert, case['failed_vehicle'], case['recovery_s'])
        if cases.setdefault(case_key, case) != case or (case_key, r['model']) in policies or r['valid'] != 'True':
            raise ValueError(f'Duplicate or invalid peer record {case}')
        policies[case_key, r['model']] = r
    validate_manifest({'scenarios': list(cases.values())}, scene, rules)
    if len(cases) != 1751 or (local.keys() - cases.keys()) != {(None, None, None)}:
        raise ValueError('Unexpected scenario coverage')

    rows = []
    for case_key, case in cases.items():
        frozen, revised = engine.run(case), engine.run(case, planner)
        # The audit accepts the JSON event-log schema (arrays, including stationary routes).
        audit = audit_pair(json.loads(json.dumps(frozen)), json.loads(json.dumps(revised)), case, context=context)
        if audit['status'] != 'pass':
            raise ValueError(f"{case['id']}: {[(audit[p]['errors'], audit[p]['missing_evidence']) for p in ('left', 'right')]}")
        row = dict(peer_id=case['id'], local_id='', N=revised['N'])
        for model in ('strict_frozen', 'original_append', 'recommended'):
            record = policies[case_key, model]
            close(row['N'], int(record['N']))
            row[f'{model}_k'] = int(record['k'])
            row[f'{model}_s'] = float(record['t_last_s'])
        close(frozen['k'], row['strict_frozen_k'])
        close(frozen['t_last_s'], row['strict_frozen_s'])
        row.update(local_on_peer_k=revised['k'], local_on_peer_s=revised['t_last_s'],
                   local_original_k='', local_original_s='')
        if case_key in local:
            orig = local[case_key]
            row.update(local_id=orig['scenario_id'], local_original_k=int(orig['revised_k']),
                       local_original_s=float(orig['revised_t_last_s']))
        rows.append(row)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    print(f'{len(rows)} peer cases, {sum(bool(r["local_id"]) for r in rows)} common cases → {output}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'runs/b/crosscheck_yyl/q4.csv')
    compare(parser.parse_args().output)
