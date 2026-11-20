"""q3 水平方位误差与概率模型，输入题定距离、角度和定位误差。
供五种求解方法调用；输出角度、概率及数值积分分段记录。
单独计算通过 nohup python3 -B -m src.run q3 --profile debug。
AI 辅助 OpenCode（Anomaly）与 Magpie gpt-6.2-sol（OpenAI，版本发布日期未公开），2026-11-20。
"""

from dataclasses import dataclass, replace
from functools import cache
import math
from typing import Callable, Literal

import numpy as np
from scipy.integrate import quad
from scipy.optimize import brentq
from scipy.special import erfcx, log_ndtr, ndtr, ndtri


Mode = Literal["actual", "command"]
MODES: tuple[Mode, ...] = ("actual", "command")


@dataclass(frozen=True)
class BearingModel:
    """保存 3 问视线距离（m）、水平角阈值和两项独立云台误差标准差（度）。"""

    range_m: float
    threshold_deg: float
    zero_deg: float
    control_deg: float
    wrap_tail_tolerance: float = 1e-16

    def __post_init__(self):
        """拒绝非有限参数、非正距离及退化为非锐角的观测楔形。"""
        values = (self.range_m, self.threshold_deg, self.zero_deg, self.control_deg, self.wrap_tail_tolerance)
        if not all(map(math.isfinite, values)) or self.range_m <= 0.0 or not 0.0 < self.threshold_deg < 90.0:
            raise ValueError("需有限的正水平距离及 (0°, 90°) 内的角阈值")
        if min(self.zero_deg, self.control_deg) < 0.0 or not 0.0 < self.wrap_tail_tolerance < 1.0:
            raise ValueError("云台标准差须非负，回绕概率截断容差须在 (0, 1) 内")

    def gimbal_sd_deg(self, mode: Mode) -> float:
        """实际指向使用独立误差的合成标准差，纯软件指令使用零（度）。"""
        if mode == "actual":
            return math.hypot(self.zero_deg, self.control_deg)
        if mode == "command":
            return 0.0
        raise ValueError(f"未知角度口径 {mode!r}")


@dataclass(frozen=True)
class AngleSegment:
    """正半轴分段，端点以弧度计，积分值及误差已乘上对称因子 2。"""

    start_rad: float
    end_rad: float
    value: float
    error: float


@dataclass(frozen=True)
class Integral:
    """积分结果、积分器绝对误差估计和各段明细，点质量情形无分段。"""

    value: float
    error: float
    segments: tuple[AngleSegment, ...] = ()


def wrap180_deg(angle_deg):
    """将标量或数组的角度（度）折回至 [-180°, 180°)。"""
    angle = np.asarray(angle_deg)
    folded = np.remainder(angle + 180.0, 360.0) - 180.0
    # 保留本来就在区间内的浮点值，避免 ±5° 邻域因加减 180° 被舍入至边界。
    return np.where((-180.0 <= angle) & (angle < 180.0), angle, folded)


def position_bearing_error_deg(delta_x_m, delta_y_m, range_m: float):
    """按 3 问几何从位置误差（m）得到指令角（度），支持广播；零向量为零概率。"""
    return np.rad2deg(np.arctan2(-np.asarray(delta_y_m), range_m - np.asarray(delta_x_m)))


@cache
def wrap_window_count(threshold_deg: float, gimbal_deg: float, tail_tolerance: float) -> tuple[int, float | None]:
    """给出对称保留周期数 K 及遗漏概率上界的常用对数；零噪声的遗漏质量为零。"""
    if gimbal_deg == 0.0:
        return 0, None
    # 对主值角 |α|≤180°，所有未计入的窗口要求 |εg|≥360(K+1)-180-θ。
    z = -float(ndtri(tail_tolerance / 2.0))
    periods = max(0, math.ceil((z * gimbal_deg + threshold_deg - 180.0) / 360.0))
    limit = 360.0 * (periods + 1) - 180.0 - threshold_deg
    log10_bound = float((math.log(2.0) + log_ndtr(-limit / gimbal_deg)) / math.log(10.0))
    return periods, log10_bound


def gimbal_success_probability_given_alpha(alpha_deg, threshold_deg: float, gimbal_deg: float,
                                           tail_tolerance: float = 1e-16):
    """给定指令偏差（度），求云台误差相加并回绕后的成功概率，支持数组。"""
    magnitude = np.abs(wrap180_deg(alpha_deg))
    if gimbal_deg == 0.0:
        return (magnitude <= threshold_deg).astype(float)

    def window(center):
        """回绕平移后的中心非负，避免用两个接近 1 的 CDF 值相减。"""
        high = (threshold_deg - center) / gimbal_deg
        low = (-threshold_deg - center) / gimbal_deg
        return ndtr(high) - ndtr(low)

    periods, _ = wrap_window_count(threshold_deg, gimbal_deg, tail_tolerance)
    result = window(magnitude)
    for k in range(1, periods + 1):
        result = result + window(360.0 * k - magnitude) + window(360.0 * k + magnitude)
    return result


