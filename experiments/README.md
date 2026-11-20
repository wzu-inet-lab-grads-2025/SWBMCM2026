# 探索实验

当前 B 题 worktree 的数据核查、一次性比较脚本、候选模型试验和模拟器原型放在 `experiments/b/`。值得复跑的探索代码也放在这里，输出写入本 worktree 的 `runs/`。每次试验记录要判断的问题、输入与单位、复跑命令、结果、局限，以及它改变了哪项决定。有随机抽样时记录种子、样本数和不确定度。

| 首轮实验 | 复跑命令（仓库根目录） | 回答的问题 |
| --- | --- | --- |
| [题面输入核查](b/input_audit.py) | `python3 experiments/b/input_audit.py` | 目标与楼体的相对位置如何？初始车位能否通过仰角筛查？哪个几何特例可供独立复核？ |
| [第一问独立解与区域图](b/q1_independent/REPORT.md) | `python3 experiments/b/q1_independent/solve.py`（[环境说明](b/q1_independent/README.md)） | 由仰角推出的连续最优半径能否达到？哪些圆弧可行？等质量时哪辆车到站路程最短？ |
| [二（2）双车复用算例](b/q2_reuse_example.py) | `python3 experiments/b/q2_reuse_example.py` | 固定 T3 与两个站位，给定三辆车的可出发时刻，按新增路程和按共同开始时间分别会选谁？[手算记录](../docs/q2-baseline.md) |
| [二（2）六种顺序的配对比较](b/q2_order_probe.py) | `python3 experiments/b/q2_order_probe.py` | 用初始站与独立 20 m 网格，比较新增路程之和、较长单车路程及同步就绪。逐告警输出在 `runs/b/q2_order_probe.csv`。 |
| [二（2）零行驶穷举](b/q2_stationary_check.py) | `python3 experiments/b/q2_stationary_check.py` | 固定每车在初始站，穷举可见车对与共享车辆的先后顺序，核对该子问题的最晚观测开始时刻。 |
| [第二问独立方案复核](b/check_q2_solutions.py) | `python3 experiments/b/check_q2_solutions.py 路径/summary.json` | 根据候选方案的站位、路线和事件重算二（1）的水平定位误差与行驶时间，以及二（2）的逐车时间轴。可同时传入多份方案。 |
| [第三问解析推导](b/q3_analytic/REPORT.md) | `python3 experiments/b/q3_analytic/verify.py` | 检验位置误差传播、角差折回、单调性与一阶近似的适用范围。 |
| [第三问圆周积分](b/q3_quadrature/README.md) | `python3 experiments/b/check_q3_thresholds.py` | 用旋转坐标中的独立求积复核两种误差口径的临界里程、80 m 覆盖率。[汇总记录](../docs/q3-independent.md) |
| [第三问随机仿真](b/q3_simulation/report.md) | `python3 experiments/b/q3_simulation/simulation.py` | 独立样本检验 80 m 及临界里程附近的覆盖率，并估计限程的抽样不确定度。 |

[题面参数转录](b/scene.json) 的数字保留原题单位。核查脚本只读，在终端输出结果。核查发现和实验优先级见[建模笔记](../docs/modeling-notes.md)。方法被采用时，将正式实现整理进 `src/`，将独立复核材料放入 `checks/`，保留从正式实现回到原实验的记录。

本轮结果可直接查看[第一问区域图](b/snapshots/q1/observable_region.png)、[最优性与边界核查数据](b/snapshots/q1/result.json)和[二（2）逐告警比较表](b/snapshots/q2_order_probe.csv)。重新运行实验时，输出仍写入 `runs/b/`。
