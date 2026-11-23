"""B 题第二问（1）两套等精度选站方案的独立复核、对照表与出图。

输入：configs/q1.yaml 的场地、楼体与仰角口径，题面五车初始位与 T2 告警；
方案 A 取队友分支的 0.25° 可行性扫描 + 10° 代表网格解，方案 B 取正文贴 0.45 m 净空线的旋转解。
输出：默认写入 runs/q2/双站等精度两方案对照/ 的两份 CSV 与 q21_schemes.pdf/png；
经队员复核后再复制到 results/q2/ 的归档目录。
运行：nohup python3 -B -m checks.q2_scheme_compare [--output <目录>]
AI 辅助：OpenCode / Magpie group/guomo，2026-11-22（版本发布日期未提供）。
"""

import argparse
import csv
import math
from pathlib import Path

import matplotlib as mpl
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Circle, Rectangle

from checks.q2_geometry import check_route, load_scene, pair_metrics
from src.shared.b_figures import common as style


# 队内工程工况：车体到楼体与场界的净空，m；与正文 4.2.3 的补充约束一致。
WALL_CLEARANCE_M = 0.45
ORIGINS = {"V3": (14.0, 86.0), "V5": (50.0, 93.0)}
LABELS = {"A": "Discrete grid construction", "B": "Clearance-binding construction"}
COLORS = {"A": style.ORANGE, "B": style.BLUE}
MARKERS = {"A": "o", "B": "s"}


def pair_stations(scene):
    """返回两套停车站与最小站距 r0。

    A 为队友分支 0.25° 可行性扫描后、在 10° 代表网格上选出的代表站（全精度小数）；B 由 y=77.9500002 m 的
    45 cm 净空线与 r0 圆求交，再正交旋转得到 V3 站。两套都满足 r1=r2=r0。
    """
    target = scene.targets["T2"]
    x_t, y_t = target[:2]
    elevation = math.radians(scene.elevation_deg[1])
    r_0 = (target[2] - scene.camera_height_m) / math.tan(elevation)
    d = 5.4500002
    a = math.sqrt(r_0 * r_0 - d * d)
    return {
        "A": {"V3": (5.560982463332533, 74.49014351406502),
              "V5": (13.019000343765285, 77.96787436636772)},
        "B": {"V3": (x_t - d, y_t + a),
              "V5": (x_t + a, y_t + d)},
    }, r_0


def measure(scene, scheme, sites):
    """按 45 cm 净空复核两段直达路，返回逐车行与方案级指标。"""
    target = scene.targets["T2"]
    rows = []
    for vehicle in ("V3", "V5"):
        origin, site = ORIGINS[vehicle], sites[vehicle]
        route = check_route(scene, [origin, site], origin, wall_clearance_m=WALL_CLEARANCE_M)
        # 净空按原楼体足迹减去车盘半径 1.5 m；闭合楼体允许相切，故取最小值。
        clearance = min(distance - scene.radius_m
                        for distance in route["min_distance_to_buildings_m"].values())
        elevation = math.degrees(math.atan2(target[2] - scene.camera_height_m,
                                            math.dist(site, target[:2])))
        rows.append({"scheme": scheme, "vehicle": vehicle,
                     "station_x_m": site[0], "station_y_m": site[1],
                     "route_length_m": route["length_m"], "travel_s": route["travel_s"],
                     "elevation_deg": elevation, "body_clearance_m": clearance})
    pair = pair_metrics(scene, sites["V3"], sites["V5"], target)
    summary = {
        "scheme": scheme,
        "longest_travel_s": max(row["travel_s"] for row in rows),
        "total_path_m": math.fsum(row["route_length_m"] for row in rows),
        "baseline_m": pair["baseline_m"],
        "angle_deg": pair["angle_deg"],
        "rmse_m": pair["rmse_m"],
        "v5_clearance_m": rows[1]["body_clearance_m"],
    }
    return rows, summary


