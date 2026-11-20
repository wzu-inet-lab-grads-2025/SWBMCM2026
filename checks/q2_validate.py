"""B 题第二问独立验收：给定方案路线与时序，重算误差并检查约束。
不选站、不分配车辆、不求最短路；JSON 报告可写入 runs/q2/。
运行：nohup python3 -B -m checks.q2_validate --help
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol（版本发布日期未提供），2026-11-20。
"""

import argparse
from collections import defaultdict
import csv
from graphlib import CycleError, TopologicalSorter
import json
import math
from pathlib import Path

from .q2_geometry import (
    CheckError, Scene, check_route, check_vehicle_separation, load_scene, pair_metrics, station_pointing, xy,
)


# 默认用于六位小数的时间表；不是放宽几何/任务约束的阈值。
TIME_FIELDS = (
    "depart_s", "arrive_s", "turn_begin_s", "turn_end_s", "wait_s", "observation_start_s",
    "observation_end_s", "reset_begin_s", "reset_end_s", "next_departure_s",
)


def verify_q21(scene: Scene, result: dict) -> dict:
    """给定 T2 两辆车与路线；重算站点、行车时限和精度，不证明站位最优。"""
    if result["target"] != "T2" or len(result["routes"]) != 2:
        raise CheckError("2（1）需要恰好两辆车观测 T2")
    target = scene.targets["T2"]
    routes = []
    seen = set()
    for row in result["routes"]:
        vehicle = row["vehicle"]
        if vehicle not in scene.vehicles or vehicle in seen:
            raise CheckError(f"选车无效或重复：{vehicle!r}")
        seen.add(vehicle)
        route = check_route(scene, row["waypoints"], scene.vehicles[vehicle])
        if route["travel_s"] > 30:
            raise CheckError(f"{vehicle} 行驶时间超过 30 s")
        site = route["waypoints"][-1]
        routes.append({"vehicle": vehicle, "site": site, **route, **station_pointing(scene, site, target)})
    metrics = pair_metrics(scene, routes[0]["site"], routes[1]["site"], target)
    return {"target": "T2", "target_xyz_m": target, "routes": routes, **metrics}


def _expect_number(row: dict, key: str, expected: float | None, tolerance: float) -> None:
    """从 JSON/CSV 读非负时间或路长；无后续任务的空时刻只接受 null 或空单元格。"""
    value = row[key]
    if expected is None:
        if value not in (None, ""):
            raise CheckError(f"{key} 应为空，得到 {value!r}")
        return
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise CheckError(f"{key} 不是数值：{value!r}") from error
    if isinstance(value, bool) or number < 0 or not math.isclose(number, expected, rel_tol=0, abs_tol=tolerance):
        raise CheckError(f"{key} 报告值 {value!r}，复算值 {expected:.12g}")


