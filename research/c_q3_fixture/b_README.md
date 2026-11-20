# B：独立盲验固定动作合成台

**正式回放未运行。** 这不是策略、官方回放器、真实数据验证或正式 S1 结果。只借用 S1 的 H=100、B=32、K=4、初始 Z0；两次全新状态的独立试验，每次只有一个合成 J（Z1、权重3、无前置）。没有读取 A/其他 agent 文件、手算或预期，没有读取 `*hand_expected*`，没有联网或修改原始数据。

## 复现

仓库根目录运行：

```sh
python3 research/c_q3_fixture/b_fixture.py
```

运行器仅用 Python 标准库，所有输出固定写入 `research/c_q3_fixture/b_output/`，无随机数。已实际运行，**62 条断言全通过**；再次运行的8个输出文件逐字节一致。终端输出保存为 `b_output/b_run_output.txt`。首次运行修正了官方 CSV 的 UTF-8 BOM 读取方式；原 CSV 未改。

- `b_inputs.json`：公开作业条件、两组固定动作；不含 d、p 或源标识。
- `b_truth_spec.json`：明确隔离的离线真值及数值行生成配方，不是决策输入。
- `b_fixture.py`：只实现本次固定动作台及独立常量断言，不是通用非法动作回放器；断言失效直接退出，不伪造正式违例试验结果。
- `b_output/b_policy_views.jsonl`：每事件完整决策输入副本，含实际可见数值行。
- `b_output/b_events.jsonl`：逐事件日志及数值见证，按 `policy_view_id` 回溯决策输入。
- `b_output/b_offline_truth.json`：完整合成行、原长 d、名义 p、源端点；只供离线检查，绝不合并入决策视图。
- `b_output/b_retrospective_sources.jsonl`：EARLY 完成时 t=5、CROSS 终止时 t=100 才形成的执行源回溯；不传给决策视图。
- `b_output/b_summary.json`、`b_assertions.json`：全部共同指标、逐条断言。
- `b_output/b_sources.json`：所用官方文件和本实现 SHA-256、PDF 页码/JSON 指针、规则引用表。

## 观察结果

- **EARLY**：释放/复核请求 t=0，中点 0.5，返回 1；立即 EXECUTE，b=5，路程1、切换0.5，服务起点2.5。离线 d=2、p=2.5，故 C=5，早于 due=6；设备保留占用至7.5，之后才 STOP。费用3.7，剩余预算28.3，复核1次；完成率/按时率均1，加权迟延0，观测超时0，预约空闲2.5，截尾0。
- **CROSS**：WAIT 94 后释放/复核，中点94.5，返回95；立即 EXECUTE，b=3，服务起点96.5，预约终点99.5，合法接受。离线 d=6、p=6.5、潜在 C=103 **不是观测到的完成时刻**。H=100 自动终止，仅观测3.5秒服务；观测超时0.5，非3.5。费用3.5，剩余预算28.5，复核1次；完成率/按时率均0，加权迟延3×(100−98)=6，预约空闲0，截尾1。没有 COMPLETE、设备再次可用或 STOP 事件。
- 每次仅3个提交动作；请求中点快照不是动作、不加延迟。EARLY 执行请求可观测占用中点4.25、预约空闲中点6.25另有只读快照；CROSS 执行请求可观测占用中点97.5另有只读快照。中点由离线观测器安排，不是自适应决策。

## 信息隔离和数值见证

每个数值行包含原时间戳、左右手各21点的位置/四元数、头姿态、左右头相机姿态、原始合成手质量标记；所有数据显式为合成，坐标无物理意义。不插值、不补齐、不清洗质量标记。

- EARLY 源时间从1000起：0.2秒前缀左手第0点 x 为 **11000,11100,11200**；复核返回后才增加 **11300,11400,11500,11600**。
- CROSS 源时间从2000起，对应 **22000,22100,22200**，返回后增加 **22300,22400,22500,22600**。
- 离线另有0.7、1.0、1.1秒样本，整个试验都不进入决策输入。请求时与请求中点仍仅3行，返回后7行；执行、等待或快照均不扩前缀。
- 决策视图排除源标识、语义、端点、d、p、种子及未来行。完成之前不含完成时间；CROSS 终止后也不虚构103为观测完成。
- `probe()` 对读前/读后状态哈希做相等断言，并修改返回副本检查不能反向修改设备状态；独立断言检查逐事件完整原行、字段、质量标记、时间和行动计数。
- 固定动作列表没有模型或查源代码。离线引擎持有真值生成事件；只有 `policy_view()` 的副本代表允许提供的输入。这是代码边界验证，不宣称进程级安全隔离。

