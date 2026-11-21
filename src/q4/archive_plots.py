"""Q4 归档出图：主网格策略、恢复、E7 分层、T4 几何及三个代表时序。
读取冻结 CSV/JSONL 与 Q2 场地快照；只重建候选区的显示掩码，不改候选或计分。
运行：nohup python3 -B -m src.shared.b_figures q4，默认输出到 runs/q4/。
"""

from dataclasses import replace
import math

from matplotlib.colors import LinearSegmentedColormap
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import PercentFormatter
import numpy as np

from src.q1.config import load_config
from src.q1.region import station_masks
from src.shared.b_figures.common import (ARCHIVES, ROOT, BLUE, INK, MASK_TINT, MUTED, ORANGE, PLOT_BG,
                                          RULE, SECONDARY, Atlas, clean_axes, field_axes, footnote, json_lines,
                                          new_figure, phase_bar, phase_keys, phase_label, read_json, records)
from .physics import DISTURBANCE_S, HORIZON_S


POLICIES = ("original_append", "recommended")
LABEL = {"original_append": "Retain + append", "recommended": "Validated portfolio"}
DISTURBANCES = {"E1": "New alert", "E2": "Vehicle failure", "E3": "Outage only", "E4": "Alert + failure",
                "E5": "Alert + outage", "E6": "Failure + outage", "E7": "Alert + failure + outage"}
CASES = {"largest_gain": "e2", "slowest_full": "e7", "no_or_least_gain": "e1"}


def _policy_legend(fig, y, *, connected=False):
    """恢复图同时编码线型；其余图以外环和实点表示策略与重合。"""
    fig.legend(handles=[Line2D([], [], marker="o", ms=7.4, mfc="white", mec=SECONDARY, mew=1.15,
                                 color=SECONDARY, ls="--" if connected else "none", label=LABEL["original_append"]),
                         Line2D([], [], marker="o", ms=4.8, color=BLUE, ls="-" if connected else "none",
                                 label=LABEL["recommended"])],
               ncols=2, loc="center", bbox_to_anchor=(.5, y), columnspacing=2.1)


def _paired_dots(ax, ys, base, rec, format_value):
    """同尺度画成对记录，右/左对齐两个数值；重合时只标一次。"""
    for y, a, b in zip(ys, base, rec):
        ax.hlines(y, min(a, b), max(a, b), color="#c0c9d3", linewidth=1.1, zorder=2)
        ax.scatter(a, y, s=51, facecolor="white", edgecolor=SECONDARY, linewidth=1.22, zorder=3)
        ax.scatter(b, y, s=24, color=BLUE, zorder=4)
        if math.isclose(a, b, abs_tol=1e-8, rel_tol=0):
            values = [(b, -7 if b > ax.get_xlim()[1] - .1 * np.ptp(ax.get_xlim()) else 7, BLUE)]
        else:
            direction = 1 if b > a else -1
            values = [(a, -7 * direction, SECONDARY), (b, 7 * direction, BLUE)]
        for x, dx, color in values:
            ax.annotate(format_value(x), (x, y), xytext=(dx, 0), textcoords="offset points",
                        ha="left" if dx > 0 else "right", va="center", fontsize=9, color=color, zorder=6,
                        bbox={"fc": ax.get_facecolor(), "ec": "none", "pad": .5})


