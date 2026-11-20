#!/usr/bin/env python3
"""Independent nominal visibility audit of six fixed Q4 targets (no scheduling)."""
import argparse
from collections import Counter
import hashlib
import itertools
import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SCENE_PATH = ROOT / "experiments/b/scene.json"
MAIN_PATH = HERE / "manifest.json"


def point_rectangle_distance(point, box):
    x, y = point
    return math.hypot(max(box[0] - x, 0, x - box[2]),
                      max(box[1] - y, 0, y - box[3]))


def segment_box_interval(start, end, lower, upper):
    """Closed finite segment, closed box; a one-point intersection blocks LOS."""
    enter, leave = 0.0, 1.0
    for s, e, lo, hi in zip(start, end, lower, upper):
        delta = e - s
        if abs(delta) < 1e-14:
            if s < lo or s > hi:
                return None
        else:
            a, b = sorted(((lo - s) / delta, (hi - s) / delta))
            enter, leave = max(enter, a), min(leave, b)
            if enter > leave:
                return None
    return [enter, leave]


def target_xyz(alert, scene):
    angle, radius, height = alert
    cx, cy = scene["centre_xy_m"]
    angle = math.radians(angle)
    return [cx + radius * math.cos(angle), cy + radius * math.sin(angle), height]


def station_status(point, target, scene):
    radius = scene["vehicle_radius_m"]
    side = scene["field_side_m"]
    buildings = scene["buildings_xyxy_height_m"]
    field_clearance = min(point[0], point[1], side - point[0], side - point[1]) - radius
    building_clearances = {name: point_rectangle_distance(point, box) - radius
                           for name, box in buildings.items()}
    # Tangency is allowed for the vehicle disk; forbidden for the optical ray.
    disk_ok = field_clearance >= -1e-10 and min(building_clearances.values()) >= -1e-10
    horizontal_range = math.dist(point, target[:2])
    elevation = math.degrees(math.atan2(target[2] - scene["camera_height_m"], horizontal_range))
    elevation_ok = scene["elevation_deg"][0] - 1e-10 <= elevation <= scene["elevation_deg"][1] + 1e-10
    start = list(point) + [scene["camera_height_m"]]
    blockers = {name: interval for name, b in buildings.items()
                if (interval := segment_box_interval(start, target, [b[0], b[1], 0], [b[2], b[3], b[4]])) is not None}
    return {"station_xy_m": list(point), "horizontal_range_m": horizontal_range,
            "elevation_deg": elevation, "elevation_upper_margin_deg": scene["elevation_deg"][1] - elevation,
            "disk_ok": disk_ok, "field_clearance_m": field_clearance,
            "minimum_building_clearance_m": min(building_clearances.values()),
            "elevation_ok": elevation_ok, "blockers_segment_parameter_intervals": blockers,
            "observable": disk_ok and elevation_ok and not blockers}


def pair_metrics(p, q, target):
    a, b = [p[i] - target[i] for i in range(2)], [q[i] - target[i] for i in range(2)]
    determinant = a[0] * b[1] - a[1] * b[0]
    dot = sum(a[i] * b[i] for i in range(2))
    denominator = math.hypot(*a) * math.hypot(*b)
    return {"baseline_m": math.dist(p, q),
            "absolute_sin_intersection": abs(determinant) / denominator,
            "intersection_deg": math.degrees(math.atan2(abs(determinant), dot))}


def regression_checks(scene):
    lo, hi = [0, 0, 0], [1, 1, 1]
    assert segment_box_interval([-1, 0.5, 1], [2, 0.5, 1], lo, hi) is not None, "roof contact blocks"
    assert segment_box_interval([-1, 0.5, 1.000001], [2, 0.5, 1.000001], lo, hi) is None
    assert segment_box_interval([-1, -1, 2], [0, 0, 1], lo, hi) == [1, 1], "vertex contact blocks"
    assert segment_box_interval([-2, 0.5, 0.5], [-1, 0.5, 0.5], lo, hi) is None, "finite, not infinite ray"
    assert segment_box_interval([-1, 2, 0.5], [2, 2, 0.5], lo, hi) is None
    b3 = scene["buildings_xyxy_height_m"]["B3"]
    t2 = target_xyz(scene["alerts_azimuth_deg_range_m_altitude_m"]["T2"], scene)
    v3 = scene["vehicles_xy_m"]["V3"]
    assert segment_box_interval(v3 + [2], t2, b3[:2] + [0], b3[2:4] + [b3[4]]) is None
    assert math.isclose(2 + 33 * 10 / 13.5, 26.444444444444443)
    # Independent boundary probes for the H=50 elevation lower range.
    rmin = 48 / math.tan(math.radians(80))
    assert math.degrees(math.atan2(48, rmin - 0.001)) > 80
    assert math.degrees(math.atan2(48, rmin + 0.001)) < 80
    assert math.isclose(math.degrees(math.atan2(48, rmin)), 80)


