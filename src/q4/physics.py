"""B 题第四问：复现第二问计划，推进断联与故障并提供实时车辆快照。
输入 q2 的配置、路点和时间表；返回连续时间轨迹、已启动告警和可改派状态。
由 src.q4.experiment 调用；独立验收用 nohup python3 -B -m pytest -q checks/test_q4.py。
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol，2026-11-20（版本发布日期未提供）。
"""

import csv
from dataclasses import dataclass, replace
from itertools import pairwise
import hashlib
import json
import math
from pathlib import Path

from checks.q2_geometry import Point, Scene as CheckScene
from checks.q2_validate import verify_q22
from src.q2.scene import Scene, read_scene


EAST = (0.0, 0.0)
EPS = 1e-9
DISTURBANCE_S = 5.0
HORIZON_S = 60.0
Pose = tuple[float, float]


def wrap(angle: float) -> float:
    """将有符号方位角（度）归入 [-180, 180)，180° 时选顺时针。"""
    return (angle + 180) % 360 - 180


def aim_time(old: Pose, new: Pose, speed: float) -> float:
    """按最短水平角差和双轴并行转动计算耗时（s）。"""
    return max(abs(wrap(new[0] - old[0])), abs(new[1] - old[1])) / speed


def pose_at(old: Pose, new: Pose, elapsed: float, speed: float) -> Pose:
    """在两轴各以最大速度且先到轴保持不动的约定下，返回当前角度（度）。"""
    if elapsed >= aim_time(old, new, speed) - EPS:
        return new
    limit = speed * max(0.0, elapsed)
    pan, tilt = wrap(new[0] - old[0]), new[1] - old[1]
    return wrap(old[0] + max(-limit, min(limit, pan))), old[1] + max(-limit, min(limit, tilt))


def route_length(points: tuple[Point, ...]) -> float:
    """以 m 返回全折线路长；单点路线长度为零。"""
    return math.fsum(math.dist(a, b) for a, b in pairwise(points))


def split_route(points: tuple[Point, ...], distance: float) -> tuple[tuple[Point, ...], tuple[Point, ...]]:
    """在已行程 distance m 切分恒速折线，返回共享同一切点的两段路线。"""
    if distance <= 0 or len(points) == 1:
        return points[:1], points
    for index, (a, b) in enumerate(pairwise(points)):
        length = math.dist(a, b)
        if distance < length - EPS:
            u = distance / length
            p = (a[0] + u * (b[0] - a[0]), a[1] + u * (b[1] - a[1]))
            return (*points[:index + 1], p), (p, *points[index + 1:])
        distance -= length
        if distance <= EPS:
            return points[:index + 2], points[index + 1:]
    return points, points[-1:]


@dataclass(frozen=True)
class Phase:
    """一段车辆物理状态，时间 s、折线路点 m、姿态度；停车时只有一个路点。"""

    kind: str
    start: float
    end: float
    alert: int | None
    points: tuple[Point, ...]
    pose0: Pose = EAST
    pose1: Pose = EAST

    @classmethod
    def from_record(cls, row: dict) -> 'Phase':
        """读取 JSON 轨迹的一段；秒、米、度原值不变，恢复不可变端点。"""
        return cls(row["kind"], row["start"], row["end"], row["alert"], tuple(map(tuple, row["points"])),
                   tuple(row["pose0"]), tuple(row["pose1"]))

    def pose(self, at: float, speed: float) -> Pose:
        """返回该相位内 at 秒的云台姿态（度）。"""
        return pose_at(self.pose0, self.pose1, at - self.start, speed)

    def cut(self, at: float, scene: Scene) -> 'Phase':
        """把物理相位截到 at 秒，以真实切断点及角度收尾。"""
        points = split_route(self.points, (at - self.start) * scene.speed_m_s)[0]
        return replace(self, end=at, points=points, pose1=self.pose(at, scene.gimbal_deg_s))


@dataclass(frozen=True)
class State:
    """可决策时刻的一辆车；未来锁定释放（s）、剩余折线（m）和实际姿态（度）。"""

    point: Point
    pose: Pose
    kind: str
    alert: int | None
    remaining_s: float | None
    remaining_route: tuple[Point, ...]
    release: float


