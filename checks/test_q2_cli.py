"""B 题第二问：核对正式入口的产物能交给独立验收器回放。
输入 q2 正式题设和 5 m 调试档，输出使用 pytest 隔离目录。
运行：nohup python3 -B -m pytest -q -p no:cacheprovider checks/test_q2_cli.py
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol（版本发布日期未提供），2026-11-20。
"""

import json

import pytest

from checks.q2_validate import main as validate
from src.run import main as run


def test_q2_cli_produces_replayable_results_for_both_subquestions(tmp_path):
    """闭仰角取等、同口径基线与六车次时间轴在真实写盘后仍可复核。"""
    assert run(["q2", "--profile", "debug", "--output-root", str(tmp_path)]) == 0
    directory, = tmp_path.iterdir()
    proof = json.loads((directory / "q22_comparison.json").read_text())
    assert len(proof["candidate_refinement"][0]["vehicle_search"]) == 6
    assert proof["euclidean_relaxation"]["gap_s"] == pytest.approx(0, abs=1e-9)
    assert validate(["--q21", str(directory / "q21_result.json"),
                     "--q22-routes", str(directory / "q22_routes.json"),
                     "--q22-timeline", str(directory / "q22_timeline.csv"),
                     "--require-vehicle-separation", "--compare", "greedy", "optimized",
                     "--output", str(directory / "validation.json")]) == 0
    report = json.loads((directory / "validation.json").read_text())
    assert report["q21"]["rmse_m"] == pytest.approx(0.143623358, abs=1e-9)
    assert all(route["pitch_deg"] == 80 for route in report["q21"]["routes"])
    assert report["q22"]["optimized"]["latest_start_s"] == pytest.approx(10.829585935957326, abs=1e-9)
    assert report["comparison"]["latest_start_saved_s"] > 0
