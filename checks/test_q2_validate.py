"""B 题第二问验收工具：核对规格已有样例和人工构造的时序。
输入为固定路径与手算时间，pytest 执行；本文件不搜索参赛方案。
运行：nohup python3 -B -m pytest -q -p no:cacheprovider checks/test_q2_validate.py
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol（版本发布日期未提供），2026-11-20。
"""

from dataclasses import replace
import json
import math
from pathlib import Path

import pytest

from checks.q2_geometry import CheckError, check_route, load_scene, station_pointing
from checks.q2_validate import compare_plans, verify_q21, verify_q22
from src.q1.config import Building


def test_recomputes_t2_pair_already_in_the_spec():
    """方案 §2.3 已列出两条直达见证路线；从实际展示坐标独立复算。"""
    sample = Path(__file__).with_name("q21_reference_from_spec.json")
    report = verify_q21(load_scene(), json.loads(sample.read_text(encoding="utf-8")))
    assert report["baseline_m"] == pytest.approx(8.243154, abs=2e-6)
    assert report["angle_deg"] == pytest.approx(90, abs=1e-5)
    assert report["rmse_m"] == pytest.approx(0.143870185, abs=2e-8)
    assert [leg["travel_s"] for leg in report["routes"]] == pytest.approx([19.771721, 2.742164], abs=1e-6)


def test_station_includes_upper_pitch_limit_but_rejects_angle_above_it():
    """题面含 80°；人工构造东西向视线以隔离该边界。"""
    scene = replace(load_scene(), buildings=())
    height = scene.camera_height_m + 5 * math.tan(math.radians(80))
    assert station_pointing(scene, (10, 10), (15, 10, height))["pitch_deg"] == 80
    with pytest.raises(CheckError, match="仰角"):
        station_pointing(scene, (10, 10), (15, 10, height + 0.01))


def test_los_roof_contact_is_blocked_but_slightly_above_it_is_clear():
    """相机在 z=2 m，视线进入 x=10 m 的楼面时恰在屋顶 z=8 m。"""
    scene = replace(load_scene(), buildings=(Building("roof", 10, 14, 5, 9, 8),))
    with pytest.raises(CheckError, match="视线.*roof"):
        station_pointing(scene, (4, 7), (22, 7, 20))
    assert station_pointing(scene, (4, 7), (22, 7, 20.01))["range_m"] == 18


def test_vehicle_route_checks_interior_of_segment_and_allows_tangency():
    """端点均合法，只有线段中间可能侵入圆盘膨胀区。"""
    scene = replace(load_scene(), buildings=(Building("wall", 4, 8, 4, 8, 8),))
    result = check_route(scene, [(2, 2.5), (10, 2.5)], (2, 2.5))
    assert result["min_distance_to_buildings_m"] == {"wall": 1.5}
    with pytest.raises(CheckError, match="侵入建筑 wall"):
        check_route(scene, [(2, 2.51), (10, 2.51)], (2, 2.51))


def test_wall_clearance_applies_at_midsegment_and_when_parked():
    """车身可通过原路径，但经过楼墙中段或停在距场界 5 cm 处时余量不足。"""
    scene = replace(load_scene(), buildings=(Building("wall", 4, 8, 4, 8, 8),))
    crossing = [(2., 2.45), (10., 2.45)]
    parked = [(2., 1.55)]
    for route in (crossing, parked):
        check_route(scene, route, route[0])
        with pytest.raises(CheckError, match="净空"):
            check_route(scene, route, route[0], wall_clearance_m=0.10)
    safe = [(2., 2.39), (10., 2.39)]
    assert check_route(scene, safe, safe[0], wall_clearance_m=0.10)["length_m"] == 8.


def test_wall_clearance_rejects_corner_tangency_between_safe_endpoints():
    """3x+4y=62.5 是楼角的 1.5 m 切线，只有线段内部会贴墙。"""
    scene = replace(load_scene(), buildings=(Building("corner", 10, 16, 10, 16, 8),))
    route = ((3.5, 13), (15.5, 4))
    for point in route:
        check_route(scene, (point,), point, wall_clearance_m=0.45)
    assert check_route(scene, route, route[0])["min_distance_to_buildings_m"]["corner"] == 1.5
    with pytest.raises(CheckError, match="建筑 corner.*净空"):
        check_route(scene, route, route[0], wall_clearance_m=0.45)


@pytest.fixture
def manual_schedule():
    """无楼手算场景：向东为零转向，向北为 3 秒；不是竞赛场景。"""
    scene = replace(
        load_scene(), buildings=(),
        vehicles={"V1": (4, 10), "V2": (22, 4), "V3": (4, 30), "V4": (22, 27), "V5": (40, 4)},
        targets={"T1": (22, 10, 2), "T2": (22, 30, 2), "T3": (40, 7, 2)},
    )
    time_fields = (
        "depart_s", "arrive_s", "turn_begin_s", "turn_end_s", "wait_s",
        "observation_start_s", "observation_end_s", "reset_begin_s", "reset_end_s", "next_departure_s",
    )
    rows = [
        ("V1", "T1", None, [(4, 10), (13, 10)], [0, 3, 3, 3, 1, 4, 9, 9, 9, None]),
        ("V2", "T1", None, [(22, 4), (22, 7)], [0, 1, 1, 4, 0, 4, 9, 9, 12, 12]),
        ("V2", "T3", "T1", [(22, 7), (34, 7)], [12, 16, 16, 16, 0, 16, 21, 21, 21, None]),
        ("V3", "T2", None, [(4, 30), (7, 30)], [0, 1, 1, 1, 2, 3, 8, 8, 8, None]),
        ("V4", "T2", None, [(22, 27)], [0, 0, 0, 3, 0, 3, 8, 8, 11, None]),
        ("V5", "T3", None, [(40, 4)], [0, 0, 0, 3, 13, 16, 21, 21, 24, None]),
    ]
    routes, timeline = [], []
    for vehicle, target, previous, waypoints, times in rows:
        key = {"vehicle": vehicle, "target": target}
        routes.append({**key, "previous_target": previous, "waypoints": waypoints})
        timeline.append({**key, **dict(zip(time_fields, times))})
    return scene, routes, timeline


