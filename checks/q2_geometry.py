"""B 题第二问独立几何验收：输入既有站点与折线路线，核查并重算指标。
供 checks.q2_validate 调用，不搜索站点或路径。
测试：nohup python3 -B -m pytest -q -p no:cacheprovider checks/test_q2_validate.py
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol（版本发布日期未提供），2026-11-20。
"""

from dataclasses import dataclass
from fractions import Fraction
from itertools import combinations, pairwise, product
import math
from pathlib import Path

from src.q1.config import Building, compute_target_xyz, load_config


Point = tuple[float, float]
Point3 = tuple[float, float, float]


class CheckError(ValueError):
    """固定方案的输入格式、几何或时序不合格。"""


@dataclass(frozen=True)
class Scene:
    """验收常量；长度 m、速度 m/s、角度度、时间 s。"""

    width_m: float
    height_m: float
    radius_m: float
    camera_height_m: float
    elevation_deg: tuple[float, float]
    buildings: tuple[Building, ...]
    vehicles: dict[str, Point]
    targets: dict[str, Point3]
    speed_m_s: float = 3.0
    gimbal_speed_deg_s: float = 30.0
    observation_s: float = 5.0
    bearing_sigma_deg: float = 1.0


def load_scene() -> Scene:
    """用仓库场地配置与原题第 1–3 页的车位和告警构造验收场景。"""
    config = load_config(Path(__file__).resolve().parents[1] / "configs/q1.yaml")
    vehicles = dict(zip(("V1", "V2", "V3", "V4", "V5"), ((15, 14), (88, 10), (14, 86), (88, 68), (50, 93))))
    alerts = {"T1": (30, 40, 45), "T2": (150, 45, 35), "T3": (285, 38, 55)}
    targets = {key: compute_target_xyz(config.dispatch_center, *alert) for key, alert in alerts.items()}
    return Scene(config.field_width, config.field_height, config.vehicle_radius, config.camera_height,
                 config.elevation_bounds_deg, config.buildings, vehicles, targets)


def xy(values) -> Point:
    """读有限的 x、y 坐标（m）；拒绝缺项、字符串和 NaN 等。"""
    if not isinstance(values, (tuple, list)) or len(values) != 2:
        raise CheckError(f"不是二维坐标：{values!r}")
    if any(type(value) not in (float, int) or not math.isfinite(value) for value in values):
        raise CheckError(f"坐标必须为有限数值：{values!r}")
    return float(values[0]), float(values[1])


def _box_intersects(a: tuple, b: tuple, lower: tuple, upper: tuple) -> bool:
    """有理数 Slab 比较闭线段和闭盒，单点重合也算相交。"""
    enter, leave = Fraction(0), Fraction(1)
    for p, q, lo, hi in zip(a, b, lower, upper):
        if p == q:
            if not lo <= p <= hi:
                return False
        else:
            t, u = (lo - p) / (q - p), (hi - p) / (q - p)
            enter, leave = max(enter, min(t, u)), min(leave, max(t, u))
            if enter > leave:
                return False
    return True


def _rectangle_distance_sq(a: Point, b: Point, building: Building) -> Fraction:
    """求整段到闭建筑底面的最小平方距离（m²），不是只查端点。"""
    start, end = (tuple(map(Fraction, point)) for point in (a, b))
    lo, hi = (tuple(map(Fraction, point[:2])) for point in building.bounds)
    if _box_intersects(start, end, lo, hi):
        return Fraction(0)

    # 不相交时，最近处至少可取在其中一图形的端点/顶点；平行边同样如此。
    end_distances = [sum(max(l - x, 0, x - h)**2 for x, l, h in zip(p, lo, hi)) for p in (start, end)]
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_sq = dx*dx + dy*dy
    if not length_sq:
        return min(end_distances)
    corner_distances = []
    for x, y in product((lo[0], hi[0]), (lo[1], hi[1])):
        ux, uy = x - start[0], y - start[1]
        t = min(1, max(0, (ux*dx + uy*dy) / length_sq))
        corner_distances.append((ux - t*dx)**2 + (uy - t*dy)**2)
    return min(end_distances + corner_distances)


