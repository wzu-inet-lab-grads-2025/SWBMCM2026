"""Nominal B-problem scene in metres, seconds and degrees."""

import math
from dataclasses import dataclass


def coordinates(values, size):
    if not isinstance(values, (list, tuple)) or len(values) != size or not all(
        type(v) in (int, float) and math.isfinite(v) for v in values
    ):
        raise ValueError(f'Expected {size} finite coordinates, got {values!r}')
    return tuple(values)


@dataclass(frozen=True)
class Scene:
    homes: dict
    targets: dict
    buildings: dict
    field_side: float
    radius: float
    speed: float
    camera_height: float
    axis_speed: float
    elevation: tuple
    observation_s: float
    centre: tuple = (50., 50.)

    @staticmethod
    def project(centre, polar):
        theta, distance, z = coordinates(polar, 3)
        if distance < 0:
            raise ValueError('Negative horizontal range')
        theta = math.radians(theta)
        return (centre[0] + distance * math.cos(theta), centre[1] + distance * math.sin(theta), z)

    @classmethod
    def from_dict(cls, data):
        centre = coordinates(data['centre_xy_m'], 2)
        targets = {t: cls.project(centre, polar) for t, polar in data['alerts_azimuth_deg_range_m_altitude_m'].items()}
        homes = {v: coordinates(p, 2) for v, p in data['vehicles_xy_m'].items()}
        buildings = {}
        for name, raw in data['buildings_xyxy_height_m'].items():
            w, s, e, n, h = coordinates(raw, 5)
            if not (w < e and s < n and h > 0):
                raise ValueError(f'{name}: invalid building dimensions')
            buildings[name] = ((w, e), (s, n), (0, h))
        side, radius, speed, height, rate, duration = coordinates([
            data['field_side_m'], data['vehicle_radius_m'], data['vehicle_speed_m_s'],
            data['camera_height_m'], data['axis_speed_deg_s'], data['joint_observation_s'],
        ], 6)
        elevation = coordinates(data['elevation_deg'], 2)
        if not homes or not targets or min(radius, speed, rate, duration) <= 0 or side <= 2 * radius or height < 0:
            raise ValueError('Invalid scene dimensions, timing, vehicles or targets')
        if not -90 <= elevation[0] <= elevation[1] <= 90:
            raise ValueError('Invalid elevation range')
        return cls(homes, targets, buildings, side, radius, speed, height, rate, elevation, duration, centre)
