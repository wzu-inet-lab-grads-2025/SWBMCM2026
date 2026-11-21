# B 题模型与求解结果

本次发布从求解代码提交 `0188fda1f926f248b8a7ed113376b654f613f0af` 的干净检出生成结果。坐标与距离单位为米，时间为秒。表内数字用于阅读，回放应使用 JSON 中的完整精度。场景原始参数、正式运行配置及逐问验收证据均可随仓库下载。[场景参数](../experiments/b/scene.json) · [正式配置](../configs/b/) · [验收清单](../docs/b-acceptance.md)

| 问题 | 模型与实现 | 已核验的结果 | 图表及机器可读结果 |
| --- | --- | --- | --- |
| 一 | [几何可行域、最优圆弧与择优证明](../experiments/b/q1_independent/REPORT.md) | 最小水平距离 7.582060 m，仰角 80°；按行驶距离择优，选 V4 | [区域图](../experiments/b/snapshots/q1/observable_region.png) · [连续最优性证书与边界检查](q1/result.json) |
| 二（1） | [双站精度模型](../docs/q2-independent.md) | 两站各距目标投影 5.818790 m，交会角 90°，最优一阶水平 RMSE 为 0.143623358 m | [站位、车辆与路径](../experiments/b/snapshots/q2_numerical/summary.json) · [两路线独立核验](../experiments/b/snapshots/q2_comparison/both.json) |
| 二（2） | [数值求解](../experiments/b/q2_numerical/REPORT.md) · [解析与下界](../experiments/b/q2_analytic/README.md) | 最晚观测开始的连续数值区间为 [10.352051000, 10.352118591] s | [数值方案和逐车事件](q2/q22-numerical.json) · [解析方案回放](q2/q22-analytic.json) · [30 套基线](q2/q22-baselines-comparison.json) |
| 三 | [两种误差定义与交叉验证](../docs/q3-independent.md) | 95% 限程分别为指令偏差 73.936066 m、含三项误差的实际指向偏差 66.837258 m | [80 m 参数域与阈值](q3/compact.json) · [积分及精度](../experiments/b/q3_quadrature/results.json) · [仿真计数与区间](../experiments/b/q3_simulation/results.json) |
| 四 | [保持与在线重调度的配对分析](../docs/q4-comparison.md) | 126 个主情景中，120 个提高完成率、6 个持平；35 个情景有原告警推迟 | [逐情景得分 CSV](../experiments/b/snapshots/q4_comparison/main.csv) · [扩展得分 CSV](../experiments/b/snapshots/q4_comparison/sensitivity.csv) · [完整逐车日志](q4/) |

二（2）的站位坐标精度、实车净空、断联执行协议及第四问双车采集时长见[约束复核报告](../docs/b-constraint-review.md)。两份独立解在同一份初始排程上的 1,751 个扰动情景逐项回放见[交叉验证记录](../docs/b-peer-crosscheck.md)。

## 第四问日志

每份 `.jsonl.gz` 解压后有 126 行，每行对应一个情景，含原始命令、逐车事件、完成数及最晚观测开始时刻。主比较从二（2）的数值计划开始，采用 0 s 完整下发的协议。扩展中 35 s 恢复属于域外压力测试。替换初始计划的实验单独归档。主面板 CSV 中的计数对应给定设计的情景。[主情景清单](../experiments/b/q4_scenarios/manifest.json) · [扩展清单](../experiments/b/q4_scenarios/sensitivity.json)

| 初始计划与清单 | 保持原计划 | 在线重调度 |
| --- | --- | --- |
| 数值方案，主清单 | [保持日志](q4/q4-retain-q22-numerical-manifest.jsonl.gz) | [改派日志](q4/q4-reschedule-q22-numerical-manifest.jsonl.gz) |
| 数值方案，扩展清单 | [保持日志](q4/q4-retain-q22-numerical-sensitivity.jsonl.gz) | [改派日志](q4/q4-reschedule-q22-numerical-sensitivity.jsonl.gz) |
| 最早就绪基线，主清单 | [保持日志](q4/q4-retain-ready.jsonl.gz) | [改派日志](q4/q4-reschedule-ready.jsonl.gz) |

另附[最小新增路程基线的保持日志](q4/q4-retain-sum.jsonl.gz)。在仓库根目录可直接从压缩文件提取一份计划的所有情景得分：

```bash
python3 - <<'PY'
import gzip, json
with gzip.open('results/q4/q4-reschedule-q22-numerical-manifest.jsonl.gz', 'rt') as stream:
    for line in stream:
        row = json.loads(line)
        print(row['scenario_id'], row['k'], row['N'], row['t_last_s'])
PY
```

## 复现与校验

正式回放使用 Python 3.10+ 标准库。第一、二、三问的科学计算依赖与种子见各问实验说明。第三问随机仿真使用种子 `2026112003`，400 万组样本估根，另取 400 万组独立验证。第一问本次用 `--no-plot` 复核数值证书；上表的图为此前已验收并归档的矢量与栅格绘图成果。[第一问环境与绘图命令](../experiments/b/q1_independent/README.md) · [第二问求解命令](../experiments/b/q2_numerical/README.md) · [第三问仿真命令](../experiments/b/q3_simulation/README.md)

在仓库根目录复跑正式基线和名义计划、核对第三问阈值，并验证第四问主清单与扩展清单：

```bash
python3 -m unittest discover -s checks
python3 src/run.py compare-baselines
python3 src/run.py replay
python3 checks/check_q22_nominal.py
python3 experiments/b/check_q3_thresholds.py
python3 src/run.py retain
python3 src/run.py reschedule
python3 checks/check_q4_pair.py
python3 src/run.py retain --manifest experiments/b/q4_scenarios/sensitivity.json --allow-stress-recovery
python3 src/run.py reschedule --manifest experiments/b/q4_scenarios/sensitivity.json --allow-stress-recovery
python3 checks/check_q4_pair.py --panel sensitivity
```

命令将新结果写入 `runs/b/`。归档文件的 SHA-256 见 [SHA256SUMS](SHA256SUMS)。在 `results/` 下运行 `sha256sum -c SHA256SUMS` 可校验本目录的 19 份结果文件。
