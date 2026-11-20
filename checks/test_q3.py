"""复核 B 题第三问的误差模型与统一入口，测试数据来自题面及独立几何公式。
输入为题定参数和手算样例，无需已有运行产物。运行 nohup python3 -B -m pytest -q checks/test_q3.py。
AI 辅助 OpenCode（Anomaly）与 Magpie gpt-6.2-sol（OpenAI，版本发布日期未公开），2026-11-20。
"""

import json
import math

import numpy as np
import pytest
from scipy.stats import norm

from checks.q3_reference import half_plane_probability, verify_run
from src.q3.methods import reliability_2d_gauss_hermite
from src.q3.model import (
    BearingModel, angle_density_mass, gimbal_success_probability_given_alpha, position_bearing_error_deg,
    projected_normal_angle_pdf, reliability_1d_adaptive, wrap180_deg,
)
from src.run import main


def test_bearing_is_wrapped_into_half_open_interval():
    """±180° 在半开区间正确归并，±5° 两个达标端点保持原值。"""
    values = np.array([181.0, -181.0, 180.0, -180.0, 360.0, -5.0, 5.0])
    np.testing.assert_array_equal(wrap180_deg(values), [-179.0, 179.0, -180.0, -180.0, 0, -5, 5])


@pytest.mark.parametrize("sigma_m", [0.3, 1.6367, 1.7787, 1.9])
@pytest.mark.parametrize("mode,gimbal_deg", [("actual", 1.0), ("command", 0.0)])
def test_projected_integral_agrees_with_gaussian_half_planes(sigma_m, mode, gimbal_deg):
    """投影密度积分与几何半平面的有界复核在两种口径一致。"""
    model = BearingModel(40.0, 5.0, 0.8, 0.6)
    expected = half_plane_probability(sigma_m, 40.0, 5.0, gimbal_deg)
    result = reliability_1d_adaptive(sigma_m, model, mode)
    assert abs(result.value - expected[0]) <= 1e-11 + expected[1]


@pytest.mark.parametrize("mode,sigma_m", [("actual", 1.6367), ("command", 1.7787)])
def test_high_order_hermite_reproduces_independent_probability(mode, sigma_m):
    """原始二维求积及楔形条件求积分别对照独立高斯边际。"""
    model = BearingModel(40.0, 5.0, 0.8, 0.6)
    actual = reliability_2d_gauss_hermite(sigma_m, model, 256, mode)
    expected = half_plane_probability(sigma_m, 40.0, 5.0, model.gimbal_sd_deg(mode))
    assert abs(actual - expected[0]) < 1e-8


def test_wrap_probability_includes_nearest_period_when_it_matters():
    """将云台噪声放大后，±360° 窗确实进入条件概率而非总是被忽略。"""
    theta, angle, sigma = 5.0, 178.0, 90.0
    expected = sum(norm.cdf((theta - angle + 360.0 * k) / sigma)
                   - norm.cdf((-theta - angle + 360.0 * k) / sigma) for k in range(-7, 8))
    probabilities = gimbal_success_probability_given_alpha([angle, -angle, angle + 360.0], theta, sigma)
    np.testing.assert_allclose(probabilities, expected, rtol=0, atol=1e-15)


def test_position_bearing_uses_both_axes_and_atan2_quadrants():
    """同样的横向误差单独比较两种纵向位置，包含视线后半面。"""
    angles = position_bearing_error_deg(np.array([39.0, 41.0]), 1.0, 40.0)
    np.testing.assert_allclose(angles, [-45.0, -135.0], rtol=0, atol=1e-13)


@pytest.mark.parametrize("sigma_m", [0.0004, 1.9, 40.0, 40000.0])
def test_projected_angle_density_is_a_symmetric_probability_density(sigma_m):
    """在窄峰、临界附近、长尾和近均匀方向工况验证密度对称与归一。"""
    model = BearingModel(40.0, 5.0, 0.8, 0.6)
    assert angle_density_mass(sigma_m, model, 2e-12).value == pytest.approx(1.0, abs=5e-13)
    for alpha in (0.0, math.pi / 2, 2.5, math.pi):
        density = projected_normal_angle_pdf(alpha, sigma_m, model.range_m)
        assert density >= 0.0
        assert density == projected_normal_angle_pdf(-alpha, sigma_m, model.range_m)


def test_zero_localization_reduces_to_pure_gimbal_or_exact_command():
    """验证退化为零定位误差的两种口径及区间端点。"""
    model = BearingModel(40.0, 5.0, 0.8, 0.6)
    assert reliability_1d_adaptive(0.0, model, "command").value == 1.0
    assert reliability_1d_adaptive(0.0, model, "actual").value == pytest.approx(2.0 * norm.cdf(5) - 1, abs=1e-14)
    near_threshold = [5.0, np.nextafter(5.0, math.inf)]
    np.testing.assert_array_equal(gimbal_success_probability_given_alpha(near_threshold, 5.0, 0.0), [1.0, 0.0])


def test_q3_cli_writes_all_methods_and_independently_verifiable_limits(tmp_path):
    """走正式入口真实写盘，读回五进程证据、两种临界点、80 m 回代和论文图。"""
    assert main(["q3", "--profile", "debug", "--output-root", str(tmp_path)]) == 0
    directory, = tmp_path.iterdir()
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    assert summary["validation"]["passed"]
    assert summary["chosen_method"] == "projected_normal"
    assert len(set(summary["validation"]["pids"])) == 5
    assert summary["limits"]["actual"]["floor_to_centimeter_m"] == 66.83
    assert summary["limits"]["command"]["floor_to_centimeter_m"] == 73.93
    assert all(row["probability"] == pytest.approx(0.95, abs=1e-11) for row in summary["ab_boundary_checks"])
    for mode in ("actual", "command"):
        sites = {row["label"]: row["probability"] for row in summary["sites"] if row["mode"] == mode}
        assert sites["below_deterministic"] > 0.95 > sites["above_deterministic"]
        assert sites["operational_floor"] > 0.95 > sites["at_design"]
    assert verify_run(directory)["passed"]
    assert {path.stem for path in directory.glob("*.pdf")} == {
        "reliability", "method_validation", "ab_feasible_region", "error_contributions",
    }
