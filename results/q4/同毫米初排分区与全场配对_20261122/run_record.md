# 运行与核验记录｜第四问同初排配对

- 原始运行位于 `runs/q4/同毫米初排_分区与全场_20261122_复核/`。本目录从该运行复制逐场表、场景清单、毫米初排与核验记录，并用底表核对第 5.4 节的结果。
- 原运行使用 Python 3.13.9，主工作树基线为 `1a4374e0c28d55944a8dc69cd813075de6113486`，分区搜索来自 `explore/b-first-round` 的 `30d54fa397c370742cd34e5cd79808ebdb59ae46` 快照。机读记录包括原执行命令和输入及适配器 SHA-256。当前适配器已增加可指定归档路径的选项，复跑命令采用该选项。[原运行记录](verification.json)
- 场景清单记录共同初排指纹。分区与全场方法共用 0.45 m 全程车体净空、3 m 最小车心距、距共线至少 5° 的筛选及严格早于 60 s 的开测计分。缺少任何告警的开测时，`t_last` 记为 60 s。连续 5 秒实采单独统计，逐场同分比较使用 \(10^{-8}\) s 时间容差。[共同初排](nominal_q2.json) · [场景清单](scenario_manifest.csv)
- 导出核验覆盖五套策略的 15,325 条场景与策略记录，核对分数、调度决定、连续时间轴、锁定告警、断联恢复和故障截断。逐车时间轴、逐告警和调度决定明细可在本地原运行及 `submission/q4_matched_mm_supplement_s4_20261122.zip` 查看。[导出核验记录](export_crosscheck.json)

从仓库根目录运行。`peer` 指向分区搜索的上述快照，`--output` 指向 `runs/q4/` 下的新目录。

```bash
peer=../SWBMCM2026-worktrees/explore-b-first-round
fw=results/q4/毫米初排与联合断联限制_全程净空四十五厘米_20261121
nohup env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 -B -m checks.q4_paired_panel \
  --peer "$peer" --field-run "$fw" --output runs/q4/paired_mm_replay --workers 8
```

复跑后用 `(scenario_id, protocol)` 匹配归档记录，按相同断联场景 ID 计算两种规则各自的汇总值。FW 归档的原配置与复跑命令记录在相邻归档中。[FW 运行记录](../毫米初排与联合断联限制_全程净空四十五厘米_20261121/run_record.md)

适配、导出复核及文字整理使用了 OpenCode / Magpie `group/gpt-6.2-sol` 辅助。队伍应按本届规则核验并据实披露。[赛事 AI 使用规定](https://www.nmmcm.org.cn/News/220.html)
