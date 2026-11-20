#!/usr/bin/env python3
"""Independent Q1 certificate, region plot, and geometry cross-checks.

Run from the repository root; all artifacts go to runs/b/q1_independent/.
No geometry or results are imported from another experiment.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OUT = ROOT / "runs/b/q1_independent"
OUT.mkdir(parents=True, exist_ok=True)
# Optional project-local dependency installation; no global environment changes.
LOCAL_DEPS = OUT / "_deps"
if LOCAL_DEPS.exists():
    sys.path.insert(0, str(LOCAL_DEPS))
os.environ.setdefault("MPLCONFIGDIR", str(OUT / "_mplconfig"))

import numpy as np
import scipy
from scipy.integrate import quad
from scipy.optimize import linprog, minimize, minimize_scalar
from scipy.spatial import ConvexHull

SCENE = json.loads((HERE / "scene.json").read_text())
THETA, ALERT_R, HEIGHT = SCENE["target_alert"]
TARGET = np.array(SCENE["center"]) + ALERT_R * np.array(
    [math.cos(math.radians(THETA)), math.sin(math.radians(THETA))]
)
CAMERA_H = SCENE["camera_height"]
DZ = HEIGHT - CAMERA_H
RHO = SCENE["vehicle_radius"]
RMIN = DZ / math.tan(math.radians(SCENE["elevation_bounds_deg"][1]))
BUILDINGS = SCENE["buildings"]
VEHICLES = np.array([v["xy"] for v in SCENE["vehicles"]])
CERT_TOL = 1e-10  # numerical contact representation, not physical clearance


def rectangle_corners(b):
    x0, x1, y0, y1 = b["bounds"]
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])


def shadow_hull(b):
    """Exact camera-plane projection of a closed prism, h_camera < h < H."""
    k = DZ / (HEIGHT - b["height"])
    corners = rectangle_corners(b)
    eight = np.vstack([corners, TARGET + k * (corners - TARGET)])
    hull = ConvexHull(eight)
    return k, eight[hull.vertices], hull.equations


SHADOWS = [shadow_hull(b) for b in BUILDINGS]


def rect_distance(points, b):
    points = np.asarray(points)
    x0, x1, y0, y1 = b["bounds"]
    dx = np.maximum(np.maximum(x0 - points[..., 0], 0), points[..., 0] - x1)
    dy = np.maximum(np.maximum(y0 - points[..., 1], 0), points[..., 1] - y1)
    return np.hypot(dx, dy)


def vehicle_geometry(points, tol=0.0):
    points = np.atleast_2d(points)
    field = ((points >= RHO - tol) & (points <= 100 - RHO + tol)).all(axis=1)
    obstacle = np.ones(len(points), dtype=bool)
    for b in BUILDINGS:
        obstacle &= rect_distance(points, b) >= RHO - tol
    return field, obstacle


def blocked_polygon(points, shadow, tol=0.0):
    """Halfspace test in the analytically projected convex shadow."""
    points = np.atleast_2d(points)
    equations = shadow[2]
    inside = np.ones(len(points), dtype=bool)
    for nx, ny, c in equations:
        inside &= nx * points[:, 0] + ny * points[:, 1] + c <= tol
    return inside


def blocked_slab(points, b, tol_t=0.0):
    """Independent original 3D closed-segment/AABB interval intersection.

    Does not call or use shadow_hull, its scale factors, or its halfspaces.
    t is measured from the camera (t=0) to the target (t=1).
    """
    points = np.atleast_2d(points)
    starts = np.column_stack([points, np.full(len(points), CAMERA_H)])
    ends = np.r_[TARGET, HEIGHT]
    directions = ends - starts
    x0, x1, y0, y1 = b["bounds"]
    lower, upper = (x0, y0, 0.0), (x1, y1, b["height"])
    enter = np.zeros(len(points))
    leave = np.ones(len(points))
    possible = np.ones(len(points), dtype=bool)
    for axis in range(3):
        velocity = directions[:, axis]
        parallel = velocity == 0.0
        possible &= ~parallel | (
            (starts[:, axis] >= lower[axis]) & (starts[:, axis] <= upper[axis])
        )
        a = np.full(len(points), -np.inf)
        z = np.full(len(points), np.inf)
        nonparallel = ~parallel
        t1 = (lower[axis] - starts[nonparallel, axis]) / velocity[nonparallel]
        t2 = (upper[axis] - starts[nonparallel, axis]) / velocity[nonparallel]
        a[nonparallel] = np.minimum(t1, t2)
        z[nonparallel] = np.maximum(t1, t2)
        enter = np.maximum(enter, a)
        leave = np.minimum(leave, z)
    return possible & (enter <= leave + tol_t)


def blocked_lp(point, b):
    """Third oracle: LP feasibility of the six 3D prism inequalities in t."""
    start = np.r_[point, CAMERA_H]
    direction = np.r_[TARGET, HEIGHT] - start
    x0, x1, y0, y1 = b["bounds"]
    low, high = np.array([x0, y0, 0.0]), np.array([x1, y1, b["height"]])
    result = linprog(
        [0.0], A_ub=np.r_[direction, -direction].reshape(-1, 1),
        b_ub=np.r_[high - start, start - low], bounds=[(0.0, 1.0)],
        method="highs",
    )
    return result.success


def masks(points, tol=0.0, oracle="polygon"):
    points = np.atleast_2d(points)
    field, obstacle = vehicle_geometry(points, tol)
    radius = np.linalg.norm(points - TARGET, axis=1)
    elevation = radius >= RMIN - tol  # lower -10 degree bound is inactive
    blocked = np.zeros(len(points), dtype=bool)
    for b, shadow in zip(BUILDINGS, SHADOWS):
        if oracle == "polygon":
            blocked |= blocked_polygon(points, shadow, tol)
        else:
            blocked |= blocked_slab(points, b, tol_t=tol)
    return field, obstacle, elevation, ~blocked


def feasible(points, tol=0.0, oracle="polygon"):
    return np.logical_and.reduce(masks(points, tol, oracle))


def classify(points):
    """Plot categories, with an explicitly documented priority for overlaps."""
    field, obstacle, elevation, clear = masks(points)
    labels = np.zeros(len(points), dtype=np.uint8)  # observable
    labels[~elevation] = 2
    labels[~clear] = 1
    labels[~obstacle] = 3
    labels[~field] = 4
    return labels


def analytic_solution():
    # On the minimum-radius circle, only the B2 vehicle exclusion intersects.
    # Its top straight side y=72.5 and east straight side x=80.5 cut the circle.
    phi_top = math.pi - math.asin((71 + RHO - TARGET[1]) / RMIN)
    phi_east = 2 * math.pi - math.acos((79 + RHO - TARGET[0]) / RMIN)
    ds = np.linalg.norm(VEHICLES - TARGET, axis=1)
    lower_bounds = np.abs(ds - RMIN)
    winner = int(np.argmin(lower_bounds))
    direction = (VEHICLES[winner] - TARGET) / ds[winner]
    point = TARGET + RMIN * direction
    return point, winner, lower_bounds, phi_top, phi_east


def certificate(point, winner, lower_bounds, phi_top, phi_east):
    radius = float(np.linalg.norm(point - TARGET))
    separation = []
    for b, shadow in zip(BUILDINGS, SHADOWS):
        x0, x1, y0, y1 = b["bounds"]
        seg_min, seg_max = np.minimum(point, TARGET), np.maximum(point, TARGET)
        gaps = [x0 - seg_max[0], seg_min[0] - x1,
                y0 - seg_max[1], seg_min[1] - y1]
        separation.append({
            "building": b["name"],
            "center_to_footprint_m": float(rect_distance(point, b)),
            "vehicle_clearance_m": float(rect_distance(point, b) - RHO),
            "xy_separating_axis_gap_m": float(max(gaps)),
            "occluded_by_3d_slab": bool(blocked_slab(point, b, CERT_TOL)[0]),
            "occluded_by_shadow": bool(blocked_polygon(point, shadow, CERT_TOL)[0]),
            "shadow_scale": shadow[0],
            "shadow_vertices_xy_m": shadow[1].tolist(),
        })
    field_margins = np.r_[point - RHO, 100 - RHO - point]
    travel = float(np.linalg.norm(point - VEHICLES[winner]))
    delta = 1e-4
    rounded_safe = np.round(TARGET + (RMIN + delta) * (point - TARGET) / radius, 6)
    return {
        "target_xyz_m": [*TARGET, HEIGHT],
        "r_lower_bound_m": RMIN,
        "quality_upper_bound_per_m": 1 / RMIN,
        "selected_xy_m": point.tolist(),
        "selected_vehicle": SCENE["vehicles"][winner]["name"],
        "r_recomputed_m": radius,
        "Q_recomputed_per_m": 1 / radius,
        "elevation_deg": math.degrees(math.atan2(DZ, radius)),
        "field_margins_m_order_xmin_ymin_xmax_ymax": field_margins.tolist(),
        "minimum_field_margin_m": float(field_margins.min()),
        "travel_tiebreak_m": travel,
        "travel_lower_bound_by_vehicle_m": lower_bounds.tolist(),
        "all_three_constraints_pass": bool(feasible(point, CERT_TOL, "slab")[0]),
        "buildings": separation,
        "optimal_arc_parameterization": "T_xy + r_min * (cos(phi),sin(phi))",
        "optimal_arc_deg": [[0, math.degrees(phi_top)], [math.degrees(phi_east), 360]],
        "optimal_arc_endpoints_closed": True,
        "optimal_arc_endpoint_xy_m": [
            (TARGET + RMIN * np.array([math.cos(phi_top), math.sin(phi_top)])).tolist(),
            (TARGET + RMIN * np.array([math.cos(phi_east), math.sin(phi_east)])).tolist(),
        ],
        "alternative_eastmost_tiebreak_xy_m": [TARGET[0] + RMIN, TARGET[1]],
        "six_decimal_safe_nonoptimal_representative": {
            "xy_m": rounded_safe.tolist(),
            "radial_increment_before_rounding_m": delta,
            "r_m": float(np.linalg.norm(rounded_safe - TARGET)),
            "Q_per_m": float(1 / np.linalg.norm(rounded_safe - TARGET)),
            "elevation_deg": math.degrees(math.atan2(DZ, np.linalg.norm(rounded_safe - TARGET))),
            "feasible_with_zero_tolerance": bool(feasible(rounded_safe)[0]),
        },
    }


def boundary_checks():
    rows = []

    def add(label, actual, expected=True):
        rows.append({"case": label, "actual": bool(actual), "expected": bool(expected),
                     "pass": bool(actual) == bool(expected)})

    step = 1e-5
    add("field tangency allowed", vehicle_geometry([[1.5, 95]], CERT_TOL)[0][0])
    add("field penetration rejected", vehicle_geometry([[1.5-step, 95]])[0][0], False)
    add("building side disk tangency allowed", vehicle_geometry([[80.5, 65]], CERT_TOL)[1][0])
    add("building side disk penetration rejected", vehicle_geometry([[80.5-step, 65]])[1][0], False)
    corner = np.array([79, 71]) + RHO / math.sqrt(2)
    add("round NE corner tangency allowed", vehicle_geometry(corner, CERT_TOL)[1][0])
    add("round-corner outward point allowed (square inflation would reject)",
        vehicle_geometry(corner + step)[1][0])
    add("80 degree endpoint included", feasible(TARGET + [RMIN, 0], CERT_TOL)[0])
    add("smaller radius excluded", feasible(TARGET + [RMIN-step, 0])[0], False)
    add("larger radius included", feasible(TARGET + [RMIN+step, 0])[0])
    add("r=0 excluded", masks(TARGET)[2][0], False)

    b = BUILDINGS[1]
    shadow = SHADOWS[1]
    contact = TARGET + shadow[0] * (np.array([79, 71]) - TARGET)
    for label, p, blocked in [
        ("B2 roof NE vertex contact", contact, True),
        ("B2 roof NE vertex clear-side perturbation", contact + [0, step], False),
        ("B2 roof NE vertex blocked-side perturbation", contact - [0, step], True),
        ("B2 roof west-edge contact", TARGET + shadow[0] * (np.array([63, 65])-TARGET), True),
        ("B2 vertical NE edge contact", TARGET + DZ/(HEIGHT-15) * (np.array([79, 71])-TARGET), True),
        ("parallel x, clear", np.array([TARGET[0], 50]), False),
        ("parallel y, blocked", np.array([60, TARGET[1]]), True),
        ("parallel y, clears above roof despite footprint crossing", np.array([10, TARGET[1]]), False),
        ("camera inside prism", np.array([70, 65]), True),
    ]:
        add(label + " / slab", blocked_slab(p, b, CERT_TOL)[0], blocked)
        add(label + " / shadow", blocked_polygon(p, shadow, CERT_TOL)[0], blocked)
        add(label + " / LP", blocked_lp(p, b), blocked)
    assert all(r["pass"] for r in rows), rows
    return rows


def numeric_optimization(point, rng):
    # A continuous constrained solver is a corroboration, NOT the global proof.
    # Shadow epsilon defines a conservative surrogate for this numerical search.
    # Solver stopping tolerances can still violate it: verify using strict 3D LOS.
    def constraints(p):
        g = [np.linalg.norm(p - TARGET) - RMIN]
        g.extend(float(rect_distance(p, b) - RHO) for b in BUILDINGS)
        g.extend(float(np.max(s[2][:, :2] @ p + s[2][:, 2])) - 1e-8 for s in SHADOWS)
        return np.array(g)

    pool = rng.uniform(RHO, 100-RHO, (1000, 2))
    starts = np.vstack([point, pool[feasible(pool)][:23]])
    rows = []
    for p0 in starts:
        result = minimize(
            lambda p: np.linalg.norm(p-TARGET), p0, method="SLSQP",
            bounds=[(RHO, 100-RHO)]*2,
            constraints=[{"type": "ineq", "fun": constraints}],
            options={"ftol": 1e-11, "maxiter": 500},
        )
        field, obstacle, elevation, _ = masks(result.x, 1e-7)
        clear = not any(blocked_slab(result.x, b, CERT_TOL)[0] for b in BUILDINGS)
        independently_feasible = bool(field[0] and obstacle[0] and elevation[0] and clear)
        rows.append({
            "start_xy": p0.tolist(), "success": bool(result.success),
            "message": result.message, "xy": result.x.tolist(),
            "r_m": float(np.linalg.norm(result.x-TARGET)),
            "r_minus_bound_m": float(np.linalg.norm(result.x-TARGET)-RMIN),
            "feasible_geometry_and_radius_1e_7_m_los_contact_1e_10_t": independently_feasible,
        })
    valid = [r for r in rows if r["success"] and r["feasible_geometry_and_radius_1e_7_m_los_contact_1e_10_t"]]
    assert valid and abs(min(r["r_m"] for r in valid)-RMIN) < 1e-7
    return rows


def cross_validation(n, seed, point, winner, lower_bounds, phi_top, phi_east):
    rng = np.random.default_rng(seed)
    points = rng.uniform(0, 100, (n, 2))
    disagreements = {}
    for b, shadow in zip(BUILDINGS, SHADOWS):
        slab = blocked_slab(points, b)
        poly = blocked_polygon(points, shadow)
        disagreements[b["name"]] = int(np.count_nonzero(slab != poly))
    assert sum(disagreements.values()) == 0
    lp_mismatches = 0
    for p in points[:100]:
        for b in BUILDINGS:
            lp_mismatches += bool(blocked_slab(p, b)[0]) != blocked_lp(p, b)
    assert lp_mismatches == 0

    # Whole optimal-circle membership check, independent 3D visibility.
    phi = np.linspace(0, 2*math.pi, 36000, endpoint=False)
    circle = TARGET + RMIN * np.column_stack([np.cos(phi), np.sin(phi)])
    stated_arc = (phi <= phi_top) | (phi >= phi_east)
    observed_arc = feasible(circle, CERT_TOL, "slab")
    arc_mismatch = int(np.count_nonzero(stated_arc != observed_arc))
    assert arc_mismatch == 0
    endpoints = TARGET + RMIN * np.array([[math.cos(phi_top), math.sin(phi_top)],
                                         [math.cos(phi_east), math.sin(phi_east)]])
    assert feasible(endpoints, CERT_TOL, "slab").all()

    # Independent bounded angular minimization of secondary travel distance.
    tie = minimize_scalar(
        lambda a: np.linalg.norm(TARGET + RMIN*np.array([math.cos(a), math.sin(a)])-VEHICLES[winner]),
        bounds=(phi_east, phi_top+2*math.pi), method="bounded", options={"xatol": 1e-13},
    )
    numerical_point = TARGET + RMIN*np.array([math.cos(tie.x), math.sin(tie.x)])
    assert np.linalg.norm(numerical_point-point) < 1e-5
    path = VEHICLES[winner] + np.linspace(0, 1, 1001)[:, None]*(point-VEHICLES[winner])
    # Path need only satisfy vehicle geometry, not observation elevation in transit.
    assert np.logical_and.reduce(vehicle_geometry(path, CERT_TOL)).all()

    area_hit = feasible(points, oracle="slab")
    phat = float(area_hit.mean())
    area = 10000*phat
    se = 10000*math.sqrt(phat*(1-phat)/n)
    summary = {
        "seed": seed, "random_points": n, "sampling_domain_m2": 10000,
        "per_building_slab_vs_shadow_mismatches": disagreements,
        "LP_vs_slab_checks": 500, "LP_vs_slab_mismatches": lp_mismatches,
        "circle_samples": len(phi), "circle_arc_membership_mismatches": arc_mismatch,
        "arc_endpoints_pass": True,
        "angular_tiebreak_xy_m": numerical_point.tolist(),
        "angular_tiebreak_distance_from_exact_m": float(np.linalg.norm(numerical_point-point)),
        "angular_tiebreak_travel_m": float(tie.fun),
        "winning_straight_path_geometry_samples": len(path),
        "winning_straight_path_pass": True,
        "area_monte_carlo_m2": area,
        "area_monte_carlo_standard_error_m2": se,
        "area_monte_carlo_normal_95_percent_interval_m2": [area-1.96*se, area+1.96*se],
        "area_CI_note": "Sampling uncertainty only; boundary tests and exact formulas define the region.",
    }
    return summary, numeric_optimization(point, rng)


def grid_validation(steps):
    rows = []
    for step in steps:
        nside = round(100/step)
        assert abs(nside*step-100) < 1e-9
        centers = (np.arange(nside)+0.5)*step
        count = 0
        r_best = math.inf
        p_best = None
        # Chunk rows to keep memory bounded at the default 0.05 m grid.
        for start in range(0, nside, 40):
            x, y = np.meshgrid(centers, centers[start:start+40])
            points = np.column_stack([x.ravel(), y.ravel()])
            valid = feasible(points)
            count += int(valid.sum())
            if valid.any():
                selected = points[valid]
                rs = np.linalg.norm(selected-TARGET, axis=1)
                idx = np.argmin(rs)
                if rs[idx] < r_best:
                    r_best, p_best = float(rs[idx]), selected[idx].tolist()
        rows.append({"step_m": step, "points": nside*nside,
                     "observable_midpoints": count, "area_midpoint_m2": count*step**2,
                     "best_grid_xy_m": p_best, "best_grid_r_m": r_best,
                     "best_grid_r_gap_m": r_best-RMIN})
    return rows


def section_area():
    """Independent area estimate: integrate exact 1D interval unions at each x.

    Shadow intersections use polygon edges, NOT raster masks or halfspaces.
    Rounded vehicle corners and the elevation disk are circular sections.
    Boundary membership does not affect area, but remains important for stations.
    """
    left, right = RHO, 100-RHO

    def polygon_section(vertices, x):
        hits = []
        for a, b in zip(vertices, np.roll(vertices, -1, axis=0)):
            if min(a[0], b[0]) <= x <= max(a[0], b[0]):
                if abs(b[0]-a[0]) < 1e-12:
                    if abs(x-a[0]) < 1e-12:
                        hits.extend([a[1], b[1]])
                else:
                    t = (x-a[0])/(b[0]-a[0])
                    hits.append(a[1] + t*(b[1]-a[1]))
        return (min(hits), max(hits)) if hits else None

    def length(x):
        intervals = []
        for b, shadow in zip(BUILDINGS, SHADOWS):
            section = polygon_section(shadow[1], x)
            if section is not None:
                intervals.append(section)
            x0, x1, y0, y1 = b["bounds"]
            dx = max(x0-x, 0.0, x-x1)
            if dx <= RHO:
                extension = math.sqrt(max(RHO**2-dx**2, 0))
                intervals.append((y0-extension, y1+extension))
        dx = abs(x-TARGET[0])
        if dx <= RMIN:
            extension = math.sqrt(max(RMIN**2-dx**2, 0))
            intervals.append((TARGET[1]-extension, TARGET[1]+extension))
        clipped = sorted((max(a, left), min(b, right)) for a, b in intervals
                         if b > left and a < right)
        occupied, end = 0.0, left
        for a, b in clipped:
            if b > end:
                occupied += b-max(a, end)
                end = b
        return right-left-occupied

    knots = [left, right, TARGET[0]-RMIN, TARGET[0]+RMIN]
    for b, shadow in zip(BUILDINGS, SHADOWS):
        x0, x1, _, _ = b["bounds"]
        knots.extend([x0-RHO, x0, x1, x1+RHO])
        knots.extend(shadow[1][:, 0])
    knots = sorted(set(max(left, min(right, float(x))) for x in knots))
    estimates = []
    for eps in [1e-5, 1e-8]:
        values = [quad(length, a, b, epsabs=eps/len(knots), epsrel=1e-11, limit=200)
                  for a, b in zip(knots[:-1], knots[1:])]
        estimates.append({"requested_absolute_tolerance_m2": eps,
                          "area_m2": sum(v for v, e in values),
                          "summed_quadrature_error_estimate_m2": sum(e for v, e in values)})
    return {"method": "Vertical exact interval union + adaptive scalar quadrature",
            "is_exact_area": False, "estimates": estimates,
            "agreement_m2": abs(estimates[0]["area_m2"]-estimates[1]["area_m2"])}


def plot_region(point, phi_top, phi_east, step):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap, BoundaryNorm
    from matplotlib.patches import Circle, FancyBboxPatch, Patch, Polygon, Rectangle
    from matplotlib.lines import Line2D

    nside = round(100/step)
    edges = np.linspace(0, 100, nside+1)
    mids = (edges[:-1]+edges[1:])/2
    x, y = np.meshgrid(mids, mids)
    points = np.column_stack([x.ravel(), y.ravel()])
    labels = classify(points).reshape(x.shape)
    colors = ["#d6eee5", "#edc799", "#cbd8f0", "#d3d3d3", "#f2f2f2"]
    cmap, norm = ListedColormap(colors), BoundaryNorm(np.arange(-0.5, 5.5), 5)
    plt.rcParams.update({"font.size": 10, "axes.labelsize": 11, "svg.fonttype": "none"})
    fig, axs = plt.subplots(1, 2, figsize=(12.8, 6.2), gridspec_kw={"width_ratios": [1.2, 1]})
    phis = [np.linspace(0, phi_top, 700), np.linspace(phi_east, 2*math.pi, 700)]
    for ax in axs:
        ax.pcolormesh(edges, edges, labels, cmap=cmap, norm=norm, shading="flat", rasterized=True)
        for b, shadow in zip(BUILDINGS, SHADOWS):
            x0, x1, y0, y1 = b["bounds"]
            ax.add_patch(Polygon(shadow[1], fill=False, edgecolor="#ab733c", linewidth=0.8, alpha=.9))
            # Rounded Euclidean offset, not an axis-expanded rectangular box.
            ax.add_patch(FancyBboxPatch((x0-RHO, y0-RHO), x1-x0+2*RHO, y1-y0+2*RHO,
                         boxstyle=f"round,pad=0,rounding_size={RHO}",
                         facecolor="none", edgecolor="#606060", linewidth=0.75))
            ax.add_patch(Rectangle((x0, y0), x1-x0, y1-y0, facecolor="#767676", edgecolor="#444", linewidth=.7))
        ax.add_patch(Rectangle((RHO, RHO), 100-2*RHO, 100-2*RHO,
                              fill=False, edgecolor="#777", linewidth=.8))
        ax.add_patch(Circle(TARGET, RMIN, fill=False, edgecolor="#456899", linewidth=1.2, linestyle="--"))
        for angles in phis:
            circle = TARGET + RMIN*np.column_stack([np.cos(angles), np.sin(angles)])
            ax.plot(circle[:, 0], circle[:, 1], color="#087f5b", lw=2.6, zorder=6)
        for angle in [phi_top, phi_east]:
            endpoint = TARGET + RMIN*np.array([math.cos(angle), math.sin(angle)])
            ax.plot(*endpoint, "o", color="#087f5b", markersize=4, zorder=7)
        ax.plot(*TARGET, marker="^", color="#b02030", ms=8, zorder=8)
        ax.plot(*point, marker="*", color="#9c174d", ms=14, markeredgecolor="white", mew=.6, zorder=9)
        ax.plot(VEHICLES[:, 0], VEHICLES[:, 1], "s", color="#356996", ms=4, zorder=7)
        ax.set_aspect("equal")
        ax.set_xlabel("East coordinate x (m)")
        ax.set_ylabel("North coordinate y (m)")
        ax.grid(alpha=.17, linewidth=.5)
        ax.set_axisbelow(True)
    axs[0].set(xlim=(0, 100), ylim=(0, 100), title="(a) Observable vehicle-center region for T1")
    for b in BUILDINGS:
        x0, x1, y0, y1 = b["bounds"]
        axs[0].text((x0+x1)/2, (y0+y1)/2, b["name"], ha="center", va="center", color="white", fontsize=9)
    for i, v in enumerate(VEHICLES):
        axs[0].annotate(f"V{i+1}", v, xytext=(4, 4), textcoords="offset points", fontsize=8)
    axs[0].annotate("T1'", TARGET, xytext=(4, 5), textcoords="offset points", color="#b02030")
    axs[1].set(xlim=(73, 96), ylim=(58.5, 81.5), title="(b) Continuous optimal arc and selected station")
    axs[1].plot([VEHICLES[3, 0], point[0]], [VEHICLES[3, 1], point[1]],
                color="#9c174d", linewidth=1.3, linestyle=":")
    axs[1].annotate("P* = (91.155711, 66.121032)", point, xytext=(77, 59.8),
                    arrowprops={"arrowstyle": "-", "color": "#9c174d"}, fontsize=8.4, color="#9c174d")
    axs[1].annotate("T1'", TARGET, xytext=(5, 6), textcoords="offset points", color="#b02030")
    axs[1].annotate("V4", VEHICLES[3], xytext=(4, 5), textcoords="offset points", fontsize=9)
    axs[1].text(74, 68, "B2\n28 m", ha="left", color="white", fontsize=10)
    axs[1].text(75, 79.9, f"r* = {RMIN:.6f} m; elevation = 80 deg", fontsize=9)
    legend = [Patch(facecolor=colors[0], label="Observable"),
              Patch(facecolor=colors[1], label="LOS blocked (closed shadows)"),
              Patch(facecolor=colors[2], label="Elevation > 80 deg"),
              Patch(facecolor=colors[3], label="Vehicle/building exclusion"),
              Patch(facecolor=colors[4], label="Field-boundary exclusion"),
              Line2D([0], [0], color="#087f5b", lw=2.6, label="All quality-optimal stations"),
              Line2D([0], [0], marker="*", color="none", markerfacecolor="#9c174d", markersize=11, label="Travel-tiebreak selection")]
    fig.legend(handles=legend, loc="lower center", ncol=3, frameon=False, bbox_to_anchor=(.5, .055), fontsize=8.5)
    fig.text(.5, .018, "Vehicle contact allowed; LOS contact forbidden; 80 deg included. Overlap priority: field > vehicle > LOS > elevation.\n"
             f"Region fill: {step:g} m midpoint cells (visualization only); boundaries and optimum: continuous formulas.",
             ha="center", fontsize=8, color="#444")
    fig.subplots_adjust(left=.07, right=.98, top=.93, bottom=.23, wspace=.20)
    for ext in ["png", "pdf", "svg"]:
        fig.savefig(OUT / f"observable_region.{ext}", dpi=240, metadata={"Creator": "q1_independent/solve.py"})
    plt.close(fig)
    return matplotlib.__version__


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20261120)
    parser.add_argument("--samples", type=int, default=250000)
    parser.add_argument("--plot-step", type=float, default=0.1)
    parser.add_argument("--grid-steps", type=float, nargs="+", default=[0.2, 0.1, 0.05])
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()
    assert args.samples >= 100
    point, winner, lower_bounds, phi_top, phi_east = analytic_solution()
    cert = certificate(point, winner, lower_bounds, phi_top, phi_east)
    assert cert["all_three_constraints_pass"]
    boundaries = boundary_checks()
    checks, solver_runs = cross_validation(args.samples, args.seed, point, winner, lower_bounds, phi_top, phi_east)
    grids = grid_validation(args.grid_steps)
    area_integral = section_area()
    mpl_version = None if args.no_plot else plot_region(point, phi_top, phi_east, args.plot_step)
    source_files = [HERE / "scene.json", HERE / "solve.py", ROOT / SCENE["source"]["authoritative"]]
    report = {
        "certificate": cert, "cross_validation": checks, "grid_convergence": grids,
        "continuous_solver_runs": solver_runs, "boundary_checks": boundaries,
        "area_section_integration": area_integral,
        "provenance": {
            "command": "python3 " + " ".join([str(Path(__file__).relative_to(ROOT)), *sys.argv[1:]]),
            "python": platform.python_version(), "numpy": np.__version__, "scipy": scipy.__version__,
            "matplotlib": mpl_version, "arguments": vars(args),
            "git_head": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip(),
            "sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files},
            "scope": "Only experiments/b/q1_independent and runs/b/q1_independent are written; no parent implementation imported.",
        },
    }
    (OUT / "result.json").write_text(json.dumps(report, indent=2, ensure_ascii=False)+"\n")
    for filename, rows in [("grid_convergence.csv", grids), ("boundary_checks.csv", boundaries)]:
        with (OUT / filename).open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    summary = {
        "P_xy_m": cert["selected_xy_m"], "vehicle": cert["selected_vehicle"],
        "r_m": cert["r_recomputed_m"], "Q_per_m": cert["Q_recomputed_per_m"],
        "elevation_deg": cert["elevation_deg"], "travel_tiebreak_m": cert["travel_tiebreak_m"],
        "optimal_arc_deg": cert["optimal_arc_deg"],
        "shadow_slab_mismatches": checks["per_building_slab_vs_shadow_mismatches"],
        "LP_mismatches": checks["LP_vs_slab_mismatches"],
        "boundary_checks_passed": f"{sum(r['pass'] for r in boundaries)}/{len(boundaries)}",
        "output_directory": str(OUT.relative_to(ROOT)),
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
