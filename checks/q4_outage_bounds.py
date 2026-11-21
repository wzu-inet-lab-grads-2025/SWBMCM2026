"""第四问断联对照复核：分离规划时钟失分与固定 T2 站不可达的情景。
输入全量复算或对照归档目录，输出只续当前车次的连续复核与恢复后确认的直线下界 JSON。
运行：nohup python3 -B -m checks.q4_outage_bounds runs/q4/<运行编号> [--report <输出 JSON>]。
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol，2026-11-21（版本发布日期未提供）。
"""

import argparse
import csv
import gzip
import hashlib
from itertools import permutations
import json
import math
from pathlib import Path

import numpy as np
import yaml

from checks.q2_geometry import station_pointing
from checks.q4_replay import Validator
from src.q4.candidates import Catalog, Geometry, Scenario, digest, prefix_hash
from src.q4.physics import EAST, HORIZON_S, Clock, OutageRule, build_prefix, load_archive
from src.q4.problem import Instance


def _turn(old, new, speed):
    """从两个度制姿态按最短水平角差和双轴并行规则重算转向秒数。"""
    return max(abs(math.remainder(new[0] - old[0], 360)), abs(new[1] - old[1])) / speed


def _continuous_times(instance: Instance) -> tuple[float, list]:
    """枚举三项任务的全序和站车组合，返回忽略车距的最晚开测下界与窗内候选。"""
    scene, prefix = instance.archive.scene, instance.prefix
    alerts = instance.pending
    if len(alerts) != 3 or any(s.release != prefix.dispatch or s.kind == "TRAVELING" for s in prefix.states):
        raise ValueError("定向验收要求三项待排任务和恢复时可立即决策的停车状态")
    choices = {a: [dict(instance.legs(pick)) for pick in instance.picks[a]] for a in alerts}
    points = instance.points
    first_travel = instance.geometry.matrix([s.point for s in prefix.states], points) / scene.speed_m_s
    transfer_travel = instance.geometry.matrix(points, points) / scene.speed_m_s
    if not (np.isfinite(first_travel).all() and np.isfinite(transfer_travel).all()):
        raise ValueError("固定路由含不可通行站点")
    poses = {}
    for a in alerts:
        for p in instance.by_alert[a]:
            look = station_pointing(instance.archive.check, points[p], instance.targets[a])
            poses[a, p] = (look["yaw_deg"], look["pitch_deg"])
    speed = scene.gimbal_deg_s
    from_east = {key: _turn(EAST, pose, speed) for key, pose in poses.items()}
    first = {}
    for (a, p), pose in poses.items():
        for v in instance.alive:
            state = prefix.states[v]
            if state.point == points[p]:
                delay = _turn(state.pose, pose, speed)
            else:
                delay = _turn(state.pose, EAST, speed) + first_travel[v, p] + from_east[a, p]
            first[a, v, p] = prefix.dispatch + delay
    initial = {a: np.array([[first[a, v, p] for v, p in choice.items()] for choice in choices[a]]) for a in alerts}

    # 两项任务共享一辆车时，该车由上一次开测加观测、复位、转场和新指向用时释放。
    gaps = {}
    for a, b in permutations(alerts, 2):
        gaps[a, b] = np.array([[
            [scene.observation_s + from_east[a, x[v]] + transfer_travel[x[v], q] + from_east[b, q]
             if v in x else -math.inf for v, q in y.items()]
            for y in choices[b]] for x in choices[a]])

    lower_bound, candidates = math.inf, []
    for a, b, c in permutations(alerts):
        # 数组的前三轴依次枚举 a、b、c 的站车组合，末轴是目标任务的两个席位。
        at_a = initial[a].max(axis=-1)
        via_a = gaps[a, b]
        ready_b = np.where(via_a >= 0, at_a[:, None, None] + via_a, initial[b][None, :, :])
        at_b = ready_b.max(axis=-1)
        via_a, via_b = gaps[a, c][:, None, :, :], gaps[b, c][None, :, :, :]
        ready_c_without_b = np.where(via_a >= 0, at_a[:, None, None, None] + via_a, initial[c][None, None, :, :])
        ready_c = np.where(via_b >= 0, at_b[:, :, None, None] + via_b, ready_c_without_b)
        latest = np.maximum(np.maximum(at_a[:, None, None], at_b[:, :, None]), ready_c.max(axis=-1))
        lower_bound = min(lower_bound, float(latest.min()))
        candidates.extend((float(latest[i, j, k]), (instance.picks[a][i], instance.picks[b][j], instance.picks[c][k]))
                          for i, j, k in np.argwhere(latest < HORIZON_S))
    return lower_bound, sorted(candidates)


