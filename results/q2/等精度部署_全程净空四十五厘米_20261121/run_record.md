# B 题第二问 · 45 cm 净空复跑记录

本地求解目录为 `runs/q2/20261121T101421Z_refinement_guk_s17r`。基于 `23158125c5fdecb80e9882e32a135c79a765ec02` 工作树，Linux、Python 3.13.9。输入、源码和绘图底表的 SHA-256 见[机器记录](run_record.json)。AI 辅助：OpenCode / Magpie `group/gpt-6.2-sol`，版本发布日期未提供。

从仓库根目录执行。第一条输出到新建的 `runs/q2/` 目录；后几条直接复核归档，不调用求解器。

```bash
q2_base=results/q2/双站定位与并发调度_连续站位_20261120
q2_safe=results/q2/等精度部署_全程净空四十五厘米_20261121
nohup env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MPLBACKEND=Agg python3 -B -m src.run q2 \
  --refine-schedule "$q2_base" --config "$q2_safe/refinement_config.yaml"
nohup python3 -B -m checks.q2_validate --q21 "$q2_safe/q21_result.json" \
  --q22-routes "$q2_safe/q22_routes.json" --q22-timeline "$q2_safe/q22_timeline.csv" \
  --require-vehicle-separation --wall-clearance-m 0.45 --execution-error-bound-m 0.40 \
  --compare greedy optimized --output runs/q2/independent_validation_45cm.json
nohup python3 -B -m checks.b_wall_clearance q2 "$q2_safe" --wall-clearance-m 0.45 \
  --output runs/q2/wall_clearance_45cm_archive_replay.csv
cmp "$q2_safe/wall_clearance.csv" runs/q2/wall_clearance_45cm_archive_replay.csv
```

独立入口重算几何、三维视线、仰角、双站定位误差、时序和连续车间距；墙距工具另输出逐车次和原地待命的见证，复跑与归档 CSV 逐字节一致。[结果](conclusions.md) · [路线图](q22_routes.pdf) · [站位图](q21_stations.pdf)