def projected_normal_angle_pdf(alpha_rad: float, sigma_m: float, range_m: float) -> float:
    """二维各向同性正态向量的方位密度（每弧度）；零标准差为点质量。"""
    if sigma_m <= 0.0:
        raise ValueError("连续角度密度要求定位标准差大于 0 m")
    kappa = range_m / sigma_m
    longitudinal = kappa * math.cos(alpha_rad)
    base = math.exp(-0.5 * kappa * kappa) / math.tau
    if longitudinal < 0.0:
        # 后半平面改用 erfcx 写出尾比，防止上溢乘下溢；base 已下溢时整块更小。
        if base == 0.0:
            return 0.0
        c = -longitudinal
        return base * (1.0 - c * math.sqrt(math.pi / 2.0) * float(erfcx(c / math.sqrt(2.0))))
    transverse = kappa * math.sin(alpha_rad)
    return base + longitudinal / math.sqrt(math.tau) * math.exp(-0.5 * transverse * transverse) * float(ndtr(longitudinal))


def _integrate_even(sigma_m: float, model: BearingModel, upper_rad: float, weight: Callable[[float], float],
                    tolerance: float, alternate: bool, extra_splits: tuple[float, ...] = ()) -> Integral:
    """将偶被积函数缩到正半轴，在 0 附近按 σ/D 切段以解析窄峰。"""
    scales = (0.5, 2.0, 6.0, 13.0) if alternate else (1.0, 4.0, 12.0)
    breaks = [scale * sigma_m / model.range_m for scale in scales]
    interior = {point for point in (*breaks, math.pi / 2.0, *extra_splits) if 0.0 < point < upper_rad}
    edges = (0.0, *sorted(interior), upper_rad)
    segments = []
    for start, end in zip(edges, edges[1:]):
        width = end - start

        def integrand(t):
            """按局部尺度积分，密度按弧度取值，乘雅可比和对称系数。"""
            angle = start + width * t
            pdf = projected_normal_angle_pdf(angle, sigma_m, model.range_m)
            return (2.0 * width * pdf) * weight(angle)

        outcome = quad(integrand, 0.0, 1.0, epsabs=tolerance / (len(edges) - 1),
                       epsrel=tolerance, full_output=True)
        if len(outcome) != 3:
            raise RuntimeError(f"角度积分未收敛 σ={sigma_m:g} m, [{start:g}, {end:g}] rad\n{outcome[3]}")
        value, error, _ = outcome
        segments.append(AngleSegment(start, end, value, error))
    return Integral(math.fsum(seg.value for seg in segments),
                    math.fsum(seg.error for seg in segments), tuple(segments))


def reliability_1d_adaptive(sigma_m: float, model: BearingModel, mode: Mode,
                            tolerance: float = 2e-12, alternate: bool = False) -> Integral:
    """以投影正态密度和自适应求积计算对应口径的概率，σ 为每轴定位标准差（m）。"""
    gimbal = model.gimbal_sd_deg(mode)
    theta = model.threshold_deg
    if sigma_m < 0.0:
        raise ValueError("定位标准差须非负")
    if sigma_m == 0.0:
        value = gimbal_success_probability_given_alpha(0.0, theta, gimbal, model.wrap_tail_tolerance)
        return Integral(float(value), 0.0)
    if gimbal == 0.0:
        result = _integrate_even(sigma_m, model, math.radians(theta), lambda _: 1.0, tolerance, alternate)
    else:
        def weight(angle):
            """把弧度密度的积分变量转换为供度制云台条件概率使用的角度。"""
            return float(gimbal_success_probability_given_alpha(
                math.degrees(angle), theta, gimbal, model.wrap_tail_tolerance,
            ))

        splits = tuple(math.radians(theta + k * gimbal) for k in (-3.0, 0.0, 3.0))
        result = _integrate_even(sigma_m, model, math.pi, weight, tolerance, alternate, splits)
    if not 0.0 <= result.value <= 1.0 + tolerance:
        raise RuntimeError(f"角度积分的概率越界 {result.value:g}")
    return replace(result, value=min(result.value, 1.0))


def angle_density_mass(sigma_m: float, model: BearingModel, tolerance: float) -> Integral:
    """复核整圆角度密度归一化，零标准差时为位于 0 的点质量。"""
    if sigma_m == 0.0:
        return Integral(1.0, 0.0)
    return _integrate_even(sigma_m, model, math.pi, lambda _: 1.0, tolerance, False)


def position_angle_variance_deg2(sigma_m: float, model: BearingModel, tolerance: float) -> float:
    """用对称性下的二阶矩给出几何指令偏差方差（平方度）。"""
    if sigma_m == 0.0:
        return 0.0
    return _integrate_even(sigma_m, model, math.pi, lambda angle: math.degrees(angle) ** 2,
                           tolerance, False).value


def solve_sigma_critical(probability: Callable[[float], float], minimum: float, start_m: float,
                         root_tolerance_m: float) -> float:
    """在单调下降的概率模型上扩张上括界，再用 Brent 求唯一临界标准差（m）。"""
    at_zero = probability(0.0)
    if at_zero < minimum:
        raise ValueError("单独云台误差已超过该可靠性要求，定位标准差无可行值")
    if at_zero == minimum:
        return 0.0
    upper = start_m
    for _ in range(40):
        if probability(upper) < minimum:
            return brentq(lambda sigma: probability(sigma) - minimum, 0.0, upper, xtol=root_tolerance_m)
        upper *= 2.0
    raise RuntimeError("未找到包含达标概率临界点的定位标准差括区")
