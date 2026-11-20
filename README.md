# SWBMCM2026

本仓库用于三人团队备战和参加 2026 年数维杯全国大学生数学建模挑战赛（秋季赛），记录备赛调研与历年赛题演练，并组织正式比赛的建模代码、实验结果和论文。

## 项目目录

下面是团队约定的目录结构。目录随第一份实际内容创建，`qN` 代表实际小问编号，例如 `q3`。

```text
SWBMCM2026/
├── research/              # 专题调研，每篇笔记附参考文献
├── docs/
│   ├── modeling-notes.md  # 题意拆解、初步思路、假设和验证计划
│   └── decisions.md       # 关键决定、依据和受影响的结果
├── problem/
│   ├── A/、B/、C/、D/     # 官方题面及各题说明
│   ├── translations/     # 中文翻译和配图
│   ├── official/         # 承诺书、格式规范和提交流程
│   └── archives/         # 原始赛题包和模板包
├── data/
│   ├── README.md          # 数据来源、版本、获取方式和预期路径
│   └── raw/               # 原始数据，按题号存放
├── src/
│   ├── run.py             # 统一运行入口
│   ├── qN/                # 各小问的代码，按需要拆分模块
│   └── shared/            # 已有跨问复用的数据处理、模型或指标
├── configs/               # 各问的参数和实验配置
├── checks/                # 手算样例、约束复核和指标重算
├── results/
│   ├── README.md          # 关键结果的复跑方式和复核记录
│   └── qN/                # 正文采用的图表、底表和运行记录
├── paper/                 # 论文源稿、引用和样式，官方示例入口为 demo.tex
├── tools/                 # 提交打包等独立脚本
├── runs/                  # 本地实验输出
├── scratch/               # 本地草稿和临时探索
└── submission/            # 本地组装的最终提交材料
```

## 第一问结果（B 题，2026-11-20）

**状态：** 已完成并复核。代码在 `src/q1/`，正式结果在 [`results/q1/可观测区域与最优站位_连续圆弧_20261120/`](results/q1/可观测区域与最优站位_连续圆弧_20261120/conclusions.md)，原始运行目录为 `runs/q1/20261120T105624Z_byf9quhb/`（`runs/` 按约定只留本地，不入库）。

**主要结果**

| 项目 | 结果 |
|---|---|
| 主目标 | 最大化 \(Q=1/r\)，等价于最小化到 \(T_1\) 的水平距离 |
| 最优水平距离 | \(r^*=7.582060170464\) m（由 \(43/\tan80^\circ\) 解析得到） |
| 最优观测质量 | \(Q^*=0.131890274875\) m\(^{-1}\) |
| 最优仰角 | \(80^\circ\) |
| 完整并列解 | 以 \(T_1\) 水平投影为圆心、半径 \(r_0\) 的连续圆弧，角域 \([0^\circ,160.747873328^\circ]\cup[236.896046854^\circ,360^\circ)\) |
| 推荐站位 | \((88.825410107,\ 76.322854075)\) m，圆周角约 \(56.503855631^\circ\) |
| 最小车辆净空 | \(9.6745898929\) m，受场地东界与 B2 东北角共同限制 |
| 可观测面积 | 0.25 m 网格 \(5614.9375\) m\(^2\)；连续截面包围 \([5608.767083,5608.774146]\) m\(^2\)，建议采用约 \(5608.77\) m\(^2\) |

**并列解选择规则：** 先保留达到 \(Q^*\) 的全部站点；在整条最优弧上最大化最小车辆净空（车体外缘到四条场界和五栋建筑底面的最短距离）；净空仍在浮点余量内并列时，按 \(x\) 升序、再按 \(y\) 升序。净空择优的全域上界差为 \(9.15\times10^{-13}\) m。

**图与底表**

![T1 可观测区域（0.25 m 网格）](<results/q1/可观测区域与最优站位_连续圆弧_20261120/observable_region.png>)

![最优圆、可行弧与代表站点](<results/q1/可观测区域与最优站位_连续圆弧_20261120/optimal_arcs.png>)

