"""B 题第四问恢复后连续重调：以实时车位联合搜索车对、站位和复用次序。
输入场地、可见站和真实快照；输出供独立轨迹回放筛选的连续时间方案。
运行：nohup python3 -B -m src.run q4 --recourse <Q4 主归档目录>。
"""

from dataclasses import dataclass
from itertools import combinations, product
import math

import numpy as np

from src.q1.region import station_masks

from .candidates import CandidateBank, Geometry, station_pose, target_config
from .physics import EAST, EPS, HORIZON_S, Archive, Prefix, aim_time, continuing_route, route_length


@dataclass(frozen=True, order=True)
class Visit:
    """一条未锁定告警的两个车辆和两个场地点下标，顺序为车辆编号升序。"""

    alert: int
    first: int
    second: int
    left: int
    right: int


@dataclass(frozen=True)
class Branch:
    """当前任务序及各车末站、释放时刻（s）。-1 表示按扰动快照接续。"""

    visits: tuple[Visit, ...]
    stations: tuple[int, ...]
    available: tuple[float, ...]
    remaining: tuple[int, ...]
    latest: float
    total: float

    def rank(self):
        """中间状态用已安排告警的最晚开测、合计开测和资源释放排序。"""
        return round(self.latest, 9), round(self.total, 9), sum(self.available), self.visits


class SiteTable:
    """同一新告警位置的场地候选缓存。几何为 m/度，预测时钟用连续秒。"""

    def __init__(self, archive: Archive, geometry: Geometry, bank: CandidateBank, location, prefixes, settings):
        """准备通用的合法候选与距离表，只允许各场景使用本场实际快照点。"""
        scene = archive.scene
        target_cfgs = (*scene.target_cfg, target_config(archive, location)) if location else scene.target_cfg
        self.areas = np.array([settings["vehicle_areas_xyxy_m"][name] for name in scene.vehicles], dtype=float)
        n = settings["grid_points_per_axis"]
        if n < 2 or np.any(self.areas[:, :2] >= self.areas[:, 2:]):
            raise ValueError("近站网格需要至少两点且矩形边长为正")
        near = [p for w, s, e, top in self.areas for p in product(np.linspace(w, e, n), np.linspace(s, top, n))]
        if geometry.wall_clearance_m:
            # 保留区域内部候选，只将临场界的一排移入有效场地。
            near = np.clip(near, geometry.router.lower, geometry.router.upper)
        near = [tuple(map(float, p)) for p in near]
        fixed = {*map(tuple, scene.origins.tolist()), *(p for pair in archive.pairs for p in pair), *near}
        self.catalogs = tuple(bank.for_target(cfg) for cfg in target_cfgs)
        self.baseline_catalog = self.catalogs[3] if location else None
        supplemental = [{p for pair in catalog.pairs for p in pair} for catalog in self.catalogs]
        moving = {state.point for prefix in prefixes for state in prefix.states}
        self.points = tuple(sorted(fixed | moving | set().union(*supplemental)))
        self.index = {p: i for i, p in enumerate(self.points)}
        self.fixed_allowed = np.ones((len(target_cfgs), 1, len(self.points)), dtype=bool)
        for a, pair in enumerate(archive.pairs):
            self.fixed_allowed[a] = False
            self.fixed_allowed[a, 0, [self.index[p] for p in pair]] = True
        xy = np.array(self.points)
        usable = geometry.router.admits(xy)
        self.static = np.array([[p in fixed or p in extra for p in self.points] for extra in supplemental])
        self.near = ((xy >= self.areas[:, None, :2] - EPS) & (xy <= self.areas[:, None, 2:] + EPS)).all(axis=2)
        self.distance_s = geometry.matrix(self.points, self.points) / scene.speed_m_s
        station_space = np.linalg.norm(xy[:, None, :] - xy, axis=2) >= 2 * scene.field.vehicle_radius
        self.visible, self.pose, self.pair_ok = [], [], []
        self.scene = scene
        for cfg in target_cfgs:
            visible = usable & np.logical_and.reduce(station_masks(*xy.T, cfg))
            pose = np.array([station_pose(p, cfg.target_xyz, scene.field.camera_height) for p in self.points])
            yaw = np.radians(pose[:, 0])
            sine = np.abs(np.sin(yaw[:, None] - yaw))
            self.visible.append(visible)
            self.pose.append(pose)
            self.pair_ok.append(station_space & visible[:, None] & visible & (sine > bank.min_sine))
        self.pose = np.array(self.pose)
        self.slew = np.abs(self.pose).max(axis=2) / scene.gimbal_deg_s

    def initial(self, prefix: Prefix):
        """从本场状态向各站计入剩余锁定、取消复位、行驶和指向，单位 s。"""
        points = [self.index[s.point] for s in prefix.states]
        first = []
        for state, point in zip(prefix.states, points):
            pose = EAST if state.release > prefix.dispatch + EPS else state.pose
            turn = aim_time(pose, EAST, self.scene.gimbal_deg_s)
            travel = self.distance_s[point].copy()
            destination = state.remaining_route[-1]
            if destination in self.index:
                route = continuing_route(state, destination)
                if route is not None:
                    travel[self.index[destination]] = route_length(route) / self.scene.speed_m_s
            ready = state.release + turn + travel + self.slew
            here = np.array([aim_time(pose, goal, self.scene.gimbal_deg_s) for goal in self.pose[:, point]])
            ready[:, point] = state.release + here
            first.append(ready)
        return np.array(first), points


