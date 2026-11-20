"""数维杯 B 题统一运行入口，接入第一问和第二问。
输入为题号、YAML 和运行档，输出到 runs/q1 或 runs/q2 的独立运行目录。
单独运行 nohup python3 -B -m src.run q2 --profile full。
模型实验使用 --compare-baseline 指定归档的 q1 运行目录。
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol（版本发布日期未提供），2026-11-20。
"""

import argparse
from pathlib import Path
import shlex
import sys


def main(argv: list[str] | None = None) -> int:
    """解析运行参数，调度指定小问并输出本次产物的绝对路径。"""
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="数维杯 B 题运行入口")
    parser.add_argument("question", choices=("q1", "q2"), help="小问编号")
    parser.add_argument("--config", type=Path, help="题面配置文件，默认使用 configs/<题号>.yaml")
    parser.add_argument("--profile", choices=("debug", "full"), default="full", help="粗网格调试或全量收敛对照")
    parser.add_argument("--output-root", type=Path, help="独立运行目录的父目录，默认使用 runs/<题号>")
    parser.add_argument("--compare-baseline", type=Path, help="运行 q1 替代模型，与指定的归档基线比较")
    parser.add_argument("--comparison-config", type=Path, help="替代模型实验设置 YAML")
    args = parser.parse_args(argv)
    config = (args.config or root / "configs" / f"{args.question}.yaml").resolve()
    output = (args.output_root or root / "runs" / args.question).resolve()
    # 外层启动器执行 Python 后，进程只保留 Python 参数。复跑命令另按仓库约定生成。
    command = shlex.join(sys.orig_argv if argv is None else [sys.executable, "-m", "src.run", *argv])

    if args.question == "q2":
        if args.compare_baseline or args.comparison_config:
            parser.error("--compare-baseline 与 --comparison-config 仅用于第一问")
        from src.q2.experiment import run_q2

        print(run_q2(config, output, args.profile, root, command))
        return 0

    if args.compare_baseline:
        from src.q1.model_comparison import run_model_comparison

        settings_path = args.comparison_config or root / "configs" / "q1_model_comparison.yaml"
        print(run_model_comparison(
            args.compare_baseline.resolve(), config, settings_path.resolve(), output, root, command,
        ))
        return 0
    if args.comparison_config:
        parser.error("--comparison-config 需要同时指定 --compare-baseline")

    from src.q1.experiment import run_q1

    print(run_q1(config, output, args.profile, root, command))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
