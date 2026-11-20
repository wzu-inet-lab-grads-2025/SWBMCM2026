"""B 题第四问批量运行入口，独立进程运行四个模型及两种既有调度基线。
输入 q4 配置与第二问归档；输出到 runs/q4 下独立目录，包含验证、时序、图表和复跑记录。
运行：nohup env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 -B -m src.run q4 --profile full。
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol，2026-11-20（版本发布日期未提供）。
"""

from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from functools import lru_cache
from importlib.metadata import version
import math
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import time

import numpy as np
import yaml

from checks.q4_replay import Validator
from src.q2.report import write_csv, write_json

from .candidates import CandidateBank, Geometry, Scenario, digest, prefix_hash, scenarios
from .exact import exact
from .heuristics import Solution, alns, insertion
from .physics import Clock, build_prefix, load_archive
from .problem import Instance, Plan


ALGORITHMS = ("cp_sat", "milp", "alns", "insertion")
STRATEGIES = ("original_append", "strict_frozen", *ALGORITHMS, "recommended")
VERSIONS = ("main", "fine_tick", "fine_grid", "angle_guard", "inclusive_window", "flexible_original_stations")
LABELS = {"E1": "New alert", "E2": "Vehicle failure", "E3": "Outage", "E4": "New + failure",
          "E5": "New + outage", "E6": "Failure + outage", "E7": "All three"}


@dataclass(frozen=True)
class Task:
    """情景和数值敏感性版本：只有主模型和同一版本参与横向公平排名。"""

    scenario: Scenario
    variant: str = "main"


@dataclass(frozen=True)
class Record:
    """送回主进程的模型输出，含独立验收的连续轨迹与共享输入指纹。"""

    info: dict
    solutions: dict[str, Solution]
    trials: list[Solution]
    assets: dict | None = None


