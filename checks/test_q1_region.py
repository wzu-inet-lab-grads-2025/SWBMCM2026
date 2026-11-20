"""核对 q1 网格的单元、三个独立掩码与联合可观测域。
用官方配置和手选边界点对照标量判据，输出为 pytest 断言结果。
单独运行 python -B -m pytest -p no:cacheprovider checks/test_q1_region.py。
"""

from pathlib import Path

import numpy as np
import pytest

from src.q1.config import load_config


CONFIG_PATH = Path(__file__).resolve().parents[1] / "configs" / "q1.yaml"


def test_independent_masks_agree_with_station_checks():
    """独立 LOS 不随前序约束短路，临界点与标量可行性一致。"""
    from src.q1.geometry import evaluate_station
    from src.q1.region import station_masks

    cfg = load_config(CONFIG_PATH)
    tx, ty, _ = cfg.target_xyz
    xs = np.array([tx, tx + cfg.distance_lower_bound - 9e-10, 92.5, 15, 35.5, 35.5 - 5e-10, 1.5])
    ys = np.array([ty, ty, 70.5, 70, 20, 20, 30])
    vehicle, gimbal, los = station_masks(xs, ys, cfg)
    assert (vehicle[0], gimbal[0], los[0]) == (True, False, True)
    assert (vehicle[2], gimbal[2], los[2]) == (True, True, True)
    assert (vehicle[3], gimbal[3], los[3]) == (False, True, False)
    for index, (x, y) in enumerate(zip(xs.tolist(), ys.tolist())):
        scalar = evaluate_station(x, y, cfg)
        assert (vehicle[index], gimbal[index], los[index]) == (scalar.vehicle_ok, scalar.gimbal_ok, scalar.los_clear)


def test_region_cell_centers_and_counts():
    """1 m 网格覆盖全场中心，联合计数及面积与掩码一致。"""
    from src.q1.region import build_observable_region

    cfg = load_config(CONFIG_PATH)
    region = build_observable_region(cfg, 1.0)
    assert region.observable_mask.shape == (100, 100)
    assert region.x_centers[[0, -1]].tolist() == [0.5, 99.5]
    assert region.y_centers[[0, -1]].tolist() == [0.5, 99.5]
    np.testing.assert_array_equal(region.observable_mask, region.vehicle_mask & region.gimbal_mask & region.los_clear_mask)
    assert 0 < region.feasible_count < 10000
    assert region.estimated_area == region.feasible_count
    assert region.counts["sequential_remaining"]["after_los"] == region.feasible_count
    with pytest.raises(ValueError, match="整除"):
        build_observable_region(cfg, 0.3)


def test_continuous_area_encloses_a_known_disk_cutout():
    """空场地的可观测面积为缩边正方形减去仰角圆。"""
    from dataclasses import replace
    from math import pi

    from checks.q1_area import enclose_observable_area

    cfg = replace(load_config(CONFIG_PATH), buildings=())
    reference = (cfg.field_width - 2 * cfg.vehicle_radius) ** 2 - pi * cfg.distance_lower_bound**2
    area = enclose_observable_area(cfg, max_strip_width_m=0.001)
    assert area.lower_m2 < reference < area.upper_m2
    assert area.upper_m2 - area.lower_m2 < 0.04


def test_continuous_area_rechecks_the_official_scene():
    """真值区间须包住独立积分参考，且明显窄于原网格波动。"""
    from checks.q1_area import enclose_observable_area

    cfg = load_config(CONFIG_PATH)
    area = enclose_observable_area(cfg, max_strip_width_m=0.0002)
    assert area.lower_m2 < 5608.77061 < area.upper_m2
    assert area.upper_m2 - area.lower_m2 < 0.2
