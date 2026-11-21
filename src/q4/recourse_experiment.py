"""B 题第四问连续重调批量对照，使用同一初排和主情景比较断联执行规则。
输入 Q4 主归档、Q2 初始方案及可选队友对照底表；保存逐车轨迹和配对统计。
运行：nohup python3 -B -m src.run q4 --recourse <Q4 主归档目录>。
"""

from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
import csv
from dataclasses import asdict, replace
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import math
import os
from pathlib import Path
from statistics import mean
import tempfile
import time

import numpy as np
import yaml

from checks.q4_replay import Validator
from src.q2.report import write_csv, write_json
from src.shared.wall_safety import execution_wall_budget

from .candidates import CandidateBank, Geometry, digest, initial_schedule_hash, prefix_hash, scenarios
from .experiment import LABELS, Record, Task, _metadata
from .heuristics import Solution
from .outage_comparison import _archived_recommendations
from .physics import OUTAGE_COMPARISONS, Clock, OutageRule, build_prefix, load_archive
from .problem import Instance, Pick
from .recourse import SiteTable, propose
from .report import Output, _ranking, _rows_for, write_representative_timeline


RECOURSE_MODELS = ("recourse", "fixed_stations")
RULE_LABELS = {OutageRule.CURRENT_TRIP_ONLY: "Current trip\nonly",
               OutageRule.CONFIRM_AFTER_RECOVERY: "Confirm at\nrecovery",
               OutageRule.STRICT_OUTAGE: "Both outage\nlimits"}


