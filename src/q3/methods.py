"""q3 的五种求解方法，共用题定模型和临界点附近的核对工况。
输入工况与采样配置，输出概率估计、根或置信区间，供第三问运行器汇总。
并行运行 nohup python3 -B -m src.run q3 --profile full。
AI 辅助 OpenCode（Anomaly）与 Magpie gpt-6.2-sol（OpenAI，版本发布日期未公开），2026-11-20。
"""

from dataclasses import asdict, dataclass
from functools import cache
import math
import time

import numpy as np
from scipy.special import erf, ndtri, roots_hermitenorm
from scipy.stats import beta, qmc, t

from .config import Config
from .model import (
    MODES, BearingModel, Mode, angle_density_mass, gimbal_success_probability_given_alpha,
    position_bearing_error_deg, reliability_1d_adaptive, solve_sigma_critical, wrap180_deg,
)


@dataclass(frozen=True)
class Site:
    """一项共同比较工况，口径明确，里程和每轴定位标准差以 m 计。"""

    mode: Mode
    label: str
    distance_m: float | None
    sigma_m: float


def linear_reliability(sigma_m: float, model: BearingModel, mode: Mode) -> float:
    """基于一阶几何线性化，忽略远端回绕窗后的正态达标概率。"""
    # atan2 在真实位置处仅有横向导数 −1/D（rad/m）。
    std_deg = math.hypot(math.degrees(sigma_m / model.range_m), model.gimbal_sd_deg(mode))
    return math.erf(model.threshold_deg / (math.sqrt(2.0) * std_deg)) if std_deg else 1.0


def _linear_sigma_limit(cfg: Config, mode: Mode) -> float:
    """从中心正态覆盖率反解一阶模型的临界定位标准差（m）。"""
    max_sd_deg = cfg.model.threshold_deg / ndtri(0.5 * (1.0 + cfg.minimum_probability))
    pos_variance_deg2 = max_sd_deg ** 2 - cfg.model.gimbal_sd_deg(mode) ** 2
    if pos_variance_deg2 < 0.0:
        raise ValueError("一阶模型的云台误差已超过允许总方差")
    return cfg.model.range_m * math.radians(math.sqrt(pos_variance_deg2))


@cache
def _hermite_nodes(order: int) -> tuple[np.ndarray, np.ndarray]:
    """标准正态 Hermite 节点及总和为 1 的权重，供同一阶数重复求值。"""
    nodes, weights = roots_hermitenorm(order)
    return nodes, weights / math.sqrt(math.tau)


def reliability_2d_gauss_hermite(sigma_m: float, model: BearingModel, order: int,
                                 mode: Mode, *, raw_indicator: bool = False) -> float:
    """在原始定位坐标求积，实际口径为二维乘积，指令口径解析消去横向积分。"""
    if sigma_m < 0:
        raise ValueError("定位标准差须非负")
    gimbal = model.gimbal_sd_deg(mode)
    theta = model.threshold_deg
    if sigma_m == 0.0:
        return float(gimbal_success_probability_given_alpha(0.0, theta, gimbal, model.wrap_tail_tolerance))
    nodes, weights = _hermite_nodes(order)
    if not gimbal and not raw_indicator:
        # 若 X>0，指令角达标等价于 |Y|≤X tanθ。把 Y 的指示函数先按正态概率精确积分。
        x_m = model.range_m - sigma_m * nodes
        threshold = np.maximum(x_m, 0.0) * (math.tan(math.radians(theta)) / (math.sqrt(2.0) * sigma_m))
        return float(weights @ erf(threshold))

    angles_deg = position_bearing_error_deg(sigma_m * nodes[:, None], sigma_m * nodes[None, :], model.range_m)
    conditional = gimbal_success_probability_given_alpha(angles_deg, theta, gimbal, model.wrap_tail_tolerance)
    return float(np.sum(conditional * weights[:, None] * weights[None, :]))


def _wilson_interval(successes: int, n: int, alpha: float) -> tuple[float, float]:
    """计算二项比例 Wilson 区间，保留全成功和全失败时的边界。"""
    z = float(ndtri(1.0 - alpha / 2.0))
    p = successes / n
    center = (p + z * z / (2.0 * n)) / (1.0 + z * z / n)
    radius = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / (1.0 + z * z / n)
    return (0.0 if successes == 0 else max(0.0, center - radius),
            1.0 if successes == n else min(1.0, center + radius))


