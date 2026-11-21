# B 题第二问复跑记录 · 全程 10 厘米车体净空

运行目录为 `runs/q2/20261121T082853Z_refinement_z5ovef2f`，Linux、Python 3.13.9，基于 `23158125c5fdecb80e9882e32a135c79a765ec02` 的工作树。实际源码、绘图输入和运行输入的 SHA-256 见 [机器记录](run_record.json)。AI 辅助使用 OpenCode / Magpie `group/gpt-6.2-sol`，版本发布日期未提供。`tdd` 覆盖整段距楼、停车距场界及第二问入口回归。

从仓库根目录执行。求解打印新建运行目录，后两条直接复核归档。

```bash
q2_reference=results/q2/双站定位与并发调度_连续站位_20261120
q2_clearance=results/q2/等精度部署_全程净空十厘米_20261121
nohup env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MPLBACKEND=Agg python3 -B -m src.run q2 \
  --refine-schedule "$q2_reference" --config "$q2_clearance/refinement_config.yaml"
nohup python3 -B -m checks.q2_validate --q21 "$q2_clearance/q21_result.json" \
  --q22-routes "$q2_clearance/q22_routes.json" --q22-timeline "$q2_clearance/q22_timeline.csv" \
  --require-vehicle-separation --wall-clearance-m 0.10 --compare greedy optimized
nohup python3 -B -m checks.b_wall_clearance q2 "$q2_clearance" --wall-clearance-m 0.10 \
  --output runs/q2/wall_clearance_replay.csv
cmp "$q2_clearance/wall_clearance.csv" runs/q2/wall_clearance_replay.csv
```

第二条复算几何、三维视线、RMSE、时间轴和全程车距。第三条重算到原楼体和场界的最小车体净空，并给出对应车辆与车次。将目录换为 [细化版](../等精度快速部署与并发排程_20261121/conclusions.md) 或 [冻结版](../双站定位与并发调度_连续站位_20261120/conclusions.md) 并省略 `--wall-clearance-m` 即可重算两份标称净空底表。[实验配置](refinement_config.yaml) · [验收记录](independent_validation.json) · [主结果](conclusions.md)
