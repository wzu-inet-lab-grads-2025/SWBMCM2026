# 第四问独立检查器：事件重算，不做调度

仅依赖 Python 3.10+ 标准库。**不导入任何策略、仿真或几何实现**，不生成调度策略、不推送。修改范围为 `experiments/b/q4_audit/`、`runs/b/q4_audit/`。

## 输入来源与固定口径

已读取共同约定 `experiments/b/q4/README.md`、英文原题第 1–5 页、验收清单及名义 `experiments/b/snapshots/q2_numerical/summary.json:q22.jobs`。PDF 不能直接显示，使用已有 `pypdf` 独立提取，文本保存在 `runs/b/q4_audit/original_problem.txt`（数学字体有缺字；正文规则可读）。运行检查器不需要 pypdf。

`data/scene.json` 与 `data/nominal_q22.json` 是独立冻结的输入副本；`data/sources.json` 记录来源 SHA-256。默认不依赖其他代理未来生成任何文件。默认上下文绝不从被检查记录的 `context` 字段自动读取；显式 `--context` 只用于人工边界测试，不能冒充正式 q22 审计。

- 两策略都必须匹配同一个名义 **[0,5) 物理前缀**，不只是互相相同。逐车比较阶段、目标、分段轨迹及声明的云台插值。5 s 同刻先故障，再考虑开始。
- 0 s 完整下发原计划，已下发后续任务可在断联中执行，但不得跳过原任务的观测/复位依赖。原指令的车、目标、路线和站位不能仅通过伪填 `command_effective_s=0` 改写。
- 命题人确认恢复时刻属于 `(5,30]`。包含 35 s 等域外压力测试的扩展清单，可在核验时使用 `--allow-stress-recovery`，或在对应外部情景显式设置 `recovery_scope: "sensitivity"`。[答疑依据](../q4_scenarios/CLARIFICATION.md)
- 5 s 前已经共同开始的任务锁定计分。故障车立即退出，健康搭档保留原 5 s 观测占用并复位。不能因观测结束在 5 s 后而撤销已有得分。
- `N` 从外部情景和三个原始告警重建；新增一定加一。**k 计同步开始，不计结束**，60 s 正好开始有效。若有告警未有效开始，`t_last_s=60`；否则取所有开始时刻最大值。
- 圆盘半径 1.5 m，建筑内侵入禁止、车体相切允许；有限三维视线触碰建筑即遮挡。3 m/s、两轴各不超过 30°/s、共同观测 5 s。水平角按短转角。无 Q2(1) 30 s 行驶上限，无 Q3 噪声。

## CLI

仓库根目录执行：

```bash
python3 -m unittest discover -s experiments/b/q4_audit -p 'test_*.py' -v
python3 experiments/b/q4_audit/examples.py
python3 experiments/b/q4_audit/audit.py experiments/b/q4_audit/samples/left.jsonl --right experiments/b/q4_audit/samples/right.jsonl --scenarios experiments/b/q4_audit/samples/scenarios.json --output runs/b/q4_audit/paired_report.json
python3 experiments/b/q4_audit/audit.py experiments/b/q4_audit/samples/single.json --single --scenarios experiments/b/q4_audit/samples/scenarios.json --output runs/b/q4_audit/single_report.json
```

换成双方实际 JSONL 路径即可。`--right` 可省略。**按外部 `scenario_id` 匹配，绝不按行位置**；样例右文件特意倒序。重复 ID、未知 ID、缺少一方、JSON 解析错误都报告为输入错误。退出码：0=pass，1=fail，2=incomplete。`--output` 省略时打印 JSON。

## 单记录与外部面板接口

```python
from experiments.b.q4_audit.audit import audit_record, audit_pair, audit_files

report = audit_record(record, external_scenario)
pair = audit_pair(left_record, right_record, external_scenario)
batch = audit_files("left.jsonl", external_scenarios, "right.jsonl")
```

接口不修改传入对象、不写文件。`external_scenario` 必须来自面板/共享情景清单，不能信任记录自己声称没有新增或故障。格式：

```json
{"id":"example", "new_alert":[90,40,50], "failed_vehicle":"V3", "recovery_s":12}
```

无某种扰动时明确填 `null`，缺字段即未核查。批量外部清单格式是 `{"scenarios":[...]}`。

### 结果记录与证据字段

共同 README 的必需字段保持不变：`scenario_id, policy, N, k, eta, t_last_s, jobs, events`。

