"""B 题 2（1）：T2 的一阶二维定位 RMSE、取等见证与噪声灵敏度。
输入 q2 题设和避障图；输出最优站位、行驶时间与随机交会复核数据。
运行：nohup python3 -B -m src.run q2 --profile full
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol（版本发布日期未提供），2026-11-20。
"""

import math

import numpy as np

from src.q1.geometry import evaluate_station

from .paths import PADDING_M, Point, VisibilityRouter
from .scene import Scene


def pair_metrics(a: Point, b: Point, target: Point, sigma_rad: float) -> dict:
    """计算距离、交会角和一阶定位协方差；共线判为退化。"""
    vectors = np.array(target) - np.array([a, b])
    r1, r2 = np.linalg.norm(vectors, axis=1)
    if min(r1, r2) == 0:
        raise ValueError("目标正下方的水平视向未定义")
    u, v = vectors / np.array([r1, r2])[:, None]
    sine = abs(u[0] * v[1] - u[1] * v[0])
    if sine <= 1e-12:
        raise ValueError("两条视线数值共线，无法得到有效的一阶精度")
    jacobian = np.array([[-u[1], u[0]], [-v[1], v[0]]]) / np.array([r1, r2])[:, None]
    inverse = np.linalg.solve(jacobian, np.eye(2))
    covariance = sigma_rad**2 * inverse @ inverse.T
    return {"baseline_m": math.dist(a, b), "angle_deg": math.degrees(math.atan2(sine, u @ v)),
            "r1_m": float(r1), "r2_m": float(r2), "sin_gamma": float(sine),
            "covariance_m2": covariance.tolist(), "rmse_m": sigma_rad * math.hypot(r1, r2) / sine}


def solve_t2(scene: Scene, router: VisibilityRouter) -> tuple[dict, list[dict]]:
    """核验草案西/北直达见证；返回闭区间下取到的理论下界及偏移对照。"""
    cfg = scene.target_cfg[scene.targets.index("T2")]
    x, y, height = cfg.target_xyz
    r0 = cfg.distance_lower_bound
    sigma = math.radians(scene.bearing_sigma_deg)
    lower_bound = math.sqrt(2) * sigma * r0

    def evaluate_radius(offset: float) -> dict:
        """保持正交朝向，检验半径偏移（m）对可观测性、时限和精度的影响。"""
        r = r0 + offset
        sites = ((x - r, y), (x, y + r))
        rows = []
        for vehicle, site in zip(("V1", "V3"), sites):
            origin = tuple(map(float, scene.origins[scene.vehicles.index(vehicle)]))
            check = evaluate_station(*site, cfg)
            if not check.feasible:
                raise ValueError(f"{vehicle} 的 T2 西/北构造不满足车辆、仰角或 LOS 约束：{check}")
            route = router.route(origin, site)
            if route.waypoints != (origin, site) or route.length_m / scene.speed_m_s > scene.q21_travel_limit_s:
                raise ValueError(f"{vehicle} 的 T2 直达见证未通过；当前场景不能使用该构造")
            rows.append({"vehicle": vehicle, "site_xy_m": site, "waypoints": route.waypoints,
                         "length_m": route.length_m, "travel_s": route.length_m / scene.speed_m_s,
                         "pitch_deg": check.elevation_deg, "station_clearance_m": check.clearance_min})
        metrics = pair_metrics(*sites, (x, y), sigma)
        return {"radial_offset_m": offset, **metrics, "routes": rows,
                "gap_to_minimum_m": math.sqrt(2) * sigma * offset}

    witness = evaluate_radius(0.)
    if not math.isclose(witness["rmse_m"], lower_bound, rel_tol=1e-12):
        raise RuntimeError("T2 取等见证的误差与解析下界不一致")
    experiments = []
    for offset in (0.0, 0.001, 0.01, 0.1):
        row = witness if offset == 0 else evaluate_radius(offset)
        experiments.append({"experiment": "radial_offset", "radial_offset_m": offset, "angle_deg": row["angle_deg"],
                            "rmse_linear_m": row["rmse_m"], "gap_to_minimum_m": row["gap_to_minimum_m"],
                            "min_pitch_margin_deg": cfg.elevation_bounds_deg[1] - max(r["pitch_deg"] for r in row["routes"]),
                            "min_station_clearance_m": min(r["station_clearance_m"] for r in row["routes"])})
    return {"target": "T2", "target_xyz_m": (x, y, height), "radius_lower_bound_m": r0,
            "min_rmse_m": lower_bound, "minimum_attained": True,
            "bearing_sigma_deg": scene.bearing_sigma_deg, **witness}, experiments


