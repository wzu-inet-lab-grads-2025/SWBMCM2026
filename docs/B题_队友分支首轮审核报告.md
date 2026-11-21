# B 题四问审核报告 · `explore/b-first-round`

2026-11-21 · 队内复核

四问的归档主结果均已复现。**第一问通过，第二问全精度方案通过，第三问须统一正式答题口径，第四问归档面板通过、通用计划接口须补验。** 最紧迫的两项是二（2）的站位交付精度和二（2）、四问共用回放器的连续车距检查。数值最快方案的两个 T2 站取到毫米即失去视线。通用回放器则能接受车体已经重叠的计划并正常计分。两套名义计划、30 套基线和 882 条第四问归档轨迹的连续车距均通过独立复核。 [约束复核底稿][peer-constraints] · [反例记录][plan-probes] · [连续车距和计分扫描][scan]

| 小问 | 归档验收 | 提交或合并前的处理 |
| --- | --- | --- |
| 一 | 可观测域、最优弧与 V4 并列择优通过 | 用公式定义 80° 边界站，坐标表标明显示精度。 |
| 二（1） | 达到一阶精度下界，两车均满足 30 s 行驶限制 | 保留精确构型，区分理论坐标、实用坐标与抽样分位数。 |
| 二（2） | 两套全精度计划、连续数值上下界和就近对照通过 | 最快站位关联全精度文件。需要定长小数下发时，使用已回放验证的坐标。 |
| 三 | 两套概率和阈值通过，主结果标签有冲突 | 原题的指令偏差主答用 **73.936066 m**，三类误差合成另报 **66.837258 m**。 |
| 四 | 126 个主情景的轨迹和启动得分通过 | 通用入口补车距和同刻锁定检查，注明断联同步协议。正式比较同时列严格保持与只追加基线。 |

## 审核对象与依据

- 冻结快照为 `30d54fa397c370742cd34e5cd79808ebdb59ae46`，Git 差异基点为 `e87e58220516e236267d42a89a1eb72031bc996f`。分支已经检出为本地跟踪分支，四问位于同一快照。结果索引标注的归档求解提交是 `0188fda1f926f248b8a7ed113376b654f613f0af`，其 `src/`、`configs/`、`checks/` 与审核快照一致。跨分支复算固定使用 `yyl-b1@38bb3c7a899ab8b5d0bc7d66eb8f4724afa4129b`。 [GitHub 固定快照][peer-git] · [分支 README][peer-readme] · [正式结果索引][results] · [跨分支说明][cross-doc]
- 按 **README → 验收与建模文档 → 求解与核验代码 → 结果及独立复算** 的顺序审查。用 `code-review` 的 Spec 与 Standards 双轴复核。两支英文题面是同一 Git 对象 `7ef7ffa1e8fc9d029b299f88e0070000e1428f78`。通信恢复范围另以官网指定论坛中实际渲染的命题人置顶回复核实。 [英文原题][problem] · [开赛公告][official] · [命题人回复][official-outage] · [逐问验收][acceptance] · [分支决策][peer-decisions]
- **题面条件。** 车辆为半径 1.5 m 的圆盘，全程留在场内且避开建筑内部。车心在 `[1.5,98.5]²`，到楼体足迹的欧氏距离至少 1.5 m。车体与场界、楼体外缘可相切。相机离地 2 m，有限三维视线触及闭楼体即遮挡，仰角 `[-10°,80°]` 含端点。车辆以 3 m/s 行驶，途中云台保持正东水平。停车后两轴可并行以 30°/s 指向，一次转向取两轴用时的最大值，水平轴按题面未限定转角范围采用最短转角。两车均就绪后同步观测 5 s，各自在完成自身复位后再次出发。二（1）的 30 s 限制只作用于该问的单车行驶时长。除第三问随机误差计算外，各问使用标称参数。 [英文原题第 1–4 页][problem] · [场景转录][scene] · [几何实现][geometry] · [时间轴实现][timeline]
- **题设对象推出的验收条件。** 全程任意两辆仍在场车辆的车心距至少 3 m。双站须为两辆不同车辆，且两条水平线可唯一交会。同车占用不可重叠，取消旧任务后从当时的真实位置与云台姿态接续。第四问只有通信允许时新指令才可生效。题面省略加减速、车辆转向和避碰耗时，因而名义模型可按分段恒速复核车距。 [英文原题第 1–5 页][problem] · [原始时间轴和接续][timeline] · [断联与命令回放][replay]

## 一 · T1 可观测域与最优站位

**通过。** 分支的区域定义同时使用圆盘扫掠几何、真实高度楼体的闭遮挡和仰角区间。相机面阴影由楼体足迹与屋顶投影的凸包给出，直接三维线段裁剪及一维线性规划复核均无判定差异。复跑通过 37 个边界算例，并重新生成了全场图和最优圆弧放大图。连续截面积分估计约 **5608.77 m²**。两支在固定 0.125 m 网格的 640,000 个单元中心逐点核对，车体、视线、仰角及三者交集均无差异，共 **358,908** 个可观测单元中心。 [一问报告][q1-doc] · [几何与解析求解][q1-code] · [正式区域图][q1-figure] · [绘图复跑日志][q1-log] · [跨分支网格复算][q1-cross-log]

