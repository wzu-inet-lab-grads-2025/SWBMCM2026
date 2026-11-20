"""q3 论文图，读取已验收的概率、收敛、参数边界及误差贡献底表。
供运行器输出四张英文图，各存 PDF 矢量和 PNG 预览于同一运行目录。
生成图表 nohup python3 -B -m src.run q3 --profile full。
AI 辅助 OpenCode（Anomaly）与 Magpie gpt-6.2-sol（OpenAI，版本发布日期未公开），2026-11-20。
"""

from pathlib import Path

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, PercentFormatter
import numpy as np


ACTUAL = "#165c56"
COMMAND = "#345f9e"
THRESHOLD = "#ad5035"
MUTED = "#606b78"
SOURCES = (("localization", "Localization", ACTUAL), ("zero", "Zero offset", "#bf7939"),
           ("control", "Control", COMMAND))


def _save(figure: Figure, directory: Path, stem: str) -> None:
    """无图形桌面输出保留矢量图和 180 dpi 预览。"""
    FigureCanvasAgg(figure)
    figure.savefig(directory / f"{stem}.pdf")
    figure.savefig(directory / f"{stem}.png", dpi=180)
    figure.clear()


def _style(axis) -> None:
    """减少辅助线，把深色留给实际数据。"""
    axis.spines[["top", "right"]].set_visible(False)
    axis.spines[["bottom", "left"]].set_color("#b5bfc6")
    axis.set_axisbelow(True)
    axis.grid(axis="y", color="#dfe5e8", linewidth=0.7)
    axis.tick_params(labelsize=9, color="#b5bfc6")


def _reliability(result: dict, directory: Path) -> None:
    """由一维积分底表生成概率–累计里程图，标记两个根和设计里程。"""
    summary, curve = result["summary"], result["curve"]
    inputs = summary["inputs"]
    cap, target = inputs["design_distance_m"], inputs["minimum_probability"]
    fig = Figure(figsize=(7.2, 4.1), layout="constrained")
    ax = fig.subplots()
    _style(ax)
    ax.set_title("Horizontal bearing reliability versus cumulative travel", loc="left", weight="bold")
    ax.axhline(target, color=THRESHOLD, linestyle="--", lw=1.1, label=f"{target:.0%} requirement")
    ax.axvline(cap, color=MUTED, linestyle=":", linewidth=1.0)
    for mode, label, color in (("actual", "Actual pointing", ACTUAL), ("command", "Commanded bearing", COMMAND)):
        ax.plot([row["distance_m"] for row in curve], [row[mode] for row in curve], label=label, lw=2, color=color)
        maximum = summary["limits"][mode]["travel_m"]
        if maximum is not None:
            ax.axvline(maximum, color=color, linestyle=":", linewidth=0.9)
            ax.scatter(maximum, target, color=color, s=26, zorder=4)
            ax.annotate(f"{maximum:.3f} m", (maximum, target), xytext=(-8, -13) if mode == "actual" else (8, 7),
                        textcoords="offset points", ha="right" if mode == "actual" else "left", color=color, fontsize=9)
    at_cap = next(row for row in curve if row["distance_m"] == cap)
    ax.scatter([cap, cap], [at_cap["actual"], at_cap["command"]], c=[ACTUAL, COMMAND], marker="D", s=24, zorder=4)
    ax.annotate(f"{cap:g} m", (cap, 0.01), xycoords=("data", "axes fraction"), xytext=(3, 0),
                textcoords="offset points", va="bottom", color=MUTED, fontsize=8)
    ax.set(xlim=(0, curve[-1]["distance_m"]), ylim=(max(0.0, min(curve[-1]["actual"], target) - .02), 1.008),
           xlabel="Cumulative travel (m)", ylabel="Success probability")
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=1, decimals=1))
    ax.legend(frameon=False, loc="lower left", fontsize=9)
    _save(fig, directory, "reliability")


