# C 题无人超市补货的数据与方法调研

论文原文、行业案例和证据对照见 [C 题文献与行业方案](C题文献与行业方案.md)。

C 题把三个不同层次的问题连在一起。第一问研究人手轨迹和观测质量，第二问在同一批记录上隐藏、延迟返回观测，第三问用带文本标注的片段生成有释放时间、交期和成本的作业。三问最值得贯穿讨论的问题是“多看到一点信息，足以改变下一步决定吗”。证据分别来自原始记录、人工遮蔽回放和情景调度，每项结论的适用范围也随之改变。[英文原题](<../problem/C/2026_“ShuWei Cup”C_Problem.pdf>) · [中文译文及原图](../problem/translations/C题-无人超市补货.md)

## 本地附件实际提供了什么

以下是按记录内部的 `episode_uuid` 去重、把三包逐项与官方划分表连接后的盘点。时间戳按每条手部轨迹的时间计，左右手和摄像机各路不会重复增加分母。标记数按“至少一只手被标记”的时间戳计，Demo 读取稀疏 JSON，另两组读取逐帧质量表。标记占比涉及全部记录的附件清点，时长分布只统计训练集。[数据核查脚本](../experiments/check_c_data.py) · [官方环境划分副本](../data/raw/C/Restocking_Tasks_and_Constraints/record_split.csv)

| 本地数据组 | 记录 | 时间戳 | 带手部质量标记 | 实际文件与视角 |
| --- | ---: | ---: | ---: | --- |
| EgoDemo | 31 | 16,414 | 108（0.66%） | JSON、Parquet、MP4。31 条有头部双目，其中 9 条另有双腕视角 |
| EgoPro | 14 | 17,642 | 410（2.32%） | CSV、MP4。头部双目及双腕视角 |
| EgoStandard | 24 | 32,014 | 310（0.97%） | CSV、MP4。头部双目 |
| 合计 | 69 | 66,070 | 828（1.25%） | 三包按内部标识与划分表全部匹配 |

