"""B 题第四问：按断联规则推进第二问计划与故障，提供实时车辆快照。
输入 q2 的配置、路点和时间表；返回连续时间轨迹、已启动告警和可改派状态。
由 src.q4.experiment 调用；独立验收用 nohup python3 -B -m pytest -q checks/test_q4.py。
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol，2026-11-20（版本发布日期未提供）。
"""

import csv
from dataclasses import dataclass, replace
from enum import StrEnum
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


class OutageRule(StrEnum):
    """断联前授权范围及双车就绪信息的可用性。"""

    FULL_QUEUE = "full_queue"
    CURRENT_TRIP_ONLY = "current_trip_only"
    CONFIRM_AFTER_RECOVERY = "confirm_after_recovery"
    STRICT_OUTAGE = "strict_outage"

    @property
    def current_trip_only(self) -> bool:
        """断联期间不启动 5 s 前尚未发出的旧车次。"""
        return self in (OutageRule.CURRENT_TRIP_ONLY, OutageRule.STRICT_OUTAGE)

    @property
    def confirm_after_recovery(self) -> bool:
        """断联期间不新开联合观测，已开测的任务继续占用。"""
        return self in (OutageRule.CONFIRM_AFTER_RECOVERY, OutageRule.STRICT_OUTAGE)


OUTAGE_COMPARISONS = (OutageRule.CURRENT_TRIP_ONLY, OutageRule.CONFIRM_AFTER_RECOVERY)
OUTAGE_VARIANTS = (*OUTAGE_COMPARISONS, OutageRule.STRICT_OUTAGE)


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
    plan: str = "optimized"


def load_archive(directory: Path, *, wall_clearance_m: float = 0.0, plan: str = "optimized",
                 waypoint_decimals: int | None = None) -> Archive:
    """按方案名重放归档 Q2（2），核查全程墙距（m）和可选的下发路点精度。"""
    scene = read_scene(directory / "config.yaml")
    field = scene.field
    origins = dict(zip(scene.vehicles, (tuple(map(float, xy)) for xy in scene.origins)))
    targets = dict(zip(scene.targets, (cfg.target_xyz for cfg in scene.target_cfg)))
    check = CheckScene(field.field_width, field.field_height, field.vehicle_radius, field.camera_height,
                       field.elevation_bounds_deg, field.buildings, origins, targets, scene.speed_m_s,
                       scene.gimbal_deg_s, scene.observation_s, scene.bearing_sigma_deg)
    routes_file = directory / "q22_routes.json"
    timing_file = directory / "q22_timeline.csv"
    routes = [row for row in json.loads(routes_file.read_text()) if row["plan"] == plan]
    with timing_file.open(newline="", encoding="utf-8") as stream:
        timeline = [row for row in csv.DictReader(stream) if row["plan"] == plan]
    if not routes or not timeline:
        raise ValueError(f"第二问归档缺少 {plan!r} 的路线或时间轴")
    replay = verify_q22(check, routes, timeline, require_vehicle_separation=True, wall_clearance_m=wall_clearance_m,
                        waypoint_decimals=waypoint_decimals)
    legs = tuple(tuple(row for row in replay["legs"] if row["vehicle"] == v) for v in scene.vehicles)
    by_target = tuple(tuple(row for row in replay["legs"] if row["target"] == a) for a in scene.targets)
    pairs = tuple(tuple(row["site"] for row in group) for group in by_target)
    assignment = tuple(tuple(scene.vehicles.index(row["vehicle"]) for row in group) for group in by_target)
    starts = tuple(replay["observation_start_s"][a] for a in scene.targets)
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
              (directory / "config.yaml", scene.field_file, routes_file, timing_file)}
    return Archive(scene, check, legs, pairs, assignment, starts, hashes, plan)


def parking(kind: str, start: float, end: float, alert: int | None, point: Point,
            pose: Pose = EAST, target_pose: Pose | None = None) -> Phase:
    """构造不移动车心的相位，云台可保持姿态或转到 target_pose。"""
    return Phase(kind, start, end, alert, (point,), pose, pose if target_pose is None else target_pose)