def write_csv(path, rows):
    """按固定列序写 UTF-8 CSV；数值保留全精度，显示位数由论文决定。"""
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def plot(scene, sites, r_0, summaries):
    """画出等精度圆与两套站位，另附全场路线；蓝为采用方案，橙为对照。"""
    mpl.rcParams.update(style.STYLE)
    fig = Figure(figsize=(8.8, 4.6))
    FigureCanvasAgg(fig)
    left = fig.add_axes([0.075, 0.21, 0.38, 0.60])
    right = fig.add_axes([0.56, 0.21, 0.38, 0.60])
    target = scene.targets["T2"]
    x_t, y_t = target[:2]

    for ax in (left, right):
        ax.set_aspect("equal")
        ax.set_facecolor(style.FIELD_BG)
        for building in scene.buildings:
            ax.add_patch(Rectangle((building.x_min, building.y_min),
                                   building.x_max - building.x_min,
                                   building.y_max - building.y_min,
                                   facecolor=style.BUILDING, edgecolor=style.RULE,
                                   linewidth=0.6, zorder=1))
        ax.plot(*ORIGINS["V3"], marker="^", color=style.MUTED, markersize=5, zorder=3)
        ax.plot(*ORIGINS["V5"], marker="^", color=style.MUTED, markersize=5, zorder=3)

    # 左图：只放大 T2 与 B3，直接看出两套站位都在 r0 圆上且互相正交。
    # 两套站相距约 2 cm，图上重合；A 用空心大圆、B 用实心小方块，保证都看得见。
    left.add_patch(Circle((x_t, y_t), r_0, fill=False, linestyle="--",
                          edgecolor=style.MUTED, linewidth=0.9, zorder=2))
    left.plot(x_t, y_t, marker="*", color=style.INK, markersize=11, zorder=4)
    left.annotate("$T_2'$", (x_t, y_t), textcoords="offset points", xytext=(-16, 6),
                  fontsize=9, color=style.INK)
    left.annotate("$B_3$", (17.0, 70.5), fontsize=9, color=style.SECONDARY)
    for vehicle, site in sites["A"].items():
        left.plot(*site, marker=MARKERS["A"], markerfacecolor="none",
                  markeredgecolor=COLORS["A"], markeredgewidth=1.3,
                  markersize=10, zorder=5)
    for vehicle, site in sites["B"].items():
        left.plot(*site, marker=MARKERS["B"], color=COLORS["B"],
                  markersize=5.5, markeredgecolor="white", markeredgewidth=0.8, zorder=6)
        left.annotate(vehicle, site, textcoords="offset points",
                      xytext=(7, -11), fontsize=8.5, color=style.INK)
    left.set_xlim(0, 30)
    left.set_ylim(60, 85)
    left.set_xlabel("East (m)")
    left.set_ylabel("North (m)")
    left.set_title("Stations on the minimum-range circle", fontsize=10)
    left.grid(True, color=style.GRID, linewidth=0.6)

    # 右图：全场路线；两套方案用同色细线，便于核对 30 s 行驶上限。
    # A 的站与 B 只差厘米级，橙色虚线加粗后仍可沿蓝色实线两侧看到。
    for vehicle, site in sites["A"].items():
        origin = ORIGINS[vehicle]
        right.plot([origin[0], site[0]], [origin[1], site[1]],
                   color=COLORS["A"], linewidth=2.0, linestyle="--", zorder=4)
    for vehicle, site in sites["B"].items():
        origin = ORIGINS[vehicle]
        right.plot([origin[0], site[0]], [origin[1], site[1]],
                   color=COLORS["B"], linewidth=0.9, linestyle="-", zorder=5)
        right.plot(*site, marker=MARKERS["B"], color=COLORS["B"],
                   markersize=5.5, markeredgecolor="white", markeredgewidth=0.8, zorder=6)
    right.set_xlim(0, 100)
    right.set_ylim(0, 100)
    right.set_xlabel("East (m)")
    right.set_ylabel("North (m)")
    right.set_title("Direct routes from the initial centers", fontsize=10)
    right.grid(True, color=style.GRID, linewidth=0.6)

    handles = [
        Line2D([], [], marker=MARKERS["A"], markerfacecolor="none",
               markeredgecolor=COLORS["A"], markersize=9, linestyle="",
               label=LABELS["A"]),
        Line2D([], [], marker=MARKERS["B"], color=COLORS["B"], linestyle="",
               markersize=6, label=LABELS["B"]),
        Line2D([], [], marker="^", color=style.MUTED, linestyle="",
               markersize=5, label="Initial vehicle center"),
    ]
    fig.legend(handles=handles, loc="lower center", ncol=3, frameon=False,
               bbox_to_anchor=(0.5, 0.015), fontsize=8.5)
    fig.text(0.075, 0.975, "Two equal-accuracy station pairs for $T_2$",
             fontsize=14, weight="semibold", va="top", color=style.INK)
    fig.text(0.075, 0.925,
             f"Both pairs: $r_1=r_2=r_0={r_0:.6f}$ m, $\\gamma=90^\\circ$, "
             f"$E_{{\\mathrm{{lin}}}}={summaries['B']['rmse_m']:.9f}$ m.",
             fontsize=8.5, color=style.MUTED, va="top")
    fig.text(0.075, 0.888,
             f"Longest drive: {summaries['A']['longest_travel_s']:.6f} s (A), "
             f"{summaries['B']['longest_travel_s']:.6f} s (B).",
             fontsize=8.5, color=style.MUTED, va="top")
    return fig


def main():
    """复核两套站对、写表并出图；任何净空或行驶不合格都直接报错。"""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path,
                        default=Path("runs/q2/双站等精度两方案对照"),
                        help="输出目录；默认写 runs/，复核后再复制到 results/")
    output = parser.parse_args().output
    scene = load_scene()
    sites, r_0 = pair_stations(scene)
    per_vehicle, summaries = [], {}
    for scheme in ("A", "B"):
        rows, summary = measure(scene, scheme, sites[scheme])
        per_vehicle.extend(rows)
        summaries[scheme] = summary
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "two_schemes.csv", per_vehicle)
    write_csv(output / "two_schemes_summary.csv", list(summaries.values()))
    fig = plot(scene, sites, r_0, summaries)
    fig.savefig(output / "q21_schemes.pdf")
    fig.savefig(output / "q21_schemes.png", dpi=220)
    for scheme, summary in summaries.items():
        print(f"{scheme}: longest {summary['longest_travel_s']:.9f} s, "
              f"total {summary['total_path_m']:.9f} m, "
              f"E {summary['rmse_m']:.9f} m, V5 clearance {summary['v5_clearance_m']:.9f} m")


if __name__ == "__main__":
    main()
