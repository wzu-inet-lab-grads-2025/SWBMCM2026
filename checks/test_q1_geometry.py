"""核对 q1 题面参数和基本几何，测试输入含题面与人工边界样例。
输入为 configs/q1.yaml 和手算坐标，输出为 pytest 的断言结果。
单独运行 python -B -m pytest -p no:cacheprovider checks/test_q1_geometry.py。
"""

from pathlib import Path

import pytest


CONFIG_PATH = Path(__file__).resolve().parents[1] / "configs" / "q1.yaml"


def test_t1_coordinates_from_official_alert():
    """核对告警自正东逆时针换算及五栋建筑的官方坐标。"""
    from src.q1.config import load_config

    cfg = load_config(CONFIG_PATH)
    assert cfg.dispatch_center == (50.0, 50.0)
    assert cfg.target_xyz[0] == pytest.approx(84.6410161514, abs=1e-9, rel=0)
    assert cfg.target_xyz[1:] == pytest.approx((70.0, 45.0), abs=1e-12, rel=0)
    assert cfg.elevation_bounds_deg == (-10.0, 80.0)
    assert (cfg.field_width, cfg.field_height, cfg.vehicle_radius, cfg.camera_height) == (
        100.0, 100.0, 1.5, 2.0,
    )
    assert [(b.id, b.x_min, b.x_max, b.y_min, b.y_max, b.height) for b in cfg.buildings] == [
        ("B1", 22, 34, 16, 26, 20), ("B2", 63, 79, 59, 71, 28),
        ("B3", 10, 24, 65, 76, 12), ("B4", 72, 86, 22, 34, 24),
        ("B5", 41, 51, 38, 46, 32),
    ]


@pytest.fixture(scope="module")
def cfg():
    """返回官方 q1 参数，用于所有标称几何用例。"""
    from src.q1.config import load_config

    return load_config(CONFIG_PATH)


@pytest.mark.parametrize(
    "xy, expected", [((3, 1), 1), ((3, 3), 2**0.5), ((1, 1), 0), ((0, 1), 0)],
)
def test_point_to_rectangle_distance(xy, expected):
    """核对边、角、内部及边界的平面距离（m）。"""
    from src.q1.config import Building
    from src.q1.geometry import point_rect_distance

    assert point_rect_distance(*xy, Building("box", 0, 2, 0, 2, 1)) == pytest.approx(expected)


@pytest.mark.parametrize(
    "xy, expected", [
        ((1.5, 30), True), ((98.5, 50), True), ((1.5 - 5e-10, 30), False),
        ((35.5, 20), True), ((35.5 - 5e-10, 20), False), ((26, 20), False),
        ((35, 27), False), ((35.2, 27.2), True),
    ],
)
def test_vehicle_tangent_and_intrusion(cfg, xy, expected):
    """核对圆盘贴边、微小侵入和圆角外侧可停放的区别。"""
    from src.q1.geometry import check_vehicle_geometry

    assert check_vehicle_geometry(*xy, cfg) is expected


def test_closed_elevation_limit(cfg):
    """距离下界包含端点，真实越界和目标正下方均不可观测。"""
    from src.q1.geometry import evaluate_station, gimbal_feasible_at_distance

    r0 = cfg.distance_lower_bound
    assert r0 == pytest.approx(7.582060170464, rel=0, abs=1e-12)
    assert gimbal_feasible_at_distance(r0, cfg)
    assert not gimbal_feasible_at_distance(r0 - 1e-5, cfg)
    assert gimbal_feasible_at_distance(r0 + 1e-5, cfg)
    vertical = evaluate_station(*cfg.target_xyz[:2], cfg)
    assert vertical.horizontal_distance == 0
    assert vertical.elevation_deg == 90
    assert not vertical.gimbal_ok


@pytest.mark.parametrize(
    "start, end, blocked", [
        ((-1, 0.5, 0.5), (2, 0.5, 0.5), True),
        ((-1, 0.5, 1), (2, 0.5, 1), True),
        ((-1, 0, 1), (2, 0, 1), True),
        ((-1, 1, 0.5), (1, -1, -0.5), True),
        ((-1, 0.5, 1.2), (2, 0.5, 1.2), False),
        ((-5e-10, 0.25, 0.5), (-5e-10, 0.75, 0.5), False),
        ((-1, 0.5, 0.5), (0, 0.5, 0.5), True),
        ((-2, 0.5, 0.5), (-1e-13, 0.5, 0.5), False),
        ((-4e-10, 0.75, 0.5), (4e-10, 1.75, 0.5), False),
        ((-4e-10, 0.75, 0.5), (4e-10, 0.25, 0.5), True),
        ((0, 1, 0.5), (0, 1, 0.5), True),
        ((0, 1.000001, 0.5), (0, 1.000001, 0.5), False),
    ],
)
def test_finite_segment_and_closed_faces(cfg, start, end, blocked):
    """手算的单位闭盒用例同时检查 Slab 与逐面交点结果。"""
    from src.q1.geometry import intersects_closed_box_by_faces, segment_aabb_intersects

    lo, hi = (0, 0, 0), (1, 1, 1)
    assert segment_aabb_intersects(start, end, lo, hi, cfg.numerics) is blocked
    assert intersects_closed_box_by_faces(start, end, lo, hi) is blocked