由高度差 43 m 和仰角上限直接得到 $r\ge43\cot80^\circ$。正东点可行，故最小距离为 **7.582060170 m**，$Q^*=0.131890275\,{\rm m}^{-1}$，仰角 **80°**。最优圆周上仅 B2 的车体排除区切去一段。由 $y=72.5$ 与 $x=80.5$ 两条切边求得角域 `[0°,160.747873328°] ∪ [236.896046854°,360°)`。两切点可取，剩余弧上视线均与闭楼体分离。取 V4 在最优圆上的径向投影 $(91.155710853,66.121032434)$ m，可行直达路长 **3.672741496 m**，达到五车中最小的反三角不等式下界。`yyl-b1` 在同一最优弧上按最大化车体净空择优，故两支推荐坐标不同。圆周公式给出精确站。毫米显示点的仰角低约 0.000343°，是可行近似坐标。 [最优弧证书与并列证明][q1-doc] · [结果 JSON][q1-result] · [跨分支择优][cross-doc] · [高精度边界复核][probes]

## 二（1） · T2 双站定位精度

**通过。** 取独立测角标准差 $\sigma=\pi/180$ rad，以一阶水平 RMSE $E=\sigma\sqrt{r_1^2+r_2^2}/|\sin\gamma|$ 衡量精度。它是线性化后两轴位置方差之和的平方根，对坐标旋转不变。基线由 $B^2=r_1^2+r_2^2-2r_1r_2\cos\gamma$ 进入模型。可行半径下界为 $r_0=33\cot80^\circ=5.818790363$ m。两站同时取 $r_i=r_0$ 且 $\gamma=90^\circ$，达到 **0.143623358 m** 的解析下界，基线为 **8.229012248 m**。方位差趋近 0° 或 180° 时误差放大，应重新选站，共线时剔除该站对。在 80° 仰角边界，正交构型的交会仍满秩。无界高斯测角下，非线性直线交点在近平行处有长尾，归档中的精度结论适用于明示的一阶指标。 [模型与证明][q2-doc] · [数值模型与退化分析][q2-num-doc]

| 构型 | V3 坐标约值 / 行驶 | V5 坐标约值 / 行驶 | 双车同时部署的最小车心距 |
| --- | --- | --- | ---: |
| 解析 | (5.21006647, 72.50000000) m / 5.369802 s | (11.02885683, 78.31879036) m / 13.881594 s | 8.229012 m |
| 数值 | (5.56098246, 74.49014351) m / 4.757378 s | (13.01900034, 77.96787437) m / 13.306469 s | 8.229012 m |

两套构型的直线路段、三维视线和行驶上限均通过复核。解析站精确地取 T2 水平投影向西、向北各平移 $r_0$，仰角在边界取等。上述解析表的两站按八位小数直接录入时，仰角分别超过 80° 约 $6.20\times10^{-9}$°、$5.69\times10^{-9}$°。数值构型的 V3 站取到毫米时超过约 $1.10\times10^{-4}$°。可执行坐标应按留量方案复核。 [解析构型][q2-analytic] · [数值构型][q2-numerical] · [65 位几何与行驶核验][precision-log]

分支的百万次非线性交会记录了种子和误差分位数，遗漏抽样区间。本次复算径向 95% 分位数为 **0.248772 m**，按二项秩次构造的约 95% 置信区间是 **[0.248425, 0.249138] m**。这个区间描述抽样误差。 [抽样程序][q21-simulation] · [区间复算][q21-sampling]

## 二（2） · 三告警并发、复用和路线

**全精度通过。** 两套计划均由 V3 先服务 T1，再在本车复位后服务 T3。正式时间轴用路径弧长、实际停靠与转向重建共同开始，记录对车等待、观测和复位。给每条行驶折线按 3 m/s 定时，将两车轨迹切成同步匀速区间，对相对位置的平方距离在每段上求极小值。全精度数值、解析方案的连续最小车心距分别为 **33.516716 m、33.516709 m**。30 套正式基线也通过同一检查。 [正式时间轴][timeline] · [独立回放脚本][q2-check] · [连续车距扫描][scan]

| 方案 | T1 车对 / 开始 (s) | T2 车对 / 开始 (s) | T3 车对 / 开始 (s) |
| --- | --- | --- | --- |
| 数值 | V3、V5 / 1.532032715 | V2、V4 / 10.197744025 | V1、V3 / **10.352118591** |
| 解析 | V3、V5 / 1.532032714 | V1、V4 / 6.962785711 | V2、V3 / **10.352122409** |

