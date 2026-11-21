# Q1｜可观测域与最优弧（审阅图集，2026-11-21）

新版同时给出全场三约束交集和 80° 圆局部。连续可行弧包含两个车体相切端点；B2 的 LOS 遮挡段完全位于车辆禁入弧内。图中 0.25 m 网格估计与独立连续面积分列；不以网格替代连续最优性或净空证书。

| 图件 | 冻结配置、数值及原图 |
| --- | --- |
| [PDF](q1_observability.pdf) · [PNG](q1_observability.png) | [配置](../可观测区域与最优站位_连续圆弧_20261120/config.yaml) · [网格底表](../可观测区域与最优站位_连续圆弧_20261120/region_counts.csv) · [连续结果](../可观测区域与最优站位_连续圆弧_20261120/summary.json) · [面积证书](../可观测区域与最优站位_连续圆弧_20261120/area_check_continuous.json) · [历史区域图](../可观测区域与最优站位_连续圆弧_20261120/observable_region.png) |

从仓库根目录重绘：`nohup python3 -B -m src.shared.b_figures q1`，只写新建的 `runs/q1/figures_*`。基准版本、源码/底表/图件 SHA-256 见 [provenance.json](provenance.json)。与历史图的关系及其余题号见[图集索引](../../../docs/B题四问_图集索引.md#q1)，完整模型与验证见[审核报告](../../../docs/B题四问_图表与作答证据审核报告.md#q1)。
