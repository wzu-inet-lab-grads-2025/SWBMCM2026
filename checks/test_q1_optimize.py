"""核对 q1 连续主目标、圆周覆盖及净空全局误差界。
测试以官方配置和 V2.0 回归值为输入，输出为 pytest 的断言结果。
单独运行 nohup python3 -B -m pytest -p no:cacheprovider checks/test_q1_optimize.py。
"""

import math
from pathlib import Path

import pytest

from src.q1.config import load_config


CONFIG_PATH = Path(__file__).resolve().parents[1] / "configs" / "q1.yaml"


def test_optimal_circle_complete_intervals_and_scans():
    """最优圆的两个闭切点、跨零点和独立遮挡角段应吻合题设。"""
    from src.q1.geometry import check_vehicle_geometry, evaluate_station, independently_check_station
    from src.q1.optimize import circle_xy, find_feasible_angle_intervals

    cfg = load_config(CONFIG_PATH)
    circle = find_feasible_angle_intervals(cfg)
    assert circle.proof["only_relevant_building"] == "B2"
    first, second = [tuple(map(math.degrees, interval)) for interval in circle.intervals_rad]
    assert first == pytest.approx((0.0, 160.74787333), abs=1e-8, rel=0)
    assert second == pytest.approx((236.89604685, 360.0), abs=1e-8, rel=0)
    assert tuple(map(math.degrees, circle.los_blocked_rad)) == pytest.approx(
        (169.9474, 221.9272), abs=1e-4, rel=0,
    )
    assert first[1] < math.degrees(circle.los_blocked_rad[0])
    assert math.degrees(circle.los_blocked_rad[1]) < second[0]
    assert [row["step_deg"] for row in circle.scan_checks] == [0.01, 0.005]
    assert all(row["max_endpoint_difference_deg"] <= cfg.numerics.angle_check_deg for row in circle.scan_checks)
    for key in ("north_tangent_xy_m", "east_tangent_xy_m"):
        xy = circle.proof[key]
        station = evaluate_station(*xy, cfg)
        assert station.feasible
        assert independently_check_station(*xy, cfg)["gimbal_ok"]
        assert station.horizontal_distance == pytest.approx(circle.radius, abs=1e-12, rel=0)
    for endpoint, inward in zip(circle.vehicle_blocked_rad, (1e-7, -1e-7)):
        assert not check_vehicle_geometry(*circle_xy(endpoint + inward, cfg), cfg)
        assert check_vehicle_geometry(*circle_xy(endpoint - inward, cfg), cfg)


def test_los_switches_are_rechecked_even_inside_the_vehicle_exclusion():
    """独立扫描须核准车体禁入弧内的 LOS 切换和两个闭接触点。"""
    from src.q1.optimize import find_feasible_angle_intervals

    cfg = load_config(CONFIG_PATH)
    circle = find_feasible_angle_intervals(cfg)
    for scan in circle.scan_checks:
        assert scan["los_switches_deg"] == pytest.approx(
            (169.94743861, 221.92713584), abs=1e-8, rel=0,
        )
        assert scan["max_los_difference_deg"] <= cfg.numerics.angle_check_deg
    corner, face = circle.proof["los_contacts_xyz_m"]
    assert corner[:2] == (79, 71)
    assert 2 < corner[2] < 28
    assert face[0] == 79 and 59 < face[1] < 71 and face[2] == 2


def test_v2_reference_station_against_official_geometry():
    """单独核对 V2.0 提供的参考坐标与两个净空约束。"""
    from src.q1.geometry import compute_clearance, independently_check_station

    cfg = load_config(CONFIG_PATH)
    x, y = 88.8254101071, 76.3228540749
    result = independently_check_station(x, y, cfg)
    assert result["vehicle_ok"] and result["gimbal_ok"] and result["los_clear"]
    assert result["horizontal_distance_m"] == pytest.approx(cfg.distance_lower_bound, abs=1e-9, rel=0)
    clearance, terms = compute_clearance(x, y, cfg)
    assert clearance == pytest.approx(9.674589893, abs=1e-8, rel=0)
    assert terms["field_east"] == pytest.approx(terms["B2"], abs=1e-8, rel=0)


def test_optimized_station_is_primary_optimal_and_has_global_gap():
    """实际求解结果须复核主目标、车体、每栋楼的 LOS 及净空误差界。"""
    from src.q1.optimize import solve_optimum

    cfg = load_config(CONFIG_PATH)
    result = solve_optimum(cfg)
    assert result.witness.feasible
    assert result.witness.horizontal_distance == pytest.approx(7.582060170464, abs=1e-10, rel=0)
    selected = result.selected
    assert (selected.x, selected.y) == pytest.approx((88.825410107, 76.322854075), rel=0, abs=1e-4)
    assert selected.feasible
    assert selected.horizontal_distance == pytest.approx(result.circle.radius, abs=1e-9, rel=0)
    assert selected.elevation_deg == pytest.approx(80, abs=1e-7, rel=0)
    assert selected.clearance_min == pytest.approx(9.674589893, abs=1e-5, rel=0)
    assert set(result.active_clearance_constraints) == {"field_east", "B2"}
    assert selected.clearance_min <= result.clearance_upper_bound
    assert 0 <= result.clearance_gap <= cfg.numerics.clearance_gap_m
    assert result.clearance_upper_bound - selected.clearance_min <= cfg.numerics.clearance_gap_m
    assert all(result.validation["building_los_clear"].values())
    assert any(abs(math.degrees(angle) - 309.855434) < 1e-3 for angle, _ in result.local_peaks)


def test_analytic_clearance_bound_beats_the_archived_baseline():
    """等主目标时解析交点提高净空、收紧全域误差界。"""
    from src.q1.optimize import solve_optimum

    cfg = load_config(CONFIG_PATH)
    opt = solve_optimum(cfg)
    certificate = opt.analytic_clearance
    assert certificate is not None
    assert certificate["lower_semicircle_left_upper_m"] < opt.selected.clearance_min
    assert certificate["west_corner_upper_m"] < opt.selected.clearance_min
    assert opt.selected.clearance_min > 9.67458988722989
    assert opt.clearance_gap < 1e-10
    assert opt.clearance_upper_bound <= opt.branch_bound_upper_bound
    assert opt.selected.horizontal_distance == pytest.approx(7.582060170464, abs=1e-12, rel=0)
