"""第四问结果归档和论文图表：统一比较指标并输出逐情景、逐车次记录。
输入四模型回放、基线及环境记录；输出到对应 runs/q4 目录，不修改第二问。
由 src.q4.experiment 调用：nohup python3 -B -m src.run q4 --profile full。
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol，2026-11-20（版本发布日期未提供）。
"""

from collections import Counter, defaultdict
from dataclasses import asdict, replace
from functools import lru_cache
import csv
import gzip
import json
import math
from pathlib import Path
from statistics import mean, median, pstdev

from checks.q2_geometry import pair_metrics
from src.q2.report import write_csv, write_json

from .candidates import target_config
from .physics import DISTURBANCE_S, HORIZON_S, Phase
from .problem import Pick


EVENTS = {
    "TRAVELING": ("DEPART", "ARRIVE"), "SLEWING": ("SLEW_START", "SLEW_END"),
    "READY_WAITING": ("WAIT_FOR_PARTNER", "WAIT_END"), "OBSERVING": ("OBSERVATION_START", "OBSERVATION_END"),
    "RESETTING": ("RESET_START", "RESET_END"), "CANCEL_RESET": ("CANCEL_RESET_START", "CANCEL_RESET_END"),
    "IDLE": ("IDLE_START", "IDLE_END"), "FAILED": ("FAILED_STATE", "FAILED_STATE_END"),
}
TIMELINE_FIELDS = "scenario_id variant model vehicle event_index time_s event alert x_m y_m pan_deg tilt_deg phase"


def _json(value) -> str:
    """压缩行内 JSON；不把 NaN 和无穷作为数据写出。"""
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _writer(stream, fields: str):
    """以固定列顺序生成一个可逐行复查的 CSV 表。"""
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(fields.split())
    return writer


def _metric(replay, n, inclusive):
    """以连续物理时刻而非整数求解目标重算输出口径。"""
    times = [t for t in replay.starts.values() if t < HORIZON_S or (inclusive and t == HORIZON_S)]
    return len(times), len(times) / n, max(times) if len(times) == n else HORIZON_S, math.fsum(times)


def _summary_key(replay, n, inclusive):
    """同一场景内严格依官方字典序，再用辅助目标区分等价的可行调度。"""
    k, _, last, total = _metric(replay, n, inclusive)
    return not replay.valid, -k, round(last, 8), round(total, 8), replay.changes


def exported_tick_gap(plan_s: float | None, lower_s: float | None) -> float | None:
    """返回所导出方案在离散时钟上的界差（s）；缺少方案时钟或下界则不报告。"""
    return round(max(0, plan_s - lower_s), 8) if plan_s is not None and lower_s is not None else None


def _visible_phases(track):
    """省略零路程占位，连续对准同目标的转向和同一次故障各记作一段。"""
    phases = []
    for p in track:
        if p.kind == "TRAVELING" and len(p.points) == 1 and p.start == p.end:
            continue
        if phases and p.kind in {"SLEWING", "FAILED"}:
            prev = phases[-1]
            if (prev.kind, prev.alert, prev.points, prev.end, prev.pose1) == (p.kind, p.alert, p.points, p.start, p.pose0):
                phases[-1] = replace(prev, end=p.end, pose1=p.pose1)
                continue
        phases.append(p)
    return phases


def _event_rows(scene, common, info, assets, strategy, plan, tracks):
    """从保存的相位生成车事件；指令边界可标注，但不虚构运动或重复故障。"""
    fault, t_rec, dispatch = (info[field] for field in ("failed_vehicle", "t_rec_s", "dispatch_s"))
    expected = {"TRAVELING": "SLEWING", "SLEWING": "READY_WAITING", "OBSERVING": "RESETTING"}
    for v, (name, track) in enumerate(zip(scene.vehicles, tracks)):
        events = []

        def sample(at, event, prefer_before=False, *, rank=-1):
            """从原始轨迹在事件时刻取车位与姿态；事件边界按因果关系选前/后。"""
            if prefer_before:
                active = next((p for p in reversed(track) if p.start < at <= p.end), None)
            else:
                active = next((p for p in track if p.start <= at < p.end), None)
            active = active or next((p for p in reversed(track) if p.start <= at), track[0])
            clipped = active.cut(at, scene)
            events.append((at, rank, event, active.alert, clipped.points[-1], clipped.pose1, active.kind))

        sample(DISTURBANCE_S, "DISTURBANCE", prefer_before=True, rank=-6)
        if name == fault:
            sample(DISTURBANCE_S, "VEHICLE_FAILURE", rank=-5)
        if t_rec is not None:
            sample(DISTURBANCE_S, "COMMUNICATION_OUTAGE", rank=-4)
            sample(t_rec, "COMMUNICATION_RECOVERY", prefer_before=True, rank=-3)
        if strategy == "dynamic" and name != fault:
            changed = next((p for p in plan if v in p.vehicles), None)
            state = assets["states"][v]
            if changed or state["alert"] is not None and state["release"] == dispatch:
                sample(dispatch, "REASSIGN" if changed and state["alert"] != changed.alert else "UPDATE_COMMAND",
                       prefer_before=True, rank=-2)
                if changed and state["kind"] == "TRAVELING":
                    pos = changed.vehicles.index(v)
                    station = assets["pairs"][changed.alert][changed.pair][pos]
                    if station != tuple(state["remaining_route"][-1]):
                        sample(dispatch, "REROUTE", prefer_before=True, rank=-1)
        if strategy == "baseline_append" and plan and v in plan[0].vehicles:
            sample(dispatch, "APPEND_COMMAND", prefer_before=True, rank=-2)
        phases = _visible_phases(track)
        for index, phase in enumerate(phases):
            first, last = EVENTS[phase.kind]
            if index + 1 < len(phases):
                following = phases[index + 1]
                if following.kind == "FAILED" or phase.kind in expected and (following.kind != expected[phase.kind]
                                                                             or following.alert != phase.alert):
                    last = phase.kind + "_INTERRUPTED"
                if phase.kind == "TRAVELING" and following.kind == "TRAVELING" and following.alert == phase.alert:
                    last = "ROUTE_CONTINUATION"
            if phase.kind == "OBSERVING":
                last = "OBSERVING_INTERRUPTED" if phase.end < phase.start + scene.observation_s and name == fault else "OBSERVATION_END"
            events.append((phase.start, 2 * index, first, phase.alert, phase.points[0], phase.pose0, phase.kind))
            events.append((phase.end, 2 * index + 1, last, phase.alert, phase.points[-1], phase.pose1, phase.kind))
        sample(HORIZON_S, "WINDOW_END", rank=10000)
        for i, (at, _, event, a, pt, pose, phase) in enumerate(sorted(events, key=lambda item: item[:2])):
            yield (*common, name, i, at, event, None if a is None else assets["names"][a], *pt, *pose, phase)