def _pairs(ready, legal, vehicles, width, alternatives):
    """按联合就绪时间枚举各车对的少量站对，最短名单无解时回查完整候选。"""
    idx = np.argsort(ready, axis=1, kind="stable")[:, :width]
    i, j = idx[vehicles[:, 0]], idx[vehicles[:, 1]]
    a, b = ready[vehicles[:, 0, None], i], ready[vehicles[:, 1, None], j]
    cost = np.where(legal[i[:, :, None], j[:, None, :]], np.maximum(a[:, :, None], b[:, None, :]), np.inf)
    total = a[:, :, None] + b[:, None, :]
    width = i.shape[1]
    order = np.lexsort((total.reshape(len(i), -1), cost.reshape(len(i), -1)), axis=-1)[:, :alternatives]
    for k, (v, w) in enumerate(vehicles):
        rows, cols = np.divmod(order[k], width)
        if cost[k, rows[0], cols[0]] < HORIZON_S:
            for p, q in zip(rows, cols):
                value = cost[k, p, q]
                if value < HORIZON_S:
                    yield int(v), int(w), int(i[k, p]), int(j[k, q]), float(value)
            continue
        # 近站可能重合或同射线。更远的合法组合也可在同一任务窗口内补位。
        left, right = (np.flatnonzero(ready[v] < HORIZON_S), np.flatnonzero(ready[w] < HORIZON_S))
        if not len(left) or not len(right):
            continue
        many = np.where(legal[np.ix_(left, right)], np.maximum(ready[v, left, None], ready[w, right]), np.inf)
        p, q = np.unravel_index(many.argmin(), many.shape)
        if many[p, q] < HORIZON_S:
            yield int(v), int(w), int(left[p]), int(right[q]), float(many[p, q])


