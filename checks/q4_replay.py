"""第四问独立回放：给定站—车分配和先后序，检查实际几何、接续和计时。
输入公共快照及候选方案，不读求解器时间变量；输出连续物理轨迹、校验与官方指标。
由 src.q4.experiment 调用；测试：nohup python3 -B -m pytest -q checks/test_q4.py。
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol，2026-11-20（版本发布日期未提供）。
"""

from dataclasses import dataclass
from functools import lru_cache
from itertools import combinations, pairwise
import math

from checks.q2_geometry import CheckError, Point, check_route, pair_metrics, station_pointing
from src.q4.physics import DISTURBANCE_S, EAST, EPS, HORIZON_S, Phase, State, _until, continuing_route, parking
from src.q4.problem import Instance, Pick, Plan


@dataclass(frozen=True)
class Motion:
    """用于连续车距复核的一段恒速运动或停车，时间 s、端点 m。"""

    t0: float
    t1: float
    a: Point
    b: Point

    def at(self, t: float) -> Point:
        """时刻 t 的车心（m），返回线段内插值。"""
        u = (t - self.t0) / (self.t1 - self.t0) if self.t1 > self.t0 else 0
        return tuple(x + u * (y - x) for x, y in zip(self.a, self.b))


@dataclass(frozen=True)
class Replay:
    """候选的实际开始时刻、名义指令、轨迹与独立校验数据；非法计划不参与比较。"""

    plan: Plan
    picks: dict[int, Pick]
    starts: dict[int, float]
    starts_tick: dict[int, int]
    tracks: tuple[tuple[Phase, ...], ...]
    available: tuple[float, ...]
    min_distance: float | None
    closest: tuple[int, int, float] | None
    violations: tuple[str, ...]
    changes: int
    strategy: str

    @property
    def valid(self) -> bool:
        """所有实现约束都通过才可入围。"""
        return not self.violations

    def metrics(self, instance: Instance) -> tuple[int, int, float, float]:
        """按独立连续回放重算 (N, k, η, t_last)，缺任何一项则 t_last=60。"""
        n = len(instance.names)
        success = [at for at in self.starts.values() if instance.clock.success(at)]
        k = len(success)
        return n, k, k / n, max(success) if k == n else HORIZON_S

    def score(self, instance: Instance) -> tuple:
        """统一报告排序：官方两项，之后早启动和原任务席位改动；微秒级以下视为同值。"""
        _, k, _, last = self.metrics(instance)
        return (-k, round(last, 8), round(sum(at for at in self.starts.values() if instance.clock.success(at)), 8),
                self.changes)


def _motions(track: tuple[Phase, ...], speed: float) -> list[Motion]:
    """逐折线段拆分实际路径；失效车不占空间（题面第 5 页）。"""
    moves = []
    for phase in track:
        if phase.kind == "FAILED" or phase.end <= phase.start:
            continue
        if phase.kind != "TRAVELING" or len(phase.points) == 1:
            moves.append(Motion(phase.start, phase.end, phase.points[0], phase.points[0]))
        else:
            now = phase.start
            for i, (a, b) in enumerate(pairwise(phase.points), start=1):
                nxt = phase.end if i == len(phase.points) - 1 else now + math.dist(a, b) / speed
                if nxt > now:
                    moves.append(Motion(now, nxt, a, b))
                now = nxt
    return moves


def separation(tracks: tuple[tuple[Phase, ...], ...], speed: float) -> tuple[float, tuple[int, int, float] | None]:
    """对两个分段线性轨迹的每个重叠时间区间最小化距离平方，返回 m 和见证时刻。"""
    paths = [_motions(track, speed) for track in tracks]
    best = math.inf
    where = None
    for v, w in combinations(range(len(paths)), 2):
        p, q = paths[v], paths[w]
        i = j = 0
        while i < len(p) and j < len(q):
            a, b = p[i], q[j]
            lo, hi = max(a.t0, b.t0), min(a.t1, b.t1)
            if lo <= hi:
                aa, bb, ac, bc = a.at(lo), b.at(lo), a.at(hi), b.at(hi)
                x, y = aa[0] - bb[0], aa[1] - bb[1]
                dx, dy = ac[0] - bc[0] - x, ac[1] - bc[1] - y
                den = dx * dx + dy * dy
                z = max(0, min(1, -(x * dx + y * dy) / den)) if den else 0
                d2 = (x + z * dx)**2 + (y + z * dy)**2
                if d2 < best:
                    best, where = d2, (v, w, lo + z * (hi - lo))
            if a.t1 <= b.t1:
                i += 1
            if b.t1 <= a.t1:
                j += 1
    return math.sqrt(best), where


