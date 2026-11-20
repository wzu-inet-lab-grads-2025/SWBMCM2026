"""Verify visible homes via roof-height clipping and enumerate zero-travel schedules."""

from itertools import combinations, permutations, product
import json
import math
from pathlib import Path


def blocked_at_home(origin, target, camera_height, building):
    # All target heights and roofs in this scene are above the camera. Only the
    # prefix ending at roof height can be blocked by this particular building.
    x, y = origin
    tx, ty, height = target
    w, s, e, n, roof = building
    fraction = min(1, (roof - camera_height) / (height - camera_height))
    dx, dy = fraction * (tx - x), fraction * (ty - y)
    if max(x, x + dx) < w or min(x, x + dx) > e or max(y, y + dy) < s or min(y, y + dy) > n:
        return False
    # The remaining separating axis is perpendicular to the segment. The
    # rectangle spans the line exactly when its four signed cross products span 0.
    sides = [dx * (cy - y) - dy * (cx - x) for cx in (w, e) for cy in (s, n)]
    return min(sides) <= 0 <= max(sides)


def main():
    scene = json.loads(Path(__file__).with_name('scene.json').read_text(encoding='utf-8'))
    visible = {}
    pan, turn = {}, {}
    ox, oy = scene['centre_xy_m']
    for target, (azimuth, r, height) in scene['alerts_azimuth_deg_range_m_altitude_m'].items():
        visible[target] = []
        x = ox + r * math.cos(math.radians(azimuth))
        y = oy + r * math.sin(math.radians(azimuth))
        for vehicle, (vx, vy) in scene['vehicles_xy_m'].items():
            tilt = math.degrees(math.atan2(height - scene['camera_height_m'], math.hypot(x - vx, y - vy)))
            low, high = scene['elevation_deg']
            if not low <= tilt <= high or any(blocked_at_home((vx, vy), (x, y, height), scene['camera_height_m'], building)
                                             for building in scene['buildings_xyxy_height_m'].values()):
                continue
            visible[target].append(vehicle)
            pan[target, vehicle] = math.degrees(math.atan2(y - vy, x - vx))
            turn[target, vehicle] = max(abs(pan[target, vehicle]), abs(tilt)) / scene['axis_speed_deg_s']
        print(f'{target} visible from {", ".join(visible[target])}')

    best = math.inf
    count = 0
    solution = []
    best_by_margin = {}
    for order in permutations(visible):
        for choices in product(*(combinations(visible[target], 2) for target in order)):
            count += 1
            free = dict.fromkeys(scene['vehicles_xy_m'], 0)
            events = []
            for target, (a, b) in zip(order, choices):
                start = max(free[v] + turn[target, v] for v in (a, b))
                for v in (a, b):
                    free[v] = start + scene['joint_observation_s'] + turn[target, v]
                gamma = abs((pan[target, a] - pan[target, b] + 180) % 360 - 180)
                assert 0 < gamma < 180
                events.append((start, target, a, b, gamma))
            latest = max(event[0] for event in events)
            margin = min(min(event[4], 180 - event[4]) for event in events)
            best_by_margin[margin] = min(latest, best_by_margin.get(margin, math.inf))
            if latest < best:
                best, solution = latest, events
    print(f'{count} stationary plans; minimum latest start = {best:.9f} s')
    for start, target, a, b, gamma in sorted(solution):
        print(f'{target}: {a} and {b}, start {start:.9f} s, intersection {gamma:.3f} deg')
    free = dict.fromkeys(scene['vehicles_xy_m'], 0.0)
    timeline = []
    for start, target, a, b, _ in sorted(solution):
        end = start + scene['joint_observation_s']
        for vehicle in (a, b):
            slew = turn[target, vehicle]
            timeline.append((vehicle, target, free[vehicle], free[vehicle] + slew, start, end, end + slew))
            free[vehicle] = end + slew
    print('vehicle alert departure=arrival  ready  start  end  reset_complete (s)')
    for vehicle, target, *times in sorted(timeline, key=lambda row: (row[0], row[2])):
        print(vehicle, target, *(f'{t:.6f}' for t in times))
    print('Undominated pairs within stationary plans: worst angular margin (deg), latest start (s)')
    fastest = math.inf
    for margin, latest in sorted(best_by_margin.items(), reverse=True):
        if latest < fastest:
            print(f'{margin:.3f}, {latest:.6f}')
            fastest = latest


if __name__ == '__main__':
    main()
