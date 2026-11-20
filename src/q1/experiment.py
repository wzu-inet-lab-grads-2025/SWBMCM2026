"""q1 运行编排与产物序列化，由 src/run.py 调用。
输入为 YAML、运行档和输出根目录，输出包括图表、掩码、完整最优解集和运行记录。
单独运行 nohup python3 -B -m src.run q1 --profile full。
"""

import csv
import hashlib
import json
import logging
import math
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import numpy as np
import yaml

from .config import Config, load_config
from .optimize import OptimumResult, primary_optimal_examples, solve_optimum
from .plots import write_plots
from .region import RegionResult, build_observable_region


def _version_info(root: Path) -> dict:
    """记录当前提交、工作区状态、逐文件哈希和依赖版本。"""
    files = [root / "src" / "run.py", *sorted((root / "src" / "q1").glob("*.py"))]
    info = {
        "source_sha256": {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
        "python": platform.python_version(),
        "dependencies": {name: version(name) for name in ("numpy", "scipy", "matplotlib", "PyYAML")},
        "git_head": None,
        "git_worktree_status": None,
    }
    try:
        info["git_head"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        info["git_worktree_status"] = subprocess.check_output(
            ["git", "status", "--short", "--untracked-files=all", "--", "src", "configs", "checks"],
            cwd=root, text=True,
        ).splitlines()
    except (OSError, subprocess.CalledProcessError):
        # 脱离 Git 的复跑仍可通过哈希与参数快照核对所用版本。
        pass
    return info


def _save_region(region: RegionResult, directory: Path) -> str:
    """保存单元中心和四类掩码，返回压缩文件名。"""
    # 近似相等的配置值也要各自保留，完整浮点表示能避免格式化导致重名。
    name = f"region_{region.resolution!r}m.npz"
    np.savez_compressed(directory / name, **asdict(region))
    return name


def _write_region_csv(rows: list[dict], directory: Path) -> None:
    """写全场独立通过数、顺序剩余数及面积对照 CSV。"""
    flat = [{
        "resolution_m": row["resolution_m"], "total_cells": row["total_cells"],
        "vehicle_pass": row["independent_pass"]["vehicle"], "gimbal_pass": row["independent_pass"]["gimbal"],
        "los_pass": row["independent_pass"]["los"], **row["sequential_remaining"],
        "observable_cells": row["feasible_cells"], "estimated_area_m2": row["estimated_area_m2"],
        "relative_area_change": row["relative_area_change"], "seconds": row["seconds"],
    } for row in rows]
    with (directory / "region_counts.csv").open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(flat[0]))
        writer.writeheader()
        writer.writerows(flat)


def _angle_summary(result: OptimumResult, cfg: Config) -> dict:
    """输出完整最优弧参数和复核过的代表站点，JSON 中角度为度。"""
    circle = result.circle
    examples = []
    for example in primary_optimal_examples(result, cfg):
        point = example.station
        examples.append({
            "role": example.role,
            "angle_deg": math.degrees(example.angle_rad),
            "station_xy_m": (point.x, point.y),
            "horizontal_distance_m": point.horizontal_distance,
            "elevation_deg": point.elevation_deg,
            "quality_per_m": 1 / point.horizontal_distance,
            "minimum_clearance_m": point.clearance_min,
        })
    return {
        "center_xy_m": cfg.target_xyz[:2],
        "radius_m": circle.radius,
        "is_continuum": any(a < b for a, b in circle.intervals_rad),
        "angle_reference": "counterclockwise_from_east_at_target",
        "angle_domain_deg": {"left": 0, "right": 360, "closed": [True, False]},
        "feasible_intervals_deg": [
            {"left": math.degrees(a), "right": math.degrees(b), "closed": [True, b != math.tau]}
            for a, b in circle.intervals_rad
        ],
        "representative_stations": examples,
        "vehicle_excluded_interval_deg": {"interval": list(map(math.degrees, circle.vehicle_blocked_rad)), "closed": [False, False]},
        "los_blocked_interval_deg": {"interval": list(map(math.degrees, circle.los_blocked_rad)), "closed": [True, True]},
        "coverage": circle.proof,
        "scan_checks": circle.scan_checks,
    }


def _optimum_summary(result: OptimumResult, cfg: Config) -> dict:
    """将主目标证明、净空证书和最终独立几何检查列入运行报告。"""
    witness, point = result.witness, result.selected
    return {
        "primary_optimum": {
            "r_lower_bound_m": result.circle.radius,
            "r_opt_m": result.circle.radius,
            "q_opt_per_m": 1 / result.circle.radius,
            "witness_point_xy": (witness.x, witness.y),
            "witness_valid": witness.feasible,
            "witness_validation": result.witness_validation,
        },
        "optimal_circle": _angle_summary(result, cfg),
        "tie_break": {
            "criterion": "maximize_minimum_vehicle_clearance",
            "selected_angle_deg": math.degrees(result.selected_angle_rad),
            "selected_station_xy": (point.x, point.y),
            "horizontal_distance_m": point.horizontal_distance,
            "elevation_deg": point.elevation_deg,
            "quality_per_m": 1 / point.horizontal_distance,
            "minimum_clearance_m": point.clearance_min,
            "clearance_components_m": result.clearance_terms,
            "active_constraints": result.active_clearance_constraints,
            "active_constraint_tolerance_m": result.active_constraint_tolerance_m,
            "global_upper_bound_m": result.clearance_upper_bound,
            "optimality_gap_m": result.clearance_gap,
            "branch_bound_upper_bound_m": result.branch_bound_upper_bound,
            "analytic_clearance": result.analytic_clearance,
            "numerical_rounding_margin_m": result.rounding_margin_m,
            "branches": result.branches,
            "bound_evaluations": result.bound_evaluations,
            "tied_candidates": result.tie_candidates,
            "coordinate_tie_break": "x_ascending_then_y_ascending",
            "local_peaks": [{"angle_deg": math.degrees(phi), "clearance_m": c} for phi, c in result.local_peaks],
        },
        "validation": {
            **result.validation,
            "blocked_by": point.blocked_by,
            "independent_geometry_passed": (
                result.validation["vehicle_ok"] and result.validation["gimbal_ok"] and result.validation["los_clear"]
            ),
        },
    }


def _format_angle_interval(arc: dict) -> str:
    """将度制角度范围写成 TeX 区间，保留端点的开闭语义。"""
    left_closed, right_closed = arc["closed"]
    lower = f"{arc['left']:.9f}".rstrip("0").rstrip(".")
    upper = f"{arc['right']:.9f}".rstrip("0").rstrip(".")
    return f"{'[' if left_closed else '('}{lower}^\\circ,{upper}^\\circ{']' if right_closed else ')'}"


def _write_station_csv(summary: dict, directory: Path) -> None:
    """把最优弧代表站点写成逐列 CSV，坐标与净空单位为 m，角度为度。"""
    rows = summary["optimal_circle"]["representative_stations"]
    with (directory / "station_table.csv").open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["role", "angle_deg", "x_m", "y_m", "horizontal_distance_m", "elevation_deg",
                         "quality_per_m", "minimum_clearance_m"])
        for row in rows:
            x, y = row["station_xy_m"]
            writer.writerow([row["role"], f"{row['angle_deg']:.12f}", f"{x:.12f}", f"{y:.12f}",
                             f"{row['horizontal_distance_m']:.12f}", f"{row['elevation_deg']:.12f}",
                             f"{row['quality_per_m']:.15f}", f"{row['minimum_clearance_m']:.12f}"])


