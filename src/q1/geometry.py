"""q1 站点的车辆、仰角、有限视线判定，供网格、优化及统一入口调用。
输入为题面配置与 m 制站点坐标，输出为可行性、距离、角度及净空。
单独核对 nohup python3 -B -m pytest -p no:cacheprovider checks/test_q1_geometry.py。
"""

import math
from dataclasses import dataclass
from fractions import Fraction

import numpy as np
from scipy.spatial import ConvexHull

from .config import Building, Config, Numerics


@dataclass(frozen=True)
class StationCheck:
    """三个条件的独立判定、水平距离（m）、仰角（度）与净空（m）。"""

    x: float
    y: float
    horizontal_distance: float
    elevation_deg: float
    vehicle_ok: bool
    gimbal_ok: bool
    los_clear: bool
    blocked_by: tuple[str, ...]
    clearance_min: float

    @property
    def feasible(self) -> bool:
        """三种约束均满足时返回真。"""
        return self.vehicle_ok and self.gimbal_ok and self.los_clear


def point_rect_distance(x: float, y: float, b: Building) -> float:
    """返回点到闭建筑底面的欧氏距离（m），底面内与边界上返回 0。"""
    return math.hypot(max(b.x_min - x, 0.0, x - b.x_max), max(b.y_min - y, 0.0, y - b.y_max))


def disk_intrudes_rectangle(x: float, y: float, radius: float, b: Building) -> bool:
    """精确比较输入浮点坐标的平方距离，圆盘仅相切时返回假。"""
    x_exact, y_exact = Fraction(x), Fraction(y)
    x_min, x_max, y_min, y_max = map(Fraction, (b.x_min, b.x_max, b.y_min, b.y_max))
    dx = max(x_min - x_exact, 0, x_exact - x_max)
    dy = max(y_min - y_exact, 0, y_exact - y_max)
    return dx * dx + dy * dy < Fraction(radius) ** 2


def field_clearances(x: float, y: float, cfg: Config) -> dict[str, float]:
    """返回圆盘外缘到四条场界的净空（m），负值表示越出场地。"""
    rho = cfg.vehicle_radius
    return {
        "field_west": x - rho,
        "field_east": (cfg.field_width - rho) - x,
        "field_south": y - rho,
        "field_north": (cfg.field_height - rho) - y,
    }


def check_vehicle_geometry(x: float, y: float, cfg: Config) -> bool:
    """判断完整圆盘是否在场内且未侵入楼体，允许与场界和楼体相切。"""
    if min(field_clearances(x, y, cfg).values()) < 0:
        return False
    rho = cfg.vehicle_radius
    for b in cfg.buildings:
        gap = point_rect_distance(x, y, b) - rho
        # 题面允许相切，临界时精确复核而非放宽 1.5 m 的几何条件。
        if abs(gap) <= cfg.numerics.position_review_m:
            if disk_intrudes_rectangle(x, y, rho, b):
                return False
        elif gap < 0:
            return False
    return True


def gimbal_feasible_at_distance(horizontal_distance: float, cfg: Config) -> bool:
    """返回 T1 标称仰角是否合规，水平距离（m）等于解析下界时通过。"""
    return horizontal_distance >= cfg.distance_lower_bound


def intersects_closed_box_by_faces(
    start: tuple[float, ...], end: tuple[float, ...],
    lower: tuple[float, ...], upper: tuple[float, ...],
) -> bool:
    """以有理数检查端点与各楼面交点，闭盒接触时返回真。"""
    p0, p1, lo, hi = (tuple(map(Fraction, v)) for v in (start, end, lower, upper))
    # 从盒外进入闭盒必经过至少一个面。端点在盒内时也覆盖零长线段。
    if any(all(lo[i] <= point[i] <= hi[i] for i in range(3)) for point in (p0, p1)):
        return True
    direction = tuple(b - a for a, b in zip(p0, p1))
    for axis, delta in enumerate(direction):
        if delta == 0:
            continue
        for face in (lo[axis], hi[axis]):
            t = (face - p0[axis]) / delta
            if 0 <= t <= 1 and all(lo[i] <= p0[i] + t * direction[i] <= hi[i] for i in range(3)):
                return True
    return False


