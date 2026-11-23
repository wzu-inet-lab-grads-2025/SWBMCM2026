"""q1 单因素复算：检验最优观测质量、可观测面积、净空站位及固定站可行性。
输入为标称 q1 配置与扰动 YAML，复用 q1 连续求解及独立面积复核，表和图写入 runs/q1/。
运行：nohup python3 -B -m checks.q1_sensitivity（从仓库根目录执行）。
"""

import argparse
import csv
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import file_digest
import json
import math
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile

import matplotlib as mpl
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
import numpy as np
import scipy
import yaml

from checks.q1_area import enclose_observable_area
from src.q1.config import Config, load_config
from src.q1.geometry import compute_clearance, independently_check_station
from src.q1.optimize import OptimumResult, solve_optimum
from src.shared.b_figures import common as style


ROOT = Path(__file__).resolve().parents[1]
BASE_CONFIG = ROOT / "results/q1/可观测区域与最优站位_连续圆弧_20261120/config.yaml"
SCENARIOS = ROOT / "configs/q1_sensitivity.yaml"
FACTORS = {
    "elevation_limit_deg": "Elevation limit",
    "target_height_m": "T1 altitude",
    "target_east_m": "T1 east position",
    "target_north_m": "T1 north position",
    "b2_height_m": "B2 roof height",
    "added_clearance_m": "Added parking clearance",
}


def _load_scenarios(path: Path) -> tuple[float, list[tuple[str, float]]]:
    """读取竖条宽度（m）及单因素改变量（m 或度）；拒绝重复或无效对照。"""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    width = float(raw["max_strip_width_m"])
    if not math.isfinite(width) or width <= 0:
        raise ValueError("面积复核竖条宽度须为有限正数")
    perturbations = raw["perturbations"]
    if not perturbations:
        raise ValueError("至少设一个扰动工况")
    cases = [("nominal", 0.0)]
    for factor, deltas in perturbations.items():
        if factor not in FACTORS or not isinstance(deltas, list) or not deltas:
            raise ValueError(f"未知扰动或幅度列表为空：{factor}")
        values = [float(delta) for delta in deltas]
        if len(set(values)) != len(values) or any(not math.isfinite(d) or d == 0 for d in values):
            raise ValueError(f"扰动幅度须为互异的有限非零数：{factor}")
        if factor == "added_clearance_m" and min(values) < 0:
            raise ValueError("额外车体净空不允许为负")
        cases.extend((factor, delta) for delta in values)
    return width, cases


def _scenario(base: Config, factor: str, delta: float) -> tuple[Config, float]:
    """从标称配置只改一项，返回工况配置及附加停车净空（m）。"""
    tx, ty, height = base.target_xyz
    lower, upper = base.elevation_bounds_deg
    extra = delta if factor == "added_clearance_m" else 0.0
    buildings = base.buildings
    if factor == "elevation_limit_deg":
        upper += delta
    elif factor == "target_height_m":
        height += delta
    elif factor == "target_east_m":
        tx += delta
    elif factor == "target_north_m":
        ty += delta
    elif factor == "b2_height_m":
        if not any(b.id == "B2" for b in buildings):
            raise ValueError("配置中不存在 B2")
        buildings = tuple(replace(b, height=b.height + delta) if b.id == "B2" else b for b in buildings)
    if not 0 < upper < 90 or not all(0 < b.height < height for b in buildings):
        raise ValueError("超出当前仰角或有限楼体投影的适用范围")
    radius = base.vehicle_radius + extra
    if 2 * radius >= min(base.field_width, base.field_height):
        raise ValueError("额外净空使车辆不能停入场地")
    # 固定车体半径；停车约束在几何计算中等价于把半径临时增加 extra。
    # 这不会改变相机位置、LOS、仰角和 Q；报告的实体车身净空仍按原半径算。
    return replace(base, target_xyz=(tx, ty, height), buildings=buildings,
                   elevation_bounds_deg=(lower, upper), vehicle_radius=radius,
                   distance_lower_bound=(height - base.camera_height) / math.tan(math.radians(upper))), extra


