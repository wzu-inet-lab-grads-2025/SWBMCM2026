# B 题第二问｜敏感性复跑记录

本地原始输出：`runs/q2/20261122T071053Z_sensitivity_8t9ixc9p/`。运行时 Git HEAD 为 `1a4374e0c28d55944a8dc69cd813075de6113486`，本地新入口与实际输入的 SHA-256 见[机器记录](run_record.json)。AI 辅助使用 OpenCode / Magpie `group/gpt-6.2-sol`，用于试验实现、计算和说明；正式材料由队员复核。

从仓库根目录运行，输出写到新建的 `runs/q2/` 目录，不覆盖历史归档。环境见 [B 题依赖](../../../configs/requirements-b.txt)。

```bash
nohup env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MPLBACKEND=Agg python3 -B -m src.q2.sensitivity \
  --config results/q2/毫米部署与初排敏感性_20261122/config.yaml
```

- **输入。** 读取[毫米方案归档](../毫米部署与初排_全程净空四十五厘米_20261121/run_record.md)的场地、题值、全精度与毫米双站路点，以及 `practical` 和 `greedy` 的排程路点与完整标称时间轴。扫描档位见[参数快照](config.yaml)。非标称因素仅用于本次试验。
- **结果。** [2（1）底表](q21_noise_sensitivity.csv)含 2 套站位 × 5 档噪声标准差；[2（2）底表](q22_sensitivity.csv)含 125 个 `timing` 联合时序工况及 4 个 `t3_height` 非零高度偏差工况。高度偏差为零的题值复用 `timing` 中三项倍率均为 1 的行。`greedy_minus_practical_s` 为就近基线减毫米方案的最晚开测差，正值表示毫米方案更快；任一固定方案不可行时只记录原因，不填该方案成绩和差值。
- **复核。** [独立验收器](../../../checks/q2_validate.py)核查双站闭楼体视线、闭仰角、全程净空、同步观测、观测结束后复位和连续车距。2（1）另核 30 s 行驶时限。两套计划的标称回放与原时间轴一致。另以归档站位角度、路点长度和 V3 的两条复用链直算，逐行核对了 125 组的三个开测时刻和最晚开测差；并核对 10 条一阶噪声结果及 4 条高度工况的可行性和有效开测时间。
- **回归。** Q1、Q2 的既有检查 **55 项通过**（`checks/test_q1_*.py` 和 `checks/test_q2_*.py`）。本轮参考 `tdd` 的公开接口验证口径，使用既有回归与独立底表复算，未新增测试接口。

[主要结论与图件](conclusions.md)