class RecourseWorker:
    """分组复用同一目标场地的候选、路长和不同恢复事件的实际快照。"""

    def __init__(self, archive_dir, config):
        """按原题和旧归档配置构建唯一的避障路由、仅追加候选和评分时钟。"""
        self.settings = config["recourse"]
        wall = self.settings.get("wall_clearance_m", 0.0)
        self.archive = load_archive(archive_dir, wall_clearance_m=wall, plan=self.settings.get("q2_plan", "optimized"),
                                    waypoint_decimals=self.settings.get("q2_waypoint_decimals"))
        self.geometry = Geometry(self.archive, wall_clearance_m=wall)
        angle = self.settings.get("minimum_intersection_gap_deg", 0.0)
        self.bank = CandidateBank(self.archive, self.geometry, config,
                                  min_sine=math.sin(math.radians(angle)) if angle else None)
        self.clock = Clock(config["tick_s"])
        self.prefix = lru_cache(maxsize=64)(
            lambda f, t, rule: build_prefix(self.archive, f, t, outage_rule=rule))

    def _record(self, task, prefix, table):
        """把连续搜索的候选装入原回放接口，重算至少 3 m 车距和真实启动。"""
        begin = time.perf_counter()
        proposed = propose(table, prefix, self.settings)
        search_s = time.perf_counter() - begin
        scenario, arc, catalog = task.scenario, self.archive, table.baseline_catalog
        pairs = {a: [pair] for a, pair in enumerate(arc.pairs)}
        baseline_pairs = len(catalog.pairs) if catalog else 0
        if catalog:
            pairs[3] = list(catalog.pairs)
        index = {a: {pair: i for i, pair in enumerate(options)} for a, options in pairs.items()}
        choices = {}
        for model, (branches, counts) in proposed.items():
            plans = []
            for branch in branches:
                converted = []
                for p in branch.visits:
                    a, v, w = p.alert, p.first, p.second
                    pair = table.points[p.left], table.points[p.right]
                    if pair[::-1] in index[a]:
                        pair, v, w = pair[::-1], w, v
                    if pair not in index[a]:
                        index[a][pair] = len(pairs[a])
                        pairs[a].append(pair)
                    converted.append(Pick(a, index[a][pair], v, w))
                plans.append((branch, tuple(converted)))
            choices[model] = plans, counts
        if catalog:
            expanded = tuple(pairs[3])
            catalog = replace(catalog, pairs=expanded, sha256=digest(expanded))
        ins = Instance(arc, self.geometry, scenario, prefix, self.clock, catalog,
                       extra_pairs={a: tuple(pairs[a]) for a in range(3)}, planning_tables=False)
        check = Validator(ins, enforce_tick_window=False)
        start_baselines = time.perf_counter()
        frozen, append = check.original(), check.append(pair_count=baseline_pairs)
        if not frozen.valid or not append.valid:
            raise RuntimeError(f"{scenario.key}/{task.variant} 保留原计划的连续回放失败")
        baseline_log = {"status": "BASELINE", "solve_s": time.perf_counter() - start_baselines}
        solutions = {"original_append": Solution(append, baseline_log), "strict_frozen": Solution(frozen, baseline_log)}
        for model, (plans, counts) in choices.items():
            validation_begin, rejected = time.perf_counter(), []
            for branch, plan in plans:
                attempt = check.dynamic(plan)
                if not attempt.valid:
                    rejected.append(attempt.violations)
                    continue
                _, k, _, _ = attempt.metrics(ins)
                if k != len(plan) + len(prefix.locked) or not math.isclose(
                        max(attempt.starts.values(), default=0), branch.latest, abs_tol=2e-8, rel_tol=0):
                    raise RuntimeError(f"{scenario.key}/{task.variant}/{model} 连续预测与独立回放时间不一致")
                log = {"status": "HEURISTIC" if ins.pending else "NO_OP", **counts,
                       "solve_s": search_s + time.perf_counter() - validation_begin,
                       "predicted_candidates": len(plans), "rejected_before_selection": len(rejected)}
                solutions[model] = Solution(attempt, log)
                break
            if model not in solutions:
                raise RuntimeError(f"{scenario.key}/{task.variant}/{model} 无可验收后缀：{rejected[-1:]}")
        sc = scenario
        info = {"scenario_id": sc.key, "category": sc.category, "disturbance_type": LABELS[sc.category],
                "sample": sc.sample, "variant": task.variant, "theta_deg": sc.theta, "R_m": sc.radius, "H_m": sc.height,
                "failed_vehicle": arc.scene.vehicles[sc.failed] if sc.failed is not None else None,
                "failure_probability": 0.2 if sc.failed is not None else None, "t_rec_s": sc.recovery,
                "outage_duration_s": sc.recovery - 5 if sc.recovery is not None else None, "dispatch_s": sc.dispatch,
                "N": len(ins.names), "tick_s": self.clock.tick, "inclusive_60": False,
                 "q2_plan": arc.plan, "initial_schedule_hash": initial_schedule_hash(arc),
                 "vehicle_state_snapshot_hash": prefix_hash(prefix),
                "station_candidate_set_hash": digest(ins.pairs), "path_cache_hash": ins.path_hash,
                "locked_at_dispatch": tuple(ins.names[a] for a in sorted(prefix.locked)),
                "t4_baseline_pairs": baseline_pairs, "recourse_field_points": len(table.points)}
        assets = {"states": [asdict(s) for s in prefix.states], "locked": prefix.locked,
                  "pairs": ins.pairs, "targets": ins.targets, "names": ins.names}
        return Record(info, solutions, [], assets)

    def run(self, tasks):
        """每组共享新告警坐标，实际可用候选仅由本场恢复快照和静态场地决定。"""
        prefixes = [self.prefix(t.scenario.failed, t.scenario.dispatch, OutageRule.FULL_QUEUE
                                if t.variant == "main" else OutageRule(t.variant)) for t in tasks]
        table = SiteTable(self.archive, self.geometry, self.bank, tasks[0].scenario.location, prefixes, self.settings)
        return [self._record(task, prefix, table) for task, prefix in zip(tasks, prefixes)]


_WORKER: RecourseWorker | None = None


def _init(archive_dir, config):
    """进程池入口，为每个工作进程建立独立的路线缓存。"""
    global _WORKER
    _WORKER = RecourseWorker(archive_dir, config)


def _work(tasks):
    """进程池序列化入口，以目标位置为单位回传独立验收的情景。"""
    return _WORKER.run(tasks)


