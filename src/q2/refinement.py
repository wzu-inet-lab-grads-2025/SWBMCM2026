"""B 题第二问细化，缩短等精度部署与并发调度的关键链。
输入已验收的 q2 归档和细化 YAML，输出 runs/q2 下站位、路线、时序、验收和图。
运行：nohup python3 -B -m src.run q2 --refine-schedule <q2 归档目录>。
"""

import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
import tempfile
import time

import numpy as np
from scipy.optimize import minimize
import yaml

from checks.q2_geometry import CheckError, load_scene
from checks.q2_validate import verify_q21, verify_q22
from src.q1.geometry import camera_shadow_hull
from src.shared.wall_safety import execution_wall_budget

from .experiment import _metadata
from .paths import PADDING_M
from .precision import solve_fast_t2
from .report import critical_chain, recover_plan, write_csv, write_json, write_plots
from .scene import build_problem, build_router, read_scene
from .schedule import SELECT_TOL_S, greedy, replay


class StationDomain:
    """一个告警的站位约束。各余量以 m 计，光学余量为最紧的阴影外侧距离。"""

    def __init__(self, target):
        """从题面的三维闭楼体生成相机标高平面的阴影外侧半平面。"""
        self.target = target
        self.center = np.array(target.target_xyz[:2])
        self.shadows = tuple(camera_shadow_hull(target, b).equations for b in target.buildings)
        self.lower = np.array([(b.x_min, b.y_min) for b in target.buildings])
        self.upper = np.array([(b.x_max, b.y_max) for b in target.buildings])

    def clearance(self, points):
        """返回每站到场界和楼体的最小车体余量、最小光学余量及仰角径向余量。"""
        p = np.atleast_2d(points)
        f = self.target
        obstacle = np.maximum(0, np.maximum(self.lower - p[:, None], p[:, None] - self.upper))
        land = np.minimum(np.min(np.linalg.norm(obstacle, axis=2), axis=1),
                          np.minimum(p, (f.field_width, f.field_height) - p).min(axis=1)) - f.vehicle_radius
        sight = np.min([np.max(p @ h[:, :2].T + h[:, 2], axis=1) for h in self.shadows], axis=0)
        pitch = np.linalg.norm(p - self.center, axis=1) - f.distance_lower_bound
        return np.column_stack((land, sight, pitch))

    def local_constraints(self, seed, margin, los_gap, *, wall_clearance_m=0.0):
        """每栋楼选种子所在外侧半平面，局部优化时保持视线不接触阴影。"""
        planes = np.array([h[np.argmax(h[:, :2] @ seed + h[:, 2])] for h in self.shadows])

        def constraints(point):
            """给 SLSQP 返回距车体、闭仰角和局部光学边界的约束余量。"""
            land, _, pitch = self.clearance(point)[0]
            return np.r_[land - max(margin, wall_clearance_m), pitch - margin,
                         planes[:, :2] @ point + planes[:, 2] - los_gap]

        return constraints

    def slew(self, point, speed):
        """从正东水平转到该告警的双轴并行耗时（s），复位用时相同。"""
        x, y = self.center - point
        pitch = math.atan2(self.target.target_xyz[2] - self.target.camera_height, math.hypot(x, y))
        return math.degrees(max(abs(math.atan2(y, x)), pitch)) / speed


def _chain(scene, domains, reused, first, second, seeds, partner_ready, settings, decimals):
    """压缩复用车的两段直达链，求不低于两条同步就绪分支的连续秒制上界。"""
    first_domain, second_domain = domains[first], domains[second]
    home = scene.origins[reused]
    rounding = math.sqrt(2) * 0.5 * 10**-decimals if decimals is not None else 0.0
    buffer = settings["practical_station_gap_m"] + rounding if decimals is not None else 0.0
    los_gap = buffer + settings["nominal_visibility_gap_m"]
    wall = settings.get("wall_clearance_m", 0.0)
    wall_buffer = wall + rounding + 2 * PADDING_M if wall else 0.0
    first_ok, second_ok = (d.local_constraints(p, buffer, los_gap, wall_clearance_m=wall_buffer)
                           for d, p in zip((first_domain, second_domain), seeds))

    def limits(x):
        """联合开测取两车较晚就绪，随后累加观测、复位、转场和二次指向。"""
        p, q, latest = x[:2], x[2:4], x[4]
        turn = first_domain.slew(p, scene.gimbal_deg_s)
        reused_ready = math.dist(home, p) / scene.speed_m_s + turn
        tail = scene.observation_s + turn + math.dist(p, q) / scene.speed_m_s
        tail += second_domain.slew(q, scene.gimbal_deg_s)
        return np.r_[latest - tail - partner_ready, latest - tail - reused_ready, first_ok(p), second_ok(q)]

    coordinates = np.ravel(seeds)
    radius = settings["search_radius_m"]
    fit = minimize(lambda x: x[-1], np.r_[coordinates, 12.0], method="SLSQP",
                   bounds=[*((x - radius, x + radius) for x in coordinates), (0, None)],
                   constraints={"type": "ineq", "fun": limits}, options={"ftol": 1e-12, "maxiter": 250})
    if not fit.success or limits(fit.x).min() < -1e-9:
        raise RuntimeError(f"复用链连续细化失败：{fit.message}")
    sites = fit.x[:4].reshape(2, 2)
    return np.round(sites, decimals) if decimals is not None else sites


