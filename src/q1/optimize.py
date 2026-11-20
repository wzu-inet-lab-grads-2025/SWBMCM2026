"""q1 解析距离下界的可达性、整圈核验及净空择优，供运行入口调用。
输入为题面配置，输出为最优圆、站位和含浮点余量的全域净空误差界。
单独核对 nohup python3 -B -m pytest -p no:cacheprovider checks/test_q1_optimize.py。
"""

import heapq
import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from scipy.optimize import brentq, minimize_scalar

from .config import Config
from .geometry import (
    StationCheck, check_los, check_vehicle_geometry, compute_clearance, evaluate_station,
    independently_check_station, point_rect_distance,
)
from .region import station_masks


@dataclass(frozen=True)
class OptimalCircle:
    """主目标最优圆半径（m）、可行和排除角段（弧度）及覆盖证据。"""

    radius: float
    intervals_rad: tuple[tuple[float, float], ...]
    vehicle_blocked_rad: tuple[float, float]
    los_blocked_rad: tuple[float, float]
    proof: dict
    scan_checks: tuple[dict, ...]


@dataclass(frozen=True)
class OptimumResult:
    """见证点、最终点、局部峰和次级全局上界，内部角度为弧度。"""

    circle: OptimalCircle
    witness: StationCheck
    selected: StationCheck
    selected_angle_rad: float
    clearance_terms: dict[str, float]
    active_clearance_constraints: tuple[str, ...]
    active_constraint_tolerance_m: float
    clearance_upper_bound: float
    clearance_gap: float
    rounding_margin_m: float
    tie_candidates: int
    branches: int
    bound_evaluations: int
    local_peaks: tuple[tuple[float, float], ...]
    witness_validation: dict
    validation: dict
    analytic_clearance: dict | None
    branch_bound_upper_bound: float


@dataclass(frozen=True)
class PrimaryOptimalExample:
    """完整最优弧上的代表站点，角度为弧度，坐标与净空为 m。"""

    role: str
    angle_rad: float
    station: StationCheck


def circle_xy(phi: float, cfg: Config) -> tuple[float, float]:
    """以弧度参数返回最优圆的车心坐标（m）。"""
    tx, ty, _ = cfg.target_xyz
    r = cfg.distance_lower_bound
    return tx + r * math.cos(phi), ty + r * math.sin(phi)


def _scan_switches(
    angles: np.ndarray, mask: np.ndarray, predicate: Callable[[float, float], bool], cfg: Config,
) -> tuple[float, ...]:
    """细化掩码相邻样本间的切换，输入角度为弧度，输出角度为度。"""
    changes = np.flatnonzero(mask != np.roll(mask, -1))
    endpoints = []
    tolerance = math.radians(cfg.numerics.angle_check_deg) / 8
    for index in changes:
        a = float(angles[index])
        b = float(angles[index + 1]) if index + 1 < angles.size else math.tau
        left_passes = bool(mask[index])
        while b - a > tolerance:
            mid = (a + b) / 2
            if mid == a or mid == b:
                break
            if predicate(*circle_xy(mid, cfg)) == left_passes:
                a = mid
            else:
                b = mid
        endpoints.append(math.degrees((a + b) / 2))
    return tuple(endpoints)


def _scan_circle(step_deg: float, cfg: Config) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """全圆分别扫描联合可行性与 LOS，返回两组切换角度（度）。"""
    angles = np.deg2rad(np.arange(0.0, 360.0, step_deg))
    tx, ty, _ = cfg.target_xyz
    r = cfg.distance_lower_bound
    vehicle, _, clear = station_masks(tx + r * np.cos(angles), ty + r * np.sin(angles), cfg)

    def clear_los(x: float, y: float) -> bool:
        """检查 m 制站点到目标的 LOS，无视车体是否能停放。"""
        return check_los(x, y, cfg)[0]

    def feasible_on_circle(x: float, y: float) -> bool:
        """圆周已满足理论仰角，只需核对车体与有限视线。"""
        return check_vehicle_geometry(x, y, cfg) and clear_los(x, y)

    return (
        _scan_switches(angles, vehicle & clear, feasible_on_circle, cfg),
        _scan_switches(angles, clear, clear_los, cfg),
    )