def _convergence(result: dict, directory: Path) -> None:
    """绘制 Hermite 阶数、Sobol 样本量和 MC 抽样区间的独立证据。"""
    validation = result["summary"]["validation"]
    target = result["summary"]["inputs"]["minimum_probability"]
    references = {mode: row["probability_at_root"] for mode, row in result["summary"]["limits"].items()}
    fig = Figure(figsize=(10.8, 3.8), layout="constrained")
    left, middle, right = fig.subplots(1, 3)
    for ax in (left, middle, right):
        _style(ax)
    for mode, label, color in (("actual", "Actual pointing", ACTUAL), ("command", "Commanded bearing", COMMAND)):
        reference = references[mode]
        gh = [r for r in result["methods"]["hermite"]["levels"] if r["mode"] == mode and r["label"] == "critical"]
        left.plot([r["order"] for r in gh], [max(1e-16, abs(r["probability"] - reference)) for r in gh],
                  "o-", lw=1.4, ms=3, color=color, label=label)
        if mode == "command":
            left.plot([r["order"] for r in gh], [abs(r["raw_indicator_probability"] - reference) for r in gh],
                      "x--", color=MUTED, lw=0.85, ms=4, label="Raw command indicator")
        qmc = [r for r in validation["stochastic"]["sobol_levels"] if r["mode"] == mode and r["label"] == "critical"]
        middle.plot([r["power"] for r in qmc], [max(1e-12, abs(r["difference"])) for r in qmc],
                    "o-", lw=1.4, ms=3, color=color)
        middle.plot([r["power"] for r in qmc], [max(1e-12, r["standard_error"]) for r in qmc],
                    "--", lw=1, color=color, alpha=0.75, label=f"{label} SE")
        middle.set_xticks([r["power"] for r in qmc], [f"$2^{{{r['power']}}}$" for r in qmc])
    left.set(title="Gauss–Hermite", xlabel="Nodes per axis", ylabel="Absolute probability error", yscale="log")
    left.legend(loc="best", fontsize=7, frameon=False)
    middle.set(title="Scrambled Sobol", xlabel="Points per scramble", ylabel="Absolute probability error / SE", yscale="log")
    middle.legend(loc="best", fontsize=7, frameon=False)

    mc = validation["stochastic"]["monte_carlo_sites"]
    right.axvline(0, linestyle="--", lw=1, color=THRESHOLD)
    tick_labels, tick_positions = [], []
    for mode, label, color in (("actual", "Actual", ACTUAL), ("command", "Command", COMMAND)):
        for probe, short in (("above_stochastic", "above"), ("critical", "at root"), ("below_stochastic", "below")):
            row = next((r for r in mc if r["mode"] == mode and r["label"] == probe), None)
            if row is None:
                continue
            i = len(tick_positions)
            tick_positions.append(i)
            tick_labels.append(f"{label} {short}")
            estimate = row["probability"]
            right.hlines(i, (row["simultaneous_low"] - target) * 1000.0,
                         (row["simultaneous_high"] - target) * 1000.0, lw=4.0, color=color, alpha=0.25)
            right.errorbar((estimate - target) * 1000.0, i,
                           xerr=[[1000.0 * (estimate - row["wilson_low"])], [1000.0 * (row["wilson_high"] - estimate)]],
                           fmt="o", color=color, capsize=2.5, ms=4)
            right.scatter((row["reference_probability"] - target) * 1000.0, i, marker="|", color="#202c37", zorder=4)
    right.set_title("Monte Carlo")
    right.set_xlabel(f"Probability minus {target:g} (× 10³)\nDark: Wilson  ·  Light: simultaneous  ·  Tick: integral", fontsize=8)
    right.set_yticks(tick_positions, tick_labels)
    right.tick_params(axis="y", labelsize=7)
    _save(fig, directory, "method_validation")