**有两处版本差异会直接影响开工。** 英文题面描述 Pro 和 Standard 的原始容器为 MCAP，本地两个 ZIP 的成员已经是 CSV 和 MP4，可以直接读取 `tables/pose_right_hand.csv` 等文件。三份内层原始数据 ZIP 的本地字节数合计为 5,838,030,490，与题面表 1 的 5,834,008,305 不同，记录数、时间戳数则逐组吻合。上游发布者文档适合核对字段语义，本届数据规模和路径以实际发放包为准。[Lightwheel 数据开发工具](https://github.com/LightwheelAI/LW-Egosuite-DevKit) · [EgoDemo 数据卡](https://huggingface.co/datasets/LightwheelAI/EgoDemo) · [EgoPro 数据卡](https://huggingface.co/datasets/LightwheelAI/EgoPro) · [EgoStandard 数据卡](https://huggingface.co/datasets/LightwheelAI/EgoStandard)

解开外层 `C_Problem_Data.zip` 并核对内层 `Restocking_Problem_and_Data_EN.zip` 的成员目录后，能找到三份原始 ZIP、六个 CSV/JSON 规则文件和三页的 `Problem_3_Requirements.pdf`。题面所指的 `Data_Documentation/`、`missing_observation_experiments.md`、`file_manifest.csv` 和 `tasks_and_evaluation.md` 均未列在这份内层目录。已逐文件核对本地七个规则文件与内层包中同名文件的 SHA-256，七项全部一致。第三问按 PDF、JSON 和任务表执行。[第三问规程 PDF](../data/raw/C/Restocking_Tasks_and_Constraints/Problem_3_Requirements.pdf) · [共同数值条件](../data/raw/C/Restocking_Tasks_and_Constraints/common_conditions.json) · [组委会数据集说明](https://www.nmmcm.org.cn/News/227.html)

**2026-11-20 官方 C 题答疑。** “C题命题人”（管理员）直接回复了第二问遮蔽窗口、观测预算、请求成本和返回延迟是否统一的问题：“没有统一规定，模型和复观测策略由各队自行设计。” 频道主“活动君”另明确，现行附件在 `Restocking_Tasks_and_Constraints/source_segment_index.csv` 中以 `dataset`、`sample` 对应数据包与记录文件夹，以同目录 `record_split.csv` 确认训练、验证、测试分组。题面关于预先固定共同条件、按原记录延迟披露和按环境划分的要求仍适用。[命题人原回复](https://pd.qq.com/g/pd52619943/post/B_e310006b963e0e001441152187263408250X60?subc=745397908) · [频道主数据集查阅说明](https://pd.qq.com/g/pd52619943/post/B_1b1f006b52a902001441152187626188910X60?subc=745397908) · [C 题答疑专区](https://pd.qq.com/g/pd52619943?subc=745397908)

**文件夹名称容易让划分失效。** 三包共 69 条记录，逐条比较发现 69 个目录名都与内部 UUID 不同。即便 Pro、Standard 的目录名看起来已经是 UUID，也要从 `session_metadata.csv` 的 `task_info.episode_uuid` 读取内部标识。Demo 则从 `meta/episodes` 的 Parquet 里读 `episode_uuid`。原始记录与划分表的匹配可逐条复核。[C 题本地数据目录](../data/raw/C/Raw_Data/) · [只读核查脚本](../experiments/check_c_data.py)

## 哪些差异能用数据检验

| 官方划分 | 环境数 | 记录数 | 合格放置补货 P | 合格整理 A | 合格检查贴标 L |
| --- | ---: | ---: | ---: | ---: | ---: |
| 训练 | 16 | 55 | 124 | 112 | 10 |
| 验证 | 4 | 7 | 15 | 13 | 7 |
| 测试 | 4 | 7 | 33 | 20 | 9 |

三组划分的环境、场景在两两之间均无 ID 重叠，训练和测试共有 3 个操作员 ID。报告泛化能力时宜用“留出环境”这一表述，并逐环境展示变化。不同来源也共享部分环境，以文件来源充当训练测试划分会改变问题。只有 4 个验证环境，单一长记录、一个特殊场景或少量长缺失段足以影响总体平均。原始轨迹宜同时看按记录或环境汇总的值和组间范围。[记录划分](../data/raw/C/Restocking_Tasks_and_Constraints/record_split.csv) · [Varoquaux 关于小样本验证误差的研究](https://arxiv.org/abs/1706.07581)

训练集的合格 P、A、L 片段分别来自 23、29、3 条记录以及 9、9、2 个环境。按片段统计，原始标注时长中位数分别为 4.0、3.2、3.6 s，最大值分别达 125.0、37.2、38.0 s。这是人类标注片段的持续时间分布。尤其是 L 类，增加同一抽样池上的回放次数能更准确描述该池的策略波动，但只有两个训练环境支撑其环境间差异推断。349 个片段中 343 个具有官方 `eligible=1`。其中 2 个的浮点时长略低于 1.2。按官方列构造采样池，才能复现同一随机种子下的任务。[源片段索引](../data/raw/C/Restocking_Tasks_and_Constraints/source_segment_index.csv) · [第三问规程第 1 节](../data/raw/C/Restocking_Tasks_and_Constraints/Problem_3_Requirements.pdf)

## 第一问的状态与可靠性

每只手有 21 个关键点。以腕点为代表可以跟踪手臂平移，易于跨格式复核。掌部多个关键点的中心能表示掌部整体运动，但权重和缺点时的处理会改变状态定义。上游 Hand Pose 字典明确给出第 0 点为腕，世界坐标中的位置单位为米，MCAP 手部四元数的字段顺序为 w、x、y、z。Demo 应另查各记录 `meta/info.json` 的字段名与维度。若采用头部相对坐标，头部旋转本身也会带来表观速度，跨记录比较时需说明坐标变换。四视角中腕部缺少世界系外参，跨视角三维验证主要依赖有相应标定的头部双目。[Lightwheel 手部字段字典](https://docs.lightwheel.net/egocentric_data/mcap_data/pose/hand) · [英文原题第 5–6 页](<../problem/C/2026_“ShuWei Cup”C_Problem.pdf>)

数据清理适合用一张逐记录表记录四类数量，即适合拟合的帧、可作留出参考的帧、保留作低质量分析的帧，以及被排除的帧。应检查左右手分别标记的原因、空值、重复时间戳、采样间隔和异常跳变，并保留区间长度和排除理由。Demo 的元数据同时有 25 fps 记录和约 30 fps 记录。Pro/Standard 的 `header.ts` 是纳秒量级，宜按整数求相对时间后再换秒。对齐视频需进一步核对 `videos/*_frames.csv`。同一世界系的原点只在本记录内有意义。[本地原始包](../data/raw/C/Raw_Data/) · [Lightwheel 数据格式文档](https://docs.lightwheel.net/egocentric_data/mcap_data/overview)

从文献方法看，末次状态保持或简单线性预测能给出可复现的低参数基线。状态空间方法适合表达真实运动变化、观测噪声与缺测期预测的区别。分阶段模型则要求先明确当时可以获知的阶段证据。三种来源的坏帧标记占比均很低，仅报总体正确率会掩盖漏检。可查坏帧召回、精准率、概率校准与逐记录标记覆盖。若当前质量标记用作预测标签，同一时刻的标记及由其直接产生的变量应从预测特征中隔离。[Särkkä 与 Svensson《Bayesian Filtering and Smoothing》](https://research.aalto.fi/en/publications/bayesian-filtering-and-smoothing-3/) · [Gneiting 与 Raftery 的概率预测评分论文](https://sites.stat.washington.edu/raftery/Research/PDF/Gneiting2007jasa.pdf)

## 第二问的遮蔽与重新观测

这里有两类证据。合格记录上**人工隐藏**某段已有观测，可以把预测与留出的记录参考作比。**自然遮挡**反映哪些任务和阶段更容易失去观测、遮挡持续多久、如何恢复。没有独立参考的位置只能统计发生与恢复。人工随机遮蔽与自然遮挡可能对应不同的运动阶段，解释二者差异可借鉴 Rubin 的缺失机制框架。合格记录本身也有测量误差。[英文原题第 2–3 页](<../problem/C/2026_“ShuWei Cup”C_Problem.pdf>) · [Rubin 关于缺失机制的原论文](https://doi.org/10.1093/biomet/63.3.581)

| 应当固定的实验条件 | 可比较的方法 | 论文应给出的证据 |
| --- | --- | --- |
| 相同的记录、起始历史、隐藏通道、窗口与参考资格 | 保持末次位置、简单运动外推、带不确定度的状态估计 | 分缺失时长的三维距离误差，注明参考样本量、单位和聚合层级 |
| 相同的请求预算、每次成本、可请求时刻和返回延迟 | 固定间隔请求、基于预测不确定度的触发。也可观察零请求端点 | 误差与观测支出的关系、请求次数、等待时间、实际计算开销 |
| 相同的名义覆盖水平与可用历史 | 比较区间或预测区域的可靠性 | 经验覆盖率与区间宽度或区域大小一起报，并展示一次覆盖失败的过程 |

请求、测量所属的原始时刻、返回时刻三者要能在同一张回放图中找到。延迟结束前，预测器只能用已返回的信息。隐藏状态后，由该点算出的速度、平滑轨迹和使用未来片段终点的特征也会泄露答案。容差可联系训练记录中的运动尺度与可接受误差，并同时展示容差变化的影响。方差触发文献讨论“少请求”与估计误差的关系，其通信模型与本题的固定回放协议有各自的假设。对高度相关的帧给出预测区间时，逐环境的经验覆盖比直接套用独立样本的覆盖保证更容易审查。[事件触发估计的原研究](https://mural.maynoothuniversity.ie/id/eprint/11464/) · [区间评分及宽度](https://sites.stat.washington.edu/raftery/Research/PDF/Gneiting2007jasa.pdf) · [非交换数据的覆盖条件](https://jmlr.org/papers/v25/23-1553.html)

## 第三问的共同实验

每个作业对应一个标注区间，一个区间可能有多个动作。案例中的服务区域、优先级、交期和人到机器的时长映射均由题设指定。原始片段时长预测是否准确，和某个策略在这些情景中获得多大服务收益，应分别评价。[第三问完整规程](../data/raw/C/Restocking_Tasks_and_Constraints/Problem_3_Requirements.pdf)

| 案例 | 作业 | 时间上限 H | 总成本预算 | 付费复核上限 | 有前置要求的作业 |
| --- | ---: | ---: | ---: | ---: | ---: |
| S1 | 8 | 100 s | 32 | 4 | 0 |
| S2 | 12 | 100 s | 34 | 6 | 2 |
| S3 | 16 | 125 s | 40 | 8 | 3 |

**抽样。** 每个案例独立从 Z0、时刻 0 开始。按 `job_id` 排序，从对应划分、对应作业族的合格片段中均匀有放回抽样，保留源索引的行顺序。参考生成器是 Python 3 的 `random.Random(int(seed)).choice`，每个案例重新初始化。每种方法在 3 案例 × 5 种映射 × 30 个公开验证种子下共 450 次验证。同一案例、映射、种子使用相同抽样。五种映射采用 p=αd+β，名义值为 (1,0.5)。固定 β=0.5 时另取 α=0.75、1.5，固定 α=1 时另取 β=0、1。最终测试另有组委会冻结的 30 个种子，公开验证种子只用于相应的验证试验。[共同条件 JSON](../data/raw/C/Restocking_Tasks_and_Constraints/common_conditions.json) · [规程第 1、4 节](../data/raw/C/Restocking_Tasks_and_Constraints/Problem_3_Requirements.pdf)

**信息。** 作业释放时免费披露起始 0.2 s 数值前缀，两次可选复核依次扩展到累计 0.6、1.0 s。每次占用设备 1 个情景秒并花费 1 个成本单位。等待本身不会增加可见前缀。复核已释放且尚未尝试的作业时可以尚未完成其前置作业，真正执行时才要求前置已完成。可见内容是允许的双手、头部、头部相机、质量与时间数值。源 ID、整段文字、未来片段终点、后续样本和全视频不能进入当前共同策略。模拟器可保管完整事实，决策端只读取已返回字段。做消融时可分别比较时长信息、复核安排和作业次序的作用。[规程第 2 节](../data/raw/C/Restocking_Tasks_and_Constraints/Problem_3_Requirements.pdf) · [Howard 的信息价值理论](https://doi.org/10.1109/TSSC.1966.300074)

**时间和成本。** 名义服务时长为原始时长加 0.5 s。设实际服务为 p，预留为 b，开始为 s。作业完成时刻是 s+p，设备下次可用时刻是 s+max(p,b)。提前完成仍占用剩余预留，超时会阻塞后续作业。移动距离 D 取情景区域间欧氏距离，每次执行加 0.5 s 切换，同区也要计。执行前用预留区间和预算验收，即 b≥0.2、s+b≤H，执行费用为 2+0.1b+0.2D。跨 H 的已接受作业按截尾处理，已收费用保留。执行、复核与等待互斥，非法动作终止该次试验并保留违规记录。[规程第 3 节](../data/raw/C/Restocking_Tasks_and_Constraints/Problem_3_Requirements.pdf) · [任务及前置关系](../data/raw/C/Restocking_Tasks_and_Constraints/tasks.csv)

**方法与指标。** 随机服务时长的预约调度文献讨论预留过长造成空闲、预留过短造成后续延迟。作为比较起点，文献中常见的经验时长分位数、按交期的优先规则、有限前瞻和信息价值问题各有明确解释。具体选择取决于训练池规模、可见前缀是否增加预测信息，以及是否有足够样本支持复杂策略。共同规程要求逐试验报告加权完成率、加权按时率、加权拖期、成本、复核数、已用时间、观察到的超时、预留空闲、截尾项数和违规情况。服务率分母覆盖全体作业，未完成项的拖期按 H 计，加权拖期为加权和。以成本或产能风险为主目标时，名义条件下每个案例平均加权按时率须达到 0.50，未达到的案例也须披露。比较结果按案例报告同种子的差值及波动，过程日志要能追溯信息与资源变化。[Denton 与 Gupta 的随机预约调度研究](https://doi.org/10.1080/07408170304395) · [规程第 4–5 节](../data/raw/C/Restocking_Tasks_and_Constraints/Problem_3_Requirements.pdf)

## 选题所需的最小证据

三人团队可用三项能力判断能否承担 C 题。一是能从三个来源的两种文件组织中复现同一状态、同一质量口径并给出剔除清单。二是能让两种策略共享窗口、预算和延迟，用一张时间图证明没有用到未来数据。三是能从任一试验的日志独立重算花费、完成与占用，并解释一次延迟返回或跨 H 截尾。若第三项没人负责，前两问的预测图再丰富也补不上共同评测的缺口。

附件盘点可在仓库根目录运行 `python experiments/check_c_data.py` 复做，需要 pandas 和 pyarrow。本次核查使用 Python 3.11 环境、pandas 2.3.3、pyarrow 22.0.0。脚本逐条匹配内部 ID、清点时间戳及标记、核对环境划分与训练片段分布。本地 C 原始数据按仓库约定不进 Git。[核查脚本](../experiments/check_c_data.py) · [仓库目录说明](../README.md)
