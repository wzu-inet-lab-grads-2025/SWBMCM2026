"""B 题第二问报告：从最终方案恢复路点、回放五车事件并绘制图表。
输入已选站和场地，输出 runs/q2 的路线 JSON、逐车表和内部复核说明。
运行：nohup python3 -B -m src.run q2 --profile full
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol（版本发布日期未提供），2026-11-20。
"""

from collections import Counter
import csv
import json
import math
from pathlib import Path

import numpy as np

from .paths import VisibilityRouter
from .precision import pair_metrics
from .scene import Scene, pointing
from .schedule import Plan, Problem, SELECT_TOL_S


TIME_FIELDS = (
    "depart_s", "arrive_s", "turn_begin_s", "turn_end_s", "wait_s", "observation_start_s",
    "observation_end_s", "reset_begin_s", "reset_end_s", "next_departure_s",
)


def recover_plan(name: str, scene: Scene, problem: Problem, router: VisibilityRouter, plan: Plan) -> dict:
    """在中选路径上用全精度重放时序；输出各车（含待命车）的事件、路长和站位。"""
    previous, free = [None] * len(scene.vehicles), [0.] * len(scene.vehicles)
    locations = [tuple(row) for row in scene.origins.tolist()]
    assignment, routes, timeline, start_times, geometry = [], [], [], {}, {}
    uses = Counter(v for pair in plan.pairs for v, _ in pair)
    for target in plan.order:
        alert = scene.targets[target]
        legs = []
        for v, index in plan.pairs[target]:
            vehicle = scene.vehicles[v]
            site = tuple(map(float, problem.jobs[target].points[index]))
            route = router.route(locations[v], site)
            if not math.isfinite(route.length_m):
                raise RuntimeError(f"{name} {vehicle}/{alert} 无真实可用路线")
            yaw, pitch, slew = (float(row[0]) for row in pointing(scene, target, np.array([site])))
            travel = route.length_m / scene.speed_m_s
            arrive = free[v] + travel
            legs.append((v, site, slew, free[v], arrive, arrive + slew))
            routes.append({"plan": name, "vehicle": vehicle, "target": alert, "previous_target": previous[v],
                           "origin": locations[v], "destination": site, "waypoints": route.waypoints,
                           "length_m": route.length_m, "travel_s": travel})
            locations[v], previous[v] = site, alert
            # 展示中带上角度，便于定位某段时序究竟受转向还是行驶限制。
            assignment.append({"plan": name, "target": alert, "vehicle": vehicle, "site_x_m": site[0],
                               "site_y_m": site[1], "yaw_deg": yaw, "pitch_deg": pitch})
        start = max(row[-1] for row in legs)
        if not math.isclose(start, plan.starts_s[target], rel_tol=0, abs_tol=SELECT_TOL_S):
            raise RuntimeError(f"{name} {alert} 真实路点回放与路径表不一致")
        start_times[alert] = start
        end = start + scene.observation_s
        for a, (v, _, slew, depart, arrive, ready) in zip(assignment[-2:], legs):
            step = sum(leg["vehicle"] == a["vehicle"] for leg in timeline) + 1
            a.update(vehicle_task_index=step, observation_start_s=start, observation_end_s=end)
            free[v] = end + slew
            timeline.append({"plan": name, "vehicle": a["vehicle"], "target": alert, "depart_s": depart,
                             "arrive_s": arrive, "turn_begin_s": arrive, "turn_end_s": ready, "wait_s": start - ready,
                             "observation_start_s": start, "observation_end_s": end, "reset_begin_s": end, "reset_end_s": free[v],
                             "next_departure_s": free[v] if step < uses[v] else None})
        geometry[alert] = pair_metrics(legs[0][1], legs[1][1], scene.target_cfg[target].target_xyz[:2],
                                       math.radians(scene.bearing_sigma_deg))
    for v, vehicle in enumerate(scene.vehicles):
        if not uses[v]:
            timeline.append({"plan": name, "vehicle": vehicle, "target": "idle", **dict.fromkeys(TIME_FIELDS)})
    timeline.sort(key=lambda row: (row["vehicle"], row["depart_s"] or 0.0))
    routes.sort(key=lambda row: (row["vehicle"], plan.order.index(scene.targets.index(row["target"]))))
    active = [r for r in timeline if r["target"] != "idle"]
    return {"plan": name, "order": tuple(scene.targets[t] for t in plan.order), "assignments": assignment,
            "routes": routes, "timeline": timeline, "start_times_s": start_times, "latest_s": max(start_times.values()),
            "diagnostics": geometry, "last_reset_s": max(free), "total_length_m": math.fsum(r["length_m"] for r in routes),
            "total_wait_s": math.fsum(r["wait_s"] for r in active),
            "total_slew_s": math.fsum(r["turn_end_s"] - r["turn_begin_s"] for r in active),
            "total_reset_s": math.fsum(r["reset_end_s"] - r["reset_begin_s"] for r in active)}