def find_feasible_angle_intervals(cfg: Config) -> OptimalCircle:
    """核对官方几何的整圈覆盖并作双步长扫描，覆盖条件失败时报错。"""
    x, y, h = cfg.target_xyz
    r, rho = cfg.distance_lower_bound, cfg.vehicle_radius
    west, south = x - r, y - r
    if not (rho <= west and x + r <= cfg.field_width - rho and rho <= south and y + r <= cfg.field_height - rho):
        raise ValueError("q1 整圈覆盖失效：最优圆跨越车心场界")
    relevant = [b for b in cfg.buildings if point_rect_distance(x, y, b) <= r + rho]
    if len(relevant) != 1 or relevant[0].id != "B2":
        raise ValueError("q1 整圈覆盖失效：需核对与最优圆相遇的建筑")
    b = relevant[0]
    north_edge = b.y_max + rho
    east_edge = b.x_max + rho
    if not (b.x_min <= west < b.x_max and b.y_min <= south and y <= b.y_max < north_edge < y + r and west < east_edge < x):
        raise ValueError("q1 整圈覆盖失效：最优圆与 B2 的相对位置改变")

    # 在北侧交点到 180° 间，x 位于楼面宽度内且 y 逐渐降低。
    # 在 180° 到东侧交点间，y 位于建筑底面的南北范围内且 x 逐渐增加。
    # 其余角度均在北侧切线以北或东侧切线以东，覆盖整圈且两个切点均闭合。
    north_phi = math.pi - math.asin((north_edge - y) / r)
    east_phi = math.tau - math.acos((east_edge - x) / r)
    north_x = x - math.sqrt(r**2 - (north_edge - y)**2)
    east_y = y - math.sqrt(r**2 - (east_edge - x)**2)
    if not (b.x_min <= north_x <= b.x_max and b.y_min <= east_y <= b.y_max):
        raise ValueError("q1 整圈覆盖失效：切点未落在预期边段")

    # 从目标反向量水平射线，低于 B2 屋顶的段从 roof_radius 开始。
    # B2 最靠近目标的横向距离更大，故圆内的 B2 投影交点均低于屋顶。
    x_gap = x - b.x_max
    roof_radius = r * (h - b.height) / (h - cfg.camera_height)
    if not (cfg.camera_height < b.height < h and 0 < roof_radius < x_gap < r):
        raise ValueError("q1 整圈覆盖失效：B2 楼顶位置需要单独核对")
    east_face_angle = math.acos(-x_gap / r)
    corner_angle = math.pi - math.atan2(b.y_max - y, x_gap)
    los_blocked = (max(east_face_angle, corner_angle), math.tau - east_face_angle)
    if not north_phi < los_blocked[0] < los_blocked[1] < east_phi:
        raise ValueError("q1 整圈覆盖失效：LOS 遮挡超出车体禁入弧")
    if corner_angle >= east_face_angle:
        # 入遮挡弧时有限光线与东、北两面相交的棱接触。
        contact_height = h - (h - cfg.camera_height) * math.hypot(x_gap, b.y_max - y) / r
        contact_start = (b.x_max, b.y_max, contact_height)
    else:
        contact_start = (b.x_max, y + math.sqrt(r**2 - x_gap**2), cfg.camera_height)
    contact_end = (b.x_max, y - math.sqrt(r**2 - x_gap**2), cfg.camera_height)
    if not all(b.y_min <= cy <= b.y_max and 0 <= cz <= b.height for _, cy, cz in (contact_start, contact_end)):
        raise ValueError("q1 整圈覆盖失效：LOS 接触点不在建筑闭面")

    # 保持北、东两条相切直线的精确坐标，只在各自切线方向修正距离舍入。
    north_tangent, _ = _checked_circle_xy((north_x, north_edge), 0, cfg)
    east_tangent, _ = _checked_circle_xy((east_edge, east_y), 1, cfg)

    exact_deg = (math.degrees(north_phi), math.degrees(east_phi))
    exact_los_deg = tuple(map(math.degrees, los_blocked))
    scans = []
    for step in (cfg.numerics.arc_scan_deg, cfg.numerics.arc_scan_deg / 2):
        endpoints, los_switches = _scan_circle(step, cfg)
        if len(endpoints) != len(exact_deg) or len(los_switches) != len(exact_los_deg):
            raise ValueError(f"角度步长 {step}° 的扫描切换数与解析覆盖不一致")
        difference = max(abs(a - b) for a, b in zip(endpoints, exact_deg))
        los_difference = max(abs(a - b) for a, b in zip(los_switches, exact_los_deg))
        if max(difference, los_difference) > cfg.numerics.angle_check_deg:
            raise ValueError(f"角度步长 {step}° 的扫描端点超出误差阈值")
        scans.append({
            "step_deg": step, "endpoints_deg": endpoints, "max_endpoint_difference_deg": difference,
            "los_switches_deg": los_switches, "max_los_difference_deg": los_difference,
        })
    proof = {
        "only_relevant_building": b.id,
        "circle_bounds_xy_m": [[west, x + r], [south, y + r]],
        "north_tangent_xy_m": (north_tangent.x, north_tangent.y),
        "east_tangent_xy_m": (east_tangent.x, east_tangent.y),
        "los_roof_radius_m": roof_radius,
        "los_minimum_horizontal_crossing_radius_m": x_gap,
        "los_within_vehicle_exclusion": True,
        "los_contacts_xyz_m": (contact_start, contact_end),
    }
    return OptimalCircle(r, ((0.0, north_phi), (east_phi, math.tau)), (north_phi, east_phi), los_blocked, proof, tuple(scans))


