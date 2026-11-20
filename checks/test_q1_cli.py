"""检验 q1 统一入口可运行、生成完整产物并保留既有运行。
使用默认官方配置与调试网格，结果写入 pytest 临时目录。
单独运行 nohup python3 -B -m pytest -p no:cacheprovider checks/test_q1_cli.py。
"""

import json
import math
from pathlib import Path
import shlex
import subprocess
import sys

import numpy as np
import pytest
import yaml

from src.q1.config import load_config
from src.q1.geometry import independently_check_station


ROOT = Path(__file__).resolve().parents[1]


def test_q1_entry_exports_reproducible_run(tmp_path):
    """真实入口输出可复算 JSON、单元掩码、五张图和独立站点检查。"""
    old = tmp_path / "older_run"
    old.mkdir()
    artifact = old / "keep.txt"
    artifact.write_text("keep", encoding="utf-8")
    invocation = [sys.executable, "-B", "-m", "src.run", "q1", "--profile", "debug", "--output-root", str(tmp_path)]
    completed = subprocess.run(["nohup", *invocation], cwd=ROOT, check=True, capture_output=True, text=True)
    run = Path(completed.stdout.strip().splitlines()[-1])
    assert run.parent == tmp_path and run != old
    assert artifact.read_text(encoding="utf-8") == "keep"
    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    assert summary["question"] == "q1"
    assert shlex.split(summary["run"]["command"]) == invocation
    assert summary["primary_optimum"]["witness_valid"]
    assert summary["tie_break"]["optimality_gap_m"] <= 1e-8
    assert summary["validation"]["independent_geometry_passed"]
    npz = next(run.glob("region_*.npz"))
    with np.load(npz) as masks:
        expected = masks["vehicle_mask"] & masks["gimbal_mask"] & masks["los_clear_mask"]
        np.testing.assert_array_equal(masks["observable_mask"], expected)
        assert summary["region"]["resolution_studies"][0]["feasible_cells"] == int(expected.sum())
    for name in ("field_layout", "constraint_masks", "observable_region", "optimal_arcs", "clearance_profile"):
        assert (run / f"{name}.png").stat().st_size > 1000
    for name in ("config.yaml", "run.log", "summary.md", "region_counts.csv", "station_table.csv", "optimal_arcs.csv"):
        assert (run / name).is_file()
    station_rows = (run / "station_table.csv").read_text(encoding="utf-8").splitlines()
    assert station_rows[0].startswith("role,angle_deg,x_m,y_m")
    assert len(station_rows) == 1 + len(summary["optimal_circle"]["representative_stations"])

    circle = summary["optimal_circle"]
    assert circle["is_continuum"]
    arcs = circle["feasible_intervals_deg"]
    assert [(arc["left"], arc["right"]) for arc in arcs] == [
        pytest.approx((0.0, 160.7478733283), abs=1e-8, rel=0),
        pytest.approx((236.8960468538, 360.0), abs=1e-8, rel=0),
    ]
    assert [arc["closed"] for arc in arcs] == [[True, True], [True, False]]

    cfg = load_config(run / "config.yaml")
    examples = {sample["role"]: sample for sample in circle["representative_stations"]}
    assert {"east_witness", "north", "north_tangent", "east_tangent", "southern_local_peak", "selected"} <= examples.keys()
    assert len({tuple(row["station_xy_m"]) for row in examples.values()}) == len(examples)
    for row in examples.values():
        xy = row["station_xy_m"]
        independent = independently_check_station(*xy, cfg)
        assert independent["vehicle_ok"] and independent["gimbal_ok"] and independent["los_clear"]
        assert independent["horizontal_distance_m"] == pytest.approx(summary["primary_optimum"]["r_opt_m"], abs=1e-12, rel=0)
        assert row["quality_per_m"] == pytest.approx(summary["primary_optimum"]["q_opt_per_m"], abs=1e-12, rel=0)
    selected = examples["selected"]
    assert selected["station_xy_m"] == summary["tie_break"]["selected_station_xy"]
    assert selected["minimum_clearance_m"] > max(
        row["minimum_clearance_m"] for role, row in examples.items() if role != "selected"
    )
    markdown = (run / "summary.md").read_text(encoding="utf-8")
    assert markdown.index("完整并列最优解集") < markdown.index("并列解选择规则") < markdown.index("最终推荐站位")
    assert "代表站点" in markdown and "最大化最小车辆净空" in markdown


def test_full_run_keeps_masks_for_distinct_accepted_steps(tmp_path):
    """两档可整除且很接近的网格步长，分别保留各自的掩码文件。"""
    cfg = yaml.safe_load((ROOT / "configs" / "q1.yaml").read_text(encoding="utf-8"))
    steps = [1.0, math.nextafter(1.0, math.inf)]
    cfg["grid"] = {"debug_m": steps[0], "plot_m": steps[0], "study_m": steps}
    snapshot = tmp_path / "q1.yaml"
    snapshot.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    completed = subprocess.run(
        ["nohup", sys.executable, "-B", "-m", "src.run", "q1", "--config", str(snapshot), "--output-root", str(tmp_path)],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    run = Path(completed.stdout.strip().splitlines()[-1])
    rows = json.loads((run / "summary.json").read_text(encoding="utf-8"))["region"]["resolution_studies"]
    assert len({row["mask_file"] for row in rows}) == 2
    for row, step in zip(rows, steps):
        with np.load(run / row["mask_file"]) as masks:
            assert float(masks["resolution"]) == step
