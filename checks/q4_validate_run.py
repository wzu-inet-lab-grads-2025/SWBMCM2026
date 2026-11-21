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
from itertools import groupby
import json
import math
from pathlib import Path
from statistics import mean
import time

import yaml

from checks.q2_geometry import check_route, pair_metrics, station_pointing
from checks.q4_replay import separation
from src.q4.candidates import digest, initial_schedule_hash, prefix_hash, target_config
from src.q4.physics import DISTURBANCE_S, EAST, HORIZON_S, OUTAGE_COMPARISONS, OUTAGE_VARIANTS, OutageRule, Phase, build_prefix, load_archive, wrap
from src.shared.wall_safety import execution_wall_budget


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

    def __init__(self, archive, *, wall_clearance_m=0.0, minimum_intersection_gap_deg=0.0):
        """按存档场地和附加墙距（m）、交会角余量（度）配置独立复核。"""
        self.archive = archive
        self.scene = archive.scene
        self.wall_clearance_m = wall_clearance_m
        self.min_sine = max(1e-12, math.sin(math.radians(minimum_intersection_gap_deg)))
        self.initial_hash = initial_schedule_hash(archive)
        self.route = lru_cache(maxsize=16384)(lambda points: check_route(archive.check, points, points[0],
                                                                         wall_clearance_m=wall_clearance_m))
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
        rule = OutageRule(variant) if variant in OUTAGE_VARIANTS else OutageRule.FULL_QUEUE
        _require(rule == OutageRule.FULL_QUEUE or recovery is not None, "执行口径对照缺少断联")
        rederived = self.prefix(failed, dispatch, rule)
        _require(row.get("q2_plan", "optimized") == self.archive.plan and row["initial_schedule_hash"] == self.initial_hash,
                 "Q2 初排方案或归档哈希变化")
        _require(row["station_candidate_set_hash"] == digest(row["pairs"]), "候选站对哈希变化")
        _require(all(tuple(map(tuple, row["pairs"][str(a)][0])) == pair for a, pair in enumerate(self.archive.pairs)),
                 "旧告警站对的第一个候选与初始计划不一致")
        _require(row["vehicle_state_snapshot_hash"] == prefix_hash(rederived), "动态状态哈希变化")
        _require(row["vehicle_state_snapshot_hash"] == digest({"dispatch": dispatch, "failed": failed, "states": row["states"],
                                                                 "locked": row["locked"]}), "导出动态状态与哈希不一致")
        return rederived

    def audit(self, info: dict, result: dict, summary: dict, validation: dict | None = None, alert_rows=None):
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
        if result["model"] == "fixed_stations":
            _require(all(a == 3 or pick["pair"] == 0 for a, pick in picks.items()), "固定旧站策略更换了原站")
        _require(all((picks[a]["pair"], picks[a]["first"], picks[a]["second"]) == (0, *self.archive.assignment[a])
                     for a in prefix.locked), "已开测任务的车辆或站位发生变化")
        append_vehicles = ()
        if strategy != "dynamic":
            old_alerts = prefix.original_starts
            _require(all(starts.get(a) == at for a, at in old_alerts.items()), "保留原计划改变了原任务的启动时刻")
            _require(starts.keys() - old_alerts.keys() == ({3} if strategy == "baseline_append" else set()), "保留原计划多出未允许的任务")
            if strategy == "baseline_append":
                if "t4_baseline_pairs" in info:
                    _require(0 <= picks[3]["pair"] < info["t4_baseline_pairs"], "仅追加动用了重调专属站对")
                append_vehicles = picks[3]["first"], picks[3]["second"]
        tracks = tuple(tuple(Phase.from_record(raw) for raw in track) for track in result["tracks"])
        _require(len(tracks) == 5 and all(track for track in tracks), "必须覆盖全部五车")
        observations = {}
        dispatch = prefix.dispatch
        only_current = info["variant"] in (OutageRule.CURRENT_TRIP_ONLY, OutageRule.STRICT_OUTAGE)
        no_confirmation = info["variant"] in (OutageRule.CONFIRM_AFTER_RECOVERY, OutageRule.STRICT_OUTAGE)
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
            for index, p in enumerate(track):
                before = track[index - 1] if index else None
                if before is not None:
                    _require(_close(before.end, p.start) and before.points[-1] == p.points[0], "轨迹出现位置跳变、重叠或空缺")
                    _require(_close(wrap(before.pose1[0] - p.pose0[0]), 0) and _close(before.pose1[1], p.pose0[1]), "云台出现姿态瞬跳")
                duration = p.end - p.start
                _require(p.kind in PHASES and math.isfinite(duration) and duration >= -TIME_TOL, "相位状态或持续时间不合法")
                _require((p.kind == "FAILED") == (v == failed and p.start >= DISTURBANCE_S), "故障退出相位不符合实际事件")
                _require(v != failed or p.kind == "FAILED" or p.end <= DISTURBANCE_S, "故障后仍在执行旧相位")
                if DISTURBANCE_S <= p.start < dispatch:
                    if only_current and p.kind in {"TRAVELING", "SLEWING"}:
                        _require(p.alert in active_alerts, "只续当前车次时在断联内准备了未授权车次")
                    if no_confirmation:
                        _require(p.kind != "OBSERVING", "断联内未确认的任务发生联合开测")
                if p.kind == "TRAVELING":
                    travel = self.route(p.points)["travel_s"]
                    _require(_close(duration, travel), "折线路长与 3 m/s 行驶时间不一致")
                    _require(not travel or p.pose0 == p.pose1 == EAST, "运动时云台不保持水平正东")
                else:
                    _require(len(p.points) == 1, "停车时发生位移")
                    if self.wall_clearance_m:
                        self.route(p.points)
                if p.kind in ("SLEWING", "RESETTING", "CANCEL_RESET"):
                    angle = max(abs(wrap(p.pose1[0] - p.pose0[0])), abs(p.pose1[1] - p.pose0[1]))
                    _require(_close(duration, angle / self.scene.gimbal_deg_s), "指向/复位不符合双轴最大角速度规则")
                    if p.kind != "SLEWING" and not (v == failed and p.end == DISTURBANCE_S):
                        _require(p.pose1 == EAST, "复位未回到水平正东")
                if p.kind in ("READY_WAITING", "IDLE", "FAILED"):
                    _require(p.pose0 == p.pose1, "等待、空闲或故障状态的云台发生转动")
                if p.kind == "OBSERVING":
                    _require(p.alert in starts and _close(p.start, starts[p.alert]), "未报告实际同步启动")
                    end = p.start + self.scene.observation_s
                    if v == failed and p.start < DISTURBANCE_S < end:
                        end = DISTURBANCE_S
                    _require(_close(p.end, end), "观察长度错误或故障退出未正确截断观测")
                    after = track[index + 1] if index + 1 < len(track) else None
                    _require(before is not None and before.kind == "READY_WAITING" and before.alert == p.alert,
                             "开测前缺少本站就绪相位")
                    _require(after is not None and after.alert == p.alert and after.kind == ("FAILED" if v == failed else "RESETTING"),
                             "观测结束后未立即复位或在故障时未退出")
                    target = tuple(info["targets"][p.alert])
                    pose = self.station(p.points[0], target)
                    _require(_close(wrap(pose["yaw_deg"] - p.pose0[0]), 0) and
                             _close(pose["pitch_deg"], p.pose0[1]) and p.pose0 == p.pose1, "观测时云台未稳定指向目标")
                    key = (p.alert, v)
                    _require(key not in observations, "同一车重复观测同一告警")
                    observations[key] = p
        joint = {}
        for a, pick in picks.items():
            vehicles = (pick["first"], pick["second"])
            p, q = map(tuple, info["pairs"][str(a)][pick["pair"]])
            first = observations.pop((a, vehicles[0]), None)
            second = observations.pop((a, vehicles[1]), None)
            _require(vehicles[0] != vehicles[1] and first is not None and second is not None and
                     (first.points[0], second.points[0]) == (p, q),
                     "两辆不同车辆未从配置的两个站同时进行观测")
            m = self.pair(p, q, tuple(info["targets"][a]))
            _require(not m["station_disks_overlap"] and m["sin_gamma"] > self.min_sine, "双站盘相交或交会角余量不足")
            joint[a] = max(first.start, second.start), min(first.end, second.end)
        _require(not observations and all(starts.get(a) == at for a, at in prefix.locked.items()), "未正确保留已启动的不可重排任务")
        distances, _ = separation(tracks, self.scene.speed_m_s)
        _require(distances >= 2 * self.scene.field.vehicle_radius - TIME_TOL, "连续轨迹车体间距低于 3 m")
        _require(_close(distances, float(summary["continuous_min_vehicle_distance_m"])), "连续车距与指标表不同")
        k = sum(at < HORIZON_S or (info["inclusive_60"] and at == HORIZON_S) for at in starts.values())
        n = info["N"]
        last = max(starts.values()) if k == n else HORIZON_S
        _require(k == len(starts) == int(summary["k"]) and n == int(summary["N"]), "成功启动数不一致")
        _require(_close(k/n, float(summary["eta"])) and _close(last, float(summary["t_last_s"])), "官方指标与车辆轨迹不一致")
        if alert_rows is not None:
            completed = self._audit_alert_rows(info, starts, joint, alert_rows)
            _require(completed == int(summary["k_full_observation"]), "评价窗内采满的告警数不一致")
        if validation is not None:
            _require(validation["valid"] and validation["timeline_consistency"] and validation["recomputed_k"] == k
                     and _close(k/n, validation["recomputed_eta"]) and _close(last, validation["recomputed_t_last_s"])
                     and _close(distances, validation["min_center_distance_m"]), "重算指标与初次回放不同")
        return distances

    def _audit_alert_rows(self, info, starts, joint, rows):
        """逐条用两条经几何验收的观测相位检查交集，返回评价窗内采满的告警数。"""
        exported = _index(rows, lambda row: row["alert"])
        _require(exported.keys() == set(info["names"]), "实采表缺告警或分组出错")
        completed = 0
        for a, name in enumerate(info["names"]):
            row = exported[name]
            if a not in starts:
                _require(not any(row[field] for field in ("observation_start_s", "observation_end_s", "joint_observation_end_s")) and
                         row["success_started"] == row["complete_5s_by_60"] == "0" and
                         float(row["joint_observation_in_window_s"]) == 0, f"{name} 未开测但存在采集时刻")
                continue
            duration_s = self.scene.observation_s
            t0, t1 = joint[a]
            in_window = max(0.0, min(t1, HORIZON_S) - t0)
            complete = t0 + duration_s <= t1 and t0 + duration_s <= HORIZON_S
            _require(row["success_started"] == "1" and _close(float(row["observation_start_s"]), starts[a]) and
                     _close(float(row["observation_end_s"]), starts[a] + duration_s) and
                     _close(float(row["joint_observation_end_s"]), t1) and
                     _close(float(row["joint_observation_in_window_s"]), in_window) and
                     row["complete_5s_by_60"] == str(int(complete)), f"{name} 的开测或双车实采与轨迹不一致")
            completed += complete
        return completed

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