class Worker:
    """每个进程生成同一份场景输入，缓存重复候选或同一场景的几何和快照。"""

    def __init__(self, archive_dir, config, profile):
        """读取一次归档、建立可见图；各模型均固定单线程。"""
        self.archive = load_archive(archive_dir)
        self.geometry = Geometry(self.archive)
        self.config = config
        self.profile = profile
        self._banks = {}
        self.instance = lru_cache(maxsize=3)(self._instance)
        self.prefix = lru_cache(maxsize=64)(lambda f, t: build_prefix(self.archive, f, t))

    def _bank(self, variant: str) -> CandidateBank:
        """主目录、半米网格和有条件交会角阈值分别建目录。"""
        bank_type = variant if variant in ("fine_grid", "angle_guard") else "main"
        if bank_type not in self._banks:
            c = self.config
            kw = ({"grid_m": c["fine_grid_m"], "pair_limit": c["fine_pair_limit"]} if bank_type == "fine_grid" else
                  {"min_sine": math.sin(math.radians(c["angle_sensitivity_deg"]))} if bank_type == "angle_guard" else {})
            self._banks[bank_type] = CandidateBank(self.archive, self.geometry, c, **kw)
        return self._banks[bank_type]

    def _instance(self, task: Task) -> tuple[Instance, Validator, dict]:
        """独立进程据输入事件构造实例，并为状态、站对和路径写入哈希。"""
        sc, variant = task.scenario, task.variant
        c, arc = self.config, self.archive
        prefix = self.prefix(sc.failed, sc.dispatch)
        clock = Clock(c["fine_tick_s"] if variant == "fine_tick" else c["tick_s"], variant == "inclusive_window")
        bank = self._bank(variant) if sc.location or variant == "flexible_original_stations" else None
        catalog = bank.catalog(sc.location) if sc.location else None
        if sc.location and variant == "fine_grid":
            # 扩集且保留原可行见证，避免加密时因筛选上限换掉原来的好站对。
            original = self._bank("main").catalog(sc.location)
            pairs = tuple(dict.fromkeys((*original.pairs, *catalog.pairs)))
            catalog = replace(catalog, pairs=pairs, sha256=digest(pairs))
        extra = None
        if variant == "flexible_original_stations":
            # 原告警和索引直接来自同一份 Q2 归档；只扩大尚未锁定的站对。
            extra = {a: bank.for_target(target).pairs[:13] for a, target in enumerate(arc.scene.target_cfg)
                     if a not in prefix.locked}
        instance = Instance(arc, self.geometry, sc, prefix, clock, catalog, extra_pairs=extra)
        info = {"scenario_id": sc.key, "category": sc.category, "disturbance_type": LABELS[sc.category],
                "sample": sc.sample, "variant": variant, "theta_deg": sc.theta, "R_m": sc.radius, "H_m": sc.height,
                "failed_vehicle": arc.scene.vehicles[sc.failed] if sc.failed is not None else None,
                "failure_probability": 0.2 if sc.failed is not None else None, "t_rec_s": sc.recovery,
                "outage_duration_s": sc.recovery - 5.0 if sc.recovery is not None else None, "dispatch_s": sc.dispatch,
                "N": len(instance.names), "tick_s": clock.tick, "inclusive_60": clock.inclusive,
                "initial_schedule_hash": digest(arc.hashes), "vehicle_state_snapshot_hash": prefix_hash(prefix),
                "station_candidate_set_hash": digest(instance.pairs), "path_cache_hash": instance.path_hash,
                "locked_at_dispatch": tuple(instance.names[a] for a in sorted(prefix.locked))}
        if catalog:
            info.update({"t4_valid_grid_sites": catalog.visible_count, "grid_sites_before_los": catalog.grid_count,
                         "t4_pool_sites": catalog.pool_count, "t4_legal_pairs_in_pool": catalog.legal_pair_count,
                         "t4_retained_pairs": len(catalog.pairs), "t4_best_pool_rmse_m": catalog.best_rmse_m})
        return instance, Validator(instance), info

    def run(self, task: Task, model: str, seed: Plan | None) -> Record:
        """一个待收结果的独立进程作业：预处理、A、B 或 C。"""
        ins, check, info = self.instance(task)
        if model == "prepare":
            pre = time.perf_counter()
            frozen = check.original()
            append = check.append()
            if not frozen.valid or not append.valid:
                raise RuntimeError(f"{task.scenario.key} 原计划基线未通过连续验收")
            baseline_log = {"status": "BASELINE", "solve_s": time.perf_counter() - pre}
            if ins.pending:
                heuristic = insertion(ins, check, beam=self.config["beam_width"])
                if ins.predict(heuristic.replay.plan) != heuristic.replay.starts_tick:
                    raise RuntimeError(f"{task.scenario.key} 插入法时钟不一致")
            else:
                heuristic = Solution(frozen, {"status": "NO_OP", "solve_s": 0.0})
            assets = {"states": [asdict(s) for s in ins.prefix.states], "locked": ins.prefix.locked,
                      "pairs": ins.pairs, "targets": ins.targets, "names": ins.names}
            return Record(info, {"original_append": Solution(append, baseline_log), "strict_frozen": Solution(frozen, baseline_log),
                                 "insertion": heuristic}, [], assets)
        if not ins.pending:
            noop = Solution(check.original(), {"status": "NO_OP", "k_bound": len(ins.names), "solve_s": 0.0})
            trials = [Solution(noop.replay, {**noop.log, "seed": seed}) for seed in self.config["alns_seeds"]] if model == "alns" else []
            return Record(info, {model: noop}, trials)
        initial = check.dynamic(seed)
        if not initial.valid:
            raise RuntimeError(f"{task.scenario.key} 共享的 warm start 在其他进程不再可行：{initial.violations}")
        if model == "alns":
            best, trials = alns(ins, check, initial.plan, self.config["alns_seeds"], self.config["alns_iterations"])
            if any(ins.predict(trial.replay.plan) != trial.replay.starts_tick for trial in trials):
                raise RuntimeError(f"{task.scenario.key} ALNS 时钟不一致")
            return Record(info, {model: best}, trials)
        result = exact(ins, check, initial, model, self.config["exact_budget_s"])
        return Record(info, {model: result}, [])


_WORKER: Worker | None = None


def _init_worker(archive_dir, config, profile):
    """每个 worker 进程接收统一配置；不启动任何其他优化线程。"""
    global _WORKER
    _WORKER = Worker(archive_dir, config, profile)


def _job(task, model, plan=None):
    """进程池序列化入口；提交时选定模型，而非在结果出来后改口径。"""
    return _WORKER.run(task, model, plan)