def _peer_scores(peer_path, baseline):
    """只读匹配同一份旧 Q2 方案的逐场成绩，先核验清单和原分数。"""
    if peer_path is None:
        return {}
    with peer_path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    peer = {r["peer_id"]: r for r in rows}
    if len(rows) != len(peer) or peer.keys() != baseline.keys():
        raise ValueError("队友对照与主样本情景键不一致")
    for key, row in peer.items():
        fixed = baseline[key]
        if (row["N"] != fixed["N"] or row["recommended_k"] != fixed["k"] or not math.isclose(
                float(row["recommended_s"]), float(fixed["t_last_s"]), rel_tol=0, abs_tol=1e-8)):
            raise ValueError(f"队友对照使用了不同的旧初始计划或评分：{key}")
    scores = {}
    for key, row in peer.items():
        n, k, last = int(row["N"]), int(row["local_on_peer_k"]), float(row["local_on_peer_s"])
        if not (0 <= k <= n and (last == 60.0 if k < n else 0 <= last < 60.0)):
            raise ValueError(f"队友对照无法按严格 60 s 口径比较：{key}")
        scores[key] = k, last
    return scores


def _comparisons(path, archive, rows, baseline, peer, same_start, outage_rules):
    """从保存的官方评分生成逐场配对、类别和恢复时刻汇总。"""
    rec = [r for r in rows if r["model"] == "recommended"]
    main = [r for r in rows if r["variant"] == "main"]
    winners = {r["scenario_id"]: _ranking(r) for r in rec if r["variant"] == "main"}
    totals = _rows_for(main, ("model",))
    for row in totals:
        row["matches_best_official"] = sum(_ranking(r) == winners[r["scenario_id"]]
                                           for r in main if r["model"] == row["model"])
    write_csv(path / "algorithm_summary.csv", totals)
    variants = _rows_for(rows, ("variant", "model"))
    write_csv(path / "protocol_summary.csv", variants)
    write_csv(path / "category_summary.csv", _rows_for(rows, ("variant", "category", "model")))
    write_csv(path / "recovery_summary.csv", _rows_for([r for r in rows if r["t_rec_s"] is not None],
                                                        ("variant", "t_rec_s", "model")))
    by_case = {(r["scenario_id"], r["variant"], r["model"]): r for r in rows}
    comparisons = []
    paired = []
    for row in rec:
        key = row["scenario_id"]
        case = key, row["variant"]
        pair = {f: row[f] for f in ("scenario_id", "variant", "category", "theta_deg", "R_m", "H_m", "failed_vehicle",
                                    "t_rec_s", "dispatch_s", "N", "k", "t_last_s", "k_full_observation", "source", "locked_at_dispatch",
                                    "vehicle_state_snapshot_hash")}
        for model in (*RECOURSE_MODELS, "original_append", "strict_frozen"):
            other = by_case[*case, model]
            pair.update({f"{model}_k": other["k"], f"{model}_t_last_s": other["t_last_s"],
                         f"{model}_k_full_observation": other["k_full_observation"]})
        comparisons.append(pair)
        if row["variant"] != "main":
            continue
        old = baseline[key]
        k, last = peer.get(key, (None, None))
        paired.append({**pair, "same_initial_schedule": same_start, "previous_k": int(old["k"]),
                        "previous_t_last_s": float(old["t_last_s"]), "peer_k": k, "peer_t_last_s": last})
    write_csv(path / "paired_policies.csv", comparisons)
    write_csv(path / "paired_main.csv", paired)
    example = max(paired, key=lambda r: (r["recourse_k"] - r["fixed_stations_k"],
                                         r["fixed_stations_t_last_s"] - r["recourse_t_last_s"], r["scenario_id"]))
    worst = max(paired, key=lambda r: (r["N"] - r["k"], r["t_last_s"], r["scenario_id"]))
    examples = [("fixed_station_gain", example), ("latest_main_start", worst)]
    if OutageRule.STRICT_OUTAGE in outage_rules:
        strict = [r for r in comparisons if r["variant"] == OutageRule.STRICT_OUTAGE]
        worst_strict = max(strict, key=lambda r: (r["N"] - r["k"], r["t_last_s"], r["scenario_id"]))
        examples.append(("latest_strict_start", worst_strict))
        write_representative_timeline(path, archive, path / "scenario_inputs.jsonl.gz", path / "plans_and_tracks.jsonl.gz",
                                      worst_strict["scenario_id"], variant=OutageRule.STRICT_OUTAGE,
                                      stem="strict_outage_timeline", labels={"strict_frozen": "Strictly retained plan",
                                                                           "recommended": "Validated rescheduling"})
    write_csv(path / "representative_cases.csv", [{"example": tag, **row} for tag, row in examples])
    write_representative_timeline(path, archive, path / "scenario_inputs.jsonl.gz", path / "plans_and_tracks.jsonl.gz",
                                  example["scenario_id"], labels={"fixed_stations": "Fixed original stations",
                                                                   "recourse": "Flexible original stations"})
    _plot(path, paired, variants, outage_rules)