**最优性范围。** 至少一次复用给出 30 种车和前后任务组合的必要条件。解析覆盖复跑用替代的转向界和内缩楼体判定，核验 34 个根区域、1,107,752 个盒，排除了早于 **10.352051000 s** 的解。与可行数值方案合起来得到 **[10.352051000, 10.352118591] s**，宽约 **67.6 µs**。另一路固定 80 万次细分的数值下界复跑为 **10.352046448 s**，其本次可行上界为 **10.352118589 s**，间隙约 **72.1 µs**。两者均是带保守浮点余量的数值界。视线排除闭楼体使部分站位边界开放，宜将该结论表述为连续域下确界的夹逼。九站枚举的最优性只覆盖该候选集及所列可行直线路段。 [连续覆盖复核][q2-cover] · [解析复跑][q2-analytic-log] · [数值重跑][q2-numeric-log] · [两份归档计划][q22-result] · [解析路线归档][q22-analytic-result]

**就近基线成立。** 六种处理顺序、五种规则在同一组 12 个候选站和可行直线路段上比较。按 T1、T2、T3 处理时，两车新增路程之和与最长单车新增路程都能取零。按编号破并列为 **22.332111 s**，按共同就绪破并列为 **10.968984 s**，直接按当前告警最早就绪贪心为 **10.829586 s**。数值计划相对后两者分别提前 **0.616866 s** 与 **0.477467 s**。解析计划的车队总行程约 6.838 m，数值计划为 24.134 m，两者最晚开测近同，差异来自决定末次开测的 V3 复用链和并行 T2 车对。 [规则与并列约定][peer-decisions] · [基线代码][baselines-code] · [逐方案比较][q22-baselines]

`yyl-b1` 的优化计划也由 V3 复用，复算最晚 **10.352848885 s**，与本支数值计划相差 **0.730 ms**。前者让 V1、V2 服务 T2，V3、V4 服务 T3。开测目标接近的初始计划仍可能因车对分工不同而改变第四问故障后的可用资源。 [跨分支排程对照][cross-doc] · [复跑时间][cross-log]

**可执行坐标有实质影响。** 高精度闭楼体裁剪复核确认，最快数值方案取毫米位后，V2 的 T2 站 `(72.709,1.500)` 被 B5 遮挡，V4 的 T2 站 `(88.062,70.800)` 被 B2 遮挡。取六位小数时，V2/T2 仍被 B5 遮挡，V3/T3 落入 B3、B5 的闭遮挡。原始全精度站均通过，V2 的全精度车体近乎贴南场界。毫米坐标表应与原精度计划或经重新检查的留量计划对应。若正式更换二（2）排程，应从同一份新计划重跑四问两策略。 [约束复核报告][peer-constraints] · [独立裁剪记录][probes] · [留量计划复跑][precision-log]

## 三 · 指令角误差和实际指向误差

**数值通过，答题口径待统一。** 将真视线旋转至正东，令 $W$ 折回 `[-180°,180°)`。第 4 页的指令偏差为 $\Delta\psi=W[\frac{180^\circ}{\pi}\operatorname{atan2}(-\delta_y,40-\delta_x)]$。第 5 页要求比较三类误差，对实际指向另用 $W(\Delta\psi+\epsilon_0+\epsilon_c)$，两独立云台项合成标准差 1°。覆盖率随每轴定位标准差严格下降，故 80 m 条件为 $a,b\ge0$ 且 $a+80b\le\sigma_*$，$\sigma_*$ 取相应覆盖率 95% 的根。 [英文原题第 4–5 页][problem] · [误差模型与单调性][q3-quad-doc]

| 评价量 | 95% 临界累计里程 (m) | 临界每轴标准差 $\sigma_*$ (m) | 按原定位参数行驶 80 m 的覆盖率 | 固定 $a=0.3$ m 时 $b$ 上限 (m/m) |
| --- | ---: | ---: | ---: | ---: |
| 题面 $\Delta\psi$，指令角 | **73.936065596** | 1.778721312 | 93.347323% | 0.0184840164 |
| 三项误差合成，实际指向 | **66.837258428** | 1.636745169 | 91.506823% | 0.0167093146 |

公开结果索引列了两套值。归档 `compact.json` 的 `main` 值仍为 `actual`，专题文档也把对应的 $b$ 和厘米限程写成“主口径”。正文明示原题符号和结论顺序即可消除冲突。若按整厘米执行，阈值应分别向下取为 **73.93 m、66.83 m**。 [第三问文档][q3-doc] · [正式机器可读结果][q3-compact] · [总索引][results]

完整 `atan2` 圆周积分、条件积分与独立半平面概率复算吻合。400 万组样本估根，再用 400 万组独立样本验证。指令角与实际指向的仿真里程约为 73.928 m、66.851 m，近似 95% 置信区间分别为 [73.8449, 74.0112] m、[66.7598, 66.9417] m，均包含确定性根。一阶近似分别高估限程 0.113 m、0.085 m。实际指向的一阶方差预算中，出发时零位、控制和定位分别占 54.02%、30.39%、15.59%。行驶 80 m 时定位占 88.10%，可据此解释短程校准和长程定位漂移的相对影响。 [积分与求根][q3-code] · [仿真统计及置信区间][q3-doc] · [独立复跑][q3-log] · [半平面概率复算][sensitivity-log]

