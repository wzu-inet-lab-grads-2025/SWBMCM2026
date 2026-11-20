"""第四问本地归档预览：验收完整主/压力运行后提取结果，可选证据 ZIP。
输入两个 runs/q4 子目录；仅新建 runs/q4 下的预览和可选 ZIP，不发布到 results 或 submission。
运行：nohup python3 -B -m tools.q4_archive runs/q4/<全量编号> runs/q4/<压力编号>。
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol，2026-11-20（版本发布日期未提供）。
"""

import argparse
import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import yaml

from checks.q4_validate_run import POLICIES, verify
from src.q4.physics import load_archive
from src.q4.report import exported_tick_gap, write_representative_timeline, write_vehicle_timelines


ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs/q4"
REPORT_FILES = ("algorithm_summary.csv", "category_summary.csv", "failure_summary.csv", "recovery_summary.csv",
                "spatial_summary.csv", "sensitivity.csv", "alns_stability.csv", "scenario_manifest.csv",
                "scenario_inputs.jsonl.gz", "alert_results.csv.gz", "critical_refinement.json",
                "summary.json", "run_record.json") + tuple(
    f"{stem}.{ext}" for stem in ("policy_performance", "disturbance_effects", "candidate_regions")
    for ext in ("pdf", "png"))
EVIDENCE_FILES = ("config.yaml", "scenario_manifest.csv", "scenario_inputs.jsonl.gz", "model_comparison.csv",
                   "alert_results.csv.gz", "vehicle_timelines.csv.gz", "plans_and_tracks.jsonl.gz", "solver_log.jsonl.gz",
                   "validation.jsonl.gz", "alns_seed_results.csv.gz")


def _load(path):
    """从相同目录结构读取已生成的记录，不调用优化器。"""
    return json.loads(path.read_text(encoding="utf-8"))