def _policy(atlas, path):
    """将正文核心对照缩为两策略、两指标；所有单模型仍留在原汇总表。"""
    table = {(r["category"], r["model"]): r for r in records(path)}
    tags, ys = tuple(DISTURBANCES), np.arange(len(DISTURBANCES))
    n = [int(table[tag, "recommended"]["scenarios"]) for tag in tags]
    full = sum(int(table[tag, "recommended"]["all_alerts_started"]) for tag in tags)
    fig = new_figure((11, 5.65), "Q4  |  Rescheduling restores starts lost to failures",
                     f"Main grid: {full:,}/{sum(n):,} all-start cases with pre-authorized queues and peer readiness confirmation.")
    left, right = fig.subplots(1, 2, sharey=True, gridspec_kw={"left": .244, "right": .963, "top": .765, "bottom": .255,
                                                            "wspace": .2, "width_ratios": [1, 1.12]})
    left.set(xlim=(.666, 1.086), xticks=[.7, .8, .9, 1], ylim=(6.6, -.6), yticks=ys,
             yticklabels=[f"{tag}  {DISTURBANCES[tag]}  ({count:,})" for tag, count in zip(tags, n)])
    left.xaxis.set_major_formatter(PercentFormatter(1, decimals=0))
    right.set(xlim=(0, 70), xticks=[0, 10, 20, 30, 40, 50, 60])
    right.tick_params(axis="y", left=False, labelleft=False)
    right.spines["left"].set_visible(False)
    for ax, metric, title, formatter in ((left, "mean_eta", "A   Mean fraction of alerts started", lambda v: f"{v:.1%}"),
                                         (right, "mean_t_last_s", "B   Mean scored latest start (s)", lambda v: f"{v:.2f}")):
        clean_axes(ax, grid="x")
        ax.set_title(title, loc="left", pad=12)
        _paired_dots(ax, ys, *[[float(table[tag, model][metric]) for tag in tags] for model in POLICIES], formatter)
    right.axvline(HORIZON_S, color=RULE, ls=":", lw=1.0, zorder=1)
    _policy_legend(fig, .137)
    footnote(fig, "Means are by category; parentheses give scenario counts. The percentage axis is zoomed.\n"
             "Incomplete cases contribute a 60 s score, not an observed start time; a score gap is not necessarily time saved.")
    atlas.export(fig, "q4_policy", (path,),
                 "仅主样本，保留并追加与逐场推荐；相同值用外环加实点；t_last 均值包含 60 s 罚值。")


def _recovery(atlas, path):
    """每个含断联类别单列一面板，以一致的秒轴消除跨类别连线遮挡。"""
    table = {(r["category"], r["model"], float(r["t_rec_s"])): r for r in records(path)}
    times = sorted({k[2] for k in table})
    fig = new_figure((9.1, 6.45), "Q4  |  Later recovery delays plans that need new orders",
                     "The main rule pre-authorizes the full queue and preserves peer readiness confirmation.")
    axes = fig.subplots(2, 2, sharex=True, sharey=True, gridspec_kw={"left": .105, "right": .97, "top": .815,
                                                                   "bottom": .226, "wspace": .13, "hspace": .44})
    for ax, tag, letter in zip(axes.flat, ("E3", "E5", "E6", "E7"), "ABCD"):
        base, rec = ([float(table[tag, m, t]["mean_t_last_s"]) for t in times] for m in POLICIES)
        n = int(table[tag, "recommended", times[0]]["scenarios"])
        clean_axes(ax)
        description = "All three disturbances" if tag == "E7" else DISTURBANCES[tag]
        ax.set_title(f"{letter}   {tag}  {description}   ·   n={n}", loc="left", fontsize=10.6)
        same = np.allclose(base, rec, rtol=0, atol=1e-8)
        ax.plot(times, base, marker="o", ms=8.0, mfc="white", mec=SECONDARY, color=SECONDARY, ls="--",
                lw=1.2, zorder=3 if same else 2)
        ax.plot(times, rec, color=BLUE, lw=0 if same else 1.95, marker="o", ms=4.8, zorder=4)
        for t, value in zip(times, rec):
            offset = 7 if same else -8
            ax.annotate(f"{value:.2f}", (t, value), xytext=(0, offset), textcoords="offset points",
                        va="top" if offset < 0 else "bottom", ha="center", color=BLUE, fontsize=9)
        if not same:
            for t, value in zip(times, base):
                ax.annotate(f"{value:.2f}", (t, value), xytext=(0, 6), textcoords="offset points", va="bottom",
                            ha="center", color=SECONDARY, fontsize=8.8,
                            bbox={"fc": PLOT_BG, "ec": "none", "pad": .4})
        if same:
            ax.text(.028, .93, "Mean scores coincide", transform=ax.transAxes, va="top", color=MUTED, fontsize=8.9)
        ax.set(xlim=(7.4, 32.6), ylim=(0, 64), xticks=times, yticks=[0, 20, 40, 60])
    fig.supylabel("Mean scored latest start (s)", x=.037, y=.521, fontsize=10.5)
    fig.supxlabel("Communication restored at (s)", y=.133, fontsize=10.5)
    _policy_legend(fig, .088, connected=True)
    footnote(fig, "The link is down from 5 s until recovery. n counts scenarios per recovery time in each panel.\n"
             "Incomplete cases score 60 s. In E6 and E7 the portfolio starts every alert at all three recovery times.")
    atlas.export(fig, "q4_recovery", (path,),
                 "E3/E5/E6/E7 共用横纵轴但分别绘制；重合的基线与推荐用同心标注，横轴为恢复时刻。")


