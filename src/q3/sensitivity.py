"""q3 参数敏感性：读取标称题设和相对扰动 YAML，沿用投影正态积分计算两种角度口径。
由 src.run 调用，输出工况底表、图和运行记录到 runs/q3/ 的独立目录，原归档不变。
运行：nohup python3 -B -m src.run q3 --sensitivity [设置 YAML] --config 标称 YAML。
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol（版本发布日期未提供），2026-11-22。
"""

import csv
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import file_digest
from importlib.metadata import version
import json
import math
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile

import yaml

from .config import Config, load_config
from .model import reliability_1d_adaptive, solve_sigma_critical


# a 为 m、b 为 m/m、D 为 m，零位和控制标准差均为度。角度和概率门槛固定为题定值。
PARAMETERS = {
    "initial_sigma_m": "Initial localization SD, $a$",
    "growth": "Localization growth, $b$",
    "range_m": "Horizontal range, $D$",
    "zero_deg": "Gimbal zero-offset SD",
    "control_deg": "Gimbal control SD",
}


def _parameters(cfg: Config) -> dict[str, float]:
    """取出五项标称量，分别使用 m、m/m、m、度、度。"""
    return {
        "initial_sigma_m": cfg.initial_sigma_m, "growth": cfg.growth, "range_m": cfg.model.range_m,
        "zero_deg": cfg.model.zero_deg, "control_deg": cfg.model.control_deg,
    }


def _scenarios(nominal: dict, settings_path: Path):
    """生成标称、单因素和两组联合工况，变化率以相对标称值的小数表示。"""
    settings = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    levels = sorted(map(float, settings["individual_relative_changes"]))
    joint = float(settings["joint_relative_change"])
    if (not levels or len(set(levels)) != len(levels) or not levels[0] < 0 < levels[-1]
            or any(not math.isfinite(x) or x == 0 or abs(x) >= 1 for x in levels) or not 0 < joint < 1):
        raise ValueError("相对扰动须包含互异的正负水平，单因素和联合扰动的绝对幅度须在 (0, 1) 内")
    yield "baseline", "none", 0.0, nominal
    for name in PARAMETERS:
        for level in levels:
            yield "one_at_a_time", name, level, {**nominal, name: nominal[name] * (1 + level)}
    for scenario, level in (("joint_favorable", -joint), ("joint_adverse", joint)):
        # 更大 D 减小几何角误差，因此与四项误差参数反向扰动，不把 D 当作累计里程。
        yield scenario, "all", level, {
            name: value * (1 - level if name == "range_m" else 1 + level) for name, value in nominal.items()
        }


def _evaluate(cfg: Config, settings_path: Path) -> list[dict]:
    """逐工况求每轴临界标准差、最大里程和设计里程概率，均不作小角度近似。"""
    rows, nominal_results = [], {}
    target, design = cfg.minimum_probability, cfg.design_distance_m
    for scenario, factor, level, values in _scenarios(_parameters(cfg), settings_path):
        model = replace(cfg.model, **{name: values[name] for name in ("range_m", "zero_deg", "control_deg")})
        a, b = values["initial_sigma_m"], values["growth"]
        design_sigma = a + design * b
        for mode in ("command", "actual"):
            def probability(sigma_m):
                """按本工况及当前角度口径求概率，输入为每轴标准差（m）。"""
                return reliability_1d_adaptive(sigma_m, model, mode, cfg.validation.quad_tolerance).value

            # 此扫描围绕题设的有限、正里程根设计，若自定义参数已使起点不达标，显式拒绝。
            critical = solve_sigma_critical(probability, target, design_sigma, cfg.validation.root_tolerance_m)
            if critical <= a:
                raise ValueError(f"工况 {scenario}/{factor}/{level:g}（{mode}）不存在正的达标里程")
            maximum, p_design = (critical - a) / b, probability(design_sigma)
            base_s, base_p = nominal_results.setdefault(mode, (maximum, p_design))
            rows.append({
                "scenario": scenario, "factor": factor, "relative_change": level, "mode": mode, **values,
                "angle_threshold_deg": model.threshold_deg, "target_probability": target, "design_travel_m": design,
                "critical_sigma_m": critical, "s_max_m": maximum, "delta_s_max_m": maximum - base_s,
                "probability_at_root": probability(critical), "probability_at_design": p_design,
                "delta_probability_at_design_pp": 100 * (p_design - base_p),
                "b_max_at_design_m_per_m": (critical - a) / design, "meets_design": p_design >= target,
            })
    return rows


