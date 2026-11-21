"""Q2(2) 归档图：两套方案的避障折线、V3 接续细节及五车时间轴。
仅使用场地快照、分配、路点和时间轴；输出到 runs/q2/，不求解站位。
运行：nohup python3 -B -m src.shared.b_figures q2。
"""

from collections import defaultdict

from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
import numpy as np

from src.shared.b_figures.common import (BLUE, INK, MUTED, PLOT_BG, SECONDARY,
                                         Atlas, clean_axes, field_axes, footnote,
                                         new_figure, phase_bar, phase_keys, read_json, records, route_arrow)
from .scene import read_scene


PLANS = ("optimized", "greedy")
PLAN_LABEL = {"optimized": "Optimized", "greedy": "Nearest assignment"}
SUPPORT_ROUTE = "#567189"
PHASES = (("TRAVELING", "depart_s", "arrive_s"), ("SLEWING", "turn_begin_s", "turn_end_s"),
          ("READY_WAITING", "turn_end_s", "observation_start_s"),
          ("OBSERVING", "observation_start_s", "observation_end_s"), ("RESETTING", "reset_begin_s", "reset_end_s"))


def _draw_routes(atlas, scene, config, routes_path, assignments_path):
    """用同尺度全场对照和独立局部坐标放大 V3 仅数米的两段转场。"""
    # 站位和路线来自不同底表；路线终点必须与配车记录一致。
    routes = [r for r in read_json(routes_path) if r["plan"] in PLANS]
    assignments = {(r["plan"], r["vehicle"], r["target"]): (float(r["site_x_m"]), float(r["site_y_m"]))
                   for r in records(assignments_path) if r["plan"] in PLANS}
    for r in routes:
        site = assignments[r["plan"], r["vehicle"], r["target"]]
        if tuple(r["waypoints"][-1]) != tuple(r["destination"]) or tuple(r["destination"]) != site:
            raise ValueError("Q2 路线路点与分配站位不一致")

    v3 = [r for r in routes if (r["plan"], r["vehicle"]) == ("optimized", "V3")]
    detail = (12.8, 18.4, 84.7, 90.3)
    fig = new_figure((11.0, 5.3), "Q2(2)  |  Routes and vehicle reuse",
                     "In the nearest-assignment schedule all six vehicle–task legs stay at the initial positions.")
    a, b, zoom = fig.subplots(1, 3, gridspec_kw={"left": .06, "right": .974, "top": .797, "bottom": .24, "wspace": .28})
    for ax, plan, letter in zip((a, b), PLANS, "AB"):
        field_axes(ax, scene.field)
        ax.set_title(f"{letter}   {PLAN_LABEL[plan]}", loc="left")
        car_ink = dict.fromkeys(scene.vehicles, SECONDARY)
        if plan == "optimized":
            car_ink.update(V3=BLUE, V2=SUPPORT_ROUTE)
        for name, target in zip(scene.targets, scene.target_cfg):
            xy = target.target_xyz[:2]
            ax.scatter(*xy, c=INK, marker="*", s=58, zorder=6)
            dx, dy = {"T1": (-6, 7), "T2": (-5, 4), "T3": (-6, 6)}[name]
            ax.annotate(name, xy, xytext=(dx, dy), textcoords="offset points",
                        ha="right" if dx < 0 else "left", va="top" if dy < 0 else "bottom", fontsize=8.5)
        for name, (x, y) in zip(scene.vehicles, scene.origins):
            ax.scatter(x, y, c=car_ink[name], marker="s", s=15, zorder=4)
        stops = defaultdict(list)
        for row in (r for r in routes if r["plan"] == plan):
            points, name = row["waypoints"], row["vehicle"]
            stops[name, tuple(row["destination"])].append(row["target"])
            crucial = plan == "optimized" and name == "V3"
            ax.plot(*np.asarray(points).T, lw=2 if crucial else 1.35, color=car_ink[name], zorder=4)
            route_arrow(ax, points, car_ink[name], minimum_m=4)
        for (name, site), alerts in stops.items():
            if plan == "optimized" and name == "V3":
                continue  # 两个近邻 V3 站在局部图上单独画站位符号，避免全场重叠。
            ax.scatter(*site, facecolors="none", edgecolors=car_ink[name], s=62, linewidths=1.15, zorder=5)
            xoff, yoff = {"V1": (4, -8), "V2": (-4, 5), "V3": (5, 4), "V4": (0, -8), "V5": (4, 2)}[name]
            ha = "left" if xoff > 0 else "right" if xoff < 0 else "center"
            ax.annotate(f"{name} {'→'.join(alerts)}", site, xytext=(xoff, yoff), textcoords="offset points",
                        va="bottom" if yoff >= 0 else "top", ha=ha,
                        color=car_ink[name], fontsize=8.4, zorder=6)
    a.add_patch(Rectangle((detail[0], detail[2]), detail[1] - detail[0], detail[3] - detail[2], fc="none",
                          ec=BLUE, lw=1.2, ls="--", zorder=6))
    a.annotate("C", (detail[1], detail[3]), xytext=(5, 3), textcoords="offset points",
               fontsize=9, weight="bold", color=BLUE)

    field_axes(zoom, scene.field, extent=detail, ticks=([13, 14, 15, 16, 17, 18], [85, 86, 87, 88, 89, 90]))
    zoom.set_facecolor(PLOT_BG)
    zoom.set_title("C   V3 in the optimized schedule", loc="left")
    zoom.set_axisbelow(True)
    zoom.grid()
    origin = scene.origins[scene.vehicles.index("V3")]
    zoom.scatter(*origin, c=BLUE, marker="s", s=42, zorder=5)
    zoom.annotate("Initial position", origin, xytext=(-6, -8), textcoords="offset points", va="top", fontsize=9, color=BLUE)
    for row in v3:
        points = np.asarray(row["waypoints"])
        zoom.plot(*points.T, lw=2.15, color=BLUE, zorder=4)
        route_arrow(zoom, points, BLUE)
        zoom.scatter(*points[-1], fc="white", ec=BLUE, linewidth=1.7, s=61, zorder=6)
        zoom.annotate(row["target"], points[-1], xytext=(7, -3), textcoords="offset points", fontsize=9.4, color=BLUE)
        offset = (-7, 9) if row["previous_target"] is None else (8, -11)
        zoom.annotate(f"{row['length_m']:.3f} m", points.mean(axis=0), xytext=offset, textcoords="offset points",
                      ha="right" if offset[0] < 0 else "left", fontsize=9, color=MUTED)
    legend = [Line2D([], [], marker="s", color=MUTED, ls="none", label="Initial vehicle center"),
              Line2D([], [], marker="o", color=MUTED, mfc="none", ls="none", label="Observation station"),
              Line2D([], [], marker="*", color=INK, ls="none", label="Alert projection")]
    fig.legend(handles=legend, ncols=3, loc="center", bbox_to_anchor=(.5, .117))
    footnote(fig, "A and B share a scale. A square inside a ring marks a station at the initial position.\n"
             "In A, V3 serves T1 then T3 while V1 and V2 prepare for T2 in parallel.")
    atlas.export(fig, "q22_routes", (config, scene.field_file, routes_path, assignments_path),
                 "同尺度显示 optimized、greedy；箭头沿归档折线，V3 接续用独立坐标放大；零行程仅标原位站。")


