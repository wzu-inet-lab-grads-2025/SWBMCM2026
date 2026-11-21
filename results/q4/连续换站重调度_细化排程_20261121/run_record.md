# B 题第四问复跑记录 · 细化排程

本机运行编号为 `20261121T080525Z_recourse_uh1p5zba`。基于提交 `23158125c5fdecb80e9882e32a135c79a765ec02` 的工作树运行，使用 Linux、Python 3.13.9 和 12 个进程。各源码与初始排程输入的 SHA-256 见 [运行记录 JSON](run_record.json)。AI 辅助使用 OpenCode / Magpie `group/gpt-6.2-sol`，版本发布日期未提供。方法和口径同 [冻结排程对照](../连续换站重调度_冻结排程_20261121/run_record.md)。

从仓库根目录运行如下命令，求解命令打印本次运行目录。

```bash
q4_reference=results/q4/扰动重调度_四模型择优_20261120
q2_refined=results/q2/等精度快速部署与并发排程_20261121
q4_archive=results/q4/连续换站重调度_细化排程_20261121
nohup env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MPLBACKEND=Agg python3 -B -m src.run q4 --profile full \
  --recourse "$q4_reference" --config "$q4_archive/recourse_config.yaml" --initial-schedule "$q2_refined"
nohup env OPENBLAS_NUM_THREADS=1 python3 -B -m checks.q4_validate_run "$q4_archive" --archive
```

直接验收归档会重算五种策略的评分和全部连续相位。对求解命令输出的 `runs/q4/<运行编号>` 去掉 `--archive` 运行同一验收入口，可同时核对完整事件和搜索日志。两份归档的 `paired_main.csv` 可按 `scenario_id` 对接，再以 `k`、`t_last_s` 核对切换初始排程的逐场差异。[完整运行验收](full_run_validation.json) · [归档验收](archive_validation.json)

- [实际配置](config.yaml) · [搜索配置](recourse_config.yaml) · [场景清单](scenario_manifest.csv) · [恢复快照与候选站对](scenario_inputs.jsonl.gz)
- [逐场评分](model_comparison.csv.gz) · [全相位车辆轨迹](plans_and_tracks.jsonl.gz) · [逐告警结果](alert_results.csv.gz)
- [主口径汇总](algorithm_summary.csv) · [三口径与基线汇总](protocol_summary.csv) · [按扰动类型汇总](category_summary.csv) · [按恢复时刻汇总](recovery_summary.csv)
- [与上一版同场结果](paired_main.csv) · [换站与断联配对](paired_policies.csv) · [主结果](conclusions.md)
