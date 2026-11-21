"""B 题第二问：核对正式入口的产物能交给独立验收器回放。
输入 q2 正式题设和 5 m 调试档，输出使用 pytest 隔离目录。
运行：nohup python3 -B -m pytest -q -p no:cacheprovider checks/test_q2_cli.py
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol（版本发布日期未提供），2026-11-20。
"""

import json
from pathlib import Path

import pytest

from checks.q2_validate import main as validate
from src.q4.candidates import initial_schedule_hash
from src.q4.physics import load_archive
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


def test_q2_refinement_preserves_t2_precision_with_wall_clearance(tmp_path):
    """用题面相同的 1.5 m 车径和 30 s 时限，切界部署改为全程留净空。"""
    root = Path(__file__).resolve().parents[1]
    reference = root / "results/q2/双站定位与并发调度_连续站位_20261120"
    settings = tmp_path / "refinement.yaml"
    settings.write_text((root / "configs/q2_refinement.yaml").read_text() + "wall_clearance_m: 0.10\n",
                        encoding="utf-8")
    output = tmp_path / "runs"
    assert run(["q2", "--refine-schedule", str(reference), "--config", str(settings),
                "--output-root", str(output)]) == 0
    directory, = output.iterdir()
    report_path = tmp_path / "independent.json"
    assert validate(["--q21", str(directory / "q21_result.json"),
                     "--q22-routes", str(directory / "q22_routes.json"),
                     "--q22-timeline", str(directory / "q22_timeline.csv"),
                     "--require-vehicle-separation", "--wall-clearance-m", "0.10",
                     "--output", str(report_path)]) == 0
    report = json.loads(report_path.read_text())
    assert report["q21"]["rmse_m"] == pytest.approx(0.143623358, abs=1e-9)
    assert max(r["travel_s"] for r in report["q21"]["routes"]) < 13.2


def test_q2_refinement_exports_certified_millimetre_routes(tmp_path):
    """不能直接舍入等精度站；留量版的毫米路点与连续墙距、仰角须重新验收。"""
    root = Path(__file__).resolve().parents[1]
    reference = root / "results/q2/双站定位与并发调度_连续站位_20261120"
    settings = root / "configs/q2_deployment.yaml"
    output = tmp_path / "runs"
    assert run(["q2", "--refine-schedule", str(reference), "--config", str(settings),
                "--output-root", str(output)]) == 0
    directory, = output.iterdir()
    deploy = directory / "q21_practical.json"
    assert validate(["--q21", str(deploy), "--q22-routes", str(directory / "q22_routes.json"),
                     "--q22-timeline", str(directory / "q22_timeline.csv"),
                     "--practical-decimals", "3", "--require-vehicle-separation", "--wall-clearance-m", "0.45",
                     "--output", str(tmp_path / "audit.json")]) == 0
    audited = json.loads((tmp_path / "audit.json").read_text())
    for leg in [*audited["q21"]["routes"], *audited["q22"]["practical"]["legs"]]:
        assert all(c == round(c, 3) for point in leg["waypoints"] for c in point)
    assert max(r["pitch_deg"] for r in audited["q21"]["routes"]) < 79.84
    assert 0.143623358 < audited["q21"]["rmse_m"] < 0.147
    assert audited["q22"]["practical"]["latest_start_s"] < 10.392
    optimized, practical = (load_archive(directory, wall_clearance_m=0.45, plan=name,
                                         waypoint_decimals=3 if name == "practical" else None)
                            for name in ("optimized", "practical"))
    assert practical.hashes == optimized.hashes
    assert initial_schedule_hash(practical) != initial_schedule_hash(optimized)
    assert practical.pairs[1][1] == (88.064, 70.901)
    assert max(practical.starts) == pytest.approx(audited["q22"]["practical"]["latest_start_s"])

    rounded = json.loads((directory / "q21_result.json").read_text())
    for leg in rounded["routes"]:
        leg["waypoints"][-1] = [round(c, 3) for c in leg["waypoints"][-1]]
    bad = tmp_path / "rounded_q21.json"
    bad.write_text(json.dumps(rounded), encoding="utf-8")
    with pytest.raises(SystemExit) as failure:
        validate(["--q21", str(bad), "--wall-clearance-m", "0.45"])
    assert failure.value.code == 1


def test_q2_refinement_keeps_precision_and_replays_the_nearest_baseline(tmp_path):
    """连续细化两小问后，正式入口仍可独立核对精度最优解、就近法和留量解。"""
    reference = Path(__file__).resolve().parents[1] / "results/q2/双站定位与并发调度_连续站位_20261120"
    assert run(["q2", "--refine-schedule", str(reference), "--output-root", str(tmp_path)]) == 0
    directory, = tmp_path.iterdir()
    report_path = directory / "independent.json"
    assert validate(["--q21", str(directory / "q21_result.json"),
                     "--q22-routes", str(directory / "q22_routes.json"),
                     "--q22-timeline", str(directory / "q22_timeline.csv"),
                     "--require-vehicle-separation", "--compare", "greedy", "optimized",
                     "--output", str(report_path)]) == 0
    report = json.loads(report_path.read_text())
    assert report["q21"]["rmse_m"] == pytest.approx(0.14362335792321346, abs=1e-12)
    assert max(leg["travel_s"] for leg in report["q21"]["routes"]) < 13.073
    assert report["q22"]["optimized"]["latest_start_s"] < 10.352119
    assert report["q22"]["practical"]["latest_start_s"] < 10.392
    assert report["comparison"]["latest_start_saved_s"] > 0.6