def _beam(table, prefix, first, allowed, vehicles, settings, *, stations, alternatives):
    """按已完成深度保留不同资源状态，返回供后续整条轨迹核验的候选。"""
    pending = tuple(a for a in range(len(table.pose)) if a not in prefix.locked)
    initial = Branch((), (-1,) * len(prefix.states), tuple(s.release for s in prefix.states), pending,
                     max(prefix.locked.values(), default=0.0), math.fsum(prefix.locked.values()))
    frontier = [initial]
    finalists = [initial]
    lim = settings["replay_candidates"]
    width = settings["beam_width"]
    counts = 0
    for _ in pending:
        children = []
        for node in frontier:
            for a in node.remaining:
                ready = np.array([first[v, a] if p < 0 else node.available[v] + table.distance_s[p] + table.slew[a]
                                  for v, p in enumerate(node.stations)])
                ready[~allowed[a]] = np.inf
                future = tuple(other for other in node.remaining if a != other)
                for v, w, p, q, start in _pairs(ready, table.pair_ok[a], vehicles, stations, alternatives):
                    last, release = list(node.stations), list(node.available)
                    last[v], last[w] = p, q
                    release[v] = start + table.scene.observation_s + float(table.slew[a, p])
                    release[w] = start + table.scene.observation_s + float(table.slew[a, q])
                    visit = Visit(a, v, w, p, q)
                    children.append(Branch((*node.visits, visit), tuple(last), tuple(release), future,
                                           max(node.latest, start), node.total + start))
        counts += len(children)
        frontier, seen = [], set()
        for node in sorted(children, key=Branch.rank):
            key = node.remaining, node.stations, tuple(round(t, 9) for t in node.available)
            if key in seen:
                continue
            seen.add(key)
            frontier.append(node)
            if len(frontier) == width:
                break
        if not frontier:
            break
        finalists.extend(frontier[:lim])
    return finalists, counts


def propose(table: SiteTable, prefix: Prefix, settings: dict):
    """同一时钟与 T4 候选上搜索固定和可换旧站两策略，返回字典序候选和展开量。"""
    first, ids = table.initial(prefix)
    vehicles = np.array(list(combinations([v for v in range(len(ids)) if v != prefix.failed], 2)))
    shared = table.static.copy()
    shared[:, ids] = True
    broad = np.array([np.broadcast_to((selected & vis)[None], first[:, a].shape)
                      for a, (selected, vis) in enumerate(zip(shared, table.visible))])
    local = broad & table.near[None]
    # 原地继续指向可省去取消复位；快照在预选矩形外时仍允许本车用该点。
    local[:, np.arange(len(ids)), ids] = np.array(table.visible)[:, ids]
    alternatives = settings["alternatives_per_pair"]
    if isinstance(alternatives, int):
        alternatives = (alternatives,)

    def search(mask):
        """固定旧站只筛旧告警的就绪表。两策略共享新告警候选与恢复快照。"""
        local_mask = local & mask
        finalists, local_count = _beam(table, prefix, first, local_mask, vehicles, settings,
                                       stations=max(1, int(local_mask.sum(axis=2).max())), alternatives=1)
        global_count = 0
        for n in alternatives:
            # 保留较窄束的结果。增加站对时，同一任务的近似解可能挤掉后续分工不同的分支。
            cases, count = _beam(table, prefix, first, broad & mask, vehicles, settings,
                                 stations=settings["stations_per_vehicle"], alternatives=n)
            finalists.extend(cases)
            global_count += count
        return finalists, {"global_expanded": global_count, "local_expanded": local_count}

    def ordered(cases):
        """未全启的场景 t_last 统一取 60 s，再用累计启动时刻打破并列。"""
        pending = len(table.pose) - len(prefix.locked)
        unique = {branch.visits: branch for branch in cases}
        return sorted(unique.values(), key=lambda b: (-len(b.visits),
                      round(b.latest, 8) if len(b.visits) == pending else HORIZON_S,
                      round(b.total, 8), b.visits))

    fixed, fixed_counts = search(table.fixed_allowed)
    flexible, flexible_counts = search(True)
    # 允许换站扩张可行策略集，保留固定旧站的候选作为后备，防止截束损失这些解。
    return {"fixed_stations": (ordered(fixed), fixed_counts),
            "recourse": (ordered(flexible + fixed), flexible_counts)}
