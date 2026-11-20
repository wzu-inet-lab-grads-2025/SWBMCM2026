# C 题数据首轮核查

本次核查使用工作区的英文题面、数据集说明、正式规程和已解压数据。文中的「全量」指逐文件、逐记录或逐片段遍历，分母随检查项列出。「抽样」仅适用于所列记录和时间窗口。所有复现命令均从仓库根目录运行，结果写到标准输出。

## 已核实与仍未知

- **数据规模。** 三组记录共用 24 个环境，按每条记录同一采样时刻只计一次，共 66,070 个轨迹时间戳。逐条与附件关联后，源索引指向 349 个文本标注区间，其中 343 个进入第三问官方抽样池。`tasks.csv` 单独列有 36 项作业，片段和作业通过同一划分、同一作业族的抽样联系。分母和脚本入口分别见三表及 **I**。
- **文件完整性。** 三组已解压目录分别有 297、308、384 个文件。逐个比对同名数据 ZIP 中央目录的相对路径、未压缩字节数和 CRC32，989 个文件全部匹配，无多件或少件。三包大小合计 5,838,030,490 字节，题面表 1 所列数值为 5,834,008,305 字节。本地 Pro、Standard 包内就是 CSV 和 MP4。上述比对验证本地 ZIP 与解压目录的一致性，题面引用的 `file_manifest.csv` 在当前解压目录未见。复现用下文 **P**。[英文题面，第 5–6 页](../problem/C/2026_“ShuWei%20Cup”C_Problem.pdf) · [本地数据包](../data/raw/C/Raw_Data/)
- **资料版本。** 工作区中的《C 题数据集说明》讲的是数据获取方式。题面引用的 `Data_Documentation/attachment_guide.md`、原始字段字典和 `missing_observation_experiments.md` 在已解压目录未见。第三问的本地正式文件为三页 `Problem_3_Requirements.pdf`，同时有共同划分副本与数值条件。核对解压目录可运行 `rg --files --no-ignore data/raw/C -g '*.md' -g '*.pdf' -g '*manifest*' -g '*dictionary*'`。[数据集说明](../problem/C/2026年第十二届数维杯全国大学生数学建模挑战赛（秋季赛）C题数据集说明.pdf) · [正式规程](../data/raw/C/Restocking_Tasks_and_Constraints/Problem_3_Requirements.pdf) · [共同条件](../data/raw/C/Restocking_Tasks_and_Constraints/common_conditions.json)

- **记录与划分。** 内部 `episode_uuid` 与官方划分表一一对应，69 条记录中有 69 个不同的内部 UUID。即使名称看起来像 UUID，本地 69 个记录目录名也均与各自内部 UUID 不同。环境与场景 ID 在训练、验证、测试之间两两没有交集。训练与测试共享 3 个操作员 ID，另两对交集为零。来源为 `data/raw/C/Restocking_Tasks_and_Constraints/record_split.csv`、Demo 的 `meta/episodes/` 和其他两组的 `session_metadata.csv`。复现用 **I**。

## 表 1　字段与时间轴

**三条训练记录抽样。** 下列目录都相对于 `data/raw/C/Raw_Data/`。字段名和形状取自本地元数据和表头，采样间隔、旁车匹配数与视频展示时间取自命令 F。