def solve_fast_t2(scene: Scene, router: VisibilityRouter, visibility_gap_m: float, *,
                  wall_clearance_m: float = 0.0) -> dict:
    """在 2（1）等距正交圆上缩短最长行驶，并满足光学和车体净空（m）。"""
    cfg = scene.target_cfg[scene.targets.index("T2")]
    x, y, z = cfg.target_xyz
    radius = cfg.distance_lower_bound
    building, = [b for b in cfg.buildings if b.id == "B3"]
    origin = dict(zip(scene.vehicles, map(tuple, scene.origins.tolist())))
    # T2 位于 B3 投影上方。把相机到 T2 的射线截到 B3 屋顶，得到北沿的遮挡切点。
    roof_fraction = (building.height - cfg.camera_height) / (z - cfg.camera_height)
    north_offset = (building.y_max - y) / (1 - roof_fraction)
    if not 0 < roof_fraction < 1 or not 0 < north_offset < radius:
        raise ValueError("T2 圆周与 B3 北侧遮挡边界的相对位置已变")
    wall_offset = building.y_max - y + cfg.vehicle_radius + wall_clearance_m
    limit = max(north_offset, wall_offset)
    if limit >= radius:
        raise ValueError("B3 北侧的车体净空要求超出该等精度构型")
    boundary = math.asin(limit / radius)
    dx, dy = np.asarray(origin["V5"]) - (x, y)
    heading = math.atan2(dy, dx)
    first_blocked_angle = 2 * heading - boundary
    edge = (x + radius * math.cos(boundary), y + limit)
    if not (0 < heading < boundary < math.pi / 2 and building.x_min <= x
            and x + radius <= building.x_max and y <= building.y_max
            and building.y_min <= y + (1 - roof_fraction) * radius * math.sin(first_blocked_angle)):
        raise ValueError("无法证明比 B3 北沿更接近 V5 的圆弧都被楼顶遮挡")
    # 更近的圆弧横坐标都在 B3 楼底范围内。北侧阈值取视线切界与车身净空的较严者。
    if wall_offset > north_offset and y + radius * math.sin(first_blocked_angle) < building.y_min:
        raise ValueError("车体净空下界所需的 B3 北侧圆弧条件未通过")
    travel_floor = math.dist(origin["V5"], edge) / scene.speed_m_s
    other_floors = {v: (math.dist(p, (x, y)) - radius) / scene.speed_m_s for v, p in origin.items() if v != "V5"}
    north = north_offset + visibility_gap_m
    if wall_clearance_m:
        north = max(north, building.y_max - y + float(router.lower[1]) + PADDING_M)
    if not north_offset < north < radius:
        raise ValueError("T2 北向余量须为正且站点仍在等精度圆的东北弧")
    # 解析半径取等。保存绝对坐标时留两个坐标 ULP，避免相减还原半径恰好落在闭仰角的外侧。
    coordinate_padding = 2 * max(math.ulp(x), math.ulp(y), math.ulp(radius))
    stored_radius = radius + coordinate_padding
    east = math.sqrt((stored_radius - north) * (stored_radius + north))
    sites = ((x - north, y + east), (x + east, y + north))  # 两站相对 T2 向量相差 90°。
    routes = []
    for vehicle, site in zip(("V3", "V5"), sites):
        station = evaluate_station(*site, cfg)
        route = router.route(origin[vehicle], site)
        travel = route.length_m / scene.speed_m_s
        if not station.feasible or route.waypoints != (origin[vehicle], site) or travel > scene.q21_travel_limit_s:
            raise ValueError(f"T2 等精度快速部署见证不合格：{vehicle}")
        routes.append({"vehicle": vehicle, "site_xy_m": site, "waypoints": route.waypoints,
                       "length_m": route.length_m, "travel_s": travel, "pitch_deg": station.elevation_deg,
                       "station_clearance_m": station.clearance_min})
    latest = max(row["travel_s"] for row in routes)
    if not travel_floor < latest < min(t for v, t in other_floors.items() if v != "V3"):
        raise ValueError("T2 快速部署的双车下界或选车排除条件未通过")
    sigma = math.radians(scene.bearing_sigma_deg)
    shadow_contact = (x + radius * math.cos(math.asin(north_offset / radius)), y + north_offset)
    deployment = {"max_travel_s": latest, "infimum_s": travel_floor, "gap_s": latest - travel_floor,
                  "v5_blocked_angle_deg": tuple(map(math.degrees, (first_blocked_angle, boundary))),
                  "v5_b3_shadow_contact_xy_m": shadow_contact, "northward_buffer_m": visibility_gap_m,
                  "coordinate_radial_padding_m": coordinate_padding,
                  "other_vehicle_travel_lower_bounds_s": other_floors}
    if wall_clearance_m:
        deployment.update({"required_wall_clearance_m": wall_clearance_m,
                           "v5_limiting_edge_xy_m": edge, "northward_buffer_m": north - north_offset})
    return {"target": "T2", "target_xyz_m": cfg.target_xyz, "radius_lower_bound_m": radius,
            "min_rmse_m": math.sqrt(2) * sigma * radius, "minimum_attained": True,
            "bearing_sigma_deg": scene.bearing_sigma_deg, **pair_metrics(*sites, (x, y), sigma), "routes": routes,
            "equal_accuracy_travel": deployment}


