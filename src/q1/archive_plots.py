"""Q1 论文图集：绘制全场可观测域与连续最优弧的矢量图。
按归档配置重建并核对显示掩码，保留原单元边界，导出 PDF、SVG 和 PNG。
运行：nohup python3 -B -m src.shared.b_figures q1，输出到 runs/q1/。
"""

from matplotlib.lines import Line2D
from matplotlib.patches import Patch, PathPatch, Rectangle
from matplotlib.path import Path
import numpy as np

from src.shared.b_figures.common import Atlas, INK, MUTED, SECONDARY, field_axes, footnote, new_figure, read_json
from .config import load_config
from .region import RegionResult, build_observable_region


VISUAL_STYLE = "paper-teal-vector"
MAP_BG = "#faf9f6"
OBSERVABLE_FILL = "#d9eae2"
OPTIMAL_ARC = "#0b7568"
LOS_BAND = "#e6b78f"


def _observable_cells_path(grid: RegionResult) -> Path:
    """返回可观测单元的复合路径，坐标为米。空掩码得到空路径。"""
    # 两侧补不可观测列，每行相邻的通过单元共用一个矩形，边界仍落在原网格线上。
    padded = np.pad(grid.observable_mask, ((0, 0), (1, 1)), constant_values=False)
    rows, columns = np.nonzero(padded[:, :-1] != padded[:, 1:])
    step = grid.resolution
    left, right = columns.reshape(-1, 2).T * step
    bottom = rows[::2] * step
    top = (rows[::2] + 1) * step
    rectangles = np.stack((np.column_stack((left, bottom)), np.column_stack((right, bottom)),
                           np.column_stack((right, top)), np.column_stack((left, top))), axis=1)
    return Path.make_compound_path_from_polys(rectangles)


