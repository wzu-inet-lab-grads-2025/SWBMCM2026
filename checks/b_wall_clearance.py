"""B 题第二至四问的连续净空复核，从保存的折线和停车相位重算墙距。
Q2、Q4 读结果目录并输出 CSV；Q3 读指定行驶路线 JSON 并输出条件性安全报告。
运行：nohup python3 -B -m checks.b_wall_clearance q4 <结果目录> --output <净空.csv.gz>。
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol，2026-11-21（版本发布日期未提供）。
"""

import argparse
from collections import defaultdict
import csv
from functools import lru_cache
import gzip
import json
import math
from pathlib import Path

from checks.q2_geometry import check_route, load_scene, xy
from src.shared.wall_safety import execution_wall_budget


def measure_route(scene, points, required_m):
    """按折线全段算最小楼距，按坐标极值算最小场界距，返回车体净空 m 与障碍名称。"""
    result = check_route(scene, points, points[0], wall_clearance_m=required_m)
    building = min((distance - scene.radius_m, name)
                   for name, distance in result["min_distance_to_buildings_m"].items())
    # 到每条场界的有向距离沿线段仿射，最小值可在端点取到。
    field = min((distance - scene.radius_m, edge) for x, y in points for distance, edge in (
        (x, "west"), (y, "south"), (scene.width_m - x, "east"), (scene.height_m - y, "north")))
    return building, field


def q2_rows(directory, scene, measure):
    """逐车次复核第二问的路径，同时覆盖整场待命的车辆。"""
    deployment = json.loads((directory / "q21_result.json").read_text(encoding="utf-8"))["routes"]
    assignments = json.loads((directory / "q22_routes.json").read_text(encoding="utf-8"))
    plans = defaultdict(list)
    plans["q21"] = [{**row, "target": "T2"} for row in deployment]
    practical = directory / "q21_practical.json"
    if practical.is_file():
        plans["q21_practical"] = [{**row, "target": "T2"}
                                  for row in json.loads(practical.read_text(encoding="utf-8"))["routes"]]
    for row in assignments:
        plans[row["plan"]].append(row)
    for name, route in plans.items():
        idle = scene.vehicles.keys() - {row["vehicle"] for row in route}
        idle_rows = [{"vehicle": v, "target": "idle", "waypoints": (scene.vehicles[v],)} for v in sorted(idle)]
        for row in (*route, *idle_rows):
            (gap_building, building), (gap_edge, edge) = measure(tuple(map(tuple, row["waypoints"])))
            yield {"plan": name, "vehicle": row["vehicle"], "alert": row["target"],
                   "building_gap_m": gap_building, "building_id": building,
                   "field_gap_m": gap_edge, "field_edge": edge}


def verify_q3_route(scene, route, *, wall_clearance_m, execution_error_bound_m):
    """验收 Q3 单车从题设初始车位出发的完整折线；停车点包括在折线内。"""
    budget = execution_wall_budget({"wall_clearance_m": wall_clearance_m,
                                    "execution_error_bound_m": execution_error_bound_m})
    vehicle = route.get("vehicle") if isinstance(route, dict) else None
    if not isinstance(vehicle, str) or vehicle not in scene.vehicles:
        raise ValueError("第三问补充路线须指定一辆题设车辆")
    raw = route.get("waypoints")
    if not isinstance(raw, (list, tuple)) or not raw:
        raise ValueError("第三问补充路线至少需要一个路点")
    points = tuple(map(xy, raw))
    origin = scene.vehicles[vehicle]
    if points[0] != origin:
        raise ValueError(f"第三问路线须从 {vehicle} 的题设初始车位 {origin} 出发")
    building, field = measure_route(scene, points, wall_clearance_m)
    return {"vehicle": vehicle, "waypoints_m": points,
            "length_m": math.fsum(math.dist(a, b) for a, b in zip(points, points[1:])),
            "building_gap_m": building[0], "building_id": building[1],
            "field_gap_m": field[0], "field_edge": field[1], "wall_safety": budget}


def q4_rows(directory, scene, measure):
    """每场每策略取所有五车相位的两类净空极小值，记录对应车辆、相位和起点时间。"""
    with gzip.open(directory / "plans_and_tracks.jsonl.gz", "rt", encoding="utf-8") as source:
        for line in source:
            plan = json.loads(line)
            min_building = min_field = (math.inf,)
            for vehicle, track in zip(scene.vehicles, plan["tracks"], strict=True):
                for phase in track:
                    try:
                        building, field = measure(tuple(map(tuple, phase["points"])))
                    except ValueError as exc:
                        raise ValueError(f"{plan['scenario_id']}/{plan['variant']}/{plan['model']}/{vehicle}: {exc}") from exc
                    where = vehicle, phase["kind"], phase["start"]
                    min_building = min(min_building, (*building, *where))
                    min_field = min(min_field, (*field, *where))
            yield {**{key: plan[key] for key in ("scenario_id", "variant", "model")},
                   **dict(zip(("building_gap_m", "building_id", "building_vehicle", "building_phase", "building_phase_start_s"),
                              min_building, strict=True)),
                   **dict(zip(("field_gap_m", "field_edge", "field_vehicle", "field_phase", "field_phase_start_s"),
                              min_field, strict=True))}


