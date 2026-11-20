"""B 题第二问运行入口，编排双站复核、网格调度与同口径基线。
输入 YAML 与运行档位；输出路线、时间轴、图表和版本记录到 runs/q2/。
运行：nohup python3 -B -m src.run q2 --profile full
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol（版本发布日期未提供），2026-11-20。
"""

from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import time

import numpy as np

from .paths import PADDING_M
from .precision import simulate_intersections, solve_t2
from .report import critical_chain, recover_plan, write_csv, write_json, write_plots
from .scene import build_problem, build_router, read_scene
from .schedule import SELECT_TOL_S, greedy, optimize, remap_plan, shorten_single_use_routes


def _latest_row(plan, targets) -> dict:
    """记录不同告警优先序的真实开测时刻及其最大值（s）。"""
    return {"order": [targets[j] for j in plan.order], "status": "feasible",
            "latest_start_s": plan.latest_s, "alert_start_s": dict(zip(targets, plan.starts_s))}


def _metadata(root: Path, command: str, profile: str, elapsed_s: float) -> dict:
    """保留复现命令、版本、源文件哈希和机器上真实计时。"""
    files = [root / "src/run.py", *(root / "src/q1").glob("*.py"), *(root / "src/q2").glob("*.py")]
    result = {"command": command, "profile": profile, "elapsed_s": elapsed_s,
              "python": platform.python_version(),
              "dependencies": {library: version(library) for library in ("numpy", "scipy", "PyYAML", "matplotlib")},
              "source_sha256": {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)},
              "git_head": None, "git_worktree_status": None,
              "ai_assistance": "OpenCode / Magpie group/gpt-6.2-sol, 2026-11-20. 供队内独立核对。"}
    try:
        result["git_head"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        result["git_worktree_status"] = subprocess.check_output(
            ["git", "status", "--short", "--untracked-files=all", "--", "src", "configs", "checks"],
            cwd=root, text=True,
        ).splitlines()
    except (OSError, subprocess.CalledProcessError):
        # 无 Git 的解包副本仍可通过哈希复跑。
        pass
    return result


def run_q2(config_path: Path, output_root: Path, profile: str, root: Path, command: str) -> Path:
    """对题设两小问依次求解，保存完整实验；full 比较所有配置网格。"""
    begin = time.perf_counter()
    scene = read_scene(config_path)
    router = build_router(scene, scene.path_polygon_sides)
    q21, sensitivity = solve_t2(scene, router)
    sensitivity.extend(simulate_intersections(q21["radius_lower_bound_m"], scene.bearing_sigma_deg,
                                              samples=scene.monte_carlo_samples, seed=scene.random_seed))
    added = [row["site_xy_m"] for row in q21["routes"]] + list(scene.refinement_sites_m)

    # 下一档仍保留上档所有候选；把上一档可行方案和本档就近方案都作为上界候选。
    steps = scene.grid_m if profile == "full" else scene.grid_m[:1]
    refinement, prior, problem = [], None, None
    for level, step in enumerate(steps):
        old_problem = problem
        problem = build_problem(scene, router, steps[:level + 1], added)
        all_baselines = greedy(problem)
        baseline = min(all_baselines, key=lambda p: p.latest_s)
        upper = baseline if prior is None else min(baseline, remap_plan(prior, old_problem, problem),
                                                   key=lambda p: p.latest_s)
        opt, proof = optimize(problem, upper)
        opt = shorten_single_use_routes(problem, opt)
        prior = opt
        refinement.append({"grid_m": step, "candidate_counts": dict(zip(scene.targets, map(lambda j: len(j.points), problem.jobs))),
                           "greedy_latest_start_s": baseline.latest_s, "optimized_latest_start_s": opt.latest_s,
                           "greedy_orders": [_latest_row(p, scene.targets) for p in all_baselines], "vehicle_search": proof})
        print(f"q2 {step:g} m：就近 {baseline.latest_s:.6f} s，优化 {opt.latest_s:.6f} s", flush=True)

    fixed, fixed_search = optimize(problem, baseline, fixed_vehicles=True)
    fixed = shorten_single_use_routes(problem, fixed)
    base_result = recover_plan("greedy", scene, problem, router, baseline)
    opt_result = recover_plan("optimized", scene, problem, router, opt)
    fixed_result = recover_plan("fixed_vehicles", scene, problem, router, fixed)
    plans = [base_result, opt_result]
    # 真候选集上放松避障行驶为端点直线距离：对任何真路线均为下界，且可穷尽求解。
    relaxation = build_problem(scene, router, steps, added, relax_travel=True)
    relaxed_seed = remap_plan(opt, problem, relaxation)
    lower_schedule, lower_search = optimize(relaxation, relaxed_seed)
    if lower_schedule.latest_s > opt_result["latest_s"] + SELECT_TOL_S:
        raise RuntimeError("欧氏路长松弛值大于已验证路径的上界")

    path_comparison = []
    comparison_router = build_router(scene, scene.path_comparison_sides)
    for plan in (*plans, fixed_result):
        for row in plan["routes"]:
            coarse_length = comparison_router.route(row["origin"], row["destination"]).length_m
            path_comparison.append({"plan": plan["plan"], "vehicle": row["vehicle"], "target": row["target"],
                                    "selected_sides": scene.path_polygon_sides, "comparison_sides": scene.path_comparison_sides,
                                    "selected_length_m": row["length_m"],
                                    "comparison_length_m": coarse_length if np.isfinite(coarse_length) else None,
                                    "abs_difference_m": abs(coarse_length - row["length_m"])
                                    if np.isfinite(coarse_length) else None})

    base_time, opt_time = (row["latest_s"] for row in plans)
    comparison = {"objective": "minimum latest synchronized observation start (s)",
                  "candidate_refinement": refinement, "path_polygon_sides": scene.path_polygon_sides,
                  "refinement_sites_m": scene.refinement_sites_m,
                  "disk_outer_excess_max_m": router.max_excess_m, "path_radial_padding_m": PADDING_M,
                  "same_alert_min_center_distance_m": problem.min_pair_distance_m,
                  "baseline_definition": "minimum total obstacle-avoiding incremental distance, then joint ready time, then IDs; best of six priorities",
                  "model_scope": "Finite candidate sites, shortest paths on the padded polygon visibility graph, and no dynamic intervehicle detours or delays. Independent replay checks actual separation.",
                  "finite_candidate_solution": {"certificate": "all assignment and relevant site branches exhausted",
                                                 "latest_start_s": opt_time, "numerical_tolerance_s": SELECT_TOL_S},
                  "euclidean_relaxation": {"candidate_counts_before_path_filter": {alert: len(job.points)
                                                                                  for alert, job in zip(scene.targets, relaxation.jobs)},
                                           "latest_start_lower_bound_s": lower_schedule.latest_s,
                                           "feasible_upper_bound_s": opt_time,
                                           "gap_s": max(0., opt_time - lower_schedule.latest_s),
                                           "vehicle_search": lower_search},
                  "plan_summaries": {p["plan"]: {k: val for k, val in p.items() if k not in ("assignments", "routes", "timeline")}
                                     for p in (*plans, fixed_result)},
                  "latest_start_saved_s": base_time - opt_time,
                  "latest_start_relative_saved": (base_time - opt_time) / base_time if base_time else None,
                  "fixed_vehicles_search": fixed_search,
                  "critical_chains": {p["plan"]: critical_chain(p) for p in (*plans, fixed_result)}}

    output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_")
    directory = Path(tempfile.mkdtemp(prefix=stamp, dir=output_root))
    shutil.copy2(config_path, directory / "config.yaml")
    shutil.copy2(scene.field_file, directory / "scene_config.yaml")
    # 快照引用自包含；保留所用原始参数内容和源配置哈希。
    snapshot = directory / "config.yaml"
    original = snapshot.read_text(encoding="utf-8")
    prefix = "scene_config:"
    snapshot.write_text("\n".join(f"{prefix} scene_config.yaml" if line.startswith(prefix) else line
                                  for line in original.splitlines()) + "\n", encoding="utf-8")
    write_json(directory / "q21_result.json", q21)
    write_csv(directory / "q21_sensitivity.csv", sensitivity)
    write_csv(directory / "q22_candidates.csv", [{"target": alert, "site_id": i, "x_m": x, "y_m": y}
                                                  for alert, job in zip(scene.targets, problem.jobs)
                                                  for i, (x, y) in enumerate(job.points)])
    for key, filename in (("assignments", "q22_assignments.csv"), ("timeline", "q22_timeline.csv")):
        write_csv(directory / filename, [row for plan in (*plans, fixed_result) for row in plan[key]])
    write_json(directory / "q22_routes.json", [r for p in (*plans, fixed_result) for r in p["routes"]])
    write_json(directory / "q22_comparison.json", comparison)
    write_csv(directory / "q22_path_comparison.csv", path_comparison)
    write_plots(directory, scene, q21, [opt_result, base_result])
    metadata = _metadata(root, command, profile, time.perf_counter() - begin)
    metadata["input_sha256"] = {"q2_config": hashlib.sha256(config_path.read_bytes()).hexdigest(),
                                "scene_config": hashlib.sha256(scene.field_file.read_bytes()).hexdigest()}
    write_json(directory / "run_record.json", metadata)
    return directory
