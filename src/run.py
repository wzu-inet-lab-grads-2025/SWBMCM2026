"""数维杯 B 题统一运行入口，接入第一至第四问。
输入为题号、YAML 和运行档，输出到 runs/<题号> 的独立运行目录。
单独运行 nohup python3 -B -m src.run q4 --profile full。
模型实验可选择 q1 模型对照、q2 站位细化或 q4 断联对照，输入各自的归档。
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
    parser.add_argument("question", choices=("q1", "q2", "q3", "q4"), help="小问编号")
    parser.add_argument("--config", type=Path, help="题面配置文件，默认使用 configs/<题号>.yaml")
    parser.add_argument("--profile", choices=("debug", "full"), default="full", help="小规模调试或所选配置的全量实验")
    parser.add_argument("--output-root", type=Path, help="独立运行目录的父目录，默认使用 runs/<题号>")
    parser.add_argument("--compare-baseline", type=Path, help="运行 q1 替代模型，与指定的归档基线比较")
    parser.add_argument("--comparison-config", type=Path, help="替代模型实验设置 YAML")
    parser.add_argument("--refine-schedule", type=Path, help="细化指定 q2 归档的连续站位和并行车次")
    parser.add_argument("--outage-comparison", type=Path, help="在指定 q4 主归档的断联场景上复跑执行口径对照")
    parser.add_argument("--recourse", type=Path, help="在指定 q4 主归档的情景上重跑连续换站与调度")
    parser.add_argument("--initial-schedule", type=Path, help="连续重调使用的另一份已验收 q2 归档")
    parser.add_argument("--peer-comparison", type=Path, help="同一初始方案的队友逐场对照 CSV，仅用于评价")
    args = parser.parse_args(argv)
    config = (args.config or root / "configs" / f"{args.question}.yaml").resolve()
    output = (args.output_root or root / "runs" / args.question).resolve()
    # 外层启动器执行 Python 后，进程只保留 Python 参数。复跑命令另按仓库约定生成。
    command = shlex.join(sys.orig_argv if argv is None else [sys.executable, "-m", "src.run", *argv])

    if args.question != "q1" and (args.compare_baseline or args.comparison_config):
        parser.error("--compare-baseline 与 --comparison-config 仅用于第一问")
    if args.question != "q4" and args.outage_comparison:
        parser.error("--outage-comparison 仅用于第四问")
    if args.recourse and (args.question != "q4" or args.outage_comparison):
        parser.error("--recourse 仅用于第四问，并与 --outage-comparison 互斥")
    if (args.initial_schedule or args.peer_comparison) and not args.recourse:
        parser.error("--initial-schedule 与 --peer-comparison 需要 --recourse")
    if args.question != "q2" and args.refine_schedule:
        parser.error("--refine-schedule 仅用于第二问")

    if args.compare_baseline:
        from src.q1.model_comparison import run_model_comparison

        settings_path = args.comparison_config or root / "configs" / "q1_model_comparison.yaml"
        print(run_model_comparison(
            args.compare_baseline.resolve(), config, settings_path.resolve(), output, root, command,
        ))
        return 0
    if args.comparison_config:
        parser.error("--comparison-config 需要同时指定 --compare-baseline")
    if args.refine_schedule:
        from src.q2.refinement import run_refinement

        settings = config if args.config else root / "configs/q2_refinement.yaml"
        print(run_refinement(args.refine_schedule.resolve(), settings, output, root, command))
        return 0
    if args.outage_comparison:
        from src.q4.outage_comparison import run_outage_comparison

        reference = args.outage_comparison.resolve()
        print(run_outage_comparison(reference, config if args.config else reference / "config.yaml",
                                    output, args.profile, root, command))
        return 0
    if args.recourse:
        from src.q4.recourse_experiment import run_recourse

        settings = config if args.config else root / "configs/q4_recourse.yaml"
        initial = args.initial_schedule.resolve() if args.initial_schedule else None
        peer = args.peer_comparison.resolve() if args.peer_comparison else None
        print(run_recourse(args.recourse.resolve(), settings, output, args.profile, root, command, initial, peer))
        return 0

    # 按需导入，第一至第三问不要求加载第四问的 OR-Tools 后端。
    if args.question == "q1":
        from src.q1.experiment import run_q1 as run
    elif args.question == "q2":
        from src.q2.experiment import run_q2 as run
    elif args.question == "q3":
        from src.q3.experiment import run_q3 as run
    else:
        from src.q4.experiment import run_q4 as run

    print(run(config, output, args.profile, root, command))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