def critical_chain(report: dict) -> list[dict]:
    """按实际晚就绪车逆向追踪一条最长链；返回按真实依赖先后列出的分项（s）。"""
    alert = max(report["start_times_s"], key=report["start_times_s"].get)
    previous = {(r["vehicle"], r["target"]): r for r in report["routes"]}
    chain = []
    while alert:
        leg = max((r for r in report["timeline"] if r["target"] == alert), key=lambda r: r["turn_end_s"])
        v = leg["vehicle"]
        p = previous[v, alert]["previous_target"]
        prior_observation_s = prior_reset_s = 0.
        if p:
            earlier = next(r for r in report["timeline"] if r["vehicle"] == v and r["target"] == p)
            prior_observation_s = earlier["observation_end_s"] - earlier["observation_start_s"]
            prior_reset_s = earlier["reset_end_s"] - earlier["reset_begin_s"]
        chain.append({"target": alert, "limiting_vehicle": v, "previous_target": p,
                      "prior_observation_s": prior_observation_s, "prior_reset_s": prior_reset_s,
                      "travel_s": leg["arrive_s"] - leg["depart_s"],
                      "slew_s": leg["turn_end_s"] - leg["turn_begin_s"], "start_s": leg["observation_start_s"]})
        alert = p
    return list(reversed(chain))