def _hash(path):
    """以 SHA-256 标识文件的原始字节，不重新序列化数值。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _config(path):
    """统一解析相对 Q2 引用，搬运前检查基准与其他配置均未变化。"""
    settings = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {**settings, "q2_archive": (path.parent / settings["q2_archive"]).resolve()}


def _recommended_scores(directory, *, main_only):
    """读取同一主键的推荐结果，压力回放比较只配对正式主样本。"""
    with (directory / "model_comparison.csv").open(encoding="utf-8", newline="") as src:
        return {(r["scenario_id"], r["variant"]): r for r in csv.DictReader(src) if r["model"] == "recommended" and
                (not main_only or r["sample"] == r["variant"] == "main")}


def _check_stress_pairs(full, stress, n_stress):
    """旧压力记录没有引用文件哈希时，仍必须逐场核查保存的主/压力配对分数。"""
    main = _recommended_scores(full, main_only=True)
    tested = _recommended_scores(stress, main_only=False)
    with (stress / "paired_stress.csv").open(encoding="utf-8", newline="") as src:
        pairs = list(csv.DictReader(src))
    if len(pairs) != n_stress or {(r["scenario_id"], r["variant"]) for r in pairs} != tested.keys():
        raise ValueError("压力复算配对底表缺项、重复或主键未对齐")
    for r in pairs:
        key = r["scenario_id"], r["variant"]
        ref, actual = main[key[0], "main"], tested[key]
        if not (r["source"] == actual["source"] and
                all(float(r[f"{label}_{field}"]) == float(row[field])
                    for label, row in (("main", ref), ("stress", actual)) for field in ("eta", "t_last_s")) and
                abs(float(r["delta_t_last_s"]) - (float(actual["t_last_s"]) - float(ref["t_last_s"]))) < 1e-8):
            raise ValueError(f"压力复算配对与两次运行的推荐分数不一致：{key}")


def _check_runs(full, stress):
    """用当前独立验收器重查即将复制的文件；旧验收报告不能替代复查。"""
    for directory in (full, stress):
        if directory.parent != RUNS:
            raise ValueError(f"运行目录须在仓库 runs/q4 中：{directory}")
        for name in EVIDENCE_FILES:
            if not (directory / name).is_file():
                raise FileNotFoundError(directory / name)
    status = _load(full / "run_record.json")
    summary = _load(full / "summary.json")
    stress_report = _load(stress / "stress_summary.json")
    if (status["profile"] != "full" or status["cases"]["main_or_dev"] != 1751 or not summary["all_reports_valid"] or
            stress_report["reference_run"] != full.name or stress_report["reference_main_cases"] != 1751):
        raise ValueError("输入不是同源的 1751 主样本及压力复算")
    config = _config(full / "config.yaml")
    if config != _config(stress / "config.yaml"):
        raise ValueError("配置快照未精确指向同一份 Q2 和同一组实验参数")
    q2 = config["q2_archive"]
    for name, expected in status["q2_archive_inputs_sha256"].items():
        if _hash(q2 / name) != expected:
            raise ValueError(f"第二问只读基准的哈希不一致：{q2 / name}")
    stress_record = stress / "run_record.json"
    if stress_record.is_file():
        for name, expected in _load(stress_record)["reference_inputs_sha256"].items():
            if _hash(full / name) != expected:
                raise ValueError(f"压力复算引用的主运行文件已变化：{full / name}")
    n_stress = stress_report["guard_cases"] + len(stress_report["additional_cases"])
    audits = []
    for directory, n in ((full, sum(status["cases"].values())), (stress, n_stress)):
        validation = verify(directory)
        if not validation["all_valid"] or validation["scenarios"] != n or validation["plan_tracks_checked"] != 7 * n:
            raise ValueError(f"轨迹验收未通过：{directory}：{validation['errors']}")
        audits.append(validation)
    _check_stress_pairs(full, stress, n_stress)
    return q2, audits


def _copy_policy_tracks(full, result):
    """原样保留推荐及两基线在主/加密情景的轨迹 JSON 行。"""
    with gzip.open(full / "plans_and_tracks.jsonl.gz", "rt", encoding="utf-8") as src, \
            gzip.open(result / "policy_plans_and_tracks.jsonl.gz", "wt", encoding="utf-8") as dest:
        dest.writelines(line for line in src if (row := json.loads(line))["variant"] == "main" and row["model"] in POLICIES)


def write_metric_view(source: Path, destination: Path) -> None:
    """从保存的上界和下界重算离散界差列，其余 CSV 字段原样保留。"""
    with source.open(encoding="utf-8", newline="") as src, destination.open("w", encoding="utf-8", newline="") as dst:
        reader = csv.DictReader(src)
        writer = csv.DictWriter(dst, fieldnames=reader.fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in reader:
            if row["plan_latest_tick_s"] and row["latest_tick_lower_s"]:
                gap = exported_tick_gap(float(row["plan_latest_tick_s"]), float(row["latest_tick_lower_s"]))
                # 不把同一浮点尾差改写成大规模无意义的数据变更。
                if not row["latest_tick_gap_s"] or abs(float(row["latest_tick_gap_s"]) - gap) > 1e-8:
                    row["latest_tick_gap_s"] = gap
            writer.writerow(row)


def _archive_files(full, stress, q2, result):
    """选择 Q4 与其 Q1/Q2 几何依赖，不携带本地账户、隐藏文件或其他题的草稿。"""
    for folder in ("q1", "q2", "q4"):
        yield from (ROOT / "src" / folder).glob("*.py")
    yield ROOT / "src/run.py"
    yield ROOT / "configs/q4.yaml"
    for stem in ("q2_geometry", "q2_validate", "q4_replay", "q4_validate_run", "test_q4"):
        yield ROOT / "checks" / f"{stem}.py"
    for name in load_archive(q2).hashes:
        yield q2 / name
    # 预览和两次运行保留各自相对位置，使 YAML 的 Q2 引用在解压后仍有效。
    for path in sorted(result.iterdir()):
        if path.is_file() and not path.name.startswith("."):
            yield path
    for directory in (full, stress):
        names = set(EVIDENCE_FILES) | ({"algorithm_summary.csv", "summary.json", "run_record.json", "critical_refinement.json"} if directory == full
                                      else {"stress_summary.json", "paired_stress.csv"})
        if (directory / "run_record.json").is_file():
            names.add("run_record.json")
        yield from (directory / name for name in sorted(names))


def _package(full, stress, q2, result, bundle):
    """保留可移植相对路径和复核入口；不写竞赛系统或注册身份信息。"""
    full_link, stress_link = (p.relative_to(ROOT).as_posix() for p in (full, stress))
    result_link = result.relative_to(ROOT).as_posix()
    instructions = f"""# Problem B, Question 4 — Reproduction material

