"""B 题第二问补充敏感性：固定归档站位与路线，扫描测角噪声、计时及 T3 高度。
输入 configs/q2_sensitivity.yaml 和毫米方案归档，输出新建 runs/q2/ 目录的底表、英文图及运行记录。
运行：nohup env OPENBLAS_NUM_THREADS=1 MPLBACKEND=Agg python3 -B -m src.q2.sensitivity
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol，2026-11-22；供队内复核。
"""

import argparse
import csv
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from itertools import product
import json
import math
from pathlib import Path
import shlex
import sys
import tempfile
import time

import yaml

from checks.q2_geometry import CheckError, Scene, station_pointing
from checks.q2_validate import compare_plans, verify_q21, verify_q22

from .experiment import _metadata
from .report import write_csv, write_json
from .scene import Scene as PlanningScene, read_scene


ROOT = Path(__file__).resolve().parents[2]
PLANS = ("practical", "greedy")


def _verification_scene(nominal: PlanningScene) -> Scene:
    """用归档的场地、起点、告警和题定参数构造独立验收场景（m、s、度）。"""
    field = nominal.field
    if nominal.q21_travel_limit_s != 30:
        raise ValueError("2（1）独立验收只支持题定的 30 s 行驶限时")
    return Scene(field.field_width, field.field_height, field.vehicle_radius, field.camera_height,
                 field.elevation_bounds_deg, field.buildings,
                 dict(zip(nominal.vehicles, map(tuple, nominal.origins.tolist()))),
                 {name: config.target_xyz for name, config in zip(nominal.targets, nominal.target_cfg)},
                 speed_m_s=nominal.speed_m_s, gimbal_speed_deg_s=nominal.gimbal_deg_s,
                 observation_s=nominal.observation_s, bearing_sigma_deg=nominal.bearing_sigma_deg)


def _levels(values, nominal, *, positive=False):
    """读取有序扫描档位；要求有限、唯一且包含题定对照点。"""
    if (not isinstance(values, list) or len(values) < 2
            or any(type(v) not in (float, int) or not math.isfinite(v) or (positive and v <= 0) for v in values)
            or len(set(values)) != len(values) or nominal not in values):
        raise ValueError(f"扫描档位须为唯一有限数值，至少两档且包含标称值 {nominal}")
    return tuple(sorted(values))


def _noise_rows(scene, reference, sigmas, audit):
    """同一理论或部署构型下改变独立测角标准差（度），复算一阶误差（m）。"""
    rows = []
    for name, filename in (("theoretical", "q21_result.json"), ("practical", "q21_practical.json")):
        plan = json.loads((reference / filename).read_text(encoding="utf-8"))
        for sigma in sigmas:
            checked = verify_q21(replace(scene, bearing_sigma_deg=sigma), plan,
                                 waypoint_decimals=plan.get("coordinate_decimals"), **audit)
            # 改变测角噪声不改变站距、可见域或路线，仰角和 30 s 限时仍照常验收。
            rows.append({"plan": name, "bearing_sigma_deg": sigma, "rmse_linear_m": checked["rmse_m"],
                         "angle_deg": checked["angle_deg"], "baseline_m": checked["baseline_m"],
                         "max_travel_s": max(leg["travel_s"] for leg in checked["routes"]),
                         "min_pitch_margin_deg": scene.elevation_deg[1] - max(leg["pitch_deg"] for leg in checked["routes"])})
            if sigma == scene.bearing_sigma_deg and not math.isclose(
                    checked["rmse_m"], plan["rmse_m"], abs_tol=1e-12, rel_tol=0):
                raise ValueError(f"{name} 的标称精度与原归档不一致")
    return rows