- 其他图：[三约束独立掩码](<results/q1/可观测区域与最优站位_连续圆弧_20261120/constraint_masks.png>)、[净空曲线](<results/q1/可观测区域与最优站位_连续圆弧_20261120/clearance_profile.png>)、[分辨率对照](<results/q1/可观测区域与最优站位_连续圆弧_20261120/resolution_comparison.png>)、[场地布局](<results/q1/可观测区域与最优站位_连续圆弧_20261120/field_layout.png>)
- 底表：[代表站点](<results/q1/可观测区域与最优站位_连续圆弧_20261120/station_table.csv>)、[最优弧端点](<results/q1/可观测区域与最优站位_连续圆弧_20261120/optimal_arcs.csv>)、[网格统计](<results/q1/可观测区域与最优站位_连续圆弧_20261120/region_counts.csv>)
- 结果说明：[中文结论](<results/q1/可观测区域与最优站位_连续圆弧_20261120/conclusions.md>)、[运行记录](<results/q1/可观测区域与最优站位_连续圆弧_20261120/run_record.md>)、[完整结果 JSON](<results/q1/可观测区域与最优站位_连续圆弧_20261120/summary.json>)
- 独立复核：[连续面积包围](<results/q1/可观测区域与最优站位_连续圆弧_20261120/area_check_continuous.md>)、[二维投影约束规划对照](<results/q1/可观测区域与最优站位_连续圆弧_20261120/model_comparison_projected_nlp.md>)

**复跑**

```bash
nohup python3 -B -m src.run q1 --profile full \
  --config results/q1/可观测区域与最优站位_连续圆弧_20261120/config.yaml
```

第一问自动测试（37 项）：`nohup python3 -B -m pytest -q -p no:cacheprovider checks/test_q1*.py`。

## 第二问结果（B 题，2026-11-20）

结果、英文论文图表和运行记录保存在 `results/q2/双站定位与并发调度_连续站位_20261120/`。求解入口为 `src.run q2`，代码在 `src/q2/`，独立验收脚本在 `checks/`。 [归档结论](results/q2/双站定位与并发调度_连续站位_20261120/conclusions.md) · [英文图表](results/q2/双站定位与并发调度_连续站位_20261120/tables_and_figures.md) · [运行记录](results/q2/双站定位与并发调度_连续站位_20261120/run_record.md)

### 2（1）双站定位精度与 T2 最优站位

以一阶二维水平定位均方根误差 $E=\sigma\sqrt{r_1^2+r_2^2}/|\sin\gamma|$ 为精度指标。其中 $r_i$ 为各站到目标的水平距离，$\gamma$ 为水平交会角，两车的独立测角误差标准差均为 $\sigma=\pi/180$ rad。该指标同时计入两个水平分量，对坐标旋转不变，单位为米。基线 $B$ 通过 $B^2=r_1^2+r_2^2-2r_1r_2\cos\gamma$ 进入交会几何。

**Table 1. Optimal station pair and travel times for T2.**

| Vehicle | Station center (m) | Range to T2 (m) | Travel distance (m) | Travel time (s) | Elevation |
| --- | --- | ---: | ---: | ---: | ---: |
| V1 | (5.210066466, 72.500000000) | 5.818790363 | 59.313513 | 19.771171 | 80° |
| V3 | (11.028856830, 78.318790363) | 5.818790363 | 8.235816 | 2.745272 | 80° |

两站同时达到仰角约束给出的距离下界，基线 **8.229012248 m**、交会角 **90°**，取得指标最小值 **0.143623358 m**。两车的直达路段、30 s 行驶时限和三维视线均通过独立验收。近共线时优先换站，严格共线时剔除配对；合法的仰角与车体边界仍可使用，视线接触楼体时判为遮挡。 [解析站位与退化分析](results/q2/双站定位与并发调度_连续站位_20261120/conclusions.md) · [全精度站位和路线](results/q2/双站定位与并发调度_连续站位_20261120/q21_result.json) · [噪声及边界试验](results/q2/双站定位与并发调度_连续站位_20261120/q21_sensitivity.csv)

![T2 station geometry and vehicle approaches](results/q2/双站定位与并发调度_连续站位_20261120/q21_stations.png)

*Figure 1. Vehicle approaches and horizontal bearing geometry of the optimal T2 station pair. The two sightlines clear the roof of B3.*

