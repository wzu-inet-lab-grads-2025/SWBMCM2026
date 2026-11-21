"""从 B 题正式归档读数，输出论文图的 PDF/PNG（Q1 另有 SVG）和来源清单。
默认在 runs/<题号>/ 新建目录，不在原结果中写文件。
运行：nohup python3 -B -m src.shared.b_figures q1；可用 --output 指定新目录。
"""

import argparse
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
import shlex
import subprocess
import tempfile

import matplotlib as mpl

from .common import ARCHIVES, ROOT, STYLE, STYLE_NAME, Atlas


FIELD_READER = "src/q1/config.py"
MASK_HELPERS = (FIELD_READER, "src/q1/geometry.py", "src/q1/region.py")
SOURCE_HELPERS = {"q1": MASK_HELPERS, "q2": (FIELD_READER, "src/q2/scene.py"),
                  "q4": (*MASK_HELPERS, "src/q4/physics.py")}


def main(argv: list[str] | None = None) -> int:
    """仅调分问出图入口；现有目录和 results/ 下的路径拒绝写入。"""
    parser = argparse.ArgumentParser(description="从正式结果归档重绘 B 题图表（不调用求解器）")
    parser.add_argument("question", choices=ARCHIVES)
    parser.add_argument("--output", type=Path, help="必须不存在；默认在 runs/<题号>/ 下新建目录")
    args = parser.parse_args(argv)
    question = args.question
    dest = (args.output or ROOT / "runs" / question).resolve()
    if dest.is_relative_to((ROOT / "results").resolve()):
        parser.error("请先生成到 runs/ 或其他新目录；不能直接写 results/")
    if args.output is not None:
        try:
            dest.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            parser.error(str(exc))
        out = dest
    else:
        dest.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_")
        out = Path(tempfile.mkdtemp(prefix=f"figures_{stamp}", dir=dest))

    with mpl.rc_context(STYLE):
        module = import_module(f"src.{question}.archive_plots")
        atlas = Atlas(ROOT / ARCHIVES[question], out)
        module.draw(atlas)

    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
        head = proc.stdout.strip() if proc.returncode == 0 else None
    except FileNotFoundError:
        head = None  # 无 Git 的解包副本仍有脚本及底表哈希。
    command = shlex.join(["nohup", "python3", "-B", "-m", "src.shared.b_figures", question])
    helpers = (ROOT / name for name in SOURCE_HELPERS.get(question, ()))
    atlas.write_provenance(question, command, head, (Path(__file__), Path(module.__file__), *helpers),
                           visual_style=getattr(module, "VISUAL_STYLE", STYLE_NAME))
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