def _retime(scene, nominal):
    """在原方案的无环任务依赖上重放，返回逐车事件和阶段时刻（s）。"""
    timeline, release = [], dict.fromkeys(scene.vehicles, 0.0)
    legs = nominal["legs"]
    successors = {(leg["vehicle"], leg["previous_target"]) for leg in legs}
    # 标称验收器的开测字典按依赖拓扑序写入，而非按开测时刻排序。
    for target in nominal["observation_start_s"]:
        pair = []
        for leg in (r for r in legs if r["target"] == target):
            vehicle = leg["vehicle"]
            try:
                slew = station_pointing(scene, leg["site"], scene.targets[target])["slew_s"]
            except CheckError as error:
                raise CheckError(f"{vehicle}/{target}：{error}") from error
            depart = release[vehicle]
            arrive = depart + leg["length_m"] / scene.speed_m_s
            pair.append((vehicle, depart, arrive, slew))
        start = max(arrive + slew for _, _, arrive, slew in pair)
        end = start + scene.observation_s
        for vehicle, depart, arrive, slew in pair:
            ready, reset_end = arrive + slew, end + slew
            release[vehicle] = reset_end
            timeline.append({"vehicle": vehicle, "target": target, "depart_s": depart, "arrive_s": arrive,
                             "turn_begin_s": arrive, "turn_end_s": ready, "wait_s": start - ready,
                             "observation_start_s": start, "observation_end_s": end, "reset_begin_s": end,
                             "reset_end_s": reset_end,
                             "next_departure_s": reset_end if (vehicle, target) in successors else None})
    timeline.extend({"vehicle": vehicle, "target": "idle"} for vehicle in nominal["idle_vehicles"])
    return timeline


def _dispatch_rows(scene, reference, factors, heights, decimals, audit):
    """按工况逐案验收两套固定排程；遮挡或冲突时保留原因，不用原开测时间顶替。"""
    routes = json.loads((reference / "q22_routes.json").read_text(encoding="utf-8"))
    with (reference / "q22_timeline.csv").open(newline="", encoding="utf-8-sig") as file:
        times = list(csv.DictReader(file))
    nominal, fixed = {}, {}
    # 就近基线的站位均为题给整米初始点，两方案可按同一下发精度和几何约束验收。
    for plan in PLANS:
        route_set = [r for r in routes if r["plan"] == plan]
        nominal[plan] = verify_q22(scene, route_set, [r for r in times if r["plan"] == plan],
                                   waypoint_decimals=decimals, **audit)
        # 原 travel_s 只适用于 3 m/s；其余路点和长度字段继续独立复核。
        fixed[plan] = [{k: v for k, v in r.items() if k != "travel_s"} for r in route_set]

    cases = [("timing", *rates, 0.0) for rates in product(factors, repeat=3)]
    cases.extend(("t3_height", 1.0, 1.0, 1.0, offset) for offset in heights if offset != 0)
    rows = []
    for group, speed, slew, duration, offset in cases:
        x, y, h = scene.targets["T3"]
        case = replace(scene, speed_m_s=scene.speed_m_s * speed, gimbal_speed_deg_s=scene.gimbal_speed_deg_s * slew,
                       observation_s=scene.observation_s * duration, targets={**scene.targets, "T3": (x, y, h + offset)})
        row = {"case": group, "speed_factor": speed, "gimbal_factor": slew, "observation_factor": duration,
               "vehicle_speed_m_s": case.speed_m_s, "gimbal_speed_deg_s": case.gimbal_speed_deg_s,
               "observation_s": case.observation_s, "t3_height_offset_m": offset, "t3_height_m": h + offset}
        verified = {}
        for plan in PLANS:
            try:
                timeline = _retime(case, nominal[plan])
                verified[plan] = verify_q22(case, fixed[plan], timeline, waypoint_decimals=decimals, **audit)
            except CheckError as error:
                row.update({f"{plan}_feasible": False, f"{plan}_failure_reason": str(error)})
                continue
            result = verified[plan]
            starts = result["observation_start_s"]
            latest = result["latest_start_s"]
            row.update({f"{plan}_feasible": True, f"{plan}_latest_start_s": latest,
                        f"{plan}_last_reset_end_s": result["last_reset_end_s"],
                        f"{plan}_min_center_distance_m": min(p["distance_m"] for p in
                                                            result["inter_vehicle_separation"]["pair_minima"]),
                        f"{plan}_latest_alert": "/".join(sorted(t for t, start in starts.items() if start == latest)),
                        **{f"{plan}_{t}_start_s": start for t, start in starts.items()}})
            for alert, start in starts.items():
                row[f"{plan}_{alert}_ready_last"] = "/".join(sorted(
                    leg["vehicle"] for leg in result["legs"] if leg["target"] == alert and leg["turn_end_s"] == start))
        if len(verified) == len(PLANS):
            row["greedy_minus_practical_s"] = compare_plans(verified["greedy"], verified["practical"])["latest_start_saved_s"]
        if (speed, slew, duration, offset) == (1, 1, 1, 0) and any(
                verified.get(plan, {}).get("observation_start_s") != nominal[plan]["observation_start_s"] for plan in PLANS):
            raise ValueError("标称工况未复现冻结的开测时间")
        rows.append(row)
    return rows