@dataclass(frozen=True)
class Archive:
    """第二问（2）归档的状态模型、合法车次、原始启动时刻及源文件哈希。"""

    scene: Scene
    check: CheckScene
    legs: tuple[tuple[dict, ...], ...]
    pairs: tuple[tuple[Point, Point], ...]
    assignment: tuple[tuple[int, int], ...]
    starts: tuple[float, ...]
    hashes: dict[str, str]


def load_archive(directory: Path) -> Archive:
    """用 q2 独立几何与时序验收重放归档优化方案，有差异时中止。"""
    scene = read_scene(directory / "config.yaml")
    field = scene.field
    origins = dict(zip(scene.vehicles, (tuple(map(float, xy)) for xy in scene.origins)))
    targets = dict(zip(scene.targets, (cfg.target_xyz for cfg in scene.target_cfg)))
    check = CheckScene(field.field_width, field.field_height, field.vehicle_radius, field.camera_height,
                       field.elevation_bounds_deg, field.buildings, origins, targets, scene.speed_m_s,
                       scene.gimbal_deg_s, scene.observation_s, scene.bearing_sigma_deg)
    routes_file = directory / "q22_routes.json"
    timing_file = directory / "q22_timeline.csv"
    routes = [row for row in json.loads(routes_file.read_text()) if row["plan"] == "optimized"]
    with timing_file.open(newline="", encoding="utf-8") as stream:
        timeline = [row for row in csv.DictReader(stream) if row["plan"] == "optimized"]
    replay = verify_q22(check, routes, timeline, require_vehicle_separation=True)
    legs = tuple(tuple(row for row in replay["legs"] if row["vehicle"] == v) for v in scene.vehicles)
    by_target = tuple(tuple(row for row in replay["legs"] if row["target"] == a) for a in scene.targets)
    pairs = tuple(tuple(row["site"] for row in group) for group in by_target)
    assignment = tuple(tuple(scene.vehicles.index(row["vehicle"]) for row in group) for group in by_target)
    starts = tuple(replay["observation_start_s"][a] for a in scene.targets)
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
              (directory / "config.yaml", scene.field_file, routes_file, timing_file)}
    return Archive(scene, check, legs, pairs, assignment, starts, hashes)


def parking(kind: str, start: float, end: float, alert: int | None, point: Point,
            pose: Pose = EAST, target_pose: Pose | None = None) -> Phase:
    """构造不移动车心的相位，云台可保持姿态或转到 target_pose。"""
    return Phase(kind, start, end, alert, (point,), pose, pose if target_pose is None else target_pose)


def _original_tracks(archive: Archive, failed: int | None) -> tuple[tuple[Phase, ...], ...]:
    """执行 t=0 已下发的队列。失去双车同步的正常搭档按旧命令准备并一直等待。"""
    tracks = []
    for v, legs in enumerate(archive.legs):
        phases = []
        origin = archive.check.vehicles[archive.scene.vehicles[v]]
        hold = 0.0
        for row in legs:
            a = archive.scene.targets.index(row["target"])
            depart, arrive, ready = (row[k] for k in ("depart_s", "arrive_s", "turn_end_s"))
            start, end, release = (row[k] for k in ("observation_start_s", "observation_end_s", "reset_end_s"))
            site = row["site"]
            pose = (row["yaw_deg"], row["pitch_deg"])
            if depart > hold:
                phases.append(parking("IDLE", hold, depart, None, origin))
            phases.append(Phase("TRAVELING", depart, arrive, a, row["waypoints"]))
            phases.append(parking("SLEWING", arrive, ready, a, site, EAST, pose))
            if start >= DISTURBANCE_S and failed in archive.assignment[a]:
                phases.append(parking("READY_WAITING", ready, math.inf, a, site, pose))
                break
            phases.append(parking("READY_WAITING", ready, start, a, site, pose))
            phases.append(parking("OBSERVING", start, end, a, site, pose))
            phases.append(parking("RESETTING", end, release, a, site, pose, EAST))
            origin, hold = site, release
        else:
            phases.append(parking("IDLE", hold, math.inf, None, origin))
        if v == failed:
            clipped = _until(tuple(phases), DISTURBANCE_S, archive.scene)
            last = clipped[-1]
            phases = [*clipped, parking("FAILED", DISTURBANCE_S, math.inf, last.alert, last.points[-1], last.pose1)]
        tracks.append(tuple(phases))
    return tuple(tracks)