## 自定实现记账口径（明确区别于题目规定）

1. **窗口边界**：采用闭区间 `[annotation_start, annotation_start+prefix]`；恰好0.2/0.6秒的既存行包含在内。官方说明未明确端点开闭，这是显式约定，不插值制造边界样本。
2. **复核承诺**：接受时立即扣1费用、占用1次复核额度、设备忙；`reviews_returned` 与前缀只在1秒后返回时更新。官方明确复核费用/延迟但未指定逐事件扣费字段时点；这里固定为接受即记账。执行接受时立即扣全费、冻结 b 和单次尝试，后续不退款（执行即时扣费为官方规定）。
3. **区域**：旅行中仍记出发区，抵达时变为 Z1，随后0.5秒切换；没有把区域坐标解释成商品位置。官方定义运动耗时但未规定中间区域日志表示。
4. **中间指标**：`metrics_if_ended_now` 是截至当前事件的评估投影，不是决策输入。未完成暂以 H 算迟延；因此完成时该值可以下降。`observed_overrun_s` 只积累已观测服务、且不越 H。
5. **预约空闲**：共同指标 `reserved_idle_s` 在完成事件确认后一次登记完整 `max(b−p,0)`；另设 `reserved_idle_elapsed_s` 记录实际走过的空闲。EARLY 在 C=5 时分别2.5和0，在6.25时分别2.5和1.25，在7.5时均2.5。这不代表提前释放设备。
6. **截尾设备状态**：H 终止时 `occupied=true` 表示截至 H 未观测设备变为可用；`trial_ended=true` 且不可继续提交，不外推 H 后事件。观测完成字段为 null，潜在 C 仅在离线回溯文件。
7. **同刻排序**：WAIT_RETURN 后处理 RELEASE；复核返回后在同刻接受执行；设备可用后在同刻 STOP。快照时间推进只是沿既有请求时间轴的观测点，不额外延长请求。

## 日志字段

- `event_id / trial_id / event / t_s`：事件主键、独立试验、事件类型、场景秒；相同行为的起止分别成事件。
- `zone / cost / budget_remaining`：当前区域、累计已承诺费用、B减费用。
- `reviews_committed / reviews_returned / reviews_remaining`：已用/已返回/剩余额度；`action_count` 只数 WAIT、REVIEW、EXECUTE、STOP。
- `device.phase / occupied / may_submit_action`：设备阶段、是否占用、能否新提交；`committed_service_end_s` 是已承诺的 s+b，**不是已知实际完成/可用时间**。
- `policy_view_id / visible_prefix_s / visible_timestamps_s / witness_left_keypoint0_x`：关联完整视图、累计源秒窗口、现有时间戳、醒目数值证据。窗口是源秒，不消耗服务秒。
- `detail.action / admission_inputs / return_at_s / commitment`：请求参数、接受前仅依公开条件的合法性检查、已知返回时刻、承诺说明。执行合法性只看 s+b，不看未揭示 p。
- `metrics_if_ended_now / reserved_idle_elapsed_s / trial_ended`：上述中间评估、已实际经历空闲、终止状态；最终共同指标以 summary 为准。
- `rule_refs`：连接 `b_sources.json` 的官方 PDF 页码和 JSON 指针。
- 决策视图 `pending_review`：公开请求及目标窗口，不是已返回信息；`feedback`：已尝试、完成反馈/完成时刻、在 H 截尾。
- 回溯 `source_ref / offline_truth_pointer / disclosed_at_s`：只读离线源引用及可披露时点；`observed_service_s` 与带 `_offline` 的潜在服务/完成值明确分开。

## 官方依据与范围

已用本地已安装 `pypdf` 阅读两份官方题目/要求 PDF；读取官方数据说明 PDF、`common_conditions.json` 及 `service_zones.csv`。直接 PDF 接口不支持，`pdftotext` 未安装；没有安装依赖或联网。

核心依据为 `data/raw/C/Restocking_Tasks_and_Constraints/Problem_3_Requirements.pdf`：p1 信息限制、p2 时序/承诺/截止规则、pp2–3 全部共同指标、p3 日志要求。官方题目 PDF pp3–6 相互核对。只检查官方 `problem/C`、`data/raw/C` 的代码候选路径和官方 C 数据压缩包成员名，未发现回放器；**没有搜索 research 下其他 agent 的实现，不能断言整个仓库不存在回放器**。

本次仅验证给定两条合成轨迹：不抽取官方8个S1任务、不运行30种子、不训练或构造策略、不做真实原始轨迹回放、不声称物理机器人性能。
