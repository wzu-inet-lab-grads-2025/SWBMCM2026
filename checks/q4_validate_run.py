"""第四问独立验收：复核保存的轨迹、锁定规则和逐情景择优，不调用求解器。
输入为完整运行目录，或用 --archive 指定 Git 结果目录；输出 JSON 验收记录。
运行：nohup python3 -B -m checks.q4_validate_run runs/q4/<运行编号>。
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol，2026-11-20（版本发布日期未提供）。
"""

import argparse
from collections import Counter
import csv
from functools import lru_cache
import gzip
import hashlib
import json
import math
from pathlib import Path
from statistics import mean
import time

import yaml

from checks.q2_geometry import check_route, pair_metrics, station_pointing
from checks.q4_replay import separation
from src.q4.candidates import digest, prefix_hash, target_config
from src.q4.physics import DISTURBANCE_S, EAST, HORIZON_S, OUTAGE_COMPARISONS, OutageRule, Phase, build_prefix, load_archive, wrap


TIME_TOL = 2e-8  # 仅处理米/度转秒的浮点尾差，不容许 60 s 边界放宽。
POLICIES = ("original_append", "strict_frozen", "recommended")
MODELS = (*POLICIES, "cp_sat", "milp", "alns", "insertion")
PHASES = {"TRAVELING", "SLEWING", "READY_WAITING", "OBSERVING", "RESETTING", "CANCEL_RESET", "IDLE", "FAILED"}


def _rows(path: Path):
    """逐行读取压缩 JSON Lines，避免同时载入所有轨迹。"""
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        yield from map(json.loads, fh)


def _csv(path: Path):
    """读取 UTF-8 CSV；在压缩表中保持相同接口。"""
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", newline="") as fh:
        yield from csv.DictReader(fh)


def _key(row: dict) -> tuple[str, str, str]:
    """识别同一场景、实验口径和策略的唯一归档主键。"""
    return row["scenario_id"], row["variant"], row["model"]


def _case(row: dict) -> tuple[str, str]:
    """实验口径是主键的一部分，加密和灵敏度不得混入主样本。"""
    return row["scenario_id"], row["variant"]


def _require(condition: bool, message: str):
    """验收失败就终止并显示可定位的原因，不允许静默略过记录。"""
    if not condition:
        raise ValueError(message)


def _index(rows, key):
    """建立唯一索引；不允许重复行覆盖先前证据。"""
    result = {}
    for row in rows:
        ident = key(row)
        _require(ident not in result, f"重复主键：{ident}")
        result[ident] = row
    return result


def _close(x: float, y: float) -> bool:
    """时序表中的数值相等用固定绝对容差；是否成功另用严格不等号。"""
    return abs(x-y) <= TIME_TOL


