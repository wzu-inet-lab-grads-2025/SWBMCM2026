"""q3 运行编排，以独立进程并行执行五种方法，计算边界及验收指标。
输入题设 YAML 与档位，由 src/run.py 调用并输出到 runs/q3 的独立目录。
单独运行 nohup python3 -B -m src.run q3 --profile full。
AI 辅助 OpenCode（Anomaly）与 Magpie gpt-6.2-sol（OpenAI，版本发布日期未公开），2026-11-20。
"""

from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import math
import multiprocessing as mp
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import time

import numpy as np
from scipy.special import log_ndtr

from .config import Config, load_config
from .methods import Site, hermite_results, linear_reliability, linear_results, monte_carlo, projected_results, sobol
from .model import (
    MODES, angle_density_mass, position_angle_variance_deg2, projected_normal_angle_pdf,
    reliability_1d_adaptive, solve_sigma_critical, wrap_window_count,
)


METHODS = {
    "taylor": linear_results,
    "hermite": hermite_results,
    "projected_normal": projected_results,
    "monte_carlo": monte_carlo,
    "sobol": sobol,
}


def _probability(cfg: Config, sigma_m: float, mode: str) -> float:
    """用默认积分分段和容差计算达标概率，定位标准差以 m 计。"""
    return reliability_1d_adaptive(sigma_m, cfg.model, mode, cfg.validation.quad_tolerance).value


def _limits(cfg: Config) -> dict:
    """计算两种口径的根、可执行厘米里程和 80 m 参数边界。"""
    limits = {}
    for mode in MODES:
        sigma_m = solve_sigma_critical(
            lambda sigma: _probability(cfg, sigma, mode), cfg.minimum_probability,
            cfg.sigma_at(cfg.design_distance_m), cfg.validation.root_tolerance_m,
        )
        candidate = cfg.distance_at(sigma_m)
        travel_m = candidate if candidate >= 0.0 else None
        floor_m = math.floor(100.0 * travel_m) / 100.0 if travel_m is not None else None
        if floor_m is not None and _probability(cfg, cfg.sigma_at(floor_m), mode) < cfg.minimum_probability:
            floor_m = round(floor_m - 0.01, 2) if floor_m > 0.0 else None
        limits[mode] = {
            "sigma_m": sigma_m, "travel_m": travel_m, "floor_to_centimeter_m": floor_m,
            "probability_at_root": _probability(cfg, sigma_m, mode),
            "a_intercept_m": sigma_m, "b_intercept_m_per_m": sigma_m / cfg.design_distance_m,
            "b_max_at_nominal_a_m_per_m": (sigma_m - cfg.initial_sigma_m) / cfg.design_distance_m
            if sigma_m >= cfg.initial_sigma_m else None,
            "a_max_at_nominal_b_m": sigma_m - cfg.growth * cfg.design_distance_m
            if sigma_m >= cfg.growth * cfg.design_distance_m else None,
        }
    return limits


def _sites(cfg: Config, limits: dict) -> tuple[tuple[Site, ...], tuple[Site, ...]]:
    """构造五种方法共用的模拟工况与仅用确定性方法验证的厘米级工况。"""
    shared, extra = [], []
    for mode, limit in limits.items():
        critical = limit["travel_m"]
        shared.extend((Site(mode, "initial", 0.0, cfg.initial_sigma_m),
                       Site(mode, "critical", critical, limit["sigma_m"]),
                       Site(mode, "at_design", cfg.design_distance_m, cfg.sigma_at(cfg.design_distance_m))))
        if critical is None:
            continue
        for dest, offset, suffix in ((shared, cfg.validation.stochastic_probe_m, "stochastic"),
                                     (extra, cfg.validation.deterministic_probe_m, "deterministic")):
            if critical >= offset:
                dest.append(Site(mode, f"below_{suffix}", critical - offset, cfg.sigma_at(critical - offset)))
            dest.append(Site(mode, f"above_{suffix}", critical + offset, cfg.sigma_at(critical + offset)))
        floor_m = limit["floor_to_centimeter_m"]
        if floor_m is not None:
            extra.append(Site(mode, "operational_floor", floor_m, cfg.sigma_at(floor_m)))
    return tuple(shared), tuple(extra)


def _init_worker(barrier):
    """在 spawn 出的各进程中设定共用起跑屏障。"""
    global _start_barrier
    _start_barrier = barrier


def _run_worker(name: str, cfg: Config, sites: tuple[Site, ...]) -> dict:
    """五个独立进程各领一个方法，齐到后启动并记录实际耗时。"""
    _start_barrier.wait(timeout=60.0)
    started = time.perf_counter()
    result = METHODS[name](cfg, sites)
    return {**result, "pid": os.getpid(), "started_monotonic_s": started,
            "elapsed_s": time.perf_counter() - started}


