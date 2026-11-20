"""第四问附加压力复算：检查主实验中交会角接近退化和最晚开测的实例。
输入完整主运行；输出独立的压力运行目录、配对底表和来源记录。
运行：nohup python3 -B -m src.q4.stress runs/q4/<完整运行编号>。
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol，2026-11-20（版本发布日期未提供）。
"""

import argparse
from concurrent.futures import ProcessPoolExecutor
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import math
import os
from pathlib import Path
import shlex
from statistics import mean, median
import sys
import tempfile
import time

import yaml

from src.q2.report import write_csv, write_json
from .candidates import Geometry, Scenario
from .experiment import Task, _init_worker, _metadata, _run_group
from .physics import load_archive
from .report import Output


def rows(path: Path):
    """逐行读取 CSV 或 gzip CSV，保留全精度底表的原字段。"""
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", newline="") as fp:
        yield from csv.DictReader(fp)


def main_sample(row):
    """附加实验不使用临界位置或其他敏感性版本来修改主结果。"""
    return row["sample"] == row["variant"] == "main"


def margin(row) -> float:
    """双视线距 0°/180° 退化点的最小角距离（度）。"""
    angle = float(row["intersection_deg"])
    return min(angle, 180 - angle)


def _optional_float(value):
    """非扰动空值留空，其余字段解释为题面的物理单位。"""
    return float(value) if value else None


def run_path(source: Path, output: Path) -> Path:
    """对全部近共线 T4 强制对照最小交会余角，另复算最迟个案及两处连续排序差。"""
    begin = time.perf_counter()
    config_path = source / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    archive_path = (source / config["q2_archive"]).resolve()
    archive = load_archive(archive_path)
    metrics = [r for r in rows(source / "model_comparison.csv") if main_sample(r)]
    primary = {r["scenario_id"]: r for r in metrics if r["model"] == "recommended"}
    primary_a = {r["scenario_id"]: r for r in metrics if r["model"] == "cp_sat"}
    original = {r["scenario_id"]: r for r in rows(source / "alert_results.csv.gz") if r["scenario_id"] in primary
                and r["variant"] == "main" and r["model"] == "recommended" and r["alert"] == "T4" and r["intersection_deg"]}
    if len(primary) != 1751:
        raise ValueError("附加压力复算需要全部 1751 个主情景")

    def scenario_for(key):
        """由归档主键构造同一扰动，禁止改动时间或故障概率。"""
        r = primary[key]
        failed = archive.scene.vehicles.index(r["failed_vehicle"]) if r["failed_vehicle"] else None
        return Scenario(r["category"], *(_optional_float(r[f]) for f in ("theta_deg", "R_m", "H_m")), failed,
                        _optional_float(r["t_rec_s"]))

    cutoff = config["angle_sensitivity_deg"]
    near = [key for key in original if margin(original[key]) < cutoff]
    gaps = [key for key in primary if abs(float(primary[key]["t_last_s"]) - float(primary_a[key]["t_last_s"])) > 1e-8]
    slowest = max(primary, key=lambda key: (float(primary[key]["t_last_s"]), key))
    tasks = [Task(scenario_for(key), "angle_guard") for key in sorted(near)]
    tasks.extend(Task(scenario_for(key), "fine_tick") for key in sorted({slowest, *gaps}))
    tasks.append(Task(scenario_for(slowest), "flexible_original_stations"))
    output.mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_stress_"), dir=output))
    snapshot = {**config, "q2_archive": os.path.relpath(archive_path, path)}
    (path / "config.yaml").write_text(yaml.safe_dump(snapshot, allow_unicode=True, sort_keys=False), encoding="utf-8")
    with Output(path, archive) as store:
        with ProcessPoolExecutor(max_workers=config["workers"], initializer=_init_worker,
                                 initargs=(archive_path, config, "stress")) as pool:
            _run_group(pool, tasks, "stress", store.write)
    new = {(r["scenario_id"], r["variant"]): r for r in rows(path / "alert_results.csv.gz") if r["model"] == "recommended"
           and r["alert"] == "T4" and r["intersection_deg"]}
    comparison = []
    for row in store.rows:
        if row["model"] != "recommended":
            continue
        key, variant = row["scenario_id"], row["variant"]
        before, after = original[key], new.get((key, variant))
        baseline = primary[key]
        base_time = float(baseline["t_last_s"])
        comparison.append({"scenario_id": key, "variant": variant, "source": row["source"], "main_eta": baseline["eta"],
                           "stress_eta": row["eta"], "main_t_last_s": base_time, "stress_t_last_s": row["t_last_s"],
                           "delta_t_last_s": row["t_last_s"] - base_time, "main_angle_margin_deg": margin(before),
                           "stress_angle_margin_deg": margin(after) if after else None, "main_rmse_m": float(before["rmse_m"]),
                           "stress_rmse_m": float(after["rmse_m"]) if after else None})
    write_csv(path / "paired_stress.csv", comparison)
    guard = [r for r in comparison if r["variant"] == "angle_guard"]
    assert all(r["stress_angle_margin_deg"] >= cutoff - 1e-8 for r in guard)
    time_change = [r["delta_t_last_s"] for r in guard]
    metrics = lambda col: {"min": min(col), "mean": mean(col), "median": median(col), "max": max(col)} if col else None
    write_json(path / "stress_summary.json", {"reference_run": source.name, "reference_main_cases": len(primary),
               "guard_deg": cutoff, "guard_cases": len(guard), "guard_all_started": sum(r["stress_eta"] == 1 for r in guard),
               "guard_delta_t_last_s": metrics(time_change), "guard_equal_official": sum(abs(t) <= 1e-8 for t in time_change),
               "guard_main_rmse_m": metrics([r["main_rmse_m"] for r in guard]),
               "guard_revised_rmse_m": metrics([r["stress_rmse_m"] for r in guard]),
                "additional_cases": [r for r in comparison if r["variant"] != "angle_guard"],
                "selection_basis": "all primary T4 angle margins below guard, two continuous best-model differences, latest start case"})
    record = _metadata(Path(__file__).resolve().parents[2], config_path, config, "stress", shlex.join(sys.orig_argv),
                       archive, Geometry(archive), time.perf_counter() - begin, cases={"stress": len(tasks)})
    record["reference_run"] = source.name
    record["reference_inputs_sha256"] = {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in
                                         ("config.yaml", "run_record.json", "model_comparison.csv", "alert_results.csv.gz")}
    write_json(path / "run_record.json", record)
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="第四问定向压力复算")
    parser.add_argument("source", type=Path)
    parser.add_argument("--output-root", type=Path, default=Path("runs/q4"))
    opts = parser.parse_args()
    print(run_path(opts.source.resolve(), opts.output_root.resolve()))