class Evidence:
    """从正式文件重新计算安全、物理时间和启动；完全不调用优化器及其矩阵。"""

    def __init__(self, archive):
        """取题面和存档第二问数据，缓存可能在多个策略中重复的几何复核。"""
        self.archive = archive
        self.scene = archive.scene
        self.route = lru_cache(maxsize=16384)(lambda points: check_route(archive.check, points, points[0]))
        self.station = lru_cache(maxsize=16384)(lambda point, target: station_pointing(archive.check, point, target))
        self.pair = lru_cache(maxsize=8192)(lambda first, second, target: pair_metrics(archive.check, first, second, target))
        self.prefix = lru_cache(maxsize=64)(lambda f, t, rule: build_prefix(archive, f, t, outage_rule=rule))

    def _audit_input(self, row: dict):
        """核查统一基准、原题时刻、候选哈希和状态快照与归档旧命令一致。"""
        failed = self.scene.vehicles.index(row["failed_vehicle"]) if row["failed_vehicle"] is not None else None
        dispatch = row["dispatch_s"]
        recovery = row["t_rec_s"]
        _require((recovery is None and dispatch == DISTURBANCE_S) or
                 (recovery == dispatch and DISTURBANCE_S < dispatch <= 30), "新指令时刻或恢复时刻越界")
        _require(row["outage_duration_s"] == (None if recovery is None else recovery - DISTURBANCE_S),
                 "停联历时不等于恢复时刻减去扰动时刻")
        names = self.scene.targets
        targets = tuple(c.target_xyz for c in self.scene.target_cfg)
        if row["theta_deg"] is not None:
            loc = row["theta_deg"], row["R_m"], row["H_m"]
            _require(0 <= loc[0] < 360 and 30 <= loc[1] <= 50 and loc[2] == 50, "新告警参数越界")
            names = (*names, "T4")
            targets = (*targets, target_config(self.archive, loc).target_xyz)
        _require(tuple(row["names"]) == names and tuple(map(tuple, row["targets"])) == targets and row["N"] == len(names),
                 "告警清单或坐标不符合输入和 Q2 基准")
        variant = row["variant"]
        rule = OutageRule(variant) if variant in OUTAGE_COMPARISONS else OutageRule.FULL_QUEUE
        _require(rule == OutageRule.FULL_QUEUE or recovery is not None, "执行口径对照缺少断联")
        rederived = self.prefix(failed, dispatch, rule)
        _require(row["initial_schedule_hash"] == digest(self.archive.hashes), "Q2 归档哈希变化")
        _require(row["station_candidate_set_hash"] == digest(row["pairs"]), "候选站对哈希变化")
        _require(row["vehicle_state_snapshot_hash"] == prefix_hash(rederived), "动态状态哈希变化")
        _require(row["vehicle_state_snapshot_hash"] == digest({"dispatch": dispatch, "failed": failed, "states": row["states"],
                                                                 "locked": row["locked"]}), "导出动态状态与哈希不一致")
        return rederived

    def audit(self, info: dict, result: dict, summary: dict, validation: dict | None = None):
        """复核一套实际执行计划：重算 LOS、楼距、车距、有效启动及跨阶段的连续性。"""
        prefix = self._audit_input(info)
        failed = prefix.failed
        starts = {int(a): at for a, at in result["starts_s"].items()}
        picks = {int(a): p for a, p in result["picks"].items()}
        _require(starts.keys() == picks.keys(), "成功启动时间和双车分配不对应")
        strategy = result["strategy"]
        _require(strategy in {"dynamic", "baseline_append", "baseline_frozen"} and strategy == summary["strategy"]
                 and result["source"] == summary["source"], "轨迹与指标表中的调度策略或来源不一致")
        if result["model"] in POLICIES[:2]:
            _require(strategy.startswith("baseline") and (result["model"] != "strict_frozen" or strategy == "baseline_frozen"),
                     "保留原计划不得重排原告警，严格冻结不得追加")
        append_vehicles = ()
        if strategy != "dynamic":
            old_alerts = prefix.original_starts
            _require(all(starts.get(a) == at for a, at in old_alerts.items()), "保留原计划改变了原任务的启动时刻")
            _require(starts.keys() - old_alerts.keys() == ({3} if strategy == "baseline_append" else set()), "保留原计划多出未允许的任务")
            if strategy == "baseline_append":
                append_vehicles = picks[3]["first"], picks[3]["second"]
        tracks = tuple(tuple(Phase.from_record(raw) for raw in track) for track in result["tracks"])
        _require(len(tracks) == 5 and all(track for track in tracks), "必须覆盖全部五车")
        observations = {}
        dispatch = prefix.dispatch
        for v, (track, original, locked_prefix) in enumerate(zip(tracks, prefix.original, prefix.fixed)):
            _require(track[0].start == 0 and track[0].points[0] == self.archive.check.vehicles[self.scene.vehicles[v]], "轨迹未从初始位置出发")
            _require(track[-1].end >= HORIZON_S, "车辆时间轴在评价窗结束前终止")
            # 允许新轨迹截断进行中的旧相位，但截断那部分的位置和状态必须与已有指令精确一致。
            if strategy == "dynamic":
                cutoff, reference = prefix.states[v].release, locked_prefix
            else:
                reference = original
                if v in append_vehicles:
                    tail = original[-1]
                    cutoff = max(dispatch, tail.start, self.archive.starts[tail.alert] if tail.kind == "READY_WAITING" else 0)
                else:
                    cutoff = track[-1].end
            old = self._clip(reference, cutoff)
            actual = self._clip(track, cutoff)
            _require(old == actual, f"{self.scene.vehicles[v]} 在既有不可改派轨迹中出现变化")
            active_alerts = {self.scene.targets.index(row["target"]) for row in self.archive.legs[v]
                             if row["depart_s"] < DISTURBANCE_S}
            before = None
            for p in track:
                if before is not None:
                    _require(_close(before.end, p.start) and before.points[-1] == p.points[0], "轨迹出现位置跳变、重叠或空缺")
                    _require(_close(wrap(before.pose1[0] - p.pose0[0]), 0) and _close(before.pose1[1], p.pose0[1]), "云台出现姿态瞬跳")
                before = p
                duration = p.end - p.start
                _require(p.kind in PHASES and math.isfinite(duration) and duration >= -TIME_TOL, "相位状态或持续时间不合法")
                _require((p.kind == "FAILED") == (v == failed and p.start >= DISTURBANCE_S), "故障退出相位不符合实际事件")
                _require(v != failed or p.kind == "FAILED" or p.end <= DISTURBANCE_S, "故障后仍在执行旧相位")
                if DISTURBANCE_S <= p.start < dispatch:
                    if info["variant"] == OutageRule.CURRENT_TRIP_ONLY and p.kind in {"TRAVELING", "SLEWING"}:
                        _require(p.alert in active_alerts, "只续当前车次时在断联内准备了未授权车次")
                    if info["variant"] == OutageRule.CONFIRM_AFTER_RECOVERY:
                        _require(p.kind != "OBSERVING", "断联内未确认的任务发生联合开测")
                if p.kind == "TRAVELING":
                    travel = self.route(p.points)["travel_s"]
                    _require(_close(duration, travel), "折线路长与 3 m/s 行驶时间不一致")
                    _require(not travel or p.pose0 == p.pose1 == EAST, "运动时云台不保持水平正东")
                else:
                    _require(len(p.points) == 1, "停车时发生位移")
                if p.kind in ("SLEWING", "RESETTING", "CANCEL_RESET"):
                    angle = max(abs(wrap(p.pose1[0] - p.pose0[0])), abs(p.pose1[1] - p.pose0[1]))
                    _require(_close(duration, angle / self.scene.gimbal_deg_s), "指向/复位不符合双轴最大角速度规则")
                if p.kind == "OBSERVING":
                    _require(p.alert in starts and _close(p.start, starts[p.alert]), "未报告实际同步启动")
                    end = p.start + self.scene.observation_s
                    if v == failed and p.start < DISTURBANCE_S < end:
                        end = DISTURBANCE_S
                    _require(_close(p.end, end), "观察长度错误或故障退出未正确截断观测")
                    target = tuple(info["targets"][p.alert])
                    pose = self.station(p.points[0], target)
                    _require(_close(wrap(pose["yaw_deg"] - p.pose0[0]), 0) and
                             _close(pose["pitch_deg"], p.pose0[1]) and p.pose0 == p.pose1, "观测时云台未稳定指向目标")
                    key = (p.alert, v)
                    _require(key not in observations, "同一车重复观测同一告警")
                    observations[key] = p.points[0]
        for a, pick in picks.items():
            vehicles = (pick["first"], pick["second"])
            p, q = map(tuple, info["pairs"][str(a)][pick["pair"]])
            _require(vehicles[0] != vehicles[1] and (observations.pop((a, vehicles[0]), None), observations.pop((a, vehicles[1]), None)) == (p, q),
                     "两辆不同车辆未从配置的两个站同时进行观测")
            m = self.pair(p, q, tuple(info["targets"][a]))
            _require(not m["station_disks_overlap"] and m["sin_gamma"] > 1e-12, "双站盘相交或交会几何退化")
        _require(not observations and all(starts.get(a) == at for a, at in prefix.locked.items()), "未正确保留已启动的不可重排任务")
        distances, _ = separation(tracks, self.scene.speed_m_s)
        _require(distances >= 2 * self.scene.field.vehicle_radius - TIME_TOL, "连续轨迹车体间距低于 3 m")
        _require(_close(distances, float(summary["continuous_min_vehicle_distance_m"])), "连续车距与指标表不同")
        k = sum(at < HORIZON_S or (info["inclusive_60"] and at == HORIZON_S) for at in starts.values())
        n = info["N"]
        last = max(starts.values()) if k == n else HORIZON_S
        _require(k == len(starts) == int(summary["k"]) and n == int(summary["N"]), "成功启动数不一致")
        _require(_close(k/n, float(summary["eta"])) and _close(last, float(summary["t_last_s"])), "官方指标与车辆轨迹不一致")
        if validation is not None:
            _require(validation["valid"] and validation["timeline_consistency"] and validation["recomputed_k"] == k
                     and _close(k/n, validation["recomputed_eta"]) and _close(last, validation["recomputed_t_last_s"])
                     and _close(distances, validation["min_center_distance_m"]), "重算指标与初次回放不同")
        return distances

    def _clip(self, track, cutoff):
        """时间边界裁剪；比较的都是同一连续物理轨迹而不是相位时间字符串。"""
        return tuple(p if p.end <= cutoff else p.cut(cutoff, self.scene) for p in track if p.start < cutoff)