def _run_parallel(cfg: Config, sites: tuple[Site, ...], extra: tuple[Site, ...]) -> dict:
    """同时派发五个任务。对确定性方法加入额外工况，抽样规模保持一致。"""
    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(len(METHODS))
    completed = {}
    with ProcessPoolExecutor(max_workers=len(METHODS), mp_context=ctx,
                             initializer=_init_worker, initargs=(barrier,)) as executor:
        tasks = {executor.submit(_run_worker, name, cfg, sites + extra if name in ("hermite", "projected_normal") else sites): name
                 for name in METHODS}
        for task in as_completed(tasks):
            name = tasks[task]
            completed[name] = task.result()
            print(f"q3 {name}: 完成，PID {completed[name]['pid']}，耗时 {completed[name]['elapsed_s']:.3f} s", flush=True)
    return {name: completed[name] for name in METHODS}


def _scan_and_curve(cfg: Config, limits: dict) -> tuple[list[dict], list[dict]]:
    """扫描从零定位误差到近均匀方向区间，并计算论文用概率–里程曲线。"""
    settings = cfg.validation
    scales_m = [0.0, *np.geomspace(cfg.model.range_m * 0.001, cfg.model.range_m * 1000.0,
                                  settings.monotonic_points).tolist()]
    scan = []
    for sigma_m in scales_m:
        mass = angle_density_mass(sigma_m, cfg.model, settings.quad_tolerance)
        min_pdf = min(projected_normal_angle_pdf(alpha, sigma_m, cfg.model.range_m)
                      for alpha in np.linspace(0.0, math.pi, 73)) if sigma_m > 0.0 else None
        scan.append({"sigma_m": sigma_m, "density_mass": mass.value, "minimum_pdf_per_rad": min_pdf,
                     **{mode: _probability(cfg, sigma_m, mode) for mode in MODES}})

    xmax = max(settings.plot_max_distance_m, cfg.design_distance_m,
               *(1.05 * (limits[mode]["travel_m"] or 0) for mode in MODES))
    distances = set(np.linspace(0.0, xmax, settings.plot_points).tolist())
    distances.add(cfg.design_distance_m)
    distances.update(limit["travel_m"] for limit in limits.values() if limit["travel_m"] is not None)
    curve = []
    for distance_m in sorted(distances):
        sigma_m = cfg.sigma_at(distance_m)
        curve.append({"distance_m": distance_m, "sigma_m": sigma_m,
                      **{mode: _probability(cfg, sigma_m, mode) for mode in MODES},
                      **{f"taylor_{mode}": linear_reliability(sigma_m, cfg.model, mode) for mode in MODES}})
    return scan, curve


def _contributions(cfg: Config, limits: dict) -> dict:
    """对三类误差计算线性方差、回绕前的几何方差以及删去单项的概率改善。"""
    model = cfg.model
    gimbal_deg = model.gimbal_sd_deg("actual")
    base = [("initial", 0.0), ("critical", limits["actual"]["travel_m"]), ("at_design", cfg.design_distance_m)]
    reduced = {"zero": replace(model, zero_deg=0.0), "control": replace(model, control_deg=0.0)}
    rows = []
    for label, distance_m in base:
        sigma_m = cfg.sigma_at(distance_m) if distance_m is not None else limits["actual"]["sigma_m"]
        vloc_lin = math.degrees(sigma_m / model.range_m) ** 2
        vloc_geom = position_angle_variance_deg2(sigma_m, model, cfg.validation.quad_tolerance)
        all_prob = _probability(cfg, sigma_m, "actual")
        without_loc = _probability(cfg, 0.0, "actual")
        without_gimbal = {source: _probability(replace(cfg, model=sub_model), sigma_m, "actual")
                          for source, sub_model in reduced.items()}
        # 发生回绕必有 X<0 或 |εg|≥90°，报告尾界的对数，避免极小概率下溢。
        log_back = log_ndtr(-model.range_m / sigma_m) if sigma_m else -math.inf
        log_gimbal = math.log(2.0) + log_ndtr(-90.0 / gimbal_deg) if gimbal_deg else -math.inf
        log_wrap = float(np.logaddexp(log_back, log_gimbal) / math.log(10.0))
        for source, vlin, vgeom, without in (
            ("localization", vloc_lin, vloc_geom, without_loc),
            ("zero", model.zero_deg ** 2, model.zero_deg ** 2, without_gimbal["zero"]),
            ("control", model.control_deg ** 2, model.control_deg ** 2, without_gimbal["control"]),
        ):
            lin_total = vloc_lin + gimbal_deg ** 2
            geom_total = vloc_geom + gimbal_deg ** 2
            rows.append({"label": label, "distance_m": distance_m, "sigma_m": sigma_m, "source": source,
                         "linear_variance_deg2": vlin, "linear_share": vlin / lin_total if lin_total else None,
                         "geometry_variance_deg2": vgeom, "geometry_share": vgeom / geom_total if geom_total else None,
                         "all_probability": all_prob, "without_probability": without, "delta_probability": without - all_prob,
                         "log10_wrap_event_bound": log_wrap if math.isfinite(log_wrap) else None})
    crossovers = {name: cfg.distance_at(model.range_m * math.radians(std))
                  for name, std in (("control", model.control_deg), ("zero", model.zero_deg), ("gimbal", gimbal_deg))}
    return {"rows": rows, "first_order_crossover_distance_m": crossovers}


