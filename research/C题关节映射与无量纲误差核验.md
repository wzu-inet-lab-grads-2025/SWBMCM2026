# 三来源手部关节、长度单位与相对位置误差

**核验结论（2026-11-20）。** 数据发布方 Lightwheel 的 LeRobot 与 MCAP 手部格式都规定左右手各有 21 点，位置单位为米。两份定义都将 0 号列为腕，9 号列为中指掌指关节。按这两个点计算的同手参考长度，在当前 Pro/Standard 的 328 个训练窗中两手均可用。拇指 1–3 号在两种格式中有不同名称，涉及这些点的解剖解释仍需核对。[LeRobot 字段与关节表](https://docs.lightwheel.net/egocentric_data/lerobot_data/format_specification) · [MCAP 手部字典](https://docs.lightwheel.net/egocentric_data/mcap_data/pose/hand)

## 关节和单位的证据

关于赛题原文是否定义拇指、同一操作员的跨记录变化及手指比例，请参阅[拇指编号与操作员手型核对](C题拇指编号与操作员手型核对.md)。

| 编号，两手相同 | EgoDemo 本地名称及 LeRobot 定义 | EgoPro/Standard 对应的 MCAP 定义 |
|---|---|---|
| 0 | Wrist，腕 | Wrist，腕 |
| 1、2、3、4 | Thumb MCP、PIP、DIP、Tip | Thumb CMC、MCP、IP、Tip |
| 5、6、7、8 | 食指 MCP、PIP、DIP、Tip | 相同 |
| 9、10、11、12 | 中指 MCP、PIP、DIP、Tip | 相同 |
| 13、14、15、16 | 无名指 MCP、PIP、DIP、Tip | 相同 |
| 17、18、19、20 | 小指 MCP、PIP、DIP、Tip（Little） | 相同（Pinky） |

MCP 是掌指关节，PIP、DIP 分别是近端、远端指间关节，CMC 是腕掌关节，IP 是指间关节，Tip 是指尖。两套说明都把 1–4 号排成拇指链，4 号均为指尖。仅凭序号无法判断 1–3 号的差异是命名惯例还是标志点定义不同。此前五点中心所用的 0、5、9、13、17 对应腕与四个非拇指掌指关节。

- **EgoDemo。** 31/31 条记录的 `meta/info.json` 中，两手 `observation.state.hand_*_world` 均为 `[21,3]`，两手全部关节名称、XYZ 顺序逐项匹配发布方 LeRobot 表。该表在 Data Parquet Schema 的 Notes 明确写有 “All position values are in meters”。[LeRobot 说明](https://docs.lightwheel.net/egocentric_data/lerobot_data/format_specification#data-parquet-schema)
- **EgoPro、EgoStandard。** 分别 14/14、24/24 条记录的左右手 CSV 都含完整 `transforms[0..20].pos.{x,y,z}` 和 `transforms.length`。发布方对这两路手部 topic 指定世界系、位置单位米、X 向前、Y 向左、Z 向上。DevKit 固定提交还列出 `HAND_FRAME_NAMES`，按原序读取 `transforms` 和 `pos.x/y/z`。赛包 CSV 表头保留了该结构。`operator.operator_info.unit` 描述人体测量元数据，轨迹单位的依据来自手部字段文档。[MCAP 手部字段](https://docs.lightwheel.net/egocentric_data/mcap_data/pose/hand#coordinate-frame) · [DevKit 固定版本的索引表与读取器](https://github.com/LightwheelAI/LW-Egosuite-DevKit/blob/ef23daaf2327fb35b66420dac443319046163559/lw_egosuite_backend/converters/pose/std_mcap.py#L38-L60)
- **本地证据的边界。** 赛包内层目录已有盘点，题面引用的原始字段字典未列其中。厘米解释采用发布方位置单位与本地同型导出保留尺度的假设；本轮没有逐值对照原 MCAP 与赛包 CSV 的导出证明。训练记录中逐记录 0–9 距离中位数折合 **7.96–9.40 厘米**，三个来源量级相容。各记录有自己的世界原点，物理定位精度仍需独立测量。LeRobot 说明明确世界坐标，与 MCAP 的轴向完全对齐还需方向性证据。[赛包核查](C题调研.md) · [题面第 5 页](<../problem/C/2026_“ShuWei Cup”C_Problem.pdf>)

## 怎样检验无量纲误差

复用原有训练池、1 秒历史、2 秒人工遮挡和保持末值基线。对窗 \(w\) 和手 \(s\)，只从遮挡前历史 \(H_w\) 计算参考长度，遮挡内锁定分母。分子是同记录、同手的三维欧氏距离。

\[
L_{w,s}=\operatorname{median}_{t\in H_w}\lVert p_{9,s}(t)-p_{0,s}(t)\rVert_2,\qquad
e^*_{w,s}(t)=\frac{\lVert\hat p_s(t)-p_s^{\rm ref}(t)\rVert_2}{L_{w,s}}.
\]

本次诊断要求整个历史段内两端点均有限、非全零、间距为正且至少有两个采样点。合格窗先按原来的腕点与显式质量规则选定，然后逐手检查分母，故能检出新增的排除。用于决策时，必须允许获取历史的 9 号点，回执仍可只含双腕。若未来协议只提供腕点，可采用训练期固定的正参考长度，报告其估计来源；固定常数产生的是统一换算，逐窗长度则会改变不同窗的相对权重。

| 训练来源 | 检查几何的记录 / 时刻（每手） | 质量口径 | 合格参考窗 / 其中分母可用（左、右） |
|---|---:|---|---|
| EgoDemo | 27 / 14,261 | 稀疏未列，质量未知，用于几何量级检查 | 0 / 0、0 |
| EgoPro | 8 / 9,501 | 显式正常 | 92 / 92、92 |
| EgoStandard | 20 / 22,814 | 显式正常 | 236 / 236、236 |

**稳定性与误差。** 28 条合格记录、10 个训练环境，每手 19,639 个评分时刻。328 个窗的 \(L\) 中位数为左 8.56、右 8.58 厘米，最小值为左 7.96、右 8.08 厘米。各窗历史前后半秒分别估计的中位数之差，除以全历史中位数，左右手最大值均低于 \(8\times10^{-7}\)。这种稳定度与记录采用近似刚性骨架的现象相容；对整手坐标同时施加偏移，0–9 长度也保持恒定。因此长度适合作为本批数据的数值分母，其稳定度不能检验整手定位准确性。

保持末值的平均距离为左 **19.79 厘米**、右 **20.93 厘米**，对应平均相对误差为左 **2.323**、右 **2.454** 个同手参考长度。两个量均先在记录内平均，再在环境内等权平均记录，最后等权平均环境。相对量在每个窗先相除，保留各自的参考长度。米与厘米两种输入单位、平移坐标原点的合成算例均产生相同的相对误差。[聚合数表](q2_diagnostic/train_palm_scale.csv) · [合成验收](../checks/test_c_palm_scale.py)

**建议作为并列诊断指标。** 厘米保留绝对位移含义，相对长度回答“偏差相当于这只手几个腕至中指掌指关节的距离”。例如使用同一个相对容差，参考长度较小的手对应的厘米容差也更紧。若应用目标规定固定厘米容差，就按厘米评估超差；若关注相对手尺度，可另外评价 \(e^*\)。在缺乏显式正常参考、仅有腕点或参考长度异常小的场合，单列不可评数量与原因。正式状态、主指标和容差仍由队伍决定。

## 复现

字段核对仅读全体的索引、表头和名称。数值、分母与预测误差仅读官方训练记录，输出每来源每手汇总。`rec_median_min/max` 为各记录 0–9 长度中位数范围；`spread` 为历史内 `(P90−P10)/中位数`；`half_change` 为上述前后半段差比。CSV 的长度和距离沿用发布方的米，其余比例无量纲。AI 辅助范围为资料检索、字段核对、预处理和聚合。[脚本](../experiments/c_palm_scale.py)

```bash
PY=/mnt/disk/zhoutianyu/miniconda3/bin/python
"$PY" -B -m experiments.c_palm_scale
"$PY" -B -m unittest checks.test_c_palm_scale
cmp runs/c_palm_scale/summary.csv research/q2_diagnostic/train_palm_scale.csv
```