def _one_score(row: dict) -> tuple[int, float]:
    """有限精度文件比较沿用报告的 1e-8 s 并列处理；不放宽窗口判定。"""
    n, k = int(row["N"]), int(row["k"])
    last = float(row["t_last_s"])
    _require(row["valid"] == "True" and n > 0 and 0 <= k <= n and _close(k/n, float(row["eta"])) and
             (last == HORIZON_S if k < n else 0 <= last <= HORIZON_S and
              (last < HORIZON_S or row["inclusive_60"] == "True")), f"非法计分：{_key(row)}")
    return -k, round(last, 8)


def _audit_selection(manifest, table):
    """复核七行同输入、六方案字典序及推荐来源；返回推荐行对应的原模型主键。"""
    scores = {key: _one_score(row) for key, row in table.items()}
    sources = {}
    for case, inputs in manifest.items():
        for model in MODELS:
            key = (*case, model)
            _require(all(table[key][field] == value for field, value in inputs.items()), f"对比分数未使用同一输入：{key}")
        key = (*case, "recommended")
        row = table[key]
        source = "original_append" if row["source"] in {"baseline_append", "baseline_frozen"} else row["source"]
        source_key = (*case, source)
        _require(source in MODELS and source != "recommended" and row["source"] == table[source_key]["source"],
                 f"推荐引用未知来源：{key}")
        _require(all(row[field] == table[source_key][field] for field in ("strategy", "k", "eta", "t_last_s",
                      "sum_successful_start_times_s", "schedule_change_cost")), f"推荐分数与其来源不一致：{key}")
        _require(scores[key] == min(scores[*case, m] for m in MODELS if m != "recommended"), f"推荐不是逐场最佳：{key}")
        sources[key] = source_key
    return scores, sources


