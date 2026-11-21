"""B 题图集验收：从正式归档出图到临时目录，不求解或改写任何底表。
校验产物、来源摘要、旧目录保护，以及评分窗外的图形可保留但不能计分。
运行：nohup python3 -B -m pytest -q -p no:cacheprovider checks/test_b_archive_figures.py
"""

import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree

import pytest

from src.q4.archive_plots import score_start_list
from src.shared.b_figures.__main__ import main
from src.shared.b_figures.common import ARCHIVES, STYLE_NAME


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(("question", "style", "stems"), [
    ("q1", "paper-teal-vector", {"q1_observability"}),
    ("q2", STYLE_NAME, {"q22_routes", "q22_timeline"}),
    ("q3", "paper-blue-slate", {"q3_parameters_80m", "q3_error_sources"}),
    ("q4", STYLE_NAME, {"q4_policy", "q4_recovery", "q4_e7_factors", "q4_candidate_regions",
             "q4_timeline_e2", "q4_timeline_e7", "q4_timeline_e1"}),
])
def test_atlas_from_frozen_inputs(tmp_path, question, style, stems):
    """验收各问的公开出图入口、可读格式及逐图输入追溯。"""
    out = tmp_path / question
    assert main([question, "--output", str(out)]) == 0
    manifest = json.loads((out / "provenance.json").read_text())
    assert manifest["question"] == question
    assert manifest["visual_style"] == style
    assert manifest["source_archive"] == ARCHIVES[question].as_posix()
    assert set(manifest["figures"]) == stems
    for relpath, digest in manifest["generator_files"].items():
        assert hashlib.sha256((ROOT / relpath).read_bytes()).hexdigest() == digest
    formats = {"pdf": b"%PDF", "png": b"\x89PNG\r\n\x1a\n"}
    if question == "q1":
        formats["svg"] = b"<?xml"
        root = ElementTree.parse(out / "q1_observability.svg").getroot()
        namespace = "{http://www.w3.org/2000/svg}"
        assert root.tag == f"{namespace}svg"
        assert root.find(f".//{namespace}image") is None
        for view in ("field", "detail"):
            cells = root.find(f".//{namespace}g[@id='observable-cells-{view}']/{namespace}path")
            assert cells is not None and cells.attrib["d"].strip()
    for stem, entry in manifest["figures"].items():
        assert entry["inputs"]
        for relpath, digest in entry["inputs"].items():
            assert hashlib.sha256((ROOT / relpath).read_bytes()).hexdigest() == digest
        assert set(entry["outputs"]) == {f"{stem}.{suffix}" for suffix in formats}
        for suffix, magic in formats.items():
            image = out / f"{stem}.{suffix}"
            assert 5_000 < image.stat().st_size < 10_000_000
            content = image.read_bytes()
            assert content.startswith(magic)
            assert hashlib.sha256(content).hexdigest() == entry["outputs"][image.name]


def test_safe_output_paths(tmp_path):
    """禁止覆盖现存目录或直接向正式归档发布，归档另经人工审核。"""
    with pytest.raises(SystemExit, match="2"):
        main(["q1", "--output", str(tmp_path)])
    with pytest.raises(SystemExit, match="2"):
        main(["q1", "--output", str(ROOT / ARCHIVES["q1"] / "must_not_write")])


def test_q4_scoring_marks_only_strictly_before_deadline():
    """对恰在 60 s 开始的记录和缺失告警分别核对评分列表。"""
    row = {"N": "4", "k": "2", "t_last_s": "60.0", "inclusive_60": "False"}
    assert score_start_list({"0": 5.0, "1": 60.0, "2": 59.99}, row) == [5.0, None, 59.99, None]
    with pytest.raises(ValueError, match="计分"):
        score_start_list({"0": 5.0, "1": 60.0, "2": 59.99}, {**row, "k": "3"})