def test_schedule_accepts_concurrent_observations_and_checks_start_not_end(manual_schedule):
    """T2 先于 T1 启动且观测重叠；V2 完成 T1 复位后才前往 T3。"""
    report = verify_q22(*manual_schedule)
    assert report["observation_start_s"] == {"T1": 4, "T2": 3, "T3": 16}
    assert report["latest_start_s"] == 16
    assert report["last_reset_end_s"] == 24
    assert report["vehicle_task_sequences"]["V2"] == ["T1", "T3"]


def test_compare_given_plans_uses_latest_start_and_names_limiting_vehicle(manual_schedule):
    """给手算基准的复用路线加 6 m 折返，T3 在 18 秒而非 16 秒启动。"""
    scene, routes, timeline = manual_schedule
    candidate = verify_q22(scene, routes, timeline)
    next(row for row in routes if row["vehicle"] == "V2" and row["target"] == "T3")["waypoints"] = [
        (22, 7), (19, 7), (34, 7),
    ]
    v2, v5 = (next(row for row in timeline if row["target"] == "T3" and row["vehicle"] == vehicle)
              for vehicle in ("V2", "V5"))
    v2.update(arrive_s=18, turn_begin_s=18, turn_end_s=18,
              observation_start_s=18, observation_end_s=23, reset_begin_s=23, reset_end_s=23)
    v5.update(wait_s=15, observation_start_s=18, observation_end_s=23, reset_begin_s=23, reset_end_s=26)

    report = compare_plans(verify_q22(scene, routes, timeline), candidate)
    assert report["latest_start_saved_s"] == 2
    assert report["latest_start_relative_saved"] == pytest.approx(1 / 9)
    assert report["alert_start_saved_s"] == {"T1": 0, "T2": 0, "T3": 2}
    assert report["candidate"]["limiting_vehicles"]["T3"] == ["V2"]


def test_schedule_rejects_reuse_before_gimbal_has_reset(manual_schedule):
    """V2 在 T1 结束时尚未复位，提前出发不符合题面的顺序规则。"""
    scene, routes, timeline = manual_schedule
    next(row for row in timeline if row["vehicle"] == "V2" and row["target"] == "T3")["depart_s"] = 9
    with pytest.raises(CheckError, match=r"V2/T3.*depart_s"):
        verify_q22(scene, routes, timeline)


def test_schedule_flags_collision_during_travel_even_when_station_disks_do_not_overlap(manual_schedule):
    """合成场景里，V2 在 t=14 s 驶过正等待观测的 V5。"""
    scene, routes, timeline = manual_schedule
    scene = replace(scene, vehicles={**scene.vehicles, "V5": (28, 7)},
                    targets={**scene.targets, "T3": (34, 13, 2)})
    next(row for row in routes if row["vehicle"] == "V5")["waypoints"] = [(28, 7)]
    v2, v5 = (next(row for row in timeline if row["target"] == "T3" and row["vehicle"] == vehicle)
              for vehicle in ("V2", "V5"))
    v2.update(turn_end_s=19, observation_start_s=19, observation_end_s=24, reset_begin_s=24, reset_end_s=27)
    v5.update(turn_end_s=1.5, wait_s=17.5, observation_start_s=19, observation_end_s=24,
              reset_begin_s=24, reset_end_s=25.5)

    report = verify_q22(scene, routes, timeline)
    assert not report["pair_metrics"]["T3"]["station_disks_overlap"]
    check = report["inter_vehicle_separation"]
    assert check["overlapping_pairs"] == [{"vehicles": ["V2", "V5"], "distance_m": 0.0, "time_s": 14.0}]
    with pytest.raises(CheckError, match="车间圆盘重叠"):
        verify_q22(scene, routes, timeline, require_vehicle_separation=True)


def test_execution_wall_budget_requires_positive_actual_gap():
    """全程偏差达到净空可贴墙，越过上界或缺失上界均不能验收。"""
    from src.shared.wall_safety import execution_wall_budget

    budget = execution_wall_budget({"wall_clearance_m": 0.45, "execution_error_bound_m": 0.40})
    assert budget["actual_clearance_lower_bound_m"] == pytest.approx(0.05)
    for error in (0.45, 0.5, None, float("inf")):
        with pytest.raises(ValueError, match="误差"):
            execution_wall_budget({"wall_clearance_m": 0.45, "execution_error_bound_m": error})
