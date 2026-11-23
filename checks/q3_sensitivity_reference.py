"""q3 敏感性独立验收：读取运行目录的两份配置和 CSV，用高斯半平面复算每个工况。
不调用投影正态求解器；输出参数覆盖、根及概率差，并核对原归档的标称结果。
运行：nohup python3 -B -m checks.q3_sensitivity_reference 运行目录 [--output 复核JSON路径]。
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol（版本发布日期未提供），2026-11-22。
"""

import argparse
import csv
import json
import math
from pathlib import Path

from scipy.optimize import brentq
import yaml

from checks.q3_reference import half_plane_probability


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "results/q3/测向可靠性与80米定位约束_五方法对照_20261120/limits.csv"
INPUTS = ("initial_sigma_m", "growth", "range_m", "zero_deg", "control_deg")
MODES = ("command", "actual")


def _read_rows(path: Path) -> list[dict]:
    """读取敏感性或临界值 CSV，保留原字段字符串供逐项校验。"""
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def verify(directory: Path, baseline_csv: Path = BASELINE) -> dict:
    """核对每个角度口径的单项及联合变动、两类指标及报表换算，返回验收明细。"""
    given = yaml.safe_load((directory / "nominal_config.yaml").read_text(encoding="utf-8"))["model"]
    settings = yaml.safe_load((directory / "config.yaml").read_text(encoding="utf-8"))
    file_rows = _read_rows(directory / "sensitivity.csv")
    rows = {(r["scenario"], r["factor"], float(r["relative_change"]), r["mode"]): r for r in file_rows}
    nominal = {r["mode"]: r for r in _read_rows(baseline_csv)}
    theta, target, design = (given[name] for name in ("threshold_deg", "minimum_probability", "design_distance_m"))
    joint = settings["joint_relative_change"]
    design_cases = {("baseline", "none", 0.0), ("joint_favorable", "all", -joint), ("joint_adverse", "all", joint)}
    design_cases.update(("one_at_a_time", factor, change) for factor in INPUTS
                        for change in settings["individual_relative_changes"])
    expected_keys = {(*case, mode) for case in design_cases for mode in MODES}
    if len(rows) != len(file_rows) or rows.keys() != expected_keys:
        raise ValueError("敏感性 CSV 的工况有遗漏、重复或未声明的变体")

    sigma_diffs, travel_diffs, coverage_diffs, tails, integration_errors = [], [], [], [], []
    threshold_confirmed = 0
    for (scenario, factor, level, mode), row in rows.items():
        metrics = {name: float(row[name]) for name in (*INPUTS, "angle_threshold_deg", "target_probability",
                   "design_travel_m", "critical_sigma_m", "s_max_m", "probability_at_root", "probability_at_design",
                   "delta_s_max_m", "delta_probability_at_design_pp", "b_max_at_design_m_per_m")}
        if not all(map(math.isfinite, metrics.values())):
            raise ValueError(f"{scenario}/{factor}/{level:g}/{mode} 有非有限数值")
        for name in INPUTS:
            change = level if factor == name or factor == "all" else 0.0
            if factor == "all" and name == "range_m":
                change = -change
            if not math.isclose(metrics[name], given[name] + given[name] * change, rel_tol=1e-14, abs_tol=0):
                raise ValueError(f"{scenario}/{factor}/{level:g} 的参数 {name} 与扫描定义不符")
        if (metrics["angle_threshold_deg"], metrics["target_probability"], metrics["design_travel_m"]) != (theta, target, design):
            raise ValueError("角度、概率门槛及设计里程不得随工况变化")

        a, b, distance, zero, control = (metrics[name] for name in INPUTS)
        sigma = metrics["critical_sigma_m"]
        gimbal = math.hypot(zero, control) if mode == "actual" else 0.0

        def reference(per_axis_m):
            """用独立高斯边际计算概率、遗漏质量界和积分误差估计。"""
            return half_plane_probability(per_axis_m, distance, theta, gimbal)

        # 报告根仅用于提供宽括区，参考函数不读取报告概率，也不计算投影角密度。
        solved = brentq(lambda s: reference(s)[0] - target, 0.25 * sigma, 2.0 * sigma, xtol=1e-12)
        estimate, at_design_tail, at_design_error = reference(a + design * b)
        at_root, at_root_tail, at_root_error = reference(sigma)
        sigma_diffs.append(abs(solved - sigma))
        travel_diffs.append(abs((solved - a) / b - metrics["s_max_m"]))
        coverage_diffs.append(abs(estimate - metrics["probability_at_design"]))
        tails.extend((at_root_tail, at_design_tail))
        integration_errors.extend((at_root_error, at_design_error))

        base = rows[("baseline", "none", 0.0, mode)]
        p = metrics["probability_at_design"]
        # 距阈值超过独立比较容差才给独立达标判别；数值上正好在门槛的点只比概率。
        if abs(estimate - target) > at_design_tail + 1e-10:
            if row["meets_design"] != str(estimate >= target):
                raise ValueError(f"{scenario}/{factor}/{level:g}/{mode} 的达标判定与独立复算不符")
            threshold_confirmed += 1
        if (abs(at_root - target) > 1e-10 or abs(metrics["probability_at_root"] - target) > 1e-10
                or row["meets_design"] != str(p >= target)
                or abs(metrics["b_max_at_design_m_per_m"] - (solved - a) / design) > 1e-10
                or abs(metrics["delta_s_max_m"] - (metrics["s_max_m"] - float(base["s_max_m"]))) > 1e-10
                or abs(metrics["delta_probability_at_design_pp"] - 100 * (p - float(base["probability_at_design"]))) > 1e-10):
            raise ValueError(f"{scenario}/{factor}/{level:g}/{mode} 的临界点、达标或差值未通过复算")
        if scenario == "baseline" and (
            abs(sigma - float(nominal[mode]["sigma_m"])) > 1e-10
            or abs(metrics["s_max_m"] - float(nominal[mode]["travel_m"])) > 1e-8
            or abs(p - float(nominal[mode]["probability_at_design"])) > 1e-10
        ):
            raise ValueError(f"{mode} 的标称口径未复现原归档")

    passed = (max(sigma_diffs) <= 1e-9 and max(travel_diffs) <= 1e-7 and max(coverage_diffs) <= 1e-10
              and max(tails) <= 1e-10 and max(integration_errors) <= 1e-10)
    return {"passed": passed, "physical_scenarios": len(design_cases), "rows_checked": len(rows),
            "design_classifications_confirmed": threshold_confirmed,
            "maximum_root_difference_m": max(sigma_diffs), "maximum_travel_limit_difference_m": max(travel_diffs),
            "maximum_design_probability_difference": max(coverage_diffs),
            "maximum_omitted_mass_bound": max(tails), "maximum_reference_quadrature_error_estimate": max(integration_errors)}


def main() -> int:
    """复核给定目录，并将 JSON 打印到终端或写入指定路径。"""
    parser = argparse.ArgumentParser(description="独立复核 B 题第三问参数敏感性")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--baseline", type=Path, default=BASELINE, help="原标称 limits.csv")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify(args.directory, args.baseline)
    text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
