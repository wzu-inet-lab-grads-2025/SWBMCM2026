# Q4｜扰动评分与代表时序（审阅图集，2026-11-21）

主评估使用整队列预授权和中心失联时的即时搭档就绪确认。七张新版图仅重绘 `recommended`、`original_append` 主结果；Q4 的两项断联假设对照在[独立归档](../断联授权与就绪确认对照_20261121/conclusions.md)，未并入此图集。60 s 罚值不等于开测；代表时序不能代替所有情景的逐车证据。

| 图件 | 冻结底表 |
| --- | --- |
| 策略总览 [PDF](q4_policy.pdf) · [PNG](q4_policy.png) | [七类汇总](../扰动重调度_四模型择优_20261120/category_summary.csv) |
| 恢复影响 [PDF](q4_recovery.pdf) · [PNG](q4_recovery.png) | [恢复汇总](../扰动重调度_四模型择优_20261120/recovery_summary.csv) |
| E7 分层 [PDF](q4_e7_factors.pdf) · [PNG](q4_e7_factors.png) | [故障](../扰动重调度_四模型择优_20261120/failure_summary.csv) · [位置](../扰动重调度_四模型择优_20261120/spatial_summary.csv) |
| T4 候选区 [PDF](q4_candidate_regions.pdf) · [PNG](q4_candidate_regions.png) | [情景输入及站对](../扰动重调度_四模型择优_20261120/scenario_inputs.jsonl.gz) · [逐场评分与节点数](../扰动重调度_四模型择优_20261120/scenario_metrics.csv) |
| 替补 `E2_V3` [PDF](q4_timeline_e2.pdf) · [PNG](q4_timeline_e2.png)；临窗 `E7_A90_R50_V2_C30` [PDF](q4_timeline_e7.pdf) · [PNG](q4_timeline_e7.png)；持平 `E1_A255_R50` [PDF](q4_timeline_e1.pdf) · [PNG](q4_timeline_e1.png) | [代表事实](../扰动重调度_四模型择优_20261120/representative_cases.csv) · [逐车相位及路点](../扰动重调度_四模型择优_20261120/policy_plans_and_tracks.jsonl.gz) · [逐车精确事件](../扰动重调度_四模型择优_20261120/policy_vehicle_timelines.csv.gz) |

从仓库根目录重绘：`nohup python3 -B -m src.shared.b_figures q4`，只写新建的 `runs/q4/figures_*`。沿用[主配置](../扰动重调度_四模型择优_20261120/config.yaml)，不重新生成站对或调度。基准版本、源码/底表/图件 SHA-256 见 [provenance.json](provenance.json)。[图集索引与历史图](../../../docs/B题四问_图集索引.md#q4) · [审核报告与逐场查询](../../../docs/B题四问_图表与作答证据审核报告.md#q4)