def _plot(path, paired, variants, outage_rules):
    """比较主清单和断联变体的计分时间，未全启按 60 s 计。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fixed, flexible = "#bc731f", "#2060a0"
    fig, (timing, protocol) = plt.subplots(1, 2, figsize=(10, 3.55), layout="constrained")
    reference = "Previous portfolio" if paired[0]["same_initial_schedule"] else "Previous portfolio, prior initial plan"
    series = [(reference, [r["previous_t_last_s"] for r in paired], "#707b88")]
    if paired[0]["peer_t_last_s"] is not None:
        series.append(("Peer branch", [r["peer_t_last_s"] for r in paired], "#508278"))
    series.extend((("Fixed-station search", [r["fixed_stations_t_last_s"] for r in paired], fixed),
                   ("Flexible-station search", [r["recourse_t_last_s"] for r in paired], flexible)))
    x = np.arange(2)
    width = 0.74 / len(series)
    for i, (name, values, color) in enumerate(series):
        bars = timing.bar(x + width * (i - (len(series) - 1) / 2), (mean(values), max(values)),
                           width, label=name, color=color)
        timing.bar_label(bars, fmt="%.1f", fontsize=8, padding=2)
    timing.set(xticks=x, xticklabels=("Mean", "Worst case"), ylabel="Latest-start score (s)", ylim=(0, 73),
               title="Main scenarios")
    timing.legend(frameon=False, fontsize=8, loc="upper left")
    subtotals = {(r["variant"], r["model"]): r for r in variants}
    policies = (("fixed_stations", "Fixed stations", fixed), ("recourse", "Flexible stations", flexible))
    xp = np.arange(len(outage_rules))
    for i, (model, label, color) in enumerate(policies):
        means = [subtotals[rule, model]["mean_t_last_s"] for rule in outage_rules]
        bars = protocol.bar(xp + (i - 0.5) * 0.34, means, width=0.34, color=color, label=label)
        protocol.bar_label(bars, fmt="%.1f", fontsize=8, padding=2)
    protocol.set(xticks=xp, xticklabels=[RULE_LABELS[r] for r in outage_rules], ylim=(0, 64),
                 ylabel="Mean latest-start score (s)", title="Outage rule variants")
    protocol.legend(frameon=False, fontsize=8, loc="upper left")
    for ax in (timing, protocol):
        ax.grid(axis="y", color="#dde2e8", linewidth=0.6)
        ax.set_axisbelow(True)
        ax.spines[["top", "right"]].set_visible(False)
    fig.savefig(path / "recourse_comparison.pdf", bbox_inches="tight")
    fig.savefig(path / "recourse_comparison.png", dpi=220, bbox_inches="tight")
    plt.close(fig)


def run_recourse(reference: Path, settings_path: Path, output: Path, profile: str, root: Path, command: str,
                  initial_schedule: Path | None, peer_path: Path | None) -> Path:
    """保持原题主清单，重跑主规则和所配断联规则，保存独立回放所需证据。"""
    begin = time.perf_counter()
    ref_config = reference / "config.yaml"
    config = yaml.safe_load(ref_config.read_text(encoding="utf-8"))
    settings = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    for key in ("workers", "beam_width", "stations_per_vehicle", "replay_candidates"):
        if type(settings[key]) is not int or settings[key] < 1:
            raise ValueError(f"{key} 必须为正整数")
    counts = settings["alternatives_per_pair"]
    counts = [counts] if type(counts) is int else counts
    if not isinstance(counts, list) or not counts or any(type(n) is not int or n < 1 for n in counts):
        raise ValueError("alternatives_per_pair 须为正整数或非空的正整数列表")
    settings["alternatives_per_pair"] = list(dict.fromkeys(counts))
    outage_rules = tuple(OutageRule(r) for r in settings.get("outage_rules", OUTAGE_COMPARISONS))
    if not outage_rules or len(outage_rules) != len(set(outage_rules)) or any(r not in RULE_LABELS for r in outage_rules):
        raise ValueError("outage_rules 须为非空且不重复的断联变体列表")
    angle = settings.get("minimum_intersection_gap_deg", 0.0)
    if not math.isfinite(angle) or not 0 <= angle < 90:
        raise ValueError("最小交会角距退化余量须在 [0,90) 度")
    wall = settings.get("wall_clearance_m", 0.0)
    safety = execution_wall_budget(settings) if "execution_error_bound_m" in settings else {}
    if wall and peer_path is not None:
        raise ValueError("全程墙距工况不得引用未按同一净空验收的历史队友分数")
    archive_dir = initial_schedule or (reference / config["q2_archive"]).resolve()
    archive = load_archive(archive_dir, wall_clearance_m=wall, plan=settings.get("q2_plan", "optimized"),
                           waypoint_decimals=settings.get("q2_waypoint_decimals"))
    geometry = Geometry(archive, wall_clearance_m=wall)
    baseline = _archived_recommendations(reference / "scenario_metrics.csv")
    locations = [(float(t), float(r), float(config["alert_height_m"]))
                 for t in np.arange(0, 360, config["azimuth_step_deg"]) for r in config["ranges_m"]]
    cases = scenarios(locations, config["recovery_s"])
    if {case.key for case in cases} != baseline.keys():
        raise ValueError("主归档场景清单与配置不一致")
    same_start = {r["initial_schedule_hash"] for r in baseline.values()} == {initial_schedule_hash(archive)}
    if peer_path is not None and not same_start:
        raise ValueError("固定队友对照仅能用于相同的 Q2 初始方案")
    peer = _peer_scores(peer_path, baseline)
    if profile == "debug":
        cases = [case for case in cases if case.location is None or case.location in (locations[0], (90, 50, 50))]
    groups = defaultdict(list)
    for case in cases:
        groups[case.location].append(Task(case))
        if case.recovery is not None:
            groups[case.location].extend(Task(case, rule.value) for rule in outage_rules)
    config.update({"recourse": settings, "workers": settings["workers"]})

    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_recourse_")
    path = Path(tempfile.mkdtemp(prefix=stamp, dir=output))
    snapshot = {**config, "q2_archive": os.path.relpath(archive_dir, path), "recourse_models": list(RECOURSE_MODELS)}
    (path / "config.yaml").write_text(yaml.safe_dump(snapshot, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"q4 recourse output: {path}", flush=True)
    count, total = 0, sum(map(len, groups.values()))
    with Output(path, archive, algorithms=RECOURSE_MODELS) as store:
        with ProcessPoolExecutor(max_workers=min(config["workers"], len(groups)), initializer=_init,
                                 initargs=(archive_dir, config)) as pool:
            for i, (tasks, records) in enumerate(zip(groups.values(), pool.map(_work, groups.values())), 1):
                for task, record in zip(tasks, records, strict=True):
                    store.write(task, record)
                count += len(tasks)
                if i % 6 == 0 or count == total:
                    print(f"q4 recourse: {count}/{total}", flush=True)
    _comparisons(path, archive, store.rows, baseline, peer, same_start, outage_rules)
    metadata = _metadata(root, settings_path, config, profile, command, archive, geometry,
                         time.perf_counter() - begin, cases={"main": len(cases), "outage_variants": total - len(cases)})
    metadata.pop("exact_budget_s")
    metadata.update({"same_initial_schedule_as_fixed_reference": same_start, "q2_plan": archive.plan,
                     "initial_schedule_hash": initial_schedule_hash(archive),
                     "outage_rules": outage_rules, "solver": "continuous time beam search",
                     "reference_input_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                                for p in (ref_config, reference / "scenario_metrics.csv")},
                      "peer_comparison_sha256": hashlib.sha256(peer_path.read_bytes()).hexdigest() if peer_path else None})
    if wall:
        metadata["required_wall_clearance_m"] = wall
    if safety:
        metadata["wall_safety"] = safety
        name = "src/shared/wall_safety.py"
        metadata["source_sha256"][name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    write_json(path / "run_record.json", metadata)
    return path