def _original_tracks(archive: Archive, failed: int | None, dispatch: float, rule: OutageRule
                     ) -> tuple[tuple[tuple[Phase, ...], ...], dict[int, float]]:
    """重放原分车、原站和原路，返回车辆轨迹与实际联合开测时刻（s）。"""
    scene = archive.scene
    legs = [{row["target"]: row for row in rows} for rows in archive.legs]
    tracks: list[list[Phase]] = [[] for _ in legs]
    origins = [archive.check.vehicles[v] for v in scene.vehicles]
    releases = [0.0 for _ in legs]
    starts = {}
    # 共享车辆完成观测、复位后才有下一车次，归档开测次序是其依赖的拓扑序。
    for a in sorted(range(len(archive.starts)), key=archive.starts.__getitem__):
        effective_ready = {}
        for v in archive.assignment[a]:
            if not math.isfinite(releases[v]):
                continue
            row = legs[v][scene.targets[a]]
            depart = max(row["depart_s"], releases[v])
            if rule.current_trip_only and row["depart_s"] >= DISTURBANCE_S:
                # 第 5 秒前已经出发或在站准备的车次可继续，后续车次要等新授权。
                depart = max(depart, dispatch)
            shift = depart - row["depart_s"]
            arrive, ready = row["arrive_s"] + shift, row["turn_end_s"] + shift
            if depart > releases[v]:
                tracks[v].append(parking("IDLE", releases[v], depart, None, origins[v]))
            tracks[v].append(Phase("TRAVELING", depart, arrive, a, row["waypoints"]))
            tracks[v].append(parking("SLEWING", arrive, ready, a, row["site"], EAST, (row["yaw_deg"], row["pitch_deg"])))
            effective_ready[v] = ready
        nominal_start = archive.starts[a]
        earliest_start = nominal_start
        if rule.confirm_after_recovery and nominal_start >= DISTURBANCE_S:
            # 未确认的联合观测不得开始；允许已授权车次行驶、指向。
            earliest_start = max(earliest_start, dispatch)
        can_start = len(effective_ready) == 2 and (nominal_start < DISTURBANCE_S or failed not in effective_ready)
        start = max(earliest_start, *effective_ready.values()) if can_start else math.inf
        if can_start:
            starts[a] = start
        for v, ready in effective_ready.items():
            row = legs[v][scene.targets[a]]
            site, pose = row["site"], (row["yaw_deg"], row["pitch_deg"])
            tracks[v].append(parking("READY_WAITING", ready, start, a, site, pose))
            if can_start:
                end = start + scene.observation_s
                release = end + row["slew_s"]
                tracks[v].extend((parking("OBSERVING", start, end, a, site, pose),
                                  parking("RESETTING", end, release, a, site, pose, EAST)))
                origins[v], releases[v] = site, release
            else:
                releases[v] = math.inf
    for v, phases in enumerate(tracks):
        if math.isfinite(releases[v]):
            phases.append(parking("IDLE", releases[v], math.inf, None, origins[v]))
        if v == failed:
            clipped = _until(tuple(phases), DISTURBANCE_S, scene)
            last = clipped[-1]
            tracks[v] = [*clipped, parking("FAILED", DISTURBANCE_S, math.inf, last.alert, last.points[-1], last.pose1)]
    return tuple(map(tuple, tracks)), starts


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


def build_prefix(archive: Archive, failed: int | None, dispatch_s: float, *,
                 outage_rule: OutageRule | str = OutageRule.FULL_QUEUE) -> Prefix:
    """在 dispatch_s 秒读取位置、姿态和锁定过程；无断联时直接用 5 s。"""
    if not DISTURBANCE_S <= dispatch_s <= 30 or (failed is not None and not 0 <= failed < len(archive.legs)):
        raise ValueError("指令生效时刻或故障车编号超出题面范围")
    rule = OutageRule(outage_rule)
    original, starts = _original_tracks(archive, failed, dispatch_s, rule)
    starts = dict(sorted(starts.items()))
    locked = {a: at for a, at in starts.items() if at < dispatch_s}
    states, fixed = [], []
    for v, phases in enumerate(original):
        phase = next(p for p in phases if p.start <= dispatch_s < p.end)
        if phase.kind == "OBSERVING" and phase.alert not in locked:
            phase = parking("READY_WAITING", dispatch_s, math.inf, phase.alert, phase.points[0], phase.pose0)
        if (rule.current_trip_only and phase.start == dispatch_s and phase.kind in {"TRAVELING", "SLEWING"}
                and any(row["target"] == archive.scene.targets[phase.alert] and row["depart_s"] >= DISTURBANCE_S
                        for row in archive.legs[v])):
            # 恢复事件先处理，保留计划将要启动的车次还没有出发。
            phase = parking("IDLE", dispatch_s, math.inf, None, phase.points[0])
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