def _run_group(executor, tasks: list[Task], name: str, consume):
    """以短批次调度四算法；warm start 后 A/B/C 独立并行，并验明四份输入一致。"""
    total = len(tasks)
    for offset in range(0, total, 48):
        batch = tasks[offset:offset + 48]
        futures = {executor.submit(_job, task, "prepare"): task for task in batch}
        seeds, subjobs = {}, {}
        for job in as_completed(futures):
            task, record = futures[job], job.result()
            seeds[task] = record
            seed = record.solutions["insertion"].replay.plan
            for m in ("cp_sat", "milp", "alns"):
                subjobs[executor.submit(_job, task, m, seed)] = task
        for job in as_completed(subjobs):
            task, other = subjobs[job], job.result()
            record = seeds[task]
            if other.info != record.info:
                raise RuntimeError(f"{task.scenario.key} 算法使用了不同的输入指纹")
            record.solutions.update(other.solutions)
            record.trials.extend(other.trials)
        for task in batch:
            consume(task, seeds[task])
        print(f"q4 {name}: {min(total, offset + len(batch))}/{total}", flush=True)


def _critical(locations, rows, count, height_m, recovery_values):
    """在 T4 方向或距离的相邻格中点按 η、t_last、可见站和几何变化加密。"""
    groups: dict[tuple, dict] = {}
    for row in rows:
        if row["model"] != "recommended" or row["theta_deg"] is None:
            continue
        key = (row["theta_deg"], row["R_m"])
        val = (row["eta"], row["t_last_s"], row["t4_valid_grid_sites"], row["t4_best_pool_rmse_m"],
               row["t4_legal_pairs_in_pool"])
        groups.setdefault(key, {})[(row["category"], row["failed_vehicle"], row["t_rec_s"])] = val
    angles = sorted({loc[0] for loc in locations})
    ranges = sorted({loc[1] for loc in locations})
    edges = []
    for t in angles:
        for r in ranges:
            a = (t, r)
            for dim in range(2):
                axis = angles if dim == 0 else ranges
                k = axis.index(a[dim]) + 1
                if k == len(axis) and dim == 1:
                    continue
                b = (axis[k % len(axis)], r) if dim == 0 else (t, axis[k])
                loc = (((a[0] + (b[0] if b[0] > t else b[0] + 360.0)) / 2) % 360, r) if dim == 0 else (t, (a[1] + b[1]) / 2)
                score, witness = (-1,), None
                for key in groups[a].keys() & groups[b].keys():
                    x, y = groups[a][key], groups[b][key]
                    delta = (abs(x[0] - y[0]), abs(x[1] - y[1]), abs(x[2] - y[2]), abs((x[3] or 0) - (y[3] or 0)), abs(x[4] - y[4]))
                    if delta > score or (delta == score and repr(key) < repr(witness)):
                        score, witness = delta, key
                edges.append((score, loc, a, b, witness))
    selections = []
    for score, point, a, b, witness in sorted(edges, key=lambda row: (row[0], row[1]), reverse=True):
        if count <= 0 or len(selections) == count:
            break
        if point not in {s[0] for s in selections}:
            selections.append((point, a, b, witness, score))
    tasks = [Task(sc) for sc in scenarios([(*item[0], height_m) for item in selections], recovery_values,
                                         sample="critical_refinement", include_no_t4=False)]
    return tasks, [{"theta_deg": p[0], "R_m": p[1], "brackets": (a, b), "witness": w, "deltas": d}
                   for p, a, b, w, d in selections]


def _sensitivity(main_tasks, rows, limit):
    """预定覆盖七类及上限邻近情景；保持主结果不被事后加密替代。"""
    id_to_task = {task.scenario.key: task for task in main_tasks}
    recommended = [r for r in rows if r["model"] == "recommended" and r["sample"] == "main"]
    if not recommended:
        return []
    choices = []
    for category in LABELS:
        group = [r for r in recommended if r["category"] == category]
        if group:
            choices.append(min(group, key=lambda r: (r["eta"], -r["t_last_s"], r["scenario_id"])))
    incomplete = sorted((r for r in recommended if r["eta"] < 1), key=lambda r: (r["eta"], r["scenario_id"]))
    borderline = sorted((r for r in recommended if r["eta"] == 1), key=lambda r: (-r["t_last_s"], r["scenario_id"]))
    keys = list(dict.fromkeys(r["scenario_id"] for r in (*choices, *incomplete[:4], *borderline)))[:limit]
    tasks = [Task(id_to_task[key].scenario, variant) for key in keys for variant in VERSIONS[1:]
             if variant not in {"fine_grid", "angle_guard"} or id_to_task[key].scenario.location]
    return tasks