def _measure(cfg: Config, base: Config, nominal: OptimumResult, factor: str,
             delta: float, extra: float, strip_width: float) -> dict:
    """复算单场质量、面积、净空及原站判据；无全局取等见证时直接报错。"""
    # 加净空只会缩小可行集。若标称 Q 与净空最优点仍可行，它在子集上仍同时取等；
    # 不需要再求新禁入弧闭切点，也不把切点的浮点舍入误判为无解。
    result = nominal if factor == "nominal" or extra else solve_optimum(cfg)
    site = (result.selected.x, result.selected.y)
    fixed = (nominal.selected.x, nominal.selected.y)
    tests = ("vehicle_ok", "gimbal_ok", "los_clear")
    selected_check = independently_check_station(*site, cfg)
    if not all(selected_check[key] for key in tests):
        raise ValueError("最优候选未通过场界、楼体、仰角和 LOS 的独立复核")
    if abs(selected_check["horizontal_distance_m"] - cfg.distance_lower_bound) > cfg.numerics.distance_check_m:
        raise ValueError("最优候选未达到仰角给出的距离下界")
    # 换回真实车体半径计算墙距；采用本工况场界、楼底，B2 楼高不改变底面。
    clearance, _ = compute_clearance(*site, replace(cfg, vehicle_radius=base.vehicle_radius))
    fixed_check = independently_check_station(*fixed, cfg)
    area = enclose_observable_area(cfg, max_strip_width_m=strip_width)
    _, cap = cfg.elevation_bounds_deg
    return {
        "factor": factor, "perturbation": delta,
        "elevation_limit_deg": cap, "target_x_m": cfg.target_xyz[0], "target_y_m": cfg.target_xyz[1],
        "target_height_m": cfg.target_xyz[2], "b2_height_m": next(b.height for b in cfg.buildings if b.id == "B2"),
        "required_extra_clearance_m": extra, "optimal_r_m": cfg.distance_lower_bound,
        "optimal_q_per_m": 1 / cfg.distance_lower_bound,
        "station_x_m": site[0], "station_y_m": site[1], "station_shift_m": math.dist(site, fixed),
        "station_elevation_deg": selected_check["elevation_deg"], "vehicle_clearance_m": clearance,
        "clearance_above_requirement_m": clearance - extra,
        "clearance_gap_m": result.clearance_gap,
        "area_lower_m2": area.lower_m2, "area_mid_m2": area.midpoint_m2, "area_upper_m2": area.upper_m2,
        "fixed_station_feasible": all(fixed_check[key] for key in tests),
        "fixed_vehicle_ok": fixed_check["vehicle_ok"], "fixed_los_clear": fixed_check["los_clear"],
        "fixed_gimbal_ok": fixed_check["gimbal_ok"], "fixed_elevation_deg": fixed_check["elevation_deg"],
        "fixed_elevation_slack_deg": cap - fixed_check["elevation_deg"],
    }


def _draw(rows: list[dict], output: Path) -> None:
    """用 CSV 同一批数据出图；空心叉标记表示原站失效，不代表重选后无解。"""
    baseline = rows[0]
    with mpl.rc_context(style.STYLE):
        fig = Figure(figsize=(9.6, 4.0), layout="constrained")
        FigureCanvasAgg(fig)
        ax_q, ax_area = fig.subplots(1, 2, gridspec_kw={"width_ratios": (1, 1.45)})
        fig.suptitle("Problem 1 | One-factor sensitivity", x=.05, ha="left", weight="semibold")
        style.clean_axes(ax_q)
        style.clean_axes(ax_area, grid="x")

        angles = sorted([baseline, *(r for r in rows if r["factor"] == "elevation_limit_deg")],
                        key=lambda r: r["elevation_limit_deg"])
        ax_q.plot([r["elevation_limit_deg"] for r in angles], [r["optimal_q_per_m"] for r in angles],
                  color=style.MUTED, lw=1, zorder=2)
        ax_q.axvline(baseline["elevation_limit_deg"], color=style.RULE, lw=.9, ls="--", zorder=1)
        ax_q.set(xticks=[r["elevation_limit_deg"] for r in angles], xlabel="Upper elevation limit (°)",
                 ylabel=r"Reoptimized quality $Q^*$ (m$^{-1}$)", title="(a) Observation quality")

        indices = {factor: pos for pos, factor in enumerate(f for f in FACTORS if any(r["factor"] == f for r in rows))}
        ax_area.axvline(0, color=style.RULE, lw=.9, ls="--", zorder=1)
        for factor, pos in indices.items():
            values = [r["area_change_pct"] for r in rows if r["factor"] == factor]
            ax_area.hlines(pos, min(values), max(values), color=style.MUTED, lw=1, zorder=2)
        ax_area.set(yticks=list(indices.values()), yticklabels=[FACTORS[f] for f in indices],
                    xlabel="Change in observable area (%)", title="(b) Observable area")
        ax_area.invert_yaxis()

        # 颜色和形状同时编码原站是否还符合三约束，保留灰度可读性。
        marks = {True: ("o", style.BLUE), False: ("x", style.ORANGE)}
        for ax, samples, x_key, y_key in (
            (ax_q, angles, "elevation_limit_deg", "optimal_q_per_m"),
            (ax_area, rows[1:], "area_change_pct", None),
        ):
            for r in samples:
                marker, color = marks[r["fixed_station_feasible"]]
                y = r[y_key] if y_key else indices[r["factor"]]
                ax.scatter(r[x_key], y, marker=marker, c=color, s=30, linewidths=1.2, zorder=3)
        fig.legend([Line2D([], [], marker=marker, color=color, linestyle="none", markersize=6)
                    for marker, color in marks.values()],
                   ["Nominal station remains feasible", "Nominal station becomes infeasible"],
                   loc="outside lower center", ncols=2)
        fig.savefig(output / "sensitivity.pdf")
        fig.savefig(output / "sensitivity.png", dpi=220)