## 四 · 断联、故障和新增告警下的重调度

**主面板通过。** 两策略用同一份二（2）数值计划，5 s 扰动，故障车退出且按题面不占路。新增告警进入 $N$，双车同步开始记入 $k$，先比 $k/N$ 再比 $t_{\rm last}$，未全启时用 60 s。原指令在 0 s 完整下发。策略仅于扰动后可通信时调用，可见信息为已知告警、车况及当时的轨迹状态。命题人确认恢复绝对时刻 $t_{\rm rec}\in(5,30]$，断联持续 $(0,25]$ s。全量下发和失联时可共同开测均是本轮协议假设。 [题面第 5 页][problem] · [命题人置顶][official-outage] · [断联口径核查][outage-clarification] · [共同口径][q4-contract] · [回放入口][replay-run]

126 场来自 6 个新增位置加无新增、5 个故障身份加无故障、10/30 s 恢复及无断联的全因子组合，覆盖七类非空扰动及一例无扰动。新增点以东为起点取 30°、150°、270° 三个方位，在东北、西北和正南覆盖不同的遮挡条件，各取 30 m、50 m 两个径向端点，高度 50 m。10 s 在下一批名义开测之前，30 s 在名义队列跑完之后。固定位置与通信条件内，五个故障身份各占 $1/5$。以下各行均值按设计清单等权。含故障场合，严格保持在五种身份下平均启动 2.2 项，无新增时条件平均启动率为 73.33%，有新增时为 55.00%。 [情景设计及概率说明][q4-design] · [逐情景统计][q4-main]

| 同一初始数值计划 | 全部告警开测 | 平均启动率 | 平均 $t_{\rm last}$ (s) | 最差 $t_{\rm last}$ (s) |
| --- | ---: | ---: | ---: | ---: |
| 严格保持原队列 | 6/126 | 61.111% | 57.636 | 60 |
| 保持原任务、健康空闲车可只追加 T4 | 42/126 | 82.540% | 47.587 | 60 |
| 在线重调度 | 126/126 | 100% | 23.993 | 35.385 |

相对严格保持，重调度在 **120 场提高开测数、6 场持平**，合计多启动 192 项，其中新增 T4 为 108 项，恢复原 T2/T3 各 42 项。只追加基线在新告警已公布且通信可用后，仅从已完成原队列的健康车中挑车，用同一站池和几何规则追加 T4。相对这条基线，重调度 **84 场提高开测数、16 场只缩短最晚时间、26 场持平**。表中 60 s 参与平均，是未全启场景的题定罚值。重调度保留原队列作为可选方案。35 个情景有原告警推迟，最大为 **13.473 s**。例如 `(270°,50 m,50 m)`、V3 故障且通信正常时，T2 从 10.198 s 推迟至 23.670 s，换得 T3、T4 也开测。 [策略细节与案例][q4-doc] · [只追加对照底表][append-csv] · [复算汇总][canonical]

主面板最晚一例为新增 `(270°,50 m,50 m)`、V1 故障、30 s 恢复。保持只启动 T1、T2。重调度用 V2、V3 在 34.583 s 启动 T3，用 V4、V5 在 35.385 s 启动 T4。面板外题内另例 `(25°,40 m,50 m)`、V3 故障、30 s 恢复，实际最晚 **37.747 s**。8 s、20 s 的各 42 场题内扩展全部全启，最晚分别为 23.670 s、25.385 s。35 s 恢复的 42 场属于域外压力测试，最晚 40.385 s。换用 `ready` 初始排程时，保持全启 6/126，改派全启 126/126，改派平均末次开测 24.014 s。`sum` 初始排程的保持组全启 9/126，平均启动率为 56.746%。首轮面板的最差值及全启结论只覆盖已计算的情景。 [正式主日志][q4-reschedule] · [扩展日志][q4-extend] · [分组核验][local-evidence] · [新增题内反例][plan-probes]

### 通用计划和协议的验收缺口

