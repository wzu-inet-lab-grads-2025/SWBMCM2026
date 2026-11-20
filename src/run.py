"""数维杯 B 题统一运行入口，目前接入第一问。
输入为小问、YAML 和运行档，输出到 runs/q1 的独立运行目录。
单独运行 nohup python3 -B -m src.run q1 --profile full。
模型实验使用 --compare-baseline 指定归档的 q1 运行目录。
"""

import argparse
from pathlib import Path
import shlex
import sys


def main(argv: list[str] | None = None) -> int:
    """解析运行参数，调度第一问并输出本次产物的绝对路径。"""
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="数维杯 B 题运行入口")
    parser.add_argument("question", choices=("q1",), help="小问编号")
    parser.add_argument("--config", type=Path, default=root / "configs" / "q1.yaml", help="题面配置文件")
    parser.add_argument("--profile", choices=("debug", "full"), default="full", help="粗网格调试或全量收敛对照")
    parser.add_argument("--output-root", type=Path, default=root / "runs" / "q1", help="独立运行目录的父目录")
    parser.add_argument("--compare-baseline", type=Path, help="运行 q1 替代模型，与指定的归档基线比较")
    parser.add_argument("--comparison-config", type=Path, help="替代模型实验设置 YAML")
    args = parser.parse_args(argv)
    # 外层启动器执行 Python 后，进程只保留 Python 参数。复跑命令另按仓库约定生成。
    command = shlex.join(sys.orig_argv if argv is None else [sys.executable, "-m", "src.run", *argv])

    if args.compare_baseline:
        from src.q1.model_comparison import run_model_comparison

        settings_path = args.comparison_config or root / "configs" / "q1_model_comparison.yaml"
        print(run_model_comparison(
            args.compare_baseline.resolve(), args.config.resolve(), settings_path.resolve(),
            args.output_root.resolve(), root, command,
        ))
        return 0
    if args.comparison_config:
        parser.error("--comparison-config 需要同时指定 --compare-baseline")

    from src.q1.experiment import run_q1

    print(run_q1(args.config.resolve(), args.output_root.resolve(), args.profile, root, command))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