def segment_aabb_intersects(
    start: tuple[float, ...], end: tuple[float, ...],
    lower: tuple[float, ...], upper: tuple[float, ...], numerics: Numerics,
) -> bool:
    """用 Slab 区间判断闭线段与闭 AABB 相交，临界参数用逐面交点复核。"""
    enter, leave = 0.0, 1.0
    for p, q, lo, hi in zip(start, end, lower, upper):
        delta = q - p
        if delta == 0:
            if not lo <= p <= hi:
                return False
            continue
        # 近乎平行仍可能跨过楼面，需要保留该轴的进出时刻。
        if abs(delta) <= numerics.position_review_m:
            return intersects_closed_box_by_faces(start, end, lower, upper)
        a, b = (lo - p) / delta, (hi - p) / delta
        enter = max(enter, min(a, b))
        leave = min(leave, max(a, b))
        if enter - leave > numerics.parameter_review:
            return False
    if abs(enter - leave) <= numerics.parameter_review:
        return intersects_closed_box_by_faces(start, end, lower, upper)
    return enter < leave


def check_los(x: float, y: float, cfg: Config) -> tuple[bool, tuple[str, ...]]:
    """返回从车心上方相机到 T1 的可见性与遮挡建筑编号。"""
    camera = (x, y, cfg.camera_height)
    blocked = tuple(
        b.id for b in cfg.buildings
        if segment_aabb_intersects(camera, cfg.target_xyz, *b.bounds, cfg.numerics)
    )
    return not blocked, blocked


def camera_shadow_hull(cfg: Config, b: Building) -> ConvexHull:
    """返回建筑在相机标高平面的闭遮挡凸包，顶面须在相机与目标之间。"""
    tx, ty, height = cfg.target_xyz
    if not cfg.camera_height <= b.height < height:
        raise ValueError(f"{b.id} 的楼顶超出有限投影的高度范围")
    target = np.array([tx, ty])
    footprint = np.array([(x, y) for x in (b.x_min, b.x_max) for y in (b.y_min, b.y_max)])
    roof_projection = target + (footprint - target) * ((height - cfg.camera_height) / (height - b.height))
    # 各标高的截面投影是同心缩放，闭并集等于最低与最高投影的凸包。
    return ConvexHull(np.concatenate((footprint, roof_projection)))


def compute_clearance(x: float, y: float, cfg: Config) -> tuple[float, dict[str, float]]:
    """返回车辆的最小净空与各项净空（m），负值表示侵入。"""
    terms = field_clearances(x, y, cfg)
    terms.update({b.id: point_rect_distance(x, y, b) - cfg.vehicle_radius for b in cfg.buildings})
    return min(terms.values()), terms


def evaluate_station(x: float, y: float, cfg: Config) -> StationCheck:
    """计算单个站点的三个独立约束和指标，坐标与距离为 m，角度为度。"""
    tx, ty, tz = cfg.target_xyz
    distance = math.hypot(x - tx, y - ty)
    elevation = math.degrees(math.atan2(tz - cfg.camera_height, distance))
    clear, blocked = check_los(x, y, cfg)
    clearance, _ = compute_clearance(x, y, cfg)
    return StationCheck(
        x, y, distance, elevation, check_vehicle_geometry(x, y, cfg),
        gimbal_feasible_at_distance(distance, cfg), clear, blocked, clearance,
    )


def independently_check_station(x: float, y: float, cfg: Config) -> dict:
    """从输出坐标用逐面交点、精确圆盘距离和 atan2 复核站点。"""
    tx, ty, tz = cfg.target_xyz
    r = math.dist((x, y), (tx, ty))
    elevation = math.degrees(math.atan2(tz - cfg.camera_height, r))
    camera = (x, y, cfg.camera_height)
    building_vehicle_ok = {
        b.id: not disk_intrudes_rectangle(x, y, cfg.vehicle_radius, b) for b in cfg.buildings
    }
    building_los_clear = {
        b.id: not intersects_closed_box_by_faces(camera, cfg.target_xyz, *b.bounds) for b in cfg.buildings
    }
    field_ok = min(field_clearances(x, y, cfg).values()) >= 0
    gimbal_ok = cfg.elevation_bounds_deg[0] <= elevation <= cfg.elevation_bounds_deg[1]
    return {
        "vehicle_ok": field_ok and all(building_vehicle_ok.values()),
        "field_ok": field_ok,
        "gimbal_ok": gimbal_ok,
        "los_clear": all(building_los_clear.values()),
        "building_vehicle_ok": building_vehicle_ok,
        "building_los_clear": building_los_clear,
        "horizontal_distance_m": r,
        "elevation_deg": elevation,
    }
