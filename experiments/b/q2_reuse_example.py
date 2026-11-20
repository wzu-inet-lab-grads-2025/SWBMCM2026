"""Compare two choices for one alert using a synthetic vehicle availability snapshot."""

from itertools import permutations
import json
import math
from pathlib import Path


def main():
    scene = json.loads(Path(__file__).with_name("scene.json").read_text(encoding="utf-8"))
    heading, radius, height = scene["alerts_azimuth_deg_range_m_altitude_m"]["T3"]
    ox, oy = scene["centre_xy_m"]
    tx = ox + radius * math.cos(math.radians(heading))
    ty = oy + radius * math.sin(math.radians(heading))
    speed = scene["vehicle_speed_m_s"]
    stations = [(45, 14), (60, 3)]
    # Each tuple gives the position after reset and the next possible departure time.
    vehicles = {"A": ((42, 14), 20), "B": ((60, 6), 34), "C": ((72, 3), 20)}
    turns = []
    for x, y in stations:
        pan = math.degrees(math.atan2(ty - y, tx - x))
        tilt = math.degrees(math.atan2(height - scene["camera_height_m"], math.hypot(tx - x, ty - y)))
        turns.append(max(abs(pan), abs(tilt)) / scene["axis_speed_deg_s"])

    print("S1/S2 | added distance (m) | arrivals | ready | joint start | joint end | free after reset (s)")
    for names in permutations(vehicles, 2):
        distances = []
        arrivals = []
        for name, station in zip(names, stations):
            position, free_time = vehicles[name]
            distance = math.dist(position, station)
            distances.append(distance)
            arrivals.append(free_time + distance / speed)
        ready = [arrival + turn for arrival, turn in zip(arrivals, turns)]
        start = max(ready)
        end = start + scene["joint_observation_s"]
        released = [end + turn for turn in turns]
        print(f"{'/'.join(names)} | {sum(distances):.3f} | {arrivals[0]:.3f}, {arrivals[1]:.3f} | "
              f"{ready[0]:.3f}, {ready[1]:.3f} | {start:.3f} | {end:.3f} | {released[0]:.3f}, {released[1]:.3f}")


if __name__ == "__main__":
    main()
