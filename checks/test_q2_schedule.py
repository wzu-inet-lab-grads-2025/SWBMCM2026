"""B 题第二问：用独立穷举核对离散调度和车辆结构固定时的求解。
输入三个小型两站任务与人工车辆初始点；无真实题面数值依赖。
运行：nohup python3 -B -m pytest -q -p no:cacheprovider checks/test_q2_schedule.py
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol（版本发布日期未提供），2026-11-20。
"""

from itertools import combinations, permutations, product
import math

import numpy as np
import pytest
from scipy.spatial.distance import cdist

from src.q2.schedule import Candidates, Problem, greedy, optimize


@pytest.mark.parametrize("vehicle_count", [3, 5])
def test_discrete_search_matches_independent_exhaustion(vehicle_count):
    """三车强制多边复用，五车也允许不同连通结构；独立穷举两站方向。"""
    origins = [(4, 4), (11, 5), (26, 11), (22, 8), (9, 12)][:vehicle_count]
    points = (((8, 3), (12, 3)), ((18, 9), (22, 9)), ((9, 6), (13, 6)))
    pointing = ((0.6, 0.1), (1.3, 0.1), (0.2, 0.4))
    jobs = tuple(Candidates(np.asarray(sites), np.asarray(t), np.eye(2), cdist(origins, sites))
                 for sites, t in zip(points, pointing))
    problem = Problem(jobs, np.array(origins, dtype=float), cdist, 3.0, 5.0, 3.0)
    baseline = min(greedy(problem), key=lambda p: p.latest_s)
    expected, expected_fixed = math.inf, math.inf
    for order in permutations(range(3)):
        for pairs in product(list(combinations(range(vehicle_count), 2)), repeat=3):
            for directions in product(((0, 1), (1, 0)), repeat=3):
                state = [(point, 0.) for point in origins]
                starts = []
                for j in order:
                    ready = []
                    for v, s in zip(pairs[j], directions[j]):
                        position, available = state[v]
                        ready.append(available + math.dist(position, points[j][s]) / 3 + pointing[j][s])
                    start = max(ready)
                    starts.append(start)
                    for v, s in zip(pairs[j], directions[j]):
                        state[v] = (points[j][s], start + 5 + pointing[j][s])
                value = max(starts)
                expected = min(expected, value)
                if order == baseline.order and all(pair == tuple(v for v, _ in chosen)
                                                   for pair, chosen in zip(pairs, baseline.pairs)):
                    expected_fixed = min(expected_fixed, value)

    best, proof = optimize(problem, baseline)
    fixed, _ = optimize(problem, baseline, fixed_vehicles=True)
    assert best.latest_s == pytest.approx(expected, abs=1e-9)
    assert fixed.latest_s == pytest.approx(expected_fixed, abs=1e-9)
    assert len(proof) == 6
    assert all(row["status"] == "exhausted_within_incumbent" for row in proof)