def _write_plot(rows: list[dict], directory: Path) -> None:
    """用实际指向的单因素行绘制两个指标；两口径及联合情景均保留在底表中。"""
    import matplotlib as mpl
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.ticker import PercentFormatter

    from src.shared.b_figures import common as style

    actual = [row for row in rows if row["mode"] == "actual"]
    base = actual[0]
    target = base["target_probability"]
    target_label = f"{100 * target:g}%"
    ticks = sorted({0.0, *(100 * row["relative_change"] for row in actual if row["scenario"] == "one_at_a_time")})
    with mpl.rc_context(style.STYLE):
        figure = Figure(figsize=(8.5, 4.15), layout="constrained")
        FigureCanvasAgg(figure)
        axes = figure.subplots(1, 2)
        for ax in axes:
            style.clean_axes(ax, grid="both")
            ax.axvline(0, color=style.RULE, lw=0.7)
            ax.set_xlabel("Change from nominal input (%)")
            ax.set_xticks(ticks)
        axes[0].axhline(0, color=style.RULE, lw=0.7)
        axes[0].set(title=f"{target_label} travel limit", ylabel="Change in travel limit (m)")
        axes[1].set(title=f"Coverage at {base['design_travel_m']:g} m", ylabel="Success probability")
        axes[1].yaxis.set_major_formatter(PercentFormatter(1, decimals=0))
        axes[1].axhline(target, color=style.MUTED, lw=0.9, ls=":")
        # 在线中央标注门槛，避开两端可能刚越过门槛的数据点。
        axes[1].annotate(f"{target_label} requirement", (0.5, target), xycoords=("axes fraction", "data"),
                         xytext=(0, 4), textcoords="offset points", ha="center", va="bottom",
                         color=style.MUTED, fontsize=8)
        colors = ("#747b86", style.BLUE, style.ORANGE, "#2d7464", "#715a98")
        for (factor, label), color, marker in zip(PARAMETERS.items(), colors, ("s", "o", "^", "D", "x")):
            series = sorted([base, *(row for row in actual if row["factor"] == factor)],
                            key=lambda row: row["relative_change"])
            x = [100 * row["relative_change"] for row in series]
            axes[0].plot(x, [row["delta_s_max_m"] for row in series], color=color, marker=marker,
                         lw=1.35, ms=3.5, label=label)
            axes[1].plot(x, [row["probability_at_design"] for row in series], color=color, marker=marker,
                         lw=1.35, ms=3.5)
        figure.suptitle("Actual pointing: one-at-a-time sensitivity", fontsize=13, weight="semibold")
        figure.legend(*axes[0].get_legend_handles_labels(), loc="outside lower center", ncols=3, fontsize=8.5)
        figure.savefig(directory / "sensitivity.pdf", metadata={"CreationDate": None, "ModDate": None})
        figure.savefig(directory / "sensitivity.png", dpi=220)


def run_sensitivity(config_path: Path, settings_path: Path, output_root: Path, profile: str,
                    root: Path, command: str) -> Path:
    """读取两份配置，在 output_root 下新建运行目录，写入底表、图和版本指纹。"""
    rows = _evaluate(load_config(config_path, profile), settings_path)
    output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_")
    directory = Path(tempfile.mkdtemp(prefix=f"sensitivity_{stamp}", dir=output_root))
    shutil.copyfile(config_path, directory / "nominal_config.yaml")
    shutil.copyfile(settings_path, directory / "config.yaml")
    with (directory / "sensitivity.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    _write_plot(rows, directory)

    tracked_sources = ("src/run.py", "src/q3/config.py", "src/q3/model.py", "src/q3/sensitivity.py",
                       "src/shared/b_figures/common.py")
    try:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        head = None  # 导出的支撑材料即使没有 Git，也可按源码和配置指纹复核。
    record = {
        "command": command, "git_head": head, "profile": profile, "method": "projected_normal", "rows": len(rows),
        "python": platform.python_version(),
        "dependencies": {name: version(name) for name in ("numpy", "scipy", "matplotlib", "PyYAML")},
        "source_sha256": {}, "snapshot_sha256": {},
        "ai_assistance": "OpenCode / Magpie group/gpt-6.2-sol, 2026-11-22 (release date not provided)",
    }
    for name in tracked_sources:
        with (root / name).open("rb") as stream:
            record["source_sha256"][name] = file_digest(stream, "sha256").hexdigest()
    for name in ("config.yaml", "nominal_config.yaml", "sensitivity.csv", "sensitivity.pdf", "sensitivity.png"):
        with (directory / name).open("rb") as stream:
            record["snapshot_sha256"][name] = file_digest(stream, "sha256").hexdigest()
    (directory / "run_record.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return directory
