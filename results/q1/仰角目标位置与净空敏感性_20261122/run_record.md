# 运行记录｜第一问参数敏感性

- 来源：`runs/q1/20261122T071018Z_参数敏感性_cftd7wj2/`；`runs/` 按约定只留本地。
- 题面：[官方英文 B 题第 1–3 页](../../../problem/B/2026_“ShuWei%20Cup”B_Problem.pdf)。标称场景来自[第一问归档配置](../可观测区域与最优站位_连续圆弧_20261120/config.yaml)；试算幅度来自 [sensitivity_config.yaml](sensitivity_config.yaml)。
- 运行时 Git 基线：`1a4374e0c28d55944a8dc69cd813075de6113486`。新增复算脚本是该基线上的工作区改动；实际输入、源码、图表的 SHA-256 和 Python、NumPy、SciPy、Matplotlib、PyYAML 版本见 [provenance.json](provenance.json)。
- 标称复算的推荐站坐标和连续面积上下界与 11 月 20 日归档逐值一致。该次只新增敏感性证据，不覆盖标称运行。
- 复算脚本、图表和结果初稿使用 OpenCode / Magpie `group/gpt-6.2-sol` 辅助（使用日期 2026-11-22，模型版本发布日期未提供），正式结论由队员复核定稿。

## 复跑

从仓库根目录执行；每次写入一个新的 `runs/q1/` 子目录：

```bash
archive='results/q1/仰角目标位置与净空敏感性_20261122'
nohup env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MPLBACKEND=Agg \
  python3 -B -m checks.q1_sensitivity \
  --config "$archive/config.yaml" --scenarios "$archive/sensitivity_config.yaml"
```

核验不调用选站求解器。下面的命令只读归档并打印 JSON；要核验新运行，把 `--run` 后的目录换成上一步打印的路径：

```bash
nohup env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  python3 -B -m checks.q1_sensitivity_validate \
  --run results/q1/仰角目标位置与净空敏感性_20261122
```

## 核验与文件

- [sensitivity.csv](sensitivity.csv)：一行标称、16 行单因素扰动。`perturbation` 的单位由 `factor` 后缀给出（m 或度）；`station_shift_m` 以标称推荐站为参照；`vehicle_clearance_m` 仍按 1.5 m 实体半径计算，`clearance_above_requirement_m` 再减附加停车净空要求；`fixed_*` 列是原推荐站不挪动的复核。
- [validation.json](validation.json)：17 个重选站都通过独立闭盒 LOS、停车和闭仰角复核；6 个扰动下原站仅因仰角上限失效。0.0625 m 网格的面积变化方向与所有连续复算一致，网格值与连续区间中点的最大绝对差为 9.708 m²，所以正文引用连续面积，不取网格估计代替。
- [sensitivity.pdf](sensitivity.pdf)、[sensitivity.png](sensitivity.png)：左图为重新选站的 $Q^*$；右图为连续可观测面积的相对变化。蓝点和橙叉均表示实测工况，分别表示原站仍可行和已失效，连线只供辨读。
- 按归档快照复跑，`sensitivity.csv` 与归档逐字节一致，独立复核输出也与 `validation.json` 一致。第一问既有几何、网格、优化和入口测试共 37 项通过；新增实验用单独的逐场复核命令验收，未新增 pytest 用例。分析运行没有修改统一入口、标称模型或 LaTeX 正文。