def _audit_summary(path, manifest, table, scores):
    """从逐场底表独立算回主样本汇总；证明计数不是重新求解。"""
    if not path.is_file():  # 定向压力运行没有主样本汇总表。
        return
    sample = "main" if any(r["sample"] == "main" for r in manifest.values()) else "development"
    cases = [key for key, r in manifest.items() if r["sample"] == sample and r["variant"] == "main"]
    summary = _index(_csv(path), lambda r: r["model"])
    _require(set(summary) == set(MODELS) and bool(cases), "算法汇总缺少主样本或策略")
    for model, row in summary.items():
        rows = [table[*case, model] for case in cases]
        _require(int(row["scenarios"]) == int(row["valid"]) == len(cases) and
                 int(row["all_alerts_started"]) == sum(r["k"] == r["N"] for r in rows) and
                 _close(float(row["mean_eta"]), mean(float(r["eta"]) for r in rows)) and
                 _close(float(row["mean_t_last_s"]), mean(float(r["t_last_s"]) for r in rows)) and
                 int(row["finite_model_optimal_count"]) == sum(r["solver_status"] == "OPTIMAL" for r in rows) and
                 int(row["matches_best_official"]) == sum(scores[*case, model] == scores[*case, "recommended"] for case in cases),
                  f"主样本算法汇总与逐场底表不一致：{model}")


def _audit_solver_log(path, table, sources):
    """核对完整运行的日志、导出状态及离散界；不重复运行求解器。"""
    logs = _index(_rows(path), _key)
    _require(logs.keys() == table.keys(), "求解日志与逐场底表主键不一致")
    for key, log in logs.items():
        row = table[key]
        _require(row["solver_status"] == log["status"] and row["k"] == str(log["incumbent_k"]) and
                 _close(float(row["t_last_s"]), log["incumbent_t_last_s"]) and
                 _close(float(row["solve_time_seconds"]), log["solve_s"]), f"求解日志与导出结果不同：{key}")
        for field in ("k_bound", "latest_tick_lower_s", "optimal_latest_tick_s"):
            value = log.get(field)
            _require((not row[field] if value is None else bool(row[field]) and _close(float(row[field]), value)),
                     f"求解日志与导出界不同：{key} {field}")
        if key in sources:
            origin = sources[key]
            _require(log["source_strategy"] == origin[-1] and log["solve_s"] == logs[origin]["solve_s"],
                     f"推荐行的来源及求解用时不对应：{key}")
    return len(logs)


