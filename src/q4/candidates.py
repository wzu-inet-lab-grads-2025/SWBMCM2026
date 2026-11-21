"""B 题第四问，生成七类场景和共享的 T4 候选站对、视线及避障路长。
输入 q2 归档和 q4 YAML；返回场景清单、有限候选集与路线索引。
由 src.q4.experiment 调用：nohup python3 -B -m src.run q4 --profile full。
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol，2026-11-20（版本发布日期未提供）。
"""

from dataclasses import asdict, dataclass, replace
from functools import lru_cache
from itertools import combinations, product
import hashlib
import json
import math

import numpy as np

from src.q1.config import Config, compute_target_xyz
from src.q1.region import station_masks
from src.q2.paths import Point, Route
from src.q2.scene import build_router

from .physics import Archive, DISTURBANCE_S, Prefix, build_prefix, route_length


StationPair = tuple[Point, Point]
Point3 = tuple[float, float, float]
CATEGORIES = ("E1", "E2", "E3", "E4", "E5", "E6", "E7")


def digest(value) -> str:
    """用无随机化的 JSON 字段顺序为公共输入生成 SHA-256。"""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class Scenario:
    """单独情景的扰动量；角度为度、长度 m、时刻 s；未发生的字段为空。"""

    category: str
    theta: float | None = None
    radius: float | None = None
    height: float | None = None
    failed: int | None = None
    recovery: float | None = None
    sample: str = "main"

    def __post_init__(self):
        """无断联用 None；按答疑，实际断联必须到 5 s 之后、30 s 以内才恢复。"""
        if self.recovery is not None and not DISTURBANCE_S < self.recovery <= 30:
            raise ValueError("通信恢复时刻须在 (5,30] s")

    @property
    def key(self) -> str:
        """稳定情景编号，保留所有实际扰动量和采样来源。"""
        t4 = "" if self.theta is None else f"_A{self.theta:g}_R{self.radius:g}"
        fault = "" if self.failed is None else f"_V{self.failed + 1}"
        outage = "" if self.recovery is None else f"_C{self.recovery:g}"
        return f"{self.category}{t4}{fault}{outage}" + (f"_{self.sample}" if self.sample != "main" else "")

    @property
    def dispatch(self) -> float:
        """有断联时新指令最早在实际恢复时生效，否则在 5 s 生效。"""
        return DISTURBANCE_S if self.recovery is None else self.recovery

    @property
    def location(self) -> tuple[float, float, float] | None:
        """返回 T4 告警的参数三元组，没有新增告警时返回空。"""
        return None if self.theta is None else (self.theta, self.radius, self.height)


def scenarios(locations, recovery_s, *, sample="main", include_no_t4=True) -> list[Scenario]:
    """生成七类扰动的笛卡尔积；五辆车全部逐一作为故障车。"""
    result = []
    for tag, (added, broken, outage) in zip(CATEGORIES, (
        (True, False, False), (False, True, False), (False, False, True),
        (True, True, False), (True, False, True), (False, True, True), (True, True, True),
    )):
        if added or include_no_t4:
            for loc, fault, recovery in product(locations if added else [(None, None, None)],
                                                range(5) if broken else [None], recovery_s if outage else [None]):
                result.append(Scenario(tag, *loc, fault, recovery, sample))
    return result


def target_config(archive: Archive, location: tuple[float, float, float]) -> Config:
    """按原题相对调度中心的参数计算三维目标与闭仰角下界。"""
    field = archive.scene.field
    xyz = compute_target_xyz(field.dispatch_center, *location)
    r_min = (xyz[2] - field.camera_height) / math.tan(math.radians(field.elevation_bounds_deg[1]))
    return replace(field, target_xyz=xyz, distance_lower_bound=r_min)


def station_pose(point: Point, xyz: Point3, height_m: float) -> tuple[float, float]:
    """从米制站点和目标得到（水平朝东为零的）方位与仰角，单位度。"""
    dx, dy = xyz[0] - point[0], xyz[1] - point[1]
    return math.degrees(math.atan2(dy, dx)), math.degrees(math.atan2(xyz[2] - height_m, math.hypot(dx, dy)))