def write_vehicle_timelines(output: Path, archive, inputs_path: Path, tracks_path: Path) -> None:
    """不求解：从给定情景输入和已验收的轨迹重写逐车事件 CSV.gz。"""
    with gzip.open(inputs_path, "rt", encoding="utf-8") as inputs:
        scenarios = {(r["scenario_id"], r["variant"]): r for r in map(json.loads, inputs)}
    with gzip.open(tracks_path, "rt", encoding="utf-8") as src, gzip.open(output, "wt", encoding="utf-8", newline="") as dst:
        writer = _writer(dst, TIMELINE_FIELDS)
        for row in map(json.loads, src):
            common = row["scenario_id"], row["variant"], row["model"]
            info = scenarios[common[:2]]
            assets = {**info, "pairs": {int(a): tuple(tuple(map(tuple, p)) for p in pairs) for a, pairs in info["pairs"].items()}}
            plan = tuple(Pick(**p) for p in row["selected"])
            tracks = tuple(tuple(Phase.from_record(p) for p in track) for track in row["tracks"])
            writer.writerows(_event_rows(archive.scene, common, info, assets, row["strategy"], plan, tracks))


class Output:
    """保存真实、完整的实验证据；大表流式压缩，小表用普通 CSV。"""

    def __init__(self, path, archive):
        """只缓存轻量对比分数，逐车时间轴与折线即时写入 gzip。"""
        self.path, self.archive = path, archive
        self.rows = []
        self.manifest = []
        self.streams = []
        self._geometry = lru_cache(maxsize=4096)(lambda p, q, t: pair_metrics(archive.check, p, q, t))

    def _open(self, filename):
        """自动选择纯文本或 gzip；压缩表解压后是 UTF-8 文本。"""
        path = self.path / filename
        fh = gzip.open(path, "wt", encoding="utf-8", newline="", compresslevel=5) if path.suffix == ".gz" else path.open("w", encoding="utf-8", newline="")
        self.streams.append(fh)
        return fh

    def __enter__(self):
        """准备按场景写出的数据表。"""
        self.inputs = self._open("scenario_inputs.jsonl.gz")
        self.plans = self._open("plans_and_tracks.jsonl.gz")
        self.validation = self._open("validation.jsonl.gz")
        self.solver = self._open("solver_log.jsonl.gz")
        self.alerts = _writer(self._open("alert_results.csv.gz"),
                              "scenario_id variant model alert status vehicle_1 vehicle_2 station_1_x station_1_y "
                              "station_2_x station_2_y baseline_m intersection_deg rmse_m observation_start_s "
                              "observation_end_s success_started locked_before_disturbance locked_at_dispatch end_note failure_reason")
        self.timeline = _writer(self._open("vehicle_timelines.csv.gz"), TIMELINE_FIELDS)
        self.trials = _writer(self._open("alns_seed_results.csv.gz"),
                              "scenario_id variant seed k eta t_last_s sum_successful_start_times_s changes solve_time_seconds "
                              "iterations improvements validity operators plan")
        return self

    def __exit__(self, kind, value, traceback):
        """即使运行失败亦刷出已写证据；仅在成功时生成整批对比表。"""
        for stream in self.streams:
            stream.close()
        if kind is None:
            write_csv(self.path / "scenario_manifest.csv", self.manifest)
            write_csv(self.path / "model_comparison.csv", self.rows)

    def _issues(self, replay, alert, info):
        """记录没有按期启动的具体策略约束，避免把未证明的事称为物理不可行。"""
        if replay.strategy.startswith("baseline"):
            if alert == 3:
                return "strict freeze / no feasible append using fixed old tasks"
            return "assigned vehicle failed; baseline forbids a substitute"
        return "not selected in the validated schedule within the horizon"

    def _alert_rows(self, common, info, assets, replay):
        """输出全部告警的站车、交会质量与启动状态，包括未选的告警。"""
        vnames = self.archive.scene.vehicles
        for a, alert in enumerate(assets["names"]):
            at = replay.starts.get(a)
            assigned = replay.picks.get(a)
            if not assigned and a < 3 and replay.strategy.startswith("baseline"):
                v1, v2 = self.archive.assignment[a]
                pair_index = 0
            elif assigned:
                v1, v2, pair_index = *assigned.vehicles, assigned.pair
            else:
                v1 = v2 = pair_index = None
            if pair_index is not None:
                p, q = assets["pairs"][a][pair_index]
                m = self._geometry(p, q, assets["targets"][a])
                geometry = (vnames[v1], vnames[v2], *p, *q, m["baseline_m"], m["angle_deg"], m["rmse_m"])
            else:
                geometry = (None,) * 9
            before = at is not None and at < DISTURBANCE_S
            success = at is not None and (at < HORIZON_S or (info["inclusive_60"] and at == HORIZON_S))
            status = ("LOCKED_BEFORE_DISTURBANCE" if before else "LOCKED_DURING_OUTAGE" if a in assets["locked"] else
                      "STARTED" if success else "UNSTARTED_AT_HORIZON")
            interrupted = before and info["failed_vehicle"] in geometry[:2] and at + self.archive.scene.observation_s > DISTURBANCE_S
            end_note = "partner withdraws at fault; surviving vehicle completes the reserved interval" if interrupted else None
            end = None if at is None else at + self.archive.scene.observation_s
            issue = None if success else self._issues(replay, a, info)
            self.alerts.writerow((*common, alert, status, *geometry, at, end, int(success), int(before),
                                  int(a in assets["locked"]), end_note, issue))

    def _record(self, name, solution, info, assets, source):
        """归档逐策略结果、解状态与界、独立回放的机器可读证据。"""
        replay, log = solution.replay, solution.log
        common = info["scenario_id"], info["variant"], name
        k, eta, last, total = _metric(replay, info["N"], info["inclusive_60"])
        latest_tick = max(replay.starts_tick.values(), default=None)
        upper_s = latest_tick * info["tick_s"] if latest_tick is not None else None
        lower_s = log.get("latest_tick_lower_s")
        # 输出的连续优选方案可以不是用于整数取等证明的 witness。
        objective_gap = exported_tick_gap(upper_s, lower_s)
        k_bound = log.get("k_bound")
        source_type = replay.strategy if source == "original_append" else source
        row = {**info, "model": name, "source": source_type, "strategy": replay.strategy, "valid": replay.valid,
               "k": k, "eta": eta, "t_last_s": last, "sum_successful_start_times_s": total,
               "schedule_change_cost": replay.changes, "solver_status": log["status"], "k_bound": k_bound,
               "k_gap": k_bound - k if k_bound is not None else None, "continuous_min_vehicle_distance_m": replay.min_distance,
               "plan_latest_tick_s": upper_s, "latest_tick_lower_s": lower_s, "optimal_latest_tick_s": log.get("optimal_latest_tick_s"),
               "latest_tick_gap_s": objective_gap, "solve_time_seconds": log.get("solve_s"), "random_seed": log.get("seed"),
               "collision_cuts": log.get("cuts")}
        self.rows.append(row)
        key = dict(zip(("scenario_id", "variant", "model"), common))
        self.solver.write(_json({**key, **log, "incumbent_k": k, "incumbent_t_last_s": last}) + "\n")
        self.validation.write(_json({**key, "valid": replay.valid, "violations": replay.violations,
                                     "recomputed_k": k, "recomputed_eta": eta, "recomputed_t_last_s": last,
                                     "timeline_consistency": replay.valid, "departure_snapshot_hash": info["vehicle_state_snapshot_hash"],
                                     "recomputed_starts_tick": replay.starts_tick, "min_center_distance_m": replay.min_distance,
                                     "closest": replay.closest}) + "\n")
        self.plans.write(_json({**key, "source": source_type, "strategy": replay.strategy, "starts_s": replay.starts,
                                "selected": [asdict(pick) for pick in replay.plan], "picks": {a: asdict(p) for a, p in replay.picks.items()},
                                "tracks": [[asdict(phase) for phase in track] for track in replay.tracks]}) + "\n")
        self._alert_rows(common, info, assets, replay)
        self.timeline.writerows(_event_rows(self.archive.scene, common, info, assets, replay.strategy, replay.plan, replay.tracks))

    def write(self, task, record):
        """写一次场景输入及六种调度，另存拥有 no-regret 回退的提交策略。"""
        from .experiment import ALGORITHMS
        info, assets = record.info, record.assets
        self.manifest.append(info)
        self.inputs.write(_json({**info, **assets}) + "\n")
        pool = (*ALGORITHMS, "original_append", "strict_frozen")
        pick = min(pool, key=lambda name: (_summary_key(record.solutions[name].replay, info["N"], info["inclusive_60"]),
                                          pool.index(name)))
        chosen = record.solutions[pick]
        portfolio_log = {**chosen.log, "source_strategy": pick, "per_algorithm_solve_s":
                         {m: record.solutions[m].log.get("solve_s", 0.0) for m in ALGORITHMS}}
        for name in ("original_append", "strict_frozen", *ALGORITHMS, "recommended"):
            solution = record.solutions[name] if name != "recommended" else type(chosen)(chosen.replay, portfolio_log)
            self._record(name, solution, info, assets, name if name != "recommended" else pick)
        for trial in record.trials:
            replay, log = trial.replay, trial.log
            k, eta, last, total = _metric(replay, info["N"], info["inclusive_60"])
            self.trials.writerow((info["scenario_id"], info["variant"], log.get("seed"), k, eta, last, total,
                                  replay.changes, log["solve_s"], log.get("iterations"), log.get("improvements"),
                                  int(replay.valid), _json(log.get("operators")), _json([asdict(p) for p in replay.plan])))