def _checked_circle_xy(xy: tuple[float, float], axis: int, cfg: Config) -> tuple[StationCheck, dict]:
    """按指定坐标轴修正最优圆浮点舍入，并独立检查全部约束。"""
    position = list(xy)
    tx, ty, _ = cfg.target_xyz
    outward = math.copysign(math.inf, xy[axis] - (tx, ty)[axis])
    # 数学点在闭仰角边界。若坐标舍入朝圆内，沿允许的轴向外移一个 ULP，
    # 让可复现的浮点坐标通过与任意站点相同的原题判据。
    for _ in range(4):
        station = evaluate_station(*position, cfg)
        validation = independently_check_station(*position, cfg)
        if station.gimbal_ok and validation["gimbal_ok"]:
            if not station.feasible or not validation["vehicle_ok"] or not validation["los_clear"]:
                raise ValueError("圆周候选站点未通过车辆或 LOS 独立复核")
            if abs(station.horizontal_distance - cfg.distance_lower_bound) > cfg.numerics.distance_check_m:
                raise ValueError("圆周站点距离复算超出配置精度")
            return station, validation
        position[axis] = math.nextafter(position[axis], outward)
    raise ValueError("圆周站点的浮点坐标无法通过原题仰角端点检查")


def _checked_on_circle(phi: float, cfg: Config) -> tuple[StationCheck, dict]:
    """按照弧度方位生成圆周站位，优先沿主要径向分量处理舍入。"""
    x, y = circle_xy(phi, cfg)
    tx, ty, _ = cfg.target_xyz
    axis = 0 if abs(x - tx) >= abs(y - ty) else 1
    return _checked_circle_xy((x, y), axis, cfg)


def primary_optimal_examples(result: OptimumResult, cfg: Config) -> tuple[PrimaryOptimalExample, ...]:
    """返回已复核的最优弧端点、弧内示例及推荐点；未通过几何核验时报错。"""
    north_phi, east_phi = result.circle.vehicle_blocked_rad
    north, _ = _checked_on_circle(math.pi / 2, cfg)
    # 使用证书中的切点坐标，避免将舍入后的端点角直接回代而误判相切。
    north_tangent, _ = _checked_circle_xy(result.circle.proof["north_tangent_xy_m"], 0, cfg)
    east_tangent, _ = _checked_circle_xy(result.circle.proof["east_tangent_xy_m"], 1, cfg)
    examples = [
        PrimaryOptimalExample("east_witness", 0.0, result.witness),
        PrimaryOptimalExample("north", math.pi / 2, north),
        PrimaryOptimalExample("north_tangent", north_phi, north_tangent),
        PrimaryOptimalExample("east_tangent", east_phi, east_tangent),
    ]
    southern_peak = max((peak for peak in result.local_peaks if peak[0] > math.pi), key=lambda peak: peak[1], default=None)
    if southern_peak is not None:
        phi = southern_peak[0]
        station, _ = _checked_on_circle(phi, cfg)
        examples.append(PrimaryOptimalExample("southern_local_peak", phi, station))

    selected = PrimaryOptimalExample("selected", result.selected_angle_rad, result.selected)
    return tuple(row for row in examples if row.station != selected.station) + (selected,)