def _clopper_pearson(successes: int, n: int, alpha: float) -> tuple[float, float]:
    """通过 Beta 分位数取得精确二项比例区间，以支持多工况同时核对。"""
    return (float(beta.ppf(alpha / 2.0, successes, n - successes + 1)) if successes else 0.0,
            float(beta.isf(alpha / 2.0, successes + 1, n - successes)) if successes < n else 1.0)


def monte_carlo(cfg: Config, sites: tuple[Site, ...]) -> dict:
    """逐种子直接抽样四个独立误差，返回每组与汇总的概率及置信区间。"""
    settings = cfg.sampling
    model = cfg.model
    alpha = cfg.validation.confidence_alpha
    replicates = []
    counts = np.zeros((len(settings.mc_seeds), len(sites)), dtype=np.int64)
    for run, seed in enumerate(settings.mc_seeds):
        rng = np.random.default_rng(seed)
        for start in range(0, settings.mc_samples, settings.mc_batch):
            n = min(settings.mc_samples - start, settings.mc_batch)
            # 列序为定位两轴、零位、控制。同一种子在各工况共用样本，工况间验收可相关。
            dx, dy, zero, control = rng.standard_normal((n, 4)).T
            gimbal_deg = model.zero_deg * zero + model.control_deg * control
            for j, site in enumerate(sites):
                angle_deg = position_bearing_error_deg(site.sigma_m * dx, site.sigma_m * dy, model.range_m)
                if site.mode == "actual":
                    angle_deg = angle_deg + gimbal_deg
                counts[run, j] += np.count_nonzero(np.abs(wrap180_deg(angle_deg)) <= model.threshold_deg)
        for j, site in enumerate(sites):
            successes = int(counts[run, j])
            lo, hi = _wilson_interval(successes, settings.mc_samples, alpha)
            replicates.append({**asdict(site), "seed": seed, "samples": settings.mc_samples,
                               "successes": successes, "probability": successes / settings.mc_samples,
                               "wilson_low": lo, "wilson_high": hi})

    summary = []
    total = settings.mc_samples * len(settings.mc_seeds)
    for site, count in zip(sites, counts.sum(axis=0)):
        successes = int(count)
        lo, hi = _wilson_interval(successes, total, alpha)
        joint_lo, joint_hi = _clopper_pearson(successes, total, alpha / len(sites))
        summary.append({**asdict(site), "samples": total, "successes": successes, "probability": successes / total,
                        "wilson_low": lo, "wilson_high": hi, "simultaneous_low": joint_lo, "simultaneous_high": joint_hi})
    return {"sites": summary, "replicates": replicates}


def sobol(cfg: Config, sites: tuple[Site, ...]) -> dict:
    """运行嵌套 2^m 二维 Sobol 重复，返回每个 scramble 及组间收敛统计。"""
    model = cfg.model
    powers = cfg.sampling.sobol_powers
    seeds = cfg.sampling.sobol_seeds
    estimates = np.empty((len(seeds), len(sites), len(powers)))
    replicates = []
    for i, seed in enumerate(seeds):
        points = qmc.Sobol(d=2, scramble=True, seed=seed).random_base2(m=powers[-1])
        # Sobol 可能触到 0，裁剪到开区间以确保逆 CDF 有限。同一重复的低档为有效前缀。
        np.clip(points, np.nextafter(0.0, 1.0), np.nextafter(1.0, 0.0), out=points)
        ndtri(points, out=points)
        for j, site in enumerate(sites):
            angle_deg = position_bearing_error_deg(site.sigma_m * points[:, 0], site.sigma_m * points[:, 1], model.range_m)
            values = gimbal_success_probability_given_alpha(
                angle_deg, model.threshold_deg, model.gimbal_sd_deg(site.mode), model.wrap_tail_tolerance,
            )
            for k, power in enumerate(powers):
                n = 1 << power
                estimate = float(values[:n].mean())
                estimates[i, j, k] = estimate
                replicates.append({**asdict(site), "seed": seed, "power": power, "samples_per_scramble": n,
                                   "probability": estimate})

    summary = []
    multiplier = float(t.isf(cfg.validation.confidence_alpha / 2.0, df=len(seeds) - 1))
    for j, site in enumerate(sites):
        for k, power in enumerate(powers):
            ps = estimates[:, j, k]
            mean, standard_deviation = float(ps.mean()), float(ps.std(ddof=1))
            standard_error = standard_deviation / math.sqrt(len(seeds))
            radius = standard_error * multiplier
            summary.append({**asdict(site), "power": power, "samples_per_scramble": 1 << power,
                            "scrambles": len(seeds), "probability": mean, "scramble_sd": standard_deviation,
                            "standard_error": standard_error, "t_low": max(0.0, mean - radius),
                            "t_high": min(1.0, mean + radius)})
    return {"levels": summary, "replicates": replicates}