This is an internal evidence bundle, not a competition upload. See the
[algorithm results]({result_link}/algorithm_summary.csv),
[representative timeline]({result_link}/representative_timeline.png) and
[archive audit]({result_link}/git_archive_validation.json).

The scripts expect Linux, Git on PATH (for run metadata), and Python 3.13.
The versions used are listed in `requirements-q4.txt`. From this extracted directory:

```sh
nohup python3 -m pip install -r requirements-q4.txt
nohup env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 -B -m checks.q4_validate_run {full_link}
nohup env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 -B -m checks.q4_validate_run {stress_link}
nohup python3 -B -m checks.q4_validate_run {result_link} --archive
nohup env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 -B -m pytest -q -p no:cacheprovider checks/test_q4.py
nohup env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 -B -m src.run q4 --profile full --config {result_link}/config.yaml
```

The verification commands read saved schedules without solving them again.
The last command creates a new run. The `model_comparison.csv` files give the
official scores by scenario, variant and method. `plans_and_tracks.jsonl.gz`,
`vehicle_timelines.csv.gz` and `solver_log.jsonl.gz` preserve the full evidence.
The Q2 baseline inputs are copied unchanged. The preview contains all scenario scores,
but its vehicle tracks cover only the recommendation and the two retain policies.

[Competition AI policy](https://www.nmmcm.org.cn/News/220.html): independently
review and rewrite any material that the team adopts; disclose actual AI assistance.
"""
    versions = _load(full / "run_record.json")["dependencies"]
    requirements = "".join(f"{name}=={version}\n" for name, version in versions.items()) + "pytest==9.0.2\n"
    with ZipFile(bundle, "x", compression=ZIP_DEFLATED, compresslevel=6) as out:
        for path in sorted(set(_archive_files(full, stress, q2, result))):
            # gz、PDF 和 PNG 已压缩，不重复消耗预算。
            out.write(path, path.relative_to(ROOT).as_posix(),
                      compress_type=ZIP_STORED if path.suffix in (".gz", ".png", ".pdf") else ZIP_DEFLATED)
        out.writestr("README.md", instructions)
        out.writestr("requirements-q4.txt", requirements)


def main():
    """重建本地归档预览；只有显式要求时才额外创建证据 ZIP。"""
    parser = argparse.ArgumentParser(description="第四问图表与可携带复算材料归档")
    parser.add_argument("full", type=Path)
    parser.add_argument("stress", type=Path)
    parser.add_argument("--bundle", action="store_true", help="在 runs/q4 中额外生成完整复算 ZIP")
    args = parser.parse_args()
    full, stress = args.full.resolve(), args.stress.resolve()
    q2, (full_audit, stress_audit) = _check_runs(full, stress)
    result = Path(tempfile.mkdtemp(prefix="归档预览_", dir=RUNS))
    config = _config(full / "config.yaml")
    config["q2_archive"] = os.path.relpath(q2, result)
    (result / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")
    for name in REPORT_FILES:
        shutil.copyfile(full / name, result / name)
    write_metric_view(full / "model_comparison.csv", result / "scenario_metrics.csv")
    write_metric_view(full / "representative_cases.csv", result / "representative_cases.csv")
    for name in ("paired_stress.csv", "stress_summary.json"):
        shutil.copyfile(stress / name, result / name)
    _copy_policy_tracks(full, result)
    baseline = load_archive(q2)
    inputs, tracks = result / "scenario_inputs.jsonl.gz", result / "policy_plans_and_tracks.jsonl.gz"
    write_vehicle_timelines(result / "policy_vehicle_timelines.csv.gz", baseline, inputs, tracks)
    write_representative_timeline(result, baseline, inputs, tracks, _load(full / "summary.json")["examples"]["largest_gain"])
    git_audit = verify(result, archive=True)
    if not git_audit["all_valid"]:
        raise ValueError(f"归档预览验收未通过：{git_audit['errors']}")
    for name, audit in (("archive_validation.json", full_audit), ("stress_validation.json", stress_audit),
                        ("git_archive_validation.json", git_audit)):
        (result / name).write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Q4 archive preview: {result}")
    if args.bundle:
        bundle = result.with_suffix(".zip")
        _package(full, stress, q2, result, bundle)
        print(f"Q4 evidence ZIP: {bundle} ({bundle.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
