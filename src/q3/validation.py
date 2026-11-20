"""q3 数值验收，输入同工况下的五方法输出、临界根和扫描数据。
供第三问运行入口决定主方法是否通过，并输出可复核的核对指标。
单独验证一次运行可执行 nohup python3 -B -m src.run q3 --profile full。
AI 辅助 OpenCode（Anomaly）与 Magpie gpt-6.2-sol（OpenAI，版本发布日期未公开），2026-11-20。
"""

import math

import numpy as np

from .config import Config
from .model import MODES


def _key(row: dict) -> tuple[str, str]:
    """用口径和工况标签连接不同方法在同一点的输出。"""
    return row["mode"], row["label"]


def _deterministic(cfg: Config, methods: dict, limits: dict, ab_checks: list[dict], scan: list[dict]) -> dict:
    """计算密度、分段复算、独立求积、根与两侧不等式的验收证据。"""
    settings = cfg.validation
    projected = methods["projected_normal"]
    main = {_key(row): row for row in projected["sites"]}
    hermite = methods["hermite"]
    previous, last = cfg.sampling.hermite_orders[-2:]
    last_rows = [row for row in hermite["levels"] if row["order"] == last]
    previous_rows = {_key(row): row["probability"] for row in hermite["levels"] if row["order"] == previous}
    max_alt_delta = max(abs(row["probability_main"] - row["probability_alternate"]) for row in main.values())
    max_estimate = max(max(row["error_main"], row["error_alternate"], row["density_error_estimate"]) for row in main.values())
    max_density_delta = max(abs(row["density_mass"] - 1.0) for row in [*scan, *main.values()])
    min_pdf = min(row["minimum_pdf_per_rad"] for row in scan if row["minimum_pdf_per_rad"] is not None)
    max_hermite_delta = max(abs(row["probability"] - main[_key(row)]["probability_main"]) for row in last_rows)
    max_hermite_step = max(abs(row["probability"] - previous_rows[_key(row)]) for row in last_rows)
    maximum_scan_increase = {mode: float(max(np.diff([row[mode] for row in scan]))) for mode in MODES}
    roots = {mode: {
        "absolute_residual": abs(limits[mode]["probability_at_root"] - cfg.minimum_probability),
        "alternate_sigma_difference_m": abs(projected["alternate_sigma_limits_m"][mode] - limits[mode]["sigma_m"]),
        "hermite_sigma_difference_m": abs(hermite["sigma_limits_m"][mode] - limits[mode]["sigma_m"]),
    } for mode in MODES}
    signed_margins = []
    for row in main.values():
        delta = row["probability_main"] - cfg.minimum_probability
        if row["label"] == "below_deterministic":
            signed_margins.append(delta)
        if row["label"] == "above_deterministic":
            signed_margins.append(-delta)
        if row["label"] == "operational_floor":
            signed_margins.append(delta)
    min_boundary_margin = min(signed_margins, default=None)
    max_ab_residual = max(abs(row["probability"] - cfg.minimum_probability) for row in ab_checks)
    checks = {
        "projection_quadrature": max(max_alt_delta, max_estimate, max_density_delta) <= settings.quadrature_agreement and min_pdf >= 0.0,
        "hermite_agreement": max(max_hermite_step, max_hermite_delta) <= settings.hermite_agreement,
        "monotonicity_scan": max(maximum_scan_increase.values()) <= settings.quadrature_agreement,
        "critical_boundary": (min_boundary_margin is None or min_boundary_margin > 10.0 * max(max_estimate, max_alt_delta))
        and max_ab_residual <= settings.quadrature_agreement
        and all(row["absolute_residual"] <= settings.quadrature_agreement
                and max(row["alternate_sigma_difference_m"], row["hermite_sigma_difference_m"]) <= settings.root_agreement_m
                for row in roots.values()),
    }
    return {"checks": checks, "maximum_quad_split_difference": max_alt_delta, "maximum_error_estimate": max_estimate,
            "maximum_density_mass_error": max_density_delta, "minimum_pdf_per_rad": min_pdf,
            "maximum_hermite_difference": max_hermite_delta, "maximum_hermite_adjacent_step": max_hermite_step,
            "maximum_scan_increase": maximum_scan_increase, "roots": roots, "minimum_boundary_margin": min_boundary_margin,
            "maximum_ab_boundary_residual": max_ab_residual}


