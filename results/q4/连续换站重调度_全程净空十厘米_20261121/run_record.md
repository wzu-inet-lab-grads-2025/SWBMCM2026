# B 题第四问复跑记录 · 全程 10 厘米车体净空

运行目录为 `runs/q4/20261121T082851Z_recourse_m6q1_38w`。基于 `23158125c5fdecb80e9882e32a135c79a765ec02` 的工作树，Linux、Python 3.13.9，12 个进程。已归档代码、配置、初始排程输入的 SHA-256。AI 辅助使用 OpenCode / Magpie `group/gpt-6.2-sol`，版本发布日期未提供。`tdd` 覆盖标称方案北场界相切的 `E1_A345_R50`，逐车逐相位检查留量搜索的所有策略。[机器记录](run_record.json) · [配置](recourse_config.yaml)

从仓库根目录执行。第一条命令打印本次求解运行目录。后两条读取归档，分别复算时序计分以及两版净空与逐场得分差。

```bash
q4_reference=results/q4/扰动重调度_四模型择优_20261120
q2_refined=results/q2/等精度快速部署与并发排程_20261121
q4_nominal=results/q4/连续换站重调度_细化排程_20261121
q4_clearance=results/q4/连续换站重调度_全程净空十厘米_20261121
nohup env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MPLBACKEND=Agg python3 -B -m src.run q4 --profile full \
  --recourse "$q4_reference" --config "$q4_clearance/recourse_config.yaml" --initial-schedule "$q2_refined"
nohup env OPENBLAS_NUM_THREADS=1 python3 -B -m checks.q4_validate_run "$q4_clearance" --archive
nohup python3 -B -m checks.b_wall_clearance q4 "$q4_clearance" --wall-clearance-m 0.10 \
  --reference "$q4_nominal" --output runs/q4/wall_clearance_replay.csv.gz \
  --paired-output runs/q4/paired_wall_clearance_replay.csv
cmp "$q4_clearance/paired_wall_clearance.csv" runs/q4/paired_wall_clearance_replay.csv
cmp <(gzip -cd "$q4_clearance/wall_clearance.csv.gz") <(gzip -cd runs/q4/wall_clearance_replay.csv.gz)
```

把第二条的目录替换为求解输出并省略 `--archive` 可额外复核原始事件和搜索日志。[完整运行验收](full_run_validation.json) · [归档验收](archive_validation.json)

- [同一初始排程的逐场净空对比](paired_wall_clearance.csv) · [各策略逐场净空](wall_clearance.csv.gz) · [五策略逐场得分](model_comparison.csv.gz)
- [冻结排程逐场墙距复核](frozen_wall_clearance.csv.gz)。用原[冻结排程上的连续重调归档](../连续换站重调度_冻结排程_20261121/conclusions.md)运行 `checks.b_wall_clearance q4`，省去 `--reference`、`--paired-output` 和 `--wall-clearance-m`。
- [场景清单](scenario_manifest.csv) · [恢复快照与候选站对](scenario_inputs.jsonl.gz) · [全相位车辆轨迹](plans_and_tracks.jsonl.gz)
- [主样本汇总](algorithm_summary.csv) · [断联口径汇总](protocol_summary.csv) · [按扰动类型汇总](category_summary.csv) · [按恢复时刻汇总](recovery_summary.csv) · [结果结论](conclusions.md)
