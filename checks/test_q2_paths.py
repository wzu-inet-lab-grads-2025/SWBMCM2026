"""B 题第二问：用人工场地复核公开的折线和批量距离查询。
输入小型楼体配置，不依赖正式赛题的最优结果。
运行：nohup python3 -B -m pytest -q -p no:cacheprovider checks/test_q2_paths.py
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol（版本发布日期未提供），2026-11-20。
"""

from dataclasses import replace
import math

import pytest

from checks.q2_geometry import check_route, load_scene
from src.q1.config import Building
from src.q2.paths import VisibilityRouter


def test_visibility_routes_around_inflated_rectangle_and_preserves_direct_segments():
    """跨过楼体的东西向路段必须绕行；未受阻的路段取欧氏距离。"""
    scene = replace(load_scene(), width_m=20., height_m=20., buildings=(Building("wall", 8, 12, 7, 13, 6),))
    router = VisibilityRouter(scene.width_m, scene.height_m, scene.radius_m, scene.buildings, sides=32)
    origin, blocked, clear = (4, 10), (16, 10), (4, 16)
    table = router.distances([origin, blocked], [blocked, clear])
    route = router.route(origin, blocked)
    measured = check_route(scene, route.waypoints, origin)
    assert table[0, 0] == pytest.approx(measured["length_m"])
    assert table[1, 0] == 0
    assert 15 < route.length_m < 18
    assert table[0, 1] == math.dist(origin, clear)
    assert router.route(origin, clear).waypoints == (origin, clear)
    assert router.route(blocked, origin).length_m == pytest.approx(route.length_m)
