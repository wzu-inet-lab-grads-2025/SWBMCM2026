# 拇指编号与操作员手型核对

## 赛题写明的内容

英文 C 题第 2 页写明每手有 21 个三维关键点，代表点可取单个关键点或若干关键点的几何中心，并要求解释选择依据。第 6 页指向原始字段字典。正文与图注均未给出 1、2、3 号拇指点的逐点解剖定义。第三问正式规程第 1 页规定前缀包含双手 21 个点的位置和朝向。该页同样未给出这些编号的映射。已有赛包目录核查未见题面提及的字段字典。[英文原题，第 2、6 页](<../problem/C/2026_“ShuWei Cup”C_Problem.pdf>) · [第三问规程](../data/raw/C/Restocking_Tasks_and_Constraints/Problem_3_Requirements.pdf) · [附件盘点](C题调研.md)

组委会的赛题发布页提供下载渠道。指定答疑专区有参赛者关于附件缺失的提问，本次公开页面未显示能确认拇指编号的命题方回复。答疑评论的可读范围有限。[赛题发布](https://www.nmmcm.org.cn/Awards/228.html) · [组委会指定答疑入口](https://www.nmmcm.org.cn/News/225.html) · [C 题专区公开帖子](https://pd.qq.com/g/pd52619943/post/B_9ed7ff6a69b60a001441152190027252840X60?subc=745397908)

## 发布方格式与解剖对应

Lightwheel 的 LeRobot 格式及本地 EgoDemo 元数据均将 1、2、3 号写为 Thumb MCP、PIP、DIP。MCAP 格式和 DevKit 索引表写为 Thumb CMC、MCP、IP。两者的 4 号都写为指尖。标准解剖中的拇指有两节指骨、一个指间关节，故 Demo 的 PIP/DIP 标签不足以证明分别存在两个人体指间关节。当前可按点编号描述夹角，例如“1–2–3 三点在 2 号点形成的夹角”。将它命名为某一解剖关节的屈曲角，还需要标志点和角度定义的依据。[LeRobot v1.0.0 文档](https://docs.lightwheel.net/egocentric_data/v1.0.0/lerobot_data/format_specification#hand-joint-convention) · [MCAP v1.0.0 文档](https://docs.lightwheel.net/egocentric_data/v1.0.0/mcap_data/pose/hand#joint-index-and-conventions) · [DevKit 固定版本](https://github.com/LightwheelAI/LW-Egosuite-DevKit/blob/ef23daaf2327fb35b66420dac443319046163559/lw_egosuite_backend/converters/pose/std_mcap.py#L38-L60) · [拇指解剖](https://www.seattlehandandelbow.com/hand-anatomy-hand-wrist-surgeon-seattle-bellevue-wa.html)

发布方的 EgoDemo 数据卡明确两种格式可以通过 `source_episode_uuid` 找到同一次采集的配对导出。本次本地赛包只有 Demo 的 LeRobot 表以及 Pro/Standard 的 CSV，尚缺同一源片段的双格式逐点对照。若取得配对导出，可先对齐同手、相同源时刻，以腕、9 号点和指尖核对时钟及坐标，再比较 1–3 号在多种姿态下的坐标并检查可能的重排。这样能核验导出点的对应。赋予真实解剖名称还需发布方的标志点说明。[EgoDemo Data Contract](https://huggingface.co/datasets/LightwheelAI/EgoDemo#data-contract)

## 训练记录中的尺度和比例

先逐时刻计算同手 `L=距离(0,9)`。食指链长取 5–6、6–7、7–8 三段距离之和，小指取 17–18、18–19、19–20 三段距离之和。比例在同一时刻除以该手的 `L`。逐记录取中位数，再在每个操作员 ID 内对记录中位数取中位数，最后按操作员等权比较。下表是这些操作员汇总值的最小至最大范围。这里的回顾性全记录统计只用于描述差异。在线评分仍按上轮方式，在遮挡前锁定每手分母。[参考长度定义](C题关节映射与无量纲误差核验.md)

| 55 条训练记录，33 个操作员 ID | 左手范围 | 右手范围 |
|---|---:|---:|
| 腕至中指掌指关节 `L`（厘米） | 8.06–9.34 | 8.00–9.31 |
| 食指链长 / `L` | 0.865–0.944 | 0.866–0.939 |
| 小指链长 / `L` | 0.634–0.706 | 0.636–0.702 |

三来源几何统计每手 46,576 个时刻，含 Demo 的 14,261 个质量未列时刻。限制在显式正常的 Pro/Standard 后，每手有 32,315 个时刻、28 条记录、21 个操作员 ID。左手食指链长 / `L` 仍为 0.865–0.937。人体测量研究也记录了个体之间指长比例的变化，赛包中的数值来自追踪骨架，可能同时受个体、骨架拟合与采集批次影响。[本地分池统计](q2_diagnostic/train_hand_proportions.csv) · [指长比例的人体测量研究](https://pmc.ncbi.nlm.nih.gov/articles/PMC2732043/)

33 个操作员 ID 中有 12 个对应多条训练记录。对每个重复 ID，取同手各记录 `L` 中位数的极差，再除以该 ID 的中位数。左手相对极差的中位数为 2.17%，最大为 6.69%；右手分别为 1.30% 和 4.39%。同一窗内尺度稳定，跨记录复用某个 ID 的固定长度则可能改变分母。[聚合结果](q2_diagnostic/train_hand_proportions.csv)

有 4 个 ID 同时出现在 Demo 和 CSV 来源。按 ID、手别、格式先取记录中位数，再比较 0–1、1–2、2–3、3–4 四段相对于 `L` 的长度。两格式差值的绝对值除以两者均值，在 4 个 ID、两手、四段中最大为 2.61%。同号链段量级相容。这里配对的是同 ID 的不同记录，逐帧同源映射和解剖标定仍待核验。

## 给队伍讨论的假设

1. **窗内尺度。** 若只报告腕点误差占参考长度的比例，只需当前记录、本手遮挡前可见的 0、9 号点能给出正且稳定的参考长度。窗内固定这个长度即可解释结果。上轮 328 个可评分训练窗支持这一条件。
2. **跨人形状。** 若进一步把指尖位置或张开度按 `L` 归一后共用同一手型模板，就引入了手指比例足够接近的附加假设。上表存在残余变异，能否近似共用要结合目标特征的容差检验。可在官方训练集内部按操作员看配对残差及可用人数，保留原环境划分和相同窗口作正式比较。
3. **容差解释。** 逐手 `L` 允许同样的厘米误差产生不同的相对误差。应用关心绝对位置时仍报告厘米，关注相对于手部尺度的运动时可并报无量纲值。正式状态及主指标由队伍决定。

## 复现

脚本只读训练轨迹，使用原始操作员标识核对索引后做分组，公开 CSV 只写跨操作员汇总。`within_range_pct` 列仅在有多条记录的 ID 上计算，`paired_diff_pct` 列仅在两格式共有的 ID 上计算。AI 辅助资料检索及描述性统计。[复现脚本](../experiments/c_hand_proportions.py)

```bash
/mnt/disk/zhoutianyu/miniconda3/bin/python -B -m experiments.c_hand_proportions
cmp runs/c_palm_scale/operator_summary.csv research/q2_diagnostic/train_hand_proportions.csv
```
