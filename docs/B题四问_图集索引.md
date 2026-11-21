# B 题四问｜图集索引

推荐用下列 **12 张论文图**。每张提供 PDF 和 220 dpi PNG，Q1 另附全矢量 SVG。图件使用 2026-11-20 冻结归档的数值、场地、路线和计分口径，历版可供对照。回代与计分使用全精度底表。[中文审核报告](B题四问_图表与作答证据审核报告.md) · [正式结果索引](../results/README.md)

Q1 用浅青绿呈现可观测区，深青绿突出最优弧与推荐站。Q3 以蓝色为主，用不同明度的蓝灰与斜纹区分对照。Q2、Q4 沿用浅底与蓝橙重点色。相位纹理、实虚线、空实点和直接标注辅助灰度阅读。浅底分面、局部时间带及重点用色参考了去年论文。[2025 年优秀论文绘图样例与页码](../research/B题论文配色参考_2025优秀论文.md)

[Q1](#q1) · [Q2](#q2) · [Q3](#q3) · [Q4](#q4) · [只读重绘与溯源](#只读重绘与溯源)

## Q1

| 新版 | 阅读重点 | 直接来源与复核底表 |
| --- | --- | --- |
| 全场可观测域＋连续最优弧 [PDF][q1-pdf] · [SVG][q1-svg] · [PNG][q1-png] | 浅青绿矢量色块沿原 0.25 m 单元边界呈现三约束交集。深青绿实线和灰虚线区分 80° 圆上的连续可行弧与车体排除弧。实点标两个可行切点，淡赭叠线标完全落在排除弧内的 LOS 遮挡段。图脚分列网格估计和连续面积。 | [配置][q1-config]、[连续弧与推荐点][q1-summary]、[连续面积证书][q1-area]；[弧端点 CSV][q1-arcs]、[网格统计][q1-counts]用于查全精度。 |

正文优先用新版。历史 [可观测区域][q1-old-region]、[最优弧][q1-old-arcs]保留；[独立约束掩码][q1-old-masks]、[净空曲线][q1-old-clearance]、[三档网格对照][q1-old-resolution]适合附录或独立复验。

[分问图集与逐图对照][q1-atlas] · [逐图来源与哈希][q1-provenance] · [上一版配色][q1-prev] · [首版审阅图集][q1-first] · [出图代码](../src/q1/archive_plots.py)

## Q2

Q2(1) 沿用 [T2 双站图][q21-old]。实线为两车直达进站的路段，灰虚线为站间基线，红色点线仅为三维 LOS 的水平投影；它们投影跨 B3，但进入楼底投影时高度约 **29.165 m、15.151 m**，已超过 12 m 楼顶。站位与路点全精度见 [Q2(1) 结果][q21-result]。

| Q2(2) 新版 | 阅读重点 | 直接来源 |
| --- | --- | --- |
| 并发路线 [PDF][q22-route-pdf] · [PNG][q22-route-png] | 两幅全场图用同一浅底和米制比例；局部用蓝线和箭头突出 V3 从初始点去 T1、再接续 T3 的两段方向与长度。原位站用方块叠圆环，不画假路径；就近基线有六个原地车次。 | [场地][q2-scene]、[配置][q2-config]、[路点][q22-routes]、[分配][q22-assign] |
| 两套五车时序 [PDF][q22-time-pdf] · [PNG][q22-time-png] | 相同秒轴、同车序，直标最晚开测 **10.353 对 10.969 s**。蓝框标优化方案 V3 两段转场，蓝实条标 T3 观测；空心圆为同刻出发、到站，虚线不是复位完成。 | [场地][q2-scene]、[配置][q2-config]、[全精度时间轴][q22-timeline]、[方案对照][q22-compare] |

旧 [路线图][q22-old-route]和[甘特图][q22-old-time]仍在初始归档；Q4 仍继承同一 `optimized` 正式方案，不从新版图回取路点。

[分问图集与逐图对照][q2-atlas] · [逐图来源与哈希][q2-provenance] · [首版审阅图集][q2-first] · [出图代码](../src/q2/archive_plots.py)

## Q3

| 新版 | 阅读重点 | 直接来源与核验 |
| --- | --- | --- |
| 80 m 定位参数域 [PDF][q3-ab-pdf] · [PNG][q3-ab-png] | 浅蓝为两者均达标，带斜纹的蓝灰窄带仅指令角达标，余区两者都不达标。各自边界含等号；深色叉号标称参数在两个域外。 | [计算摘要][q3-summary]、[边界折线点][q3-ab-table]、[阈值][q3-limits]；[边界回代][q3-ab-check] |
| 三项误差贡献 [PDF][q3-err-pdf] · [PNG][q3-err-png] | 两格统一用蓝色、浅蓝灰斜纹、中蓝灰实填标车辆定位、云台零位、云台控制。左侧是方差占比，右侧是逐项消融的达标率增益（百分点）。66.837 m 对应实际指向临界，0 m 的最大消融增益放在图脚。 | [误差贡献与消融][q3-error-table] |

正文继续用原 [双口径可靠性曲线][q3-reliability]，附录用原 [五法验证图][q3-methods]。历史 [参数域][q3-old-ab]和[贡献图][q3-old-errors]保留，口径和阈值与新版相同。

[分问图集与逐图对照][q3-atlas] · [逐图来源与哈希][q3-provenance] · [上一版配色][q3-prev] · [首版审阅图集][q3-first] · [出图代码](../src/q3/archive_plots.py)

## Q4

新版仅用主执行口径：完整旧队列已预授权，中心断联时搭档仍可即时确认就绪。默认 `sample=main, variant=main`，展示 `recommended` 与 `original_append`（E7 空间面板只画推荐）；严格冻结和各单模型仍在[算法汇总][q4-algorithms]与[逐场评分][q4-metrics]。**全启按情景计、$\eta$ 在情景内按告警计；计分时间中的 60 s 可以是罚值，不是实开时刻。**

| 新版 | 阅读重点 | 直接来源 |
| --- | --- | --- |
| 策略总览 [PDF][q4-policy-pdf] · [PNG][q4-policy-png] | 七类、两指标、同场对比。灰色外环标基线、蓝色实点标推荐，重合仍可识别；均分时间包含未全启的罚值。 | [七类汇总][q4-category] |
| 恢复时刻 [PDF][q4-recovery-pdf] · [PNG][q4-recovery-png] | E3/E5/E6/E7 四格共用秒轴；10、20、30 s 为恢复时刻，而非停联长度；对齐均值而非逐场时间轴。 | [恢复汇总][q4-recovery] |
| E7 分层 [PDF][q4-factors-pdf] · [PNG][q4-factors-png] | 左图每个故障车 216 场；右图用单调明度蓝色阶画每种 T4 位置 15 场的均值，空心、实心菱形标最小、最大值。两图不是同一组条件下的单场对比。 | [故障分层][q4-failure]、[空间分层][q4-spatial] |
| T4 候选几何 [PDF][q4-candidates-pdf] · [PNG][q4-candidates-png] | 1 m 主网格可见节点最少和最多的 E1 两例。底色另为 0.5 m 显示掩码；实点是目录端点，不是最终使用的站。 | [Q2 场地][q2-scene]、[逐场评分与节点数][q4-metrics]、[情景输入及站对][q4-inputs] |
| `E2_V3` [PDF][q4-e2-pdf] · [PNG][q4-e2-png] | 故障截断 T1 的联合观测，但已合法开测仍计分；V5 复位后替补 T3。评分为 (2/3, 60 s) 对 (1, 21.244 s)。 | 三张时序共用：[代表例选择][q4-summary]、[逐场评分][q4-metrics]、[输入][q4-inputs]、[连续轨迹][q4-tracks]。时刻另可用[逐告警][q4-alerts]、[逐车事件][q4-events]复核。 |
| `E7_A90_R50_V2_C30` [PDF][q4-e7-pdf] · [PNG][q4-e7-png] | 浅赭带标 5–30 s 断联，灰带标 60 s 窗外。30 s 恢复后 V4 绕 B4 补 T2；57.990 s 是真实开测，窗外观测/复位仍绘出，不参与评分。 | 同上 |
| `E1_A255_R50` [PDF][q4-e1-pdf] · [PNG][q4-e1-png] | 两策按同一分配追加 T4，均以 21.332 s 全启。图上的持平不等于整类新增告警都无改善。 | 同上 |

[历史策略图][q4-old-policy]、[扰动因素图][q4-old-effects]、[候选区图][q4-old-candidates]仍在。保留[历史故障代表图][q4-old-timeline]的替补路线与**联合观测**区间，新版另把每辆车的相位分开，避免把 V5 独自保留的 T1 占用读作故障后仍双车联合观测。篇幅有限时，总览和最迟/替补时序优先，其余可入附录。**逐场表与逐车事件不被代表图替代。**

[断联授权/就绪确认补充图][q4-outage-fig]来自[独立配对归档][q4-outage]，未重绘，不计入新版 12 张，也不混入上述主汇总。

[分问图集与逐图对照][q4-atlas] · [逐图来源与哈希][q4-provenance] · [首版审阅图集][q4-first] · [出图代码](../src/q4/archive_plots.py)

## 只读重绘与溯源

在仓库根目录，按 [B 题依赖版本](../configs/requirements-b.txt) 准备环境。以下脚本不启动 `src.run`、不调求解器；只重建 Q1/T4 的显示掩码、按归档汇总和连续相位画图。

```bash
for q in q1 q2 q3 q4; do
  nohup env MPLBACKEND=Agg python3 -B -m src.shared.b_figures "$q" || break
done
```

每问打印一个新建 `runs/qN/figures_*` 路径。可传 `--output runs/<不存在的预览目录>`，已有目录和 `results/` 下的路径会拒绝。入口为 [`src/shared/b_figures/__main__.py`](../src/shared/b_figures/__main__.py)；[`common.py`](../src/shared/b_figures/common.py) 的 `ARCHIVES` 绑定来源归档。当前命令重绘推荐图件，Q2、Q4 重绘的 PDF/PNG 哈希与发布件一致。首版和蓝橙版的哈希匹配源码分别快照在本地 `runs/b_figures_previous_source_b5jlo0p0/` 和 `runs/b_figures_blue_orange_source_hxa_z0ef/`。Q3 三色版源码快照位于 `runs/b_figures_q3_blue_gray_mug5l81w/previous/`。

每问的 `provenance.json` 记录视觉版本、Git 基点、工作树制图源码及辅助代码 SHA-256、Python/Matplotlib/NumPy 版本、逐图直接输入、显示变换及 PDF/PNG/SVG 哈希。图件与制图源码的对应关系以 `generator_files` 哈希为准。源文件和输入相对仓库根目录，输出名相对清单目录。归档件可按清单核对字节，跨字体或绘图环境复跑时按数据、版式和对应版本核对。

[出图验收测试](../checks/test_b_archive_figures.py)覆盖四问的图件、清单和格式、哈希、目录保护与 60 s 严格计分。发布预览另检查 PDF 单页、PDF/PNG 画布尺寸、文字页界和显著重叠，并逐张查看地图符号、图例和相位。冻结数值的几何、概率和连续时序证据由[中文审核报告](B题四问_图表与作答证据审核报告.md)逐问链接，制图验收不替代这些证据。

[q1-first]: ../results/q1/可观测域与最优弧_审阅图集_20261121/README.md
[q1-prev]: ../results/q1/可观测域与最优弧_论文配色图集_20261121/README.md
[q1-atlas]: ../results/q1/可观测域与最优弧_青绿矢量图集_20261121/README.md
[q1-provenance]: ../results/q1/可观测域与最优弧_青绿矢量图集_20261121/provenance.json
[q1-pdf]: ../results/q1/可观测域与最优弧_青绿矢量图集_20261121/q1_observability.pdf
[q1-svg]: ../results/q1/可观测域与最优弧_青绿矢量图集_20261121/q1_observability.svg
[q1-png]: ../results/q1/可观测域与最优弧_青绿矢量图集_20261121/q1_observability.png
[q1-config]: ../results/q1/可观测区域与最优站位_连续圆弧_20261120/config.yaml
[q1-summary]: ../results/q1/可观测区域与最优站位_连续圆弧_20261120/summary.json
[q1-area]: ../results/q1/可观测区域与最优站位_连续圆弧_20261120/area_check_continuous.json
[q1-arcs]: ../results/q1/可观测区域与最优站位_连续圆弧_20261120/optimal_arcs.csv
[q1-counts]: ../results/q1/可观测区域与最优站位_连续圆弧_20261120/region_counts.csv
[q1-old-region]: ../results/q1/可观测区域与最优站位_连续圆弧_20261120/observable_region.png
[q1-old-arcs]: ../results/q1/可观测区域与最优站位_连续圆弧_20261120/optimal_arcs.png
[q1-old-masks]: ../results/q1/可观测区域与最优站位_连续圆弧_20261120/constraint_masks.png
[q1-old-clearance]: ../results/q1/可观测区域与最优站位_连续圆弧_20261120/clearance_profile.png
[q1-old-resolution]: ../results/q1/可观测区域与最优站位_连续圆弧_20261120/resolution_comparison.png

[q2-first]: ../results/q2/并发路线与五车时序_审阅图集_20261121/README.md
[q2-atlas]: ../results/q2/并发路线与五车时序_论文配色图集_20261121/README.md
[q2-provenance]: ../results/q2/并发路线与五车时序_论文配色图集_20261121/provenance.json
[q21-old]: ../results/q2/双站定位与并发调度_连续站位_20261120/q21_stations.png
[q21-result]: ../results/q2/双站定位与并发调度_连续站位_20261120/q21_result.json
[q2-scene]: ../results/q2/双站定位与并发调度_连续站位_20261120/scene_config.yaml
[q2-config]: ../results/q2/双站定位与并发调度_连续站位_20261120/config.yaml
[q22-route-pdf]: ../results/q2/并发路线与五车时序_论文配色图集_20261121/q22_routes.pdf
[q22-route-png]: ../results/q2/并发路线与五车时序_论文配色图集_20261121/q22_routes.png
[q22-time-pdf]: ../results/q2/并发路线与五车时序_论文配色图集_20261121/q22_timeline.pdf
[q22-time-png]: ../results/q2/并发路线与五车时序_论文配色图集_20261121/q22_timeline.png
[q22-routes]: ../results/q2/双站定位与并发调度_连续站位_20261120/q22_routes.json
[q22-assign]: ../results/q2/双站定位与并发调度_连续站位_20261120/q22_assignments.csv
[q22-timeline]: ../results/q2/双站定位与并发调度_连续站位_20261120/q22_timeline.csv
[q22-compare]: ../results/q2/双站定位与并发调度_连续站位_20261120/q22_comparison.json
[q22-old-route]: ../results/q2/双站定位与并发调度_连续站位_20261120/q22_routes.png
[q22-old-time]: ../results/q2/双站定位与并发调度_连续站位_20261120/q22_timeline.png

[q3-first]: ../results/q3/80米参数域与误差贡献_审阅图集_20261121/README.md
[q3-prev]: ../results/q3/80米参数域与误差贡献_三色图集_20261121/README.md
[q3-atlas]: ../results/q3/80米参数域与误差贡献_蓝灰配色图集_20261121/README.md
[q3-provenance]: ../results/q3/80米参数域与误差贡献_蓝灰配色图集_20261121/provenance.json
[q3-ab-pdf]: ../results/q3/80米参数域与误差贡献_蓝灰配色图集_20261121/q3_parameters_80m.pdf
[q3-ab-png]: ../results/q3/80米参数域与误差贡献_蓝灰配色图集_20261121/q3_parameters_80m.png
[q3-err-pdf]: ../results/q3/80米参数域与误差贡献_蓝灰配色图集_20261121/q3_error_sources.pdf
[q3-err-png]: ../results/q3/80米参数域与误差贡献_蓝灰配色图集_20261121/q3_error_sources.png
[q3-summary]: ../results/q3/测向可靠性与80米定位约束_五方法对照_20261120/summary.json
[q3-ab-table]: ../results/q3/测向可靠性与80米定位约束_五方法对照_20261120/ab_boundary.csv
[q3-ab-check]: ../results/q3/测向可靠性与80米定位约束_五方法对照_20261120/ab_boundary_checks.csv
[q3-limits]: ../results/q3/测向可靠性与80米定位约束_五方法对照_20261120/limits.csv
[q3-error-table]: ../results/q3/测向可靠性与80米定位约束_五方法对照_20261120/error_contributions.csv
[q3-reliability]: ../results/q3/测向可靠性与80米定位约束_五方法对照_20261120/reliability.pdf
[q3-methods]: ../results/q3/测向可靠性与80米定位约束_五方法对照_20261120/method_validation.pdf
[q3-old-ab]: ../results/q3/测向可靠性与80米定位约束_五方法对照_20261120/ab_feasible_region.pdf
[q3-old-errors]: ../results/q3/测向可靠性与80米定位约束_五方法对照_20261120/error_contributions.pdf

[q4-first]: ../results/q4/扰动评分与代表时序_审阅图集_20261121/README.md
[q4-atlas]: ../results/q4/扰动评分与代表时序_论文配色图集_20261121/README.md
[q4-provenance]: ../results/q4/扰动评分与代表时序_论文配色图集_20261121/provenance.json
[q4-policy-pdf]: ../results/q4/扰动评分与代表时序_论文配色图集_20261121/q4_policy.pdf
[q4-policy-png]: ../results/q4/扰动评分与代表时序_论文配色图集_20261121/q4_policy.png
[q4-recovery-pdf]: ../results/q4/扰动评分与代表时序_论文配色图集_20261121/q4_recovery.pdf
[q4-recovery-png]: ../results/q4/扰动评分与代表时序_论文配色图集_20261121/q4_recovery.png
[q4-factors-pdf]: ../results/q4/扰动评分与代表时序_论文配色图集_20261121/q4_e7_factors.pdf
[q4-factors-png]: ../results/q4/扰动评分与代表时序_论文配色图集_20261121/q4_e7_factors.png
[q4-candidates-pdf]: ../results/q4/扰动评分与代表时序_论文配色图集_20261121/q4_candidate_regions.pdf
[q4-candidates-png]: ../results/q4/扰动评分与代表时序_论文配色图集_20261121/q4_candidate_regions.png
[q4-e2-pdf]: ../results/q4/扰动评分与代表时序_论文配色图集_20261121/q4_timeline_e2.pdf
[q4-e2-png]: ../results/q4/扰动评分与代表时序_论文配色图集_20261121/q4_timeline_e2.png
[q4-e7-pdf]: ../results/q4/扰动评分与代表时序_论文配色图集_20261121/q4_timeline_e7.pdf
[q4-e7-png]: ../results/q4/扰动评分与代表时序_论文配色图集_20261121/q4_timeline_e7.png
[q4-e1-pdf]: ../results/q4/扰动评分与代表时序_论文配色图集_20261121/q4_timeline_e1.pdf
[q4-e1-png]: ../results/q4/扰动评分与代表时序_论文配色图集_20261121/q4_timeline_e1.png
[q4-algorithms]: ../results/q4/扰动重调度_四模型择优_20261120/algorithm_summary.csv
[q4-category]: ../results/q4/扰动重调度_四模型择优_20261120/category_summary.csv
[q4-recovery]: ../results/q4/扰动重调度_四模型择优_20261120/recovery_summary.csv
[q4-failure]: ../results/q4/扰动重调度_四模型择优_20261120/failure_summary.csv
[q4-spatial]: ../results/q4/扰动重调度_四模型择优_20261120/spatial_summary.csv
[q4-summary]: ../results/q4/扰动重调度_四模型择优_20261120/summary.json
[q4-metrics]: ../results/q4/扰动重调度_四模型择优_20261120/scenario_metrics.csv
[q4-inputs]: ../results/q4/扰动重调度_四模型择优_20261120/scenario_inputs.jsonl.gz
[q4-tracks]: ../results/q4/扰动重调度_四模型择优_20261120/policy_plans_and_tracks.jsonl.gz
[q4-alerts]: ../results/q4/扰动重调度_四模型择优_20261120/alert_results.csv.gz
[q4-events]: ../results/q4/扰动重调度_四模型择优_20261120/policy_vehicle_timelines.csv.gz
[q4-old-policy]: ../results/q4/扰动重调度_四模型择优_20261120/policy_performance.pdf
[q4-old-effects]: ../results/q4/扰动重调度_四模型择优_20261120/disturbance_effects.pdf
[q4-old-candidates]: ../results/q4/扰动重调度_四模型择优_20261120/candidate_regions.pdf
[q4-old-timeline]: ../results/q4/扰动重调度_四模型择优_20261120/representative_timeline.pdf
[q4-outage]: ../results/q4/断联授权与就绪确认对照_20261121/conclusions.md
[q4-outage-fig]: ../results/q4/断联授权与就绪确认对照_20261121/outage_comparison.pdf