def _local_sites(scene, domains, problem, settings, decimals):
    """从网格最近可见点优化非关键车的停车位置，补入连续细化站点。"""
    margin = settings["practical_station_gap_m"] if decimals is not None else 0.0
    pad = math.sqrt(2) * 0.5 * 10**-decimals if decimals is not None else 0.0
    wall = settings.get("wall_clearance_m", 0.0)
    added = []
    for domain, job in zip(domains, problem.jobs):
        points = job.points[np.all(domain.clearance(job.points) >= margin, axis=1)]
        for home in scene.origins:
            if np.all(domain.clearance(home) >= max(margin, settings["nominal_visibility_gap_m"])):
                continue
            cost = np.linalg.norm(points - home, axis=1)
            for i in np.argsort(cost, kind="stable")[:3]:
                if cost[i] > scene.speed_m_s * 12.0:
                    continue
                los = margin + pad + settings["nominal_visibility_gap_m"]
                constraints = domain.local_constraints(points[i], margin + pad, los,
                                                       wall_clearance_m=wall + pad + 2 * PADDING_M if wall else 0.0)
                fit = minimize(lambda p: float(np.dot(p - home, p - home)), points[i], method="SLSQP",
                               constraints={"type": "ineq", "fun": constraints},
                               options={"ftol": 1e-11, "maxiter": 120})
                if fit.success and constraints(fit.x).min() >= -1e-9:
                    added.append(tuple(np.round(fit.x, decimals) if decimals is not None else fit.x))
    return added


def _site_index(problem, alert, point):
    """将站位原始坐标精确映射到已通过统一场地判定的候选表。"""
    matches = np.flatnonzero(np.all(problem.jobs[alert].points == point, axis=1))
    if len(matches) != 1:
        raise ValueError(f"连续细化站未通过可观测域判定：告警 {alert}，站点 {point}")
    return int(matches[0])


def _complete(scene, router, problem, domains, first, second, reused, partner, partner_site, chain, margin):
    """固定两段关键站，枚举其他三车的并行分工并按总路长破并列。"""
    first_site, second_site = (_site_index(problem, a, p) for a, p in zip((first, second), chain))
    partner_site = _site_index(problem, first, partner_site)
    if not problem.pair_mask(first, np.array([first_site]), np.array([partner_site]))[0, 0]:
        raise RuntimeError("细化后首告警双站不兼容")
    a, b = problem.jobs[first], problem.jobs[second]
    first_ready = max(a.initial_distance[reused, first_site] / scene.speed_m_s + a.slew[first_site],
                      a.initial_distance[partner, partner_site] / scene.speed_m_s + a.slew[partner_site])
    transfer = router.route(tuple(chain[0]), tuple(chain[1])).length_m / scene.speed_m_s
    cap = first_ready + scene.observation_s + a.slew[first_site] + transfer + b.slew[second_site]
    parallel, = set(range(len(scene.targets))) - {first, second}
    remaining = set(range(len(scene.vehicles))) - {reused, partner}
    eligible = [np.all(domain.clearance(job.points) >= margin, axis=1) for domain, job in zip(domains, problem.jobs)]
    if not all(eligible[t][s] for t, s in ((first, first_site), (first, partner_site), (second, second_site))):
        raise RuntimeError("关键链站点的舍入后余量低于所选口径")

    def candidates(alert, vehicle):
        """筛出在关键链开测上界之前已就绪的站，保持同一站位余量。"""
        job = problem.jobs[alert]
        ready = job.initial_distance[vehicle] / scene.speed_m_s + job.slew
        return np.flatnonzero(eligible[alert] & (ready <= cap + SELECT_TOL_S))

    completed = []
    for v in sorted(remaining):
        options = candidates(second, v)
        compatible = options[problem.pair_mask(second, np.array([second_site]), options)[0]]
        x, y = sorted(remaining - {v})
        p, q = candidates(parallel, x), candidates(parallel, y)
        if not len(compatible) or not len(p) or not len(q):
            continue
        dist = problem.jobs[parallel].initial_distance
        costs = np.where(problem.pair_mask(parallel, p, q), dist[x, p, None] + dist[y, q], np.inf)
        i, j = np.unravel_index(costs.argmin(), costs.shape)
        if not math.isfinite(costs[i, j]):
            continue
        other = int(compatible[np.argmin(b.initial_distance[v, compatible])])
        pairs = {
            first: tuple(sorted(((reused, first_site), (partner, partner_site)))),
            parallel: ((x, int(p[i])), (y, int(q[j]))),
            second: tuple(sorted(((reused, second_site), (v, other)))),
        }
        plan = replay(problem, (first, parallel, second), tuple(pairs[t] for t in range(len(domains))))
        completed.append((float(costs[i, j] + b.initial_distance[v, other]), plan.latest_s, v, plan))
    if not completed:
        raise RuntimeError("复用链没有兼容的并行车次")
    return min(completed, key=lambda item: item[:3])[-1]


