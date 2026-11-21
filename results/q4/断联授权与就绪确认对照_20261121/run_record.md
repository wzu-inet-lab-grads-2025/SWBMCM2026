# 断联执行口径对照｜运行及复核记录

- **全量运行：** `runs/q4/20261121T030600Z_outage_pppo9zoe/`，墙钟 **443.08 s**。以 [Q4 主归档](../扰动重调度_四模型择优_20261120/) 为配对参照，读取同一 [Q2(2) 初始计划](../../q2/双站定位与并发调度_连续站位_20261120/) 的 `optimized` 路线与时间轴。原数值归档不变。
- **环境与代码：** Linux x86-64、Python 3.13.9，12 个工作进程，CP-SAT/SCIP 单线程、各 1.5 s 场景预算。Git 基点 `38bb3c7a899ab8b5d0bc7d66eb8f4724afa4129b`，运行包含当时未提交的断联实现。依赖版本、源码、初始计划及主表的 SHA-256 见[机器运行记录](run_record.json)，实际生效配置见[配置快照](config.yaml)。归档 PDF 用原汇总数据重新绘制，缩短轴标题并留出页边距，原始运行和数值未变。
- **核查范围：** [全量回放报告](validation_report.json) 不调用求解器，核对 18,396 套连续轨迹和 91,980 条逐车时间轴；[原 Git 归档复验](reference_git_validation.json) 单列。另据模型评分和逐告警表重汇总 [2,628 行配对与 40 组汇总](paired_validation.json)，核对未启动告警、共同初始计划和候选站哈希。`model_comparison.csv.gz` 解压后与全量报告中 `model_comparison.csv` 的哈希相同。连续时间与直线下界的[复核记录](outage_bounds.json)及下文命令独立保存。

## 复跑与独立验收

在仓库根目录运行：

```bash
q4_main=results/q4/扰动重调度_四模型择优_20261120
q4_outage=results/q4/断联授权与就绪确认对照_20261121
nohup env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MPLBACKEND=Agg python3 -B -m src.run q4 --profile full \
  --outage-comparison "$q4_main" --config "$q4_outage/config.yaml"
# 换成本次命令打印的运行目录，验证全部轨迹和计分。
run=runs/q4/20261121T030600Z_outage_pppo9zoe
nohup env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 -B -m checks.q4_validate_run "$run" \
  --report "$run/validation_report.json"
nohup env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 -B -m checks.q4_outage_bounds "$q4_outage"
nohup python3 -B -m checks.q4_validate_run "$q4_main" --archive
```

`--outage-comparison` 默认读取主归档的配置；指定对照快照时核对除相对路径字符串外各参数均相同，且指向同一 Q2(2) 归档。新运行只写 `runs/q4/`。限时求解与并列择优的详细日志、选中来源或用时可能随复跑机器变化。下界脚本可读对照归档（如命令所示）或相同结构的全量运行，不重新执行四个求解器。

## 查找逐场证据

1. [配对底表](paired_outage.csv)以 `scenario_id + variant` 为主键；`main_*` 是原推荐，`retain_*` 是本执行口径的仅追加基线。用 `k=N` 判全启，`unstarted_alerts` 查漏启告警；`t_last_s=60` 是未全启的计分约定。
2. [模型评分](model_comparison.csv.gz)及[逐告警记录](alert_results.csv.gz)另加 `model` 键，包含四种重调方法、两条基线和推荐结果。恢复时的位置、姿态、锁定状态和站对见[情景输入](scenario_inputs.jsonl.gz)。[汇总](outage_summary.csv)的空 `category` 或 `t_rec_s` 表示沿该维度合并。
3. 全量的 `vehicle_timelines.csv.gz`、`plans_and_tracks.jsonl.gz`、`scenario_manifest.csv`、`solver_log.jsonl.gz`、`alns_seed_results.csv.gz` 和 `validation.jsonl.gz` 留在原始运行目录，可用上面的命令重建，不用摘要替代轨迹验收。

AI 辅助及日期见[运行记录](run_record.json)。本目录作为队内复算材料，正式采用前须由参赛队员核验、取舍并按[组委会规定](https://www.nmmcm.org.cn/News/220.html)记录实际使用情况。