def _audit_selection(manifest, table, models=MODELS):
    """复核各策略的共同输入、官方字典序和推荐来源。"""
    scores = {key: _one_score(row) for key, row in table.items()}
    sources = {}
    for case, inputs in manifest.items():
        for model in models:
            key = (*case, model)
            _require(all(table[key][field] == value for field, value in inputs.items()), f"对比分数未使用同一输入：{key}")
        key = (*case, "recommended")
        row = table[key]
        source = "original_append" if row["source"] in {"baseline_append", "baseline_frozen"} else row["source"]
        source_key = (*case, source)
        _require(source in models and source != "recommended" and row["source"] == table[source_key]["source"],
                 f"推荐引用未知来源：{key}")
        _require(all(row[field] == table[source_key][field] for field in ("strategy", "k", "eta", "t_last_s",
                      "sum_successful_start_times_s", "schedule_change_cost")) and
                 row.get("k_full_observation") == table[source_key].get("k_full_observation"),
                 f"推荐分数或实采数与其来源不一致：{key}")
        _require(scores[key] == min(scores[*case, m] for m in models if m != "recommended"), f"推荐不是逐场最佳：{key}")
        sources[key] = source_key
    return scores, sources


def _audit_summary(path, manifest, table, scores, models=MODELS):
    """从逐场底表独立算回主样本汇总；证明计数不是重新求解。"""
    if not path.is_file():  # 定向压力运行没有主样本汇总表。
        return
    sample = "main" if any(r["sample"] == "main" for r in manifest.values()) else "development"
    cases = [key for key, r in manifest.items() if r["sample"] == sample and r["variant"] == "main"]
    summary = _index(_csv(path), lambda r: r["model"])
    _require(set(summary) == set(models) and bool(cases), "算法汇总缺少主样本或策略")
    for model, row in summary.items():
        rows = [table[*case, model] for case in cases]
        _require(int(row["scenarios"]) == int(row["valid"]) == len(cases) and
                 int(row["all_alerts_started"]) == sum(r["k"] == r["N"] for r in rows) and
                 _close(float(row["mean_eta"]), mean(float(r["eta"]) for r in rows)) and
                 _close(float(row["mean_t_last_s"]), mean(float(r["t_last_s"]) for r in rows)) and
                 int(row["finite_model_optimal_count"]) == sum(r["solver_status"] == "OPTIMAL" for r in rows) and
                 int(row["matches_best_official"]) == sum(scores[*case, model] == scores[*case, "recommended"] for case in cases),
                 f"主样本算法汇总与逐场底表不一致：{model}")
        if "k_full_observation" in rows[0]:
            _require(int(row["all_alerts_fully_observed"]) == sum(r["k_full_observation"] == r["N"] for r in rows),
                     f"主样本采满汇总与逐场底表不一致：{model}")