def _practical_q21(scene, check, router, domain, nominal, settings, audit_kwargs):
    """沿取等站的径向外移后取整，复算 T2 双站部署和舍入后的余量（m）。"""
    decimals = settings["practical_decimals"]
    margin = settings["practical_station_gap_m"]
    # 径向距离对平面位移是 1-Lipschitz。两坐标舍入最多缩短半个单元的对角线。
    extra_radius = margin + math.sqrt(2) * 0.5 * 10**-decimals
    result = {"target": "T2", "target_xyz_m": nominal["target_xyz_m"], "coordinate_decimals": decimals,
              "required_station_gap_m": margin, "theoretical_min_rmse_m": nominal["min_rmse_m"], "routes": []}
    margins = []
    for row in nominal["routes"]:
        v = row["vehicle"]
        direction = np.array(row["site_xy_m"]) - domain.center
        site = tuple(np.round(domain.center + direction * (1 + extra_radius / np.linalg.norm(direction)), decimals).tolist())
        origin = tuple(scene.origins[scene.vehicles.index(v)].tolist())
        path = router.route(origin, site)
        land, sight, pitch = map(float, domain.clearance(site)[0])
        if not path.waypoints or min(land, sight, pitch) < margin:
            raise ValueError(f"{v} 的 2（1）毫米部署不满足站位余量或没有路线")
        result["routes"].append({"vehicle": v, "site_xy_m": site, "waypoints": path.waypoints,
                                 "station_clearance_m": land, "sight_halfplane_gap_m": sight,
                                 "pitch_radial_gap_m": pitch})
        margins.append({"plan": "q21_practical", "target": "T2", "vehicle": v, "site_x_m": site[0], "site_y_m": site[1],
                        "parking_gap_m": land, "sight_halfplane_gap_m": sight, "pitch_radial_gap_m": pitch})
    audit = verify_q21(check, result, **audit_kwargs, waypoint_decimals=decimals)
    for row, leg in zip(result["routes"], audit["routes"], strict=True):
        row.update({k: leg[k] for k in ("length_m", "travel_s", "pitch_deg")})
    result.update({k: audit[k] for k in ("baseline_m", "angle_deg", "r1_m", "r2_m", "rmse_m")})
    result["rmse_gap_m"] = result["rmse_m"] - result["theoretical_min_rmse_m"]
    return result, audit, margins