def _ranking(row):
    """每个场景的官方名次键；秒级结果不作显示舍入后再排序。"""
    return -row["k"], round(row["t_last_s"], 8)


def _group(entries, fields):
    """按需要的实验因子分组，保留同场景配对。"""
    groups = defaultdict(list)
    for r in entries:
        groups[tuple(r[f] for f in fields)].append(r)
    return groups


def _rows_for(entries, fields):
    """同时报告平均启动比例、全完成数、删失 60 s 后的均值和可行时间分布。"""
    result = []
    for keys, rows in _group(entries, fields).items():
        valid = [r for r in rows if r["valid"]]
        full = [r["t_last_s"] for r in valid if r["k"] == r["N"]]
        result.append({**dict(zip(fields, keys)), "scenarios": len(rows), "valid": len(valid), "all_alerts_started": len(full),
                       "mean_eta": mean(r["eta"] for r in valid) if valid else None,
                       "mean_t_last_s": mean(r["t_last_s"] for r in valid) if valid else None,
                       "median_t_last_all_started_s": median(full) if full else None,
                       "max_t_last_all_started_s": max(full, default=None),
                       "mean_schedule_change_cost": mean(r["schedule_change_cost"] for r in valid) if valid else None,
                       "finite_model_optimal_count": sum(r["solver_status"] == "OPTIMAL" for r in valid),
                       "no_op": sum(r["solver_status"] == "NO_OP" for r in valid),
                       "median_solver_s": median(r["solve_time_seconds"] for r in valid) if valid else None,
                       "collision_cuts": sum(r["collision_cuts"] or 0 for r in valid)})
    return result


