"""q1 计算图表，供统一运行入口在本地实验目录生成英文 PNG。
输入为场景、网格掩码和最优圆证据，输出为五张主图与分辨率对照图。
单独生成 nohup python3 -B -m src.run q1 --profile debug。
"""

import math
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import ListedColormap
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Circle, Patch, Rectangle

from .config import Config
from .geometry import compute_clearance
from .optimize import OptimumResult, circle_xy
from .region import RegionResult


VISIBLE = "#318781"
EXCLUDED = "#eee8de"
BUILDING = "#626872"
TARGET = "#325b9a"
SELECTED = "#8a2869"
WITNESS = "#223e65"
BLOCKED_BODY = "#b3544b"
BLOCKED_LOS = "#d89029"
MASK_COLORS = ListedColormap((EXCLUDED, VISIBLE))


def _save(figure: Figure, directory: Path, name: str) -> None:
    """用无图形桌面的 Agg 渲染指定 PNG 文件。"""
    FigureCanvasAgg(figure)
    figure.savefig(directory / name, dpi=180)
    figure.clear()


def _scene(ax, cfg: Config, *, full_field: bool = True, labels: bool = True, outlines: bool = False) -> None:
    """绘制官方建筑底面和 T1，坐标轴使用米与等比例。"""
    for b in cfg.buildings:
        ax.add_patch(Rectangle(
            (b.x_min, b.y_min), b.x_max - b.x_min, b.y_max - b.y_min,
            facecolor="none" if outlines else "#d0d3d9", edgecolor=BUILDING, linewidth=1, alpha=0.85, zorder=2,
        ))
        if labels:
            ax.text((b.x_min + b.x_max) / 2, (b.y_min + b.y_max) / 2, b.id,
                    ha="center", va="center", color="#252936", fontsize=8, zorder=3, clip_on=True)
    ax.scatter(*cfg.target_xyz[:2], s=48, marker="^", color=TARGET, edgecolor="white", zorder=6, label="T1 projection")
    if full_field:
        ax.set(xlim=(0, cfg.field_width), ylim=(0, cfg.field_height))
    ax.set(xlabel="East (m)", ylabel="North (m)", aspect="equal")
    ax.tick_params(labelsize=8)


def _imshow(ax, mask: np.ndarray, cfg: Config) -> None:
    """用实际单元边界渲染布尔掩码，避免图像插值改变可行轮廓。"""
    ax.imshow(mask, extent=(0, cfg.field_width, 0, cfg.field_height), origin="lower",
              interpolation="nearest", cmap=MASK_COLORS, vmin=0, vmax=1, zorder=0)


def _plot_layout(cfg: Config, directory: Path) -> None:
    """导出场地位置、目标投影和调度中心。"""
    fig = Figure(figsize=(6, 6), layout="constrained")
    ax = fig.subplots()
    ax.set_title("Field and nominal T1 alert", loc="left", weight="bold")
    _scene(ax, cfg)
    ax.scatter(*cfg.dispatch_center, s=26, color="#252936", label="Dispatch center", zorder=5)
    ax.legend(loc="upper left", frameon=True, fontsize=8)
    _save(fig, directory, "field_layout.png")


def _plot_masks(region: RegionResult, cfg: Config, directory: Path) -> None:
    """导出三张相互独立的通过掩码和共用图例。"""
    fig = Figure(figsize=(12, 4.6), layout="constrained")
    axes = fig.subplots(1, 3, sharex=True, sharey=True)
    panels = (("Vehicle geometry", region.vehicle_mask), ("Gimbal elevation", region.gimbal_mask),
              ("Clear line of sight", region.los_clear_mask))
    for ax, (title, mask) in zip(axes, panels):
        _imshow(ax, mask, cfg)
        _scene(ax, cfg, labels=False, outlines=True)
        ax.set_title(title, loc="left", weight="bold", fontsize=11)
    axes[2].legend(handles=[Patch(color=VISIBLE, label="Pass"), Patch(color=EXCLUDED, label="Fail")],
                   loc="lower right", fontsize=8)
    fig.suptitle(f"Independent checks at {region.resolution:g} m cell spacing", x=0.02, ha="left")
    fig.supxlabel("Line of sight is evaluated even at locations where a vehicle cannot stop.", fontsize=9)
    _save(fig, directory, "constraint_masks.png")


def _plot_observable(region: RegionResult, result: OptimumResult, cfg: Config, directory: Path) -> None:
    """导出最终可观测区、理论最优圆及所选站位。"""
    fig = Figure(figsize=(6, 6), layout="constrained")
    ax = fig.subplots()
    _imshow(ax, region.observable_mask, cfg)
    _scene(ax, cfg)
    ax.add_patch(Circle(cfg.target_xyz[:2], result.circle.radius, fill=False, linestyle="--", linewidth=1.5,
                        edgecolor=TARGET, label="Primary optimum circle", zorder=4))
    ax.scatter(result.selected.x, result.selected.y, marker="*", s=155, color=SELECTED,
               edgecolor="white", linewidth=0.7, zorder=7, label="Selected station")
    ax.set_title(f"Observable region for T1  |  {region.resolution:g} m cells", loc="left", weight="bold")
    handles, _ = ax.get_legend_handles_labels()
    ax.legend(handles=[Patch(color=VISIBLE, label="Observable"), Patch(color=EXCLUDED, label="Excluded"), *handles],
              loc="upper left", fontsize=8)
    _save(fig, directory, "observable_region.png")