def _parameter_region(result: dict, directory: Path) -> None:
    """在同一坐标绘制两种角度口径的设计参数边界与标称参数。"""
    summary = result["summary"]
    inputs = summary["inputs"]
    fig = Figure(figsize=(6.6, 4.1), layout="constrained")
    ax = fig.subplots()
    _style(ax)
    ax.set_title(f"Localization parameters for {inputs['design_distance_m']:g} m cumulative travel", loc="left", weight="bold")
    for mode, label, color, fill in (("command", "Commanded bearing", COMMAND, "#dce6f4"),
                                     ("actual", "Actual pointing", ACTUAL, "#b3d9d3")):
        rows = [row for row in result["ab_curve"] if row["mode"] == mode]
        x, y = [r["a_m"] for r in rows], [r["b_m_per_m"] for r in rows]
        ax.fill_between(x, y, color=fill)
        boundary = f"$a+{inputs['design_distance_m']:g}b={summary['limits'][mode]['sigma_m']:.6f}$"
        ax.plot(x, y, color=color, lw=1.7, label=f"{label} · {boundary}")
    ax.scatter(inputs["initial_sigma_m"], inputs["growth"], color=THRESHOLD, s=42, marker="x",
               label="Nominal localization", zorder=4)
    ax.text(0.04, 0.09, "Feasible for actual pointing", color=ACTUAL, transform=ax.transAxes, fontsize=9)
    ax.text(0.70, 0.47, "Infeasible for both", color=MUTED, transform=ax.transAxes, fontsize=9)
    max_a = max(inputs["initial_sigma_m"], *(lim["a_intercept_m"] for lim in summary["limits"].values()))
    max_b = max(inputs["growth"], *(lim["b_intercept_m_per_m"] for lim in summary["limits"].values()))
    ax.set(xlabel="Initial localization standard deviation, a (m)", ylabel="Growth rate, b (m/m)",
           xlim=(0, max_a * 1.07), ylim=(0, max_b * 1.17))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:.3f}"))
    ax.legend(loc="upper right", frameon=False, fontsize=9)
    _save(fig, directory, "ab_feasible_region")


def _contributions(result: dict, directory: Path) -> None:
    """并列显示一阶方差占比和逐项误差消融对达标概率的提升。"""
    rows = result["summary"]["contributions"]["rows"]
    groups = ("initial", "critical", "at_design")
    distances = {row["label"]: row["distance_m"] for row in rows}
    captions = [f"{distances[group]:g} m" if group != "critical" else "Critical" for group in groups]
    x = np.arange(len(groups))
    fig = Figure(figsize=(8.4, 3.6), layout="constrained")
    left, right = fig.subplots(1, 2)
    for ax in (left, right):
        _style(ax)
        ax.set_xticks(x, captions)
    bottom = np.zeros(x.shape)
    for j, (source, label, color) in enumerate(SOURCES):
        selected = {row["label"]: row for row in rows if row["source"] == source}
        shares = np.array([selected[group]["linear_share"] or 0.0 for group in groups])
        improvements = [100.0 * selected[group]["delta_probability"] for group in groups]
        left.bar(x, shares, width=0.58, bottom=bottom, label=label, color=color)
        right.bar(x + (j - 1) * 0.23, improvements, width=0.2, color=color)
        bottom += shares
    left.set(title="First-order variance shares", ylabel="Share of variance", ylim=(0, 1.02))
    left.yaxis.set_major_formatter(PercentFormatter(1, decimals=0))
    right.set(title="Reliability gain after removing one source", ylabel="Probability gain (percentage points)")
    left.legend(loc="upper center", bbox_to_anchor=(0.5, -0.11), frameon=False, ncol=3, fontsize=8)
    _save(fig, directory, "error_contributions")


def write_plots(result: dict, directory: Path) -> None:
    """从本次验收后的全量数据写出四组图，不读取手工标注数值。"""
    for plot in (_reliability, _convergence, _parameter_region, _contributions):
        plot(result, directory)