def _e7_factors(atlas, failure_path, spatial_path):
    """把车号和目标几何分开分层，避免混淆 E7 边际均值与相同单场景比较。"""
    failure = {(r["failed_vehicle"], r["model"]): r for r in records(failure_path) if r["category"] == "E7"}
    spatial = {(float(r["theta_deg"]), float(r["R_m"])): r for r in records(spatial_path)
               if r["category"] == "E7" and r["model"] == "recommended"}
    vehicles = sorted({v for v, _ in failure})
    az = sorted({theta for theta, _ in spatial})
    ranges = sorted({r for _, r in spatial})
    values = np.array([[float(spatial[t, d]["mean_t_last_s"]) for t in az] for d in ranges])
    fig = new_figure((11.0, 4.8), "Q4  |  E7 outcomes by failed vehicle and T4 position",
                     "E7 main grid  ·  Actual times for complete cases; incomplete cases score 60 s.")
    left, right = fig.subplots(1, 2, gridspec_kw={"left": .075, "right": .968, "top": .764, "bottom": .27,
                                                   "wspace": .33, "width_ratios": [1, 1.2]})
    clean_axes(left, grid="x")
    left.set(xlim=(0, 71), xticks=[0, 10, 20, 30, 40, 50, 60], yticks=np.arange(len(vehicles)), yticklabels=vehicles,
             ylim=(4.62, -.62), xlabel="Mean scored latest start (s)")
    left.set_title("A   By failed vehicle (216 each)", loc="left")
    _paired_dots(left, range(len(vehicles)),
                 *[[float(failure[v, m]["mean_t_last_s"]) for v in vehicles] for m in POLICIES], lambda v: f"{v:.2f}")
    left.axvline(HORIZON_S, color=RULE, lw=1, ls=":", zorder=1)
    # 整个热图用顺序明度，不让定性的彩虹色误读为分组；极值另用实空符号。
    blues = LinearSegmentedColormap.from_list("e7_blues", ("#eff4f9", "#a9c9e9", "#3b83cb"))
    im = right.imshow(values, origin="lower", aspect="auto", interpolation="nearest", cmap=blues)
    right.set_title("B   Portfolio by T4 position (15 each)", loc="left")
    right.set(yticks=np.arange(len(ranges)), yticklabels=[f"{r:g}" for r in ranges], ylabel="T4 range (m)",
              xlabel="T4 azimuth (CCW from east)")
    right.set_xticks(np.arange(0, len(az), 4), [f"{v:g}°" for v in az[::4]])
    extrema = []
    for index, fill, edge in ((np.argmin(values), "white", BLUE), (np.argmax(values), ORANGE, "white")):
        r, t = np.unravel_index(index, values.shape)
        right.plot(t, r, marker="D", ms=5.2, mfc=fill, mec=edge, mew=.85, lw=0)
        extrema.append(f"{values[r, t]:.2f} s at {az[t]:g}° / {ranges[r]:g} m")
    colorbar = fig.colorbar(im, ax=right, orientation="horizontal", pad=.36, fraction=.12, aspect=24)
    colorbar.set_label("Mean latest start (s)", fontsize=9)
    colorbar.ax.tick_params(labelsize=8.6)
    _policy_legend(fig, .128)
    footnote(fig, f"A averages all alert locations and recovery times. B averages all five faults and three recovery times.\n"
              f"Open diamond: minimum {extrema[0]}; filled diamond: maximum {extrema[1]}.")
    atlas.export(fig, "q4_e7_factors", (failure_path, spatial_path),
                 "只取 E7。左为按故障车分层的两策略计分时间；右为推荐策略每个方位与距离的 15 场均值。")