def _plot_arcs(result: OptimumResult, cfg: Config, directory: Path) -> None:
    """导出圆周的可行弧、车体禁入弧、独立遮挡弧与位置标记。"""
    fig = Figure(figsize=(7.2, 6.2), layout="constrained")
    ax = fig.subplots()
    _scene(ax, cfg, full_field=False, labels=False)
    circle = result.circle
    for index, (a, b) in enumerate(circle.intervals_rad):
        points = np.array([circle_xy(phi, cfg) for phi in np.linspace(a, b, 400)])
        ax.plot(points[:, 0], points[:, 1], color=VISIBLE, lw=3,
                label="Feasible arc" if index == 0 else "_nolegend_", zorder=4)
    for interval, color, style, label in (
        (circle.vehicle_blocked_rad, BLOCKED_BODY, "-", "Vehicle excluded"),
        (circle.los_blocked_rad, BLOCKED_LOS, "--", "LOS blocked"),
    ):
        points = np.array([circle_xy(phi, cfg) for phi in np.linspace(*interval, 160)])
        ax.plot(points[:, 0], points[:, 1], color=color, lw=2.5, ls=style, label=label, zorder=4)
    for key in ("north_tangent_xy_m", "east_tangent_xy_m"):
        ax.scatter(*circle.proof[key], s=38, facecolor="white", edgecolor=VISIBLE, linewidth=1.8, zorder=5)
    ax.scatter(result.witness.x, result.witness.y, s=60, marker="D", color=WITNESS, label="Witness", zorder=6)
    ax.scatter(result.selected.x, result.selected.y, s=155, marker="*", color=SELECTED,
               edgecolor="white", linewidth=0.8, label="Selected station", zorder=7)
    tx, ty, _ = cfg.target_xyz
    pad = circle.radius + 2
    obstacle = next(b for b in cfg.buildings if b.id == circle.proof["only_relevant_building"])
    ax.text((max(obstacle.x_min, tx - pad) + obstacle.x_max) / 2,
            (max(obstacle.y_min, ty - pad) + obstacle.y_max) / 2,
            obstacle.id, ha="center", va="center", color="#252936", fontsize=9, zorder=3)
    ax.set(xlim=(tx - pad, tx + pad), ylim=(ty - pad, ty + pad))
    ax.set_title("Feasibility on the primary optimum circle", loc="left", weight="bold")
    handles, labels = ax.get_legend_handles_labels()
    fig.legend(handles, labels, loc="outside lower center", ncols=3, fontsize=9)
    _save(fig, directory, "optimal_arcs.png")


def _plot_clearance(result: OptimumResult, cfg: Config, directory: Path) -> None:
    """导出净空曲线和连续可行角域内的局部与全局候选。"""
    fig = Figure(figsize=(8, 4.5), layout="constrained")
    ax = fig.subplots()
    angles = np.linspace(0, math.tau, 1201)
    values = [compute_clearance(*circle_xy(phi, cfg), cfg)[0] for phi in angles]
    ax.plot(np.rad2deg(angles), values, color="#999aa6", lw=1, label="Full circle")
    for index, (a, b) in enumerate(result.circle.intervals_rad):
        subset = np.linspace(a, b, 601)
        ax.plot(np.rad2deg(subset), [compute_clearance(*circle_xy(phi, cfg), cfg)[0] for phi in subset],
                color=VISIBLE, lw=2, label="Feasible" if index == 0 else "_nolegend_")
    if result.local_peaks:
        ax.scatter([math.degrees(phi) for phi, _ in result.local_peaks],
                   [score for _, score in result.local_peaks], color=WITNESS, s=24, label="Local peaks", zorder=4)
    ax.scatter(math.degrees(result.selected_angle_rad), result.selected.clearance_min,
               s=145, color=SELECTED, marker="*", label="Selected", zorder=5)
    ax.axhline(0, color="#999aa6", lw=0.7)
    ax.set(xlim=(0, 360), xticks=np.arange(0, 361, 45), xlabel="Circle angle (degrees)", ylabel="Minimum vehicle clearance (m)")
    ax.set_title("Clearance among primary-optimal stations", loc="left", weight="bold")
    ax.legend(loc="lower right", fontsize=8)
    _save(fig, directory, "clearance_profile.png")


def _plot_resolution_study(regions: list[RegionResult], cfg: Config, directory: Path) -> None:
    """导出各分辨率的联合轮廓，使用同一场地范围和色标。"""
    fig = Figure(figsize=(4.15 * len(regions), 4.5), layout="constrained")
    axes = fig.subplots(1, len(regions), sharex=True, sharey=True, squeeze=False)
    for ax, region in zip(axes[0], regions):
        _imshow(ax, region.observable_mask, cfg)
        _scene(ax, cfg, labels=False)
        ax.set_title(f"{region.resolution:g} m  |  {region.estimated_area:.2f} m²", loc="left", fontsize=11)
    fig.legend(handles=[Line2D([], [], marker="s", linestyle="none", color=VISIBLE, label="Observable cells")],
               loc="outside lower center", fontsize=8)
    _save(fig, directory, "resolution_comparison.png")


def write_plots(regions: list[RegionResult], plot_region: RegionResult, result: OptimumResult,
                cfg: Config, directory: Path) -> None:
    """按任务书写五张基础图和多分辨率对照图，输入来自本次运行。"""
    _plot_layout(cfg, directory)
    _plot_masks(plot_region, cfg, directory)
    _plot_observable(plot_region, result, cfg, directory)
    _plot_arcs(result, cfg, directory)
    _plot_clearance(result, cfg, directory)
    if len(regions) > 1:
        _plot_resolution_study(regions, cfg, directory)
