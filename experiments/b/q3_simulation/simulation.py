#!/usr/bin/env python3
"""Independent forward Monte Carlo for Problem B, Q3.

Only scene.json and the stated problem/translation are inputs. No other Q3
implementation is imported or read. All generated files stay beside this file
unless --output-dir explicitly names another allowed q3_simulation directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import time
from pathlib import Path

import numpy as np
import scipy
from scipy.integrate import quad
from scipy.optimize import brentq
from scipy.special import betaincinv, ndtr, ndtri

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DEG = np.pi / 180.0
MODES = ("total", "command_only")


def wrap(angle):
    """Radians, in [-pi, pi); never subtract raw atan2 outputs without this."""
    return (np.asarray(angle) + np.pi) % (2.0 * np.pi) - np.pi


def forward_geometry(p_true, target, delta_xy, zero_rad, control_rad):
    """Actual coordinates -> raw atan2 commands -> wrapped physical errors.

    delta_xy = estimated position - true position. Zero/control errors are
    additive physical azimuth errors, NOT extra position or command errors.
    The horizontal target range is fixed relative to p_true, not p_estimated.
    """
    p_true = np.asarray(p_true, dtype=float)
    target = np.asarray(target, dtype=float)
    p_estimated = p_true + np.asarray(delta_xy, dtype=float)
    true_ray = target - p_true
    estimated_ray = target - p_estimated
    if np.any((true_ray[..., 0] == 0) & (true_ray[..., 1] == 0)) or np.any(
            (estimated_ray[..., 0] == 0) & (estimated_ray[..., 1] == 0)):
        raise ValueError("Bearing is undefined when the observation position coincides with the target")
    psi_true = np.arctan2(true_ray[..., 1], true_ray[..., 0])
    psi_command = np.arctan2(estimated_ray[..., 1], estimated_ray[..., 0])
    command_error = wrap(psi_command - psi_true)
    total_error = wrap(psi_command + zero_rad + control_rad - psi_true)
    return command_error, total_error


def angles(z, sigma_m, cfg, phi_deg=0.0, source="all"):
    p_true = np.array([50.0, 50.0])
    phi = phi_deg * DEG
    target = p_true + cfg["range_m"] * np.array([np.cos(phi), np.sin(phi)])
    delta = sigma_m * z[:, :2] if source in ("all", "position") else np.zeros_like(z[:, :2])
    zero = cfg["zero_sd_deg"] * DEG * z[:, 2] if source in ("all", "zero", "gimbal") else 0.0
    control = cfg["control_sd_deg"] * DEG * z[:, 3] if source in ("all", "control", "gimbal") else 0.0
    return forward_geometry(p_true, target, delta, zero, control)


def wilson(k, n, alpha=0.05):
    p = k / n
    z = float(ndtri(1.0 - alpha / 2.0))
    den = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / den
    radius = z / den * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    return [max(0.0, center - radius), min(1.0, center + radius)]


def clopper_pearson(k, n, alpha=0.05):
    lo = float(betaincinv(k, n - k + 1, alpha / 2.0)) if k else 0.0
    hi = float(betaincinv(k + 1, n - k, 1.0 - alpha / 2.0)) if k < n else 1.0
    return [lo, hi]


def coverage_record(k, n):
    p = k / n
    return {"successes": int(k), "failures": int(n - k), "n": int(n),
            "coverage": p, "binomial_se": math.sqrt(p * (1.0 - p) / n),
            "wilson_95": wilson(k, n), "clopper_pearson_95": clopper_pearson(k, n)}


def count_modes(z, sigma, cfg, chunk):
    counts = {mode: 0 for mode in MODES}
    tol = cfg["tolerance_deg"] * DEG
    for start in range(0, len(z), chunk):
        cmd, total = angles(z[start:start + chunk], sigma, cfg)
        counts["command_only"] += int(np.count_nonzero(np.abs(cmd) <= tol))
        counts["total"] += int(np.count_nonzero(np.abs(total) <= tol))
    return counts


def sample_roots(z, cfg, chunk):
    intercept = cfg["position_sd_intercept_m"]
    slope = cfg["position_sd_slope_m_per_m"]
    target = cfg["coverage"]
    cache = {}

    def get(s):
        if s not in cache:
            cache[s] = count_modes(z, intercept + slope * s, cfg, chunk)
        return cache[s]

    grid = [{"s_m": s, "coverage": {m: get(s)[m] / len(z) for m in MODES}}
            for s in [0.0, 20.0, 40.0, 60.0, 80.0, 100.0, 120.0]]
    roots = {}
    for mode in MODES:
        lo, hi = 0.0, 120.0
        assert get(lo)[mode] / len(z) > target > get(hi)[mode] / len(z)
        iterations = 0
        while hi - lo > 0.00025:
            mid = (lo + hi) / 2.0
            if get(mid)[mode] / len(z) >= target:
                lo = mid
            else:
                hi = mid
            iterations += 1
        s = (lo + hi) / 2.0
        p = get(s)[mode] / len(z)
        h = 0.5
        dp_ds = (get(s + h)[mode] - get(s - h)[mode]) / (2.0 * h * len(z))
        se = math.sqrt(p * (1 - p) / len(z)) / abs(dp_ds)
        roots[mode] = {"s_m": s, "sigma_m": intercept + slope * s,
                       "bisection_bracket_m": [lo, hi], "iterations": iterations,
                       "in_sample": coverage_record(get(s)[mode], len(z)),
                       "coverage_slope_per_m": dp_ds, "slope_half_step_m": h,
                       "approx_root_se_m": se,
                       "approx_root_ci95_m": [s - ndtri(0.975) * se, s + ndtri(0.975) * se],
                       "warning": "In-sample coverage is fitted, not independent validation. Root CI is a local delta-method approximation, not an exact adaptive binomial interval."}
    return roots, grid


def independent_validation(rng, n, roots, cfg, chunk):
    # Freeze the entire design BEFORE drawing this independent sample.
    design = [("baseline_0", 0.0, MODES), ("baseline_80", cfg["design_travel_m"], MODES)]
    for mode in MODES:
        for offset in [-1.0, -0.5, 0.0, 0.5, 1.0]:
            design.append((f"{mode}_root{offset:+.1f}", roots[mode]["s_m"] + offset, (mode,)))
    counts = {(label, mode): 0 for label, _, modes in design for mode in modes}
    tol = cfg["tolerance_deg"] * DEG
    for start in range(0, n, chunk):
        z = rng.standard_normal((min(chunk, n - start), 4))
        for label, s, modes in design:
            sigma = cfg["position_sd_intercept_m"] + cfg["position_sd_slope_m_per_m"] * s
            cmd, total = angles(z, sigma, cfg)
            errors = {"total": total, "command_only": cmd}
            for mode in modes:
                counts[label, mode] += int(np.count_nonzero(np.abs(errors[mode]) <= tol))
    rows = []
    family_size = len(counts)
    for label, s, modes in design:
        for mode in modes:
            row = {"label": label, "mode": mode, "s_m": s,
                   **coverage_record(counts[label, mode], n)}
            interval = clopper_pearson(counts[label, mode], n, alpha=0.05 / family_size)
            row["familywise95_bonferroni_clopper_pearson"] = interval
            row["familywise_decision"] = ("meets" if interval[0] >= cfg["coverage"] else
                                          "fails" if interval[1] < cfg["coverage"] else "unresolved")
            rows.append(row)
    certified_brackets = {}
    for mode in MODES:
        same = [row for row in rows if row["mode"] == mode]
        meets = [row["s_m"] for row in same if row["familywise_decision"] == "meets"]
        fails = [row["s_m"] for row in same if row["familywise_decision"] == "fails"]
        certified_brackets[mode] = [max(meets) if meets else None, min(fails) if fails else None]
    return {"rows": rows, "family_size": family_size,
            "simultaneous95_root_brackets_m": certified_brackets,
            "root_bracket_note": "Both root brackets have simultaneous at-least-95% coverage under the model monotonicity: select the largest certified passing distance and smallest certified failing distance from the prespecified validation design. This is not a refitted point estimate.",
            "interval_note": "Wilson and ordinary Clopper-Pearson are pointwise 95%. Bonferroni Clopper-Pearson intervals jointly cover the fixed validation family with at least 95% confidence, conditional on the independent fit sample. Validation points share random numbers, so their estimates are correlated. No validation-based refitting is performed."}


def summary(errors_rad, cfg):
    e = errors_rad / DEG
    n = len(e)
    k = int(np.count_nonzero(np.abs(errors_rad) <= cfg["tolerance_deg"] * DEG))
    mean = float(np.mean(e))
    variance = float(np.var(e, ddof=1))
    fourth = float(np.mean((e - mean) ** 4))
    variance_se = math.sqrt(max(0., (fourth - (n - 3) / (n - 1) * variance ** 2) / n))
    return {"mean_deg": mean, "mean_se_deg": math.sqrt(variance / n),
            "variance_deg2": variance, "variance_se_deg2": variance_se,
            "variance_approx95_deg2": [max(0., variance - ndtri(0.975) * variance_se),
                                      variance + ndtri(0.975) * variance_se],
            "sd_deg": math.sqrt(variance),
            "q95_absolute_deg": float(np.quantile(np.abs(e), 0.95)),
            **coverage_record(k, n)}


def deterministic_checks():
    p = np.array([12.0, -7.0])
    t = p + [40.0, 0.0]
    cases = []

    def check(name, got, expected, atol=2e-12):
        difference = float(np.max(np.abs(np.asarray(got) - np.asarray(expected))))
        assert difference < atol, (name, difference)
        cases.append({"name": name, "max_abs_error": difference, "tolerance": atol})

    check("wrap_degrees_half_open", wrap(np.array([-540, -180, 180, 540, 358, -358]) * DEG) / DEG,
          [-180, -180, -180, -180, -2, 2])
    cmd, total = forward_geometry(p, t, [[0, 1], [1, 0], [0, 40 * np.tan(5 * DEG)]], 0, 0)
    check("estimated_minus_true_sign_and_meters", cmd / DEG,
          [-np.arctan2(1, 40) / DEG, 0, -5])
    check("no_gimbal_equals_command", total, cmd)
    for true_deg, command_deg, expected in [(179, -179, 2), (-179, 179, -2)]:
        target = p + 40 * np.array([np.cos(true_deg * DEG), np.sin(true_deg * DEG)])
        p_est = target - 40 * np.array([np.cos(command_deg * DEG), np.sin(command_deg * DEG)])
        cmd, total = forward_geometry(p, target, p_est - p, 0.8 * DEG, 0.6 * DEG)
        check(f"atan2_cut_command_{true_deg}", cmd / DEG, expected)
        check(f"atan2_cut_total_{true_deg}", total / DEG, expected + 1.4)
    cmd, total = forward_geometry(p, t, [0, 0], 0.8 * DEG, 0.6 * DEG)
    check("degree_to_radian_noise_conversion", total / DEG, 1.4)
    check("zero_position_command", cmd, 0.0)
    cmd, total = forward_geometry(p, t, [80, 0], 0, 0)
    check("opposite_ray_maps_plus_pi_to_minus_pi", cmd / DEG, -180.0)
    check("opposite_ray_total", total / DEG, -180.0)
    return cases


def stochastic_checks(rng, n, cfg):
    z = rng.standard_normal((n, 4))
    corr = np.corrcoef(z, rowvar=False)
    square_corr = np.corrcoef(z * z, rowvar=False)
    means = np.mean(z, axis=0)
    variances = np.var(z, axis=0, ddof=1)
    offdiag = ~np.eye(4, dtype=bool)
    independent = {"variables": ["position_x", "position_y", "gimbal_zero", "gimbal_control"],
                   "n": n, "standardized_means": means.tolist(), "standardized_variances": variances.tolist(),
                   "correlation_matrix": corr.tolist(), "squared_correlation_matrix": square_corr.tolist(),
                   "max_abs_cross_correlation": float(np.max(np.abs(corr[offdiag]))),
                   "mean_check_bound": 6 / math.sqrt(n), "variance_check_bound": 6 * math.sqrt(2 / (n - 1)),
                   "correlation_check_bound": 6 / math.sqrt(n),
                   "note": "Independent standard-normal columns by construction. Finite-sample correlation checks (including squares) diagnose mistakes; they cannot prove independence."}
    assert np.max(np.abs(means)) < independent["mean_check_bound"]
    assert np.max(np.abs(variances - 1)) < independent["variance_check_bound"]
    assert independent["max_abs_cross_correlation"] < independent["correlation_check_bound"]
    assert np.max(np.abs(square_corr[offdiag])) < independent["correlation_check_bound"]

    sigma = cfg["position_sd_intercept_m"] + cfg["position_sd_slope_m_per_m"] * cfg["design_travel_m"]
    p = np.array([12., -7.])
    target = p + [cfg["range_m"], 0.]
    delta = sigma * z[:, :2]
    zero = cfg["zero_sd_deg"] * DEG * z[:, 2]
    control = cfg["control_sd_deg"] * DEG * z[:, 3]
    ref = forward_geometry(p, target, delta, zero, control)
    rotations = []
    direction_mc = []
    for phi_deg in [0., 30., 90., 179.9, -179.9, 285.]:
        phi = phi_deg * DEG
        rotation = np.array([[np.cos(phi), -np.sin(phi)], [np.sin(phi), np.cos(phi)]])
        transformed = forward_geometry(rotation @ p, rotation @ target, delta @ rotation.T, zero, control)
        errors = [float(np.max(np.abs(wrap(a - b)))) for a, b in zip(transformed, ref)]
        assert max(errors) < 2e-13
        rotations.append({"rotation_deg": phi_deg, "command_max_abs_diff_rad": errors[0],
                          "total_max_abs_diff_rad": errors[1]})
        # Do not rotate draws here: independently check isotropy in fixed Cartesian axes.
        command, all_errors = angles(z, sigma, cfg, phi_deg)
        direction_mc.append({"bearing_deg": phi_deg, "command_only": summary(command, cfg),
                             "total": summary(all_errors, cfg)})
    translated = forward_geometry(p + [300., -500.], target + [300., -500.], delta, zero, control)
    translation_error = max(float(np.max(np.abs(wrap(a - b)))) for a, b in zip(translated, ref))
    assert translation_error < 2e-13
    estimated_ray = target - (p + delta)
    raw_deg = np.arctan2(estimated_ray[:, 1], estimated_ray[:, 0]) / DEG
    degree_result = (raw_deg + zero / DEG + control / DEG + 180.) % 360. - 180.
    unit_error_deg = float(np.max(np.abs(degree_result - ref[1] / DEG)))
    assert unit_error_deg < 2e-11

    source_rows = []
    contribution_rows = []
    for s in [0.0, cfg["design_travel_m"]]:
        sigma_s = cfg["position_sd_intercept_m"] + cfg["position_sd_slope_m_per_m"] * s
        position = angles(z, sigma_s, cfg, source="position")[1]
        all_errors = angles(z, sigma_s, cfg)[1]
        variables = np.column_stack([position / DEG, zero / DEG, control / DEG])
        covariance = np.cov(variables, rowvar=False, ddof=1)
        linear_variances = np.array([(sigma_s / cfg["range_m"] / DEG) ** 2,
                                     cfg["zero_sd_deg"] ** 2, cfg["control_sd_deg"] ** 2])
        unwrapped = position + zero + control
        wrap_count = int(np.count_nonzero(np.abs(unwrapped) >= np.pi))
        contribution_rows.append({"s_m": s, "sigma_m": sigma_s,
                                  "labels": ["position", "gimbal_zero", "gimbal_control"],
                                  "linear_variance_deg2": linear_variances.tolist(),
                                  "linear_fraction": (linear_variances / np.sum(linear_variances)).tolist(),
                                  "empirical_covariance_deg2": covariance.tolist(),
                                  "empirical_variance_deg2": np.diag(covariance).tolist(),
                                  "empirical_fraction_of_sum": (np.diag(covariance) / np.trace(covariance)).tolist(),
                                  "empirical_total_variance_deg2": float(np.var(all_errors / DEG, ddof=1)),
                                  "covariance_sum_deg2": float(np.sum(covariance)),
                                  "extra_wrap_count": wrap_count})
        assert wrap_count == 0
        assert abs(np.sum(covariance) - np.var(all_errors / DEG, ddof=1)) < 1e-10
        for source in ["none", "position", "zero", "control", "gimbal", "all"]:
            source_rows.append({"s_m": s, "source": source,
                                **summary(angles(z, sigma_s, cfg, source=source)[1], cfg)})
    return {"deterministic": deterministic_checks(), "independence": independent,
            "rotation_samplewise": rotations, "bearing_distribution": direction_mc,
            "translation_max_abs_diff_rad": translation_error,
            "independent_degree_path_max_abs_diff_deg": unit_error_deg,
            "single_source": source_rows, "variance_contributions": contribution_rows}


def quadrature_probability(sigma, cfg, mode, eps=1e-11):
    """Secondary, independent polar-density integration; NOT used by MC fit.

    For X~N(R,sigma^2), Y~N(0,sigma^2), integrate their raw atan2 density
    over the front half-plane. Omitted back-half-plane mass <= Phi(-R/sigma).
    Integrate the actual Gaussian zero/control convolution for total error.
    All roots are in sigma/R<0.05; omitted mass is <2e-98 at s=80.
    """
    alpha = cfg["tolerance_deg"] * DEG
    g = math.hypot(cfg["zero_sd_deg"], cfg["control_sd_deg"]) * DEG
    if sigma == 0:
        probability = 1.
        if mode == "total" and g:
            # At zero position error only the wrapped gimbal error remains.
            central = math.erf(alpha / (math.sqrt(2.) * g))
            first_image = ndtr((alpha - 2 * math.pi) / g) - ndtr((-alpha - 2 * math.pi) / g)
            probability = central + 2 * first_image
        return {"coverage": float(probability), "quad_abs_error_estimate": 0.,
                "omitted_back_half_plane_mass_bound": 0.,
                "omitted_gimbal_wrap_tail_note": "Zero position error; gimbal images outside -1,0,1 are negligible at 1 degree SD."}
    kappa = cfg["range_m"] / sigma

    def integrand(theta):
        density = (math.exp(-0.5 * kappa * kappa) / (2 * math.pi)
                   + kappa * math.cos(theta) / math.sqrt(2 * math.pi)
                   * math.exp(-0.5 * (kappa * math.sin(theta)) ** 2)
                   * ndtr(kappa * math.cos(theta)))
        if mode == "command_only":
            weight = 1.0
        else:
            weight = sum(ndtr((alpha - theta + 2 * math.pi * j) / g)
                         - ndtr((-alpha - theta + 2 * math.pi * j) / g)
                         for j in [-1, 0, 1])
        return 2 * density * weight

    upper = alpha if mode == "command_only" else math.pi / 2
    points = sorted(set(v for v in [alpha, 1 / kappa, 2 / kappa, 4 / kappa, 8 / kappa]
                        if 0 < v < upper))
    value, error = quad(integrand, 0, upper, points=points, epsabs=eps, epsrel=eps, limit=200)
    return {"coverage": min(1., max(0., float(value))), "quad_abs_error_estimate": float(error),
            "omitted_back_half_plane_mass_bound": float(ndtr(-kappa)),
            "omitted_gimbal_wrap_tail_note": "Only wrap shifts -1,0,1 retained; all further terms require >450 degree Gaussian errors (sd=1 degree), negligible far below double precision."}


def benchmarks(cfg, fit, validation):
    z95 = float(ndtri((1 + cfg["coverage"]) / 2))
    r = cfg["range_m"]
    alpha = cfg["tolerance_deg"]
    intercept = cfg["position_sd_intercept_m"]
    slope = cfg["position_sd_slope_m_per_m"]
    out = {}
    for mode in MODES:
        gvar = cfg["zero_sd_deg"] ** 2 + cfg["control_sd_deg"] ** 2 if mode == "total" else 0.
        linear_sigma = r * DEG * math.sqrt((alpha / z95) ** 2 - gvar)
        sigma_root = brentq(lambda sigma: quadrature_probability(sigma, cfg, mode)["coverage"] - cfg["coverage"],
                            0.5, 3.0, xtol=2e-12)
        s_root = (sigma_root - intercept) / slope
        tight = quadrature_probability(sigma_root, cfg, mode, eps=1e-13)
        h = 1e-3
        quadrature_slope = (quadrature_probability(sigma_root + h * slope, cfg, mode)["coverage"]
                            - quadrature_probability(sigma_root - h * slope, cfg, mode)["coverage"]) / (2 * h)
        out[mode] = {"linear_sigma_limit_m": linear_sigma,
                     "linear_s_limit_m": (linear_sigma - intercept) / slope,
                     "quadrature_coverage_slope_per_m": quadrature_slope,
                     "quadrature_local_root_error_estimate_m": tight["quad_abs_error_estimate"] / abs(quadrature_slope),
                     "polar_quadrature_sigma_limit_m": sigma_root,
                     "polar_quadrature_s_limit_m": s_root,
                     "coverage_at_quadrature_root_tight_tolerance": tight,
                     "linear_minus_quadrature_s_m": (linear_sigma - sigma_root) / slope,
                     "mc_fit_minus_quadrature_s_m": fit[mode]["s_m"] - s_root,
                     "mc_fit_difference_in_estimated_se": (fit[mode]["s_m"] - s_root) / fit[mode]["approx_root_se_m"],
                     "s_zero_command_failure_wedge_upper_bound": (float(2 * ndtr(-r * math.sin(alpha * DEG) / intercept))
                                                                 if mode == "command_only" else None),
                     "a_b_mc_uncertainty": {"sigma_limit_estimate_m": fit[mode]["sigma_m"],
                                             "sigma_limit_se_m": slope * fit[mode]["approx_root_se_m"],
                                             "sigma_limit_approx95_m": [intercept + slope * s for s in fit[mode]["approx_root_ci95_m"]],
                                             "b_limit_se_at_fixed_a": slope * fit[mode]["approx_root_se_m"] / cfg["design_travel_m"]},
                     "a_b_at_80": {"a_min_m": 0., "b_min_m_per_m": 0.,
                                    "constraint": "a + 80*b <= sigma_limit_m",
                                    "sigma_limit_m": sigma_root,
                                    "b_max_if_a_0_3": (sigma_root - 0.3) / 80,
                                    "a_max_if_b_0_02": sigma_root - 80 * 0.02,
                                    "max_a_if_b_zero": sigma_root,
                                    "max_b_if_a_zero": sigma_root / 80}}
        for row in validation["rows"]:
            if row["mode"] == mode:
                sigma = intercept + slope * row["s_m"]
                benchmark = quadrature_probability(sigma, cfg, mode)
                row["quadrature_check"] = benchmark
                row["mc_minus_quadrature"] = row["coverage"] - benchmark["coverage"]
                p = benchmark["coverage"]
                se = math.sqrt(p * (1 - p) / row["n"])
                row["mc_difference_in_binomial_se"] = row["mc_minus_quadrature"] / se if se > 0 else None
    return out


def make_report(result):
    cfg, fit = result["config"], result["fit"]
    val = result["validation"]["rows"]
    bm = result["benchmarks"]
    labels = {"total": "总指向偏差（定位＋零位＋控制）", "command_only": "仅指令偏差（定位）"}
    lines = ["# B 题第三问：独立正向 Monte Carlo 验证", "",
             "本目录仅依据英文题面、中文译文和 `experiments/b/scene.json`，未读取其他 Q3 代理稿或导入其实现。结果由 `simulation.py` 自动生成。", "",
             "## 1. 口径与几何实现", "",
             "英文第4页定义 Δψ 为 commanded bearing 与 true bearing 的差，同时给出零位和控制噪声；译文第110–122行保留此歧义。因此分别报告：", "",
             "- 仅指令：`wrap(atan2(Ty−P̂y, Tx−P̂x) − atan2(Ty−Py, Tx−Px))`。",
             "- 总指向：`wrap(atan2(Ty−P̂y, Tx−P̂x) + ε0 + εc − atan2(Ty−Py, Tx−Px))`。",
             "- `P̂=P+(δx,δy)`，真实水平距离始终为40 m；两项云台误差分别生成后相加。所有三角运算/回绕采用弧度，输入及报告的角度用度。回绕范围为 `[-π,π)`。",
             "- 坐标误差两分量各自的标准差是 `0.3+0.02s` m，而非二维径向标准差；不另除以或乘以 √2。目标已知，不引入 Q2 的1°测量噪声。高度不进入本题水平方位 `atan2`。",
             "- 高斯位置误差不按场地或障碍截断；这是定位估计误差，不是车辆真实位置随机离开可行区域。", "",
             "## 2. 抽样、估根与置信区间", "",
             f"- 主种子 `{result['random']['seed_entropy']}`，NumPy `PCG64`；SeedSequence子流 `(0,)` 估根、`(1,)` 独立验证、`(2,)` 检查。",
             f"- 估根 {result['sample_sizes']['fit']:,} 样本；独立验证 {result['sample_sizes']['validation']:,} 样本；检查 {result['sample_sizes']['checks']:,} 样本。各样本含4个独立标准正态数。",
             "- 估根用共同随机数和原始几何计数，在 `[0,120]` m 二分，括区宽不超过0.00025 m。这只是数值定位精度，绝不是统计误差。",
             "- 根附近覆盖率对里程的导数由±0.5 m共同随机数差分计算；根标准误为 `sqrt(p(1−p)/N)/|dp/ds|`，根区间是局部渐近95%区间，不作精确有限样本保证。",
             "- 同一批样本算出的根，其覆盖率接近0.95是拟合结果，不能再当作验证。所有验证点在读取独立验证样本前冻结；不根据验证结果调根。",
             "- 验证各点共享验证随机数以降低比较噪声，但与估根子流独立。普通Wilson区间为点态95%；机器结果另给普通精确Clopper–Pearson区间及全部14个点的Bonferroni精确同时95%区间（点间相关不破坏该保证）。", "",
             "## 3. 独立估计的临界里程", "",
             "|口径|MC根 / m|MC标准误 / m|近似95%根区间 / m|同样本覆盖率|",
             "|---|---:|---:|---|---:|"]
    for mode in MODES:
        f = fit[mode]
        lo, hi = f["approx_root_ci95_m"]
        lines.append(f"|{labels[mode]}|{f['s_m']:.6f}|{f['approx_root_se_m']:.5f}|[{lo:.5f}, {hi:.5f}]|{f['in_sample']['coverage']:.7f}|")
    lines += ["", "### 独立样本：端点与根附近", "",
              "|口径|s / m|覆盖率|Wilson 95%|同时95%判断|",
              "|---|---:|---:|---|---|"]
    decisions = {"meets": "满足", "fails": "不满足", "unresolved": "无法区分阈值"}
    for row in val:
        lo, hi = row["wilson_95"]
        lines.append(f"|{labels[row['mode']]}|{row['s_m']:.5f}|{row['coverage']:.7f}|[{lo:.7f}, {hi:.7f}]|{decisions[row['familywise_decision']]}|")
    brackets = result["validation"]["simultaneous95_root_brackets_m"]
    root_diagnostics = []
    for mode in MODES:
        row = next(row for row in val if row["label"] == f"{mode}_root+0.0")
        lo, hi = row["wilson_95"]
        relation = "包含" if lo <= cfg["coverage"] <= hi else "不包含"
        root_diagnostics.append(f"{labels[mode]}根处点态Wilson区间{relation}0.95，对积分真值的偏差为{row['mc_difference_in_binomial_se']:+.2f}个标准误。")
    lines += ["", "根处的独立覆盖区间通常包含0.95，但点态95%区间可能偶然排除阈值；不能把随机略高于0.95解释为确定满足，也不能把略低于0.95解释为模型失败。" + "".join(root_diagnostics) + "保留原始种子结果，不更换种子挑选漂亮区间。±0.5 m及±1 m点提供阈值两侧验证。零失败也不等于真实覆盖率为1，须看区间下端。",
              f"独立验证的同时95%根括区为：总指向 [{brackets['total'][0]:.5f}, {brackets['total'][1]:.5f}] m；仅指令 [{brackets['command_only'][0]:.5f}, {brackets['command_only'][1]:.5f}] m。它们由同一组同时区间及总体单调性导出；比局部渐近MC根区间更宽，但不依赖根方差近似。", "",
              "## 4. 自行推导的数值积分与线性近似核对", "",
              "以下积分只在MC根与独立验证设计冻结后运行，不作为MC角误差生成器或估根输入。对 `X=40−δx, Y=−δy` 的二维高斯密度作极坐标径向积分，得到角密度：", "",
              "`f(θ)=exp(−κ²/2)/(2π) + κ cosθ exp(−κ² sin²θ/2) Φ(κ cosθ)/sqrt(2π)`，`κ=40/σ`。",
              "积分 `f(θ)` 乘云台高斯和落在回绕±5°范围内的条件概率。只积前半平面，漏掉的概率严格不超过 `Φ(−40/σ)`；在s=80时为约1.082×10⁻⁹⁸。数值误差估计、收紧容差复核均存JSON。仅指令口径也可用 `2Φ(40 sin5°/σ)−1` 核对，其与精确扇形概率的差由极小的后半平面事件控制。", "",
              "一阶模型为 `δψ≈(sinφ δx−cosφ δy)/40 + ε0+εc`（弧度）；因此总方差以度²计是 `(σ·180/(40π))²+0.8²+0.6²`。仅指令不含后两项。", "",
              "|口径|一阶里程上限 / m|非线性积分上限 / m|一阶偏高 / m|MC与积分差 / MC标准误|",
              "|---|---:|---:|---:|---:|"]
    for mode in MODES:
        b = bm[mode]
        lines.append(f"|{labels[mode]}|{b['linear_s_limit_m']:.6f}|{b['polar_quadrature_s_limit_m']:.6f}|{b['linear_minus_quadrature_s_m']:.6f}|{b['mc_fit_difference_in_estimated_se']:.3f}|")
    lines += ["", "阈值附近σ/40约0.04–0.045，一阶近似相当准确但不精确；MC统计误差与约0.1 m的非线性修正不能混为一谈。非线性角误差不是严格高斯，不能将正态方差相加当作精确分布。上述积分是该小误差范围内高精度数值校核，而非整个参数域的无条件闭式解。",
              "", "## 5. s=80的非负a、b约束", "",
              "在非负a、b下，要求 `a≥0, b≥0, a+80b≤σ*`。a的单位为m，b为m/m；若要求整个0–80 m区间满足，非负b使80 m成为最不利点。", "",
              "|口径|σ* / m|a=0.3时b上限 / (m/m)|b=0.02时a上限 / m|",
              "|---|---:|---:|---:|"]
    for mode in MODES:
        ab = bm[mode]["a_b_at_80"]
        lines.append(f"|{labels[mode]}|{ab['sigma_limit_m']:.9f}|{ab['b_max_if_a_0_3']:.9f}|{ab['a_max_if_b_0_02']:.9f}|")
    lines += ["", "这里σ*取本目录独立积分值；若仅使用MC，应把估根不确定度乘0.02传递至σ*，再除80传递至b。不能把MC根小数位当作工程保证。原模型在80 m时σ=1.9 m，两种口径均不满足95%要求。", "",
              "## 6. 单误差源与方差贡献", "",
              "|s / m|源|样本方差 / 度²|绝对误差95%分位 / 度|覆盖率|",
              "|---:|---|---:|---:|---:|"]
    for row in result["checks"]["single_source"]:
        lines.append(f"|{row['s_m']:.0f}|{row['source']}|{row['variance_deg2']:.6f}|{row['q95_absolute_deg']:.6f}|{row['coverage']:.7f}|")
    lines += ["", f"本节仅用独立检查子流的{result['sample_sizes']['checks']:,}样本，不是上节{result['sample_sizes']['validation']:,}独立验证计数；样本方差、覆盖率会有所不同。机器结果附覆盖率区间、均值标准误，以及用样本四阶中心矩估计的方差标准误和近似95%区间。95%绝对分位数仅作诊断，不用来估根。",
              "", "|s / m|一阶定位占比|一阶零位占比|一阶控制占比|非线性样本定位占比|",
              "|---:|---:|---:|---:|---:|"]
    for row in result["checks"]["variance_contributions"]:
        v = row["linear_fraction"]
        lines.append(f"|{row['s_m']:.0f}|{v[0]:.3%}|{v[1]:.3%}|{v[2]:.3%}|{row['empirical_fraction_of_sum'][0]:.3%}|")
    independence = result["checks"]["independence"]
    max_rotation = max(max(r["command_max_abs_diff_rad"], r["total_max_abs_diff_rad"])
                       for r in result["checks"]["rotation_samplewise"])
    lines += ["", "三类贡献是定位（二维合为一类）、零位、控制，不能把两坐标各记一次完整角方差。样本方差和与总方差有有限样本协方差差异；JSON给出完整协方差矩阵，含协方差后重构总方差一致，检查样本中没有总角再次跨±π的事件。80 m时定位漂移主导，优先减少累积定位漂移或中途重定位；短程时云台零位标定与控制误差更值得优先改进。", "",
              "## 7. 实现检查与局限", "",
              f"- 原始标准正态4列最大交叉相关绝对值：{independence['max_abs_cross_correlation']:.6f}；均值、方差、平方相关也通过6标准误级的诊断限。独立性来自生成构造，相关检查本身不能证明独立。",
              f"- 0°、30°、90°、179.9°、−179.9°、285°联合旋转真实坐标、目标和同一样本误差，逐样本最大差{max_rotation:.3g} rad。固定全局坐标抽样但改变真方位的分布检查也在JSON中。",
              "- 确认179°→−179°差为+2°而不是−358°；反向为−2°；±180°统一映为−180°；真实零误差、纯沿线位置误差、横向误差符号和5°几何边界通过确定性测试。",
              f"- 整体平移最大角差{result['checks']['translation_max_abs_diff_rad']:.3g} rad；另一条度制计算路径与弧度路径最大差{result['checks']['independent_degree_path_max_abs_diff_deg']:.3g}°。",
              "- 所报95%是单次停车时的边际覆盖率；题面未定义沿途误差的时间相关，不能推断整段路径所有时刻联合成功率，也不能将多任务误差视为新抽样而不说明。",
              "- 高斯零位偏差在本题边际模型中抽样；真实设备长期固定偏置、未知相关误差、目标位置不确定等不在题面假设内。",
              "- 共同随机数计数函数是阶梯函数。总体覆盖率随σ下降：写标准化位置误差为(u,v)，视线为(40−σu,−σv)，有 `dθ/dσ=−40v/[(40−σu)²+(σv)²]`，故绝对位置角随σ增大（退化零概率事件除外）。独立对称单峰的回绕高斯云台噪声落入±5°窗口的条件概率随该绝对角下降，所以总体仍递减；σ趋无穷时方位趋均匀，覆盖率趋10/360。单条含云台的样本可能因抵消不单调，故不把每条样本事件嵌套视作前提。", "",
              "## 8. 复跑与产物", "",
              "```bash", "python3 experiments/b/q3_simulation/test_simulation.py",
              "python3 experiments/b/q3_simulation/simulation.py", "```", "",
              f"环境：Python {result['environment']['python']}，NumPy {result['environment']['numpy']}，SciPy {result['environment']['scipy']}。本次总耗时约{result['environment']['elapsed_seconds']:.1f} s（受机器/BLAS线程影响）。",
              "`results.json`含种子、输入哈希、全部计数与区间、估根括区、独立检查和积分误差；`report.md`为本报告。`README.md`记录参数、文件说明及最小依赖。没有提交，也没有写入本目录以外的项目文件。", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fit-n", type=int, default=4_000_000)
    parser.add_argument("--validation-n", type=int, default=4_000_000)
    parser.add_argument("--check-n", type=int, default=500_000)
    parser.add_argument("--seed", type=int, default=2026112003)
    parser.add_argument("--chunk", type=int, default=250_000)
    parser.add_argument("--output-dir", type=Path, default=HERE)
    args = parser.parse_args()
    if min(args.fit_n, args.validation_n, args.check_n) < 2 or args.chunk <= 0:
        parser.error("sample sizes must be at least 2 and chunk must be positive")
    output = args.output_dir.resolve()
    allowed = [HERE, ROOT / "runs" / "b" / "q3_simulation"]
    if not any(output == p or p in output.parents for p in allowed):
        parser.error("output must remain inside experiments/b/q3_simulation or runs/b/q3_simulation")
    output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    scene_file = ROOT / "experiments" / "b" / "scene.json"
    scene = json.loads(scene_file.read_text())
    cfg = scene["q3"]
    pdf = ROOT / scene["source"]
    translation = ROOT / "problem" / "translations" / "B题-协同观测与调度.md"
    hash_file = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    assert hash_file(pdf) == scene["sha256"], "English problem PDF differs from scene provenance"
    seeds = np.random.SeedSequence(args.seed).spawn(3)
    rngs = [np.random.Generator(np.random.PCG64(seed)) for seed in seeds]
    print(f"Fit: {args.fit_n:,} forward samples", flush=True)
    z = rngs[0].standard_normal((args.fit_n, 4))
    fit, grid = sample_roots(z, cfg, args.chunk)
    del z
    print(f"Independent validation: {args.validation_n:,} samples, frozen design", flush=True)
    validation = independent_validation(rngs[1], args.validation_n, fit, cfg, args.chunk)
    print(f"Geometry/source/independence checks: {args.check_n:,} samples", flush=True)
    checks = stochastic_checks(rngs[2], args.check_n, cfg)
    print("Independent quadrature cross-check (not used for MC fit)", flush=True)
    benchmark = benchmarks(cfg, fit, validation)
    result = {"schema_version": 1, "config": cfg,
              "sources": {"scene": str(scene_file.relative_to(ROOT)), "scene_sha256": hash_file(scene_file),
                          "english_pdf": str(pdf.relative_to(ROOT)), "english_pdf_sha256": hash_file(pdf),
                          "translation": str(translation.relative_to(ROOT)), "translation_sha256": hash_file(translation),
                          "english_pages_read": [1, 2, 3, 4, 5],
                          "other_q3_work_read": False},
              "random": {"seed_entropy": args.seed, "bit_generator": "PCG64",
                         "stream_spawn_keys": {name: list(seed.spawn_key) for name, seed in zip(["fit", "validation", "checks"], seeds)},
                         "generation_order": "Row-major independent N(0,1) columns: dx,dy,zero,control"},
              "sample_sizes": {"fit": args.fit_n, "validation": args.validation_n, "checks": args.check_n, "chunk": args.chunk},
              "fit": fit, "fit_coarse_grid": grid, "validation": validation, "checks": checks,
              "benchmarks": benchmark,
              "environment": {"python": platform.python_version(), "numpy": np.__version__, "scipy": scipy.__version__,
                              "elapsed_seconds": time.perf_counter() - started}}
    (output / "results.json").write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    (output / "report.md").write_text(make_report(result))
    print(json.dumps({"output": str(output), "mc_roots_m": {m: fit[m]["s_m"] for m in MODES},
                      "quadrature_roots_m": {m: benchmark[m]["polar_quadrature_s_limit_m"] for m in MODES},
                      "elapsed_seconds": result["environment"]["elapsed_seconds"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