def _straight_t2_bound(archive, prefix) -> dict:
    """忽略楼体、车距及其他告警，给两座固定 T2 站计算成对同时就绪的下界（s）。"""
    scene = archive.scene
    stations = archive.pairs[scene.targets.index("T2")]
    target = archive.check.targets["T2"]
    poses = [(look["yaw_deg"], look["pitch_deg"])
             for look in (station_pointing(archive.check, p, target) for p in stations)]
    ready = {}
    for v, state in zip(scene.vehicles, prefix.states):
        if state.kind == "FAILED":
            continue
        if state.release != prefix.dispatch or state.kind == "TRAVELING":
            raise ValueError("直线下界要求健康车在恢复时驻车且可立即改派")
        speed = scene.gimbal_deg_s
        # 离开驻点前必须回到正东水平；再放宽行驶到直线，同站车则可直接转向。
        ready[v] = [prefix.dispatch + (_turn(state.pose, pose, speed) if state.point == p else
                    _turn(state.pose, EAST, speed) + math.dist(state.point, p) / scene.speed_m_s + _turn(EAST, pose, speed))
                    for p, pose in zip(stations, poses)]
    lower_bound, first, second = min((max(ready[v][0], ready[w][1]), v, w) for v, w in permutations(ready, 2))
    return {"station_pair_m": stations, "ready_lower_s_by_vehicle": ready,
            "earliest_vehicle_pair": (first, second), "earliest_t2_lower_s": lower_bound}