### 2（2）三告警分配、路线与五车时间轴

三条告警各用两辆车。分配与路线以最小化三条告警的最晚同步开测为目标。两车均到站并完成指向后共同观测 5 s，复用车在本车复位后出发。

**Table 2. Station assignments and synchronized observation start times.**

| Alert | Optimized station centers (m) | Start (s) | Nearest-assignment station centers (m) | Start (s) |
| --- | --- | ---: | --- | ---: |
| T1 | V3 (15.042, 87.078), V5 (50, 93) | 1.532033 | V3 (14, 86), V5 (50, 93) | 1.532033 |
| T2 | V1 (15, 14), V2 (72.680, 1.510) | 10.204157 | V1 (15, 14), V3 (14, 86) | **10.968984** |
| T3 | V3 (16.767, 88.660), V4 (88, 68) | **10.352849** | V2 (88, 10), V4 (88, 68) | 5.777589 |

就近法按两车避障路长之和贪心选择，路长并列时比较同步开测时间，并从六种处理顺序中取最好结果。优化由 V1、V2 并行准备 T2，让 V3 从 T1 接续 T3，缩短决定最晚开测的云台转向链。最晚开测提前 **0.616135 s（5.617%）**。连续场地的数值下界为 **10.352000 s**，与可行方案相差约 **0.849 ms**。原 1.25 m 档有限候选集的最优值为 10.731534 s。V3 的 T3 站靠近 B5 遮挡边界，复跑使用表中的毫米级坐标。

![Vehicle routes and observation stations for the two schedules](results/q2/双站定位与并发调度_连续站位_20261120/q22_routes.png)

*Figure 2. Vehicle routes for the optimized schedule and nearest-assignment baseline. Open circles mark stations. Stars mark alert projections.*

![Complete five-vehicle observation timelines](results/q2/双站定位与并发调度_连续站位_20261120/q22_timeline.png)

*Figure 3. Five-vehicle timelines. Dashed lines mark the latest synchronized observation start in each schedule.*

- 时间轴记录两套方案中每辆车的出发、到站、指向、等待、观测起止、复位及后续出发。 [完整五车时间轴](results/q2/双站定位与并发调度_连续站位_20261120/q22_timeline.csv)
- [分配底表](results/q2/双站定位与并发调度_连续站位_20261120/q22_assignments.csv) · [路线全路点](results/q2/双站定位与并发调度_连续站位_20261120/q22_routes.json) · [六序基线及关键链](results/q2/双站定位与并发调度_连续站位_20261120/q22_comparison.json)
- [独立验收](results/q2/双站定位与并发调度_连续站位_20261120/validation.json) · [连续域下界](results/q2/双站定位与并发调度_连续站位_20261120/continuous_bound.json) · [按原题逐项审核](docs/B_Question_2_Result_Audit.md)

**复跑归档结果**

```bash
nohup env OPENBLAS_NUM_THREADS=1 python3 -B -m src.run q2 --profile full \
  --config results/q2/双站定位与并发调度_连续站位_20261120/config.yaml
```

复跑路径由命令打印，新产物写入本地 `runs/q2/`。归档快照的第一、二问共 **49 项测试通过**。 [复跑和验收命令](results/q2/双站定位与并发调度_连续站位_20261120/run_record.md)

## 协作约定

- 队员和 agent 开工前先查看本说明，优先沿用已有目录和文件。按任务范围创建文件，各问的代码、配置、结果和论文段落使用同一编号。
- 跨问共用的假设、数据口径或指标发生变化时，在 `docs/decisions.md` 记录依据和需要重跑的结果。待验证的思路先记在 `docs/modeling-notes.md`。
- 原始附件保持原样，实验产物先写入当前检出的 `runs/`。经队员复核并用于论文的产物放入 `results/qN/`，附上配置、运行命令和代码版本。
- `data/raw/C/`、`runs/`、`scratch/` 和 `submission/` 由 `.gitignore` 管理，保留在本地。手工创建的 worktree 默认放在同级的 `../SWBMCM2026-worktrees/`，由工具创建的 worktree 沿用工具自己的位置。
