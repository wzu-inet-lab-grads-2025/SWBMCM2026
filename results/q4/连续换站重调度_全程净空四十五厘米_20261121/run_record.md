# B 题第四问 · 45 cm 净空复跑记录

完整运行目录为 `runs/q4/20261121T101349Z_recourse_5ibx20cx`。基于 `23158125c5fdecb80e9882e32a135c79a765ec02` 工作树，Linux、Python 3.13.9，12 个进程。输入、初排和源码哈希见[机器记录](run_record.json)；归档 `config.yaml` 的初排路径改指同字节的 45 cm 版 Q2（2）输入，`q2_archive_inputs_sha256` 未变。AI 辅助：OpenCode / Magpie `group/gpt-6.2-sol`，版本发布日期未提供。

从仓库根目录执行。第一条复跑完整实验并打印运行目录；初排与本次实际使用的细化排程的四个输入文件逐字节一致。其余命令只读归档复核，报告写入 `runs/`。

```bash
q4_base=results/q4/扰动重调度_四模型择优_20261120
q2_safe=results/q2/等精度部署_全程净空四十五厘米_20261121
q4_safe=results/q4/连续换站重调度_全程净空四十五厘米_20261121
nohup env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MPLBACKEND=Agg python3 -B -m src.run q4 --profile full \
  --recourse "$q4_base" --config "$q4_safe/recourse_config.yaml" --initial-schedule "$q2_safe"
nohup env OPENBLAS_NUM_THREADS=1 python3 -B -m checks.q4_validate_run "$q4_safe" --archive \
  --report runs/q4/archive_validation_45cm_replay.json
nohup python3 -B -m checks.b_wall_clearance q4 "$q4_safe" --wall-clearance-m 0.45 \
  --output runs/q4/wall_clearance_45cm_archive_replay.csv.gz
cmp <(gzip -cd "$q4_safe/wall_clearance.csv.gz") <(gzip -cd runs/q4/wall_clearance_45cm_archive_replay.csv.gz)
```

把独立验收命令的目录换为求解器打印的目录并去掉 `--archive`，可额外重验原始逐事件表和日志。另存的逐场墙距底表已与归档复跑结果按解压后的字节核对一致。超过归档限额的原逐事件表留在 `runs/`；归档中保存用于复验的全部分相位轨迹。[完整运行验收](full_run_validation.json) · [归档验收](archive_validation.json)

- [场景清单](scenario_manifest.csv) · [恢复快照、已锁告警和候选站对](scenario_inputs.jsonl.gz)
- [逐场得分](model_comparison.csv.gz) · [五策略同场得分配对](paired_policies.csv) · [逐告警结果](alert_results.csv.gz)
- [断联口径汇总](protocol_summary.csv) · [扰动类型汇总](category_summary.csv) · [恢复时刻汇总](recovery_summary.csv)