def _clearance(phi: float, cfg: Config) -> float:
    """按圆周参数返回车辆最小净空（m）。"""
    return compute_clearance(*circle_xy(phi, cfg), cfg)[0]


def _sample_peaks(circle: OptimalCircle, cfg: Config) -> tuple[tuple[float, float], ...]:
    """从扫描的局部峰细化候选，活跃净空切换时复核两项交点。"""
    step = math.radians(cfg.numerics.arc_scan_deg)
    angular_tolerance = math.radians(cfg.numerics.angle_check_deg) / 8
    peaks = []
    # 用一条连续弧包含 360° 同一点，避免在 0° 把潜在邻域峰拆开。
    start, stop = circle.intervals_rad[1][0], math.tau + circle.intervals_rad[0][1]
    angles = np.linspace(start, stop, math.ceil((stop - start) / step) + 1)
    values = np.array([_clearance(phi, cfg) for phi in angles])
    maxima = np.flatnonzero((values[1:-1] >= values[:-2]) & (values[1:-1] > values[2:])) + 1
    for index in maxima:
        left, right = float(angles[index - 1]), float(angles[index + 1])
        result = minimize_scalar(
            lambda phi: -_clearance(phi, cfg), bounds=(left, right), method="bounded",
            options={"xatol": angular_tolerance},
        )
        candidates = [float(angles[index]), float(result.x)] if result.success else [float(angles[index])]
        _, before = compute_clearance(*circle_xy(left, cfg), cfg)
        _, after = compute_clearance(*circle_xy(right, cfg), cfg)
        active_before = min(before, key=before.get)
        active_after = min(after, key=after.get)
        if active_before != active_after:
            def difference(phi: float) -> float:
                """返回左右活跃净空的差（m），用于寻找几何折点。"""
                _, terms = compute_clearance(*circle_xy(phi, cfg), cfg)
                return terms[active_before] - terms[active_after]

            candidates.append(brentq(difference, left, right, xtol=angular_tolerance))
        selected = max(candidates, key=lambda phi: _clearance(phi, cfg)) % math.tau
        peaks.append((selected, _clearance(selected, cfg)))
    return tuple(sorted(peaks))


def _analytic_clearance_bound(cfg: Config, circle: OptimalCircle, padding: float) -> dict | None:
    """用东场界与 B2 东北角证明整圆的净空上界，条件失效时交给分支定界。"""
    tx, ty, _ = cfg.target_xyz
    radius, rho = circle.radius, cfg.vehicle_radius
    building = next(b for b in cfg.buildings if b.id == circle.proof["only_relevant_building"])
    east_limit = cfg.field_width - rho
    east_corner_x, north_corner_y = building.x_max, building.y_max
    if not east_corner_x < tx or not building.y_min <= ty - radius <= ty <= north_corner_y:
        return None

    def northeast_clearance(x: float) -> float:
        """计算上半圆一点到 B2 东北角的净空上界（m）。"""
        y = ty + math.sqrt(max(0.0, radius**2 - (x - tx)**2))
        return math.hypot(x - east_corner_x, y - north_corner_y) - rho

    def balance(x: float) -> float:
        """返回东场界净空与东北角净空的差（m）。"""
        return east_limit - x - northeast_clearance(x)

    if not balance(tx) > 0 > balance(tx + radius):
        return None

    # 任何分割 x0 均给出可检验的上界。二分只用于找到平衡两侧上界的位置。
    x0 = brentq(balance, tx, tx + radius, xtol=4 * math.ulp(tx))
    phi = math.acos((x0 - tx) / radius)
    point, _ = _checked_on_circle(phi, cfg)
    x0 = point.x
    # x<=x0 的上半圆到东北角的距离平方为线性项减去非负系数乘 sqrt，
    # 在整个左区间上是凸函数，最大值在最西点或分割点。角点距离给实际楼距上界。
    west = northeast_clearance(tx - radius)
    upper_left = northeast_clearance(x0)
    lower_left = max(0.0, x0 - east_corner_x) - rho
    # 右区间 x>=x0 的最小净空受东场界约束。
    east = east_limit - x0
    upper = max(west, upper_left, lower_left, east) + padding
    if upper - point.clearance_min + padding > cfg.numerics.clearance_gap_m:
        return None
    return {
        "angle_rad": phi,
        "split_x_m": x0,
        "west_corner_upper_m": west,
        "upper_semicircle_at_split_upper_m": upper_left,
        "lower_semicircle_left_upper_m": lower_left,
        "right_field_upper_m": east,
        "whole_circle_upper_m": upper,
        "clearance_at_candidate_m": point.clearance_min,
    }