def _candidate_regions(atlas, cfg_path, metric_path, input_path, configs, cases):
    """画 0.5 m 显示掩码与原目录站对端点，区别图像分辨率和站对筛选。"""
    field = load_config(cfg_path)
    fig = new_figure((9.0, 5.7), "Q4  |  T4 observability and retained station endpoints",
                     "The two E1 locations have the fewest and most observable nodes on the archived 1 m grid.")
    axes = fig.subplots(1, 2, sharey=True, gridspec_kw={"left": .079, "right": .965, "top": .805, "bottom": .223,
                                                        "wspace": .2})
    rho = field.vehicle_radius
    xx, yy = np.meshgrid(*(np.linspace(rho, size - rho, round((size - 2 * rho) / .5) + 1)
                           for size in (field.field_width, field.field_height)))
    for ax, letter, row in zip(axes, "AB", cases):
        scenario = configs[row["scenario_id"]]
        xyz = tuple(scenario["targets"][3])
        r_min = (xyz[2] - field.camera_height) / math.tan(math.radians(field.elevation_bounds_deg[1]))
        mask = np.logical_and.reduce(station_masks(xx, yy, replace(field, target_xyz=xyz, distance_lower_bound=r_min)))
        ax.contourf(xx, yy, mask, levels=[.5, 1.5], colors=[MASK_TINT], zorder=0)
        field_axes(ax, field, extent=(-3.2, field.field_width + 3.2, -3.2, field.field_height + 3.2))
        ends = np.unique(np.asarray(scenario["pairs"]["3"]).reshape(-1, 2), axis=0)
        ax.scatter(*ends.T, s=16, color=BLUE, edgecolor="white", linewidth=.43, zorder=5)
        ax.scatter(*xyz[:2], s=89, marker="*", color=ORANGE, edgecolor="white", linewidth=.7, zorder=6)
        # 星标留在精确坐标；文本居中放在星与近邻站之间，不盖住候选端点。
        right_edge = xyz[0] > .75 * field.field_width
        ax.annotate("T4", xyz[:2], xytext=(-9.5 if right_edge else 9.5, 0), textcoords="offset points",
                    ha="center", va="center", fontsize=8.7, color=ORANGE, zorder=7)
        n = int(row["t4_valid_grid_sites"])
        ax.set_title(f"{letter}   {float(row['theta_deg']):g}° / {float(row['R_m']):g} m   ·   {n:,} nodes", loc="left")
    axes[1].set_ylabel("")
    fig.legend(handles=[Patch(facecolor=MASK_TINT, label="Observable (display mask)"),
                         Line2D([], [], marker="o", ms=4, color=BLUE, ls="none", label="Retained pair endpoint"),
                         Line2D([], [], marker="*", ms=9, color=ORANGE, ls="none", label="T4 projection")],
               ncols=3, loc="center", bbox_to_anchor=(.5, .128))
    footnote(fig, "The fill is a 0.5 m display mask, not the working candidate grid. Endpoints come from the frozen catalog;\n"
             "they are not all used by the selected schedule. A’s projection lies on the east field boundary.")
    atlas.export(fig, "q4_candidate_regions", (cfg_path, metric_path, input_path),
                 "按归档 1 m 节点数选 E1 几何最少和最多的位置；0.5 m 显示掩码独立重建，站对端点直接取冻结目录。")


def score_start_list(starts: dict, row: dict) -> list[float | None]:
    """按严格 <60 s 从保存的连续启动表提取计分时刻，并核对 k、t_last。"""
    if row["inclusive_60"] != "False":
        raise ValueError("Q4 时序图只接受主计分窗口 S < 60 s")
    times = [starts.get(str(a)) for a in range(int(row["N"]))]
    accepted = [t if t is not None and t < HORIZON_S else None for t in times]
    scored = [t for t in accepted if t is not None]
    last = max(scored) if len(scored) == len(times) else HORIZON_S
    if len(scored) != int(row["k"]) or not math.isclose(last, float(row["t_last_s"]), abs_tol=1e-8, rel_tol=0):
        raise ValueError("Q4 连续回放启动时刻与归档计分不一致")
    return accepted


def _case_notes(tag, baseline, rec):
    """从归档相位提取各代表场景的关键动作；不从整数时钟反推时间。"""
    start = rec["starts_s"]
    if tag == "e2":
        interrupted = next(p["end"] for p in rec["tracks"][2] if p["kind"] == "OBSERVING")
        leg = next(p for p in rec["tracks"][4] if p["kind"] == "TRAVELING" and p["alert"] == 2 and p["end"] > p["start"])
        return ("V3 is withdrawn at 5 s; V5 replaces it for T3 after its T1 reset.",
                f"T1 counts at {start['0']:.3f} s even though V3’s withdrawal ends joint observation at {interrupted:g} s.\n"
                f"V5 holds its T1 slot through reset and departs at {leg['start']:.3f} s. The baseline’s 60 s is a penalty.",
                (4, 2, "V5 relief"))
    if tag == "e7":
        leg = next(p for p in rec["tracks"][3] if p["kind"] == "TRAVELING" and p["alert"] == 1 and p["end"] > p["start"])
        length = math.fsum(math.dist(p, q) for p, q in zip(leg["points"], leg["points"][1:]))
        return ("T4 arrives and V2 fails at 5 s. The central link is down from 5 to 30 s.",
                f"V4 drives {length:.3f} m around B4 to the fixed T2 station. T2 starts only {HORIZON_S - start['1']:.3f} s "
                "before the scoring deadline.\nThe 60 s line ends scoring, not observation or reset; the shaded tail retains both.",
                (3, 1, f"T2 detour · {leg['end'] - leg['start']:.3f} s"))
    if rec["picks"] != baseline["picks"] or any(not math.isclose(t, baseline["starts_s"][a], abs_tol=1e-8, rel_tol=0)
                                                for a, t in start.items()):
        raise ValueError("Q4 无收益代表场景的开测时刻变动")
    return ("A new alert arrives at 5 s; both policies append T4 using V1 and V5.",
            "None of the original joint starts changes in this case. V1 finishes T2 and resets before traveling to T4.", None)


