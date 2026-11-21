# B 题第四问复跑记录 · 冻结排程

本机运行编号为 `20261121T080525Z_recourse_mzdbewm6`。基于提交 `23158125c5fdecb80e9882e32a135c79a765ec02` 的工作树运行，使用 Linux、Python 3.13.9 和 12 个进程。实际源码、依赖及输入的 SHA-256 见 [运行记录 JSON](run_record.json)。AI 辅助使用 OpenCode / Magpie `group/gpt-6.2-sol`，版本发布日期未提供。`tdd` 用于第二问入口、第四问同快照换站回归，两份 Q4 全量结果另由独立验收器复算。

主场景从 [11 月 20 日第四问归档](../扰动重调度_四模型择优_20261120/conclusions.md) 取得，使用其配置引用的旧 Q2 排程。队友输入原样取自 `explore/b-first-round` 提交 `30d54fa397c370742cd34e5cd79808ebdb59ae46` 的 `experiments/b/snapshots/yyl_crosscheck/q4.csv`。`local_on_peer_*` 是队友在己方冻结排程上的成绩。程序核对 1,751 个情景键、旧推荐分数与队友分数的严格 60 s 窗口，再生成配对表。[归档输入 CSV](peer_comparison_input.csv) · [队友对照来源](https://github.com/wzu-inet-lab-grads-2025/SWBMCM2026/blob/30d54fa397c370742cd34e5cd79808ebdb59ae46/docs/b-peer-crosscheck.md)

从仓库根目录运行如下命令，求解命令打印本次运行目录。

```bash
q4_reference=results/q4/扰动重调度_四模型择优_20261120
q4_archive=results/q4/连续换站重调度_冻结排程_20261121
nohup env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MPLBACKEND=Agg python3 -B -m src.run q4 --profile full \
  --recourse "$q4_reference" --config "$q4_archive/recourse_config.yaml" \
  --peer-comparison "$q4_archive/peer_comparison_input.csv"
nohup env OPENBLAS_NUM_THREADS=1 python3 -B -m checks.q4_validate_run "$q4_archive" --archive
```

第二条命令直接复算本归档的五列策略评分及每条连续相位。对求解命令输出的 `runs/q4/<运行编号>` 去掉 `--archive` 运行同一验收入口，可再核对逐车事件及求解日志。完整事件表和原始搜索日志随运行留在 `runs/`。[完整运行验收](full_run_validation.json) · [归档验收](archive_validation.json)

- [实际配置](config.yaml) · [搜索配置](recourse_config.yaml) · [场景清单](scenario_manifest.csv) · [恢复快照与候选站对](scenario_inputs.jsonl.gz)
- [五种策略的逐场评分](model_comparison.csv.gz) · [全相位车辆轨迹](plans_and_tracks.jsonl.gz) · [逐告警结果](alert_results.csv.gz)
- [主口径汇总](algorithm_summary.csv) · [三口径与基线汇总](protocol_summary.csv) · [按扰动类型汇总](category_summary.csv) · [按恢复时刻汇总](recovery_summary.csv)
- [同初始排程配对](paired_main.csv) · [换站对照与断联配对](paired_policies.csv) · [主结果](conclusions.md)