- `jobs`：`{"T1":{"start_s":1.5,"vehicles":["V3","V5"]}, ...}`；未开始为 `null, []`。包含新增时的 T4。只把它当待对账断言，不当物理证据。
- `events`：每项含 `vehicle,target,kind,t0,t1`。`target` 非任务事件可为 `null`。支持 `drive/slew/wait/observe/reset/idle/fail`。逐车零长同刻事件按原列表顺序解释；正时长重叠违规。结束后的 idle/wait 应明确记录，健康车至少覆盖到 60，故障车覆盖到 5。允许开始在 60、结束/复位在窗外。
- `drive.path`：**实际走过**的 `[x,y]` 折线。中断时最后一点是中断位置，不是计划终点。由弧长和时间重算速度、在途位置、连续性、全路段扫掠净距。可将一条初始路线拆成多个事件，不等于收到新指令。
- 任务阶段 `station:[x,y]`：与重建位置交叉检查。缺失明确 `station_evidence_missing`；不能只用任意目标站位倒推已到达。
- 每个 `drive/slew/observe` 记录 `command_issued_s`、`command_effective_s`（继承触发该动作的指令时间）。0/0 表示原始已下发指令；新指令满足 `5 <= issued <= effective <= action.t0`，有断联则 `effective >= recovery_s`。可在断联时生成指令，但不能提前到车生效。记录时间本身不是实际网络证据，见下方审计边界。
- 初始云台正东水平；完整 slew/reset 从位置、目标和前一姿态独立重建旋转最短时间。可选 `gimbal0_deg/gimbal1_deg` 用 `[pan,tilt]` 交叉核对；可选行驶 `gimbal_deg` 必须为 `[0,0]`。
- **中断转向**必需实际 `gimbal1_deg`，再检查后续复位/行驶，缺失不能当作已指向或已复位。用 `interrupted:true` 标注；新独立轴协议也接受有效 `planned_end_s > t1` 或已知故障时刻退出作为截断证据，不强制重复布尔标记。
- `rotation_model:"linear_axes"`：保留旧协议，每个转动事件的两轴按端点同比例线性插值，允许较小转角轴降速、同时到终点，每轴不超过 30°/s。
- **`rotation_model:"independent_rate_limited_axes"`**：各轴独立以 30°/s 朝各自目标转动，到达后停住。每个 slew/reset 需 `gimbal_goal_deg:[pan,tilt]`，与独立几何目标/复位零角比对；从该事件 **t0 的实际姿态**（前段传播，若声明 `gimbal0_deg` 则交叉核对）按 `sign(delta)*min(abs(delta),30*(t-t0))` 重算每轴，水平 delta 折回短转角、俯仰不折回。`gimbal1_deg` 与重算 t1 对比，不能以截断端点当作目标再线性插值。若提供 `axis_speed_deg_s` 必须等于 30；`planned_end_s` 也由 t0 实际姿态独立复算。即使事件在中途拆段，续段必须从非零实际角度继续，不能重新从正东起算。
- 两协议均检查 [0,5) 云台前缀；独立轴协议将每个轴到达目标的折点纳入分段比较，不能仅比起止端点或两轴同比例插值。名义完整阶段耗时相同不表示两协议中途姿态相同。缺少协议声明为 incomplete，绝不根据日志猜测协议。
- `trace_complete_until_s:60`：声明没有遗漏截至 60 s 的事件。它不能代替事件覆盖；同时检查逐车空洞、末尾缺失和最后一次观测的复位。日志缺失时不能从“没有 observe 行”断言任务失败。

完整可直接使用的记录：`samples/single.json`。不同策略可按上述字段做适配，不应由检查器猜测未说明的字段别名。

## 输出：违规不等于证据不足

`status` 是 `pass/fail/incomplete`；**只有 `certified:true` 才有完整模型内证据**。有任何错误为 fail；无错误但缺证据为 incomplete。`errors` 和 `missing_evidence` 分开，包含稳定 `code`、原始 `event_index`、车辆/目标与细节。

