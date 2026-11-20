"""Nominal geometry and the finite, vehicle-specific station domain (metres/degrees)."""
from functools import lru_cache
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCENE_PATH = ROOT / 'experiments/b/scene.json'
SNAPSHOT = ROOT / 'experiments/b/snapshots/q2_numerical/summary.json'
SCENE = json.loads(SCENE_PATH.read_text())
PLAN = json.loads(SNAPSHOT.read_text())['q22']['jobs']
HOME = {v: tuple(xy) for v, xy in SCENE['vehicles_xy_m'].items()}
BOX = tuple(tuple(b) for b in SCENE['buildings_xyxy_height_m'].values())
RADIUS = SCENE['vehicle_radius_m']
SPEED = SCENE['vehicle_speed_m_s']
RATE = SCENE['axis_speed_deg_s']
DURATION = SCENE['joint_observation_s']
ZERO = (0., 0.)
# Convex, disjoint work areas. All nominal legs are contained in these areas.
AREAS = {'V1': (2, 2, 32, 32), 'V2': (66, 1.5, 98, 20), 'V3': (2, 77.6, 30, 98),
         'V4': (81, 48, 98, 85), 'V5': (36, 80, 67, 98)}


def project(polar):
    theta, r, z = polar
    cx, cy = SCENE['centre_xy_m']
    a = math.radians(theta)
    return cx + r * math.cos(a), cy + r * math.sin(a), z


TARGETS = {t: project(p) for t, p in SCENE['alerts_azimuth_deg_range_m_altitude_m'].items()}


def slab(a, b, lo, hi):
    enter, leave = 0., 1.
    for x, y, l, h in zip(a, b, lo, hi):
        d = y - x
        if d == 0:
            if not l <= x <= h:
                return False
        else:
            u, w = sorted(((l - x) / d, (h - x) / d))
            enter, leave = max(enter, u), min(leave, w)
            if enter > leave:
                return False
    return True


def point_box(p, b):
    return math.hypot(max(b[0] - p[0], 0, p[0] - b[2]), max(b[1] - p[1], 0, p[1] - b[3]))


def point_segment(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    if dx == dy == 0:
        return math.dist(a, p)
    u = max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)))
    return math.hypot(a[0] + u * dx - p[0], a[1] + u * dy - p[1])


@lru_cache(maxsize=16384)
def road(a, b):
    side = SCENE['field_side_m']
    if not all(RADIUS - 1e-10 <= x <= side - RADIUS + 1e-10 for p in (a, b) for x in p):
        return False
    for box in BOX:
        if slab(a, b, box[:2], box[2:4]):
            return False
        corners = ((x, y) for x in (box[0], box[2]) for y in (box[1], box[3]))
        if min(point_box(a, box), point_box(b, box), *(point_segment(p, a, b) for p in corners)) < RADIUS - 1e-10:
            return False
    return True


@lru_cache(maxsize=16384)
def aim(p, t):
    dx, dy = t[0] - p[0], t[1] - p[1]
    return math.degrees(math.atan2(dy, dx)), math.degrees(math.atan2(t[2] - SCENE['camera_height_m'], math.hypot(dx, dy)))


@lru_cache(maxsize=16384)
def visible(p, t):
    lo, hi = SCENE['elevation_deg']
    origin = (*p, SCENE['camera_height_m'])
    return (road(p, p) and lo <= aim(p, t)[1] <= hi and not any(
        slab(origin, t, (x, y, 0), (X, Y, h)) for x, y, X, Y, h in BOX))


def turn(a, b=ZERO):
    return max(abs(x - y) for x, y in zip(a, b)) / RATE


def axes(a, b, dt):
    return tuple(x + math.copysign(min(abs(y - x), max(dt, 0) * RATE), y - x) for x, y in zip(a, b))


@lru_cache(maxsize=32768)
def pair_ok(p, q, t):
    ax, ay = t[0] - p[0], t[1] - p[1]
    bx, by = t[0] - q[0], t[1] - q[1]
    scale = math.hypot(ax, ay) * math.hypot(bx, by)
    return math.dist(p, q) >= 2 * RADIUS and abs(ax * by - ay * bx) > 1e-8 * scale


def in_area(v, p):
    x, y, X, Y = AREAS[v]
    return x - 1e-9 <= p[0] <= X + 1e-9 and y - 1e-9 <= p[1] <= Y + 1e-9


def stations(v, current):
    x, y, X, Y = AREAS[v]
    points = {HOME[v], current}
    points.update(tuple(leg['station']) for j in PLAN for leg in j['legs'] if leg['vehicle'] == v)
    points.update((x + i * (X - x) / 3, y + j * (Y - y) / 3) for i in range(4) for j in range(4))
    return tuple(sorted(p for p in points if in_area(v, p) and road(p, p)))
