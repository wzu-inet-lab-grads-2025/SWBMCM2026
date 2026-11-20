"""q3 独立半平面复核，直接用两条边界的高斯边际和后半平面尾界。
输入为 runs/q3 的 summary.json，输出根值、概率差及尾部包围；不引用求解器。
运行 nohup python3 -B -m checks.q3_reference 运行目录 --output 复核JSON路径。
AI 辅助 OpenCode（Anomaly）与 Magpie gpt-6.2-sol（OpenAI，版本发布日期未公开），2026-11-20。
"""

import argparse
import json
import math
from pathlib import Path

from scipy.integrate import quad
from scipy.optimize import brentq
from scipy.special import ndtr


def half_plane_probability(sigma_m: float, range_m: float, threshold_deg: float,
                           gimbal_deg: float) -> tuple[float, float, float]:
    """返回楔形概率的下侧估计、遗漏尾界及积分误差估计，标准差为 m 或度。"""
    # 给定云台误差 e，成功是两个半平面的交，边界角为 -θ-e 与 θ-e。
    # 两条单位法向的高斯边际之和减一是下界，漏掉的补集之交位于反向楔形。
    # 若 |e|≤90°-θ，反向楔形完全在 X≤0 中，故漏项不超过 Φ(-D/σ)。
    normal_scale = range_m / (sigma_m * math.sqrt(2.0))
    backward_tail = float(ndtr(-range_m / sigma_m))

    def marginal_difference(angle_deg):
        """对称法向叠加所得的边际差，正负云台角的贡献互为镜像。"""
        return math.erf(normal_scale * math.sin(math.radians(angle_deg)))

    if gimbal_deg == 0.0:
        return marginal_difference(threshold_deg), backward_tail, 0.0

    cutoff = min(12.0, (90.0 - threshold_deg) / gimbal_deg)
    # 对称云台分布使两个边际可合为一个 erf 期望，窗口外直接用标准正态尾界。
    value, error = quad(
        lambda z: marginal_difference(threshold_deg + gimbal_deg * z)
        * math.exp(-0.5 * z * z) / math.sqrt(math.tau),
        -cutoff, cutoff, epsabs=3e-14, epsrel=3e-14,
    )
    return value, backward_tail + 2.0 * float(ndtr(-cutoff)), error


def verify_run(directory: Path) -> dict:
    """用独立几何复核输出根、边界前后、起点及 80 m 概率，返回验收记录。"""
    with (directory / "summary.json").open(encoding="utf-8") as handle:
        summary = json.load(handle)
    inputs = summary["inputs"]
    modes = {}
    for mode, limit in summary["limits"].items():
        gimbal = math.hypot(inputs["zero_deg"], inputs["control_deg"]) if mode == "actual" else 0.0

        def reference(sigma_m):
            """取指定口径的独立概率估计与误差界。"""
            return half_plane_probability(sigma_m, inputs["range_m"], inputs["threshold_deg"], gimbal)

        target = inputs["minimum_probability"]
        sigma_m = limit["sigma_m"]
        # 以两倍根值设置宽括区，另用半平面公式独立求根。
        independently_solved_m = brentq(lambda sigma: reference(sigma)[0] - target,
                                         0.25 * sigma_m, 2.0 * sigma_m, xtol=1e-12)
        rows = []
        for row in (item for item in summary["sites"] if item["mode"] == mode):
            if row["sigma_m"] == 0.0:
                continue
            estimate, tail_bound, error = reference(row["sigma_m"])
            difference = row["probability"] - estimate
            rows.append({
                **row, "reference_probability": estimate, "omitted_mass_upper_bound": tail_bound,
                "reference_quadrature_error": error, "difference": difference,
                "passed": -1e-10 <= difference <= tail_bound + 1e-10,
            })
        root_delta = abs(sigma_m - independently_solved_m)
        modes[mode] = {
            "sigma_m": independently_solved_m,
            "travel_m": (independently_solved_m - inputs["initial_sigma_m"]) / inputs["growth"],
            "sigma_abs_difference_m": root_delta, "sites": rows,
            "passed": root_delta <= 1e-8 and all(row["passed"] for row in rows),
        }
    return {"passed": all(mode["passed"] for mode in modes.values()), "modes": modes}


def main() -> int:
    """读取一次配置和结果快照，不改动被核对的求解输出。"""
    parser = argparse.ArgumentParser(description="用高斯半平面独立复核 B 题第三问")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify_run(args.directory)
    output = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    for name, mode in result["modes"].items():
        print(f"{name}: 半平面复核 {'通过' if mode['passed'] else '失败'}，"
              f"σ*={mode['sigma_m']:.12f} m，根差={mode['sigma_abs_difference_m']:.3g} m")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
