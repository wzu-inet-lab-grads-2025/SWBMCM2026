"""Replay both parts of a Q2 submission from its stations, polylines and event times."""

import argparse
import json
import math
from pathlib import Path

from q2_order_probe import Scene, intersects, rectangle_clearance


GEOM_EPS = 1e-9   # metres, inclusive vehicle boundaries only
TIME_EPS = 1e-6   # seconds, for exported full-precision event times


def require(condition, message):
    if not condition:
        raise ValueError(message)


def close(actual, expected, label):
    require(math.isclose(actual, expected, rel_tol=0, abs_tol=TIME_EPS),
            f'{label}: reported {actual:.12g}, replay {expected:.12g}')


def point(raw):
    require(len(raw) == 2 and all(math.isfinite(x) for x in raw), f'invalid 2D point {raw}')
    return tuple(raw)


def drive(scene, origin, station, raw_path):
    path = [point(p) for p in raw_path]
    require(path and math.dist(path[0], origin) <= GEOM_EPS and math.dist(path[-1], station) <= GEOM_EPS,
            f'route does not join {origin} to {station}')
    # Include the exact stored endpoints so stationary legs also have a geometry check.
    path = [origin, *path, station]
    radius, side = scene.config['vehicle_radius_m'], scene.config['field_side_m']
    require(all(radius - GEOM_EPS <= v <= side - radius + GEOM_EPS for p in path for v in p),
            f'vehicle disk crosses field boundary on route to {station}')
    for a, b in zip(path, path[1:]):
        for index, box in enumerate(scene.boxes, 1):
            require(rectangle_clearance(a, b, box[:2]) >= radius - GEOM_EPS,
                    f'vehicle disk enters B{index} along {a} to {b}')
    return sum(map(math.dist, path, path[1:]))


def sight(scene, target, station):
    tx, ty, height = scene.targets[target]
    camera = (*station, scene.config['camera_height_m'])
    for index, box in enumerate(scene.boxes, 1):
        require(not intersects(camera, scene.targets[target], box), f'{station} to {target} touches B{index}')
    dx, dy = tx - station[0], ty - station[1]
    r = math.hypot(dx, dy)
    elevation = math.degrees(math.atan2(height - camera[2], r))
    lo, hi = scene.config['elevation_deg']
    require(lo - 1e-10 <= elevation <= hi + 1e-10, f'{target}: elevation {elevation}')
    bearing = math.atan2(dy, dx)
    slew = max(abs(math.degrees(bearing)), abs(elevation)) / scene.config['axis_speed_deg_s']
    return r, bearing, slew


def intersection(p, q, a, b):
    ux, uy = math.cos(a), math.sin(a)
    vx, vy = math.cos(b), math.sin(b)
    offset = ((q[0] - p[0]) * vy - (q[1] - p[1]) * vx) / (ux * vy - uy * vx)
    return p[0] + offset * ux, p[1] + offset * uy


def geometry(scene, target, legs):
    require(len(legs) == 2 and len({leg['vehicle'] for leg in legs}) == 2, f'{target} needs two vehicles')
    p, q = [point(leg['station']) for leg in legs]
    baseline = math.dist(p, q)
    require(baseline >= 2 * scene.config['vehicle_radius_m'] - GEOM_EPS, f'{target}: overlapping stations')
    r1, a1, _ = sight(scene, target, p)
    r2, a2, _ = sight(scene, target, q)
    cross = abs(math.sin(a2 - a1))
    require(cross > 1e-12, f'{target}: degenerate bearing intersection')
    angle = math.degrees(math.atan2(cross, math.cos(a2 - a1)))
    sigma = math.radians(scene.config['q2_1']['bearing_sd_deg'])
    # Inverting the two independent bearing derivatives gives the two column
    # norms sigma*r_i/|sin(gamma)| of the horizontal position error map.
    rmse = sigma * math.hypot(r1, r2) / cross
    result = {'baseline_m': baseline, 'ranges_m': [r1, r2], 'angle_deg': angle, 'linear_rmse_m': rmse}
    # Independently check the local accuracy formula by perturbing each bearing
    # and intersecting the two lines. Near parallelism makes finite differences unstable.
    if cross > 1e-3:
        h = 1e-6
        derivatives = [math.dist(intersection(p, q, a1 + da, a2 + db), intersection(p, q, a1 - da, a2 - db)) / (2 * h)
                       for da, db in [(h, 0), (0, h)]]
        numerical = sigma * math.hypot(*derivatives)
        require(math.isclose(numerical, rmse, rel_tol=3e-6), f'{target}: inconsistent bearing derivatives')
        result['finite_difference_rmse_m'] = numerical
    return result