| 样本路径或字段 | 文件明示 | 样本实测与限制 |
|---|---|---|
| `EgoDemo/E001/` | `data/chunk-000/file-000.parquet` 含 `timestamp`、`frame_index`、手和头的位姿。`meta/info.json` 列关节名、第 0 点为腕、帧率 30。`annotation.json` 给片段起止，`bad_frame_ratio.json` 给带原因的帧。 | 1,619 行 × 15 列，帧索引 0–1,618。float32 时间从 0 到 53.933334 秒，与帧号除以 30 的最大差为 1.781 微秒以内。相邻间隔中位数 33.332825 毫秒，范围 33.332825–33.336639 毫秒。4 路 MP4 均有 1,619 帧，按展示时间 PTS 对齐。 |
| `EgoPro/291aff24-8b62-4a5e-b470-85114bdf86dc/` | `tables/pose_right_hand.csv` 有 `header.ts`、`transforms[i].pos.*`、`transforms[i].quat.*`。`videos/*_frames.csv` 提供 `video_timestamp_ns`，`frame_id` 的值为相机名。 | 手表 2,458 行 × 156 列，首末采样跨度 81.9 秒，间隔中位数 33.333 毫秒。双手、头、质量表及 4 路视频旁车时间戳逐行匹配。MP4 末帧 PTS 比源时间差 −0.819 毫秒。 |
| `EgoStandard/01e98c50-1164-448f-a7ff-b722d7a1c19b/` | CSV 与 MP4 存放方式同 Pro。`tables/annotation_semantic_segments.csv` 含语义文本和 `segment.start_time/end_time`。 | 手表 528 行 × 156 列，首末跨度 17.566667 秒，间隔中位数 33.333 毫秒。双手、头、质量表及 2 路视频旁车逐行匹配。MP4 末帧 PTS 比源时间差 −0.176 毫秒。两条 CSV 样本的相邻间隔均在 33.332999–33.334001 毫秒之间。 |
| 手、头和相机位姿 | 每手每时刻 21 × 3 个位置分量、21 × 4 个四元数分量。CSV 位姿表四元数为 w,x,y,z，样本外参表的旋转列序为 x,y,z,w。头部双目外参注明 `world` 与对应相机。 | 头部和每个头部相机每时刻各有一组位置和四元数。头部相机位姿与对应外参平移的最大差约 3.11 × 10⁻¹⁵。长度单位、坐标轴朝向和旋转主动/被动约定未在所查本地表内独立确认。按题面，不同记录的世界原点各自定义，腕部相机缺世界系外参。 |
| 质量与片段对齐 | 原始质量标记描述人手。Pro 和 Standard 用 `header.ts` 连接质量表，Demo JSON 用 `frame`。 | 三例的 4 / 8 / 5 个原始片段均与源索引的起止和文本匹配。采用半开区间时，按实测时间有 4 / 4 / 6 帧未被动作标注覆盖。Demo 若改用 `frame_index/30` 则有 5 帧未覆盖。E001 的坏帧 `frame=25` 可按零基或一基解释，分别约 0.833333 或 0.800000 秒。统计覆盖时保留此歧义。 |

**时间基准。** Demo 样本的 `timestamp` 与记录内相对秒相符。Pro 和 Standard 的 `header.ts` 是大整数纳秒，比较同一记录两个时刻时先做整数相减，再换成秒。样本中 Pro 首个轨迹时刻比会话头时间晚 128 纳秒。计算试验前缀时需另外注明标注起点相对于哪一个记录零点。视频旁车逐行匹配可以确认文件间的时间对应，实际曝光同步精度仍未知。

**全量表头复核（命令 P）。** Pro 的 14 条和 Standard 的 24 条记录中，左右手表均为 156 列。手部质量表随原因数量展开，表头可为 10、11 或 12 列。Pro 对应 7、3、4 条记录，Standard 对应 14、3、7 条。某些表只有 `problem_type.length`，没有 `problem_type[0]`。Demo 的 31 份 `meta/info.json` 均明列左手四元数分量顺序 `qw,qx,qy,qz`。其中 22 份列出头部两路视频，9 份另列双腕视频。来源为 `data/raw/C/Raw_Data/{EgoPro,EgoStandard}/*/tables/` 和 `data/raw/C/Raw_Data/EgoDemo/*/meta/info.json`。

## 表 2　质量与划分

官方 `train / validation / test` 各有 **55 / 7 / 7 条记录**，对应 **16 / 4 / 4 个环境**。`episode_uuid` 是记录键。各划分内保留一条记录的全部相邻帧。按组汇总和按环境汇总是对同一批采样时刻的两种分组方式，两类行不可相加。来源为 `data/raw/C/Restocking_Tasks_and_Constraints/record_split.csv` 和 `data/raw/C/Raw_Data/` 的逐记录位姿、质量文件。

**读表方法。** N 是主轨迹时间戳数，联合坏帧按至少一手有坏标记计一次。左、右坏数可重叠。未列是稀疏清单中没有该时刻，显式正常指逐帧表 `is_bad=False`。仅另一手被标坏时，不能推断本手正常。每行的「未列＋显式正常＋联合坏」等于 N。复现用 Q。

