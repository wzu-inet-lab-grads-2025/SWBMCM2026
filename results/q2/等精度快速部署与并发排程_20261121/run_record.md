# B 题第二问复跑记录 · 2026-11-21

本机运行编号为 `20261121T080016Z_refinement_0plkk632`。Linux、Python 3.13.9。基于提交 `23158125c5fdecb80e9882e32a135c79a765ec02` 的工作树运行，实际源码和输入的 SHA-256 见 [运行记录](run_record.json)。AI 辅助使用 OpenCode / Magpie `group/gpt-6.2-sol`，版本发布日期未提供。队员据原题独立定稿。

从仓库根目录运行以下命令。第一条命令打印新建的 `runs/q2/` 路径，可将该路径传给第二条独立验收命令。[细化设置](refinement_config.yaml) · [初版输入](../双站定位与并发调度_连续站位_20261120/run_record.md)

```bash
q2_reference=results/q2/双站定位与并发调度_连续站位_20261120
q2_archive=results/q2/等精度快速部署与并发排程_20261121
nohup env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MPLBACKEND=Agg python3 -B -m src.run q2 \
  --refine-schedule "$q2_reference" --config "$q2_archive/refinement_config.yaml"
nohup python3 -B -m checks.q2_validate \
  --q21 "$q2_archive/q21_result.json" \
  --q22-routes "$q2_archive/q22_routes.json" --q22-timeline "$q2_archive/q22_timeline.csv" \
  --require-vehicle-separation --compare greedy optimized
nohup env OPENBLAS_NUM_THREADS=1 python3 -B -m checks.q2_continuous_bound \
  --trial-s 10.352100 --t1-partner-s 1.53203 --output runs/q2/continuous_bound_replay.json
cmp "$q2_archive/continuous_bound.json" runs/q2/continuous_bound_replay.json
```

独立验收重算楼体距离、三维视线、一阶交会误差、同步观察、复位及运动和停车期间的连续车距。`q22_routes.json` 与 `q22_timeline.csv` 保存 `optimized`、`practical`、`greedy` 三套轨迹。第四问使用 `optimized` 构建初始排程。时间与距离在底表中保留全精度。[验收记录](independent_validation.json) · [主结果](conclusions.md)