def run_refinement(reference: Path, settings_path: Path, output: Path, root: Path, command: str) -> Path:
    """由已验收的 q2 初始解生成两小问的方案，独立检查后写新目录。"""
    begin = time.perf_counter()
    cfg = reference / "config.yaml"
    settings = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    for field in ("search_radius_m", "nominal_visibility_gap_m", "practical_station_gap_m"):
        if not math.isfinite(settings[field]) or settings[field] <= 0:
            raise ValueError(f"{field} 需为有限的正米数")
    decimals = settings["practical_decimals"]
    if type(decimals) is not int or not 0 <= decimals <= 9:
        raise ValueError("practical_decimals 需为 0 至 9 的整数")
    scene = read_scene(cfg)
    check = load_scene()
    wall = settings.get("wall_clearance_m", 0.0)
    safety = execution_wall_budget(settings) if "execution_error_bound_m" in settings else {}
    router = build_router(scene, scene.path_polygon_sides, wall_clearance_m=wall)
    audit_kwargs = {"require_vehicle_separation": True, "wall_clearance_m": wall}
    old_q21 = json.loads((reference / "q21_result.json").read_text(encoding="utf-8"))
    q21 = solve_fast_t2(scene, router, settings["nominal_visibility_gap_m"], wall_clearance_m=wall)
    q21_checks = {"reference": verify_q21(check, old_q21, require_vehicle_separation=True),
                  "optimized": verify_q21(check, q21, **audit_kwargs)}
    if not math.isclose(q21_checks["optimized"]["rmse_m"], old_q21["min_rmse_m"], abs_tol=1e-12, rel_tol=0):
        raise RuntimeError("2（1）快速部署没有保持一阶精度下界")
    domains = [StationDomain(t) for t in scene.target_cfg]
    practical_q21 = None
    if settings.get("practical_q21", False):
        practical_q21, q21_checks["practical"], practical_q21_margins = _practical_q21(
            scene, check, router, domains[scene.targets.index("T2")], q21, settings, audit_kwargs)
    with (reference / "q22_timeline.csv").open(newline="", encoding="utf-8") as stream:
        times = list(csv.DictReader(stream))
    routes = json.loads((reference / "q22_routes.json").read_text(encoding="utf-8"))
    previous = {}
    for name in ("optimized", "greedy"):
        previous[name] = verify_q22(check, [r for r in routes if r["plan"] == name],
                                   [r for r in times if r["plan"] == name], require_vehicle_separation=True)
    legs = previous["optimized"]["legs"]
    reused, = [v for v in scene.vehicles if sum(row["vehicle"] == v for row in legs) == 2]
    opening, closing = sorted((row for row in legs if row["vehicle"] == reused), key=lambda row: row["depart_s"])
    first, second = (scene.targets.index(row["target"]) for row in (opening, closing))
    partner, = [row for row in legs if row["target"] == opening["target"] and row["vehicle"] != reused]
    seeds = [opening["site"], closing["site"]]
    added = [tuple(r["destination"]) for r in routes] + list(scene.refinement_sites_m)
    grid = build_problem(scene, router, scene.grid_m, added)
    plans, summary, validations, margins, nearest_orders = [], [], {}, [], []
    reused_index, partner_index = (scene.vehicles.index(v) for v in (reused, partner["vehicle"]))
    for name, decimals in (("optimized", None), ("practical", decimals)):
        station = partner["site"]
        partner_ready = partner["turn_end_s"]
        if decimals is not None:
            station = tuple(np.round(station, decimals))
            route = router.route(tuple(scene.origins[partner_index]), station)
            partner_ready = route.length_m / scene.speed_m_s + domains[first].slew(station, scene.gimbal_deg_s)
        chain = _chain(scene, domains, reused_index, first, second, seeds, partner_ready, settings, decimals)
        nearby = _local_sites(scene, domains, grid, settings, decimals)
        pool = [*added, *map(tuple, chain), *nearby]
        # 实用解所有下发坐标均取毫米，连同最优站位一起重算距离、可见性和时间。
        steps = scene.grid_m
        if decimals is not None:
            pool.extend(tuple(p) for job in grid.jobs for p in np.round(job.points, decimals))
            pool = [tuple(np.round(point, decimals)) for point in pool]
            steps = ()
        problem = build_problem(scene, router, steps, pool)
        margin = settings["practical_station_gap_m"] if decimals is not None else 0.0
        plan = _complete(scene, router, problem, domains, first, second, reused_index,
                         partner_index, station, chain, margin)
        recovered = recover_plan(name, scene, problem, router, plan)
        route_decimals = decimals if practical_q21 is not None else None
        validations[name] = verify_q22(check, recovered["routes"], recovered["timeline"], **audit_kwargs,
                                       waypoint_decimals=route_decimals)
        plans.append(recovered)
        summary.append({"plan": name, "latest_start_s": recovered["latest_s"],
                        "total_length_m": recovered["total_length_m"], **recovered["start_times_s"],
                        "coordinate_decimals": decimals, "required_station_gap_m": margin})
        for row in recovered["assignments"]:
            xyz = row["site_x_m"], row["site_y_m"]
            land, sight, pitch = domains[scene.targets.index(row["target"])].clearance(xyz)[0]
            margins.append({**{key: row[key] for key in ("plan", "target", "vehicle", "site_x_m", "site_y_m")},
                            "parking_gap_m": float(land), "sight_halfplane_gap_m": float(sight),
                            "pitch_radial_gap_m": float(pitch)})
        if name == "optimized":
            # 同一候选、路由和时序上枚举六种处理顺序。整条连续轨迹有车距冲突时淘汰该顺序。
            feasible = []
            for nearest in greedy(problem):
                candidate = recover_plan("greedy", scene, problem, router, nearest)
                try:
                    audit = verify_q22(check, candidate["routes"], candidate["timeline"], **audit_kwargs)
                except CheckError as error:
                    nearest_orders.append({"order": " ".join(candidate["order"]), "valid": False, "reason": str(error)})
                    continue
                nearest_orders.append({"order": " ".join(candidate["order"]), "valid": True,
                                       "latest_start_s": candidate["latest_s"], "total_length_m": candidate["total_length_m"],
                                       **candidate["start_times_s"]})
                feasible.append((candidate, audit))
            if not feasible:
                raise RuntimeError("同候选集的六种就近基线均未通过连续验收")
            baseline, validations["greedy"] = min(feasible, key=lambda row: (row[0]["latest_s"], row[0]["total_length_m"]))
    if summary[0]["latest_start_s"] >= max(previous["optimized"]["observation_start_s"].values()):
        raise RuntimeError("标称连续细化未改善给定归档")

    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_refinement_")
    path = Path(tempfile.mkdtemp(prefix=stamp, dir=output))
    shutil.copy2(settings_path, path / "refinement_config.yaml")
    shutil.copy2(scene.field_file, path / "scene_config.yaml")
    config = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    config["scene_config"] = "scene_config.yaml"
    (path / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")
    write_json(path / "q21_result.json", q21)
    if practical_q21 is not None:
        write_json(path / "q21_practical.json", practical_q21)
        write_csv(path / "q21_practical_station_margins.csv", practical_q21_margins)
    write_csv(path / "q21_travel_comparison.csv", [{"plan": name, "rmse_m": audit["rmse_m"],
                                                   "vehicle": leg["vehicle"], "travel_s": leg["travel_s"],
                                                   "length_m": leg["length_m"], "x_m": leg["site"][0], "y_m": leg["site"][1]}
                                                  for name, audit in q21_checks.items() for leg in audit["routes"]])
    plans.append(baseline)
    summary.append({"plan": "greedy", "latest_start_s": baseline["latest_s"], "total_length_m": baseline["total_length_m"],
                    **baseline["start_times_s"], "coordinate_decimals": None, "required_station_gap_m": 0.0})
    write_csv(path / "q22_refinement.csv", summary)
    write_csv(path / "q22_greedy_orders.csv", nearest_orders)
    write_csv(path / "q22_station_margins.csv", margins)
    write_csv(path / "q22_assignments.csv", [r for plan in plans for r in plan["assignments"]])
    write_csv(path / "q22_timeline.csv", [r for plan in plans for r in plan["timeline"]])
    write_json(path / "q22_routes.json", [r for plan in plans for r in plan["routes"]])
    write_json(path / "validation.json", {"q21": q21_checks, "q22": validations, "q22_reference": previous,
                                          **({"wall_safety": safety} if safety else {})})
    write_json(path / "critical_chains.json", {plan["plan"]: critical_chain(plan) for plan in plans})
    write_plots(path, scene, practical_q21 or q21, [plans[1] if practical_q21 else plans[0], baseline], formats=("png", "pdf"))
    record = _metadata(root, command, "refinement", time.perf_counter() - begin)
    if safety:
        record["wall_safety"] = safety
        name = "src/shared/wall_safety.py"
        record["source_sha256"][name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    record["input_sha256"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (
        cfg, scene.field_file, settings_path, reference / "q22_routes.json", reference / "q22_timeline.csv",
        reference / "q21_result.json")}
    record["plot_input_sha256"] = {name: hashlib.sha256((path / name).read_bytes()).hexdigest() for name in (
        "scene_config.yaml", "q21_practical.json" if practical_q21 else "q21_result.json", "q22_routes.json",
        "q22_assignments.csv", "q22_timeline.csv")}
    write_json(path / "run_record.json", record)
    return path