def _metadata(root, config_path, config, profile, command, archive, geometry, elapsed, *, cases):
    """保存题面输入、软件版本、当前源代码哈希和运行机器；不冒充无未提交改动。"""
    files = [root / "src/run.py", config_path, *sorted((root / "src/q4").glob("*.py")),
             *(root / "checks" / name for name in ("q4_replay.py", "q2_geometry.py", "q2_validate.py")),
             *sorted((root / "src/q2").glob("*.py")), *sorted((root / "src/q1").glob("*.py"))]
    import hashlib
    def file_id(path):
        return str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False)
    status = subprocess.run(["git", "status", "--short", "--untracked-files=all", "--", "src", "configs", "checks"],
                            cwd=root, capture_output=True, text=True, check=False)
    cpu = Path("/proc/cpuinfo").read_text().splitlines()
    mem = Path("/proc/meminfo").read_text().splitlines()[0]
    return {"command": command, "profile": profile, "elapsed_s": elapsed, "cases": cases, "git_head": revision.stdout.strip(),
            "git_worktree_status": status.stdout.splitlines(), "python": platform.python_version(), "OS": platform.platform(),
            "CPU": next((line.split(":", 1)[1].strip() for line in cpu if line.startswith("model name")), platform.machine()),
            "memory": mem, "workers": config["workers"], "threads_per_solver": 1, "exact_budget_s": config["exact_budget_s"],
            "dependencies": {key: version(key) for key in ("numpy", "scipy", "PyYAML", "matplotlib", "ortools")},
            "q2_archive_inputs_sha256": archive.hashes,
            "source_sha256": {file_id(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
            "path_polygon_sides": archive.scene.path_polygon_sides, "maximum_disk_outer_excess_m": geometry.router.max_excess_m,
            "ai_assistance": "OpenCode / Magpie group/gpt-6.2-sol, 2026-11-20（版本发布日期未提供）"}


def run_q4(config_path: Path, output: Path, profile: str, root: Path, command: str) -> Path:
    """运行正式 1751 个主情景、临界加密和数值灵敏度，或少量开发情景。"""
    from .report import Output, make_reports

    begin = time.perf_counter()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    archive_dir = (config_path.parent / config["q2_archive"]).resolve()
    archive = load_archive(archive_dir)
    geometry = Geometry(archive)
    output.mkdir(parents=True, exist_ok=True)
    destination = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_"), dir=output))
    snapshot = dict(config)
    import os
    snapshot["q2_archive"] = os.path.relpath(archive_dir, destination)
    (destination / "config.yaml").write_text(yaml.safe_dump(snapshot, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"q4 output: {destination}", flush=True)
    recovery = config["recovery_s"]
    height = config["alert_height_m"]
    locations = [(float(t), float(r), float(height)) for t in np.arange(0, 360, config["azimuth_step_deg"])
                 for r in config["ranges_m"]]
    if profile == "debug":
        locations = [(7.5, 37, height), (155, 47, height), (253, 35, height)]
        main = [Task(s) for s in scenarios(locations, [8, 23], sample="development")
                if s.failed in (None, 1, 2) and (s.recovery != 23 or s.category in ("E3", "E6", "E7"))]
        config = {**config, "workers": min(config["workers"], 6)}
    else:
        main = [Task(s) for s in scenarios(locations, recovery)]
    with Output(destination, archive) as store:
        with ProcessPoolExecutor(max_workers=config["workers"], initializer=_init_worker,
                                 initargs=(archive_dir, config, profile)) as pool:
            _run_group(pool, main, "development" if profile == "debug" else "main", store.write)
            if profile == "full":
                more, critical = _critical(locations, store.rows, config["critical_points"], height, recovery)
            else:
                more, critical = [], []
            _run_group(pool, more, "critical", store.write)
            if profile == "full":
                sensitivity = _sensitivity(main, store.rows, config["sensitivity_cases"])
            else:
                sensitivity = [Task(main[-1].scenario, variant) for variant in VERSIONS[1:]]
            _run_group(pool, sensitivity, "sensitivity", store.write)
        write_json(destination / "critical_refinement.json", critical)
    stats = _metadata(root, config_path, config, profile, command, archive, geometry, time.perf_counter() - begin,
                      cases={"main_or_dev": len(main), "critical": len(more), "sensitivity": len(sensitivity)})
    write_json(destination / "run_record.json", stats)
    make_reports(destination, archive, geometry, store.rows, stats)
    return destination
