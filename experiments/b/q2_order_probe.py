"""Paired Q2(2) experiment on two finite station pools and straight feasible legs."""

import csv
from functools import lru_cache
from itertools import combinations, permutations
import json
import math
from pathlib import Path


def intersects(start, end, bounds):
    low, high = 0.0, 1.0
    for a, b, (left, right) in zip(start, end, bounds):
        delta = b - a
        if delta == 0:
            if a < left or a > right:
                return False
        else:
            entry, exit_ = sorted(((left - a) / delta, (right - a) / delta))
            low, high = max(low, entry), min(high, exit_)
            if low > high:
                return False
    return True


def point_segment_distance(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    if dx == dy == 0:
        return math.dist(p, a)
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)
    t = min(1, max(0, t))
    return math.dist(p, (a[0] + t * dx, a[1] + t * dy))


def rectangle_clearance(a, b, rect):
    (w, e), (s, n) = rect
    if intersects(a, b, rect):
        return 0
    endpoint_distances = [math.hypot(max(w - x, 0, x - e), max(s - y, 0, y - n)) for x, y in (a, b)]
    corners = [(w, s), (w, n), (e, s), (e, n)]
    return min(*endpoint_distances, *(point_segment_distance(p, a, b) for p in corners))


class Scene:
    def __init__(self, config):
        self.config = config
        self.homes = {name: tuple(p) for name, p in config['vehicles_xy_m'].items()}
        self.boxes = [((w, e), (s, n), (0, h)) for w, s, e, n, h in config['buildings_xyxy_height_m'].values()]
        ox, oy = config['centre_xy_m']
        self.targets = {name: (ox + r * math.cos(math.radians(a)), oy + r * math.sin(math.radians(a)), h)
                        for name, (a, r, h) in config['alerts_azimuth_deg_range_m_altitude_m'].items()}

    @lru_cache(maxsize=None)
    def direct(self, a, b):
        radius, side = self.config['vehicle_radius_m'], self.config['field_side_m']
        if not all(radius <= v <= side - radius for p in (a, b) for v in p):
            return False
        return all(rectangle_clearance(a, b, box[:2]) >= radius for box in self.boxes)

    @lru_cache(maxsize=None)
    def pointing(self, name, p):
        tx, ty, h = target = self.targets[name]
        camera_h = self.config['camera_height_m']
        dx, dy = tx - p[0], ty - p[1]
        tilt = math.degrees(math.atan2(h - camera_h, math.hypot(dx, dy)))
        lo, hi = self.config['elevation_deg']
        if not lo <= tilt <= hi or any(intersects((*p, camera_h), target, box) for box in self.boxes):
            return None
        bearing = math.degrees(math.atan2(dy, dx))
        return bearing, max(abs(bearing), abs(tilt)) / self.config['axis_speed_deg_s']


def schedule(scene, pool, order, rule):
    current = {name: (p, 0) for name, p in scene.homes.items()}
    events = []
    for target in order:
        legs = {}
        for vehicle, (origin, free) in current.items():
            legs[vehicle] = []
            for p in pool:
                aiming = scene.pointing(target, p)
                if aiming is not None and scene.direct(origin, p):
                    distance = math.dist(origin, p)
                    bearing, turn = aiming
                    ready = free + distance / scene.config['vehicle_speed_m_s'] + turn
                    legs[vehicle].append((p, distance, bearing, turn, ready))
        best_score, best_key, best, ties = math.inf, None, None, 0
        for a, b in combinations(sorted(current), 2):
            for first in legs[a]:
                for second in legs[b]:
                    p, d1, alpha, _, t1 = first
                    q, d2, beta, _, t2 = second
                    if math.dist(p, q) < 2 * scene.config['vehicle_radius_m'] or abs(math.sin(math.radians(alpha - beta))) < 1e-12:
                        continue
                    start = max(t1, t2)
                    if rule.startswith('sum'):
                        score = d1 + d2
                    elif rule.startswith('max'):
                        score = max(d1, d2)
                    else:
                        score = start
                    if score < best_score:
                        best_score, ties, best_key = score, 0, None
                    if score == best_score:
                        ties += 1
                        key = (start if rule.endswith('_time') else 0, a, b, p, q)
                        if best_key is None or key < best_key:
                            best_key, best = key, (a, b, first, second, start)
        if best is None:
            raise ValueError(f'No feasible pair for {target} in order {order}')
        a, b, first, second, start = best
        for vehicle, leg in ((a, first), (b, second)):
            current[vehicle] = (leg[0], start + scene.config['joint_observation_s'] + leg[3])
        events.append((target, start, first[1] + second[1], ties, a, b, first[0], second[0]))
    return events


def main():
    here = Path(__file__).resolve().parent
    scene = Scene(json.loads((here / 'scene.json').read_text(encoding='utf-8')))
    pools = {'homes': sorted(scene.homes.values()), 'grid20': [(x, y) for x in range(10, 100, 20) for y in range(10, 100, 20)]}
    out = here.parents[1] / 'runs/b/q2_order_probe.csv'
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow(['pool', 'order', 'rule', 'target', 'start_s', 'added_m', 'ties', 'vehicle_1', 'vehicle_2', 'station_1', 'station_2'])
        for pool_name, pool in pools.items():
            rules = ('sum', 'max', 'sum_time', 'max_time', 'ready')
            print(pool_name, '/'.join(rules), '→ latest_start_s, total_m')
            for order in permutations(scene.targets):
                latest, totals = [], []
                for rule in rules:
                    events = schedule(scene, pool, order, rule)
                    latest.append(max(e[1] for e in events))
                    totals.append(sum(e[2] for e in events))
                    for event in events:
                        writer.writerow([pool_name, '-'.join(order), rule, *event])
                print('-'.join(order), '/'.join(f'{t:.3f}' for t in latest), '/'.join(f'{d:.3f}' for d in totals))
    print(out)


if __name__ == '__main__':
    main()