def linear_results(cfg: Config, sites: tuple[Site, ...]) -> dict:
    """计算一阶方法的两个解析根和共同比较工况的达标概率。"""
    return {"sigma_limits_m": {mode: _linear_sigma_limit(cfg, mode) for mode in MODES},
            "sites": [{**asdict(site), "probability": linear_reliability(site.sigma_m, cfg.model, site.mode)}
                      for site in sites]}


def hermite_results(cfg: Config, sites: tuple[Site, ...]) -> dict:
    """输出每档 Hermite 工况概率、相邻阶差值和两种口径的临界根。"""
    rows = []
    last_order = cfg.sampling.hermite_orders[-1]
    for site in sites:
        previous = None
        for order in cfg.sampling.hermite_orders:
            start = time.perf_counter()
            value = reliability_2d_gauss_hermite(site.sigma_m, cfg.model, order, site.mode)
            elapsed = time.perf_counter() - start
            raw = None
            if site.mode == "command" and site.label == "critical":
                # 保留跳变的诊断，择优和验收只使用同一楔形事件的条件化结果。
                raw = reliability_2d_gauss_hermite(site.sigma_m, cfg.model, order, site.mode, raw_indicator=True)
            rows.append({**asdict(site), "order": order, "probability": value,
                         "delta_from_previous": value - previous if previous is not None else None,
                         "raw_indicator_probability": raw, "probability_time_s": elapsed})
            previous = value

    limits = {}
    for mode in MODES:
        limits[mode] = solve_sigma_critical(
            lambda sigma: reliability_2d_gauss_hermite(sigma, cfg.model, last_order, mode),
            cfg.minimum_probability, cfg.sigma_at(cfg.design_distance_m), cfg.validation.root_tolerance_m,
        )
    return {"levels": rows, "sigma_limits_m": limits}


def projected_results(cfg: Config, sites: tuple[Site, ...]) -> dict:
    """按两种分段和容差复算每个工况，记录密度质量及全部积分段。"""
    v = cfg.validation
    rows, segments = [], []
    for site in sites:
        mass = angle_density_mass(site.sigma_m, cfg.model, v.quad_tolerance)
        row = {**asdict(site), "density_mass": mass.value, "density_error_estimate": mass.error}
        for name, tolerance, alternate in (("main", v.quad_tolerance, False),
                                           ("alternate", v.alternate_quad_tolerance, True)):
            result = reliability_1d_adaptive(site.sigma_m, cfg.model, site.mode, tolerance, alternate)
            row[f"probability_{name}"] = result.value
            row[f"error_{name}"] = result.error
            segments.extend({**asdict(site), "integral": name, **asdict(segment)} for segment in result.segments)
        rows.append(row)
        segments.extend({**asdict(site), "integral": "density_mass", **asdict(segment)} for segment in mass.segments)
    limits = {
        mode: solve_sigma_critical(
            lambda sigma: reliability_1d_adaptive(sigma, cfg.model, mode, v.alternate_quad_tolerance, True).value,
            cfg.minimum_probability, cfg.sigma_at(cfg.design_distance_m), v.root_tolerance_m,
        ) for mode in MODES
    }
    return {"sites": rows, "segments": segments, "alternate_sigma_limits_m": limits}
