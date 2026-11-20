"""Reconstruct route time from the exported station choices, independent of the planner."""

from ast import literal_eval
import csv
from itertools import groupby
import json
import math
from pathlib import Path


def main():
    here = Path(__file__).resolve().parent
    config = json.loads((here / 'scene.json').read_text(encoding='utf-8'))
    csv_file = here.parents[1] / 'runs/b/q2_order_probe.csv'
    centre = complex(*config['centre_xy_m'])
    targets = {}
    for name, (azimuth, radius, height) in config['alerts_azimuth_deg_range_m_altitude_m'].items():
        angle = math.radians(azimuth)
        location = centre + radius * complex(math.cos(angle), math.sin(angle))
        targets[name] = (location, height - config['camera_height_m'])

    def group(row):
        return row['pool'], row['order'], row['rule']

    checked = {}
    with csv_file.open(encoding='utf-8') as stream:
        for key, rows in groupby(csv.DictReader(stream), key=group):
            assert key not in checked, key
            last_position = {name: complex(*p) for name, p in config['vehicles_xy_m'].items()}
            free = dict.fromkeys(last_position, 0.0)
            starts = {}
            for row in rows:
                name = row['target']
                destination, dz = targets[name]
                turns = {}
                ready = []
                distances = []
                for side in (1, 2):
                    vehicle = row[f'vehicle_{side}']
                    station = complex(*literal_eval(row[f'station_{side}']))
                    walk = abs(station - last_position[vehicle])
                    sight = destination - station
                    yaw = math.atan2(sight.imag, sight.real)
                    elevation = math.atan2(dz, abs(sight))
                    turns[vehicle] = math.degrees(max(abs(yaw), abs(elevation))) / config['axis_speed_deg_s']
                    ready.append(free[vehicle] + walk / config['vehicle_speed_m_s'] + turns[vehicle])
                    last_position[vehicle] = station
                    distances.append(walk)
                assert len(turns) == 2 and name not in starts, (key, name)
                starts[name] = max(ready)
                assert math.isclose(starts[name], float(row['start_s']), abs_tol=1e-9, rel_tol=0), (key, name)
                assert math.isclose(sum(distances), float(row['added_m']), abs_tol=1e-9, rel_tol=0), (key, name)
                for vehicle, turn in turns.items():
                    free[vehicle] = starts[name] + config['joint_observation_s'] + turn
            assert list(starts) == key[1].split('-') and set(starts) == set(targets), key
            checked[key] = starts
    assert len(checked) == 60, 'Expected two pools, six orders and five rules'
    print(f'{len(checked)} schedules and {sum(map(len, checked.values()))} starts verified from {csv_file.name}.')


if __name__ == '__main__':
    main()