def _audit_protocol_summary(path, table):
    """由逐场分数和实采数核对各断联规则的分组、汇总和时间统计。"""
    groups = {}
    for (_, variant, model), row in table.items():
        groups.setdefault((variant, model), []).append(row)
    rows = _index(_csv(path), lambda row: (row["variant"], row["model"]))
    _require(rows.keys() == groups.keys(), "断联规则汇总中有多余或缺失的组")
    for key, group in groups.items():
        row = rows[key]
        full = [float(r["t_last_s"]) for r in group if r["k"] == r["N"]]
        _require(int(row["scenarios"]) == int(row["valid"]) == len(group) and int(row["all_alerts_started"]) == len(full) and
                 int(row["all_alerts_fully_observed"]) == sum(r["k_full_observation"] == r["N"] for r in group) and
                 _close(float(row["mean_t_last_s"]), mean(float(r["t_last_s"]) for r in group)) and
                 (row["max_t_last_all_started_s"] == "" if not full else
                  _close(float(row["max_t_last_all_started_s"]), max(full))), f"断联规则汇总与逐场底表不一致：{key}")


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


def _audit_timelines(path, expected, vehicles, observation_events, travel_starts, legacy_placeholders, recoveries=None):
    """核对逐车开测、复原相位事件和评价窗，到恢复点先记恢复再执行指令。"""
    counts, ends, positions = Counter(), set(), {}
    recovered = set()
    after_recovery = {"REASSIGN", "UPDATE_COMMAND", "REROUTE", "APPEND_COMMAND", "DEPART", "OBSERVATION_START"}
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
        if recoveries is not None:
            at_recovery = recoveries[key[:2]]
            if event == "COMMUNICATION_RECOVERY":
                _require(at == at_recovery and item not in recovered, "恢复事件重复或时刻与情景不一致")
                recovered.add(item)
            elif at == at_recovery and event in after_recovery:
                _require(item in recovered, "同刻新指令、发车或开测发生在恢复事件之前")
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
    if recoveries is not None:
        _require(len(recovered) == len(vehicles) * sum(recoveries[key[:2]] is not None for key in expected),
                 "逐车事件表缺少恢复记录")
    return ends, counts