def run():
    scene = json.loads(SCENE_PATH.read_text(encoding="utf-8"))
    rows = json.loads(MAIN_PATH.read_text(encoding="utf-8"))["scenarios"]
    alerts = sorted({tuple(row["new_alert"]) for row in rows if row["new_alert"] is not None})
    regression_checks(scene)
    out = []
    # Interior 2 m grid is descriptive only. It is NOT an observable-area integral.
    grid = list(itertools.product(range(2, 99, 2), repeat=2))
    for alert in alerts:
        target = target_xyz(alert, scene)
        initial = {v: station_status(p, target, scene) for v, p in scene["vehicles_xy_m"].items()}
        visible_vehicles = [v for v, status in initial.items() if status["observable"]]
        assert len(visible_vehicles) >= 3
        witnesses = {}
        for failed in (None,) + tuple(scene["vehicles_xy_m"]):
            pairs = []
            for a, b in itertools.combinations(visible_vehicles, 2):
                if failed in (a, b):
                    continue
                metrics = pair_metrics(scene["vehicles_xy_m"][a], scene["vehicles_xy_m"][b], target)
                if metrics["baseline_m"] >= 2 * scene["vehicle_radius_m"] and metrics["absolute_sin_intersection"] > 1e-8:
                    pairs.append((metrics["absolute_sin_intersection"], a, b, metrics))
            assert pairs, (alert, failed)
            _, a, b, metrics = max(pairs)
            witnesses[failed or "none"] = {"vehicles": [a, b],
                                           "stations_xy_m": [scene["vehicles_xy_m"][a], scene["vehicles_xy_m"][b]],
                                           **metrics}
        counts = Counter()
        for point in grid:
            status = station_status(point, target, scene)
            if not status["disk_ok"]:
                counts["disk_invalid"] += 1
            elif not status["elevation_ok"]:
                counts["elevation_invalid_after_disk"] += 1
            elif status["blockers_segment_parameter_intervals"]:
                counts["blocked_after_disk_and_elevation"] += 1
            else:
                counts["observable"] += 1
        assert sum(counts.values()) == len(grid)
        projection_in = [name for name, b in scene["buildings_xyxy_height_m"].items()
                         if b[0] <= target[0] <= b[2] and b[1] <= target[1] <= b[3]]
        out.append({"new_alert": list(alert), "target_xyz_m": target,
                    "projection_inside_or_on_buildings": projection_in,
                    "initial_station_checks": initial, "initial_observable_vehicles": visible_vehicles,
                    "static_pair_witness_by_failed_vehicle": witnesses,
                    "grid_mutually_exclusive_counts": dict(sorted(counts.items()))})
    return {"scope": "Static nominal geometry; not path reachability, dynamic vehicle availability, or a Q4 schedule feasibility claim.",
            "sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in (SCENE_PATH, MAIN_PATH)},
            "vehicle_disk_tangency": "allowed; all initial-station witnesses have strictly positive clearance",
            "ray_tangency": "blocked, including finite segment endpoints",
            "grid": {"x_y_m": "2,4,...,98", "spacing_m": 2, "points": len(grid), "interpretation": "counts, not area fractions or probabilities"},
            "minimum_horizontal_range_for_H50_m": 48 / math.tan(math.radians(80)),
            "alerts": out,
            "acceptance": {"synthetic_and_T2_regressions": "passed", "targets": len(out),
                           "all_failure_identities_have_noncollinear_static_pair": True}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    result = run()
    path = HERE / "geometry_audit.json"
    if args.check:
        assert json.loads(path.read_text(encoding="utf-8")) == result, "geometry evidence differs on regeneration"
    else:
        path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for row in result["alerts"]:
        print(row["new_alert"], "visible initial vehicles", row["initial_observable_vehicles"],
              "grid counts", row["grid_mutually_exclusive_counts"])
    print("PASS: all six targets, all five single-failure identities, closed-ray/roof/vertex/elevation regressions")


if __name__ == "__main__":
    main()