def simulate_intersections(r_m: float, sigma_deg: float, *, samples: int, seed: int) -> list[dict]:
    """给固定半径的一组名义角添加方位噪声，统计前向交点及其误差分位数。"""
    sigma_rad = math.radians(sigma_deg)
    noise = np.random.default_rng(seed).normal(0, sigma_rad, size=(2, samples))
    rows = []
    for angle in (1, 5, 30, 90, 150, 175, 179):
        gamma = math.radians(angle)
        # 真目标在原点，两条从车指向目标的名义方位分别为 0 和 gamma。
        bearings = noise + np.array([0., gamma])[:, None]
        cx, cy = np.cos(bearings), np.sin(bearings)
        det = cx[0] * cy[1] - cy[0] * cx[1]
        nonparallel = np.abs(det) > 1e-12
        bx, by = r_m * (1 - math.cos(gamma)), -r_m * math.sin(gamma)
        first = np.divide(bx * cy[1] - by * cx[1], det, out=np.zeros_like(det), where=nonparallel)
        second = np.divide(bx * cy[0] - by * cx[0], det, out=np.zeros_like(det), where=nonparallel)
        positive = nonparallel & (first >= 0) & (second >= 0)
        error = np.hypot(first[positive] * cx[0, positive] - r_m, first[positive] * cy[0, positive])
        median, p95 = np.quantile(error, (0.5, 0.95)) if len(error) else (None, None)
        rows.append({"experiment": "bearing_noise", "angle_deg": angle, "rmse_linear_m": math.sqrt(2) * sigma_rad * r_m / math.sin(gamma),
                     "samples": samples, "seed": seed, "degenerate_samples": int((~nonparallel).sum()),
                     "negative_ray_samples": int((nonparallel & ~positive).sum()), "valid_samples": len(error),
                     "failure_rate": 1 - len(error) / samples, "valid_median_m": median, "valid_p95_m": p95,
                     "valid_sample_rmse_m": float(np.sqrt(np.mean(error**2))) if len(error) else None})
    return rows