@dataclass(frozen=True)
class Catalog:
    """一个 T4 的共享合法站对集合及采样诊断量，长度 m。"""

    pairs: tuple[StationPair, ...]
    grid_count: int
    visible_count: int
    pool_count: int
    legal_pair_count: int
    best_rmse_m: float | None
    sha256: str


class Geometry:
    """复用 q2 保守可见图，缓存 q4 的真实折线路点与米制距离。"""

    def __init__(self, archive: Archive, *, wall_clearance_m: float = 0.0):
        """构建楼体外扩图；附加墙距单位 m，实车车径仍用于车间距离核查。"""
        self.wall_clearance_m = wall_clearance_m
        self.field = archive.scene.field
        self.router = build_router(archive.scene, archive.scene.path_polygon_sides,
                                   wall_clearance_m=wall_clearance_m)

    @lru_cache(maxsize=8192)
    def route(self, a: Point, b: Point) -> Route:
        """恢复限定路由规则下从 a 到 b 的最短合法折线。"""
        return self.router.route(a, b)

    def matrix(self, origins, stations) -> np.ndarray:
        """批量返回统一可见图距离（m）；不可达为无穷。"""
        return self.router.distances(origins, stations)


def _grid(geometry: Geometry, step: float) -> np.ndarray:
    """保留标称网格内点，边缘候选向内折到可停车的场界。"""
    field = geometry.field
    lo = field.vehicle_radius
    axes = [np.unique(np.clip(np.r_[np.arange(lo, hi, step), hi], safe_lo, safe_hi))
            for hi, safe_lo, safe_hi in zip((field.field_width - lo, field.field_height - lo),
                                           geometry.router.lower, geometry.router.upper)]
    grid = np.array(list(product(*axes)))
    return grid[geometry.router.admits(grid)]


def _poses(xy: np.ndarray, config: Config) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """返回点到目标的单位水平视向、距离 m 及从东水平起算的两轴最大角度。"""
    delta = np.array(config.target_xyz[:2]) - xy
    rr = np.linalg.norm(delta, axis=1)
    yaw = np.arctan2(delta[:, 1], delta[:, 0])
    pitch = np.arctan2(config.target_xyz[2] - config.camera_height, rr)
    return delta / rr[:, None], rr, np.degrees(np.maximum(np.abs(yaw), np.abs(pitch)))


def _diverse(points: np.ndarray, cost: np.ndarray, count: int, separation_m: float) -> list[int]:
    """按用时排序留空间上分散的局部站点，以便提供可替换路线和站位。"""
    chosen = []
    for i in np.argsort(cost, kind="stable"):
        if all(math.dist(points[i], points[j]) >= separation_m for j in chosen):
            chosen.append(int(i))
            if len(chosen) == count:
                break
    return chosen