| 来源、划分或环境 ID | split | 记录 | N | 未列 | 显式正常 | 联合坏 | 左坏 / 右坏 |
|---|---|---:|---:|---:|---:|---:|---:|
| **全部** | 全部 | 69 | 66070 | 16306 | 48936 | 828 | 394 / 606 |
| EgoDemo | 全部 | 31 | 16414 | 16306 | 0 | 108 | 96 / 63 |
| EgoPro | 全部 | 14 | 17642 | 0 | 17232 | 410 | 126 / 314 |
| EgoStandard | 全部 | 24 | 32014 | 0 | 31704 | 310 | 172 / 229 |
| 划分汇总 | train | 55 | 47046 | 14261 | 32315 | 470 | 276 / 315 |
| 划分汇总 | validation | 7 | 6783 | 1397 | 5259 | 127 | 81 / 78 |
| 划分汇总 | test | 7 | 12241 | 648 | 11362 | 231 | 37 / 213 |
| home_x_apartment_0342 | train | 7 | 2239 | 2237 | 0 | 2 | 0 / 2 |
| home_x_apartment_0701 | train | 2 | 2924 | 0 | 2817 | 107 | 62 / 53 |
| hospitality_x_restaurant_0004 | validation | 1 | 1150 | 0 | 1104 | 46 | 32 / 21 |
| logistics_x_courier_station_0011 | test | 3 | 6317 | 0 | 6104 | 213 | 29 / 195 |
| logistics_x_courier_station_0021 | train | 3 | 5763 | 3254 | 2458 | 51 | 51 / 26 |
| logistics_x_courier_station_0022 | validation | 3 | 3703 | 0 | 3658 | 45 | 19 / 36 |
| logistics_x_courier_station_0041 | test | 1 | 3637 | 0 | 3619 | 18 | 8 / 18 |
| office_x_corporate_office_0086 | train | 5 | 4270 | 0 | 4226 | 44 | 13 / 36 |
| retail_x_convenience_0014 | train | 1 | 1661 | 1661 | 0 | 0 | 0 / 0 |
| retail_x_supermarket_0037 | train | 4 | 1307 | 664 | 639 | 4 | 0 / 4 |
| retail_x_supermarket_0049 | train | 1 | 679 | 679 | 0 | 0 | 0 / 0 |
| retail_x_supermarket_0054 | train | 18 | 6140 | 2403 | 3694 | 43 | 13 / 43 |
| retail_x_supermarket_0058 | train | 7 | 1828 | 779 | 1049 | 0 | 0 / 0 |
| retail_x_supermarket_0059 | train | 1 | 5220 | 0 | 5117 | 103 | 54 / 55 |
| retail_x_supermarket_0066 | train | 1 | 179 | 179 | 0 | 0 | 0 / 0 |
| retail_x_supermarket_0071 | train | 1 | 4012 | 0 | 3982 | 30 | 4 / 30 |
| retail_x_supermarket_0074 | validation | 2 | 1324 | 791 | 497 | 36 | 30 / 21 |
| retail_x_supermarket_0078 | test | 2 | 648 | 648 | 0 | 0 | 0 / 0 |
| retail_x_supermarket_0087 | train | 1 | 4428 | 0 | 4388 | 40 | 40 / 40 |
| retail_x_supermarket_0121 | train | 1 | 1766 | 1720 | 0 | 46 | 39 / 26 |
| retail_x_supermarket_0124 | train | 1 | 3945 | 0 | 3945 | 0 | 0 / 0 |
| retail_x_supermarket_0125 | train | 1 | 685 | 685 | 0 | 0 | 0 / 0 |
| retail_x_supermarket_0147 | validation | 1 | 606 | 606 | 0 | 0 | 0 / 0 |
| retail_x_supermarket_0159 | test | 1 | 1639 | 0 | 1639 | 0 | 0 / 0 |

**各组、各环境均为零的检查项。** 66,001 个记录内相邻时间对（各记录 N−1 之和）中，倒序、重复、超过 50 毫秒的间隔均为 0。50 毫秒是本轮核查阈值。按每手每时刻 63 个 XYZ 分量检查，左右手各自的全缺、部分缺、非数值、非有限值均为 0。另查每个关节三个坐标同时为零，各组和环境也均为 0。这些计数只涉及文件中实际存在的采样点。全部 **828** 个联合坏帧都有完整有限双手坐标。Demo 元数据的标称帧率分布为 25.0 Hz（2 条）、29.999 Hz（4 条）、30.0 Hz（24 条）、30.003 Hz（1 条）。其 31 份 `meta/info.json` 都写 `train:0:1`，其中 4 条在官方划分中属于验证或测试。以上均可用 Q 复现。

## 表 3　片段、作业与信息披露

来源为 `data/raw/C/Restocking_Tasks_and_Constraints/` 内的 `source_segment_index.csv`、`job_families.csv`、`tasks.csv`、`record_split.csv`、`common_conditions.json` 和正式规程第 1–3 节。P 是放置补货，A 是整理摆放，L 是检查贴标。下表仅在标为样例的行涉及某一条具体来源。

