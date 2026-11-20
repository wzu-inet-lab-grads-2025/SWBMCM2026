# 运行记录：q1 可观测区域与最优站位

## 来源

- 正式运行 ID：`20261120T105624Z_byf9quhb`（本地 `runs/q1/`，`runs/` 按仓库约定不入库）。
- 运行日期：2026-11-20。
- 代码基线提交：`4f5334ee22b0af3517601d5357d121795e685243`。
- 入库分支：`yyl-b1`。代码版本以 `summary.json` 的 `run.source_sha256` 为准；该字段逐文件记录 `src/run.py`、`src/q1/*.py` 的 SHA-256。
- 题面：`problem/B/2026_“ShuWei Cup”B_Problem.pdf`，第 1–3 页。SHA-256 `9585081e724cef939d4e5713ea72f9eddefd4469a3b519a8ce184677d407692a`。

## 环境

| 项目 | 值 |
|---|---|
| Python | 3.13.9 |
| NumPy | 2.3.5 |
| SciPy | 1.17.0 |
| Matplotlib | 3.10.8 |
| PyYAML | 6.0.3 |
| pytest | 9.0.2 |

## 命令

```bash
nohup python3 -B -m src.run q1 --profile full
```

全量运行按 0.5/0.25/0.125 m 三档网格计算，再完成最优圆核验、净空择优和绘图。总耗时 1.96 s，其中三档网格计算合计 0.348 s、站位求解 0.106 s、绘图 1.401 s。

## 本次参数

与 `config.yaml` 完全一致，关键值：

- 场地 \(100\times100\) m，调度中心 \((50,50)\)；
- T1 告警 \((30^\circ,40,45)\)；
- 车体半径 1.5 m，相机高 2 m；
- 云台仰角 \([-10^\circ,80^\circ]\)；
- 网格：调试 1.0 m、主图 0.25 m、收敛对照 0.5/0.25/0.125 m；
- 数值参数：位置临界复核 \(10^{-9}\) m、距离一致性 \(10^{-9}\) m、角度对照 \(10^{-8}\)°、净空误差界 \(10^{-8}\) m。

## 复跑与复核

1. 复跑主结果（使用随目录保存的参数快照）：

```bash
nohup python3 -B -m src.run q1 --profile full --config results/q1/可观测区域与最优站位_连续圆弧_20261120/config.yaml
```

2. 复跑全部测试：

```bash
nohup python3 -B -m pytest -q -p no:cacheprovider checks
```

3. 连续面积复核（需要一个本地 q1 运行目录；可先用上一条命令生成）：

```bash
nohup python3 -B -m checks.q1_area --run <本地 q1 运行目录>
```

4. 替代模型对照（需要一个本地 q1 运行目录作为基线）：

```bash
nohup python3 -B -m src.run q1 --compare-baseline <本地 q1 运行目录> \
  --config results/q1/可观测区域与最优站位_连续圆弧_20261120/config.yaml \
  --comparison-config results/q1/可观测区域与最优站位_连续圆弧_20261120/comparison_config.yaml
```

`area_check_continuous.md` 与 `model_comparison_projected_nlp.md` 来自同一天的本地复核运行，其中的 `runs/q1/...` 绝对路径只在本机存在；复跑时以当时新生成的运行目录替换。

## 文件清单

| 文件 | 内容 |
|---|---|
| `summary.md` | 队内可读结果摘要：完整最优弧、代表站点、并列规则、推荐站位 |
| `summary.json` | 机器可读完整结果：角域、代表站点、净空证书、逐栋复核、代码哈希 |
| `station_table.csv` | 代表站点底表：角度、坐标、距离、仰角、质量、最小净空 |
| `optimal_arcs.csv` | 最优弧两段的端点与开闭语义 |
| `region_counts.csv` | 三档网格的独立通过数、顺序剩余数、面积与耗时 |
| `config.yaml` | 本次运行的参数快照 |
| `field_layout.png` | 场地、建筑、T1 和调度中心 |
| `constraint_masks.png` | 车辆、仰角、LOS 三张独立掩码 |
| `observable_region.png` | 0.25 m 网格下三约束交集的可观测区域 |
| `optimal_arcs.png` | 最优圆、可行弧、B2 车体禁入弧、LOS 遮挡弧和代表点 |
| `clearance_profile.png` | 最优弧上的最小车辆净空曲线与局部峰 |
| `resolution_comparison.png` | 0.5/0.25/0.125 m 区域对照 |
| `area_check_continuous.*` | 连续面积竖条包围复核 |
| `model_comparison_projected_nlp.*` | 二维投影约束规划对照实验 |
| `comparison_config.yaml` | 对照实验设置快照 |
| `conclusions.md` | 中文结论、口径、证据与边界 |

## 验收核对

- `summary.json` 的 `primary_optimum.witness_valid` 为 `true`；
- `tie_break.optimality_gap_m` 为 \(9.15\times10^{-13}\) m，小于 \(10^{-8}\) m；
- `validation.independent_geometry_passed` 为 `true`，五栋建筑的车辆与 LOS 判定均为 `true`；
- 37 项测试通过。