1. **高优先级 · 连续车距检查缺失。** `execute_jobs` 检查每车的整条路线、当前位置接续和同一任务的站对。用原始车位构造 V3 和 V5 相向互换沿线站位的输入，两车在 **6.112374 s** 同到 `(32,89.5)`，回放仍给出 `3/3`。另一份原生 `--plan` 让 V4 经楼外绕行在 **45 s** 到达 `(15,14)` 并为 T2 指向，此处 V1 完成 T3 后仍停放，回放也给出 `3/3`。整改验收应覆盖行驶、停驻和跨任务重叠，在任何计分前检查车辆全时域距离。已归档的 882 条轨迹全部通过该检查，主重调度最小车心距 **29.388509 m**。本支数值和跨分支使用的两份初始路线各 12 个路点均在对应私有矩形内。区域间距至少 6 m、矩形内直线与插值封闭，足以解释这两份初始计划下的安全隔离。独立事件审计器的原始范围明确排除了互撞。 [同任务站对代码][geometry-pair] · [通用回放代码][timeline] · [独立审计范围][audit-limit] · [相向车实测][probes] · [跨任务反例计划][collision-plan] · [反例结果][plan-probes] · [归档连续检查][scan] · [候选区检查][local-evidence]
2. **高优先级 · 5 s 同刻开测锁定不一致。** 计分用 `start<5` 标记扰动前锁定，策略快照却将 `t0<=now` 的观测纳入已开始集合。按归档规则，5 s 同刻应先处理扰动，未开测车辆仍可参与改派。将 V5 的 T1 路线经 `(44.832767330515,93.6)` 折返，使 T1 恰在 5 s 就绪。若同时新增 `(330°,40 m,50 m)` 且 V4 故障，结果标记 T1 未锁定，决策已把它排除。在同一份初始计划上，将同刻开测的任务纳入改派候选后，最晚开测由 **21.444 s** 提前至 **19.887 s**，均为 `4/4`。归档初始计划在第 5 秒没有开测事件。恢复恰与既有任务开测同刻时，当前实现先记录旧任务已开测。对 T3 原定开测时刻及其前后 1 ms 实测已验证锁定集合的切换。 [评分][replay-score] · [决策快照][replay-snapshot] · [同刻约定][q4-contract] · [5 s 反例][plan-probes] · [恢复同刻测试][probes]
3. **中优先级 · 断联中的双车共同触发依赖执行协议。** 0 s 预装队列只提供旧任务指令。以 V1 故障、30 s 恢复为例，代码会在失联中阻止失去搭档的 V3 开测。实际要实现这条规则，健康车需有共享时钟及搭档就绪确认，或明确独立车间链路与故障确认机制。如果断联使该确认不可用，一种可复算的做法是保留已下发的行驶和指向，推迟新的同步开测到恢复时。这一协议的影响在灵敏度表量化。 [旧队列回放][replay-queue] · [下发协议对照][protocol-log]
4. **中优先级 · 私有候选区限制通用输入。** 束宽 36，每车 4×4 网格加原站位和当前点。固定车对只保留当前告警的最早兼容站对，可能错过有利于后续复用的其他站对。任一健康车决策位置出其矩形即跳过全部改派。将 V1 的 T3 站换成合法 `(15,1.5)`，三个名义开测时刻保持相同。仅新增 `(30°,30 m,50 m)` 时，返回 `current_position_outside_candidate_area`，得 **3/4**。V3、V5 在原末站复位后追加即可得 **4/4、18.593 s**。README 已披露该限制，通用输入的结论应保留跳过搜索的原因和实际得分。 [README 的限制][peer-readme] · [候选与站对筛选][search] · [配置][search-config] · [实测记录][plan-probes]
5. **需定稿 · 60 s 端点。** 题面同时写评价窗 `[0,60]` 和 “before the end”。本分支闭区间计分，恰 `60.0` s 时计入 $k$，`nextafter(60,+∞)` 排除。`yyl-b1` 主口径严格用 `S<60`。七份归档均无端点启动，两个口径在这些主比较上的开测计分相同。建议正文声明选用哪条解释，并随验收保留两侧与恰好在端点的用例。 [题面第 5 页][problem] · [计分实现][replay-score] · [边界单测][cutoff-test] · [独立端点复算][probes] · [团队口径][team-decisions]

跨任务同站反例的复跑命令如下，从队友 worktree 根目录运行会得到 `k/N = 3/3`。逐车日志中，45 s 的 V1 停驻坐标与 V4 指向坐标均为 `(15,14)`。 [情景输入][collision-manifest] · [逐车回放][collision-replay]

```bash
nohup python3 -B src/run.py retain \
  --plan runs/b/audit-30d54fa/q4-overlap-plan.json \
  --manifest runs/b/audit-30d54fa/q4-overlap-manifest.json \
  --output runs/b/audit-30d54fa/q4-overlap-replay.jsonl
```

### 同初始计划的跨分支比较

在 `yyl-b1` 优化排程上复跑 1,751 场，125 场与首轮原面板共同。严格保持的两份分数逐场吻合，比较 CSV 与归档字节一致。本分支改派和 `yyl-b1` 逐场择优均为 **1,751/1,751** 全开测。两者平均 $t_{\rm last}$ 分别 **25.292 s、34.482 s**，最大值 **37.614 s、57.990 s**。本分支在 1,363 场更快，242 场持平，146 场更慢。在同清单上，`yyl-b1` 严格保持和只追加的全启数分别为 **7、583**。均值相差 **9.190 s**，包含旧站约束、候选站集、路径及搜索算法的共同影响。 [比较口径][cross-doc] · [配对代码][cross-code] · [复跑及汇总][cross-log] · [逐情景底表][cross-csv]

125 个共同扰动情景中，两支分别使用各自的初始排程时，末次开测均值为 24.103 s、33.741 s。统一用 `yyl-b1` 初始排程后，均值为 24.598 s、33.741 s。首轮主面板中的无扰动对照单独计入 126 场统计。 [共同情景对照][cross-doc] · [逐情景底表][cross-csv]