def replay(scene, document):
    speed = scene.config['vehicle_speed_m_s']
    legs = document['q21']['legs']
    q21 = geometry(scene, 'T2', legs)
    trips = {}
    for leg in legs:
        name = leg['vehicle']
        distance = drive(scene, scene.homes[name], point(leg['station']), leg['path'])
        travel = distance / speed
        close(leg['travel_s'], travel, f'T2 {name} drive')
        require(travel <= scene.config['q2_1']['travel_limit_s'] + TIME_EPS, f'{name}: travel time {travel}')
        trips[name] = travel
    q21['travel_s'] = trips

    jobs = sorted(document['q22']['jobs'], key=lambda job: job['start_s'])
    require(sorted(j['target'] for j in jobs) == sorted(scene.targets), 'expected T1, T2 and T3 once each')
    available = dict.fromkeys(scene.homes, 0.0)
    last = dict(scene.homes)
    used = set()
    starts, travel_sum = {}, 0.0
    timeline = {name: [] for name in scene.homes}
    for job in jobs:
        target, legs = job['target'], job['legs']
        geometry(scene, target, legs)
        ready, turns = [], []
        for leg in legs:
            name, station, depart = leg['vehicle'], point(leg['station']), leg['depart_s']
            require(math.isfinite(depart) and depart >= -TIME_EPS, f'invalid departure {depart}')
            if name in used:
                close(depart, available[name], f'{target} {name} departure after reset')
            distance = drive(scene, last[name], station, leg['path'])
            travel_sum += distance
            arrival = depart + distance / speed
            close(leg['arrival_s'], arrival, f'{target} {name} arrival')
            _, _, turn = sight(scene, target, station)
            close(leg['ready_s'], arrival + turn, f'{target} {name} pointing complete')
            ready.append(arrival + turn)
            turns.append(turn)
            timeline[name].append({'target': target, 'station': station, 'depart_s': depart,
                                   'drive_m': distance, 'arrival_s': arrival, 'ready_s': ready[-1]})
            last[name] = station
            used.add(name)
        start = max(ready)
        close(job['start_s'], start, f'{target} joint start')
        starts[target] = start
        for leg, turn in zip(legs, turns):
            name = leg['vehicle']
            end = start + scene.config['joint_observation_s']
            available[name] = end + turn
            close(leg['reset_end_s'], available[name], f'{target} {name} reset')
            row = timeline[name][-1]
            row.update(wait_s=start - row['ready_s'], start_s=start, end_s=end, reset_end_s=available[name])
    return {'q21': q21, 'q22': {'start_s': starts, 'latest_s': max(starts.values()), 'total_m': travel_sum,
                                'timeline': timeline}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('summary', type=Path, nargs='+')
    parser.add_argument('--output', type=Path, help='Save both verified timelines and the recomputed metrics as JSON')
    args = parser.parse_args()
    scene = Scene(json.loads(Path(__file__).with_name('scene.json').read_text()))
    reports = [{'input': str(path), **replay(scene, json.loads(path.read_text()))} for path in args.summary]
    text = json.dumps(reports, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + '\n', encoding='utf-8')
    else:
        print(text)


if __name__ == '__main__':
    main()
