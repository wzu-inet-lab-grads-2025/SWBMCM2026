# B 题第二问：怎样复核已有方案

本工具验收给定方案的站位、路线和时间轴。运行依赖与第一问共用（Python、PyYAML）。日常复跑的验收输出留在 `runs/q2/`。AI 辅助范围为题面核对及复核代码和测试。

第二问已归档的 T2 站位、优化与就近方案、连续域下界可按运行记录中的命令复核。 [归档复核命令](../results/q2/双站定位与并发调度_连续站位_20261120/run_record.md)

## 2（1）：复算规格已有的 T2 样例

```bash
nohup python3 -B -m checks.q2_validate \
  --q21 checks/q21_reference_from_spec.json \
  --output runs/q2/规格参考站验收_20261120/q21_validation.json
```

输入 JSON 含 `target: "T2"` 和 `routes`。每条路线含 `vehicle`、`waypoints`，首路点为原题初始位置，最后一个为观测站。报告复算单车视线、仰角、最小楼体距离、行驶时限、交会角和一阶二维 RMSE。样例使用原方案 §2.3 的六位小数坐标，只说明这组站点可行，不把它认定为最优。仰角按原题闭区间判定；正好在边界的解析站位应保留解析表达式，另行复核用于展示或执行的舍入坐标。

## 2（2）：对队员生成的优化与就近方案分别验收

```bash
nohup python3 -B -m checks.q2_validate \
  --q22-routes runs/q2/待验收/q22_routes.json \
  --q22-timeline runs/q2/待验收/q22_timeline.csv \
  --compare greedy optimized \
  --output runs/q2/待验收/validation.json
```

- **路线 JSON**：用数组，或 `{"routes": [...]}`。每项必填 `vehicle, target, previous_target, waypoints`；首任务的 `previous_target` 为 `null`。如有 `origin, destination, length_m, travel_s`，也会复核。同一告警必须有两条不同车辆的路线。
- **时间轴 CSV**：每项按车次填写 `vehicle,target,depart_s,arrive_s,turn_begin_s,turn_end_s,wait_s,observation_start_s,observation_end_s,reset_begin_s,reset_end_s,next_departure_s`。没有下一任务则末栏留空；从未分配任务的车辆用 `target=idle`（或 `target` 留空且 `status=idle`）单独记录，时间栏全空。
- **多个方案**：两个文件均用相同的 `plan` 名分组；单方案可以同时省略。报告分别复算各告警启动与最晚启动、最后复位结束、各车顺序、双站指标。`--compare` 后写两个实际 `plan` 名（先基线后候选），可另输出最晚启动差和每条告警中最迟就绪的车辆。不比较时省略该选项。时间表默认用 `1e-6` 秒绝对容差匹配，不能用该容差放宽站位和建筑约束。
- **车间重叠**：报告在首发至最后复位期间、含待命车辆的两车最小中心距及其时刻；不是只检查两站静止距离。用 `--require-vehicle-separation` 可将车心距小于 3 m 判为不合格。默认诊断而不改变题面口径；接近临界时建议进一步复核。
- **不检查的事项**：路线是否全局最短、优化和就近法是否用了同一候选集、就近算法执行是否正确，以及任何关于候选集或连续场地的最优性声明。上述内容应由队员按求解配置与运行记录核验。

自动测试（含人工构造的并发/复用、提前出发、路径切楼体、车间重叠样例）：

```bash
nohup python3 -B -m pytest -q -p no:cacheprovider checks/test_q2_validate.py
```
