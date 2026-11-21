"""Q3 归档图：80 m 定位参数的三种区域、三类误差贡献及消融。
输入为原始临界值、参数域折线和消融底表；输出到 runs/q3/，不重算概率。
运行：nohup python3 -B -m src.shared.b_figures q3。
"""

from matplotlib.patches import Patch
from matplotlib.ticker import FormatStrFormatter, PercentFormatter
import numpy as np

from src.shared.b_figures.common import (INK, MUTED, PLOT_BG, Atlas, clean_axes, footnote, new_figure, read_json,
                                         records)


VISUAL_STYLE = "paper-blue-slate"
# 主蓝保留上一版的亮度，辅助色用低饱和蓝灰，靠明度与斜纹区分层次。
BLUE = "#1264cb"
BLUE_TINT = "#e5effc"
SLATE_TINT = "#dce5ef"
SLATE_HATCH = "#8c9fb3"
COMMAND_LINE = "#5f7387"


def _parameters(atlas, summary_path, boundary_path, limits_path):
    """显式区分两者达标、仅指令角达标和两者均不达标的区域。"""
    inputs = read_json(summary_path)["inputs"]
    distance = inputs["design_distance_m"]
    a_nom, b_nom = inputs["initial_sigma_m"], inputs["growth"]
    data, limits = records(boundary_path), {r["mode"]: r for r in records(limits_path)}
    curves = {mode: np.array(sorted((float(r["a_m"]), float(r["b_m_per_m"])) for r in data if r["mode"] == mode))
              for mode in ("actual", "command")}
    fig = new_figure((8.0, 5.0), f"Q3  |  Localization parameters at {distance:g} m",
                     "Required reliability: at least 95% within ±5°. Actual pointing is the stricter criterion.")
    ax = fig.add_axes((.136, .24, .81, .55))
    clean_axes(ax)
    x = np.unique(np.concatenate([v[:, 0] for v in curves.values()]))
    y = {mode: np.interp(x, *values.T, right=0) for mode, values in curves.items()}
    ax.fill_between(x, 0, y["actual"], color=BLUE_TINT, zorder=1)
    ax.fill_between(x, y["actual"], y["command"], facecolor=SLATE_TINT,
                    edgecolor=SLATE_HATCH, hatch="//", linewidth=0, zorder=1)
    for mode, color, style in (("actual", BLUE, "-"), ("command", COMMAND_LINE, "--")):
        ax.plot(*curves[mode].T, color=color, ls=style, lw=1.9, zorder=2)
    ax.scatter(a_nom, b_nom, color=INK, s=63, marker="x", linewidth=2.1, zorder=4)
    ax.annotate(f"Nominal ({a_nom:.2f}, {b_nom:.3f})", (a_nom, b_nom), xytext=(9, 8), textcoords="offset points",
                color=INK, weight="semibold")
    ax.text(.17, .0052, "Both meet 95%", color=BLUE, weight="semibold")
    ax.text(1.21, .0209, "Neither meets 95%", color=MUTED, ha="center", weight="semibold")
    anchor_x = 1.15
    anchor_y = np.mean([np.interp(anchor_x, *values.T) for values in curves.values()])
    ax.annotate("Command only", (anchor_x, anchor_y), xytext=(1.57, .0115), color=COMMAND_LINE, ha="center",
                bbox={"fc": PLOT_BG, "ec": "none", "pad": 2},
                arrowprops={"arrowstyle": "-", "color": COMMAND_LINE, "relpos": (.35, 0), "shrinkB": 1.5})
    ax.set(xlim=(0, 1.88), ylim=(0, .024), xlabel="Initial per-axis standard deviation, a (m)",
           ylabel="Growth rate, b (m/m)")
    ax.yaxis.set_major_formatter(FormatStrFormatter("%.3f"))
    actual, command = (float(limits[m]["sigma_m"]) for m in ("actual", "command"))
    footnote(fig, f"Actual boundary (solid): a + {distance:g}b = {actual:.6f} m.  "
             f"Command boundary (dashed): a + {distance:g}b = {command:.6f} m.\n"
             "Each boundary counts as feasible for its own criterion. The nominal parameters miss both targets.")
    atlas.export(fig, "q3_parameters_80m", (summary_path, boundary_path, limits_path),
                 "以 ab_boundary.csv 同一边界数据分区，标出两条线之间只满足指令角的窄带；不重求根。")


