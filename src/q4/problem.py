"""B 题第四问共享的有限站对调度模型：把连续接续转换成保守整数准备时间。
输入归档、情景快照和站对；为四个求解器提供同一组状态、转移和官方目标。
单独核查：nohup python3 -B -m pytest -q checks/test_q4.py。
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol，2026-11-20（版本发布日期未提供）。
"""

from dataclasses import dataclass
from itertools import permutations
import math

import numpy as np

from .candidates import Catalog, Geometry, Scenario, StationPair, digest, station_pose, target_config
from .physics import Archive, Clock, EAST, EPS, HORIZON_S, Prefix, aim_time, continuing_route, route_length


@dataclass(frozen=True, order=True)
class Pick:
    """一次双车任务：告警下标、站对下标（站位顺序固定）、两站对应车号下标。"""

    alert: int
    pair: int
    first: int
    second: int

    @property
    def vehicles(self) -> tuple[int, int]:
        """按站对规范的第一站、第二站返回车号。"""
        return self.first, self.second


Plan = tuple[Pick, ...]  # 任一全局拓扑序；无公共车辆的相邻告警可以并行。


class Instance:
    """求解器共享实例；几何为 m/度，预计算持续时间、下界和时限是整数 tick。"""

    def __init__(self, archive: Archive, geometry: Geometry, scenario: Scenario, prefix: Prefix, clock: Clock,
                 catalog: Catalog | None, *, extra_pairs: dict[int, tuple[StationPair, ...]] | None = None):
        """将初始状态及多路径准备时间变成只读数组；未修改原归档。"""
        self.archive, self.geometry, self.scenario, self.prefix, self.clock = archive, geometry, scenario, prefix, clock
        self.catalog = catalog
        self.names = (*archive.scene.targets, "T4") if scenario.location else archive.scene.targets
        self.targets = tuple(c.target_xyz for c in archive.scene.target_cfg)
        if scenario.location:
            self.targets = (*self.targets, target_config(archive, scenario.location).target_xyz)
        self.pending = tuple(a for a in range(len(self.names)) if a not in prefix.locked)
        self.alive = tuple(v for v in range(len(prefix.states)) if v != prefix.failed)
        self.dispatch_step = clock.ceil(prefix.dispatch)
        self.horizon = clock.last_step
        self.observation = clock.ceil(archive.scene.observation_s)
        self.impossible = clock.ceil(HORIZON_S) * 10
        self.pairs = {a: (pair,) for a, pair in enumerate(archive.pairs)}
        if extra_pairs:
            for a, pairs in extra_pairs.items():
                self.pairs[a] = tuple(dict.fromkeys((*self.pairs[a], *pairs)))
        if scenario.location:
            self.pairs[3] = () if catalog is None else catalog.pairs
        self.points = tuple(dict.fromkeys(p for pairs in self.pairs.values() for pair in pairs for p in pair))
        ids = {point: i for i, point in enumerate(self.points)}
        self.sites = {a: tuple((ids[p], ids[q]) for p, q in self.pairs[a]) for a in self.pairs}
        self.by_alert = {a: sorted({p for pair in self.sites[a] for p in pair}) for a in self.pairs}
        self.picks = {a: tuple(Pick(a, i, v, w) for i in range(len(self.pairs[a]))
                               for v, w in permutations(self.alive, 2)) for a in self.pending}
        self.vector_picks = {a: np.array([(p.first, p.second, *self.sites[a][p.pair], self.change(p))
                                         for p in picks], dtype=np.int32).reshape(-1, 5).T
                             for a, picks in self.picks.items()}
        self._build_durations()

    def _build_durations(self) -> None:
        """生成首次就绪与两任务接续；同站改派直接从当前指向转到新目标。"""
        scene, horizon = self.archive.scene, self.impossible
        dims = (len(self.names), len(self.points))
        self.pose = {(a, p): station_pose(self.points[p], self.targets[a], scene.field.camera_height)
                     for a in self.pending for p in self.by_alert[a]}
        self.reset = np.full(dims, horizon, dtype=np.int32)
        self.first = np.full((len(scene.vehicles), *dims), horizon, dtype=np.int32)
        self.transfer = np.full((*dims, *dims), horizon, dtype=np.int32)
        for (a, p), pose in self.pose.items():
            self.reset[a, p] = self.clock.ceil(aim_time(EAST, pose, scene.gimbal_deg_s))
        if not self.pose:
            self.path_hash = digest({"prefix_only": True})
            return
        active = sorted({p for _, p in self.pose})
        # 每场只有原有站和一个新告警的大候选集，不计算同告警站之间的行驶。
        new_only = set(self.by_alert.get(3, [])) - {p for a in self.pending if a != 3 for p in self.by_alert[a]}
        other = sorted(set(active) - new_only)
        initial = self.geometry.matrix([s.point for s in self.prefix.states], [self.points[p] for p in active])
        links = self.geometry.matrix([self.points[p] for p in other], [self.points[p] for p in active])
        active_id, other_id = {p: i for i, p in enumerate(active)}, {p: i for i, p in enumerate(other)}
        for (a, p), pose in self.pose.items():
            station = self.points[p]
            for v in self.alive:
                state = self.prefix.states[v]
                release = self.clock.ceil(state.release)
                attitude = EAST if state.release > self.prefix.dispatch + EPS else state.pose
                if state.point == station:
                    need = self.clock.ceil(aim_time(attitude, pose, scene.gimbal_deg_s))
                else:
                    ongoing = continuing_route(state, station)
                    length = route_length(ongoing) if ongoing else float(initial[v, active_id[p]])
                    if not math.isfinite(length):
                        continue
                    need = (self.clock.ceil(aim_time(attitude, EAST, scene.gimbal_deg_s)) +
                            self.clock.ceil(length / scene.speed_m_s) + int(self.reset[a, p]))
                self.first[v, a, p] = release + need
            for b in self.pending:
                if a == b:
                    continue
                for q in self.by_alert[b]:
                    length = links[other_id[p], active_id[q]] if p in other_id else links[other_id[q], active_id[p]]
                    if math.isfinite(length):
                        self.transfer[a, p, b, q] = (self.observation + int(self.reset[a, p]) +
                                                      self.clock.ceil(float(length) / scene.speed_m_s) +
                                                      int(self.reset[b, q]))
        # 以路径图路长/过边时间共同标识四种方法的真实输入，而非以目录名代替。
        self.path_hash = digest({"points_m": self.points, "first_tick": self.first.tolist(),
                                 "paths_m": (initial.tobytes().hex(), links.tobytes().hex()),
                                 "continued": [s.remaining_route for s in self.prefix.states],
                                 "transfer_used": [(a, b, self.transfer[a, :, b, :].tobytes().hex())
                                                   for a in self.pending for b in self.pending if a != b]})

    def change(self, pick: Pick) -> int:
        """与原计划对比站—车席位数差异；T4 是新增请求，其席位不计为改动。"""
        if pick.alert == 3:
            return 0
        return 2 if pick.pair else sum(v != w for v, w in zip(pick.vehicles, self.archive.assignment[pick.alert]))

    def legs(self, pick: Pick) -> tuple[tuple[int, int], tuple[int, int]]:
        """返回规范站序中的两个 (车下标, 全局站下标)。"""
        return tuple(zip(pick.vehicles, self.sites[pick.alert][pick.pair]))

    def score(self, plan: Plan, starts: dict[int, int]) -> tuple[int, int, int, int]:
        """规划用整数序：先成功数、其次官方 t_last；再提前成功任务并减少席位改动。"""
        k = len(starts) + len(self.prefix.locked)
        last = max(starts.values(), default=0) if k == len(self.names) else self.clock.ceil(HORIZON_S)
        dropped = sum(a < 3 and a not in starts for a in self.pending)
        return (-k, last, sum(starts.values()), 2 * dropped + sum(map(self.change, plan)))

    def predict(self, plan: Plan) -> dict[int, int] | None:
        """快速按先后序计算整数就绪。若任一选中告警不能按期启动则返回空。"""
        previous = {}
        starts = {}
        for pick in plan:
            if pick.alert in starts:
                return None
            a = pick.alert
            ready = []
            for v, p in self.legs(pick):
                if v in previous:
                    b, q = previous[v]
                    ready.append(starts[b] + int(self.transfer[b, q, a, p]))
                else:
                    ready.append(int(self.first[v, a, p]))
                previous[v] = (a, p)
            starts[a] = max(ready)
            if starts[a] > self.horizon:
                return None
        return starts

    def insertions(self, remainder: Plan, alert: int, *, top: int = 5):
        """同时计算每个插入位置下的全部站对/车对，返回最优若干整数计划及其分数。"""
        picks = self.picks[alert]
        if not picks:
            return []
        v1, v2, p1, p2, changes = self.vector_picks[alert]
        options = {}
        n = len(picks)
        rest_change = sum(map(self.change, remainder))
        dropped = sum(a < 3 and a != alert and all(pick.alert != a for pick in remainder) for a in self.pending)
        k = len(remainder) + 1 + len(self.prefix.locked)
        for position in range(len(remainder) + 1):
            last_alert = [(None, None)] * len(self.prefix.states)
            pprev = [p1, p2]
            starts = {}
            # 只变化一条告警的配置；其他任务仍可在同一向量分量上并行开始。
            for j in range(len(remainder) + 1):
                if j == position:
                    a = alert
                    r = []
                    for v, p in zip((v1, v2), pprev):
                        ready = self.first[v, a, p].copy()
                        for vehicle, (prev, site) in enumerate(last_alert):
                            if prev is not None:
                                use = v == vehicle
                                ready[use] = starts[prev] + self.transfer[prev, site, a, p[use]]
                        r.append(ready)
                    starts[a] = np.maximum(*r)
                else:
                    pick = remainder[j if j < position else j - 1]
                    a = pick.alert
                    r = []
                    for v, p in self.legs(pick):
                        prev, site = last_alert[v]
                        ready = self.first[v, a, p] if prev is None else starts[prev] + self.transfer[prev, site, a, p]
                        if j > position:
                            # 只有插入告警之后该车的首个车次会以该告警为紧前任务。
                            if prev is None or prev in {item.alert for item in remainder[:position]}:
                                ready = np.where(v1 == v, starts[alert] + self.transfer[alert, p1, a, p], ready)
                                ready = np.where(v2 == v, starts[alert] + self.transfer[alert, p2, a, p], ready)
                        r.append(ready)
                        last_alert[v] = (a, p)
                    starts[a] = np.maximum(*r)
            latest = np.maximum.reduce([np.broadcast_to(t, (n,)) for t in starts.values()])
            eligible = np.flatnonzero(latest <= self.horizon)
            if not len(eligible):
                continue
            total = sum(starts.values())
            last = latest if k == len(self.names) else np.full(n, self.clock.ceil(HORIZON_S))
            order = np.lexsort((eligible, changes[eligible], total[eligible], last[eligible]))[:top]
            for c in eligible[order]:
                plan = remainder[:position] + (picks[c],) + remainder[position:]
                options[plan] = (-k, int(last[c]), int(total[c]), 2 * dropped + rest_change + int(changes[c]))
        return sorted(options.items(), key=lambda row: (row[1], row[0]))[:top]