| 范围 | 检查项 | 核对结果与分母 |
|---|---|---|
| 全量 · 索引 | 观测质量资格 | 索引中 `eligible=1` 的 343 个区间是官方长度抽样池。索引未给区间级观测质量合格位，正式规程第 1 节明确长度资格不认证观测质量。因此按每个 split × 类别报告长度合格数，官方观测质量合格数记为「未提供」。帧级质量统计见表 2。 |
| 全量 · train | 长度合格 / 该族总数 | P **124/127**，A **112/114**，L **10/11**。 |
| 全量 · validation | 同一口径 | P **15/15**，A **13/13**，L **7/7**。 |
| 全量 · test | 同一口径 | P **33/33**，A **20/20**，L **9/9**。9 个池均非空，6 个非合格片段全部来自 train。命令 I、S。 |
| 全量 · 原始标注 | 文件和索引对应 | 349/349 个源区间与原始标注逐项对应，缺失、重复引用及标注内容错配均为 0。按十进制起止时间相减，3 段恰为 1.2 秒，其中 2 段的 `duration_s` 保存为 `1.1999999999999993`。这两段的官方 `eligible` 均为 1。命令 S。 |
| 全量 · 标记覆盖 | 长度合格段中含任一原始坏标记 | 以 `[start,end)` 和脚本 S 所列时间基准计，343 段中有 51 段含手部坏标记。按 P / A / L 顺序，训练为 19 / 11 / 1，验证为 7 / 2 / 0，测试为 5 / 6 / 0。每格分母是上面相同 split、相同作业族的长度合格数。其余区间的物理观测质量仍未知。 |
| 全量 · 越界 | 末采样与播放终点 | 严格比较，3/349 个区间结束点晚于最后轨迹采样。超过本轮 10 微秒审计容差的有 2 个，分别是 `U_22c09bd4a0a5`（1.100 毫秒）及 `U_e045c5b73db2`（8.935 毫秒）。另一段 `U_7a5ebc701228` 相差 0.763 微秒。184 个视频均以包 PTS 与帧持续时间核查，5 个区间结束点晚于末帧 PTS，超过实际播放终点的区间为 0。命令 S。 |
| 抽样 · 作业追溯 | `S1_J01` → validation / P | `tasks.csv` 首项是 P 类，0 秒释放、35 秒到期、权重 3、区域 Z4，无前置。按正式规程用公开验证种子 `2026111700` 离线分配，该次抽到 `U_e045c5b73db2`。源索引的 `sample` 是 `478f3e70-4066-408b-93cf-831625320004`，内部 `episode_uuid` 是 `3cd0ae33-561c-491a-b903-24cfffcb59e4`。原标注零基序号 2，对应 21.9–38.3 秒整理桌上矿泉水瓶的动作。换种子可得到不同来源。命令 S。 |
| 全量 · 案例 | S1 / S2 / S3 | 作业数 8 / 12 / 16，时间上限 100 / 100 / 125 秒，成本预算 32 / 34 / 40，复核次数上限 4 / 6 / 8。36 项作业 ID 唯一，任务类别与区域均能匹配，前置作业均在同一案例且关系无环。命令 P。 |
| 规程 · 公开条件 | 作业、资源 | 公开任务条件含 `case_id`、`job_id`、`family_id`、`release_s`、`due_s`、`priority_weight`、`zone_id`、`predecessors`，以及共同区域、预算、映射条件。释放时刻决定何时取得该作业的数值前缀。 |
| 规程 · 免费释放 | 累计 0.2 源秒 | 从该原始标注起点取窗口内已有时间戳、左右手各 21 点的位置与朝向、头和左右头戴相机位姿、原始手部质量标记。保留缺失和坏标记，按原始值披露。 |
| 规程 · 两次付费复核 | 累计 0.6、1.0 源秒 | 只扩大上述数值通道的时间范围。每次耗 1 成本单位并占用 1 情景秒，延迟结束后才可用。只可复核已释放且未尝试执行的作业，各作业最多两次，案例总额另受限。单独等待不会扩大该作业的可见窗口。 |
| 规程 · 执行反馈 | 完成时或到 H 终止时 | 本次试验已实际返回的执行结果可用于此后的决定。已完成作业可由返回的开始、完成时刻得知耗时。H 截断只观察到 H，可记录已执行未完成。执行来源引用在完成或终止后写入审计日志，不能反向修改之前的决定。 |
| 规程 · 离线核验 | 分配、评分、追溯 | `unit_id`、`episode_uuid`、源目录或片段编号、起止点、`duration_s`、由它直接得到的实际服务时长、语义文本、未披露轨迹、全视频以及抽样种子会泄露来源或未来。全段均值、全段坏帧率、用未来帧平滑或插值所得的前缀、通过种子或前缀匹配索引反查答案，也不能进入当前决定。跨试验不得积攒验证或测试真值。 |
| 抽样 · 前缀逐行核对 | `U_883c40426953`，train / P | 源索引第 261 行、划分表第 59 行。原标注 0–3 秒。以设备元数据起点为零，用整数纳秒截取 0.2 / 0.6 / 1.0 秒，依次读到 CSV 数据行索引 0–5、0–17、0–29。累计 6 / 18 / 30 帧，含右手遮挡 0 / 3 / 7 帧。五路位姿和质量表在这些时刻时间戳一致，每时刻的 315 个位姿标量均有限。命令 V。 |