def _check_field(scene: Scene, site: Point) -> None:
    """车辆是圆盘；约束圆心位于内缩场地闭矩形内。"""
    x, y = site
    rho = scene.radius_m
    if not (rho <= x <= scene.width_m - rho and rho <= y <= scene.height_m - rho):
        raise CheckError(f"车辆圆盘越出场界：{site}")


def check_route(scene: Scene, waypoints, origin: Point, *, wall_clearance_m: float = 0.0) -> dict:
    """复核闭场地及各段楼距，按 m 要求车体净空，返回路长、时间和逐楼最小间距。"""
    if not math.isfinite(wall_clearance_m) or wall_clearance_m < 0:
        raise CheckError("车体净空须为有限非负米数")
    if not isinstance(waypoints, (list, tuple)) or not waypoints:
        raise CheckError("路线至少需要一个路点，原地停车可只写起点")
    points = tuple(map(xy, waypoints))
    if points[0] != origin:
        raise CheckError(f"路线起点 {points[0]} 与既定位置 {origin} 不一致")
    # 内缩场地凸，路点都在闭场界内即可保证折线各段不越界。
    required_radius = Fraction(scene.radius_m) + Fraction(wall_clearance_m)
    if wall_clearance_m:
        east = Fraction(scene.width_m) - required_radius
        north = Fraction(scene.height_m) - required_radius
        for x, y in points:
            if not (required_radius <= x <= east and required_radius <= y <= north):
                raise CheckError(f"车体到场界的净空不足 {wall_clearance_m:g} m：{(x, y)}")
    else:
        for point in points:
            _check_field(scene, point)
    segments = tuple(pairwise(points)) or ((points[0], points[0]),)
    clearances = {}
    radius_sq = required_radius**2
    for building in scene.buildings:
        dist_sq = min(_rectangle_distance_sq(a, b, building) for a, b in segments)
        if dist_sq < radius_sq:
            if wall_clearance_m:
                raise CheckError(f"车体到建筑 {building.id} 的净空不足 {wall_clearance_m:g} m")
            raise CheckError(f"车辆圆盘侵入建筑 {building.id}，不是允许的相切")
        clearances[building.id] = math.sqrt(dist_sq)
    length_m = math.fsum(math.dist(a, b) for a, b in segments)
    return {"waypoints": points, "length_m": length_m, "travel_s": length_m / scene.speed_m_s,
            "min_distance_to_buildings_m": clearances}


def station_pointing(scene: Scene, site: Point, target: Point3) -> dict:
    """检查站位、闭仰角与有限三维视线，返回方位、仰角（度）和指向时间（s）。"""
    site = xy(site)
    _check_field(scene, site)
    dx, dy = target[0] - site[0], target[1] - site[1]
    r = math.hypot(dx, dy)
    pitch = math.degrees(math.atan2(target[2] - scene.camera_height_m, r))
    lower, upper = scene.elevation_deg
    if r == 0 or not lower <= pitch <= upper:
        raise CheckError(f"水平距离为零或仰角 {pitch:.15g}° 不在闭区间 [{lower}, {upper}]°")
    camera, goal = (tuple(map(Fraction, point)) for point in ((*site, scene.camera_height_m), target))
    for building in scene.buildings:
        if _rectangle_distance_sq(site, site, building) < Fraction(scene.radius_m)**2:
            raise CheckError(f"观测站车辆圆盘侵入建筑 {building.id}")
        lo, hi = (tuple(map(Fraction, point)) for point in building.bounds)
        # 遮挡用原三维闭楼体，不能用车辆膨胀轮廓或二维投影替代。
        if _box_intersects(camera, goal, lo, hi):
            raise CheckError(f"视线与建筑 {building.id} 棱柱相接或相交")
    yaw = math.degrees(math.atan2(dy, dx))
    return {"yaw_deg": yaw, "pitch_deg": pitch, "range_m": r,
            "slew_s": max(abs(yaw), abs(pitch)) / scene.gimbal_speed_deg_s}


