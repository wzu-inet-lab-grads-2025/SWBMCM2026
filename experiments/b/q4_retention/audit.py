#!/usr/bin/env python3
"""Read-only checker: independent of retention.py and its helper functions."""
import argparse
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TOL = 2e-8


def close(a, b):
    return abs(a - b) <= TOL


def vector_close(a, b):
    return len(a) == len(b) and all(close(x, y) for x, y in zip(a, b))


def segment_rectangle_distance(a, b, rect):
    """Exact piecewise-quadratic point-to-rectangle distance along a segment."""
    cuts = [0., 1.]
    for axis in (0, 1):
        delta = b[axis] - a[axis]
        if delta:
            for edge in (rect[axis], rect[axis + 2]):
                u = (edge - a[axis]) / delta
                if 0 < u < 1:
                    cuts.append(u)
    cuts.sort()
    best = math.inf
    for lo, hi in zip(cuts, cuts[1:]):
        mid = (lo + hi) / 2
        aa = bb = 0.
        terms = []
        for axis in (0, 1):
            d = b[axis] - a[axis]
            value = a[axis] + mid * d
            edge = rect[axis] if value < rect[axis] else rect[axis + 2] if value > rect[axis + 2] else None
            intercept, slope = (0., 0.) if edge is None else (a[axis] - edge, d)
            aa += slope * slope
            bb += intercept * slope
            terms.append((intercept, slope))
        candidates = [lo, hi]
        if aa:
            candidates.append(min(hi, max(lo, -bb / aa)))
        for u in candidates:
            best = min(best, sum((c + d * u) ** 2 for c, d in terms))
    return math.sqrt(best)


def ray_hits_box(station, target, box, camera_height):
    lo, hi = 0., 1.
    origin = [*station, camera_height]
    for a, b, lower, upper in zip(origin, target, [box[0], box[1], 0.], [box[2], box[3], box[4]]):
        d = b - a
        if d == 0:
            if not lower <= a <= upper:
                return False
        else:
            u, v = sorted(((lower - a) / d, (upper - a) / d))
            lo, hi = max(lo, u), min(hi, v)
            if lo > hi:
                return False
    return lo <= hi  # Contacts with faces, edges, or vertices are blocked.


