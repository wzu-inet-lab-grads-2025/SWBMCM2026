# B 题第二问｜毫米部署复跑

原始目录为 `runs/q2/20261121T114210Z_refinement_c8rl0mxu`。计算基于 `23158125c5fdecb80e9882e32a135c79a765ec02` 的本地工作树，机器记录保存环境、输入和代码 SHA-256。几何图采用实际毫米部署，排程图采用毫米方案与六序就近基线。AI 辅助使用 OpenCode 与 Magpie `group/gpt-6.2-sol`。[机器记录](run_record.json) · [配置](refinement_config.yaml)

从仓库根目录运行。求解命令创建新的 `runs/q2` 目录。可将后两条命令中的 `q2_mm` 换成求解输出路径，复核新产物。

```bash
q2_ref=results/q2/双站定位与并发调度_连续站位_20261120
q2_mm=results/q2/毫米部署与初排_全程净空四十五厘米_20261121
nohup env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MPLBACKEND=Agg python3 -B -m src.run q2 \
  --refine-schedule "$q2_ref" --config "$q2_mm/refinement_config.yaml"
nohup env OPENBLAS_NUM_THREADS=1 python3 -B -m checks.q2_validate \
  --q21 "$q2_mm/q21_practical.json" --q22-routes "$q2_mm/q22_routes.json" \
  --q22-timeline "$q2_mm/q22_timeline.csv" --practical-decimals 3 --require-vehicle-separation \
  --wall-clearance-m 0.45 --execution-error-bound-m 0.40
nohup python3 -B -m checks.b_wall_clearance q2 "$q2_mm" --wall-clearance-m 0.45 \
  --output runs/q2/wall_clearance_mm_replay.csv
```

`q21_result.json` 给出全精度理论值及等精度路线，`q21_practical.json` 给出实际下发路点。第四问需读取第二问的 `config.yaml`、`scene_config.yaml`、`q22_routes.json` 和 `q22_timeline.csv`，并显式选择 `practical`。[结论](conclusions.md) · [逐方案时序](q22_timeline.csv) · [前缀及其哈希](../../q4/毫米初排与联合断联限制_全程净空四十五厘米_20261121/run_record.md)
