# 第四问运行和验收记录

- **正式运行：** `runs/q4/20261120T142102Z_by9oaz91/`；墙钟约 467.46 s（不含最后出图），12 个工作进程，每个求解器单线程。原始 [run_record.json](run_record.json) 保留当时的工作树基点、配置和运行源码哈希，不回填后续验收与制图代码。
- **额外近共线/极限复算：** `runs/q4/20261120T143357Z_stress_4ahj513d/`；30 场交会角门槛、3 场 0.05 s 时钟、1 场最晚实例放开旧站。与基础样本分开。[压力复算摘要](stress_summary.json) · [压力回放验收](stress_validation.json)
- **全量复跑：** `runs/q4/20261120T164012Z_9fitljmt/` 的 1,947 组输入与正式运行一致。推荐、两条基线、CP-SAT、ALNS、双车增量插入的全部逐情景官方分数不变。限时 MILP 的启动数不变，18 个主情景及 2 个灵敏度变体的最晚开测时刻有差异；并列最优时的推荐来源也可随之变化。仍保留正式运行，不用复跑替换底表。压力入口也独立复跑并验收了 34 场，见本地 `runs/q4/20261120T162557Z_stress_o5yvvlwg/`。
- **环境：** Python 3.13.9、NumPy 2.3.5、SciPy 1.17.0、PyYAML 6.0.3、Matplotlib 3.10.8，OR-Tools 9.15.6755（CP-SAT 和 SCIP 10.0.0/SoPlex 8.0.0）。标准库用于读取压缩产物；测试需 pytest。主结果不依赖商用许可证或 GPU。
- **基准：** [只读 Q2(2) 归档](../../q2/双站定位与并发调度_连续站位_20261120/) `optimized` 组，独立复现最晚开测 10.352848885 s。保留 q2 文件原样。

## 仅用 Git 归档验收

在仓库根目录执行（不调用求解器）：

```bash
q4_archive=results/q4/扰动重调度_四模型择优_20261120
nohup python3 -B -m checks.q4_validate_run "$q4_archive" --archive
```

[Git 归档验收](git_archive_validation.json)绑定所读文件的 SHA-256：检查 1,947 组输入和 13,629 行分数，重算逐情景择优与主样本汇总；回放 1,895 个主/加密情景的推荐与两条基线（5,685 套轨迹、28,425 条逐车时间轴）。其他模型及灵敏度的轨迹、完整求解器日志和 ALNS 种子逐次输出不在 Git 内。这里核对评分和连续可行性，不重新证明整数模型最优。

## 从归档配置复跑并全量验收

```bash
q4_archive=results/q4/扰动重调度_四模型择优_20261120
nohup env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MPLBACKEND=Agg python3 -B -m src.run q4 --profile full \
  --config "$q4_archive/config.yaml"
# 把下面两个变量换成本机实际打印的目录。
q4_full=runs/q4/20261120T142102Z_by9oaz91
nohup python3 -B -m checks.q4_validate_run "$q4_full"
nohup env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 -B -m src.q4.stress "$q4_full"
q4_stress=runs/q4/20261120T143357Z_stress_4ahj513d
nohup python3 -B -m checks.q4_validate_run "$q4_stress"
```

`--profile debug` 先跑 32 个独立开发情景及 5 个灵敏度变体。正式配置下，[主运行全量验收](archive_validation.json)读取 13,629 套轨迹、68,145 条逐车时间轴、13,629 条求解日志及 19,470 条 ALNS 种子记录；[原压力运行验收](stress_validation.json)另外覆盖 34 场、238 套轨迹。回放重核路线、同步和锁定规则、故障截断、连续姿态与车距、评分边界及日志对应关系。原始运行的旧版事件表保留为历史；其中的零路程出发占位只在完整历史模式下兼容，归档发布表已从原相位重建。新压力运行另保存 `run_record.json`，含所引用主运行文件及执行源码的哈希。

## 如何查任一场景

1. 从[情景清单](scenario_manifest.csv)定位 `scenario_id` 和 `variant`，从[情景输入](scenario_inputs.jsonl.gz)读车位、姿态和候选站对。主统计限定 `sample=main, variant=main`，不要与加密或灵敏度行混用。
2. [逐情景策略表](scenario_metrics.csv) 用 `model` 查看 `recommended`、主基线 `original_append`、严格冻结 `strict_frozen` 或四个单模型。`source` 指示择优来源，`strategy` 指示实际调度策略。
3. `solve_time_seconds` 对 `recommended` 是**被选中的算法**的求解段用时，不是组合策略端到端耗时；`median_solver_s` 同理。`latest_tick_gap_s` 是导出方案的 `plan_latest_tick_s - latest_tick_lower_s`（两者均存在时）；`optimal_latest_tick_s` 是离散证书的取等值，取等方案可和连续计分选出的导出方案不同。
4. [逐告警分配](alert_results.csv.gz)提供所有策略的站车、交会角、RMSE 和失败原因。`observation_end_s` 是计划占用结束时刻，故障时看 `end_note` 和真实相位。[逐车事件](policy_vehicle_timelines.csv.gz)与[折线及相位](policy_plans_and_tracks.jsonl.gz)可按同一场景、版本及策略提取五车轴，同一时间戳用 `event_index` 排序。去掉零路程出发占位，合并未中断的转向及同一次故障的相邻段；原始分相位轨迹、启动数和连续评分不变。
5. 本地完整运行还保存 `vehicle_timelines.csv.gz`、`plans_and_tracks.jsonl.gz`、`solver_log.jsonl.gz`、`validation.jsonl.gz` 和 `alns_seed_results.csv.gz`。这些是全部模型和变体的证据，不以 Git 精简版替代。

## 本地复建预览

```bash
nohup env MPLBACKEND=Agg python3 -B -m tools.q4_archive "$q4_full" "$q4_stress"
```

先独立验收完整运行与压力配对，再用保存的轨迹重绘代表图、重建推荐和两基线的逐车事件，并验收精简预览。输出到新建的 `runs/q4/归档预览_*`，不改 `results/`、原始运行或 `submission/`。加 `--bundle` 才在 `runs/q4/` 创建带源码、数据和复算命令的队内 ZIP。

图、代码和汇总含 AI 辅助：OpenCode / Magpie `group/gpt-6.2-sol`，2026-11-20，版本发布日期未提供。需由参赛队员独立核验并记录采用内容及人工修改。[组委会规定](https://www.nmmcm.org.cn/News/220.html)
