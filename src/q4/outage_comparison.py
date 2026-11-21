"""B 题第四问断联口径对照，复用主归档的初始方案、候选站、路由和重排方法。
输入 Q4 主归档和等效配置；输出到 runs/q4 下的新目录，保存连续轨迹、配对底表和汇总图。
运行：nohup python3 -B -m src.run q4 --outage-comparison <主归档目录>。
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol，2026-11-21（版本发布日期未提供）。
"""

from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import os
from pathlib import Path
from statistics import mean
import tempfile
import time

import numpy as np
import yaml

from src.q2.report import write_csv, write_json

from .candidates import Geometry, digest, scenarios
from .experiment import Task, _init_worker, _metadata, _run_group
from .physics import OUTAGE_COMPARISONS, OutageRule, load_archive
from .report import Output


def _archived_recommendations(path: Path) -> dict:
    """读取唯一的主网格逐场推荐；其它数值灵敏度和临界加密行不参与配对。"""
    scores = {}
    with path.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["model"] != "recommended" or row["sample"] != "main" or row["variant"] != "main":
                continue
            key = row["scenario_id"]
            if key in scores or row["valid"] != "True":
                raise ValueError(f"主归档的推荐结果不唯一或未通过验收：{key}")
            scores[key] = row
    return scores