def write_json(path: Path, data) -> None:
    """写 UTF-8 JSON；禁止把无穷或 NaN 序列化成场景数据。"""
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict]) -> None:
    """按首次出现的列顺序写 CSV；空值由 CSV 留空。"""
    fields = list(dict.fromkeys(field for row in rows for field in row))
    with path.open("w", encoding="utf-8", newline="") as out:
        writer = csv.DictWriter(out, fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_plots(directory: Path, scene: Scene, q21: dict, plans: list[dict]) -> None:
    """输出 T2 双站几何、优化与基线的五车甘特图和实走折线。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch, Rectangle

    f = scene.field
    colors = dict(zip(scene.vehicles, plt.get_cmap("tab10").colors))

    def field_axes(ax) -> None:
        """绘制米制场地底图；点线留给调用处添加车辆和目标。"""
        for b in f.buildings:
            ax.add_patch(Rectangle((b.x_min, b.y_min), b.x_max - b.x_min, b.y_max - b.y_min,
                                   facecolor="#e1e5ea", edgecolor="#97a2ae"))
            ax.text((b.x_min + b.x_max) / 2, (b.y_min + b.y_max) / 2, b.id,
                    ha="center", va="center", fontsize=8, clip_on=True)
        ax.set(xlim=(-1, f.field_width + 1), ylim=(-1, f.field_height + 1), xlabel="East (m)", ylabel="North (m)", aspect="equal")

    fig, axes = plt.subplots(1, 2, figsize=(10.2, 5.0), layout="constrained")
    x_t, y_t, _ = q21["target_xyz_m"]
    stations = np.array([row["site_xy_m"] for row in q21["routes"]])
    for ax in axes:
        field_axes(ax)
        for row, (x, y) in zip(q21["routes"], stations):
            v = row["vehicle"]
            ax.plot(*np.asarray(row["waypoints"]).T, marker="o", lw=2, color=colors[v], label=f"{v} travel")
            ax.plot([x, x_t], [y, y_t], ls=":", color="#a73335")
        ax.plot(*stations.T, "--", color="#414e5a", label="Baseline")
        ax.scatter(x_t, y_t, marker="*", s=95, color="#a73335", zorder=5)
    axes[0].set_title("Vehicle approaches")
    axes[0].legend(loc="lower right", frameon=False)
    # 全场图保留起点和完整路线，局部图标明站点、基线与水平交会角。
    zoom = axes[1]
    zoom.set(xlim=(stations[:, 0].min() - 3, x_t + 13), ylim=(y_t - 8, stations[:, 1].max() + 6),
             title="T2 station geometry")
    zoom.annotate("T2 projection", (x_t, y_t), xytext=(8, -12), textcoords="offset points", color="#a73335")
    for row, (x, y) in zip(q21["routes"], stations):
        v = row["vehicle"]
        zoom.annotate(f"{v} station", (x, y), xytext=(10, -14 if v == "V1" else 7),
                      textcoords="offset points", color=colors[v])
    zoom.annotate(f"{q21['baseline_m']:.3f} m", stations.mean(axis=0), xytext=(-7, 6),
                  textcoords="offset points", ha="right", color="#414e5a")
    zoom.annotate(f"{q21['angle_deg']:.0f}°", (x_t, y_t), xytext=(-11, 7),
                  textcoords="offset points", ha="right", color="#a73335")
    fig.savefig(directory / "q21_stations.png", dpi=190, bbox_inches="tight", pad_inches=0.12)
    plt.close(fig)

    titles = {"optimized": "Optimized schedule", "greedy": "Nearest-assignment baseline"}
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.4), layout="constrained")
    for ax, plan in zip(axes, plans):
        field_axes(ax)
        ax.set_title(titles.get(plan["plan"], plan["plan"].capitalize()))
        for target, cfg in zip(scene.targets, scene.target_cfg):
            x, y, _ = cfg.target_xyz
            ax.scatter(x, y, marker="*", s=66, color="#343c48")
            ax.annotate(target, (x, y), xytext=(5, 5), textcoords="offset points", fontsize=8)
        stops, approach_x = {}, {}
        for route in plan["routes"]:
            v = route["vehicle"]
            waypoints = route["waypoints"]
            ax.plot(*np.asarray(waypoints).T, lw=1.8, color=colors[v], marker=".", markersize=4)
            stop = (v, route["destination"])
            stops.setdefault(stop, []).append(route["target"])
            if len(waypoints) > 1:
                approach_x[stop] = waypoints[-2][0]
        # 同车在同一点观察多个告警时合并标记；相邻站点的标记错开上下布置。
        per_vehicle = Counter(v for v, _ in stops)
        shown = Counter()
        for (v, site), targets in stops.items():
            below = per_vehicle[v] > 1 and shown[v] % 2 == 0
            shown[v] += 1
            # 标签放在进站线的另一侧，避免盖住短进站段。
            align_right = approach_x.get((v, site), site[0]) > site[0]
            ax.scatter(*site, edgecolor=colors[v], facecolor="white", s=35, zorder=3)
            ax.annotate(f"{v}/{' → '.join(targets)}", site, xytext=(-5 if align_right else 5, -8 if below else 6),
                        textcoords="offset points", ha="right" if align_right else "left",
                        va="top" if below else "bottom", color=colors[v], fontsize=8)
    fig.savefig(directory / "q22_routes.png", dpi=190, bbox_inches="tight", pad_inches=0.12)
    plt.close(fig)

    phases = [("Travel", "depart_s", "arrive_s", "#2c69a0"), ("Slew", "turn_begin_s", "turn_end_s", "#dd9037"),
              ("Wait", "turn_end_s", "observation_start_s", "#bbc6d1"),
              ("Observe", "observation_start_s", "observation_end_s", "#32917f"),
              ("Reset", "reset_begin_s", "reset_end_s", "#8064aa")]
    fig, axes = plt.subplots(2, 1, figsize=(9.4, 6.6), sharex=True, layout="constrained")
    for ax, plan in zip(axes, plans):
        for row in plan["timeline"]:
            y = scene.vehicles.index(row["vehicle"])
            if row["target"] == "idle":
                ax.text(0, y, "Idle", va="center", fontsize=8)
                continue
            for _, a, b, color in phases:
                ax.barh(y, row[b] - row[a], left=row[a], height=0.55, color=color, edgecolor="none")
            ax.text((row["observation_start_s"] + row["observation_end_s"]) / 2, y, row["target"],
                    va="center", ha="center", color="white", fontsize=8)
        ax.axvline(plan["latest_s"], color="#353b42", ls="--", lw=1.)
        ax.grid(axis="x", linewidth=0.4, alpha=0.4)
        ax.set(yticks=list(range(len(scene.vehicles))), yticklabels=scene.vehicles,
               title=titles.get(plan["plan"], plan["plan"].capitalize()))
        ax.invert_yaxis()
    axes[0].legend(handles=[Patch(color=color, label=label) for label, _, _, color in phases],
                   loc="lower right", ncol=5, frameon=False, fontsize=8)
    axes[-1].set_xlabel("Time after release (s)")
    fig.savefig(directory / "q22_timeline.png", dpi=190, bbox_inches="tight", pad_inches=0.12)
    plt.close(fig)