def _ab_boundaries(cfg: Config, limits: dict) -> tuple[list[dict], list[dict]]:
    """采样 a-b 边界并在两截距和中点回代精确概率。"""
    curve, checks = [], []
    for mode, limit in limits.items():
        for i in range(101):
            a_m = limit["sigma_m"] * i / 100.0
            b = max(0.0, (limit["sigma_m"] - a_m) / cfg.design_distance_m)
            row = {"mode": mode, "a_m": a_m, "b_m_per_m": b}
            curve.append(row)
            if i in (0, 50, 100):
                sigma_m = a_m + b * cfg.design_distance_m
                checks.append({**row, "sigma_m": sigma_m, "probability": _probability(cfg, sigma_m, mode)})
    return curve, checks


def _metadata(root: Path, command: str, elapsed: float, config_path: Path) -> dict:
    """记录复跑命令、软件版本、基准提交、源文件与配置 SHA-256。"""
    files = [root / "src/run.py", *(root / "src/q3").glob("*.py"), *sorted((root / "checks").glob("*q3*.py"))]
    result = {"command": command, "elapsed_s": elapsed, "python": platform.python_version(),
              "dependencies": {name: version(name) for name in ("numpy", "scipy", "matplotlib", "PyYAML")},
              "git_head": None, "git_status_q3": None,
              "input_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
              "source_sha256": {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in sorted(files) if p.is_file()},
              "ai_assistance": "OpenCode (Anomaly) / Magpie gpt-6.2-sol (OpenAI), 2026-11-20; release date unavailable"}
    try:
        result["git_head"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        result["git_status_q3"] = subprocess.check_output(
            ["git", "status", "--short", "--untracked-files=all", "--", "src/run.py", "src/q3", "checks/*q3*.py", "configs/q3.yaml"],
            cwd=root, text=True,
        ).splitlines()
    except (OSError, subprocess.CalledProcessError):
        # 无 Git 的支撑材料可以通过快照和哈希确认代码版本。
        pass
    return result


def run_q3(config_path: Path, output_root: Path, profile: str, root: Path, command: str) -> Path:
    """运行第三问、写出数值诊断与结果，通过所选档位的验收后生成图表。"""
    from .report import write_records, write_results
    from .validation import validate_methods

    start = time.perf_counter()
    cfg = load_config(config_path, profile)
    output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_")
    directory = Path(tempfile.mkdtemp(prefix=stamp, dir=output_root))
    shutil.copy2(config_path, directory / "config.yaml")
    limits = _limits(cfg)
    sites, extra = _sites(cfg, limits)
    print(f"q3 {profile}: {directory}，并行启动五种方法", flush=True)
    methods = _run_parallel(cfg, sites, extra)
    scan, curve = _scan_and_curve(cfg, limits)
    ab_curve, ab_checks = _ab_boundaries(cfg, limits)
    comparison, validation = validate_methods(cfg, methods, limits, ab_checks, scan)
    gimbal_data = {}
    for mode in MODES:
        std = cfg.model.gimbal_sd_deg(mode)
        windows, log_bound = wrap_window_count(cfg.model.threshold_deg, std, cfg.model.wrap_tail_tolerance)
        gimbal_data[mode] = {"gimbal_sd_deg": std, "retained_wrap_periods_each_side": windows,
                             "log10_omitted_wrap_probability_bound": log_bound}
    summary = {
        "profile": profile, "chosen_method": "projected_normal" if validation["passed"] else None,
        "primary_mode": "actual", "inputs": {**asdict(cfg.model), "initial_sigma_m": cfg.initial_sigma_m,
                                                "growth": cfg.growth, "minimum_probability": cfg.minimum_probability,
                                                "design_distance_m": cfg.design_distance_m},
        "gimbal": gimbal_data, "limits": limits,
        "sites": [{**row, "probability": row["probability_main"]} for row in methods["projected_normal"]["sites"]],
        "ab_boundary_checks": ab_checks, "contributions": _contributions(cfg, limits),
        "comparison_at_critical": comparison, "validation": validation,
    }
    result = {"summary": summary, "methods": methods, "scan": scan, "curve": curve, "ab_curve": ab_curve}
    write_records(directory, cfg, result, _metadata(root, command, time.perf_counter() - start, config_path))
    if not validation["passed"]:
        raise RuntimeError(f"第三问数值验收未通过 {validation['failed']}；明细见 {directory}")
    write_results(directory, cfg, result)
    return directory