def pair_metrics(scene: Scene, first: Point, second: Point, target: Point3) -> dict:
    """重算固定双站的基线、实际角度、距离和一阶二维 RMSE；拒绝共线。"""
    qx, qy = map(Fraction, target[:2])
    (ax, ay), (bx, by) = ((qx - Fraction(x), qy - Fraction(y)) for x, y in (first, second))
    cross, dot = abs(ax*by - ay*bx), ax*bx + ay*by
    if cross == 0:
        raise CheckError("两站对目标共线（包括同站），不能交会")
    r1, r2 = math.hypot(ax, ay), math.hypot(bx, by)
    sine = float(cross) / (r1*r2)
    if sine == 0:
        raise CheckError("浮点精度不足以分辨该交会角")
    baseline = math.dist(first, second)
    return {"baseline_m": baseline, "angle_deg": math.degrees(math.atan2(cross, dot)),
            "r1_m": r1, "r2_m": r2, "sin_gamma": sine,
            "rmse_m": math.radians(scene.bearing_sigma_deg)*math.hypot(r1, r2)/sine,
            "station_disks_overlap": baseline < 2*scene.radius_m}


@dataclass(frozen=True)
class _Motion:
    """一段匀速运动；时间 s、平面端点 m，首尾相同时为停车。"""

    start_s: float
    end_s: float
    origin: Point
    destination: Point

    def position(self, time_s: float) -> Point:
        """返回该段内时刻的车心；保留端点原值。"""
        if time_s <= self.start_s:
            return self.origin
        if time_s >= self.end_s:
            return self.destination
        u = (time_s - self.start_s) / (self.end_s - self.start_s)
        ax, ay = self.origin
        bx, by = self.destination
        return ax + u * (bx - ax), ay + u * (by - ay)


def _motion_intervals(origin: Point, legs: list[dict], speed_m_s: float, until_s: float) -> list[_Motion]:
    """把已验收的逐车路线和停车窗展开为空间线段及其实际占用时段。"""
    motions = []
    time_s, site = 0.0, origin
    for leg in legs:
        depart = leg["depart_s"]
        if time_s < depart:
            motions.append(_Motion(time_s, depart, site, site))
        steps = [(a, b, math.dist(a, b)) for a, b in pairwise(leg["waypoints"]) if a != b]
        traversed_lengths = []
        time_s = depart
        for index, (a, b, length) in enumerate(steps):
            traversed_lengths.append(length)
            end = (leg["arrive_s"] if index == len(steps) - 1
                   else depart + math.fsum(traversed_lengths) / speed_m_s)
            if time_s < end:
                motions.append(_Motion(time_s, end, a, b))
            time_s = end
        time_s, site = leg["arrive_s"], leg["site"]
    if time_s < until_s:
        motions.append(_Motion(time_s, until_s, site, site))
    return motions


def check_vehicle_separation(scene: Scene, legs_by_vehicle: dict[str, list[dict]], until_s: float) -> dict:
    """复核连续时间车心最小距离（m）；含空闲车，不对轨迹作调整。"""
    paths = {vehicle: _motion_intervals(origin, legs_by_vehicle[vehicle], scene.speed_m_s, until_s)
             for vehicle, origin in scene.vehicles.items()}
    pair_minima = []
    for first, second in combinations(sorted(paths), 2):
        best = (math.inf, 0.0)
        for a, b in product(paths[first], paths[second]):
            begin, end = max(a.start_s, b.start_s), min(a.end_s, b.end_s)
            if begin > end:
                continue
            pa, pb, qa, qb = a.position(begin), b.position(begin), a.position(end), b.position(end)
            x, y = pa[0] - pb[0], pa[1] - pb[1]
            dx, dy = qa[0] - qb[0] - x, qa[1] - qb[1] - y
            # 同一时段内相对位置是线段，投影后截到 [0,1]，不能仅采样事件端点。
            norm_sq = dx*dx + dy*dy
            u = min(1.0, max(0.0, -(x*dx + y*dy) / norm_sq)) if norm_sq else 0.0
            best = min(best, (math.hypot(x + u*dx, y + u*dy), begin + u*(end - begin)))
        pair_minima.append({"vehicles": [first, second], "distance_m": best[0], "time_s": best[1]})
    diameter = 2 * scene.radius_m
    return {"minimum_required_center_distance_m": diameter, "pair_minima": pair_minima,
            "overlapping_pairs": [result for result in pair_minima if result["distance_m"] < diameter]}