- `event_score`：仅从原始两车同步 observe **开始**与外部 N 重算，便于找汇总算错；不是物理可行性证书。
- `recomputed`：有足够证据时的有效开始分数；若有不能确定的开始或缺失任务日志，则为 `null`。即使值存在，仍需查看 `status`：窗内开始后错误释放车辆、后来事件重叠等可使整体 fail，却不反向抹除已开始的锁定任务。
- `score_bounds`：日志证据下已验证/可能有效开始的保守计数范围，不是对不存在的真实调度作猜测。上游移动、指向、发令错误/缺失沿车辆证据链传播，不能被后面的 observe 行洗掉。
- `tasks`：逐目标的有效、未核查或无有效开始；保留原始 observe 行号。
- `checked_counts`：各类实测检查次数。不得将没有执行检查的一项说成已验证。
- `winner`：仅当双方均 certified 且物理前缀相同，才按 η 优先、同率 t_last 较小决定 left/right/tie；否则为 null。前缀另外报告 `prefix_status`。两端不同 rotation_model 即使都通过各自单记录检查，也会比较其名义中间角度；若不同则 `pair_errors:pair_prefix_rotation_model`，不能误报相同前缀。
- 距离、角度和同一事件的重算使用 `1e-6` 数值容差，按速度换算时相应缩放。判定任务是否在 `[0,60] s` 内共同开始、是否早于 5 s 故障及新命令是否在恢复后生效，均直接比较事件时刻。共同开始取两辆车观测开始时刻的较晚值。视线接触楼体按遮挡处理。

### 明确不能由有限事件日志证明的事项

每份报告的 `scope_exclusions` 均列出：策略内部是否偷看未来恢复时刻/故障身份（只能查可见前缀与命令时间）、遥测和完整性声明是否真实、未记录的连续角速度剖面、策略最优性、车辆互撞与第三问噪声。检查器不证明“保持策略”的所有决策规则与策略标签一致（没有靠标签猜测语义），只检验共同物理/命令/锁定规则及初始队列依赖。若需某个保留策略专门验收，需先给出明确策略合同。

## 合成正反例与测试

`samples/cases.jsonl` 每行包含 `id/context/scenario/record/expected_status/expected_codes/notes`，完全自包含、机器可读。其中小型边界例明确采用两车/一任务/无建筑的 **合成 context**；正式无扰动、新增、观测中故障、断联回放使用冻结 q22，不混同。`examples.py` 只生成手写见证，不包含选车/选站/优化。

41 个见证覆盖：

- 无扰动正例与把 `t_last` / `jobs.start_s` 错填结束时刻反例。
- 新增计分 `(k,N,eta,t_last)=(3,4,.75,60)` 与漏记 T4 分母反例。
- at-5 健康开始、at-5 故障先于开始、忽略同刻故障反例。
- at-60 成功、60.01 不计分、按结束时间漏计 60 开始反例。
- 观测中 V3 故障：T1 锁定、V5 保留占用；撤销得分、健康搭档过早释放、故障车复用反例。
- 在途 5 s 拼接正例、直接从计划终点出发的瞬移反例；在途故障实际截断与延迟退出反例。
- 断联期间执行初始队列、恢复时新指令生效正例；恢复前 0.01 s 生效反例。
- 发令证据缺失、路径缺失、完整性声明缺失、中断云台端点缺失均 incomplete，不能悄然通过。
- 同车充当两席、只重叠不同步、转向不足、事件重叠、前缀位置改变反例；转向中故障正例。
- 独立限速转轴的完整名义回放、V4 在 5 s 被截断、由 planned_end 识别截断、非零 t0 续转正例；把快轴错误按比例缩放、续段错误重置零角反例；缺少目标角或截断实际端点明确 incomplete。

`test_audit.py` 额外核验独立手算分数、旧路线拆段不误报、新路线伪称初始指令必报、两方共享错误前缀仍失败、输入不被修改、乱序配对、重复/未知/缺 ID、CLI 三种退出码、坏字段不崩溃，以及楼角精确圆盘距离、有限三维视线接触、折线拐角插值。

结果：`runs/b/q4_audit/synthetic_results.json`、`paired_report.json`、`single_report.json`、`test_results.txt`。这些是检查器自测及冻结计划回放。当前生产端只读联调另存 `runs/b/q4_audit/production_report.json`，不修改双方文件。

## 独立转轴协议联调记录

最初联调发现保持端遗漏 `trace_complete_until_s`。补齐后，审计器从两种策略的车辆事件逐项复核了截至 60 s 的完整时间轴，并核对了保持策略未能启动的 192 个任务实例。主清单 126 对、扩展清单 126 对全部通过。最终报告分别为 `runs/b/q4_comparison/paired_main_audit.json` 和 `runs/b/q4_comparison/paired_sensitivity_audit.json`。两份报告均记录了审计器校验值。[复跑入口与逐情景比较](../../../docs/q4-comparison.md)

V4/T2 从 `t0=0.9335681693539041`、姿态 `[0,0]` 转向 `[178.73579121051677,23.184672017939654]`；5 s 的实际姿态是 `[121.99295491938287,23.184672017939654]`。俯仰轴早已停住，水平轴仍以 30°/s 转动。将俯仰按整个阶段比例缩小会触发 `independent_axis_endpoint`，声明错误 linear_axes 则触发 `prefix_rotation`。
