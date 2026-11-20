"""B 题 2（2）：在有限站点集上联合选择车辆、站位和复用顺序。
输入候选站、初始路长及路径查询，返回最晚开测时刻和逐车方案。
调用 nohup python3 -B -m src.run q2 --profile full 运行题目实例。
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol（版本发布日期未提供），2026-11-20。
"""

from collections.abc import Callable
from dataclasses import dataclass
from itertools import combinations, permutations, product
import math
import time

import numpy as np
from scipy.spatial.distance import cdist


SitePair = tuple[tuple[int, int], tuple[int, int]]
SELECT_TOL_S = 1e-9
SINE_ZERO = 1e-12


@dataclass(frozen=True)
class Candidates:
    """单告警的站点（m）、到站指向时间（s）、单位水平视线和初始路长（m）。"""

    points: np.ndarray
    slew: np.ndarray
    bearing: np.ndarray
    initial_distance: np.ndarray


@dataclass(frozen=True)
class Problem:
    """调度输入；候选站路长查询必须与初始路长使用同一最短路模型。"""

    jobs: tuple[Candidates, ...]
    origins: np.ndarray
    distance: Callable[[np.ndarray, np.ndarray], np.ndarray]
    speed_m_s: float
    observation_s: float
    min_pair_distance_m: float

    def pair_mask(self, target: int, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        """返回两候选集合间的合法配对矩阵；要求车体驻站互不重叠、视线非共线。"""
        job = self.jobs[target]
        a, b = job.bearing[left], job.bearing[right]
        sin_angle = a[:, :1] * b[:, 1] - a[:, 1:] * b[:, 0]
        distance = cdist(job.points[left], job.points[right], metric="sqeuclidean")
        return (distance >= self.min_pair_distance_m**2) & (np.abs(sin_angle) > SINE_ZERO)


@dataclass(frozen=True)
class Plan:
    """固定方案：告警拓扑序、按告警索引的车/站对、各告警开测时间（s）。"""

    order: tuple[int, ...]
    pairs: tuple[SitePair, ...]
    starts_s: tuple[float, ...]

    @property
    def latest_s(self) -> float:
        """题面目标为三告警联合观测的最晚启动时刻。"""
        return max(self.starts_s)


def _nearest_pair(problem: Problem, target: int, lengths: np.ndarray, free: np.ndarray) -> SitePair:
    """给定车辆可用时刻（s）及路长（m），按两车总路长、同步开测、编号贪心。"""
    job = problem.jobs[target]
    ranks = [np.argsort(row, kind="stable") for row in lengths]
    best = (math.inf, math.inf, -1, -1, -1, -1)
    for a, b in combinations(range(len(lengths)), 2):
        ds_a, ds_b = lengths[a], lengths[b]
        ni, nj = ranks[a], ranks[b]
        if not math.isfinite(ds_a[ni[0]] + ds_b[nj[0]]):
            continue
        min_b = ds_b[nj[0]]
        for i in ni:
            if not np.isfinite(ds_a[i] + min_b) or ds_a[i] + min_b > best[0]:
                break
            # 按右车距离有序扫描，最短合法者之外只需核对同距离的并列。
            # 一次取一小批可避免为明显远离的点对构造 n×n 配对矩阵。
            first_distance = math.inf
            for begin in range(0, len(nj), 16):
                block = nj[begin:begin + 16]
                limit = min(best[0], first_distance)
                summed = ds_a[i] + ds_b[block]
                if not np.isfinite(summed[0]) or summed[0] > limit:
                    break
                allowed = np.isfinite(summed) & (summed <= limit) & problem.pair_mask(target, np.array([i]), block)[0]
                for j, length in zip(block[allowed], summed[allowed]):
                    start = max(free[a] + ds_a[i] / problem.speed_m_s + job.slew[i],
                                free[b] + ds_b[j] / problem.speed_m_s + job.slew[j])
                    best = min(best, (length, start, a, b, int(i), int(j)))
                    first_distance = min(first_distance, length)
    if not math.isfinite(best[0]):
        raise ValueError("当前告警不存在可达的就近双站")
    _, _, a, b, i, j = best
    return (a, i), (b, j)


def replay(problem: Problem, order: tuple[int, ...], pairs: tuple[SitePair, ...]) -> Plan:
    """固定车/站后以原始浮点路长和云台时长重放，不添加全局串行约束。"""
    positions, free = problem.origins.copy(), np.zeros(len(problem.origins))
    starts, previous = [0.] * len(problem.jobs), np.full(len(free), -1)
    for target in order:
        job = problem.jobs[target]
        ready = []
        for vehicle, site in pairs[target]:
            destination = job.points[site]
            length = (job.initial_distance[vehicle, site] if previous[vehicle] < 0
                      else problem.distance(positions[vehicle:vehicle + 1], destination[None])[0, 0])
            ready.append(free[vehicle] + length / problem.speed_m_s + job.slew[site])
            positions[vehicle], previous[vehicle] = destination, target
        starts[target] = float(max(ready))
        for vehicle, site in pairs[target]:
            free[vehicle] = starts[target] + problem.observation_s + job.slew[site]
    return Plan(order, pairs, tuple(starts))


def greedy(problem: Problem) -> list[Plan]:
    """六个优先序各做一次就近双车分配，保留全部可行结果。"""
    plans = []
    for order in permutations(range(len(problem.jobs))):
        positions, free = problem.origins.copy(), np.zeros(len(problem.origins))
        pairs = {}
        for target in order:
            job = problem.jobs[target]
            lengths = job.initial_distance.copy()
            shifted = np.flatnonzero(np.any(positions != problem.origins, axis=1))
            if len(shifted):
                lengths[shifted] = problem.distance(positions[shifted], job.points)
            pair = pairs[target] = _nearest_pair(problem, target, lengths, free)
            start = max(free[v] + lengths[v, p] / problem.speed_m_s + job.slew[p] for v, p in pair)
            for v, p in pair:
                positions[v] = job.points[p]
                free[v] = start + problem.observation_s + job.slew[p]
        plans.append(replay(problem, order, tuple(pairs[j] for j in range(len(order)))))
    return plans


def remap_plan(plan: Plan, original: Problem, updated: Problem) -> Plan:
    """在含相同坐标的新候选集和路径表上回放旧方案；原站点缺失时报告键错误。"""
    lookups = [{tuple(point): site for site, point in enumerate(job.points)} for job in updated.jobs]
    pairs = tuple(tuple((v, lookups[t][tuple(original.jobs[t].points[s])]) for v, s in pair)
                  for t, pair in enumerate(plan.pairs))
    return replay(updated, plan.order, pairs)


class _Search:
    """在一个可行开测上界内，用车辆枚举和站位分支定界求解浮点调度。"""

    def __init__(self, problem: Problem, incumbent: Plan):
        """从最短路三角不等式构造每辆车各告警的安全站点域。"""
        self.problem = problem
        self.best = incumbent
        self.cap = incumbent.latest_s + SELECT_TOL_S
        direct = tuple(job.initial_distance / problem.speed_m_s + job.slew for job in problem.jobs)
        self.domains = tuple(tuple(np.flatnonzero(row <= self.cap) for row in ready) for ready in direct)
        self.initial = tuple(tuple(row[domain] for row, domain in zip(ready, domains))
                             for ready, domains in zip(direct, self.domains))
        self.minimum = np.array([[np.min(row, initial=np.inf) for row in rows] for rows in self.initial])
        self.transition_cache = {}
        self.nodes = 0

    def transition(self, first: int, second: int, vehicle: int) -> np.ndarray:
        """前次开测至下次准备的权重（s），对同一车使用裁剪后的站点域。"""
        if first > second:
            return self.transition(second, first, vehicle).T
        key = (first, second, vehicle)
        if key not in self.transition_cache:
            a, b = self.domains[first][vehicle], self.domains[second][vehicle]
            job_a, job_b = self.problem.jobs[first], self.problem.jobs[second]
            travel_floor = cdist(job_a.points[a], job_b.points[b]) / self.problem.speed_m_s
            fixed = job_a.slew[a, None] + job_b.slew[b] + self.problem.observation_s
            earliest_a = np.maximum(self.initial[first][vehicle], np.min(np.delete(self.minimum[first], vehicle)))
            earliest_b = np.maximum(self.initial[second][vehicle], np.min(np.delete(self.minimum[second], vehicle)))
            # 两个方向共用转场权重。若任一方向仍可能在上界内接续，则保留该项。
            possible = (np.minimum(earliest_a[:, None], earliest_b) + fixed + travel_floor <= self.cap)
            rows, cols = np.flatnonzero(possible.any(axis=1)), np.flatnonzero(possible.any(axis=0))
            weights = np.full_like(travel_floor, np.inf)
            if len(rows) and len(cols):
                length = self.problem.distance(job_a.points[a[rows]], job_b.points[b[cols]])
                section = np.ix_(rows, cols)
                weights[section] = np.where(possible[section], fixed[section] + length / self.problem.speed_m_s, np.inf)
            self.transition_cache[key] = weights
        return self.transition_cache[key]

    def search_assignment(self, order: tuple[int, ...], vehicle_pairs: tuple[tuple[int, int], ...]) -> bool:
        """检查固定车辆组合；返回是否通过下界进入站位搜索。"""
        pred, succ, latest_visit = {}, {}, {}
        earliest, edge_floor = {}, {}
        for target, pair in zip(order, vehicle_pairs):
            bounds = []
            for v in pair:
                previous = pred[target, v] = latest_visit.get(v)
                latest_visit[v] = target
                bound = self.minimum[target, v]
                if previous is not None:
                    succ[previous, v] = target
                    # 先用至少有 5 s 观测的廉价下界，避免为明显超时的车次建距离表。
                    if earliest[previous] + self.problem.observation_s > self.best.latest_s + SELECT_TOL_S:
                        return False
                    weights = self.transition(previous, target, v)
                    edge_floor[previous, v] = float(np.min(weights, initial=np.inf))
                    bound = max(bound, earliest[previous] + edge_floor[previous, v])
                bounds.append(bound)
            earliest[target] = max(bounds)
            if earliest[target] > self.best.latest_s + SELECT_TOL_S:
                return False

        tail = {}
        for target, pair in reversed(list(zip(order, vehicle_pairs))):
            tail[target] = max((edge_floor[target, v] + tail[succ[target, v]] for v in pair if (target, v) in succ),
                               default=0.0)

        starts = {}
        selected = {}

        def visit(depth: int) -> None:
            """逐告警考察站位；同未来状态下只保留最早共同开测。"""
            self.nodes += 1
            if depth == len(order):
                if max(starts.values()) < self.best.latest_s:
                    pairs = tuple(tuple((v, int(self.domains[t][v][p])) for v, p in selected[t])
                                  for t in range(len(order)))
                    self.best = Plan(order, pairs, tuple(starts[t] for t in range(len(order))))
                return
            target = order[depth]
            vehicles = vehicle_pairs[depth]
            deadline = self.best.latest_s + SELECT_TOL_S
            durations, site_ids, remaining = [], [], []
            kept = []
            for v in vehicles:
                previous, following = pred[target, v], succ.get((target, v))
                ready = self.initial[target][v] if previous is None else (
                    starts[previous] + self.transition(previous, target, v)[dict(selected[previous])[v]])
                forward = (np.min(self.transition(target, following, v), axis=1, initial=np.inf) + tail[following]
                           if following is not None else np.zeros(len(ready)))
                # 同队的另一辆车也未必能立即就绪；先用每车乐观时刻筛掉无法接续的站。
                use = np.flatnonzero(np.maximum(ready, earliest[target]) + np.maximum(tail[target], forward) <= deadline)
                if not len(use):
                    return
                kept.append(use)
                durations.append(ready[use])
                site_ids.append(self.domains[target][v][use])
                remaining.append(forward[use])

            start = np.maximum(durations[0][:, None], durations[1])
            future = np.maximum(tail[target], np.maximum(remaining[0][:, None], remaining[1]))
            allowed = (start + future <= deadline) & self.problem.pair_mask(target, *site_ids)
            start[~allowed] = np.inf
            reused = [(target, v) in succ for v in vehicles]
            if all(reused):
                i, j = np.where(allowed)
            elif reused[0]:
                i, j = np.arange(start.shape[0]), np.argmin(start, axis=1)
            elif reused[1]:
                i, j = np.argmin(start, axis=0), np.arange(start.shape[1])
            else:
                row, col = np.unravel_index(np.argmin(start), start.shape)
                i, j = np.array([row]), np.array([col])
            bounds = (start + future)[i, j]
            for idx in np.argsort(bounds, kind="stable"):
                if bounds[idx] > self.best.latest_s + SELECT_TOL_S:
                    break
                a, b = i[idx], j[idx]
                starts[target] = float(start[a, b])
                selected[target] = ((vehicles[0], kept[0][a]), (vehicles[1], kept[1][b]))
                visit(depth + 1)

        visit(0)
        return True


def optimize(problem: Problem, incumbent: Plan, *, fixed_vehicles: bool = False) -> tuple[Plan, list[dict]]:
    """穷尽车辆组合和六个拓扑序；提供上界内的穷尽记录，或仅搜索给定车辆结构。"""
    search = _Search(problem, incumbent)
    records = []
    orders = (incumbent.order,) if fixed_vehicles else permutations(range(len(problem.jobs)))
    for order in orders:
        begin = time.perf_counter()
        old_best, old_nodes = search.best.latest_s, search.nodes
        if fixed_vehicles:
            options = [(tuple(v for v, _ in incumbent.pairs[t]),) for t in order]
        else:
            options = [list(combinations(np.flatnonzero(np.isfinite(search.minimum[t])), 2)) for t in order]
        total, entered = 0, 0
        for pairs in product(*options):
            total += 1
            entered += search.search_assignment(order, pairs)
        records.append({"order": order, "status": "exhausted_within_incumbent", "assignments_considered": total,
                        "assignments_searched": entered, "station_search_nodes": search.nodes - old_nodes,
                        "cap_before_s": old_best, "global_cap_after_s": search.best.latest_s,
                        "improved_in_order": search.best.latest_s < old_best, "seconds": time.perf_counter() - begin})
    result = replay(problem, search.best.order, search.best.pairs)
    if not math.isclose(result.latest_s, search.best.latest_s, rel_tol=0, abs_tol=SELECT_TOL_S):
        raise RuntimeError("站位搜索的开测时间与独立递推不一致")
    return result, records


def shorten_single_use_routes(problem: Problem, plan: Plan) -> Plan:
    """固定复用车站位，为只出车一次的车辆选不推迟原开测且路长更短的站。"""
    counts = np.bincount([v for pair in plan.pairs for v, _ in pair], minlength=len(problem.origins))
    pairs = list(plan.pairs)
    for target, pair in enumerate(plan.pairs):
        job = problem.jobs[target]
        lengths = np.full_like(job.initial_distance, np.inf)
        for v, site in pair:
            if counts[v] == 1:
                ready = job.initial_distance[v] / problem.speed_m_s + job.slew
                lengths[v] = np.where(ready <= plan.starts_s[target] + SELECT_TOL_S, job.initial_distance[v], np.inf)
            else:
                # 常数零值只用于本步路长择优；复用车相同的已选站位和实际时序不改变。
                lengths[v, site] = 0.
        pairs[target] = _nearest_pair(problem, target, lengths, np.zeros(len(problem.origins)))
    tightened = replay(problem, plan.order, tuple(pairs))
    if any(new > old + SELECT_TOL_S for new, old in zip(tightened.starts_s, plan.starts_s)):
        raise RuntimeError("单次用车的路长择优推迟了原开测时刻")
    return tightened