def _plots(directory, noise, timing, factors, nominal_sigma_deg):
    """从验收底表绘制英文简图，保留 PDF 与 PNG；无效情景不画成零秒。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    blue, orange, green = "#245782", "#BD591E", "#227364"
    plt.rcParams.update({"axes.spines.top": False, "axes.spines.right": False, "pdf.fonttype": 42})

    def save(fig, name):
        for ext in ("png", "pdf"):
            fig.savefig(directory / f"{name}.{ext}", dpi=220, bbox_inches="tight", pad_inches=0.1)
        plt.close(fig)

    fig, ax = plt.subplots(figsize=(5.2, 3.2), layout="constrained")
    for plan, label, color, style in (("theoretical", "Exact geometry", blue, "o-"),
                                      ("practical", "Millimetre deployment", orange, "s--")):
        rows = [r for r in noise if r["plan"] == plan]
        ax.plot([r["bearing_sigma_deg"] for r in rows], [r["rmse_linear_m"] for r in rows], style,
                color=color, markersize=4, label=label)
    ax.axvline(nominal_sigma_deg, color="#6E7785", ls=":", lw=1)
    ax.set(xlabel="Bearing-error standard deviation (deg)", ylabel="First-order horizontal RMSE (m)")
    ax.legend(frameon=False, loc="upper left")
    ax.grid(axis="y", alpha=0.25)
    save(fig, "q21_noise_sensitivity")

    grid = {(r["speed_factor"], r["gimbal_factor"], r["observation_factor"]): r
            for r in timing if r["case"] == "timing"}
    base = grid[1, 1, 1]["practical_latest_start_s"]
    fig, (left, right) = plt.subplots(1, 2, figsize=(9.4, 3.7), layout="constrained")
    for i, (label, color, style) in enumerate((("Vehicle speed", blue, "o-"), ("Gimbal rate", orange, "s-"),
                                               ("Observation duration", green, "^-"))):
        readings = []
        for factor in factors:
            cell = [1, 1, 1]
            cell[i] = factor
            readings.append(grid[tuple(cell)].get("practical_latest_start_s", math.nan) - base)
        left.plot([100 * f for f in factors], readings, style, color=color, label=label, markersize=4)
    left.axhline(0, color="#6E7785", lw=0.8)
    left.set(title="(a) One factor at a time", xlabel="Parameter (% of nominal)", ylabel="Change in latest start (s)")
    left.grid(alpha=0.22)
    left.legend(frameon=False, fontsize=8, loc="best")

    diff = np.array([[grid[v, w, 1].get("greedy_minus_practical_s", math.nan) for w in factors] for v in factors])
    scale = max(0.01, float(np.max(np.abs(diff[np.isfinite(diff)]), initial=0)))
    shaded = right.imshow(diff, origin="lower", cmap="RdBu", vmin=-scale, vmax=scale)
    ticklabels = [f"{f * 100:g}" for f in factors]
    nominal_duration = grid[1, 1, 1]["observation_s"]
    right.set(xticks=range(len(factors)), yticks=range(len(factors)), xticklabels=ticklabels, yticklabels=ticklabels,
              title=f"(b) Baseline minus practical, {nominal_duration:g} s observation", xlabel="Gimbal rate (% of nominal)",
              ylabel="Vehicle speed (% of nominal)")
    for (i, j), value in np.ndenumerate(diff):
        right.text(j, i, f"{value:+.3f}" if math.isfinite(value) else "n/a", ha="center", va="center",
                   fontsize=7, color="white" if abs(value) > 0.62 * scale else "#17202B")
    fig.colorbar(shaded, ax=right, label="Latest-start saving (s)", shrink=0.86)
    save(fig, "q22_timing_sensitivity")


def main(argv=None):
    """从冻结归档计算两类分析，打印新的本地运行目录，不改动归档。"""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=ROOT / "configs/q2_sensitivity.yaml", help="敏感性试验配置 YAML")
    arguments = parser.parse_args(argv)
    began = time.perf_counter()
    config = arguments.config.read_bytes()
    settings = yaml.safe_load(config)
    reference = ROOT / settings["reference_archive"]
    refinement_file = reference / "refinement_config.yaml"
    refinement = yaml.safe_load(refinement_file.read_bytes())
    nominal = read_scene(reference / "config.yaml")
    scene = _verification_scene(nominal)
    sigmas = _levels(settings["bearing_sigma_deg"], scene.bearing_sigma_deg, positive=True)
    factors = _levels(settings["timing_factors"], 1.0, positive=True)
    heights = _levels(settings["t3_height_offsets_m"], 0.0)
    audit = {"require_vehicle_separation": True, "wall_clearance_m": refinement["wall_clearance_m"]}
    noise = _noise_rows(scene, reference, sigmas, audit)
    timing = _dispatch_rows(scene, reference, factors, heights, refinement["practical_decimals"], audit)

    output = ROOT / "runs/q2"
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_sensitivity_")
    directory = Path(tempfile.mkdtemp(dir=output, prefix=stamp))
    (directory / "config.yaml").write_bytes(config)
    write_csv(directory / "q21_noise_sensitivity.csv", noise)
    write_csv(directory / "q22_sensitivity.csv", timing)
    _plots(directory, noise, timing, factors, scene.bearing_sigma_deg)
    command = shlex.join(sys.orig_argv if argv is None else [sys.executable, "-m", "src.q2.sensitivity", *argv])
    record = _metadata(ROOT, command, "q2_sensitivity", time.perf_counter() - began)
    record["ai_assistance"] = "OpenCode / Magpie group/gpt-6.2-sol, 2026-11-22. 供队内独立核对。"
    record["input_sha256"] = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in (
        reference / "config.yaml", nominal.field_file, refinement_file,
        *(reference / filename for filename in ("q21_result.json", "q21_practical.json", "q22_routes.json", "q22_timeline.csv")),
    )}
    record["input_sha256"]["sensitivity_config"] = hashlib.sha256(config).hexdigest()
    for source in ("checks/q2_geometry.py", "checks/q2_validate.py"):
        record["source_sha256"][source] = hashlib.sha256((ROOT / source).read_bytes()).hexdigest()
    record["verification"] = {"wall_clearance_m": audit["wall_clearance_m"], "center_distance_m": 2 * scene.radius_m,
                              "q21_cases": len(noise), "q22_waypoint_decimals": refinement["practical_decimals"],
                              "q22_case_counts": {kind: {plan: {"passed": sum(r[f"{plan}_feasible"] for r in timing if r["case"] == kind),
                                                              "total": sum(r["case"] == kind for r in timing)}
                                                         for plan in PLANS} for kind in ("timing", "t3_height")}}
    write_json(directory / "run_record.json", record)
    print(directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