def verify_q22(scene: Scene, routes: list[dict], timeline: list[dict], *, time_tol_s: float = 1e-6,
               require_vehicle_separation: bool = False) -> dict:
    """回放固定车次、区分最晚启动和最后复位（s），互撞默认为诊断而非拒绝条件。"""
    if not math.isfinite(time_tol_s) or time_tol_s < 0:
        raise CheckError("时间表的比较容差必须为有限非负秒数")
    by_leg, successors = {}, {}
    participants = {target: [] for target in scene.targets}
    dependencies = {target: set() for target in scene.targets}
    for row in routes:
        vehicle, target, previous = row["vehicle"], row["target"], row["previous_target"]
        if vehicle not in scene.vehicles or target not in scene.targets:
            raise CheckError(f"路线车号或告警无效：{vehicle}/{target}")
        if (vehicle, target) in by_leg or (vehicle, previous) in successors:
            raise CheckError(f"{vehicle}/{target} 重复，或同一前驱被分配了多条后续路线")
        if previous is not None:
            if previous not in scene.targets or previous == target:
                raise CheckError(f"{vehicle}/{target} 的前序告警无效：{previous!r}")
            dependencies[target].add(previous)
        by_leg[vehicle, target] = row
        successors[vehicle, previous] = target
        participants[target].append(vehicle)
    if any(len(vehicles) != 2 for vehicles in participants.values()):
        raise CheckError("每条告警必须恰好由两辆不同的车观测")
    for (vehicle, target), row in by_leg.items():
        previous = row["previous_target"]
        if previous is not None and (vehicle, previous) not in by_leg:
            raise CheckError(f"{vehicle}/{target} 缺少其前序 {previous} 路线")
    try:
        order = tuple(TopologicalSorter(dependencies).static_order())
    except CycleError as error:
        raise CheckError("共享车辆的任务依赖成环，无法同步观测") from error

    observed, idle = {}, set()
    active = {vehicle for vehicle, _ in by_leg}
    for row in timeline:
        vehicle, target = row["vehicle"], row["target"]
        if target == "idle" or (target in (None, "") and row.get("status") == "idle"):
            if vehicle not in scene.vehicles or vehicle in active or vehicle in idle:
                raise CheckError(f"待命记录与实际车次冲突或车号无效：{vehicle}")
            if any(row.get(field) not in (None, "") for field in TIME_FIELDS):
                raise CheckError(f"待命车 {vehicle} 不应有观测车次时间")
            idle.add(vehicle)
        else:
            key = (vehicle, target)
            if key not in by_leg or key in observed:
                raise CheckError(f"时间轴有重复或未分配的车次：{vehicle}/{target}")
            observed[key] = row
    if observed.keys() != by_leg.keys() or active | idle != set(scene.vehicles):
        raise CheckError("时间轴缺失已分配车次或未标明待命车")

    replay, start_times, metrics = {}, {}, {}
    for target in order:
        legs = []
        for vehicle in sorted(participants[target]):
            row = by_leg[vehicle, target]
            previous = row["previous_target"]
            prior = replay.get((vehicle, previous))
            origin = scene.vehicles[vehicle] if prior is None else prior["site"]
            departure = 0.0 if prior is None else prior["reset_end_s"]
            try:
                route = check_route(scene, row["waypoints"], origin)
                site = route["waypoints"][-1]
                for field, expected in (("origin", origin), ("destination", site)):
                    if field in row and xy(row[field]) != expected:
                        raise CheckError(f"{field} 与折线路点不一致")
                for field, tolerance in (("length_m", time_tol_s*scene.speed_m_s), ("travel_s", time_tol_s)):
                    if field in row:
                        _expect_number(row, field, route[field], tolerance)
                pointing = station_pointing(scene, site, scene.targets[target])
            except (CheckError, KeyError) as error:
                raise CheckError(f"{vehicle}/{target}：{error}") from error
            arrival = departure + route["travel_s"]
            replay[vehicle, target] = leg = {
                "vehicle": vehicle, "target": target, "previous_target": previous, "site": site,
                **route, **pointing, "depart_s": departure, "arrive_s": arrival,
                "turn_begin_s": arrival, "turn_end_s": arrival + pointing["slew_s"],
            }
            legs.append(leg)

        # 未共用车的告警无先后约束；计算顺序不是实际观测启动顺序。
        start = start_times[target] = max(leg["turn_end_s"] for leg in legs)
        end = start + scene.observation_s
        metrics[target] = pair_metrics(scene, legs[0]["site"], legs[1]["site"], scene.targets[target])
        for leg in legs:
            vehicle = leg["vehicle"]
            reset_end = end + leg["slew_s"]
            leg.update(wait_s=start - leg["turn_end_s"], observation_start_s=start, observation_end_s=end,
                       reset_begin_s=end, reset_end_s=reset_end,
                       next_departure_s=reset_end if (vehicle, target) in successors else None)
            for field in TIME_FIELDS:
                try:
                    _expect_number(observed[vehicle, target], field, leg[field], time_tol_s)
                except (CheckError, KeyError) as error:
                    raise CheckError(f"{vehicle}/{target}：{error}") from error

    sequences = {vehicle: [target for target in order if (vehicle, target) in by_leg] for vehicle in scene.vehicles}
    legs_by_vehicle = {vehicle: [replay[vehicle, target] for target in sequence] for vehicle, sequence in sequences.items()}
    last_reset = max(leg["reset_end_s"] for leg in replay.values())
    separation = check_vehicle_separation(scene, legs_by_vehicle, last_reset)
    if require_vehicle_separation and separation["overlapping_pairs"]:
        first = separation["overlapping_pairs"][0]
        raise CheckError(f"车间圆盘重叠：{'/'.join(first['vehicles'])}，{first['time_s']:.6g} s，"
                         f"车心距 {first['distance_m']:.6g} m")
    return {"time_table_abs_tolerance_s": time_tol_s, "observation_start_s": start_times,
            "latest_start_s": max(start_times.values()), "last_reset_end_s": last_reset,
            "vehicle_task_sequences": sequences, "idle_vehicles": sorted(idle), "pair_metrics": metrics,
            "inter_vehicle_separation": {"enforced": require_vehicle_separation, **separation},
            "legs": [leg for vehicle_legs in legs_by_vehicle.values() for leg in vehicle_legs]}