def draw(atlas: Atlas) -> None:
    """左图回答可观测区域；右图放大 80° 圆上的并列解及相切端点。"""
    config, summary_path, area_path = (atlas.source / n for n in ("config.yaml", "summary.json", "area_check_continuous.json"))
    cfg, summary, area = load_config(config), read_json(summary_path), read_json(area_path)
    grid = build_observable_region(cfg, summary["region"]["plot_resolution_m"])
    if (grid.feasible_count != summary["region"]["feasible_grid_count"] or
            grid.estimated_area != summary["region"]["estimated_area_m2"]):
        raise ValueError("Q1 重建的显示掩码与归档单元数不同")

    circle = summary["optimal_circle"]
    center, radius = np.array(circle["center_xy_m"]), circle["radius_m"]
    chosen = summary["tie_break"]
    figure = new_figure((8.8, 5.55), "Q1  |  T1 observability and optimal stations",
                        f"Minimum horizontal range: {radius:.6f} m  ·  Elevation: {chosen['elevation_deg']:.0f}°  ·  "
                        "Tie-break: maximum vehicle clearance")
    left, right = figure.subplots(1, 2, gridspec_kw={"left": .08, "right": .965, "top": .797, "bottom": .25, "wspace": .25})
    tx, ty = center
    pad = radius + 2.8
    focus = (tx - pad, tx + pad, ty - pad, ty + pad)
    observable = _observable_cells_path(grid)
    for ax, title in ((left, "A   Observable vehicle centers"), (right, "B   Feasibility on the 80° circle")):
        detail = ax is right
        # 两张图共用矢量路径，通过坐标区裁切局部。矩形共用边不描线。
        ax.add_patch(PathPatch(observable, facecolor=OBSERVABLE_FILL, edgecolor="none", zorder=0,
                               gid="observable-cells-detail" if detail else "observable-cells-field"))
        if ax is left:
            field_axes(ax, cfg)
            ax.add_patch(Rectangle((focus[0], focus[2]), 2 * pad, 2 * pad, fill=False, edgecolor=MUTED,
                                   linestyle=(0, (2, 2)), linewidth=1, zorder=4))
            ax.annotate("Detail B", (focus[0], focus[3]), xytext=(-7, 7), textcoords="offset points",
                        ha="right", color=MUTED, fontsize=9)
        else:
            field_axes(ax, cfg, extent=focus, ticks=([75, 80, 85, 90, 95], [60, 65, 70, 75, 80]))
        ax.set_facecolor(MAP_BG)
        ax.set_title(title, loc="left")
        ax.scatter(*center, c=INK, s=49, marker="^", edgecolors="white", linewidths=.7, zorder=8)
        ax.scatter(*chosen["selected_station_xy"], c=OPTIMAL_ARC, s=145, marker="*", edgecolors="white", linewidths=1, zorder=9)

    def arc(ax, limits, **style):
        """按归档连续角域画圆弧（单位度），不把网格作为最优可行性证据。"""
        angle = np.deg2rad(np.linspace(*limits, 360))
        ax.plot(tx + radius * np.cos(angle), ty + radius * np.sin(angle), **style)

    for ax in (left, right):
        for interval in circle["feasible_intervals_deg"]:
            arc(ax, (interval["left"], interval["right"]), color=OPTIMAL_ARC, lw=2.4, zorder=5)
        arc(ax, circle["vehicle_excluded_interval_deg"]["interval"], color=SECONDARY, linestyle="--", lw=1.4, zorder=5)
    # 宽底线仍在同一解析圆上，强调 LOS 区间是车体排除段的子集，不额外删除某段可行弧。
    arc(right, circle["los_blocked_interval_deg"]["interval"], color=LOS_BAND, lw=5.15, zorder=4, solid_capstyle="butt")
    for key, phi, text_xy, align in (
        ("north_tangent_xy_m", circle["feasible_intervals_deg"][0]["right"], (78.05, 75.75), "right"),
        ("east_tangent_xy_m", circle["feasible_intervals_deg"][-1]["left"], (82.6, 61.5), "center"),
    ):
        xy = circle["coverage"][key]
        right.scatter(*xy, s=38, marker="o", fc=OPTIMAL_ARC, ec="white", linewidth=1.0, zorder=7)
        right.annotate(f"{phi:.3f}°", xy, xytext=text_xy, ha=align, va="center", color=OPTIMAL_ARC, fontsize=8.2,
                       arrowprops={"arrowstyle": "-", "color": OPTIMAL_ARC, "lw": .75, "shrinkB": 4})
    right.annotate("T1 projection", center, xytext=(7, -12), textcoords="offset points", color=MUTED, fontsize=9)
    point = chosen["selected_station_xy"]
    right.annotate(f"Selected station\n({point[0]:.3f}, {point[1]:.3f}) m", point, xytext=(tx + .15, ty + .48 * radius),
                   ha="center", va="center", fontsize=8.9, color=OPTIMAL_ARC,
                   arrowprops={"arrowstyle": "-", "color": OPTIMAL_ARC, "lw": .85, "shrinkB": 6, "relpos": (1, 1)})

    figure.legend(handles=[Patch(facecolor=OBSERVABLE_FILL, label=f"Observable ({grid.resolution:g} m cells)"),
                            Line2D([], [], color=OPTIMAL_ARC, lw=2.4, label="Feasible optimal arc"),
                            Line2D([], [], color=SECONDARY, ls="--", label="Vehicle excluded"),
                            Line2D([], [], color=LOS_BAND, lw=5, label="LOS blocked (within excluded arc)"),
                            Line2D([], [], color=OPTIMAL_ARC, marker="o", ls="none", label="Included tangency")],
                  loc="center", bbox_to_anchor=(.50, .128), ncols=3, columnspacing=1.65, handlelength=1.7)
    footnote(figure, f"Grid: {grid.feasible_count:,} passing cells, {grid.estimated_area:,.2f} m². "
             f"Independent continuous area: about {area['midpoint_m2']:,.2f} m².\n"
             "The solid and dashed arcs form the 80° circle. Endpoint angles are measured CCW from east; both are included.")
    atlas.export(figure, "q1_observability", (config, summary_path, area_path),
                 "按归档步长重建 T1 显示掩码并核对单元数；同一行相邻单元合并为矢量矩形，保留原单元边界。"
                 "弧、端点、推荐站及连续面积来自 JSON。", svg=True)