def recommended_scores(directory):
    """读取完整运行或压缩归档的推荐分数，主键为场景与断联规则。"""
    plain = directory / "model_comparison.csv"
    path = plain if plain.is_file() else directory / "model_comparison.csv.gz"
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", newline="") as stream:
        rows = [row for row in csv.DictReader(stream) if row["model"] == "recommended"]
    scores = {(row["scenario_id"], row["variant"]): row for row in rows}
    if len(scores) != len(rows):
        raise ValueError("推荐结果的场景与规则键重复")
    return scores


def compare_q4(directory, reference, rows, reference_rows):
    """核对两次重调的初始排程、恢复快照和计分，再输出逐情景差值。"""
    before, after = recommended_scores(reference), recommended_scores(directory)
    if before.keys() != after.keys():
        raise ValueError("留量与标称结果的情景清单不同")
    gaps = []
    for group in (reference_rows, rows):
        selected = {(row["scenario_id"], row["variant"]): row for row in group if row["model"] == "recommended"}
        if selected.keys() != after.keys():
            raise ValueError("净空与计分结果的情景清单不同")
        gaps.append(selected)
    for case, old in before.items():
        new = after[case]
        if any(old[field] != new[field] for field in ("category", "N", "dispatch_s", "inclusive_60",
                                                     "initial_schedule_hash", "vehicle_state_snapshot_hash")):
            raise ValueError(f"留量与标称重调的输入不同：{case}")
        a, b = float(old["t_last_s"]), float(new["t_last_s"])
        yield {"scenario_id": case[0], "variant": case[1], "category": old["category"], "N": old["N"],
               "nominal_k": old["k"], "clearance_k": new["k"],
               "nominal_t_last_s": a, "clearance_t_last_s": b, "change_t_last_s": b - a,
               **{f"{label}_{item}": gap[case][item] for label, gap in zip(("nominal", "clearance"), gaps)
                  for item in ("building_gap_m", "field_gap_m")}}


def write_rows(path, rows):
    """写出 CSV（可用 gzip 包装），要求至少一条可复核记录。"""
    if not rows:
        raise ValueError("没有可复核的轨迹")
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "wt", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(path.resolve())


def main():
    """复核成功后保存净空表；任选额外净空阈值，小于阈值时进程返回非零。"""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question", choices=("q2", "q3", "q4"))
    parser.add_argument("directory", type=Path, help="Q2/Q4 的归档目录，或 Q3 的完整路线 JSON")
    parser.add_argument("--wall-clearance-m", type=float, default=0.0)
    parser.add_argument("--execution-error-bound-m", type=float, help="Q3 条件性证明使用的全程误差硬上界（m）")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference", type=Path, help="相同初始排程的 Q4 标称结果，用于逐场比分")
    parser.add_argument("--paired-output", type=Path, help="Q4 推荐策略的净空与开测时间配对 CSV")
    args = parser.parse_args()
    if bool(args.reference) != bool(args.paired_output) or (args.reference and args.question != "q4"):
        parser.error("--reference 与 --paired-output 须在 q4 同时给出")
    scene = load_scene()
    if args.question == "q3":
        try:
            route = json.loads(args.directory.read_text(encoding="utf-8"))
            report = verify_q3_route(scene, route, wall_clearance_m=args.wall_clearance_m,
                                     execution_error_bound_m=args.execution_error_bound_m)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")
        print(args.output.resolve())
        return 0
    if args.execution_error_bound_m is not None:
        parser.error("Q2/Q4 的执行误差预算请在各自的独立验收入口配置")
    measure = lru_cache(maxsize=16384)(lambda points: measure_route(scene, points, args.wall_clearance_m))
    rows = list((q2_rows if args.question == "q2" else q4_rows)(args.directory, scene, measure))
    if args.reference:
        nominal = lru_cache(maxsize=16384)(lambda points: measure_route(scene, points, 0.0))
        reference_rows = q4_rows(args.reference, scene, nominal)
        paired = list(compare_q4(args.directory, args.reference, rows, reference_rows))
        write_rows(args.paired_output, paired)
    write_rows(args.output, rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