def _group_plans(rows: list[dict]) -> dict[str, list[dict]]:
    """按同名方案拆分路线和时序表；只有单方案时可省略 plan 栏。"""
    grouped = defaultdict(list)
    for row in rows:
        name = row.get("plan", "given")
        if not isinstance(name, str) or not name.strip():
            raise CheckError("plan 必须是非空字符串")
        grouped[name].append(row)
    return dict(grouped)


def compare_plans(baseline: dict, candidate: dict) -> dict:
    """比较两份已验收报告的启动时间（s），并列出各告警就绪最晚的车辆。"""
    if (baseline["observation_start_s"].keys() != candidate["observation_start_s"].keys()
            or baseline["inter_vehicle_separation"]["enforced"] != candidate["inter_vehicle_separation"]["enforced"]):
        raise CheckError("比较需要相同告警与车间分离口径")

    def summarize(report: dict) -> dict:
        """取复算后的目标值，与决定各告警启动的车辆；不依赖展示精度。"""
        starts, latest = report["observation_start_s"], report["latest_start_s"]
        limiting = {
            target: sorted(leg["vehicle"] for leg in report["legs"]
                           if leg["target"] == target and leg["turn_end_s"] == start)
            for target, start in starts.items()
        }
        return {"latest_start_s": latest, "latest_alerts": sorted(j for j, start in starts.items() if start == latest),
                "limiting_vehicles": limiting, "observation_start_s": starts}

    saved = baseline["latest_start_s"] - candidate["latest_start_s"]
    return {"baseline": summarize(baseline), "candidate": summarize(candidate), "latest_start_saved_s": saved,
            "latest_start_relative_saved": saved / baseline["latest_start_s"] if baseline["latest_start_s"] else None,
            "alert_start_saved_s": {target: start - candidate["observation_start_s"][target]
                                    for target, start in baseline["observation_start_s"].items()}}


def main(argv: list[str] | None = None) -> int:
    """读取固定方案的 JSON 或表格；错误时非零退出，不写成功报告。"""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--q21", metavar="JSON", type=Path, help="2（1）固定方案：target 与 routes")
    parser.add_argument("--q22-routes", metavar="JSON", type=Path, help="2（2）固定路线列表（或含 routes 的对象）")
    parser.add_argument("--q22-timeline", metavar="CSV", type=Path, help="2（2）对应的完整五车辆时间轴")
    parser.add_argument("--time-tol-s", metavar="SECONDS", type=float, default=1e-6, help="时间表比较的绝对容差")
    parser.add_argument("--require-vehicle-separation", action="store_true", help="任何车辆圆盘在同一时刻重叠均判为不合格")
    parser.add_argument("--compare", nargs=2, metavar=("BASELINE", "CANDIDATE"), help="比较两份输入方案，参数为 plan 名")
    parser.add_argument("--output", type=Path, help="保存 JSON 验收报告（默认打印到终端）")
    args = parser.parse_args(argv)
    if bool(args.q22_routes) != bool(args.q22_timeline):
        parser.error("--q22-routes 和 --q22-timeline 必须一起提供")
    if (args.require_vehicle_separation or args.compare) and not args.q22_routes:
        parser.error("--require-vehicle-separation 和 --compare 仅用于 2（2）")
    if not (args.q21 or args.q22_routes):
        parser.error("请提供 --q21 或一对 --q22-routes / --q22-timeline")
    try:
        scene = load_scene()
        report = {"scope": "given-plan verification only; no optimization or optimality certificate"}
        if args.q21:
            report["q21"] = verify_q21(scene, json.loads(args.q21.read_text(encoding="utf-8")))
        if args.q22_routes:
            payload = json.loads(args.q22_routes.read_text(encoding="utf-8"))
            route_sets = _group_plans(payload["routes"] if isinstance(payload, dict) else payload)
            with args.q22_timeline.open(encoding="utf-8-sig", newline="") as source:
                time_sets = _group_plans(list(csv.DictReader(source)))
            if not route_sets or route_sets.keys() != time_sets.keys():
                raise CheckError("路线与时间轴中的 plan 方案名不一致，或没有方案")
            report["q22"] = {
                name: verify_q22(scene, routes, time_sets[name], time_tol_s=args.time_tol_s,
                                 require_vehicle_separation=args.require_vehicle_separation)
                for name, routes in route_sets.items()
            }
            if args.compare:
                baseline, candidate = args.compare
                if baseline == candidate or baseline not in route_sets or candidate not in route_sets:
                    raise CheckError("--compare 需要两个不同且存在的 plan 名")
                report["comparison"] = {"baseline_plan": baseline, "candidate_plan": candidate,
                                        **compare_plans(report["q22"][baseline], report["q22"][candidate])}
        text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text, encoding="utf-8")
            print(args.output)
        else:
            print(text, end="")
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"验收失败：{error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