def solve_optimum(cfg: Config) -> OptimumResult:
    """验证解析最优值可达，在全部可行圆弧上给出净空择优及全域差界。"""
    witness, witness_validation = _checked_on_circle(0.0, cfg)
    circle = find_feasible_angle_intervals(cfg)
    peaks = _sample_peaks(circle, cfg)
    tolerance = cfg.numerics.clearance_gap_m
    # 为端点、三角函数、距离和上界加减累计的浮点误差留出余量。
    padding = 32 * math.ulp(max(cfg.field_width, cfg.field_height, cfg.target_xyz[2]))
    if tolerance <= 4 * padding:
        raise ValueError("净空全局误差要求低于当前浮点余量")
    candidates = dict(peaks)
    for a, b in circle.intervals_rad:
        for phi in (a, b % math.tau):
            candidates[phi] = _clearance(phi, cfg)
    initial_best = max(candidates.values())
    queue = []
    evaluations = 0
    best = initial_best

    def bound(a: float, b: float) -> None:
        """为一个弧度区间加入净空上界（m），并保留中点候选。"""
        nonlocal evaluations, best
        mid = (a + b) / 2
        c = _clearance(mid, cfg)
        candidates[mid] = c
        best = max(best, c)
        # 点到闭矩形与场界的距离是 1-Lipschitz，圆周弧长为 r×弧度。
        upper = c + circle.radius * max(mid - a, b - mid) + padding
        heapq.heappush(queue, (-upper, a, b))
        evaluations += 1

    for a, b in circle.intervals_rad:
        bound(a, b)
    branches = 0
    while -queue[0][0] - best + padding > tolerance / 2:
        _, a, b = heapq.heappop(queue)
        mid = (a + b) / 2
        if mid == a or mid == b:
            raise ValueError("区间已达到浮点分辨率，仍无法验证净空全局误差界")
        bound(a, mid)
        bound(mid, b)
        branches += 1
    branch_bound_upper = -queue[0][0]
    analytic = _analytic_clearance_bound(cfg, circle, padding)
    if analytic is not None:
        candidates[analytic["angle_rad"]] = analytic["clearance_at_candidate_m"]
    upper_bound = min(branch_bound_upper, analytic["whole_circle_upper_m"]) if analytic else branch_bound_upper
    # 先比较已验证的净空。只有差异落入舍入余量时才按 x、y 排序。
    best_clearance = max(candidates.values())
    tied = [phi for phi, value in candidates.items() if best_clearance - value <= padding
            and upper_bound - value + 2 * padding <= tolerance]
    chosen_phi = min(tied, key=lambda phi: circle_xy(phi, cfg))
    selected, validation = _checked_on_circle(chosen_phi, cfg)
    clearance, terms = compute_clearance(selected.x, selected.y, cfg)
    gap = max(0.0, upper_bound - clearance + padding)
    if gap > tolerance:
        raise ValueError("选中站点的净空全域误差界超出验收阈值")
    # 对并列候选取整和坐标排序后的站位，用数值余量识别近乎同时起作用的边界。
    active_tolerance = 2 * tolerance
    active = tuple(name for name, value in terms.items() if value - clearance <= active_tolerance)
    return OptimumResult(
        circle, witness, selected, chosen_phi, terms, active, active_tolerance, upper_bound, gap, padding,
        len(tied), branches, evaluations, peaks, witness_validation, validation, analytic, branch_bound_upper,
    )
