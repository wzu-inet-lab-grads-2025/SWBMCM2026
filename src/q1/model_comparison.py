"""q1 模型对照实验，用平面投影约束求解站位并与原版比较。
输入为原版运行、题面配置和实验设置，输出为 runs/q1 下的对照报告。
单独运行 nohup python3 -B -m src.run q1 --compare-baseline <原版运行目录>。
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
from itertools import product
import json
import math
from pathlib import Path
import shlex
import shutil
from statistics import median
import sys
import tempfile
from time import perf_counter

import numpy as np
from scipy.optimize import minimize
import yaml

from .config import Building, Config, load_config
from .geometry import (
    StationCheck, camera_shadow_hull, compute_clearance, evaluate_station,
    independently_check_station, intersects_closed_box_by_faces,
)
from .optimize import solve_optimum


@dataclass(frozen=True)
class ComparisonSettings:
    """替代模型的多起点、停止精度和计时设置，径向复核阈值单位为 m。"""

    starts_per_axis: int
    max_iterations: int
    solver_ftol: float
    radius_check_m: float
    timing_repeats: int


def _projection_planes(cfg: Config) -> tuple[tuple[Building, np.ndarray], ...]:
    """生成每栋可能遮挡楼体的平面半空间系数，法向量为单位向量。"""
    return tuple(
        (b, camera_shadow_hull(cfg, b).equations)
        for b in cfg.buildings if b.height >= cfg.camera_height
    )


def _checked_solution(
    xy: np.ndarray, cfg: Config, tolerance: float, level: float | None = None,
) -> StationCheck | None:
    """按原三维约束验收点，允许在距离比较容差内修正径向舍入。"""
    target = np.asarray(cfg.target_xyz[:2])
    offset = xy - target
    radius = math.hypot(*offset)
    desired = max(radius, cfg.distance_lower_bound) if level is None else level
    if radius == 0 or abs(desired - radius) > tolerance:
        return None
    point = xy.copy() if radius == desired else target + offset * (desired / radius)
    axis = int(np.argmax(np.abs(offset)))
    for _ in range(4):
        station = evaluate_station(*map(float, point), cfg)
        exact = independently_check_station(station.x, station.y, cfg)
        if station.gimbal_ok and exact["gimbal_ok"]:
            if station.feasible and exact["vehicle_ok"] and exact["los_clear"]:
                return station
            return None
        # 浮点坐标可能落到仰角圆内侧一两个 ULP，沿主要径向分量取外侧浮点值。
        point[axis] = np.nextafter(point[axis], math.copysign(math.inf, offset[axis]))
    return None


def solve_projected_model(cfg: Config, settings: ComparisonSettings) -> dict:
    """直接在 (x,y) 求主目标，再用 (x,y,c) 在相同质量下最大化净空。"""
    shadows = _projection_planes(cfg)
    target = np.asarray(cfg.target_xyz[:2])
    rho = cfg.vehicle_radius
    bounds = [(rho, cfg.field_width - rho), (rho, cfg.field_height - rho)]
    upper_angle = math.radians(cfg.elevation_bounds_deg[1])
    height_difference = cfg.target_xyz[2] - cfg.camera_height
    options = {"ftol": settings.solver_ftol, "maxiter": settings.max_iterations}

    def quality_loss(xy: np.ndarray) -> float:
        """半平方水平距离（m²）与题设 Q=1/r 有相反的单调方向。"""
        offset = xy - target
        return float(0.5 * (offset @ offset))

    def quality_gradient(xy: np.ndarray) -> np.ndarray:
        """返回半平方距离对 m 制车心坐标的梯度。"""
        return xy - target

    def margins(xy: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """返回各车体净空和各凸包最大半平面值，单位为 m。"""
        _, terms = compute_clearance(*xy, cfg)
        visible = [max(planes[:, :2] @ xy + planes[:, 2]) for _, planes in shadows]
        return np.asarray(tuple(terms.values())), np.asarray(visible)

    def primary_constraints(xy: np.ndarray) -> np.ndarray:
        """SLSQP 检查车体、投影视线以及由仰角公式等价变形的 m 制余量。"""
        clearance, visibility = margins(xy)
        angle_margin = math.hypot(*(xy - target)) * math.sin(upper_angle) - height_difference * math.cos(upper_angle)
        # 遮挡凸包的边界也遮挡。数值求解先用闭不等式，输出用原三维严格检查筛选。
        return np.concatenate((clearance, visibility, [angle_margin]))

    axes = [np.linspace(a, b, settings.starts_per_axis) for a, b in bounds]
    primary_attempts = settings.starts_per_axis**2
    first_pass, primary_success = [], 0
    for start in product(*axes):
        trial = minimize(
            quality_loss, start, jac=quality_gradient, bounds=bounds, method="SLSQP", options=options,
            constraints=[{"type": "ineq", "fun": primary_constraints}],
        )
        if trial.success:
            primary_success += 1
            point = _checked_solution(trial.x, cfg, settings.radius_check_m)
            if point is not None:
                first_pass.append(point)
    if not first_pass:
        raise ValueError("二维投影模型在所有起点均未找到通过题面复核的站位")
    primary = min(first_pass, key=lambda point: point.horizontal_distance)
    best_radius = primary.horizontal_distance
    starts = [point for point in first_pass if point.horizontal_distance - best_radius <= settings.radius_check_m]

    def clearance_loss(state: np.ndarray) -> float:
        """最小化负净空（m），c 是对所有车体余量的共同下界。"""
        return float(-state[2])

    def clearance_gradient(state: np.ndarray) -> np.ndarray:
        """返回负净空对 x、y、c 三个变量的梯度。"""
        return np.array([0.0, 0.0, -1.0])

    def clearance_constraints(state: np.ndarray) -> np.ndarray:
        """车体各项净空须不小于 c，平面遮挡约束继续生效。"""
        clearance, visibility = margins(state[:2])
        return np.concatenate((clearance - state[2], visibility))

    def primary_level(state: np.ndarray) -> float:
        """维持本站第一阶段求得的水平距离，等式残差以 m 计。"""
        return (quality_loss(state[:2]) - 0.5 * best_radius**2) / best_radius

    second_pass, secondary_success = [], 0
    for point in starts:
        trial = minimize(
            clearance_loss, (point.x, point.y, point.clearance_min), jac=clearance_gradient,
            bounds=[*bounds, (0.0, None)], method="SLSQP", options=options,
            constraints=[{"type": "ineq", "fun": clearance_constraints}, {"type": "eq", "fun": primary_level}],
        )
        if trial.success:
            secondary_success += 1
            candidate = _checked_solution(trial.x[:2], cfg, settings.radius_check_m, best_radius)
            if candidate is not None:
                second_pass.append(candidate)
    if not second_pass:
        raise ValueError("二维投影模型未在已找到的同质量站位中通过净空择优复核")
    best = max(second_pass, key=lambda point: (point.clearance_min, -point.x, -point.y))
    return {
        "primary": asdict(primary), "selected": asdict(best),
        "quality_per_m": 1 / best.horizontal_distance,
        "validation": independently_check_station(best.x, best.y, cfg),
        "primary_attempts": primary_attempts, "primary_solver_success": primary_success,
        "primary_verified_feasible": len(first_pass), "secondary_attempts": len(starts),
        "secondary_solver_success": secondary_success, "secondary_verified_feasible": len(second_pass),
        "secondary_minimum_clearance_range_m": [min(p.clearance_min for p in second_pass), best.clearance_min],
    }


def _compare_masks(baseline: Path, summary: dict, cfg: Config) -> list[dict]:
    """对原版同一批格心重算投影视线，并比较闭接触语义下的可观测掩码。"""
    shadows = _projection_planes(cfg)
    rows = []
    for row in summary["region"]["resolution_studies"]:
        with np.load(baseline / row["mask_file"]) as masks:
            xs, ys = masks["x_centers"], masks["y_centers"]
            x, y = xs[None, :], ys[:, None]
            projected_clear = np.ones((ys.size, xs.size), dtype=bool)
            for building, planes in shadows:
                margin = np.full(projected_clear.shape, -np.inf)
                for nx, ny, offset in planes:
                    np.maximum(margin, nx * x + ny * y + offset, out=margin)
                clear = margin > 0
                # 投影边界用独立的逐面交点处理浮点近零值，接触语义与题面一致。
                for iy, ix in np.argwhere(np.abs(margin) <= cfg.numerics.position_review_m):
                    camera = (float(xs[ix]), float(ys[iy]), cfg.camera_height)
                    clear[iy, ix] = not intersects_closed_box_by_faces(camera, cfg.target_xyz, *building.bounds)
                projected_clear &= clear
            # 两个模型对车辆和仰角使用同一题面条件，保持格心和这两个掩码一致。
            observed = projected_clear & masks["vehicle_mask"] & masks["gimbal_mask"]
            rows.append({
                "resolution_m": row["resolution_m"], "total_cells": int(projected_clear.size),
                "los_disagreements": int(np.count_nonzero(projected_clear != masks["los_clear_mask"])),
                "observable_disagreements": int(np.count_nonzero(observed != masks["observable_mask"])),
                "projected_observable_count": int(observed.sum()),
            })
    return rows


def run_model_comparison(
    baseline: Path, config_path: Path, settings_path: Path, output_root: Path, root: Path, command: str,
) -> Path:
    """在共享配置下运行两套模型，比较归档结果并新建对照目录。"""
    from .experiment import _version_info

    summary_path = baseline / "summary.json"
    baseline_data = summary_path.read_bytes()
    summary = json.loads(baseline_data)
    baseline_config = (baseline / "config.yaml").read_bytes()
    if (summary["question"] != "q1"
            or hashlib.sha256(baseline_config).hexdigest() != summary["input"]["config_sha256"]
            or yaml.safe_load(baseline_config) != yaml.safe_load(config_path.read_bytes())):
        raise ValueError("原版运行与此次比较的 q1 配置不一致")
    cfg = load_config(config_path)
    settings = ComparisonSettings(**yaml.safe_load(settings_path.read_bytes()))
    if not (settings.starts_per_axis >= 2 and settings.timing_repeats >= 1 and settings.max_iterations >= 1
            and 0 < settings.solver_ftol < 1 and 0 < settings.radius_check_m < 1):
        raise ValueError("模型对照设置的起点、迭代或精度参数无效")

    geometry_seconds, projected_seconds = [], []
    for repetition in range(settings.timing_repeats):
        # 交替顺序，站位求解计时不计入配置读取、网格比对和写盘。
        if repetition % 2 == 0:
            started = perf_counter()
            current = solve_optimum(cfg)
            geometry_seconds.append(perf_counter() - started)
            started = perf_counter()
            alternate = solve_projected_model(cfg, settings)
            projected_seconds.append(perf_counter() - started)
        else:
            started = perf_counter()
            alternate = solve_projected_model(cfg, settings)
            projected_seconds.append(perf_counter() - started)
            started = perf_counter()
            current = solve_optimum(cfg)
            geometry_seconds.append(perf_counter() - started)
    mask_comparison = _compare_masks(baseline, summary, cfg)
    primary_upper = summary["primary_optimum"]["q_opt_per_m"]
    best = alternate["selected"]
    old = summary["tie_break"]

    output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = Path(tempfile.mkdtemp(prefix=f"{stamp}_模型对照_", dir=output_root))
    shutil.copyfile(config_path, folder / "config.yaml")
    shutil.copyfile(settings_path, folder / "comparison_config.yaml")
    replay_command = shlex.join([
        "nohup", sys.executable, "-B", "-m", "src.run", "q1", "--compare-baseline", str(baseline),
        "--config", str(folder / "config.yaml"), "--comparison-config", str(folder / "comparison_config.yaml"),
    ])
    report = {
        "question": "q1", "baseline_run": str(baseline),
        "baseline_summary_sha256": hashlib.sha256(baseline_data).hexdigest(),
        "baseline": {"selected_station_xy": old["selected_station_xy"], "distance_m": old["horizontal_distance_m"],
                     "quality_per_m": old["quality_per_m"], "clearance_m": old["minimum_clearance_m"],
                     "archived_optimization_seconds": summary["run"]["optimization_seconds"]},
        "geometry_rerun": {"selected": asdict(current.selected), "clearance_gap_m": current.clearance_gap},
        "projected_nlp": alternate, "primary_quality_upper_bound_per_m": primary_upper,
        "projected_quality_gap_to_upper_per_m": primary_upper - alternate["quality_per_m"],
        "projected_clearance_minus_archived_m": best["clearance_min"] - old["minimum_clearance_m"],
        "projected_clearance_minus_current_geometry_m": best["clearance_min"] - current.selected.clearance_min,
        "mask_comparison": mask_comparison,
        "timing": {"geometry_seconds": geometry_seconds, "projected_nlp_seconds": projected_seconds,
                   "geometry_median_seconds": median(geometry_seconds), "projected_nlp_median_seconds": median(projected_seconds)},
        "settings": asdict(settings), "command": command, "replay_command": replay_command,
        "provenance": _version_info(root),
    }
    (folder / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    lines = [
        "# B 题第一问模型对照", "",
        f"原版基线为 `{baseline.name}`。两套模型使用相同题面数据和同分规则。",
        "几何法逐栋判断三维视线，通过仰角下界缩减搜索空间。二维规划把楼体遮挡投影为凸多边形，"
        "在车心平面直接最小化平方距离，随后在求得的距离水平上最大化共同净空下界。", "",
        "| 指标 | 原版几何法 | 投影约束规划 |", "|---|---:|---:|",
        f"| 站位（m） | ({old['selected_station_xy'][0]:.9f}, {old['selected_station_xy'][1]:.9f}) | ({best['x']:.9f}, {best['y']:.9f}) |",
        f"| 水平距离（m） | {old['horizontal_distance_m']:.12f} | {best['horizontal_distance']:.12f} |",
        f"| 观测质量（m⁻¹） | {old['quality_per_m']:.12f} | {alternate['quality_per_m']:.12f} |",
        f"| 最小净空（m） | {old['minimum_clearance_m']:.10f} | {best['clearance_min']:.10f} |", "",
        f"二维规划使用 {alternate['primary_attempts']} 个独立起点。"
        f"第一阶段有 {alternate['primary_verified_feasible']} 个收敛结果通过三维复核。"
        f"第二阶段有 {alternate['secondary_verified_feasible']}/{alternate['secondary_attempts']} 个通过复核。", "",
        f"同进程各运行 {settings.timing_repeats} 次。当前几何法站位求解中位数为 {median(geometry_seconds):.4f} s，"
        f"二维规划为 {median(projected_seconds):.4f} s。时间包含各自求站的预处理。", "",
        "| 网格间距（m） | LOS 差异数 | 联合可观测差异数 |", "|---:|---:|---:|",
        *(f"| {row['resolution_m']:g} | {row['los_disagreements']} | {row['observable_disagreements']} |" for row in mask_comparison), "",
        "二维规划以多起点局部收敛给出候选。第一问主目标的全局证明来自仰角下界和可行见证点。", "",
        "复跑命令", "", "```bash", replay_command, "```", "",
    ]
    (folder / "comparison.md").write_text("\n".join(lines), encoding="utf-8")
    return folder