def audit(result, scene=None):
    if scene is None:
        scene = json.loads((ROOT / 'experiments/b/scene.json').read_text())
    checks = 0
    def require(condition, message):
        nonlocal checks
        checks += 1
        if not condition:
            raise AssertionError(f"{result['scenario_id']}: {message}")
    scenario, events, jobs = result['scenario'], result['events'], result['jobs']
    snapshot = json.loads((ROOT / 'experiments/b/snapshots/q2_numerical/summary.json').read_text())['q22']['jobs']
    original = {(leg['vehicle'], job['target']): leg for job in snapshot for leg in job['legs']}
    failed = scenario['failed_vehicle']
    commands = result['commands']
    require(set(jobs) == ({'T1', 'T2', 'T3', 'T4'} if scenario['new_alert'] else {'T1', 'T2', 'T3'}), 'target set')
    starts_from_events = {}
    for vehicle, origin in scene['vehicles_xy_m'].items():
        ev = [e for e in events if e['vehicle'] == vehicle]
        positive = [e for e in ev if e['t1'] > e['t0']]
        stop = 5. if vehicle == failed else result['timeline_end_s']
        require(bool(positive), f'{vehicle}: empty timeline')
        require(close(positive[0]['t0'], 0.), f'{vehicle}: starts at zero')
        require(close(positive[-1]['t1'], stop), f'{vehicle}: complete timeline')
        position, gimbal, cursor = origin, [0., 0.], 0.
        for e in ev:
            require(e['t1'] >= e['t0'], 'negative event duration')
            if e['kind'] == 'fail':
                require(vehicle == failed and e['t0'] == e['t1'] == 5., 'failure identity/time')
                require(vector_close(position, e['position_xy_m']), 'failure at actual position')
                require(vector_close(gimbal, e['gimbal_deg']), 'failure at actual pose')
                continue
            require(e['t1'] <= stop + TOL, 'post-failure ghost operation')
            require(close(cursor, e['t0']), 'timeline overlap or gap')
            require(vector_close(position, e['position0_xy_m']), 'teleport at phase transition')
            require(vector_close(gimbal, e['gimbal0_deg']), 'gimbal jump at phase transition')
            cursor = e['t1']
            dt = e['t1'] - e['t0']
            if e['target']:
                matches = [c for c in commands if c['vehicle'] == vehicle and c['target'] == e['target']]
                require(len(matches) == 1, 'one command per original seat')
                require(e['command_effective_s'] <= e['t0'], 'operation before effective command')
                require(e['command_effective_s'] == matches[0]['effective_s'], 'command timestamp copied faithfully')
                require(vector_close(e['station'], matches[0]['station']), 'operation at original station')
                if e['kind'] != 'drive':
                    require(vector_close(position, e['station']), 'stationary operation away from station')
            if e['kind'] == 'drive':
                path = e['path']
                length = sum(math.hypot(a[0] - b[0], a[1] - b[1]) for a, b in zip(path, path[1:]))
                require(close(length, scene['vehicle_speed_m_s'] * dt), 'arc length / speed / time')
                require(vector_close(path[0], position) and vector_close(path[-1], e['position1_xy_m']), 'path endpoints')
                require(vector_close(e['gimbal0_deg'], [0., 0.]) and vector_close(e['gimbal1_deg'], [0., 0.]), 'driving east-horizontal')
                segments = list(zip(path, path[1:])) or [(path[0], path[0])]
                for a, b in segments:
                    r, side = scene['vehicle_radius_m'], scene['field_side_m']
                    require(all(r - TOL <= x <= side - r + TOL for x in [*a, *b]), 'field boundary clearance')
                    for name, box in scene['buildings_xyxy_height_m'].items():
                        require(segment_rectangle_distance(a, b, box) >= r - TOL, f'disk swept into {name}')
            elif e['kind'] in ('slew', 'reset'):
                goal = e['gimbal_goal_deg']
                expect = [a + (1 if b >= a else -1) * min(abs(b - a), scene['axis_speed_deg_s'] * dt)
                          for a, b in zip(gimbal, goal)]
                require(vector_close(expect, e['gimbal1_deg']), 'two-axis motion not bounded-rate shortest rotation')
                require(vector_close(position, e['position1_xy_m']), 'translation during slew/reset')
            else:
                require(vector_close(gimbal, e['gimbal1_deg']), 'pose change during stationary phase')
                require(vector_close(position, e['position1_xy_m']), 'translation during stationary phase')
            if e['kind'] == 'observe':
                starts_from_events.setdefault(e['target'], []).append((vehicle, e['t0']))
                polar = scene['alerts_azimuth_deg_range_m_altitude_m'][e['target']]
                theta, radius, altitude = polar
                cx, cy = scene['centre_xy_m']
                target = [cx + radius * math.cos(math.radians(theta)), cy + radius * math.sin(math.radians(theta)), altitude]
                dx, dy = target[0] - position[0], target[1] - position[1]
                angles = [math.degrees(math.atan2(dy, dx)), math.degrees(math.atan2(altitude - scene['camera_height_m'], math.hypot(dx, dy)))]
                require(vector_close(gimbal, angles), 'observation before full pointing')
                require(scene['elevation_deg'][0] <= angles[1] <= scene['elevation_deg'][1], 'elevation out of bounds')
                for name, box in scene['buildings_xyxy_height_m'].items():
                    require(not ray_hits_box(position, target, box, scene['camera_height_m']), f'view blocked by {name}')
                expected_end = e['t0'] + scene['joint_observation_s']
                if vehicle == failed and e['t0'] < 5.:
                    expected_end = min(expected_end, 5.)
                require(close(e['t1'], expected_end), 'observation occupancy wrong or early healthy release')
            position, gimbal = e['position1_xy_m'], e['gimbal1_deg']
        require(len([e for e in ev if e['kind'] == 'fail']) == int(vehicle == failed), 'failure event count')
        drives = [e for e in ev if e['kind'] == 'drive']
        queue = result['original_queues'][vehicle]
        require([e['target'] for e in drives] == queue[:len(drives)], 'original per-vehicle order/prefix')
        for previous, current in zip(drives, drives[1:]):
            resets = [e for e in ev if e['target'] == previous['target'] and e['kind'] == 'reset']
            require(len(resets) == 1 and close(resets[0]['t1'], resets[0]['planned_end_s']), 'next task before full previous reset')
            require(current['t0'] >= resets[0]['t1'] - TOL, 'overlapping per-vehicle assignments')
    for command in commands:
        leg = original.get((command['vehicle'], command['target']))
        require(leg is not None, 'invented original assignment')
        require(command['path'] == leg['path'] and command['station'] == leg['station'], 'changed original route/station')
        t = command['effective_s']
        require(command['issued_s'] == t, 'command time mismatch')
        recovery = scenario['recovery_s']
        require(recovery is None or not 5 <= t < recovery, 'command issued during outage')
        if result['dispatch_mode'] == 'preloaded':
            require(t == 0., 'preloaded command not at zero')
    for target, job in jobs.items():
        if target != 'T4':
            original_job = next(j for j in snapshot if j['target'] == target)
            require(job['planned_vehicles'] == [leg['vehicle'] for leg in original_job['legs']], 'changed original pair')
        observed = starts_from_events.get(target, [])
        if job['start_s'] is None:
            require(not observed and not job['vehicles'], 'unstarted job with observing vehicles')
        else:
            require(len(observed) == 2 and len({v for v, t in observed}) == 2, 'not exactly two vehicles')
            require(all(close(t, job['start_s']) for v, t in observed), 'not synchronous starts')
            require(set(job['vehicles']) == {v for v, t in observed}, 'vehicle list mismatch')
            require(0 <= job['start_s'] <= 60., 'started outside scoring window')
            require(failed not in job['vehicles'] or job['start_s'] < 5., 'failed participant at/after disturbance')
            p, q = [job['stations'][v] for v in job['vehicles']]
            require(math.dist(p, q) >= 2 * scene['vehicle_radius_m'] - TOL, 'coincident/overlapping observation pair')
            theta, radius, _ = scene['alerts_azimuth_deg_range_m_altitude_m'][target]
            target_xy = [scene['centre_xy_m'][0] + radius * math.cos(math.radians(theta)),
                         scene['centre_xy_m'][1] + radius * math.sin(math.radians(theta))]
            a, b = [target_xy[d] - p[d] for d in (0,1)], [target_xy[d] - q[d] for d in (0,1)]
            require(abs(a[0]*b[1] - a[1]*b[0]) > 1e-8, 'parallel bearing rays cannot intersect stably')
        require(job['locked_at_5s'] == (job['start_s'] is not None and job['start_s'] < 5.), 'lock boundary')
    k = sum(len(rows) == 2 and rows[0][1] <= 60. for rows in starts_from_events.values())
    n = len(jobs)
    last = max((rows[0][1] for rows in starts_from_events.values()), default=0.) if k == n else 60.
    require(result['k'] == k and result['N'] == n, 'k/N from raw events')
    require(close(result['eta'], k / n), 'eta from raw events')
    require(close(result['t_last_s'], last), 't_last from raw events')
    return {'scenario_id': result['scenario_id'], 'policy': result['policy'], 'checks': checks, 'passed': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results_jsonl')
    parser.add_argument('--output')
    args = parser.parse_args()
    data = Path(args.results_jsonl).read_bytes()
    results = [json.loads(line) for line in data.decode().splitlines() if line.strip()]
    rows = [audit(result) for result in results]
    report = {'passed': True, 'results_sha256': hashlib.sha256(data).hexdigest(),
              'total_checks': sum(r['checks'] for r in rows), 'scenarios': rows}
    text = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        Path(args.output).write_text(text)
    else:
        print(text, end='')


if __name__ == '__main__':
    main()
