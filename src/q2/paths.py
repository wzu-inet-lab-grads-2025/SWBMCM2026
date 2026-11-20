"""B 题第二问：用外扩建筑可见性图查询车辆的避障路长和折线。
输入场地、车体半径和 m 制起终点，由 q2 调度实验调用。
单独验证：nohup python3 -B -m pytest -q -p no:cacheprovider checks/test_q2_paths.py
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol（版本发布日期未提供），2026-11-20。
"""

from dataclasses import dataclass
from itertools import combinations, product
import math

import numpy as np
from scipy.sparse.csgraph import shortest_path
from scipy.spatial import ConvexHull
from scipy.spatial.distance import cdist

from src.q1.config import Building


Point = tuple[float, float]

# 图顶点和连边浮点舍入后仍须通过原圆盘判据。沿外包轮廓接触可通行。
PADDING_M = 1e-7
GRAPH_REVIEW_M = 1e-10
PAIR_BLOCK = 8192


@dataclass(frozen=True)
class Route:
    """以 m 计的完整中心折线；不可达时路点为空且路长无穷。"""

    waypoints: tuple[Point, ...]
    length_m: float


@dataclass(frozen=True)
class _Obstacle:
    """外扩建筑顶点、闭盒包络和以 m 计的凸半平面方程。"""

    vertices: np.ndarray
    lower: np.ndarray
    upper: np.ndarray
    normals: np.ndarray
    offsets: np.ndarray


def _inflate(building: Building, offsets: np.ndarray) -> _Obstacle:
    """楼底矩形与外切圆盘近似的 Minkowski 和由顶点和的凸包给出。"""
    corners = np.array(list(product((building.x_min, building.x_max), (building.y_min, building.y_max))))
    sums = (corners[:, None] + offsets).reshape(-1, 2)
    hull = ConvexHull(sums)
    vertices = hull.points[hull.vertices]
    return _Obstacle(vertices, vertices.min(axis=0), vertices.max(axis=0), hull.equations[:, :2],
                     hull.equations[:, 2] + GRAPH_REVIEW_M)


