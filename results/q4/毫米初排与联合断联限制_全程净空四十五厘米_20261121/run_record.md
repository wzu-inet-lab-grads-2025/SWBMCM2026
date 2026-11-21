# B 题第四问｜毫米初排和断联工况复跑

原始目录为 `runs/q4/20261121T114636Z_recourse_fau6ugko`，Linux、Python 3.13.9，12 个进程，求解与导出合计约 **141 s**。基于 `23158125c5fdecb80e9882e32a135c79a765ec02` 的本地工作树。初排和源码哈希见机器记录。归档配置引用毫米 Q2，四个初排源文件与实际运行输入逐字节一致。初排身份还包括选择的方案 `practical`。AI 辅助使用 OpenCode 与 Magpie `group/gpt-6.2-sol`。[机器记录](run_record.json) · [初排与方案](../../q2/毫米部署与初排_全程净空四十五厘米_20261121/conclusions.md)

从仓库根目录运行。复跑命令创建新的 `runs/q4` 目录。计分、路线和实采可从归档直接独立验收。完整逐事件表与求解日志使用新运行目录验收，命令省略 `--archive`。

```bash
q4_ref=results/q4/扰动重调度_四模型择优_20261120
q2_mm=results/q2/毫米部署与初排_全程净空四十五厘米_20261121
q4_mm=results/q4/毫米初排与联合断联限制_全程净空四十五厘米_20261121
nohup env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MPLBACKEND=Agg python3 -B -m src.run q4 --profile full \
  --recourse "$q4_ref" --config "$q4_mm/recourse_config.yaml" --initial-schedule "$q2_mm"
nohup env OPENBLAS_NUM_THREADS=1 python3 -B -m checks.q4_validate_run "$q4_mm" --archive
nohup python3 -B -m checks.b_wall_clearance q4 "$q4_mm" --wall-clearance-m 0.45 \
  --output runs/q4/wall_clearance_mm_replay.csv.gz
cmp <(gzip -cd "$q4_mm/wall_clearance.csv.gz") <(gzip -cd runs/q4/wall_clearance_mm_replay.csv.gz)
```

完整运行验收回放 **142,325** 条车辆时间轴、**2,999,570** 条事件和 **113,475** 条告警记录，核对了恢复与同刻发车、开测的先后顺序。归档保存所有分相位计划，压缩后解压数据与完整运行相同。完整事件大表留在 `runs/`。如需从归档生成逐车事件，可调用 `src.q4.report.write_vehicle_timelines`，以本目录的归档快照和分相位轨迹作输入。[完整运行验收](full_run_validation.json) · [归档验收](archive_validation.json) · [归档分相位轨迹](plans_and_tracks.jsonl.gz)

逐告警表中，`observation_end_s` 是排程规定的占用结束。`joint_observation_end_s` 是两辆车同时观测的真实结束，`joint_observation_in_window_s` 取到 60 s 为止的双车重叠时长，`complete_5s_by_60` 表示评价窗内连续采满。逐场 `k` 只计成功开测，`k_full_observation` 统计采满告警。[逐告警表](alert_results.csv.gz) · [逐场分数](model_comparison.csv.gz) · [按规则汇总](protocol_summary.csv)