原告警未开测时，允许换站属于可比较的调度选择。固定旧站是 `yyl-b1` 主实验的算法约束。站位因素可用该分支已有的受控变体观察。例如新增 `(90°,50 m,50 m)`、V2 故障、30 s 恢复，固定站为 **57.990 s**，同一实现放开旧站得 **36.373 s**，本分支得 **34.663 s**。这个单例支持放宽站位能改善特定瓶颈。跨分支复跑重算本分支策略，另一个分支的推荐解和只追加分数取自它的固定版本归档。 [站位变体底表][peer-stress] · [配对输入和输出逻辑][cross-code]

## 独立灵敏度分析

| 条件变化 | 复算结果与解释 |
| --- | --- |
| 一、二（1）仰角上限从 80° 收紧到 79° | 第一问最小站距 **8.358353 m**，$Q^*=0.119640791\,{\rm m}^{-1}$。保持二（1）的正交等距构型时，一阶精度变为 **0.158328309 m**，两车行驶约 5.481 s、13.813 s，仍在 30 s 内。该变化模拟额外机械余量。 |
| 二（1）两站径向外移 1 mm | 归档两站保持正交且可直达，一阶 RMSE 变为 **0.143648041 m**。原题保留闭 80°。 |
| 二（2）给 V2/T2、V4/T2、V3/T3 相应的约 1 mm 光学和场界留量 | 按毫米坐标复跑，三个开测时刻约为 **1.532033、10.198037、10.352584 s**。最晚只增加约 **0.466 ms**，所有视线通过 65 位闭楼体复算。 |
| 二（2）解析路线给 0.1 m 光学留量 | 按毫米坐标复跑，开测 **1.532122、6.962786、10.391271 s**，比最快名义解多 **0.039153 s**。留量按车心平面到遮挡阴影计。 |
| 四问按窗口内双车交叠时长核采集 | 主重调度的 **486** 次开测中 **444** 次采满。全部任务均双车采满 5 s 的情景为 **84/126**，严格保持为 **3/126**。另外 42 场均为 T1 的一辆车在 5 s 故障，从 1.532033 s 开测起共同采集约 **3.468 s**，仍按题意锁定得分。健康搭档按原观测结束时刻复位。 |
| 四问添加 5° 交会角余量门槛 | 以二（2）数值计划起步时，主重调度交会角距 0° 或 180° 的最小余量为 **12.454°**，通过该工程门槛。换用 `ready` 初始计划时，改派组的最小值为 **4.816°**。`yyl-b1` 更广面板中有 30 个 T4 方案不达门槛，已有复算在这 30 场保持全启，最多推迟约 0.617 s。 |
| 四问假设故障车在原位置继续占路 | 保留归档轨迹作事后连续检查。105 个有故障主情景均保有至少 3 m 的车心距，最小 **29.388509 m**。题面主分析使用故障车退出且不阻路。 |
| 四问附加端到端决策及发令延迟 1、3、5、10 s | 旧队列在延迟段继续执行，之后按当时状态改派。四档均 **126/126** 全启，平均末次开测为 **24.526、25.298、26.291、28.996 s**，最差为 **36.385、38.385、40.385、45.385 s**。该检查覆盖首轮主面板。 |

留量、采集和延迟结果分别来自站位复跑、`observe` 区间交集和延后决策时的状态快照。对加减速、转弯与停车稳定时间，实车估算需速度剖面、转弯半径等设备参数。对告警坐标误差及相机捕获，需另有目标误差域和视场数据。 [站位高精度复算][precision-log] · [二（1）留量底表][q2-margin] · [采集与角度扫描][scan] · [跨分支交会角复算记录][peer-constraints] · [停障与延迟复算][sensitivity-log] · [延迟逐场底表][delay-csv]

断联协议的三条对照均沿用同一份初始排程、126 场主清单和同型重调度器。表内“纯断联”取 30 s 恢复。只下发当前任务的版本在完成当前任务及复位后等待发送后续指令。需联网触发的版本保留预装的行驶、指向，并在恢复时允许新同步开测。 [两策略逐场对照][protocol-csv] · [协议复算摘要][protocol-log]

| 两策略共同采用的协议 | 纯断联保持 / 改派 (s) | 保持全启 | 改派全启 | 改派平均 / 最差 (s) |
| --- | ---: | ---: | ---: | ---: |
| 预装全队列，离线可同步 | 10.352 / 10.352 | 6/126 | 126/126 | 23.993 / 35.385 |
| 每次只下发当前任务 | 32.788 / 32.766 | 6/126 | 126/126 | 25.639 / 43.448 |
| 预装全队列，新开测需联网确认 | 30.000 / 30.000 | 6/126 | 126/126 | 26.423 / 44.829 |

## Standards · 复现、接口与合并规范