def _audit_timelines(path, expected, vehicles, observation_events, travel_starts, legacy_placeholders):
    """核对逐车同步开测、观测结束/退出、真实出发和评价窗端点。"""
    counts, ends, positions = Counter(), set(), {}
    for row in _csv(path):
        key = _key(row)
        name = row["vehicle"]
        _require(key in expected and name in vehicles, "时间轴存在未归档的策略或车辆")
        item = (*key, name)
        at = float(row["time_s"])
        idx, prev = positions.get(item, (0, 0.0))
        _require(int(row["event_index"]) == idx and math.isfinite(at) and at >= prev, f"车辆事件编号或时序不连续：{item}")
        positions[item] = (idx + 1, at)
        event = row["event"]
        counts[event] += 1
        if event in ("OBSERVATION_START", "OBSERVATION_END", "OBSERVING_INTERRUPTED"):
            evidence = (*item, event, row["alert"], at)
            _require(evidence in observation_events, f"观测事件与保存的相位不符：{evidence}")
            observation_events.remove(evidence)
        if event == "DEPART":
            evidence = (*item, row["alert"], at)
            if evidence in travel_starts:
                travel_starts.remove(evidence)
            else:
                _require(evidence in legacy_placeholders, f"出发事件没有对应的实际行驶：{evidence}")
                legacy_placeholders.remove(evidence)
        if event == "WINDOW_END":
            _require(at == HORIZON_S and item not in ends, "车的评价窗终点重复或不在 60 秒")
            ends.add(item)
    _require(len(ends) == len(vehicles)*len(expected), "车辆逐事件时间轴不完整")
    _require(not observation_events and not travel_starts, "逐车时间轴缺少开测、观测结束或出发记录")
    return ends, counts


