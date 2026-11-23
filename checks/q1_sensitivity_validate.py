"""q1 敏感性逐场复核：读取运行或归档的选站底表，不调用选站求解器。
用有理数闭盒和停车距离检查两套站，用三维网格核对面积变化方向，输出 JSON 到标准输出。
运行：nohup python3 -B -m checks.q1_sensitivity_validate --run <q1 敏感性目录>。
"""

import argparse
import csv
from dataclasses import replace
import json
import math
from pathlib import Path

from checks.q2_geometry import CheckError, Scene, check_route, station_pointing
from src.q1.config import load_config
from src.q1.region import build_observable_region


def validate(run: Path) -> dict:
    """返回逐场验收与 0.0625 m 网格对照；几何、指标或变化方向不符时失败。"""
    base = load_config(run / "config.yaml")
    meta = json.loads((run / "provenance.json").read_text(encoding="utf-8"))
    with (run / "sensitivity.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != meta["scenario_count_including_nominal"] or rows[0]["factor"] != "nominal":
        raise ValueError("底表缺少标称行或工况数不符")
    fixed = (float(rows[0]["station_x_m"]), float(rows[0]["station_y_m"]))
    checked = []
    for index, row in enumerate(rows):
        factor, delta = row["factor"], float(row["perturbation"])
        target = tuple(float(row[k]) for k in ("target_x_m", "target_y_m", "target_height_m"))
        site = (float(row["station_x_m"]), float(row["station_y_m"]))
        elevation = (base.elevation_bounds_deg[0], float(row["elevation_limit_deg"]))
        extra = float(row["required_extra_clearance_m"])
        buildings = tuple(replace(b, height=float(row["b2_height_m"])) if b.id == "B2" else b for b in base.buildings)
        scene = Scene(base.field_width, base.field_height, base.vehicle_radius, base.camera_height, elevation,
                      buildings, {}, {"T1": target})
        seat = check_route(scene, [site], site, wall_clearance_m=extra)  # 只验停车，不把它冒充实际到站路线。
        pointing = station_pointing(scene, site, target)
        clearance = min(site[0], base.field_width - site[0], site[1], base.field_height - site[1],
                        *seat["min_distance_to_buildings_m"].values()) - base.vehicle_radius
        values = {"optimal_r_m": pointing["range_m"], "optimal_q_per_m": 1 / pointing["range_m"],
                  "station_elevation_deg": elevation[1], "vehicle_clearance_m": clearance}
        if any(not math.isclose(value, float(row[key]), rel_tol=0, abs_tol=1e-10) for key, value in values.items()):
            raise ValueError(f"{factor} {delta:+g}：选站指标复算不符")
        if not 0 <= float(row["clearance_gap_m"]) <= base.numerics.clearance_gap_m:
            raise ValueError(f"{factor} {delta:+g}：净空择优上界差不合格")

        # 单因素面板中原站的车体和 LOS 均通过，只有仰角上限可导致失败。
        check_route(scene, [fixed], fixed, wall_clearance_m=extra)
        fixed_pitch = station_pointing(replace(scene, elevation_deg=(-90.0, 90.0)), fixed, target)["pitch_deg"]
        fixed_ok = elevation[0] <= fixed_pitch <= elevation[1]
        if fixed_ok != (row["fixed_station_feasible"] == "True"):
            raise ValueError(f"{factor} {delta:+g}：原站可行性复算不符")

        # 面积另用三维 LOS 的单元中心掩码，只检验增减方向，不以离散值替代连续区间。
        cfg = replace(base, target_xyz=target, buildings=buildings, vehicle_radius=base.vehicle_radius + extra,
                      elevation_bounds_deg=elevation,
                      distance_lower_bound=(target[2] - base.camera_height) / math.tan(math.radians(elevation[1])))
        grid_area = build_observable_region(cfg, 0.0625).estimated_area
        if index:
            grid_delta = grid_area - checked[0]["grid_area_m2"]
            if ((grid_delta > 0) != (float(row["area_change_lower_m2"]) > 0)
                    or (grid_delta < 0) != (float(row["area_change_upper_m2"]) < 0)):
                raise ValueError(f"{factor} {delta:+g}：独立网格与连续面积的变化方向不符")
        checked.append({"factor": factor, "perturbation": delta, "reoptimized_station_valid": True,
                        "fixed_station_valid": fixed_ok, "fixed_station_failed_constraint": "" if fixed_ok else "gimbal_upper",
                        "grid_step_m": 0.0625, "grid_area_m2": grid_area,
                        "grid_minus_continuous_mid_m2": grid_area - float(row["area_mid_m2"])})
    return {
        "archive_source_run": meta["run_id"], "cases": len(checked),
        "fixed_station_invalid": sum(not row["fixed_station_valid"] for row in checked),
        "grid_change_sign_agrees_in_all_perturbations": True,
        "max_absolute_grid_area_error_m2": max(abs(row["grid_minus_continuous_mid_m2"]) for row in checked),
        "cases_checked": checked,
    }


def main() -> None:
    """读取一个结果目录并输出 JSON；留存报告时由调用者重定向到本地运行目录。"""
    parser = argparse.ArgumentParser(description="独立复核第一问敏感性底表")
    parser.add_argument("--run", required=True, type=Path, help="敏感性运行或归档目录")
    args = parser.parse_args()
    print(json.dumps(validate(args.run), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