def _timeline(atlas, info, tracks, scores, tag, paths):
    """每页一个情景；给两策略五车连续相位、评分读数和 60 s 后实际必要尾段。"""
    a, b = (tracks[m] for m in POLICIES)
    subtitle, note, highlight = _case_notes(tag, a, b)
    scored = {m: score_start_list(tracks[m]["starts_s"], scores[m]) for m in POLICIES}
    case = info["scenario_id"]
    titles = {"e2": "A replacement restores T3", "e7": "The last alert starts just inside the window",
              "e1": "No gain from reordering"}
    figure = new_figure((11.15, 6.72), f"Q4  |  {titles[tag]}", subtitle)
    axes = figure.subplots(2, 2, gridspec_kw={"left": .077, "right": .966, "top": .808, "bottom": .236,
                                              "height_ratios": [1, 1], "width_ratios": [4.7, 1],
                                              "hspace": .39, "wspace": .095})
    failed, recovery = info["failed_vehicle"], info["t_rec_s"]
    disturbance_ink = ORANGE if failed else SECONDARY
    # 持平例在两策略突出同一 T4，而非用色暗示推荐有益。其余例只强调替补后的共同观测。
    focus_alert = highlight[1] if highlight else info["names"].index("T4")
    # 缩短空闲占主导的两页，只截去非活动尾段；E7 保留越过评分窗的观测及复位。
    last_active = max(p["end"] for m in POLICIES for track in tracks[m]["tracks"]
                      for p in track if p["kind"] not in {"IDLE", "FAILED", "READY_WAITING"})
    xmax = max(HORIZON_S + 8, math.ceil(last_active)) if last_active > HORIZON_S else math.ceil(last_active / 5) * 5
    nice_step = 5 if xmax <= 40 else 10
    kinds = set()
    for i, (m, (ax, panel)) in enumerate(zip(POLICIES, axes)):
        clean_axes(ax, grid="x")
        if recovery is not None:
            ax.axvspan(DISTURBANCE_S, recovery, facecolor="#faf0e3", edgecolor="none", zorder=0)
        if xmax > HORIZON_S:
            ax.axvspan(HORIZON_S, xmax, facecolor="#edf0f4", edgecolor="none", zorder=1)
        for v, track in enumerate(tracks[m]["tracks"]):
            for p in track:
                start, end, kind = p["start"], min(p["end"], xmax), p["kind"]
                if end <= start:
                    continue
                kinds.add(kind)
                text = info["names"][p["alert"]] if kind == "OBSERVING" and end - start >= 2.2 else None
                emphasized = kind == "OBSERVING" and p["alert"] == focus_alert and (m == "recommended" or not highlight)
                if m == "recommended" and highlight and (v, p["alert"], kind) == (*highlight[:2], "TRAVELING"):
                    text = highlight[-1]
                    emphasized = True
                if text and start < HORIZON_S < end:
                    # 末次观测跨评分线时，文字放在较长的半段，区间本身照实画。
                    lo, hi = (start, HORIZON_S) if HORIZON_S - start > end - HORIZON_S else (HORIZON_S, end)
                    phase_label(ax, (lo + hi) / 2, v, text, kind, emphasized=emphasized)
                    text = None
                phase_bar(ax, v, start, end, kind, label=text, emphasized=emphasized)
        ax.axvline(DISTURBANCE_S, color=disturbance_ink, lw=1.05, ls="--", zorder=4)
        if failed:
            ax.scatter(DISTURBANCE_S, int(failed[1:]) - 1, s=41, marker="X", color=ORANGE,
                       edgecolor="white", lw=.7, zorder=7)
        if recovery is not None:
            ax.axvline(recovery, color=ORANGE, lw=1.1, ls=":", zorder=4)
        if xmax > HORIZON_S:
            ax.axvline(HORIZON_S, color=INK, lw=1.1, ls="--", zorder=3)
        ticks = sorted({DISTURBANCE_S, *np.arange(0, xmax + .01, nice_step)})
        ax.set(xlim=(0, xmax), xticks=ticks, ylim=(4.6, -.6),
               yticks=range(5), yticklabels=[f"V{v+1}" for v in range(5)])
        if i == 0:
            ax.tick_params(labelbottom=False)
        else:
            ax.set_xlabel("Time after release (s)")
        score = scores[m]
        official_last = float(score["t_last_s"])
        full = score["N"] == score["k"]
        result = f"{official_last:.3f} s latest start" if full else f"{official_last:g} s penalty"
        ax.set_title(f"{'AB'[i]}   {LABEL[m]}", loc="left")
        ax.set_title(f"{score['k']}/{score['N']} started   ·   {result}", loc="right", color=BLUE if full else ORANGE)
        panel.set_axis_off()
        panel.text(0, 1.04, "Counted starts", transform=panel.transAxes, fontsize=10.1, weight="semibold", va="bottom")
        for j, (name, t) in enumerate(zip(info["names"], scored[m])):
            color = ORANGE if t is None else BLUE if j == focus_alert else MUTED
            panel.text(.01, .87 - j * .212, name, transform=panel.transAxes, weight="semibold", fontsize=9.6)
            panel.text(.97, .87 - j * .212, f"{t:.3f} s" if t is not None else "Not counted", transform=panel.transAxes,
                        ha="right", color=color, fontsize=9.6)
    handles = phase_keys(kinds)
    figure.legend(handles=handles, ncols=len(handles), loc="center", bbox_to_anchor=(.53, .143),
                  columnspacing=1.14, handlelength=1.35)
    if xmax < HORIZON_S:
        note += f"\nDisplay ends at {xmax:g} s; the score still uses 60 s."
    footnote(figure, note)
    figure.text(.965, .959, case, ha="right", va="top", color=MUTED, fontsize=9.1)
    atlas.export(figure, f"q4_timeline_{tag}", paths,
                 f"{case} 主情景的 original_append 与 recommended；五车相位连续回放，核对计分并保留 E7 的窗外观测/复位。")