def make_reports(path: Path, archive, geometry, rows, stats):
    """按真实运行结果生成汇总、较难与获益场景证据、图和英文论文用底表。"""
    from .experiment import ALGORITHMS, LABELS, STRATEGIES
    use = "main" if stats["profile"] == "full" else "development"
    main = [r for r in rows if r["variant"] == "main" and r["sample"] == use]
    categories = _rows_for(main, ("category", "model"))
    write_csv(path / "category_summary.csv", categories)
    write_csv(path / "failure_summary.csv", _rows_for([r for r in main if r["failed_vehicle"] and r["model"] in
                                                       ("original_append", "recommended")], ("category", "failed_vehicle", "model")))
    recovery = _rows_for([r for r in main if r["t_rec_s"] and r["model"] in ("original_append", "recommended")],
                         ("category", "t_rec_s", "model"))
    write_csv(path / "recovery_summary.csv", recovery)
    write_csv(path / "spatial_summary.csv", _rows_for([r for r in main if r["theta_deg"] is not None and r["model"] in
                                                       ("original_append", "recommended")], ("category", "theta_deg", "R_m", "model")))
    model_rows = _rows_for(main, ("model",))
    byid = {(r["scenario_id"], r["model"]): r for r in main}
    rec = [r for r in main if r["model"] == "recommended"]
    samples = defaultdict(list)
    for r in rows:
        if r["variant"] == "main" and r["model"] == "recommended":
            samples[r["sample"]].append(r)
    for model in model_rows:
        name = model["model"]
        paired = [(byid[r["scenario_id"], name], r) for r in rec]
        model["matches_best_official"] = sum(_ranking(a) == _ranking(b) for a, b in paired)
        comparison = [(a, byid[a["scenario_id"], "original_append"]) for a, _ in paired]
        model["better_than_append"] = sum(_ranking(a) < _ranking(b) for a, b in comparison)
        model["equal_to_append"] = sum(_ranking(a) == _ranking(b) for a, b in comparison)
        model["worse_than_append"] = sum(_ranking(a) > _ranking(b) for a, b in comparison)
    write_csv(path / "algorithm_summary.csv", model_rows)
    ranked = sorted((r for r in model_rows if r["model"] in ALGORITHMS),
                    key=lambda r: (-r["mean_eta"], r["mean_t_last_s"], -r["finite_model_optimal_count"], r["median_solver_s"],
                                   ALGORITHMS.index(r["model"])))
    sensitivity = []
    for r in rows:
        if r["variant"] != "main" and r["model"] == "recommended":
            baseline = byid[r["scenario_id"], "recommended"]
            sensitivity.append({**{k: r[k] for k in ("scenario_id", "category", "variant", "source", "t4_retained_pairs") if k in r},
                                "main_eta": baseline["eta"], "variant_eta": r["eta"], "main_t_last_s": baseline["t_last_s"],
                                "variant_t_last_s": r["t_last_s"], "delta_eta": r["eta"] - baseline["eta"],
                                "delta_t_last_s": r["t_last_s"] - baseline["t_last_s"]})
    write_csv(path / "sensitivity.csv", sensitivity)

    # 每个 seed 的独立输出都保留，统计不夸大恰好最好的那次结果。
    stability = []
    with gzip.open(path / "alns_seed_results.csv.gz", "rt", encoding="utf-8") as stream:
        grouped = _group(csv.DictReader(stream), ("scenario_id", "variant"))
    for (key, variant), runs in grouped.items():
        ordered = sorted((float(r["eta"]), -float(r["t_last_s"])) for r in runs)
        best, mid, worst = ordered[-1], ordered[(len(ordered) - 1) // 2], ordered[0]
        stability.append({"scenario_id": key, "variant": variant, "seeds": len(runs), "best_eta": best[0], "best_t_last_s": -best[1],
                          "median_eta_lower_order": mid[0], "median_t_last_s_lower_order": -mid[1], "worst_eta": worst[0],
                          "worst_t_last_s": -worst[1], "eta_sd": pstdev(e for e, _ in ordered), "t_last_sd_s": pstdev(-t for _, t in ordered),
                          "median_trial_runtime_s": median(float(r["solve_time_seconds"]) for r in runs)})
    write_csv(path / "alns_stability.csv", stability)

    def effect(r):
        """按完成比例改善、接着最晚开测提前量挑选有代表性的案例。"""
        b = byid[r["scenario_id"], "original_append"]
        return r["eta"] - b["eta"], b["t_last_s"] - r["t_last_s"]
    best = max(rec, key=lambda r: (*effect(r), r["scenario_id"]))
    slow = max((r for r in rec if r["k"] == r["N"]), key=lambda r: (r["t_last_s"], r["scenario_id"]), default=best)
    incomplete = [r for r in rec if r["k"] < r["N"]]
    worst = min(incomplete, key=lambda r: (r["eta"], r["scenario_id"]), default=slow)
    same = min(rec, key=lambda r: (*effect(r), r["category"] != "E3", r["scenario_id"]))
    examples = {"largest_gain": best["scenario_id"], "worst_or_slowest": worst["scenario_id"], "no_or_least_gain": same["scenario_id"],
                "slowest_full": slow["scenario_id"]}
    data = {"best_single_model": ranked[0]["model"], "algorithm_ranking": ranked, "algorithm_summary": model_rows,
            "recommended_sources": dict(Counter(r["source"] for r in rec)), "cases": stats["cases"],
            "recommended_incomplete": len(incomplete), "examples": examples,
            "sample_summaries": {key: {"cases": len(val), "mean_eta": mean(r["eta"] for r in val),
                                        "all_alerts_started": sum(r["eta"] == 1 for r in val)} for key, val in samples.items()},
            "sensitivity_changed_completion": dict(Counter(r["variant"] for r in sensitivity if r["delta_eta"])),
            "alns_nonzero_quality_sd_main": sum(r["variant"] == "main" and byid.get((r["scenario_id"], "alns")) is not None
                                              and (r["eta_sd"] > 0 or r["t_last_sd_s"] > 1e-8) for r in stability),
            "all_reports_valid": all(r["valid"] for r in rows)}
    write_json(path / "summary.json", data)
    write_csv(path / "representative_cases.csv", [{"example": key, **byid[case, model]} for key, case in examples.items()
                                                   for model in ("original_append", "strict_frozen", *ALGORITHMS, "recommended")])
    _figures(path, archive, rec, categories, recovery, byid, examples)
    _paper_tables(path, LABELS, categories, model_rows, examples, byid, data, stats)


def _paper_tables(path, labels, categories, models, examples, byid, data, stats):
    """写英文准确数据供比赛正文引用，分开主样本与敏感性。"""
    c = {(r["category"], r["model"]): r for r in categories}
    lines = ["# Problem 4 — verified experiment tables", "",
             f'Primary sample: {stats["cases"]["main_or_dev"]} scenarios. Each new alert is observed by a distinct vehicle pair. '
             "Times are in seconds and come from continuous replay. Incomplete cases have $t_{last}=60$.", "",
             "| Disturbance | Cases | Retain: mean η | Dynamic: mean η | Retain: mean t_last | Dynamic: mean t_last | Dynamic: all started |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for tag, label in labels.items():
        if (tag, "recommended") not in c:
            continue
        b, d = c[tag, "original_append"], c[tag, "recommended"]
        lines.append(f"| {label} | {d['scenarios']} | {b['mean_eta']:.4f} | {d['mean_eta']:.4f} | {b['mean_t_last_s']:.3f} | {d['mean_t_last_s']:.3f} | {d['all_alerts_started']} |")
    lines.extend(["", "| Method | Mean η | Mean t_last (s) | Matches best official scores | Finite-model proofs | Median solve time (s) |",
                  "|---|---:|---:|---:|---:|---:|"])
    for r in models:
        lines.append(f"| {r['model']} | {r['mean_eta']:.4f} | {r['mean_t_last_s']:.4f} | {r['matches_best_official']} | {r['finite_model_optimal_count']} | {r['median_solver_s']:.4f} |")
    lines.extend(["", f'Best single method: **{data["best_single_model"]}**. The recommendation takes the best validated scenario-wise result from the four methods and both retain policies.',
                  "The recommended row's solve time is for the selected method only, not the elapsed time of the combined policy. "
                  "The finite-model witness can differ from the exported plan chosen on the continuous clock.",
                  "", "| Case | Scenario ID | Retain (η, t_last) | Recommended (η, t_last) |", "|---|---|---:|---:|"])
    for key, case in examples.items():
        b, a = (byid[case, m] for m in ("original_append", "recommended"))
        lines.append(f"| {key.replace('_', ' ')} | `{case}` | ({b['eta']:.4f}, {b['t_last_s']:.3f}) | ({a['eta']:.4f}, {a['t_last_s']:.3f}) |")
    lines.extend(["", "*Figure 1. Scenario-wise performance by disturbance type.* `policy_performance.png`",
                  "", "*Figure 2. Recovery-time effect, failed-vehicle stratification, and spatial variation.* `disturbance_effects.png`",
                  "", "*Figure 3. Representative routes, vehicle states and intervals when both assigned vehicles are observing. "
                  "Withdrawal truncates the joint interval without revoking a valid start.* `representative_timeline.png`",
                  "", "*Figure 4. Observable grid and selected station-pair locations across representative T4 positions.* `candidate_regions.png`"])
    (path / "tables_and_figures.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _figures(path, archive, rec, categories, recovery, byid, examples):
    """从逐场真实结果画英文指标、时序、路线和可观测域。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    import numpy as np
    from src.q1.region import station_masks
    from .experiment import LABELS

    colors = {"original_append": "#969b9f", "strict_frozen": "#c5c8cb", "cp_sat": "#3879b8", "milp": "#eea04a",
              "alns": "#4a9b75", "insertion": "#a3659d", "recommended": "#242f48"}
    pretty = {"original_append": "Retain + append", "strict_frozen": "Strict freeze", "cp_sat": "CP-SAT", "milp": "SCIP MILP",
              "alns": "ALNS", "insertion": "Insertion", "recommended": "Validated portfolio"}

    def save(fig, name):
        """保存读图和论文插图两种格式，背景留白。"""
        for suffix in ("png", "pdf"):
            fig.savefig(path / f"{name}.{suffix}", dpi=180, bbox_inches="tight", facecolor="white")
        plt.close(fig)

    methods = ("original_append", "cp_sat", "milp", "alns", "insertion", "recommended")
    g = {(r["category"], r["model"]): r for r in categories}
    tags = [tag for tag in LABELS if (tag, "recommended") in g]
    xs = np.arange(len(tags))
    fig, axes = plt.subplots(2, 1, figsize=(10.3, 6.6), sharex=True, layout="constrained")
    for ax, metric, name in zip(axes, ("mean_eta", "mean_t_last_s"), ("Mean fraction started", "Mean latest start (s)")):
        for i, model in enumerate(methods):
            ax.bar(xs + (i - 2.5) * .13, [g[tag, model][metric] for tag in tags], .12, color=colors[model], label=pretty[model])
        ax.set_ylabel(name)
        ax.grid(axis="y", alpha=.22)
        ax.set_axisbelow(True)
    axes[0].set_ylim(0, 1.06)
    axes[0].legend(ncol=3, frameon=False, loc="lower right")
    axes[-1].set_xticks(xs, [f"{tag}\n{LABELS[tag]}" for tag in tags], fontsize=9)
    save(fig, "policy_performance")

    fig, ax = plt.subplots(2, 2, figsize=(10.5, 7.8), layout="constrained")
    for (tag, model), group in _group(recovery, ("category", "model")).items():
        if tag not in ("E3", "E5", "E6", "E7"):
            continue
        group = sorted(group, key=lambda r: r["t_rec_s"])
        col = {"E3": "#8378a6", "E5": "#3879b8", "E6": "#d37e31", "E7": "#397454"}[tag]
        line = "-" if model == "recommended" else "--"
        ax[0, 0].plot([r["t_rec_s"] for r in group], [r["mean_eta"] for r in group], line, color=col, marker="o", label=tag)
        ax[0, 1].plot([r["t_rec_s"] for r in group], [r["mean_t_last_s"] for r in group], line, color=col, marker="o")
    for a, v in zip(ax[0], ("Mean fraction started", "Mean latest start (s)")):
        a.set(xlabel="Communication restored at (s)", ylabel=v)
        a.grid(alpha=.25)
    handles, keys = ax[0, 0].get_legend_handles_labels()
    handles_by_label = dict(zip(keys, handles))
    ax[0, 0].legend(list(handles_by_label.values()), list(handles_by_label) + [], ncol=2, loc="lower left", frameon=False)
    ax[0, 1].legend([Line2D([0], [0], color=".4", ls=ls) for ls in ("-", "--")], ["Portfolio", "Retain + append"], frameon=False)
    failure = _group([r for r in rec if r["category"] == "E7"], ("failed_vehicle",))
    if failure:
        names = sorted(failed[0] for failed in failure)
        xx = np.arange(len(names))
        for i, model in enumerate(("original_append", "recommended")):
            ax[1, 0].bar(xx + (i - .5) * .35, [mean(byid[r["scenario_id"], model]["eta"] for r in failure[(v,)]) for v in names],
                         width=.33, label=pretty[model], color=colors[model])
        ax[1, 0].set(xticks=xx, xticklabels=names, xlabel="Failed vehicle (E7)", ylabel="Mean fraction started", ylim=(0, 1.05))
        ax[1, 0].legend(frameon=False, loc="lower right")
    pos = _group([r for r in rec if r["category"] == "E7"], ("theta_deg", "R_m"))
    if pos:
        tt = sorted({k[0] for k in pos})
        rr = sorted({k[1] for k in pos})
        matrix = np.array([[mean(r["t_last_s"] for r in pos[t, radius]) if (t, radius) in pos else np.nan for t in tt] for radius in rr])
        image = ax[1, 1].imshow(matrix, origin="lower", aspect="auto", cmap="viridis", interpolation="nearest")
        ax[1, 1].set(yticks=np.arange(len(rr)), yticklabels=[f"{r:g}" for r in rr], xlabel="New-alert azimuth (degrees)", ylabel="New-alert range (m)")
        show = np.arange(0, len(tt), max(1, len(tt)//6))
        ax[1, 1].set_xticks(show, [f"{tt[i]:g}" for i in show])
        fig.colorbar(image, ax=ax[1, 1], label="Mean latest start, E7 (s)")
    save(fig, "disturbance_effects")

    write_representative_timeline(path, archive, path / "scenario_inputs.jsonl.gz", path / "plans_and_tracks.jsonl.gz",
                                  examples["largest_gain"])
    candidates = [r for r in rec if r["theta_deg"] is not None and r["category"] == "E1"]
    diagnostic = [min(candidates, key=lambda r: r["t4_valid_grid_sites"]), max(candidates, key=lambda r: r["t4_valid_grid_sites"])] if candidates else []
    if diagnostic:
        keep = {r["scenario_id"] for r in diagnostic}
        with gzip.open(path / "scenario_inputs.jsonl.gz", "rt", encoding="utf-8") as stream:
            simple = {r["scenario_id"]: r for line in stream if (r := json.loads(line))["scenario_id"] in keep and r["variant"] == "main"}
        fig, axes = plt.subplots(1, 2, figsize=(9, 4.7), layout="constrained")
        for a, r in zip(axes, diagnostic):
            _draw_field(a, archive)
            target = target_config(archive, (r["theta_deg"], r["R_m"], r["H_m"]))
            xx, yy = np.meshgrid(np.linspace(1.5, 98.5, 195), np.linspace(1.5, 98.5, 195))
            view = np.logical_and.reduce(station_masks(xx, yy, target))
            a.contourf(xx, yy, view, levels=[.5, 1.5], colors=["#cbebdb"], alpha=.62)
            p = np.array(simple[r["scenario_id"]]["pairs"]["3"]).reshape(-1, 2)
            if len(p):
                a.scatter(*p.T, s=8, c="#276d79", alpha=.7, label="Retained station endpoints")
            a.scatter(*target.target_xyz[:2], s=80, marker="*", c="#bf643c", label="T4")
            a.set_title(f"Azimuth {r['theta_deg']:g}°, range {r['R_m']:g} m")
        axes[-1].legend(loc="upper right", frameon=False, fontsize=8)
        save(fig, "candidate_regions")


def _draw_field(ax, archive):
    """绘制题面场地、楼体和米制坐标轴，为路线和站点图共用。"""
    from matplotlib.patches import Rectangle

    field = archive.scene.field
    for b in field.buildings:
        ax.add_patch(Rectangle((b.x_min, b.y_min), b.x_max - b.x_min, b.y_max - b.y_min, fc="#e3e8eb", ec="#8b969f", lw=.75))
        ax.text((b.x_min + b.x_max)/2, (b.y_min + b.y_max)/2, b.id, fontsize=8, ha="center", va="center")
    ax.set(xlim=(0, field.field_width), ylim=(0, field.field_height), aspect="equal", xlabel="East (m)", ylabel="North (m)")


def joint_observation_intervals(tracks: tuple[tuple[Phase, ...], ...]) -> dict[int, tuple[float, float]]:
    """返回各告警两车都在观测的区间（s），截在 60 s；故障只缩短区间、不改变启动得分。"""
    phases = defaultdict(list)
    for track in tracks:
        for p in track:
            if p.kind == "OBSERVING":
                phases[p.alert].append(p)
    result = {}
    # 已验收轨迹每条告警有两个观测相位；故障车的相位已经在退出秒截断。
    for alert, (first, second) in phases.items():
        start, end = max(first.start, second.start), min(first.end, second.end, HORIZON_S)
        if start <= end:
            result[alert] = (start, end)
    return result


def write_representative_timeline(path: Path, archive, inputs_path: Path, tracks_path: Path, example: str) -> None:
    """从已验收轨迹生成代表图 PNG/PDF，不读取求解器时钟或名义观测结束列。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    import numpy as np

    with gzip.open(inputs_path, "rt", encoding="utf-8") as stream:
        input_row = next(r for r in map(json.loads, stream) if r["scenario_id"] == example and r["variant"] == "main")
    labels = {"original_append": "Retain + append", "recommended": "Validated portfolio"}
    with gzip.open(tracks_path, "rt", encoding="utf-8") as stream:
        data = {r["model"]: r for r in map(json.loads, stream)
                if r["scenario_id"] == example and r["variant"] == "main" and r["model"] in labels}
    fig, ax = plt.subplots(2, 3, figsize=(14.4, 8.1), gridspec_kw={"width_ratios": [1, 1.6, 1]}, layout="constrained")
    phase_colors = dict(zip(EVENTS, ("#427bb6", "#e6ae56", "#c9cbd0", "#4b9e76", "#ab72a6", "#c57766", "#eef1f6", "#41464d")))
    carcolors = ["#4477aa", "#b27828", "#228855", "#a25391", "#646a77"]
    targets = np.array(input_row["targets"])
    for y, method in enumerate(labels):
        left, center, right = ax[y]
        tracks = tuple(tuple(Phase.from_record(p) for p in track) for track in data[method]["tracks"])
        _draw_field(left, archive)
        left.scatter(*targets[:, :2].T, marker="*", c="#a34f42", s=65, zorder=4)
        for a, xy in enumerate(targets):
            left.annotate(f"T{a+1}", xy[:2], xytext=(4, 3), textcoords="offset points", fontsize=8)
        for v, track in enumerate(tracks):
            for phase in track:
                pp, t0, t1, phase_name = np.asarray(phase.points), phase.start, phase.end, phase.kind
                if phase_name == "TRAVELING" and len(pp) > 1:
                    left.plot(*pp.T, color=carcolors[v], lw=1.5, ls="-" if t0 >= input_row["dispatch_s"] else "--")
                if phase_name == "OBSERVING":
                    left.scatter(*pp[0], facecolors="white", edgecolors=carcolors[v], s=28, zorder=3)
                if t0 < HORIZON_S and t1 > t0:
                    center.barh(v, min(t1, HORIZON_S) - t0, left=t0, height=.55, color=phase_colors[phase_name])
            left.scatter(*archive.scene.origins[v], marker="s", s=18, color=carcolors[v])
            state = input_row["states"][v]
            left.scatter(*state["point"], marker="x", s=29, color=carcolors[v], zorder=5)
        center.set(yticks=range(len(tracks)), yticklabels=archive.scene.vehicles, xlim=(0, HORIZON_S), xlabel="Time (s)")
        center.invert_yaxis()
        center.axvline(DISTURBANCE_S, color=".37", ls="--", lw=1)
        if input_row["t_rec_s"]:
            center.axvspan(DISTURBANCE_S, input_row["t_rec_s"], color=".5", alpha=.07)
            center.axvline(input_row["t_rec_s"], color=".37", ls=":", lw=1)
        intervals = joint_observation_intervals(tracks)
        for a in range(len(targets)):
            if a in intervals:
                right.plot(intervals[a], [a, a], color=phase_colors["OBSERVING"], lw=3, marker="|", ms=6, solid_capstyle="butt")
            else:
                right.text(40, a, "No start", color=".5", va="center", fontsize=8)
        right.axvline(DISTURBANCE_S, color=".37", ls="--", lw=1)
        right.set(yticks=range(len(targets)), yticklabels=input_row["names"], xlim=(0, HORIZON_S),
                  ylim=(len(targets) - .5, -.5), xlabel="Time (s)")
        left.set_title(labels[method])
    ax[0, 1].set_title(f"Vehicle timelines — {example}", fontsize=10)
    ax[0, 2].set_title("Both vehicles observing", fontsize=10)
    ax[-1, 1].legend([Patch(color=phase_colors[p]) for p in ("TRAVELING", "SLEWING", "READY_WAITING", "OBSERVING", "RESETTING", "FAILED")],
                     ["Travel", "Slew", "Wait", "Observe", "Reset", "Failed"], ncol=3, frameon=False, fontsize=8)
    for suffix in ("png", "pdf"):
        fig.savefig(path / f"representative_timeline.{suffix}", dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
