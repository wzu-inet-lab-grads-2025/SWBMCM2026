# Q3｜80 米参数域与误差贡献（审阅图集，2026-11-21）

软件指令按题面定义，实际指向额外计入云台误差。新版区分 80 m 参数域的三个区域，明确误差贡献图里的 **66.837 m** 是实际指向临界，并分开方差占比与消融概率增益。未重新积分或求根；原[可靠性曲线](../测向可靠性与80米定位约束_五方法对照_20261120/reliability.pdf)和[方法验证图](../测向可靠性与80米定位约束_五方法对照_20261120/method_validation.pdf)继续使用。

| 图件 | 冻结底表与历史对照 |
| --- | --- |
| 参数域 [PDF](q3_parameters_80m.pdf) · [PNG](q3_parameters_80m.png) | [边界点](../测向可靠性与80米定位约束_五方法对照_20261120/ab_boundary.csv) · [两口径阈值](../测向可靠性与80米定位约束_五方法对照_20261120/limits.csv) · [历史参数域](../测向可靠性与80米定位约束_五方法对照_20261120/ab_feasible_region.pdf) |
| 误差贡献 [PDF](q3_error_sources.pdf) · [PNG](q3_error_sources.png) | [占比与消融](../测向可靠性与80米定位约束_五方法对照_20261120/error_contributions.csv) · [历史贡献图](../测向可靠性与80米定位约束_五方法对照_20261120/error_contributions.pdf) |

从仓库根目录重绘：`nohup python3 -B -m src.shared.b_figures q3`，只写新建的 `runs/q3/figures_*`。计算采用[原配置](../测向可靠性与80米定位约束_五方法对照_20261120/config.yaml)；基准版本、源码/底表/图件 SHA-256 见 [provenance.json](provenance.json)。[图集索引](../../../docs/B题四问_图集索引.md#q3) · [审核报告](../../../docs/B题四问_图表与作答证据审核报告.md#q3)