def _paired_rows(scores: list[dict], reference: dict, alerts_path: Path) -> list[dict]:
    """按情景键比较同一告警集合；t_last 保留未全启时记为 60 s 的官方定义。"""
    missed = defaultdict(list)
    with gzip.open(alerts_path, "rt", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["model"] == "recommended" and row["success_started"] == "0":
                missed[row["scenario_id"], row["variant"]].append(row["alert"])
    retain = {(r["scenario_id"], r["variant"]): r for r in scores if r["model"] == "original_append"}
    paired = []
    for row in scores:
        if row["model"] != "recommended":
            continue
        case = row["scenario_id"], row["variant"]
        original = reference[case[0]]
        main_k, main_last = int(original["k"]), float(original["t_last_s"])
        k, last = row["k"], row["t_last_s"]
        unstarted = missed[case]
        if not row["valid"] or len(unstarted) != row["N"] - k:
            raise ValueError(f"对照推荐与逐告警启动记录不一致：{case}")
        score, original_score = (-k, round(last, 8)), (-main_k, round(main_last, 8))
        paired.append({**{f: row[f] for f in ("scenario_id", "variant", "category", "t_rec_s", "theta_deg", "R_m",
                                             "failed_vehicle", "N", "locked_at_dispatch", "source", "k", "eta", "t_last_s")},
                       "main_k": main_k, "main_t_last_s": main_last, "delta_t_last_s": last - main_last,
                       "official_comparison": "worse" if score > original_score else "better" if score < original_score else "same",
                       "unstarted_alerts": " ".join(unstarted), "retain_k": retain[case]["k"],
                       "retain_t_last_s": retain[case]["t_last_s"]})
    return paired


def _summarize(paired: list[dict]) -> list[dict]:
    """按断联规则、类目和恢复时刻汇总同场配对；留空表示沿该维度合并。"""
    groups = defaultdict(list)
    for row in paired:
        for category in (None, row["category"]):
            for recovery in (None, row["t_rec_s"]):
                groups[row["variant"], category, recovery].append(row)
    summary = []
    for (rule, category, recovery), rows in groups.items():
        full = [r for r in rows if r["k"] == r["main_k"] == r["N"]]
        summary.append({"variant": rule, "category": category, "t_rec_s": recovery, "cases": len(rows),
                        "all_started_main": sum(r["main_k"] == r["N"] for r in rows),
                        "all_started": sum(r["k"] == r["N"] for r in rows),
                        "mean_eta_main": mean(r["main_k"] / r["N"] for r in rows),
                        "mean_eta": mean(r["eta"] for r in rows),
                        "mean_t_last_main_s": mean(r["main_t_last_s"] for r in rows),
                        "mean_t_last_s": mean(r["t_last_s"] for r in rows),
                        **{label: sum(r["official_comparison"] == label for r in rows) for label in ("worse", "same", "better")},
                        "full_in_both": len(full),
                        "mean_delay_when_full_s": mean(r["delta_t_last_s"] for r in full) if full else None,
                        "max_delay_when_full_s": max((r["delta_t_last_s"] for r in full), default=None)})
    return summary


def _plot(path: Path, summary: list[dict]) -> None:
    """以英文论文图展示各恢复时刻的全启率（%）和最晚开测均值（s）。"""
    import matplotlib.pyplot as plt

    by_recovery = {(r["variant"], r["t_rec_s"]): r for r in summary if r["category"] is None and r["t_rec_s"] is not None}
    recovery = sorted({t for _, t in by_recovery})
    x = np.arange(len(recovery))
    labels = ("Full queue, peer confirmation", "Current trip only", "Confirm at recovery")
    colors = ("#395783", "#cc882b", "#178278")
    width = 0.25
    fig, (share, timing) = plt.subplots(1, 2, figsize=(8.2, 3.35), layout="constrained")
    for index, (rule, label, color) in enumerate(zip((OutageRule.FULL_QUEUE, *OUTAGE_COMPARISONS), labels, colors)):
        control = rule == OutageRule.FULL_QUEUE
        rows = [by_recovery[OUTAGE_COMPARISONS[0] if control else rule, t] for t in recovery]
        positions = x + width * (index - 1)
        full = [100 * r["all_started_main" if control else "all_started"] / r["cases"] for r in rows]
        latest = [r["mean_t_last_main_s" if control else "mean_t_last_s"] for r in rows]
        share.bar(positions, full, width, color=color, label=label)
        timing.bar(positions, latest, width, color=color)
    for ax, ylabel, limit in ((share, "All-alert start rate (%)", 106),
                               (timing, "Mean latest-start score (s)", 60)):
        ax.set(xlabel="Communication recovery time (s)", ylabel=ylabel, ylim=(0, limit))
        ax.set_xticks(x, [f"{t:g}" for t in recovery])
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", color="0.88", linewidth=0.6)
        ax.set_axisbelow(True)
    fig.legend(*share.get_legend_handles_labels(), loc="outside upper center", ncol=3, frameon=False, fontsize=8)
    fig.savefig(path, facecolor="white", bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)


def run_outage_comparison(reference: Path, config_path: Path, output: Path, profile: str, root: Path, command: str) -> Path:
    """只在主归档含断联的情景上运行两项对照，返回新的本地结果目录。"""
    begin = time.perf_counter()
    source_config = yaml.safe_load((reference / "config.yaml").read_text(encoding="utf-8"))
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    archive_dir = (config_path.parent / config["q2_archive"]).resolve()
    if (archive_dir != (reference / source_config["q2_archive"]).resolve() or
            {**config, "q2_archive": source_config["q2_archive"]} != source_config):
        raise ValueError("断联对照须使用主归档的第二问方案与相同求解配置")
    archive = load_archive(archive_dir)
    base = _archived_recommendations(reference / "scenario_metrics.csv")
    locations = [(float(t), float(r), float(config["alert_height_m"]))
                 for t in np.arange(0, 360, config["azimuth_step_deg"]) for r in config["ranges_m"]]
    cases = scenarios(locations, config["recovery_s"])
    if base.keys() != {case.key for case in cases} or any(r["initial_schedule_hash"] != digest(archive.hashes) for r in base.values()):
        raise ValueError("主归档的情景清单或初始调度与配置不一致")
    affected = [case for case in cases if case.recovery is not None]
    if profile == "debug":
        affected = [case for case in affected if case.location is None or case.location == locations[-1]]
    tasks = [Task(case, rule.value) for case in affected for rule in OUTAGE_COMPARISONS]

    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_outage_")
    path = Path(tempfile.mkdtemp(prefix=stamp, dir=output))
    snapshot = {**config, "q2_archive": os.path.relpath(archive_dir, path)}
    (path / "config.yaml").write_text(yaml.safe_dump(snapshot, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"q4 outage output: {path}", flush=True)

    with Output(path, archive) as store:
        def consume(task, record):
            """核对原站和 T4 目录的共同指纹，允许实际车辆状态和接续路程随执行规则改变。"""
            original = base[task.scenario.key]
            fields = ("initial_schedule_hash", "station_candidate_set_hash", "N", "tick_s", "t_rec_s", "dispatch_s")
            if any(str(record.info[field]) != original[field] for field in fields):
                raise RuntimeError(f"主结果与断联对照的共同输入不同：{task.scenario.key}")
            store.write(task, record)

        with ProcessPoolExecutor(max_workers=config["workers"], initializer=_init_worker,
                                 initargs=(archive_dir, config, profile)) as pool:
            _run_group(pool, tasks, "outage comparison", consume)

    paired = _paired_rows(store.rows, base, path / "alert_results.csv.gz")
    summary = _summarize(paired)
    write_csv(path / "paired_outage.csv", paired)
    write_csv(path / "outage_summary.csv", summary)
    _plot(path / "outage_comparison.pdf", summary)
    record = _metadata(root, config_path, config, profile, command, archive, Geometry(archive),
                       time.perf_counter() - begin,
                       cases={"reference_main": len(base), "outage": len(affected), "paired_runs": len(tasks)})
    record.update({"reference_archive": str(reference.relative_to(root)) if reference.is_relative_to(root) else str(reference),
                   "reference_inputs_sha256": {name: hashlib.sha256((reference / name).read_bytes()).hexdigest()
                                              for name in ("config.yaml", "scenario_metrics.csv")},
                   "outage_rules": OUTAGE_COMPARISONS,
                   "ai_assistance": "OpenCode / Magpie group/gpt-6.2-sol，2026-11-21（版本发布日期未提供）"})
    write_json(path / "run_record.json", record)
    return path