def _digest(path: Path) -> str:
    """返回文件 SHA-256，用于追溯输入、计算程序和产物。"""
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def main() -> None:
    """按配置逐场复算，成功后新建运行目录，保存表、图、快照和运行指纹。"""
    parser = argparse.ArgumentParser(description="第一问单因素参数敏感性复算")
    parser.add_argument("--config", type=Path, default=BASE_CONFIG, help="标称场景配置")
    parser.add_argument("--scenarios", type=Path, default=SCENARIOS, help="单因素试算配置")
    args = parser.parse_args()

    base = load_config(args.config)
    strip_width, cases = _load_scenarios(args.scenarios)
    nominal = solve_optimum(base)
    rows = []
    for factor, delta in cases:
        try:
            cfg, extra = _scenario(base, factor, delta)
            rows.append(_measure(cfg, base, nominal, factor, delta, extra, strip_width))
        except ValueError as error:
            raise ValueError(f"{factor} {delta:+g} 工况无法通过既有解析覆盖、选站或几何复核：{error}") from error
    reference = rows[0]
    for row in rows:
        row["quality_change_pct"] = 100 * (row["optimal_q_per_m"] / reference["optimal_q_per_m"] - 1)
        row["area_change_pct"] = 100 * (row["area_mid_m2"] / reference["area_mid_m2"] - 1)
        # 两次连续面积各有包围区间，相减时保留最不利端点，标称自比较严格为 0。
        row["area_change_lower_m2"] = 0.0 if row is reference else row["area_lower_m2"] - reference["area_upper_m2"]
        row["area_change_upper_m2"] = 0.0 if row is reference else row["area_upper_m2"] - reference["area_lower_m2"]

    output_root = ROOT / "runs/q1"
    output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = Path(tempfile.mkdtemp(prefix=f"{stamp}_参数敏感性_", dir=output_root))
    shutil.copyfile(args.config, output / "config.yaml")
    shutil.copyfile(args.scenarios, output / "sensitivity_config.yaml")
    with (output / "sensitivity.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(reference))
        writer.writeheader()
        writer.writerows(rows)
    _draw(rows, output)
    inputs = ("config.yaml", "sensitivity_config.yaml")
    sources = ("checks/q1_sensitivity.py", "checks/q1_area.py", "src/q1/config.py", "src/q1/geometry.py",
               "src/q1/optimize.py", "src/q1/region.py", "src/shared/b_figures/common.py")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    profile = {
        "run_id": output.name, "git_head": head.stdout.strip() if head.returncode == 0 else None,
        "command": shlex.join(["nohup", *sys.orig_argv]), "scenario_count_including_nominal": len(rows),
        "env": {"python": platform.python_version(), "numpy": np.__version__, "scipy": scipy.__version__,
                "matplotlib": mpl.__version__, "pyyaml": yaml.__version__},
        "source_sha256": {name: _digest(ROOT / name) for name in sources},
        "input_sha256": {name: _digest(output / name) for name in inputs},
        "result_sha256": {name: _digest(output / name) for name in ("sensitivity.csv", "sensitivity.pdf", "sensitivity.png")},
    }
    (output / "provenance.json").write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