def verify(path: Path, *, archive: bool = False) -> dict:
    """复核完整运行或归档，按配置识别轨迹覆盖范围和可用逐事件底表。"""
    begin = time.perf_counter()
    cfg = yaml.safe_load((path / "config.yaml").read_text(encoding="utf-8"))
    recourse = "recourse" in cfg
    recourse_models = tuple(cfg.get("recourse_models", ("recourse",)))
    if recourse:
        _require(recourse_models in (("recourse",), ("recourse", "fixed_stations")), "未知的连续重调策略清单")
    models = (*POLICIES, *recourse_models) if recourse else MODELS
    names = ("config.yaml", "scenario_manifest.csv", "scenario_inputs.jsonl.gz")
    if archive and recourse:
        # 图表归档保存每套方案的连续相位和压缩评分。可从相位复原的百万行事件表留在 runs/。
        metrics, plans, events = "model_comparison.csv.gz", "plans_and_tracks.jsonl.gz", None
        names += ("algorithm_summary.csv",)
    elif archive:
        metrics, plans, events = "scenario_metrics.csv", "policy_plans_and_tracks.jsonl.gz", "policy_vehicle_timelines.csv.gz"
        names += ("algorithm_summary.csv",)
    else:
        metrics, plans, events = "model_comparison.csv", "plans_and_tracks.jsonl.gz", "vehicle_timelines.csv.gz"
        names += ("validation.jsonl.gz", "alns_seed_results.csv.gz", "solver_log.jsonl.gz")
        if (path / "algorithm_summary.csv").is_file():
            names += ("algorithm_summary.csv",)
    names += (metrics, plans) + ((events,) if events is not None else ())
    settings = cfg.get("recourse", {})
    wall = settings.get("wall_clearance_m", 0.0)
    angle = settings.get("minimum_intersection_gap_deg", 0.0)
    _require(math.isfinite(angle) and 0 <= angle < 90, "最小交会角距退化余量须在 [0,90) 度")
    safety = execution_wall_budget(settings) if "execution_error_bound_m" in settings else None
    baseline = load_archive((path / cfg["q2_archive"]).resolve(), wall_clearance_m=wall,
                            plan=settings.get("q2_plan", "optimized"), waypoint_decimals=settings.get("q2_waypoint_decimals"))
    audit = Evidence(baseline, wall_clearance_m=wall, minimum_intersection_gap_deg=angle)
    instances = _index(_rows(path / "scenario_inputs.jsonl.gz"), _case)
    manifest = _index(_csv(path / "scenario_manifest.csv"), _case)
    _require(bool(instances) and instances.keys() == manifest.keys(), "情景清单不同于实际运行输入")
    if recourse:
        rules = tuple(settings.get("outage_rules", OUTAGE_COMPARISONS))
        _require(bool(rules) and len(rules) == len(set(rules)) and set(rules) <= set(OUTAGE_VARIANTS), "未知或重复的断联变体")
        expected_cases = {(ident, rule) for (ident, variant), row in instances.items() if variant == "main"
                          for rule in (("main", *rules) if row["t_rec_s"] is not None else ("main",))}
        _require(instances.keys() == expected_cases, "断联变体须完整覆盖各自的含断联主场景")
    for case, row in instances.items():
        info = {**row, "locked_at_dispatch": tuple(row["locked_at_dispatch"])}
        _require(all(value == ("" if info.get(field) is None else str(info[field])) for field, value in manifest[case].items()),
                 f"逐场 JSON 输入与情景清单不一致：{case}")
        audit._audit_input(row)
    table = _index(_csv(path / metrics), _key)
    _require(table.keys() == {(*case, m) for case in manifest for m in models}, "逐场策略或推荐结果缺失")
    check_collection = "k_full_observation" in next(iter(table.values()))
    _require(check_collection or not recourse or OutageRule.STRICT_OUTAGE not in rules, "联合断联工况缺少双车实采数据")
    scores, sources = _audit_selection(manifest, table, models)
    _audit_summary(path / "algorithm_summary.csv", manifest, table, scores, models)
    solver_logs = None
    if archive:
        for key, r in table.items():
            upper, lower, gap = (float(r[f]) if r[f] else None for f in ("plan_latest_tick_s", "latest_tick_lower_s", "latest_tick_gap_s"))
            if upper is not None and lower is not None:
                _require(gap is not None and upper >= lower - TIME_TOL and _close(upper - lower, gap), f"导出离散界差不一致：{key}")
    else:
        solver_logs = _audit_solver_log(path / "solver_log.jsonl.gz", table, sources)
    expected = (set(table) if not archive or recourse else
                {key for key in table if key[1] == "main" and key[2] in POLICIES})
    _require(bool(expected), "没有可回放的归档策略")
    alert_groups = None
    if check_collection:
        alert_name = "alert_results.csv.gz"
        names += (alert_name,)
        alerts = _csv(path / alert_name)
        if archive and not recourse:
            alerts = (row for row in alerts if _key(row) in expected)
        alert_groups = groupby(alerts, _key)
        if recourse and (path / "protocol_summary.csv").is_file():
            _audit_protocol_summary(path / "protocol_summary.csv", table)
            names += ("protocol_summary.csv",)
    errors, checked, mn = [], set(), math.inf
    tracks = _rows(path / plans)
    records = ((r, None) for r in tracks) if archive else zip(tracks, _rows(path / "validation.jsonl.gz"), strict=True)
    signatures = {}
    observation_events, travel_starts, legacy_placeholders = set(), set(), set()
    alerts_checked = 0
    for result, validation in records:
        key = _key(result)
        _require(key in expected and key not in checked and (validation is None or key == _key(validation)),
                 f"轨迹或复核记录的主键不一致：{key}")
        checked.add(key)
        try:
            info = instances[key[:2]]
            alert_rows = None
            if alert_groups is not None:
                alert_key, group = next(alert_groups, (None, ()))
                _require(alert_key == key, f"逐告警实采主键与轨迹不一致：{key}")
                alert_rows = list(group)
            mn = min(mn, audit.audit(info, result, table[key], validation, alert_rows))
            alerts_checked += len(alert_rows) if alert_rows is not None else 0
            signatures[key] = digest([result[field] for field in ("selected", "starts_s", "picks", "tracks")])
            if events is None:
                continue
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
        _require(alert_groups is None or next(alert_groups, None) is None, "实采表存在未归档的额外记录")
        for key, origin in sources.items():
            if key in expected and origin in expected:
                _require(signatures[key] == signatures[origin], f"推荐轨迹与来源方案不同：{key}")
    windows, counts = (), Counter()
    events_checked = events is not None and not errors
    if events_checked:
        recoveries = {case: row["t_rec_s"] for case, row in instances.items()} if check_collection else None
        windows, counts = _audit_timelines(path / events, expected, baseline.scene.vehicles, observation_events,
                                            travel_starts, legacy_placeholders, recoveries)
    seed_runs = None
    if not archive and not recourse:
        seeds = Counter((*_case(r), int(r["seed"])) for r in _csv(path / "alns_seed_results.csv.gz"))
        _require(seeds == Counter({(*case, seed): 1 for case in manifest for seed in cfg["alns_seeds"]}), "ALNS 种子记录缺失或重复")
        seed_runs = sum(seeds.values())
    return {"scope": "git_archive" if archive else "full_run", "all_valid": not errors, "errors": errors,
               **({"required_wall_clearance_m": wall} if wall else {}),
               **({"wall_safety": safety} if safety is not None and not errors else {}),
               **({"minimum_intersection_gap_deg": angle} if angle else {}),
             "scenarios": len(instances), "scenarios_with_tracks": len({key[:2] for key in checked}),
              "scenario_scores_checked": len(table), "plan_tracks_checked": len(checked),
              "alert_results_checked": alerts_checked,
             "vehicle_timelines": 5 * len(checked) if events is None and not errors else len(windows),
             "event_table_checked": events_checked, "timeline_events": sum(counts.values()),
             "event_types": dict(counts), "seed_runs": seed_runs, "solver_logs": solver_logs,
            "min_vehicle_center_distance_m": mn if math.isfinite(mn) else None,
             "q2_plan": baseline.plan, "initial_schedule_hash": audit.initial_hash,
             "q2_initial_latest_start_s": max(baseline.starts), "q2_archive_inputs_sha256": baseline.hashes,
            "auditor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "input_sha256": {name: hashlib.sha256((path / name).read_bytes()).hexdigest() for name in names},
            "duration_s": time.perf_counter() - begin}


def main() -> int:
    """命令行独立验收，失败时以非零状态返回；可写 JSON 验收记录。"""
    parser = argparse.ArgumentParser(description="第四问逐场轨迹、事件和官方指标归档核查")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--archive", action="store_true", help="验收 Git 归档中的计分、可用轨迹及底表")
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