def verify(directory: Path) -> dict:
    """匹配逐场评分和恢复快照，逐一复核两类失分；下界、合法见证与主评分分开。"""
    inputs = ("config.yaml", "paired_outage.csv", "scenario_inputs.jsonl.gz")
    config = yaml.safe_load((directory / inputs[0]).read_text(encoding="utf-8"))
    archive = load_archive((directory / config["q2_archive"]).resolve())
    geometry = Geometry(archive)
    with (directory / inputs[1]).open(encoding="utf-8", newline="") as stream:
        misses = {row["scenario_id"] for row in csv.DictReader(stream)
                  if row["variant"] == OutageRule.CURRENT_TRIP_ONLY and int(row["k"]) < int(row["N"])}
        stream.seek(0)
        confirmation = [row for row in csv.DictReader(stream) if row["variant"] == OutageRule.CONFIRM_AFTER_RECOVERY]
    cohort = {row["scenario_id"]: row for row in confirmation
              if float(row["t_rec_s"]) == 30 and row["failed_vehicle"] in ("V1", "V2")}
    confirmation_misses = {row["scenario_id"] for row in confirmation if int(row["k"]) < int(row["N"])}
    reports = {}
    checked_cohort, straight_bounds = set(), {}
    with gzip.open(directory / inputs[2], "rt", encoding="utf-8") as stream:
        for row in map(json.loads, stream):
            key = row["scenario_id"]
            if row["variant"] == OutageRule.CONFIRM_AFTER_RECOVERY and key in cohort:
                v = row["failed_vehicle"]
                prefix = build_prefix(archive, archive.scene.vehicles.index(v), row["t_rec_s"],
                                      outage_rule=OutageRule.CONFIRM_AFTER_RECOVERY)
                if (row["t_rec_s"] != 30 or v != cohort[key]["failed_vehicle"] or "T2" not in cohort[key]["unstarted_alerts"].split()
                        or tuple(map(tuple, row["pairs"]["1"][0])) != archive.pairs[1]
                        or prefix_hash(prefix) != row["vehicle_state_snapshot_hash"] or 1 in prefix.locked):
                    raise ValueError(f"T2 固定站、失分或实际恢复快照不一致：{key}")
                if v not in straight_bounds:
                    bound = _straight_t2_bound(archive, prefix)
                    straight_bounds[v] = {**bound, "cases": 0}
                if straight_bounds[v]["earliest_t2_lower_s"] < HORIZON_S:
                    raise ValueError(f"直线放宽仍可能在窗内开测，不能证明超时：{key}")
                straight_bounds[v]["cases"] += 1
                if key in checked_cohort:
                    raise ValueError(f"断联对照情景重复：{key}")
                checked_cohort.add(key)
                continue
            if row["variant"] != OutageRule.CURRENT_TRIP_ONLY or key not in misses:
                continue
            failed = archive.scene.vehicles.index(row["failed_vehicle"])
            scenario = Scenario(row["category"], row["theta_deg"], row["R_m"], row["H_m"], failed, row["t_rec_s"])
            prefix = build_prefix(archive, failed, scenario.dispatch, outage_rule=OutageRule.CURRENT_TRIP_ONLY)
            if key in reports or prefix_hash(prefix) != row["vehicle_state_snapshot_hash"]:
                raise ValueError(f"断联对照情景重复或实际恢复快照不一致：{key}")
            pairs = tuple(tuple(map(tuple, pair)) for pair in row["pairs"]["3"])
            catalog = Catalog(pairs, 0, 0, 0, 0, None, digest(pairs))
            # 下界计算使用连续秒。仅对窗内方案调用回放验收，微秒网格避免原始取整放大。
            instance = Instance(archive, geometry, scenario, prefix, Clock(1e-6), catalog)
            if digest(instance.pairs) != row["station_candidate_set_hash"]:
                raise ValueError(f"站对目录不一致：{key}")
            lower_bound, candidates = _continuous_times(instance)
            check = Validator(instance)
            witness = None
            for at, plan in candidates:
                result = check.dynamic(plan)
                if result.valid and result.metrics(instance)[1] == len(instance.names):
                    if not math.isclose(at, max(result.starts.values()), abs_tol=1e-8, rel_tol=0):
                        raise ValueError(f"连续枚举和回放的时刻不一致：{key}")
                    witness = {"t_last_s": at, "min_vehicle_distance_m": result.min_distance,
                               "starts_s": {instance.names[a]: s for a, s in sorted(result.starts.items())},
                               "plan": [{**vars(pick), "station_pair_m": instance.pairs[pick.alert][pick.pair]} for pick in plan]}
                    break
            reports[key] = {"earliest_relaxed_s": lower_bound, "relaxed_candidates_within_window": len(candidates), "witness": witness}
    if reports.keys() != misses or checked_cohort != cohort.keys():
        raise ValueError("断联复核的情景输入缺项")
    return {OutageRule.CURRENT_TRIP_ONLY: {"cases": len(reports),
                                          "witness_count": sum(r["witness"] is not None for r in reports.values()),
                                          "proven_late_count": sum(r["earliest_relaxed_s"] >= HORIZON_S for r in reports.values()),
                                          "records": reports},
            OutageRule.CONFIRM_AFTER_RECOVERY: {"t_rec_s": 30, "fixed_alert": "T2", "proven_late_count": len(checked_cohort),
                                                "covers_all_misses": checked_cohort == confirmation_misses,
                                                "bounds_by_failed_vehicle": dict(sorted(straight_bounds.items()))},
            "input_sha256": {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in inputs},
            "auditor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="第四问断联两项对照失分的开测下界复核")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = verify(args.directory)
    text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.report:
        args.report.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