作业样本可直接定位到 [EgoPro 原始标注](../data/raw/C/Raw_Data/EgoPro/478f3e70-4066-408b-93cf-831625320004/tables/annotation_semantic_segments.csv) 的第 4 行（含表头）。本次分配只用于离线追溯。命令 S 对 CSV 片段估算标注时钟原点时，以首条语义消息的 `header.ts` 减去该片段的 `start_time`。所有区间边界都按脚本提供的口径报告。

前缀样本来自 `data/raw/C/Raw_Data/EgoStandard/045c1e70-ab63-404a-94ef-735e68c8fd6a/` 下的 `session_metadata.csv`、`tables/pose_*.csv` 和 `tables/annotation_bad_frame_pose_hand.csv`。命令 V 单独检验设备元数据原点与导出偏移相符的解释。在这个样本中，接下来的第 6、18、30 号帧分别比对应边界晚 **64 纳秒**。若改以首帧为零并包含右端点，则会数到 **7 / 19 / 31** 帧。规程明确的是累计的源时间窗，未写明纳秒舍入约定。绝对时间戳、源帧号、文件哈希和全表形状不能用作反查来源的键。

## 会影响后续复核的三个问题

1. **跨通道时间零点与端点。** E001 的质量帧编号可有一帧歧义，Standard 前缀样本中 64 纳秒的原点差会改变三个窗口的样本数。逐帧对齐与累计前缀需要记录采用的原点、整数或浮点精度以及端点约定。见 F、V。
2. **空间单位与坐标约定。** 本地题面引用的字段字典未在已解压目录中找到。文件能识别关节、四元数分量和部分父子坐标系。报告物理距离或跨记录方向时仍需核实长度单位、轴向和旋转约定。见 F。
3. **观测质量的分母。** Demo 采用稀疏坏帧清单，其他两组存在逐时刻质量布尔值。长片段进入第三问抽样池，不能据此推出全片段各时刻的观测都合格。表 2、3 分别给出字段覆盖和官方抽样资格，区间级质量认证仍未知。

## 复现入口

以下命令只读数据。`-B` 关闭 Python 字节码缓存。`PY` 指本机已有、可读取 Parquet 的解释器，本次复核已有脚本使用 Python 3.11.14、pandas 2.3.3 和 pyarrow 22.0.0。在其他机器上可换成装有相同依赖的 Python。

本次读取的正式规程 PDF 的 SHA-256 为 `9cc9caf7b6d373c527190b646ddd8132f44318b3012bfa3958ae9da07a13381d`，可用 `sha256sum data/raw/C/Restocking_Tasks_and_Constraints/Problem_3_Requirements.pdf` 识别本地版次。

```bash
PY=/mnt/disk/zhoutianyu/miniconda3/envs/LLM4AD/bin/python
# P　解压目录校验和、案例约束与前置关系
python3 -B research/c_audit_package.py
# I　已有盘点脚本，读取内部 UUID、源索引、ZIP 成员和记录划分
"$PY" -B research/check_c_data.py
# F　三条记录的字段、时间与 MP4 展示时间戳；需要 ffprobe
/mnt/disk/zhoutianyu/miniconda3/envs/collm/bin/python -B research/c_audit_fields.py
# Q　全量坐标、标记、时序与集间 ID 对照，输出含各环境分母。加 --json --records 可看逐记录明细。
/mnt/disk/zhoutianyu/miniconda3/bin/python -B research/c_audit_quality.py
# S　逐片段/作业关联、长度边界和视频播放终点。加 --details 可看逐段证据。
/mnt/disk/zhoutianyu/miniconda3/bin/python -B research/c_audit_segments.py
# V　逐节点核对规程原文与一条训练记录的三个前缀
python3 -B research/c_audit_prefix.py --pdf-quotes
# R　直接查看第三问规程和数值条件。此命令只读取任务目录。
python3 -B - <<'PY'
from pathlib import Path
from pypdf import PdfReader
rules = Path('data/raw/C/Restocking_Tasks_and_Constraints')
for page in PdfReader(rules / 'Problem_3_Requirements.pdf').pages:
    print(page.extract_text())
print((rules / 'common_conditions.json').read_text())
PY
```