class VisibilityRouter:
    """题面分离楼体的保守多边形最短路，单位 m；楼体重叠或碰场界时报配置错误。"""

    def __init__(self, width_m: float, height_m: float, radius_m: float, buildings: tuple[Building, ...], *,
                 sides: int):
        """建立场地角点与凸障碍顶点的图，并保存固定图最短距离和前驱。"""
        if sides < 4 or sides % 4:
            raise ValueError("圆盘近似边数必须为 4 的正整数倍")
        self.lower = np.array((radius_m, radius_m))
        self.upper = np.array((width_m - radius_m, height_m - radius_m))
        self.max_excess_m = (radius_m + PADDING_M) / math.cos(math.pi / sides) - radius_m
        angles = (2 * np.arange(sides) + 1) * math.pi / sides
        offsets = (radius_m + self.max_excess_m) * np.column_stack((np.cos(angles), np.sin(angles)))
        self.obstacles = tuple(_inflate(b, offsets) for b in buildings)
        for b in self.obstacles:
            if np.any(b.lower <= self.lower) or np.any(b.upper >= self.upper):
                raise ValueError("外扩楼体接触场界，需重新求可见性图轮廓")
        for a, b in combinations(self.obstacles, 2):
            if not (np.any(a.upper < b.lower) or np.any(b.upper < a.lower)):
                # 两个凸包分离时，至少有一条候选边的法线是分离轴。
                separated = any(np.any(np.all(q.vertices @ p.normals.T + p.offsets >= 0, axis=0))
                                for p, q in ((a, b), (b, a)))
                if not separated:
                    raise ValueError("外扩楼体相交，需先合并外轮廓")
        field = np.array(list(product(*zip(self.lower, self.upper))))
        self.vertices = np.concatenate((field, *(b.vertices for b in self.obstacles)))
        graph = self._direct(self.vertices, self.vertices)
        graph = np.minimum(graph, graph.T)
        self.graph_dist, self.predecessor = shortest_path(graph, directed=False, method="FW", return_predecessors=True)

    def admits(self, points: np.ndarray) -> np.ndarray:
        """查端点是否属于保守路径场地；允许外包多边形边界。"""
        admitted = np.all((points >= self.lower) & (points <= self.upper), axis=1)
        for b in self.obstacles:
            admitted &= ~np.all(points @ b.normals.T + b.offsets < 0, axis=1)
        return admitted

    def _direct(self, origins: np.ndarray, destinations: np.ndarray) -> np.ndarray:
        """返回直达路长矩阵。凸多边形内部与开线段相交时对应值置无穷。"""
        lengths = cdist(origins, destinations)
        for b in self.obstacles:
            # 先排除包围框互离的点对，再对其余点对计算穿越所有凸半平面的参数区间。
            hits_box = np.all(
                (np.minimum(origins[:, None], destinations) <= b.upper)
                & (np.maximum(origins[:, None], destinations) >= b.lower), axis=2,
            )
            rows, cols = np.where(hits_box & np.isfinite(lengths))
            if not len(rows):
                continue
            initial_planes = origins @ b.normals.T + b.offsets
            final_planes = destinations @ b.normals.T + b.offsets
            for offset in range(0, len(rows), PAIR_BLOCK):
                i, j = rows[offset:offset + PAIR_BLOCK], cols[offset:offset + PAIR_BLOCK]
                start, end = initial_planes[i], final_planes[j]
                crosses_in = (start >= 0) & (end < 0)
                crosses_out = (start < 0) & (end >= 0)
                ratio = np.divide(start, start - end, out=np.zeros_like(start), where=crosses_in | crosses_out)
                enter = np.max(np.where(crosses_in, ratio, 0.0), axis=1)
                leave = np.min(np.where(crosses_out, ratio, 1.0), axis=1)
                interior = (enter < leave) & ~np.any((start >= 0) & (end >= 0), axis=1)
                lengths[i[interior], j[interior]] = np.inf
        return lengths

    def distances(self, origins, destinations) -> np.ndarray:
        """批量计算保守场地中最短路长（m）；返回行起点、列终点的矩阵。"""
        a = np.asarray(origins, dtype=float).reshape(-1, 2)
        b = np.asarray(destinations, dtype=float).reshape(-1, 2)
        lengths = np.full((len(a), len(b)), np.inf)
        valid_a, valid_b = np.flatnonzero(self.admits(a)), np.flatnonzero(self.admits(b))
        direct_to_a = self._direct(a[valid_a], self.vertices)
        direct_to_b = self._direct(b[valid_b], self.vertices)
        for offset in range(0, len(valid_a), 32):
            rows = valid_a[offset:offset + 32]
            block = self._direct(a[rows], b[valid_b])
            i, j = np.where(np.isinf(block))
            if len(i):
                path_to_graph = np.min(direct_to_a[offset:offset + len(rows), :, None] + self.graph_dist, axis=1)
                for start in range(0, len(i), PAIR_BLOCK):
                    p, q = i[start:start + PAIR_BLOCK], j[start:start + PAIR_BLOCK]
                    block[p, q] = np.min(path_to_graph[p] + direct_to_b[q], axis=1)
            lengths[np.ix_(rows, valid_b)] = block
        return lengths

    def route(self, origin: Point, destination: Point) -> Route:
        """恢复可见性图最短路的一条折线；原地观测只返回一个点。"""
        a, b = np.array([origin]), np.array([destination])
        if not self.admits(a)[0] or not self.admits(b)[0]:
            return Route((), math.inf)
        if origin == destination:
            return Route((origin,), 0.0)
        if math.isfinite(self._direct(a, b)[0, 0]):
            return Route((origin, destination), math.dist(origin, destination))

        access_a = self._direct(a, self.vertices)[0]
        access_b = self._direct(b, self.vertices)[0]
        options = access_a[:, None] + self.graph_dist + access_b
        first, last = np.unravel_index(options.argmin(), options.shape)
        if not math.isfinite(options[first, last]):
            return Route((), math.inf)
        reverse = [last]
        while reverse[-1] != first:
            reverse.append(self.predecessor[first, reverse[-1]])
        route = [origin, *(tuple(map(float, self.vertices[node])) for node in reversed(reverse)), destination]
        points = tuple(point for i, point in enumerate(route) if i == 0 or point != route[i - 1])
        return Route(points, math.fsum(math.dist(a, b) for a, b in zip(points, points[1:])))