def _contributions(atlas, path):
    """左图比较方差占比，右图只对有可见效应的两个远里程展示消融百分点。"""
    indexed = {(r["label"], r["source"]): r for r in records(path)}
    # 方差占比和消融沿用同一组色样，零位另用斜纹帮助灰度辨识。
    sources = (
        ("localization", "Vehicle localization", {"facecolor": BLUE, "edgecolor": BLUE}),
        ("zero", "Gimbal zero offset", {"facecolor": SLATE_TINT, "edgecolor": SLATE_HATCH, "hatch": "//"}),
        ("control", "Gimbal control", {"facecolor": "#a7bacd", "edgecolor": "#768da5"}),
    )
    groups = ("initial", "critical", "at_design")
    distances = [float(indexed[group, "localization"]["distance_m"]) for group in groups]
    labels = [f"{distances[0]:g} m", f"{distances[1]:.3f} m\n(actual 95% limit)", f"{distances[2]:g} m"]
    fig = new_figure((10.4, 4.85), "Q3  |  Localization drift dominates at longer travel distances",
                     "Actual pointing includes position, gimbal zero offset and gimbal control errors.")
    left, right = fig.subplots(1, 2, gridspec_kw={"left": .153, "right": .96, "top": .79, "bottom": .235,
                                                   "wspace": .47, "width_ratios": [1.05, 1]})
    for ax in (left, right):
        clean_axes(ax, grid="x")
    indexes = np.arange(len(groups))
    offset = np.zeros(len(groups))
    for i, (key, _, style) in enumerate(sources):
        is_localization = key == "localization"
        shares = np.array([float(indexed[group, key]["linear_share"]) for group in groups])
        left.barh(indexes, shares, left=offset, height=.5, linewidth=.45, **style)
        for j, share in enumerate(shares):
            if share >= .09:
                left.text(offset[j] + share / 2, j, f"{share:.1%}", va="center", ha="center", fontsize=8.6,
                          color="white" if is_localization else INK, weight="semibold")
        offset += shares
        values = [100 * float(indexed[group, key]["delta_probability"]) for group in groups[1:]]
        ypos = np.arange(len(values)) + (i - 1) * .218
        right.barh(ypos, values, height=.157, linewidth=.45, **style)
        for yy, value in zip(ypos, values):
            right.text(value + .12, yy, f"+{value:.3f}", va="center", fontsize=9,
                       color=BLUE if is_localization else MUTED, weight="semibold" if is_localization else "normal")
    left.set(title="A   First-order variance shares", xlim=(0, 1), ylim=(2.55, -.55),
             yticks=indexes, yticklabels=labels, xlabel="Share of total variance")
    left.set_xticks([0, .25, .5, .75, 1])
    left.xaxis.set_major_formatter(PercentFormatter(1, decimals=0))
    right.set(title="B   Gain from removing one error source", xlim=(0, 10), ylim=(1.52, -.52),
              yticks=[0, 1], yticklabels=[f"{distances[1]:.3f} m", f"{distances[2]:g} m"],
              xlabel="Reliability gain (percentage points)")
    fig.legend(handles=[Patch(**style, linewidth=.45, label=label) for _, label, style in sources], ncols=3, loc="center",
               bbox_to_anchor=(.5, .118), columnspacing=2.0)
    max_initial = max(100 * float(indexed["initial", key]["delta_probability"]) for key, _, _ in sources)
    footnote(fig, f"At 0 m the largest gain is only {max_initial:.5f} percentage points (not drawn in B). "
             "Single-source gains are not additive.")
    atlas.export(fig, "q3_error_sources", (path,),
                 "方差用 linear_share；精确概率逐项消融增益换算为百分点；0 m 的极小消融增益单列图注。")


def draw(atlas: Atlas) -> None:
    """重绘参数域与误差贡献；阈值曲线和方法验证沿用原 PDF。"""
    directory = atlas.source
    _parameters(atlas, directory / "summary.json", directory / "ab_boundary.csv", directory / "limits.csv")
    _contributions(atlas, directory / "error_contributions.csv")