def verify(path: Path, *, archive: bool = False) -> dict:
    """复核目录内全部保存的数据；归档版只回放推荐及两基线的主/加密情景。"""
    begin = time.perf_counter()
    names = ("config.yaml", "scenario_manifest.csv", "scenario_inputs.jsonl.gz")
    if archive:
        metrics, plans, events = "scenario_metrics.csv", "policy_plans_and_tracks.jsonl.gz", "policy_vehicle_timelines.csv.gz"
        names += ("algorithm_summary.csv",)
    else:
        metrics, plans, events = "model_comparison.csv", "plans_and_tracks.jsonl.gz", "vehicle_timelines.csv.gz"
        names += ("validation.jsonl.gz", "alns_seed_results.csv.gz", "solver_log.jsonl.gz")
        if (path / "algorithm_summary.csv").is_file():
            names += ("algorithm_summary.csv",)
    names += (metrics, plans, events)
    cfg = yaml.safe_load((path / "config.yaml").read_text(encoding="utf-8"))
    baseline = load_archive((path / cfg["q2_archive"]).resolve())
    audit = Evidence(baseline)
    instances = _index(_rows(path / "scenario_inputs.jsonl.gz"), _case)
    manifest = _index(_csv(path / "scenario_manifest.csv"), _case)
    _require(bool(instances) and instances.keys() == manifest.keys(), "情景清单不同于实际运行输入")
    for case, row in instances.items():
        info = {**row, "locked_at_dispatch": tuple(row["locked_at_dispatch"])}
        _require(all(value == ("" if info.get(field) is None else str(info[field])) for field, value in manifest[case].items()),
                 f"逐场 JSON 输入与情景清单不一致：{case}")
        audit._audit_input(row)
    table = _index(_csv(path / metrics), _key)
    _require(table.keys() == {(*case, m) for case in manifest for m in MODELS}, "每场必须包含 6 个对照与 1 个推荐")
    scores, sources = _audit_selection(manifest, table)
    _audit_summary(path / "algorithm_summary.csv", manifest, table, scores)
    solver_logs = None
    if archive:
        for key, r in table.items():
            upper, lower, gap = (float(r[f]) if r[f] else None for f in ("plan_latest_tick_s", "latest_tick_lower_s", "latest_tick_gap_s"))
            if upper is not None and lower is not None:
                _require(gap is not None and upper >= lower - TIME_TOL and _close(upper - lower, gap), f"导出离散界差不一致：{key}")
    else:
        solver_logs = _audit_solver_log(path / "solver_log.jsonl.gz", table, sources)
    expected = {key for key in table if key[1] == "main" and key[2] in POLICIES} if archive else set(table)
    _require(bool(expected), "没有可回放的归档策略")
    errors, checked, mn = [], set(), math.inf
    tracks = _rows(path / plans)
    records = ((r, None) for r in tracks) if archive else zip(tracks, _rows(path / "validation.jsonl.gz"), strict=True)
    signatures = {}
    observation_events, travel_starts, legacy_placeholders = set(), set(), set()
    for result, validation in records:
        key = _key(result)
        _require(key in expected and key not in checked and (validation is None or key == _key(validation)),
                 f"轨迹或复核记录的主键不一致：{key}")
        checked.add(key)
        try:
            info = instances[key[:2]]
            mn = min(mn, audit.audit(info, result, table[key], validation))
            signatures[key] = digest([result[field] for field in ("selected", "starts_s", "picks", "tracks")])
            for vehicle, track in zip(baseline.scene.vehicles, result["tracks"]):
                for phase in track:
                    if phase["kind"] == "OBSERVING":
                        alert = info["names"][phase["alert"]]
                        end = "OBSERVING_INTERRUPTED" if vehicle == info["failed_vehicle"] and phase["end"] < (
                            phase["start"] + baseline.scene.observation_s) else "OBSERVATION_END"
                        observation_events.add((*key, vehicle, "OBSERVATION_START", alert, phase["start"]))
                        observation_events.add((*key, vehicle, end, alert, phase["end"]))
                    if phase["kind"] == "TRAVELING":
                        depart = (*key, vehicle, info["names"][phase["alert"]], phase["start"])
                        if phase["end"] > phase["start"]:
                            travel_starts.add(depart)
                        elif not archive:  # 只容许与原始轨迹零路程占位对应的旧日志事件。
                            legacy_placeholders.add(depart)
        except (ValueError, KeyError) as exc:
            errors.append({"key": key, "error": str(exc)})
            if len(errors) >= 25:
                break
    if not errors:
        _require(checked == expected, "输出表和实际车辆轨迹未一一对应")
        for key, origin in sources.items():
            if key in expected and origin in expected:
                _require(signatures[key] == signatures[origin], f"推荐轨迹与来源方案不同：{key}")
    windows, counts = _audit_timelines(path / events, expected, baseline.scene.vehicles, observation_events, travel_starts,
                                        legacy_placeholders) if not errors else ((), Counter())
    seed_runs = None
    if not archive:
        seeds = Counter((*_case(r), int(r["seed"])) for r in _csv(path / "alns_seed_results.csv.gz"))
        _require(seeds == Counter({(*case, seed): 1 for case in manifest for seed in cfg["alns_seeds"]}), "ALNS 种子记录缺失或重复")
        seed_runs = sum(seeds.values())
    return {"scope": "git_archive" if archive else "full_run", "all_valid": not errors, "errors": errors,
            "scenarios": len(instances), "scenarios_with_tracks": len({key[:2] for key in checked}),
            "scenario_scores_checked": len(table), "plan_tracks_checked": len(checked), "vehicle_timelines": len(windows),
             "timeline_events": sum(counts.values()), "event_types": dict(counts), "seed_runs": seed_runs, "solver_logs": solver_logs,
            "min_vehicle_center_distance_m": mn if math.isfinite(mn) else None,
            "q2_initial_latest_start_s": max(baseline.starts), "q2_archive_inputs_sha256": baseline.hashes,
            "auditor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "input_sha256": {name: hashlib.sha256((path / name).read_bytes()).hexdigest() for name in names},
            "duration_s": time.perf_counter() - begin}


def main() -> int:
    """命令行独立验收，失败时以非零状态返回；可写 JSON 验收记录。"""
    parser = argparse.ArgumentParser(description="第四问逐场轨迹、事件和官方指标归档核查")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--archive", action="store_true", help="验收 Git 归档中的三种正式策略，非完整运行日志")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = verify(args.directory, archive=args.archive)
    text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.report:
        args.report.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if result["all_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