def _draw_timeline(atlas, scene, config, timeline_path, comparison_path):
    """显示完整五车相位，独立标记最晚同步开测而非最后复位。"""
    comparison = read_json(comparison_path)
    rows = records(timeline_path)
    latest = {p: comparison["plan_summaries"][p]["latest_s"] for p in PLANS}
    saved = latest["greedy"] - latest["optimized"]
    fig = new_figure((9.0, 6.15), "Q2(2)  |  A shorter V3 chain lowers the latest start",
                     f"Latest synchronized start improves by {saved:.3f} s; all phases are drawn on one time scale.")
    upper, lower = fig.subplots(2, 1, sharex=True, gridspec_kw={"left": .093, "right": .965, "top": .816,
                                                                 "bottom": .225, "hspace": .31})
    for ax, p, letter in zip((upper, lower), PLANS, "AB"):
        data = [r for r in rows if r["plan"] == p and r["target"] != "idle"]
        if max(float(r["observation_start_s"]) for r in data) != latest[p]:
            raise ValueError("Q2 相位底表与最晚开测结果不同")
        clean_axes(ax, grid="x")
        critical = comparison["critical_chains"][p][-1]["target"]
        score_color = BLUE if p == "optimized" else MUTED
        ax.set_title(f"{letter}   {PLAN_LABEL[p]}", loc="left")
        ax.set_title(f"{critical} last at {latest[p]:.3f} s", loc="right", color=score_color)
        for r in data:
            v = scene.vehicles.index(r["vehicle"])
            for kind, start, end in PHASES:
                # 只将方案差异的两段行驶与最后一项联合观测提到前景；真实端点不动。
                emphasize = p == "optimized" and ((kind == "TRAVELING" and r["vehicle"] == "V3") or
                                                     (kind == "OBSERVING" and r["target"] == critical))
                phase_bar(ax, v, float(r[start]), float(r[end]), kind, emphasized=emphasize,
                          label=r["target"] if kind == "OBSERVING" else None)
            # 原地车次的出发和到站同刻，以空心圆标记；不为零行程虚构宽度。
            if r["depart_s"] == r["arrive_s"]:
                ax.plot(float(r["depart_s"]), v, marker="o", ms=4.2, mfc="white", mec=SECONDARY, zorder=6, clip_on=False)
        ax.axvline(latest[p], color=score_color, ls="--", lw=1.05, zorder=3)
        ax.set(xlim=(-.16, 20.35), ylim=(4.6, -.6), yticks=range(len(scene.vehicles)), yticklabels=scene.vehicles)
        ax.set_xticks(range(0, 21, 2))
        ax.get_yticklabels()[scene.vehicles.index("V3")].set(weight="bold", color=score_color)
    lower.set_xlabel("Time after release (s)")
    handles = phase_keys({kind for kind, _, _ in PHASES})
    handles.append(Line2D([], [], marker="o", mfc="white", mec=SECONDARY, ls="none", label="Depart = arrive"))
    fig.legend(handles=handles, ncols=len(handles), loc="center", bbox_to_anchor=(.5, .108),
               columnspacing=1.15, handlelength=1.35)
    footnote(fig, "The dashed lines mark the latest start, not the end of observation or reset.\n"
             "A outlines V3’s two travel legs and highlights T3 observation. In B, V3 serves T1 then T2.")
    atlas.export(fig, "q22_timeline", (config, scene.field_file, timeline_path, comparison_path),
                 "只读 optimized 和 greedy 相位；圆点标出零行程的同刻出发/到站；最晚开测与关键链从对照 JSON 核对。")


def draw(atlas: Atlas) -> None:
    """按归档场地和连续路点发布 Q2(2) 两张改进图，Q2(1) 原图不变。"""
    directory = atlas.source
    config = directory / "config.yaml"
    scene = read_scene(config)
    _draw_routes(atlas, scene, config, directory / "q22_routes.json", directory / "q22_assignments.csv")
    _draw_timeline(atlas, scene, config, directory / "q22_timeline.csv", directory / "q22_comparison.json")