def _until(phases: tuple[Phase, ...], at: float, scene: Scene) -> tuple[Phase, ...]:
    """截取 at 秒前的物理前缀；恰在边界开始的零时长事件留给后继决定。"""
    return tuple(p if p.end <= at else p.cut(at, scene) for p in phases if p.start < at)


@dataclass(frozen=True)
class Prefix:
    """扰动后的可见输入：指令生效时刻、已启动告警和既有车辆轨迹。"""

    failed: int | None
    dispatch: float
    locked: dict[int, float]
    states: tuple[State, ...]
    fixed: tuple[tuple[Phase, ...], ...]
    original: tuple[tuple[Phase, ...], ...]
    original_starts: dict[int, float]


def build_prefix(archive: Archive, failed: int | None, dispatch_s: float) -> Prefix:
    """先按旧命令推进到真实指令生效时刻，再读取位置、姿态和锁定剩余过程。"""
    if not DISTURBANCE_S <= dispatch_s <= 30 or (failed is not None and not 0 <= failed < len(archive.legs)):
        raise ValueError("指令生效时刻或故障车编号超出题面范围")
    original = _original_tracks(archive, failed)
    starts = {a: at for a, at in enumerate(archive.starts)
              if at < DISTURBANCE_S or failed not in archive.assignment[a]}
    locked = {a: at for a, at in starts.items() if at < dispatch_s}
    states, fixed = [], []
    for phases in original:
        phase = next(p for p in phases if p.start <= dispatch_s < p.end)
        if phase.kind == "OBSERVING" and phase.alert not in locked:
            phase = parking("READY_WAITING", dispatch_s, math.inf, phase.alert, phase.points[0], phase.pose0)
        _, remaining = split_route(phase.points, (dispatch_s - phase.start) * archive.scene.speed_m_s)
        release = dispatch_s
        if phase.alert in locked and phase.kind in {"OBSERVING", "RESETTING"}:
            release = next(p.end for p in phases if p.alert == phase.alert and p.kind == "RESETTING")
        states.append(State(remaining[0], phase.pose(dispatch_s, archive.scene.gimbal_deg_s), phase.kind, phase.alert,
                            phase.end - dispatch_s if math.isfinite(phase.end) else None, remaining, release))
        fixed.append(_until(phases, release, archive.scene))
    return Prefix(failed, dispatch_s, locked, tuple(states), tuple(fixed), original, starts)


def continuing_route(state: State, station: Point) -> tuple[Point, ...] | None:
    """原目的站保留时沿当前折线继续行驶，其他情况交给可见图从实时车位寻路。"""
    if state.kind == "TRAVELING" and state.remaining_route[-1] == station:
        return state.remaining_route
    return None


@dataclass(frozen=True)
class Clock:
    """相同的整数时钟；ceil 相位长度，默认判启动时间严格小于 60 s。"""

    tick: float
    inclusive: bool = False

    def ceil(self, seconds: float) -> int:
        """以 tick 为单位向上取整，消除精确整刻度的浮点误差。"""
        return math.ceil(seconds / self.tick - EPS)

    def seconds(self, step: int) -> float:
        """将整数时刻换回秒，避免 60 s 边界的乘法尾差。"""
        return round(step * self.tick, 10)

    @property
    def last_step(self) -> int:
        """返回允许成功启动的最大整数时刻。"""
        return self.ceil(HORIZON_S) - (0 if self.inclusive else 1)

    def success(self, at: float) -> bool:
        """以连续秒时刻按本实验的窗口端点规则判断成功。"""
        return at <= HORIZON_S if self.inclusive else at < HORIZON_S
