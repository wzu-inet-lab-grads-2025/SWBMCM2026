"""Closed sightline intersections and swept-disk route checks from the Q2 experiments."""

import math


class InfeasibleGeometry(ValueError):
    """A proposed route or sightline violates the scene constraints."""


def intersects_box(a, b, bounds):
    first, last = 0., 1.
    for start, end, (lo, hi) in zip(a, b, bounds):
        if end == start:
            if not lo <= start <= hi:
                return False
        else:
            near, far = sorted(((lo - start) / (end - start), (hi - start) / (end - start)))
            first, last = max(first, near), min(last, far)
            if first > last:
                return False
    return True


def rectangle_clearance(a, b, rect):
    if intersects_box(a, b, rect):
        return 0.
    (w, e), (s, n) = rect
    gap = min(math.hypot(max(w-x, 0, x-e), max(s-y, 0, y-n)) for x, y in (a, b))
    dx, dy = b[0] - a[0], b[1] - a[1]
    if dx or dy:
        for x in (w, e):
            for y in (s, n):
                t = ((x-a[0])*dx + (y-a[1])*dy) / (dx*dx + dy*dy)
                if 0 < t < 1:
                    gap = min(gap, math.hypot(x-a[0]-t*dx, y-a[1]-t*dy))
    return gap


def check_route(scene, path):
    for point in path:
        if not all(scene.radius <= x <= scene.field_side - scene.radius for x in point):
            raise InfeasibleGeometry(f'Route leaves field at {point}')
    for a, b in zip(path, path[1:]):
        for name, box in scene.buildings.items():
            if rectangle_clearance(a, b, box[:2]) < scene.radius - 1e-9:
                raise InfeasibleGeometry(f'Route {a} to {b} enters {name}')


def pointing(scene, target, station):
    tx, ty, z = scene.targets[target]
    dx, dy = tx - station[0], ty - station[1]
    if dx == dy == 0:
        raise InfeasibleGeometry(f'{target}: undefined horizontal bearing')
    tilt = math.degrees(math.atan2(z - scene.camera_height, math.hypot(dx, dy)))
    if not scene.elevation[0] - 1e-10 <= tilt <= scene.elevation[1] + 1e-10:
        raise InfeasibleGeometry(f'{target}: elevation {tilt} outside bounds at {station}')
    for name, box in scene.buildings.items():
        if intersects_box((*station, scene.camera_height), (tx, ty, z), box):
            raise InfeasibleGeometry(f'{target}: line of sight at {station} touches {name}')
    return math.degrees(math.atan2(dy, dx)), tilt


def travelled_path(path, distance):
    """Traversed polyline at arc length distance, retaining all passed corners."""
    prefix = [path[0]]
    for a, b in zip(path, path[1:]):
        length = math.dist(a, b)
        if distance < length and length > 0:
            if distance > 0:
                prefix.append(tuple(x + (y-x) * distance / length for x, y in zip(a, b)))
            return prefix
        if length:
            prefix.append(b)
        distance -= length
    return prefix


def compatible_pair(scene, station_a, station_b, bearing_a, bearing_b, min_sine=1e-12):
    return (math.dist(station_a, station_b) >= 2 * scene.radius - 1e-9
            and abs(math.sin(math.radians(bearing_a - bearing_b))) > min_sine)
