# B 题第二问运行记录

## 运行来源和版本

- 日期为 2026-11-20，归档目标为 `yyl-b1`。运行快照以 `e22186b25a6b7187405522f6cf0758e6b2a737b8` 为基线，叠加本次第二问源码。逐文件 SHA-256 和输入指纹见 [run_record.json](run_record.json)。
- 原网格运行在本地 `runs/q2/20261120T124734Z_2lvkw1ca/`。后续加入三个毫米级站点并核查连续域下界，原始复核数据位于本地 `runs/q2/20261120_实验结果审核/`。归档复跑用独立的第二问源码快照生成结果表、绘图和验收文件。
- 场地与实验参数分别由 [scene_config.yaml](scene_config.yaml) 和 [config.yaml](config.yaml) 冻结。细化站来自 V3 的 T1–T3 接续链及 V2 的 T2 最早就绪时间搜索，复跑时加入各级网格，优化、就近和固定车辆对照共用候选。
- 软件环境为 Python 3.13.9、NumPy 2.3.5、SciPy 1.17.0、PyYAML 6.0.3、Matplotlib 3.10.8。原题第 1–4 页用于坐标、约束和指标核对。 [英文原题](<../../../problem/B/2026_“ShuWei Cup”B_Problem.pdf>)

## 复跑与独立验收

从仓库根目录复跑。统一入口打印 `runs/q2/` 内的新运行目录，导出两问数值结果及三张英文图。

```bash
nohup env OPENBLAS_NUM_THREADS=1 python3 -B -m src.run q2 --profile full \
  --config results/q2/双站定位与并发调度_连续站位_20261120/config.yaml
```

省略 `--config` 时使用 `configs/q2.yaml`，复跑最晚开测为 10.731534 s 的原网格实验。 [原网格复现说明](../../../docs/B_Question_2_Reproduction.md)

可直接从归档重算 2（1）的精度和 2（2）的完整调度。独立验收器使用仓库标称场地，与归档的场地快照一致。它复核三维视线、全路段净空、全程车间距、各车事件、两套主要方案的目标值和差值。

```bash
q2_archive=results/q2/双站定位与并发调度_连续站位_20261120
nohup python3 -B -m checks.q2_validate \
  --q21 "$q2_archive/q21_result.json" \
  --q22-routes "$q2_archive/q22_routes.json" \
  --q22-timeline "$q2_archive/q22_timeline.csv" \
  --require-vehicle-separation --compare greedy optimized
```

覆盖五车三十种有向接续链，复核连续场地的 10.352000 s 数值下界。

```bash
nohup env OPENBLAS_NUM_THREADS=1 python3 -B -m checks.q2_continuous_bound
```

本次归档中的第一、二问共 49 项自动测试通过。

```bash
nohup env OPENBLAS_NUM_THREADS=1 python3 -B -m pytest -q -p no:cacheprovider \
  checks/test_q1*.py checks/test_q2*.py
```

## 文件与口径

| 文件 | 内容 |
| --- | --- |
| [conclusions.md](conclusions.md) | 两小问的中文结论、精度指标、约束、调度目标、基线差异和最优性范围 |
| [tables_and_figures.md](tables_and_figures.md) | 英文结果表、两套完整五车时间轴与图题 |
| [q21_result.json](q21_result.json)、[q21_sensitivity.csv](q21_sensitivity.csv) | T2 双站解析构造的实算数据、逐车路点、噪声和边界余量实验 |
| [q21_stations.png](q21_stations.png) | 双车进站路线、基线与 90° 交会的局部放大图 |
| [q22_assignments.csv](q22_assignments.csv)、[q22_routes.json](q22_routes.json)、[q22_timeline.csv](q22_timeline.csv) | 以 `optimized` 和 `greedy` 标识两套主要方案。`fixed_vehicles` 为固定就近配对后重新选站的对照。 |
| [q22_routes.png](q22_routes.png)、[q22_timeline.png](q22_timeline.png) | 优化与就近方案的路线对照、全五车甘特图 |
| [q22_comparison.json](q22_comparison.json)、[q22_path_comparison.csv](q22_path_comparison.csv) | 六序就近基线、关键链、候选集证书和两档路径图的固定端点路长对照 |
| [validation.json](validation.json)、[continuous_bound.json](continuous_bound.json)、[original_grid_chain_bound.json](original_grid_chain_bound.json) | 独立几何时序回放、连续域数值下界和原网格下界 |

归档采用仓库既有求解入口、绘图和独立验收流程，专项 skill 使用记录为无。源码位于 [src/q2](../../../src/q2)，验收入口见 [checks/q2_validation.md](../../../checks/q2_validation.md)。