class Validator:
    """从快照和实际折线路点重建最早启动轨迹；几何/时序不引用求解器的表值。"""

    def __init__(self, instance: Instance, *, enforce_tick_window: bool = True):
        """缓存物理常量和重复的几何检查；整数求解器同时核验离散评价窗。"""
        self.ins = instance
        self.archive = instance.archive
        self.prefix = instance.prefix
        self.scene = self.archive.scene
        self.clock = instance.clock
        self.enforce_tick_window = enforce_tick_window
        self.wall_clearance_m = instance.geometry.wall_clearance_m
        self._cached_route = lru_cache(maxsize=4096)(self._route_check)
        self._cached_station = lru_cache(maxsize=1024)(self._station)
        self._cached_pair = lru_cache(maxsize=128)(self._pair)
        self._cached_plan = lru_cache(maxsize=256)(self._dynamic)

    def _station(self, alert: int, point: Point) -> tuple[float, float]:
        """用 q2 验收的精确连线—楼体判据重新检查完整三维视线。"""
        check = station_pointing(self.archive.check, point, self.ins.targets[alert])
        return check["yaw_deg"], check["pitch_deg"]

    def _pair(self, pick: Pick) -> tuple[Point, Point]:
        """检查站对交会秩及 3 m 最小车心距，不接受缺失或重复的车。"""
        if pick.first == pick.second or not all(v in self.ins.alive for v in pick.vehicles):
            raise CheckError("选车重复或包括故障车辆")
        if pick.alert not in self.ins.pending or not 0 <= pick.pair < len(self.ins.pairs[pick.alert]):
            raise CheckError("已启动告警不可改派，且站对必须属于共享目录")
        first, second = self.ins.pairs[pick.alert][pick.pair]
        pair = pair_metrics(self.archive.check, first, second, self.ins.targets[pick.alert])
        if pair["station_disks_overlap"] or pair["sin_gamma"] <= 1e-12:
            raise CheckError("双车站位重叠或交会退化")
        return first, second

    def _route_check(self, origin: Point, station: Point, path: tuple[Point, ...] | None):
        """独立检查折线路点与每段楼距；未提供折线时恢复公共路由。"""
        route = path if path is not None else self.ins.geometry.route(origin, station).waypoints
        check = check_route(self.archive.check, route, origin, wall_clearance_m=self.wall_clearance_m)
        if route[-1] != station:
            raise CheckError("路径终点与给定驻站不一致")
        return route, check["travel_s"]

    def _turn(self, p: tuple[float, float], q: tuple[float, float]) -> float:
        """独立以 atan2(sin,cos) 计算最短角差和两轴最大用时。"""
        a = math.radians(q[0] - p[0])
        pan = abs(math.degrees(math.atan2(math.sin(a), math.cos(a))))
        return max(pan, abs(q[1] - p[1])) / self.scene.gimbal_deg_s

    def _prepare(self, v: int, a: int, station: Point, state: State, available_tick: int, track: list[Phase]):
        """执行必要的取消复位、行车和指向，分别返回连续和规划时钟下的就绪时刻。"""
        if v == self.prefix.failed or state.release < self.prefix.dispatch - EPS:
            raise CheckError("故障车或通信恢复前的新任务")
        point, pose = state.point, state.pose
        now, tick = state.release, available_tick
        if now > self.prefix.dispatch + EPS and state.kind in ("OBSERVING", "RESETTING"):
            pose = EAST
        elif state.kind == "FAILED":
            raise CheckError("故障车被调度")
        if point != station:
            reset_time = self._turn(pose, EAST)
            if reset_time > EPS:
                track.append(parking("CANCEL_RESET", now, now + reset_time, a, point, pose, EAST))
                now += reset_time
                tick += self.clock.ceil(reset_time)
            path, duration = self._cached_route(point, station, continuing_route(state, station))
            track.append(Phase("TRAVELING", now, now + duration, a, path))
            now += duration
            tick += self.clock.ceil(duration)
            pose = EAST
        else:
            if self.wall_clearance_m:
                self._cached_route(point, station, (point,))
            track.append(Phase("TRAVELING", now, now, a, (point,), pose, pose))
        goal = self._cached_station(a, station)
        duration = self._turn(pose, goal)
        track.append(parking("SLEWING", now, now + duration, a, station, pose, goal))
        return now + duration, tick + self.clock.ceil(duration), goal

    def _complete_tracks(self, tracks: list[list[Phase]], available: list[float]):
        """以空闲/故障占位补齐评价窗；对临近 60 s 的已启动任务继续记录完整复位。"""
        until = max(HORIZON_S, *(p.end for track in tracks for p in track if math.isfinite(p.end)))
        for v, (track, at) in enumerate(zip(tracks, available)):
            last = track[-1]
            if last.end == math.inf:
                track[-1] = last.cut(until, self.scene)
            elif at < until:
                kind, alert, pose = ("FAILED", last.alert, last.pose1) if v == self.prefix.failed else ("IDLE", None, last.pose1)
                track.append(parking(kind, at, until, alert, last.points[-1], pose))
        return tuple(tuple(track) for track in tracks)

    def _finish(self, plan, picks, starts, ticks, tracks, available, violations, changes, strategy):
        """统一添加车距检查和机读验证信息；回放结果对未成功告警不虚构时刻。"""
        tracks = self._complete_tracks(tracks, available)
        if self.wall_clearance_m:
            # 指令生效前的截断折线、原地等待和复位也必须在同一安全域内。
            for vehicle, track in zip(self.scene.vehicles, tracks, strict=True):
                for phase in track:
                    try:
                        self._cached_route(phase.points[0], phase.points[-1], phase.points)
                    except CheckError as error:
                        violations = (*violations, f"{vehicle} {phase.kind} {phase.start:g} s：{error}")
        d, closest = separation(tracks, self.scene.speed_m_s)
        if d < 2 * self.archive.check.radius_m - 1e-8:
            violations = (*violations, f"车身交叠：{d:.8f} m，车号与时刻 {closest}")
        return Replay(plan, picks, starts, ticks, tracks, tuple(available), d if math.isfinite(d) else None,
                      closest, tuple(violations), changes, strategy)

    def _original_picks(self, starts: dict[int, float]) -> dict[int, Pick]:
        """返回先前不可改派告警的归档双车站对。"""
        return {a: Pick(a, 0, *self.archive.assignment[a]) for a in starts}

    def _dynamic(self, plan: Plan) -> Replay:
        """按给定顺序独立接续实际车辆，顺序只在共享车辆之间施加依赖。"""
        tracks = [list(track) for track in self.prefix.fixed]
        states = list(self.prefix.states)
        starts = dict(self.prefix.locked)
        picks, ticks = self._original_picks(starts), {}
        release_ticks = [self.clock.ceil(s.release) for s in states]
        used = set()
        for pick in plan:
            a = pick.alert
            if a in starts:
                raise CheckError("动用锁定或以多个配置重复安排同一告警")
            stations = self._cached_pair(pick)
            prep = [self._prepare(v, a, p, states[v], release_ticks[v], tracks[v])
                    for v, p in zip(pick.vehicles, stations)]
            start, ticks[a] = max(r[0] for r in prep), max(r[1] for r in prep)
            if not self.clock.success(start) or (self.enforce_tick_window and ticks[a] > self.ins.horizon):
                raise CheckError(f"{self.ins.names[a]} 超过启动窗口")
            end = start + self.scene.observation_s
            starts[a] = start
            picks[a] = pick
            for v, p, (ready, _, pose) in zip(pick.vehicles, stations, prep):
                reset = self._turn(pose, EAST)
                tracks[v].extend((parking("READY_WAITING", ready, start, a, p, pose),
                                  parking("OBSERVING", start, end, a, p, pose),
                                  parking("RESETTING", end, end + reset, a, p, pose, EAST)))
                states[v] = State(p, EAST, "IDLE", None, None, (p,), end + reset)
                release_ticks[v] = ticks[a] + self.clock.ceil(self.scene.observation_s) + self.clock.ceil(reset)
                used.add(v)
        # 对未再使用且暂停于非东姿态的车辆复位，例如原搭档已经故障而该车不再执行任何任务。
        for v in self.ins.alive:
            state = states[v]
            if v not in used and state.release == self.prefix.dispatch and state.pose != EAST:
                duration = self._turn(state.pose, EAST)
                tracks[v].append(parking("CANCEL_RESET", state.release, state.release + duration,
                                         state.alert, state.point, state.pose, EAST))
                states[v] = State(state.point, EAST, "IDLE", None, None, (state.point,), state.release + duration)
        changes = sum(self.ins.change(p) for p in plan) + 2 * sum(a < 3 and a not in starts for a in self.ins.pending)
        return self._finish(plan, picks, starts, ticks, tracks, [s.release for s in states], (), changes, "dynamic")

    def dynamic(self, plan: Plan) -> Replay:
        """公开验收入口，错误保留为不可排名的报告；连续时刻和 tick 独立重算。"""
        try:
            return self._cached_plan(plan)
        except (CheckError, ValueError, IndexError, KeyError) as exc:
            return Replay(plan, {}, dict(self.prefix.locked), {}, self.prefix.fixed, (), None, None, (str(exc),),
                          0, "dynamic")

    def _append_states(self) -> tuple[State, ...]:
        """只从各自最后一项既定任务之后追加；不可成功的原任务不允许提前释放。"""
        result = []
        for phases in self.prefix.original:
            last = phases[-1]
            at = max(last.start, self.prefix.dispatch)
            if last.kind == "READY_WAITING":
                at = max(at, self.archive.starts[last.alert])
            result.append(State(last.points[0], last.pose0, last.kind, last.alert, None, last.points, at))
        return tuple(result)

    def original(self, added: Pick | None = None) -> Replay:
        """严格冻结或给定仅追加的 T4 站对—车型组合；原任务不改派、不延时。"""
        starts = dict(self.prefix.original_starts)
        picks = self._original_picks(starts)
        tracks = [list(track) for track in self.prefix.original]
        available = [p[-1].start for p in tracks]
        if added is not None:
            if added.alert != 3:
                raise CheckError("保留原计划只可追加 T4")
            stations = self._cached_pair(added)
            states = self._append_states()
            prep = []
            for v, p in zip(added.vehicles, stations):
                s = states[v]
                tracks[v] = list(_until(self.prefix.original[v], s.release, self.scene))
                prep.append(self._prepare(v, 3, p, s, self.clock.ceil(s.release), tracks[v]))
            start = max(r[0] for r in prep)
            if not self.clock.success(start):
                raise CheckError("追加告警无法按时启动")
            end = start + self.scene.observation_s
            starts[3], picks[3] = start, added
            for v, p, (ready, _, pose) in zip(added.vehicles, stations, prep):
                reset = self._turn(pose, EAST)
                tracks[v].extend((parking("READY_WAITING", ready, start, 3, p, pose),
                                  parking("OBSERVING", start, end, 3, p, pose),
                                  parking("RESETTING", end, end + reset, 3, p, pose, EAST)))
                available[v] = end + reset
        return self._finish((added,) if added else (), picks, starts, {}, tracks, available, (), 0,
                            "baseline_append" if added else "baseline_frozen")

    def append(self, *, pair_count: int | None = None) -> Replay:
        """按连续就绪下界验证无干扰追加，可限定为原实验目录前 pair_count 对。"""
        baseline = self.original()
        choices = self.ins.picks.get(3, ())
        if pair_count is not None:
            choices = [pick for pick in choices if pick.pair < pair_count]
        if not choices:
            return baseline
        states = self._append_states()
        points = [self.ins.points[p] for p in self.ins.by_alert[3]]
        lengths = self.ins.geometry.matrix([s.point for s in states], points)
        ready = {}
        for v in self.ins.alive:
            s = states[v]
            for index, point in enumerate(points):
                p = self.ins.by_alert[3][index]
                goal = self.ins.pose[3, p]
                if point == s.point:
                    delay = self._turn(s.pose, goal)
                else:
                    delay = (self._turn(s.pose, EAST) + lengths[v, index] / self.scene.speed_m_s + self._turn(EAST, goal))
                ready[v, p] = s.release + delay
        ordered = sorted((max(ready[v, p] for v, p in self.ins.legs(pick)), pick) for pick in choices)
        for start, pick in ordered:
            if start > HORIZON_S + EPS:
                break
            try:
                alternate = self.original(pick)
            except CheckError:
                continue
            if alternate.valid and alternate.score(self.ins) < baseline.score(self.ins):
                return alternate
        return baseline
