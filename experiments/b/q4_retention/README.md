# 第四问：保留原计划与独立逐车回放

仅实现保持策略，不导入重调度或其他第四问代理的代码。输入为共同口径指定的 `experiments/b/snapshots/q2_numerical/summary.json:q22.jobs` 及 `experiments/b/scene.json`；Python 3.10+，运行仅需标准库。

## 立即使用

从仓库根目录执行（`-B` 避免写入字节码）：

```bash
# 全套情景、手算对照、复跑及几何/时间独立核查
python3 -B experiments/b/q4_retention/verify.py

# 共同 JSON 合同 -> 每行一份结果
python3 -B experiments/b/q4_retention/retention.py experiments/b/q4_retention/scenarios.json --output runs/b/q4_retention/cli_results.jsonl

# 接收单个情景的标准输入；也支持列表、{"scenarios":[...]}、JSONL
python3 -B experiments/b/q4_retention/retention.py - <<'JSON'
{"id":"new_fail_outage","new_alert":[150,50,50],"failed_vehicle":"V2","recovery_s":20}
JSON

# 独立检查已有结果，不调用模拟器
python3 -B experiments/b/q4_retention/audit.py runs/b/q4_retention/preloaded.jsonl --output runs/b/q4_retention/independent_preloaded_audit.json

# 仅当前任务下发：独立敏感性，不能混入主策略结果
python3 -B experiments/b/q4_retention/retention.py experiments/b/q4_retention/scenarios.json --dispatch-mode current_only --output runs/b/q4_retention/cli_current_only.jsonl
```

Python API（仓库根目录可作 namespace package 导入）：

```python
from experiments.b.q4_retention.retention import run, state_at, interpolate_path

result = run({"id": "example", "new_alert": None,
              "failed_vehicle": "V2", "recovery_s": 20})
assert result["k"] == 2
before = result["state_5s_before"]  # 5-minus，全车状态不受未来扰动影响
after = result["state_5s_after"]    # 5-plus，含故障退出/断联
v2_at_4s = state_at(result, 4.0)["V2"]
position, travelled_prefix = interpolate_path([[0,0],[3,0],[3,4]], 5)
assert position == [3.0, 2.0]
```

`run(scenario, *, dispatch_mode='preloaded') -> result`：默认字段为 `null`，不修改输入。`new_alert` 必须满足 θ∈[0,360)、R∈[30,50]、H=50；故障限一个 V1…V5。恢复时刻非空必须为有限且 >5 的绝对时刻；[10,30] 之外接受但标为 `recovery_scope='range_sensitivity'`，不冒充主情景。JSON 中拒绝 NaN/Infinity。`run` 不缓存场景，不使用搜索器、随机权重或未来恢复时间作派令决策。

## 结果字段与语义

- 共同字段：`scenario_id, policy, N, k, eta, t_last_s, jobs, events`。未开始任务的 `start_s=null, vehicles=[]`；有新增则 T4 永远没有原计划席位，仍计入 N。
- `jobs` 另含 `planned_vehicles, stations, locked_at_5s, observation_end_s, reason`。已锁定是得分口径，**不是车辆已释放**。
- `events` 每行含 `vehicle,target,kind,t0,t1`；种类为 drive/slew/wait/observe/reset/idle/fail。drive 的 `path` 是实际走过的折线，`planned_path` 是未截断的原路线，`distance_m` 为实际路程。其他作业带 `station`，每项任务事件带 `command_issued_s, command_effective_s`。
- `commands` 为实际下发记录。预下发包含六个原席位、时刻全为 0；`current_only` 的 V3→T3 在可用且联通时下发。`system_events` 单列断联、新增与恢复，不伪装逐车运动。
- `state_5s_before/after` 与 `state_at` 给全车位置、两轴角度（度）、占用、当前任务、已锁定观测占用、是否允许改派、可达指令、按当前姿态复位所需时间、观测剩余量、原任务队列和观测后复位释放时刻。
- `reassignable=true` 表示允许改任务，不表示可以绕过云台复位立即开车；`command_reachable=false` 时也不能立即接受新指令。`locked_occupancy` 是该次原观测在 5 s 锁定而车辆仍占用的描述标签；它并非在 0 s 知道未来故障。
- 故障车 `active=false,position_xy_m=null` 表示退出、不阻路；`last_position_xy_m` 保留故障前真实位置用于核查。故障后没有 idle/drive 等伪活动。
- 时间为浮点秒；事件采用右连续状态，`side='left'` 可取左极限。连续折线路段按弧长插值；云台每轴按 30°/s 独立尽快转动，短轴先到后等待；行驶时始终 (0°,0°)。
- 计分窗 [0,60]，60 恰好开始成功；未全部开始则 `t_last_s=60`。恰好 5 s 优先故障再尝试共同开始，只有严格早于 5 的观测锁定。已成功开始的观测/复位可记录到 60 之后，`timeline_end_s` 给日志末端。
- 原计划固定车对、固定站、固定每车顺序，按物理就绪最早执行，不强加失联前后的绝对开始时刻。原伙伴故障使未开始任务保持等待，不跳过依赖、不挪用健康伙伴。

## 交付索引

- [REPORT.md](REPORT.md)：题面口径、推导、手算、分身份结果、断联敏感性、限制。
- `retention.py`：事件驱动回放、状态采样、CLI。
- `audit.py`：不导入回放器，独立核对原计划/路径、连续扫掠圆盘、三维闭线段遮挡、时间与评分。
- `verify.py`：120 情景 × 2 协议、8 份超范围恢复敏感性、100 个确定种子属性样本、精确边界/依赖/折线反例和检查器变异测试；生成全部下列精简证据。
- `scenarios.json`：共同合同的 120 行全因子设计，无扰动与七类扰动组合俱全。
- `evidence.json`：检查数量、五故障手算答案、边界实验、输入/代码/详细结果 SHA-256。
- `state_5s.json`：全车 5 s 状态及五种故障后的状态。
- `new_alert_geometry.json`：四种新增位置的投影、初始车位仰角与逐楼遮挡。
- `runs/b/q4_retention/preloaded.jsonl`、`current_only.jsonl`：各 120 份完整结果/逐车事件。
- `runs/b/q4_retention/summary.csv`、`nominal.json`、`range_sensitivity.jsonl`、`audit.json`：数表、无扰动详例、超范围恢复结果、逐情景核查。

只写 `experiments/b/q4_retention/` 与 `runs/b/q4_retention/`；不提交、不推送。所有数值按原始双精度使用，不从报告的舍入坐标重建路径。