**分支自身约定。** `experiments/README.md` 规定实验输出写 `runs/`。Q3 求积与仿真的部分默认复跑命令会直接覆盖跟踪中的 JSON 或报告，Q4 探索版 `reproduce.sh` 的末步也会改写跟踪证据。正式索引还缺 `ready` 改派、`sum` 保持和归档压缩步骤，跨分支索引里的 Q1 网格对照缺直接复跑入口。应补齐命令及显式输出路径。计划加载器按存在 `q22` 优先走旧快照解析。标为 `b.plan.v1` 的文档如果同时有 `q22.jobs`，即使声明的 `scene_sha256` 与当前场景不一致也会读入，并以当前哈希返回 `Plan`。这会让归档掩盖计划与场景的版本错配。合并前应先按格式选择解析器，再核验原生场景哈希。 [实验约定][experiments-readme] · [求积默认输出][q3-output] · [仿真默认输出][q3-sim-output] · [探索复跑末步][q4-verify] · [结果复跑索引][results] · [计划加载器][plan-loader]

**当前团队合并约定。** 两支都新增了 `src/run.py`。当前团队入口按 `q1` 到 `q4` 分派，队友入口只有二（2）和四问。直接采用队友入口会破坏现有复跑方式。迁入正式实现时还要迁移场景和计划依赖，正式配置当前直接引用探索目录输入。正式产物按团队约定需在 `results/qN/` 下带有含义的运行子目录，齐备底表、图、运行记录和中文结论。当前分支的部分图和底表在探索区、逐场完整日志平铺在正式区。关键代码还需补中文模型口径和容差说明。可复用的观测结束与复位释放规则目前在公共时间轴、基线和束搜索各算一遍，后续统一该规则时应核对三处回归。 [团队 README][team-readme] · [团队规范][team-rules] · [分支入口][peer-cli] · [正式配置][q22-config] · [圆盘路段检查][geometry] · [基线][baselines-code] · [束搜索][search]

## 实际执行与交付记录

- Linux、Python 3.13.9、NumPy 2.3.5、SciPy 1.17.0。分支正式单测 15 项、情景 14 项、独立事件审计 29 项、第三问解析与仿真单测 6+11 项通过。第一问 37 项边界检查通过。二（2）数值路线独立回放记录 8,030 项检查通过，并复核 90 套探索基线。 [第一问重跑][q1-log] · [第二问重跑][q2-numeric-log] · [第三问重跑][q3-log] · [正式复跑][formal-log]
- 19 份结果文件的 SHA-256 校验通过。七份第四问归档各 126 条，复跑数据字段逐条相等，比较时忽略生成来源字段。主和扩展共 252 对归档分数通过独立成对事件审计。二（2）的两套名义回放、五种正式基线和 30 套对照表的数据字段也一致。1,751 行跨分支输出与冻结 CSV 字节一致。第一问的局部求解运行记录和连续面积浮点末位会随环境变化。 [归档校验清单][sums] · [逐文件复跑对账][canonical] · [三条补跑的基线][baselines-extra] · [跨分支日志][cross-log]
- Spec 和 Standards 已分别独立审查，本报告再对几何、概率、车距、起止时刻与对照策略作复算。Q4 全域最坏情景、实车参数和实测网络时延超出当前证据范围。 [对照覆盖说明][cross-doc] · [情景设计范围][q4-design]
- 队友分支路径为 `/mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round`。审核证据位于其 `runs/b/audit-30d54fa/`，由 Git 忽略。报告保存在 `yyl-b1` 的 `docs/`。审核 worktree 干净，报告未暂存、提交或推送。 [复跑及反例目录][evidence-dir]