def _write_arc_csv(summary: dict, directory: Path) -> None:
    """把主目标最优弧的两段端点和开闭语义写成 CSV，角度为度。"""
    arcs = summary["optimal_circle"]["feasible_intervals_deg"]
    with (directory / "optimal_arcs.csv").open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["segment", "left_deg", "right_deg", "left_closed", "right_closed"])
        for index, arc in enumerate(arcs, start=1):
            writer.writerow([index, f"{arc['left']:.12f}", f"{arc['right']:.12f}", *arc["closed"]])


def _write_summary(directory: Path, summary: dict) -> None:
    """输出机器可读结果和队内核对摘要。"""
    with (directory / "summary.json").open("w", encoding="utf-8") as output:
        json.dump(summary, output, ensure_ascii=False, indent=2, allow_nan=False)
        output.write("\n")

    t = summary["tie_break"]
    x, y = t["selected_station_xy"]
    phi = t["selected_angle_deg"]
    rows = summary["region"]["resolution_studies"]
    primary = summary["primary_optimum"]
    circle = summary["optimal_circle"]
    tx, ty = circle["center_xy_m"]
    angles = r" \cup ".join(map(_format_angle_interval, circle["feasible_intervals_deg"]))
    example_labels = {
        "east_witness": "Due east (witness)", "north": "Due north",
        "north_tangent": "B2 north-side tangency", "east_tangent": "B2 east-side tangency",
        "southern_local_peak": "Southeast clearance peak", "selected": "Recommended station",
    }
    lines = [
        "# B 题第一问运行摘要", "",
        "可观测面积为等面积网格中心采样估计。主目标由仰角距离下界和可行见证点证明。"
        "净空择优用经几何核验的可行弧作分支定界，并检查东侧场界与 B2 东北角的解析上界。"
        "所报误差界含浮点余量。", "",
        "| Cell size (m) | Observable cells | Estimated area (m²) |", "|---:|---:|---:|",
        *(f"| {row['resolution_m']:g} | {row['feasible_cells']} | {row['estimated_area_m2']:.6f} |" for row in rows),
        "", "## 完整并列最优解集", "",
        f"圆心为 $T_{{xy}}\\approx({tx:.12f}, {ty:g})$ m，圆周角 $\\varphi$ 从正东逆时针量取。"
        f"所有主目标最优站位均达到 $r_0=(z_T-h_c)/\\tan\\alpha_{{\\max}}\\approx{primary['r_opt_m']:.12f}$ m，"
        f"$Q^\\star\\approx{primary['q_opt_per_m']:.12f}$ m$^{{-1}}$，"
        f"仰角均为 ${summary['input']['elevation_bounds_deg'][1]:g}^\\circ$。", "",
        r"$$\mathcal S_1=\{T_{xy}+r_0(\cos\varphi,\sin\varphi):\varphi\in\Phi^\star\}.$$", "",
        r"精确角域为 $\Phi^\star=[0^\circ,\varphi_N]\cup[\varphi_E,360^\circ)$，"
        r"其中 $\varphi_N=180^\circ-\arcsin((y_{2,\max}+\rho-y_T)/r_0)$，"
        r"$\varphi_E=360^\circ-\arccos((x_{2,\max}+\rho-x_T)/r_0)$。"
        "反三角函数取度，两个边界量为 B2 底面坐标上界，$\\rho$ 为车体半径。", "",
        f"$\\Phi^\\star$ 的数值表示为 ${angles}$（端点角度为近似值）。"
        "这是一条跨过正东方向的连续圆弧，因此有无穷多个并列解。"
        "两个 B2 车体相切端点均可行；360° 与 0° 是同一点。见 [最优圆可行弧图](optimal_arcs.png)。", "",
        "### 代表站点", "",
        "下表的站点均达到相同的主目标值，所列站点只用于展示，不是最优解集的枚举。", "",
        "| Primary-optimal example | Circle angle (°) | Station (m) | Minimum vehicle clearance (m) |",
        "|---|---:|---:|---:|",
        *(f"| {example_labels[row['role']]} | {row['angle_deg']:.9f} | "
          f"({row['station_xy_m'][0]:.9f}, {row['station_xy_m'][1]:.9f}) | {row['minimum_clearance_m']:.9f} |"
          for row in circle["representative_stations"]),
        "", "## 并列解选择规则", "",
        "1. **主目标：**只在达到最小水平距离、即达到最大 $Q$ 的可行站点中择优。",
        "2. **并列择优：**在整条 $\\mathcal S_1$ 上最大化最小车辆净空，而不是仅在表中比较。", "",
        r"   $$c(P)=\min\left\{x-\rho, W-\rho-x, y-\rho, H_f-\rho-y, \min_i[d(P,R_i)-\rho]\right\}.$$", "",
        "   $W$、$H_f$ 是场地宽度和高度，$R_i$ 是第 $i$ 栋建筑的底面，$d(P,R_i)$ 是车心到该底面的距离。"
        "较大的 $c(P)$ 给车辆停放留出更大余量，不牺牲 $Q$，也不改变视线和仰角约束。", "",
        "3. **仍并列：**净空在浮点舍入余量内仍相同时，先取较小的 $x$，再取较小的 $y$；"
        "最终站位仍须通过几何复核和净空全域误差界检查。",
        "", "## 最终推荐站位", "",
        "| Station result | Value |", "|---|---:|",
        f"| Station (m) | ({x:.9f}, {y:.9f}) |",
        f"| Horizontal distance (m) | {t['horizontal_distance_m']:.12f} |",
        f"| Elevation (degrees) | {t['elevation_deg']:.9f} |",
        f"| Quality (m⁻¹) | {t['quality_per_m']:.12f} |",
        f"| Minimum clearance (m) | {t['minimum_clearance_m']:.10f} |",
        f"| Secondary optimality gap (m) | {t['optimality_gap_m']:.4e} |",
        "", f"理论站位由 $P=T_{{xy}}+r_0(\\cos\\varphi,\\sin\\varphi)$ 定义，$\\varphi\\approx {phi:.9f}^\\circ$。"
        "推荐点受东场界和 B2 东北角的净空共同限制。两张表的坐标与角度已舍入，"
        "几何复核使用 `summary.json` 中的完整坐标。",
        "", "按本次快照配置复跑", "", "```bash", summary["run"]["replay_command"], "```", "",
        "`summary.json` 记录实际运行命令、代码哈希、角段、上界与逐栋复核。",
        "实现过程中使用 OpenCode 中的 Codex（gpt-6.2-sol）辅助编程与复核。", "",
    ]
    (directory / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def run_q1(config_path: Path, output_root: Path, profile: str, root: Path, command: str) -> Path:
    """执行调试或全量 q1 运行，返回新输出路径。异常写入日志并向上抛出。"""
    output_root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_"), dir=output_root))
    logger = logging.Logger("q1")
    stream = logging.FileHandler(directory / "run.log", encoding="utf-8")
    stream.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(stream)
    start = time.perf_counter()

    try:
        logger.info("Python 进程参数 %s", command)
        config_copy = directory / "config.yaml"
        shutil.copyfile(config_path, config_copy)
        cfg = load_config(config_copy)
        plot_step = cfg.grid.debug_m if profile == "debug" else cfg.grid.plot_m
        steps = (plot_step,) if profile == "debug" else tuple(dict.fromkeys((*cfg.grid.study_m, plot_step)))
        rows, regions = [], []
        previous_area = None
        for step in steps:
            grid_start = time.perf_counter()
            region = build_observable_region(cfg, step)
            seconds = time.perf_counter() - grid_start
            area = region.estimated_area
            relative_change = (area - previous_area) / previous_area if previous_area else None
            previous_area = area
            regions.append(region)
            rows.append({
                "resolution_m": step, **region.counts, "feasible_cells": region.feasible_count,
                "estimated_area_m2": area, "relative_area_change": relative_change,
                "seconds": seconds, "mask_file": _save_region(region, directory),
            })
            logger.info("网格 %g m，候选 %d，可观测 %d，面积 %.6f m²，计算 %.3f s",
                        step, rows[-1]["total_cells"], region.feasible_count, area, seconds)

        optimum_start = time.perf_counter()
        result = solve_optimum(cfg)
        optimum_seconds = time.perf_counter() - optimum_start
        logger.info("整圈核验与站位优化 %.3f s，净空全域误差界 %.6g m", optimum_seconds, result.clearance_gap)
        plot_region = next(region for region in regions if region.resolution == plot_step)
        plot_start = time.perf_counter()
        write_plots(regions, plot_region, result, cfg, directory)
        plot_seconds = time.perf_counter() - plot_start
        logger.info("绘图 %.3f s，见证点与最终点通过独立几何复核", plot_seconds)

        replay_command = shlex.join([
            "nohup", sys.executable, "-B", "-m", "src.run", "q1", "--profile", profile, "--config", str(config_copy),
        ])
        summary = {
            "problem": "B", "question": "q1",
            "input": {**yaml.safe_load(config_copy.read_text(encoding="utf-8")), "target_xyz": cfg.target_xyz,
                      "config_sha256": hashlib.sha256(config_copy.read_bytes()).hexdigest()},
            "region": {"plot_resolution_m": plot_step, "feasible_grid_count": plot_region.feasible_count,
                       "estimated_area_m2": plot_region.estimated_area, "resolution_studies": rows},
            **_optimum_summary(result, cfg),
            "run": {"command": command, "replay_command": replay_command, "profile": profile, "optimization_seconds": optimum_seconds,
                    "plot_seconds": plot_seconds, **_version_info(root)},
        }
        _write_region_csv(rows, directory)
        _write_station_csv(summary, directory)
        _write_arc_csv(summary, directory)
        summary["run"]["total_seconds"] = time.perf_counter() - start
        _write_summary(directory, summary)
        logger.info("运行完成，耗时 %.3f s，文件 %s", time.perf_counter() - start, directory)
    except Exception:
        logger.exception("q1 运行失败，输出目录 %s", directory)
        raise
    finally:
        stream.close()
    return directory
