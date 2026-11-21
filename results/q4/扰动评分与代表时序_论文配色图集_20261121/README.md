# Q4｜扰动评分与代表时序（论文配色版，2026-11-21）

对照图用蓝实点与灰空心圈区分推荐和仅追加；时序只突出关键观测和替补路段，E7 用浅赭带标 5–30 s 断联、灰带标 60 s 后的实际观测及复位。E1 两策持平，T4 用同样的强调色。主汇总仍基于整队列预授权、断联中可即时确认搭档就绪，且只计 $S<60$ s；60 s 罚值不是开测。两项[断联口径对照](../断联授权与就绪确认对照_20261121/conclusions.md)仍是补充，不计入七张图。

| 配色版 | 首版对照 | 冻结底表 |
| --- | --- | --- |
| 策略总览 [PDF](q4_policy.pdf) · [PNG](q4_policy.png) | [PDF](../扰动评分与代表时序_审阅图集_20261121/q4_policy.pdf) · [PNG](../扰动评分与代表时序_审阅图集_20261121/q4_policy.png) | [七类汇总](../扰动重调度_四模型择优_20261120/category_summary.csv) |
| 恢复时刻 [PDF](q4_recovery.pdf) · [PNG](q4_recovery.png) | [PDF](../扰动评分与代表时序_审阅图集_20261121/q4_recovery.pdf) · [PNG](../扰动评分与代表时序_审阅图集_20261121/q4_recovery.png) | [恢复汇总](../扰动重调度_四模型择优_20261120/recovery_summary.csv) |
| E7 分层 [PDF](q4_e7_factors.pdf) · [PNG](q4_e7_factors.png) | [PDF](../扰动评分与代表时序_审阅图集_20261121/q4_e7_factors.pdf) · [PNG](../扰动评分与代表时序_审阅图集_20261121/q4_e7_factors.png) | [按故障车](../扰动重调度_四模型择优_20261120/failure_summary.csv) · [按 T4 位置](../扰动重调度_四模型择优_20261120/spatial_summary.csv) |
| T4 候选几何 [PDF](q4_candidate_regions.pdf) · [PNG](q4_candidate_regions.png) | [PDF](../扰动评分与代表时序_审阅图集_20261121/q4_candidate_regions.pdf) · [PNG](../扰动评分与代表时序_审阅图集_20261121/q4_candidate_regions.png) | [情景输入与站对](../扰动重调度_四模型择优_20261120/scenario_inputs.jsonl.gz) · [评分与 1 m 网格节点数](../扰动重调度_四模型择优_20261120/scenario_metrics.csv) |
| 替补 `E2_V3` [PDF](q4_timeline_e2.pdf) · [PNG](q4_timeline_e2.png) | [PDF](../扰动评分与代表时序_审阅图集_20261121/q4_timeline_e2.pdf) · [PNG](../扰动评分与代表时序_审阅图集_20261121/q4_timeline_e2.png) | 三个时序共用：[代表情景](../扰动重调度_四模型择优_20261120/representative_cases.csv) · [五车相位与路点](../扰动重调度_四模型择优_20261120/policy_plans_and_tracks.jsonl.gz) · [逐车事件](../扰动重调度_四模型择优_20261120/policy_vehicle_timelines.csv.gz) · [逐场评分](../扰动重调度_四模型择优_20261120/scenario_metrics.csv) |
| 临窗 `E7_A90_R50_V2_C30` [PDF](q4_timeline_e7.pdf) · [PNG](q4_timeline_e7.png) | [PDF](../扰动评分与代表时序_审阅图集_20261121/q4_timeline_e7.pdf) · [PNG](../扰动评分与代表时序_审阅图集_20261121/q4_timeline_e7.png) | 同上 |
| 持平 `E1_A255_R50` [PDF](q4_timeline_e1.pdf) · [PNG](q4_timeline_e1.png) | [PDF](../扰动评分与代表时序_审阅图集_20261121/q4_timeline_e1.pdf) · [PNG](../扰动评分与代表时序_审阅图集_20261121/q4_timeline_e1.png) | 同上 |

从仓库根目录运行 `nohup python3 -B -m src.shared.b_figures q4`，只写新建的 `runs/q4/figures_*`。读取[主归档](../扰动重调度_四模型择优_20261120/conclusions.md)和 [Q2 场地快照](../../q2/双站定位与并发调度_连续站位_20261120/scene_config.yaml)，只为图重建 0.5 m 显示掩码，不重建候选目录或调度。精确计分仍以逐场底表为准。[源码、底表及图件 SHA-256](provenance.json) · [首版图集](../扰动评分与代表时序_审阅图集_20261121/README.md) · [图集索引](../../../docs/B题四问_图集索引.md#q4) · [审核报告](../../../docs/B题四问_图表与作答证据审核报告.md#q4)