[problem]: </mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/problem/B/2026_“ShuWei Cup”B_Problem.pdf>
[official]: https://www.nmmcm.org.cn/News/225.html
[official-outage]: https://pd.qq.com/g/pd52619943/post/B_e4b7ff6a0ac20e001441152187121863520X60?subc=745397906
[peer-git]: https://github.com/wzu-inet-lab-grads-2025/SWBMCM2026/tree/30d54fa397c370742cd34e5cd79808ebdb59ae46
[peer-readme]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/README.md
[results]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/results/README.md
[sums]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/results/SHA256SUMS
[acceptance]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/docs/b-acceptance.md
[peer-decisions]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/docs/decisions.md
[peer-constraints]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/docs/b-constraint-review.md
[scene]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/experiments/b/scene.json
[geometry]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/src/shared/geometry.py:39
[geometry-pair]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/src/shared/geometry.py:78
[timeline]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/src/shared/timeline.py:90
[plan-loader]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/src/shared/plan.py:28
[peer-cli]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/src/run.py:171
[q1-doc]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/experiments/b/q1_independent/REPORT.md
[q1-code]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/experiments/b/q1_independent/solve.py:170
[q1-result]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/results/q1/result.json
[q1-figure]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/experiments/b/snapshots/q1/observable_region.png
[q2-doc]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/docs/q2-independent.md
[q2-num-doc]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/experiments/b/q2_numerical/REPORT.md:61
[q2-analytic]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/experiments/b/snapshots/q2_analytic/summary.json
[q2-numerical]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/experiments/b/snapshots/q2_numerical/summary.json
[q2-margin]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/experiments/b/snapshots/q2_numerical/sensitivity.json
[q21-simulation]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/experiments/b/q2_numerical/search.py:169
[q2-check]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/experiments/b/check_q2_solutions.py
[q2-cover]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/experiments/b/check_q2_cover.py:65
[q22-result]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/results/q2/q22-numerical.json
[q22-analytic-result]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/results/q2/q22-analytic.json
[q22-baselines]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/results/q2/q22-baselines-comparison.json
[q22-config]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/configs/b/q22-numerical.json:3
[baselines-code]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/src/q2/baselines.py:67
[q3-doc]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/docs/q3-independent.md
[q3-compact]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/results/q3/compact.json
[q3-quad-doc]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/experiments/b/q3_quadrature/README.md:25
[q3-code]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/experiments/b/q3_quadrature/solve.py:152
[q3-output]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/experiments/b/q3_quadrature/solve.py:244
[q3-sim-output]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/experiments/b/q3_simulation/simulation.py:506
[q4-doc]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/docs/q4-comparison.md
[q4-main]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/experiments/b/snapshots/q4_comparison/main.csv
[q4-contract]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/experiments/b/q4/README.md:5
[outage-clarification]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/experiments/b/q4_scenarios/CLARIFICATION.md:21
[q4-design]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/experiments/b/q4_scenarios/README.md:21
[q4-reschedule]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/results/q4/q4-reschedule-q22-numerical-manifest.jsonl.gz
[q4-extend]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/results/q4/q4-reschedule-q22-numerical-sensitivity.jsonl.gz
[replay]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/src/q4/replay.py
[replay-score]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/src/q4/replay.py:65
[replay-snapshot]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/src/q4/replay.py:83
[replay-queue]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/src/q4/replay.py:129
[replay-run]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/src/q4/replay.py:202
[search]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/src/q4/reschedule.py:49
[search-config]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/configs/b/q4-reschedule.json
[audit-limit]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/experiments/b/q4_audit/README.md:81
[cutoff-test]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/checks/test_retention.py:58
[q4-verify]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/experiments/b/q4_reschedule/verify.py:193
[experiments-readme]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/experiments/README.md
[cross-doc]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/docs/b-peer-crosscheck.md
[cross-code]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/experiments/b/crosscheck_yyl.py:96
[cross-csv]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/experiments/b/snapshots/yyl_crosscheck/q4.csv
[peer-stress]: /mnt/disk/yuanyilun/projects/SWBMCM2026/results/q4/扰动重调度_四模型择优_20261120/paired_stress.csv:35
[team-readme]: /mnt/disk/yuanyilun/projects/SWBMCM2026/README.md:39
[team-rules]: /mnt/disk/yuanyilun/projects/SWBMCM2026/AGENTS.md:32
[team-decisions]: /mnt/disk/yuanyilun/projects/SWBMCM2026/docs/decisions.md
[evidence-dir]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/runs/b/audit-30d54fa/
[q1-log]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/runs/b/audit-30d54fa/q1-plot.log
[q1-cross-log]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/runs/b/audit-30d54fa/q1-grid-crosscheck.log
[q2-analytic-log]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/runs/b/audit-30d54fa/q2-analytic.log
[q2-numeric-log]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/runs/b/audit-30d54fa/q2-numerical.log
[q3-log]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/runs/b/audit-30d54fa/q3.log
[formal-log]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/runs/b/audit-30d54fa/q4-formal.log
[canonical]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/runs/b/audit-30d54fa/canonical-check.log
[cross-log]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/runs/b/audit-30d54fa/q4-cross.log
[scan]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/runs/b/audit-30d54fa/separation-scan.log
[probes]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/runs/b/audit-30d54fa/targeted-probes.log
[plan-probes]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/runs/b/audit-30d54fa/plan-boundary-probes.log
[collision-plan]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/runs/b/audit-30d54fa/q4-overlap-plan.json
[collision-manifest]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/runs/b/audit-30d54fa/q4-overlap-manifest.json
[collision-replay]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/runs/b/audit-30d54fa/q4-overlap-replay.jsonl
[local-evidence]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/runs/b/audit-30d54fa/local-evidence.log
[precision-log]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/runs/b/audit-30d54fa/precision-replay.log
[q21-sampling]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/runs/b/audit-30d54fa/q21-sampling.log
[baselines-extra]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/runs/b/audit-30d54fa/q22-extra-baselines.log
[append-csv]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/runs/b/audit-30d54fa/q4-append-comparison.csv
[protocol-log]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/runs/b/audit-30d54fa/q4-protocol-sensitivity.log
[protocol-csv]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/runs/b/audit-30d54fa/q4-protocol-sensitivity.csv
[sensitivity-log]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/runs/b/audit-30d54fa/sensitivity-probes.log
[delay-csv]: /mnt/disk/yuanyilun/projects/SWBMCM2026-worktrees/explore-b-first-round/runs/b/audit-30d54fa/q4-delay-sensitivity.csv