def draw(atlas: Atlas) -> None:
    """从哈希固定的归档数据中筛出主网格与示例，不运行 Q4 实验入口。"""
    d = atlas.source
    _policy(atlas, d / "category_summary.csv")
    _recovery(atlas, d / "recovery_summary.csv")
    _e7_factors(atlas, d / "failure_summary.csv", d / "spatial_summary.csv")
    metric_path, input_path, tracks_path, summary_path = (d / n for n in
        ("scenario_metrics.csv", "scenario_inputs.jsonl.gz", "policy_plans_and_tracks.jsonl.gz", "summary.json"))
    metrics = {(r["scenario_id"], r["model"]): r for r in records(metric_path)
               if r["sample"] == r["variant"] == "main" and r["model"] in POLICIES}
    sample = [r for (_, m), r in metrics.items() if m == "recommended" and r["category"] == "E1"]
    ranked = sorted(sample, key=lambda r: int(r["t4_valid_grid_sites"]))
    candidates = [ranked[0], ranked[-1]]
    examples = read_json(summary_path)["examples"]
    case_ids = {examples[key] for key in CASES}
    needed = {r["scenario_id"] for r in candidates} | case_ids
    configs = {r["scenario_id"]: r for r in json_lines(input_path) if r["variant"] == "main" and r["scenario_id"] in needed}
    _candidate_regions(atlas, ROOT / ARCHIVES["q2"] / "scene_config.yaml", metric_path, input_path, configs, candidates)
    tracks = {(r["scenario_id"], r["model"]): r for r in json_lines(tracks_path)
              if r["variant"] == "main" and r["scenario_id"] in case_ids and r["model"] in POLICIES}
    for key, tag in CASES.items():
        case = examples[key]
        _timeline(atlas, configs[case], {m: tracks[case, m] for m in POLICIES},
                  {m: metrics[case, m] for m in POLICIES}, tag, (metric_path, input_path, tracks_path, summary_path))