class CandidateBank:
    """每个目标方位与距离只生成一次同一份有限双站目录。"""

    def __init__(self, archive: Archive, geometry: Geometry, config: dict,
                 *, grid_m: float | None = None, pair_limit: int | None = None, min_sine: float | None = None):
        """准备 Q2 站、扰动时真实车位及转场后站位的参考路长矩阵。"""
        self.archive, self.geometry, self.config = archive, geometry, config
        self.grid_m = config["grid_m"] if grid_m is None else grid_m
        self.pair_limit = config["pair_limit"] if pair_limit is None else pair_limit
        self.min_sine = config["min_sine"] if min_sine is None else min_sine
        at_five = build_prefix(archive, None, DISTURBANCE_S)
        self.anchors = tuple(dict.fromkeys([*(s.point for s in at_five.states),
                                             *(p for pair in archive.pairs for p in pair)]))
        self.grid = _grid(geometry, self.grid_m)
        self.distance = geometry.matrix(self.anchors, self.grid)

    def _local_pool(self, target: Config, visible: np.ndarray) -> np.ndarray:
        """由各参考车位的最快邻域、80° 仰角边界和全场粗网格产生站点。"""
        grid = self.grid[visible]
        if not len(grid):
            return grid
        _, _, turns = _poses(grid, target)
        scene = self.archive.scene
        scores = self.distance[:, visible] / scene.speed_m_s + turns / scene.gimbal_deg_s
        near = [i for row in scores for i in _diverse(grid, row, self.config["near_sites_per_anchor"], 2.6)]
        h = self.config["local_grid_m"]
        delta = np.array(list(product((-h, 0.0, h), repeat=2)))
        hot = grid[[int(row.argmin()) for row in scores]]
        angles = np.arange(32) * math.tau / 32
        rays = np.column_stack((np.cos(angles), np.sin(angles)))
        ring = np.array(target.target_xyz[:2]) + (target.distance_lower_bound + 1e-6) * rays
        points = np.unique(np.concatenate((grid[near], np.array(self.anchors), ring,
                                           (hot[:, None] + delta).reshape(-1, 2))), axis=0)
        mask = self.geometry.router.admits(points) & np.logical_and.reduce(station_masks(*points.T, target))
        return points[mask]

    def catalog(self, location: tuple[float, float, float]) -> Catalog:
        """将 T4 的方位、距离和高度换算为扫描配置，再返回共享目录。"""
        return self.for_target(target_config(self.archive, location))

    @lru_cache(maxsize=256)
    def for_target(self, target: Config) -> Catalog:
        """在共线和同站重叠之外，以同步时间和一阶 RMSE 双口径筛选站对。"""
        scene = self.archive.scene
        visible = np.logical_and.reduce(station_masks(*self.grid.T, target))
        points = self._local_pool(target, visible)
        u, ranges, angle = _poses(points, target)
        i, j = np.triu_indices(len(points), 1)
        sine = np.abs(u[i, 0] * u[j, 1] - u[i, 1] * u[j, 0])
        # 站对共线无法定位；同时占用的两个驻站圆盘至少相距 3 m。
        good = ((sine > self.min_sine) &
                (np.linalg.norm(points[i] - points[j], axis=1) >= 2 * target.vehicle_radius))
        i, j, sine = i[good], j[good], sine[good]
        if not len(i):
            return Catalog((), len(self.grid), int(visible.sum()), len(points), 0, None, digest([]))
        rmse = math.radians(scene.bearing_sigma_deg) * np.hypot(ranges[i], ranges[j]) / sine
        time = self.geometry.matrix(self.anchors, points) / scene.speed_m_s + angle / scene.gimbal_deg_s
        options, quality = [], []
        for a, b in combinations(range(len(self.anchors)), 2):
            # 同一对站可交换两车。第二个站对须在同步耗时容许余量内改善 RMSE。
            cost = np.minimum(np.maximum(time[a, i], time[b, j]), np.maximum(time[a, j], time[b, i]))
            fastest = int(cost.argmin())
            close = np.flatnonzero(cost <= cost[fastest] + self.config["quality_slack_s"])
            best_near = int(close[np.argmin(rmse[close])])
            options.append(fastest)
            quality.append(best_near)
        # 先保留几何最优与各参考车对的最快代表，再用剩余名额保留低 RMSE 代表。
        best_geometry = int(rmse.argmin())
        selected = list(dict.fromkeys([best_geometry, *options, *quality, *np.argsort(rmse)[:12]]))[:self.pair_limit]
        pairs = tuple((tuple(map(float, points[i[p]])), tuple(map(float, points[j[p]]))) for p in selected)
        return Catalog(pairs, len(self.grid), int(visible.sum()), len(points), len(i), float(rmse[best_geometry]),
                       digest(pairs))


def prefix_hash(prefix: Prefix) -> str:
    """为四个求解器共享的车位、姿态和锁定起测时刻生成标识。"""
    return digest({"dispatch": prefix.dispatch, "locked": prefix.locked, "failed": prefix.failed,
                   "states": [asdict(s) for s in prefix.states]})


def initial_schedule_hash(archive: Archive) -> str:
    """同一份文件含多份方案时区分所选行，保留历史默认方案的指纹。"""
    if archive.plan == "optimized":
        return digest(archive.hashes)
    return digest({"files": archive.hashes, "plan": archive.plan})


def route_time(points: tuple[Point, ...], archive: Archive) -> float:
    """把真实折线路长按题面恒速换算为连续秒。"""
    return route_length(points) / archive.scene.speed_m_s