def _stochastic(cfg: Config, methods: dict) -> dict:
    """以同时二项置信区间和重复 scramble 的采样波动复核主积分。"""
    projected = {_key(row): row["probability_main"] for row in methods["projected_normal"]["sites"]}
    family_count = len(methods["monte_carlo"]["sites"])
    mc_checks = []
    boundary_checks = []
    for row in methods["monte_carlo"]["sites"]:
        reference = projected[_key(row)]
        inside = row["simultaneous_low"] <= reference <= row["simultaneous_high"]
        wilson = row["wilson_low"] <= reference <= row["wilson_high"]
        if row["label"] == "below_stochastic":
            boundary_checks.append(row["simultaneous_low"] > cfg.minimum_probability)
        if row["label"] == "above_stochastic":
            boundary_checks.append(row["simultaneous_high"] < cfg.minimum_probability)
        mc_checks.append({**row, "reference_probability": reference, "wilson_contains_reference": wilson,
                          "simultaneous_contains_reference": inside})

    convergence = []
    for row in methods["sobol"]["levels"]:
        reference = projected[_key(row)]
        tolerance = max(cfg.validation.sobol_absolute_tolerance, 4.0 * row["standard_error"])
        convergence.append({**row, "reference_probability": reference,
                            "difference": row["probability"] - reference, "agreement_tolerance": tolerance,
                            "within_tolerance": abs(row["probability"] - reference) <= tolerance})
    powers = cfg.sampling.sobol_powers
    first = [check for check in convergence if check["power"] == powers[0]]
    last = [check for check in convergence if check["power"] == powers[-1]]
    first_rmse = math.sqrt(float(np.mean([check["difference"] ** 2 for check in first])))
    last_rmse = math.sqrt(float(np.mean([check["difference"] ** 2 for check in last])))
    checks = {
        "mc_simultaneous_agreement": all(row["simultaneous_contains_reference"] for row in mc_checks),
        "mc_boundary_classification": all(boundary_checks),
        "sobol_agreement_and_convergence": all(row["within_tolerance"] for row in last)
        and last_rmse <= max(first_rmse, cfg.validation.sobol_absolute_tolerance),
    }
    return {"checks": checks, "monte_carlo_sites": mc_checks, "simultaneous_site_count": family_count,
            "boundary_tests": len(boundary_checks), "sobol_levels": convergence, "sobol_first_rmse": first_rmse,
            "sobol_final_rmse": last_rmse, "sobol_max_final_abs_difference": max(abs(row["difference"]) for row in last)}


def _critical_comparison(cfg: Config, methods: dict, limits: dict) -> list[dict]:
    """把五种方法在各自口径的共同临界里程处对齐，同时列出确定性根。"""
    comparison = []
    for mode in MODES:
        reference = limits[mode]["probability_at_root"]
        options = {
            "taylor": methods["taylor"]["sites"],
            "hermite": [row for row in methods["hermite"]["levels"] if row["order"] == cfg.sampling.hermite_orders[-1]],
            "projected_normal": [{**row, "probability": row["probability_main"]}
                                 for row in methods["projected_normal"]["sites"]],
            "monte_carlo": methods["monte_carlo"]["sites"],
            "sobol": [row for row in methods["sobol"]["levels"] if row["power"] == cfg.sampling.sobol_powers[-1]],
        }
        for name, rows in options.items():
            point = next(row for row in rows if _key(row) == (mode, "critical"))
            sigma_m = (limits[mode]["sigma_m"] if name == "projected_normal"
                       else methods[name].get("sigma_limits_m", {}).get(mode))
            travel_m = cfg.distance_at(sigma_m) if sigma_m is not None else None
            comparison.append({"mode": mode, "method": name, "probability_at_reference_root": point["probability"],
                               "probability_difference": point["probability"] - reference, "critical_sigma_m": sigma_m,
                               "maximum_distance_m": travel_m if travel_m is None or travel_m >= 0.0 else None,
                               "interval_low": point.get("wilson_low", point.get("t_low")),
                               "interval_high": point.get("wilson_high", point.get("t_high")),
                               "runtime_s": methods[name]["elapsed_s"], "pid": methods[name]["pid"]})
    return comparison


def validate_methods(cfg: Config, methods: dict, limits: dict, ab_checks: list[dict], scan: list[dict]) -> tuple[list[dict], dict]:
    """组合验收条件；全量档同时要求抽样一致性、收敛和两侧判别。"""
    deterministic = _deterministic(cfg, methods, limits, ab_checks, scan)
    stochastic = _stochastic(cfg, methods)
    pids = [method["pid"] for method in methods.values()]
    latest_start = max(method["started_monotonic_s"] for method in methods.values())
    earliest_finish = min(method["started_monotonic_s"] + method["elapsed_s"] for method in methods.values())
    checks = {**deterministic["checks"], "parallel_workers": len(set(pids)) == 5}
    if cfg.profile == "full":
        checks.update(stochastic["checks"])
    failed = [name for name, passed in checks.items() if not passed]
    return _critical_comparison(cfg, methods, limits), {
        "passed": not failed, "checks": checks, "failed": failed, "deterministic": deterministic, "stochastic": stochastic,
        "pids": pids, "start_spread_s": latest_start - min(method["started_monotonic_s"] for method in methods.values()),
        "five_way_overlap_s": max(0.0, earliest_finish - latest_start),
    }
