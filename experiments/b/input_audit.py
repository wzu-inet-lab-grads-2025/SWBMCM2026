"""Audit the printed scene before station selection. Run with Python 3, stdlib only."""

import hashlib
import json
import math
from pathlib import Path


def main():
    here = Path(__file__).resolve().parent
    scene = json.loads((here / "scene.json").read_text(encoding="utf-8"))
    source = here.parents[1] / scene["source"]
    if hashlib.sha256(source.read_bytes()).hexdigest() != scene["sha256"]:
        raise SystemExit("PDF version changed. Check the transcription against the new source.")

    centre_x, centre_y = scene["centre_xy_m"]
    buildings = scene["buildings_xyxy_height_m"]
    vehicles = scene["vehicles_xy_m"]
    camera_z = scene["camera_height_m"]
    low, high = scene["elevation_deg"]
    side = scene["field_side_m"]
    radius = scene["vehicle_radius_m"]
    print("vehicle  clearance to nearest field/building boundary after disk radius (m)")
    for vehicle, (x, y) in vehicles.items():
        clearances = [x, y, side - x, side - y]
        for west, south, east, north, _ in buildings.values():
            clearances.append(math.hypot(max(west - x, 0, x - east), max(south - y, 0, y - north)))
        print(f"{vehicle}: {min(clearances) - radius:.3f}")

    print("alert  x(m)  y(m)  footprint  elevation at each initial vehicle (deg)")
    targets = {}
    for alert, (azimuth, distance, z) in scene["alerts_azimuth_deg_range_m_altitude_m"].items():
        angle = math.radians(azimuth)
        x = centre_x + distance * math.cos(angle)
        y = centre_y + distance * math.sin(angle)
        targets[alert] = (x, y, z)
        footprint = [name for name, (w, s, e, n, _) in buildings.items() if w <= x <= e and s <= y <= n]
        readings = []
        for vehicle, (vx, vy) in vehicles.items():
            elevation = math.degrees(math.atan2(z - camera_z, math.hypot(x - vx, y - vy)))
            gate = "" if low <= elevation <= high else " (outside)"
            readings.append(f"{vehicle}={elevation:.3f}{gate}")
        print(f"{alert}  {x:.6f}  {y:.6f}  {','.join(footprint) or '-'}  {'  '.join(readings)}")

    # Specific north-face crossing for a later line-of-sight checker to reproduce.
    vx, vy = vehicles["V3"]
    tx, ty, tz = targets["T2"]
    west, _, east, north, roof = buildings["B3"]
    fraction = (north - vy) / (ty - vy)
    entry_x = vx + fraction * (tx - vx)
    if not (0 <= fraction <= 1 and west <= entry_x <= east):
        raise ValueError("V3–T2 no longer crosses the B3 north face projection")
    entry_z = camera_z + fraction * (tz - camera_z)
    print(f"V3–T2 at y={north}: x={entry_x:.3f}, z={entry_z:.3f}, B3 roof={roof} m")


if __name__ == "__main__":
    main()
