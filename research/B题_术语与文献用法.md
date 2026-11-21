# B 题术语、文献与用词对照

**用途：** 给 `paper/demo.tex` 的续写提供三样东西：题面认可的英文术语、全文统一的符号、以及每条文献“能支撑什么、不能支撑什么”。重点不是列同义词，而是防止同一个概念在四问里换三个说法。

**适用范围：** 队友已经写好的段落不按本表重写；本表用于新增段落、图题表题、摘要和终稿审计。若队友的用词与题面一致，保留原词，不为了“统一”而改已定稿的句子。

**依据：** 英文原题五页（[PDF](../problem/B/2026_“ShuWei%20Cup”B_Problem.pdf)）、官方[格式规范](../problem/official/2026年第十二届数维杯全国大学生数学建模挑战赛（秋季赛）论文格式规范.pdf)、[AI 使用规定](https://www.nmmcm.org.cn/News/220.html)、`research/BC文献.bib` 和 `research/B题四问_约束复核.md`。术语的“题面原词”以英文 PDF 为准，不用中文翻译回译。

---

## 1. 用词总原则

1. **题面词优先。** 题目用 “observable region”、“bearing line”、“intersection angle”、“baseline length”、“joint observation”、“gimbal zero-offset error”、“completion ratio”、“latest observation start time”，正文首次出现就按这些词定义，不另造新词。
2. **一个概念一个词。** 不要一会儿 “station”、一会儿 “site”、一会儿 “vantage point”；不要一会儿 “bearing”、一会儿 “azimuth”、一会儿 “heading”。选定后全文一致。
3. **区分三类量。** 题面给定的量（$R$、$H$、$\theta$、$1^\circ$）、模型内部的量（$r_i$、$\gamma$、$\Sigma_{\rm lin}$）、算法/评价的量（$\eta$、$t_{\rm last}$、$k$）要在符号表里分开，正文不要混用。
4. **术语不是装饰。** 首次出现给英文全称和缩写，例如 “line of sight (LOS)”；之后用缩写。不要在每段重复全称。
5. **不用营销词。** “robust”、“optimal”、“significant”、“real-time”、“safe” 都有严格含义；没有定义或证据就换成具体说法（见第 7 节）。
6. **中文只留在队内文档。** 论文正文、图题、表题、坐标轴、图例、代码注释全部英文；图表中不能出现中文。

---

## 2. 逐问核心术语

### 2.1 场景与几何

| 中文 | 推荐英文（首次出现写法） | 避免 / 易混 | 说明 |
| --- | --- | --- | --- |
| 作业场地 | operational field | area, map | 100 m × 100 m，原点西南角 |
| 调度中心 | dispatch center | control center, base | $O=(50,50)$ |
| 告警 | alert | alarm, warning | 含 $(\theta,R,H)$ |
| 目标 | target; UAV target | object, intruder | 部署阶段静止 |
| 观测车 | observation vehicle | sensor, agent, UAV | 地面车，不是无人机 |
| 观测站 | observation station | site, point, position | 车心停放位置 |
| 可观测区域 | observable region | observability region, visibility region | 题面词；首次定义“the set of admissible station centers from which the target is observable” |
| 可观测站 | observable station | visible point | 同时满足三类约束 |
| 车心 | vehicle center | centroid, disk center | 相机在车心正上方 2 m |
| 等效圆盘 | equivalent disk of radius 1.5 m | circle, sphere | 二维圆盘，不是球 |
| 建筑 | building; building prism | block, obstacle | 轴对齐长方体 |
| 楼体底面 | building footprint | base, floor | 平面矩形 |
| 场界 | field boundary | border, edge | 车心到边界 $\ge1.5$ m |
| 净空 | clearance | margin, gap | 车体到楼底/场界的最近距离 |
| 视线 | line of sight (LOS) | sight line, ray | 三维闭线段 |
| 遮挡 | occlusion | blockage, shadowing | 与棱、面、顶点相切或接触都算 |
| 仰角 | elevation angle | pitch angle | 视线与水平面的夹角；云台的俯仰轴是 pitch axis |
| 方位角 | azimuth angle | bearing, heading | 从正东逆时针；bearing line 是方位线 |
| 基线长度 | baseline length $\ell_b$ | baseline $B$ | 题面图 2 用 $B$；正文建议 $\ell_b$，避免与建筑 $B_j$ 冲突 |
| 交会角 | intersection angle $\gamma$ | cut angle, parallax angle | 两条方位线水平投影的夹角 |
| 水平距离 | horizontal distance $r$ | range, distance | 站到目标的水平距离；不要用斜距 |
| 告警距离 | alert range $R$ | radius | 调度中心到目标的水平距离 |
| 三维斜距 | slant range | distance | 相机到目标的三维距离；Q1/Q2 不用它做 $Q$ |
| 视线投影 | horizontal projection of the LOS | shadow | 图 2 的红色点线 |
| 配置空间 | configuration space | free space | 圆盘车用 C-space 障碍 |
| 位形障碍 | C-space obstacle | expanded building | 楼底按车半径外扩 |
| 闵可夫斯基和/膨胀 | Minkowski sum / dilation | offset | 圆盘与矩形的外扩边界带圆角 |
| 等视域 | isovist | viewshed | 2D 可见多边形；只在文献背景用，不替换题面 observable region |

### 2.2 观测、误差与统计

| 中文 | 推荐英文（首次出现写法） | 避免 / 易混 | 说明 |
| --- | --- | --- | --- |
| 双站方位交会 | two-station bearing intersection | triangulation, trilateration | 题面词 “two-station bearing-intersection localization” |
| 方位线 | bearing line | ray, direction line | 每条线只给方向 |
| 测向 | direction finding; bearing measurement | localization | 测向是取方位，定位是求位置 |
| 测角误差 | bearing measurement error | angle noise | 题面 $N(0,(1^\circ)^2)$ |
| 定位误差 | localization error | position error | 估计位置与真实位置之差 |
| 位置估计 | position estimate | estimate | Q3 用 $\widehat P$ |
| 协方差 | covariance | variance | 二维矩阵 |
| 一阶传播 | first-order propagation | linear approximation | 只在名义点附近 |
| 雅可比 | Jacobian | gradient | $J_i$ 是方位对位置的偏导 |
| 信息矩阵 | information matrix | Fisher matrix | $J^{\mathsf T}J/\sigma_\alpha^2$ |
| 克拉美–罗下界 | Cramér–Rao lower bound (CRLB) | error bound | 只在无偏、正则条件下；不要把它当实际误差 |
| 几何精度因子 | geometric dilution of precision (GDOP) | precision | 可作为相关指标提一句，正文主指标仍是 $E_{\rm lin}$ |
| 误差椭圆 | error ellipse | confidence ellipse | 协方差椭圆；给覆盖概率才叫 coverage ellipse |
| 覆盖椭圆 | coverage ellipse | error ellipse | 明确覆盖概率（如 95%） |
| 投影正态 | projected normal distribution | wrapped normal | 二维高斯方向的角度分布；不是普通正态 |
| 折角 | wrapped to $[-180^\circ,180^\circ)$ | normalized, clipped | 用 $W(u)=((u+180)\bmod360)-180$ |
| 角度回绕 | angle wrapping | normalization | 不是把角度取绝对值 |
| 云台 | gimbal | camera mount, PTZ | 水平/俯仰两轴 |
| 云台零位偏差 | gimbal zero-offset error | zero error, bias | 题面词；可括注 boresight error |
| 云台控制误差 | gimbal control error | pointing noise | 题面词 |
| 指向偏差 | pointing deviation | pointing error | $\Delta\psi$ 是偏差，不是标准差 |
| 指令方位 | commanded bearing | desired angle | 按估计位置算出的方位 |
| 真实方位 | true bearing | actual angle | 按真实位置算出的方位 |
| 累计里程 | cumulative travel distance $s$ | path length, driving distance | 车辆从出发到当前的总行驶距离 |
| 标准差 | standard deviation $\sigma$ | error | 单坐标标准差 $0.3+0.02s$ m |
| 覆盖率 | coverage probability | confidence level | $\Pr(|\Delta\psi|\le5^\circ)$ |
| 临界值 | critical value $\sigma_*$ | threshold | 使覆盖率恰好 0.95 的 $\sigma$ |
| 达标率 | compliance probability | pass rate | 给定参数下满足 95% 的概率 |

### 2.3 调度、扰动与评价

| 中文 | 推荐英文（首次出现写法） | 避免 / 易混 | 说明 |
| --- | --- | --- | --- |
| 调度 / 排程 | scheduling / schedule | planning | scheduling 是过程，schedule 是时间表 |
| 路径 / 路线 | path / route | trajectory | path 是几何曲线，route 是站点顺序 |
| 分配 | assignment | allocation | 车与告警的配对 |
| 车辆复用 | vehicle reuse | sharing, pooling | 同一车依次服务多个告警 |
| 任务席位 | assignment slot | seat, position | 每个告警要两个席位 |
| 车次 | task leg / trip | task, job | 一辆车从一个站到下一个站的一次任务 |
| 接续 | succession / chain | sequence | 同一车的车次链 |
| 时间轴 | timeline | Gantt chart | 题面要求逐车完整时间轴 |
| 出发 | departure | start | 从当前站出发 |
| 到站 | arrival | reach | 到达观测站 |
| 云台转向 | gimbal slew | rotation, turn | 两轴并行，取较大时间 |
| 联合观测 | joint observation | simultaneous observation | 两车都就绪后持续 5 s |
| 云台复位 | gimbal reset | return, restore | 回到正东水平 |
| 最晚开测时间 | latest observation start time $t_{\rm last}$ | makespan, completion time | 题面词；不是最后完成时间 |
| 完成率 | completion ratio $\eta$ | success rate, completion rate | 题面定义 $\eta=k/N$ |
| 就近分配启发式 | nearest-assignment heuristic | greedy algorithm | 题面要求自定义；写清按什么距离、什么顺序、并列怎么破 |
| 基线方案 | baseline scheme | baseline | 与“基线长度”同词不同义；正文用 baseline scheme |
| 优化方案 | optimized scheme | optimal scheme | 除非证明全局最优，否则不叫 optimal |
| 下界 | lower bound | bound | 说明是连续松弛还是候选集 |
| 连续松弛 | continuous relaxation | relaxation | 放松离散/候选限制 |
| 间隙 | gap | error | 上界与下界之差 |
| 候选站 | candidate station | grid point | 有限候选，不等于连续域 |
| 离散时钟 | discrete clock | time step | 规划用 0.1 s，评分用连续时间 |
| 连续回放 | continuous replay | simulation | 逐段核验轨迹、相位、同步 |
| 快照 | snapshot | state | 5 s 或恢复时刻的状态 |
| 锁定前缀 | locked prefix | frozen prefix | 已开测告警不可改派 |
| 重调度 | rescheduling | re-planning | 扰动后重排 |
| 在线/事件触发 | online / event-triggered | real-time | 不是实车实时系统 |
| 保留原调度 | retain the original schedule | frozen schedule | 题面比较对象 |
| 追加 | append | insert | 不改原任务，只追加新告警 |
| 故障 | vehicle failure | fault, breakdown | 故障车退出服务、不阻路 |
| 断联 | communication outage | disconnection | 通信中断 |
| 恢复时刻 | recovery time $t_{\rm rec}$ | reconnection time | 题面答疑：$t_{\rm rec}\in(5,30]$ 是恢复时刻 |
| 预授权 | pre-authorization | pre-approval | 断联中可续做旧队列的执行假设 |
| 情景 | scenario | case, instance | 一次完整扰动实现 |
| 代表案例 | representative case | example | 从情景中挑出来解释的个例 |
| 结构化情景网格 | structured scenario grid | random sample | 方位 × 半径 × 故障 × 恢复时刻 |
| 等权汇总 | equal-weight aggregation | expectation | 不是概率加权 |
| 逐场配对 | paired comparison | comparison | 同一情景比较两个策略 |
| 全启 | all alerts started | full completion | 场级指标：$k=N$ |
| 计分罚值 | penalty value | observed time | 未全启时 $t_{\rm last}=60$ s 是罚值 |
| 评价窗口 | evaluation window | horizon | $[0,60]$ s |

### 2.4 证据与证明层次

| 中文 | 推荐英文 | 避免 / 易混 | 说明 |
| --- | --- | --- | --- |
| 全局最优 | global optimum | best | 需要目标函数、约束和证明 |
| 取等 | attainment; the bound is attained | achievement | 下界有可行点达到 |
| 见证点 | witness point | example | 证明可行性的具体点 |
| 证明 | proof | verification | 数学证明与计算核验分开 |
| 验证 / 核验 | verification | validation | 计算/实现核验用 verification |
| 独立回放 | independent replay | simulation | 从底表重算，不调用求解器 |
| 下界证书 | lower-bound certificate | proof | 计算证据，不夸成数学证明 |
| 灵敏度分析 | sensitivity analysis | robustness | 改变参数看结果变化 |
| 逐项移除 | source-removal test; contribution analysis | ablation | 去掉一类误差看概率变化 |
| 适用范围 | scope; limitations | future work | 明确不能外推到什么 |
| 标称 | nominal | ideal, normal | 题设参数、无扰动 |
| 工程补充 | engineering supplement | assumption | 队内加的 45 cm、3 m 等 |
| 可行 | feasible | valid | 满足约束 |
| 合法 | valid | feasible | 通过连续回放/规则检查 |

---

## 3. 全文符号表（建议）

续写时先按这张表登记，避免 Q1 的 $\mathcal V$ 与车辆 $V_k$、Q2 的 $B$ 与建筑 $B_j$ 冲突。符号一旦在队友稿中出现，保留原符号；新符号按此表补。

| 符号 | 含义 | 单位 | 首次出现 | 备注 |
| --- | --- | --- | --- | --- |
| $O$ | 调度中心 | m | Q1 | $(50,50)$ |
| $\mathcal B_j$ | 第 $j$ 栋建筑（含高度） | — | Q1 | 题面用 $B_1$–$B_5$；正文可用 $\mathcal B_j$ 避免与基线冲突 |
| $V_k$ | 第 $k$ 辆观测车 | — | Q2 | 初始位置给定 |
| $T_a$ | 第 $a$ 个告警目标 | — | Q1 | $a=1,2,3,4$ |
| $(\theta,R,H)$ | 告警方位、距离、高度 | °, m, m | Q1 | 题面原始参数 |
| $q$ / $T'$ | 目标水平投影 | m | Q1 | $T'$ 是题面图 2 的写法 |
| $P=(x,y)$ | 车心/观测站平面位置 | m | Q1 | 队友稿已用 |
| $\widehat P$ | 车位估计 | m | Q3 | 题面用 $\widehat P$ |
| $r_i$ | 站 $i$ 到目标的水平距离 | m | Q1 | 不要与告警距离 $R$ 混用 |
| $\alpha_i$ | 站 $i$ 的视向方位角 | rad/° | Q2(1) | 公式中统一弧度，结果可写度 |
| $\gamma$ | 交会角 | °/rad | Q2(1) | 两条方位线水平投影夹角 |
| $\ell_b$ | 基线长度 | m | Q2(1) | 题面图 2 用 $B$；正文建议 $\ell_b$ |
| $\sigma_\alpha$ | 单站测角标准差 | rad | Q2(1) | $1^\circ=\pi/180$ |
| $J_i$ | 方位对位置的雅可比行 | 1/m | Q2(1) | $(- \sin\alpha_i,\cos\alpha_i)/r_i$ |
| $\Sigma_{\rm lin}$ | 一阶位置协方差 | m$^2$ | Q2(1) | $\sigma_\alpha^2(J^{\mathsf T}J)^{-1}$ |
| $E_{\rm lin}$ | 一阶二维 RMSE | m | Q2(1) | $\sqrt{\operatorname{tr}\Sigma_{\rm lin}}$ |
| $Q$ | 观测质量 $1/r$ | m$^{-1}$ | Q1 | 题面指定，不是定位精度 |
| $\beta$ | 视线仰角 | ° | Q1 | $\operatorname{atan2}(H-h_c,r)$ |
| $\rho$ | 车体等效半径 | m | Q1 | 1.5 |
| $h_c$ | 相机高度 | m | Q1 | 2 |
| $g_{vj}$ | 车 $v$ 对告警 $j$ 的云台指向/复位用时 | s | Q2(2) | 两轴取较大值 |
| $L_{vj}$ | 车 $v$ 到告警 $j$ 站的路长 | m | Q2(2) | 避障路长，不是直线 |
| $d_{vj}$ | 车 $v$ 执行告警 $j$ 的出发时刻 | s | Q2(2) | 无前继为 0 |
| $S_j$ | 告警 $j$ 的联合开测时刻 | s | Q2(2) | $\max_v(\cdot)$ |
| $\eta$ | 完成率 | — | Q4 | $\eta=k/N$ |
| $N$ | 评价窗内告警总数 | — | Q4 | 含新告警 |
| $k$ | 成功开测的告警数 | — | Q4 | 两车都到齐并完成指向 |
| $t_{\rm last}$ | 最晚开测时间 | s | Q4 | 未全启按定义记 60 |
| $\Delta\psi$ | 指向偏差 | ° | Q3 | 指令角与真实角之差，折回 |
| $\sigma(s)$ | 单坐标定位标准差 | m | Q3 | $a+bs$ |
| $a,b$ | 定位标准差模型参数 | m, m/m | Q3 | 非负 |
| $\epsilon_0,\epsilon_c$ | 零位、控制误差 | ° | Q3 | 独立零均值 |
| $\kappa$ | $D/\sigma$ | — | Q3 | 投影正态参数 |
| $f_\kappa$ | 投影正态角度密度 | 1/rad | Q3 | 见 2.4 |
| $\Phi$ | 标准正态分布函数 | — | Q3 | 不要与 $\phi$ 混 |
| $W(u)$ | 角度折回函数 | ° | Q3 | $((u+180)\bmod360)-180$ |
| $t_{\rm rec}$ | 通信恢复时刻 | s | Q4 | $t_{\rm rec}\in(5,30]$ |
| $s$ | 累计行驶距离 | m | Q3 | 不是目标视距 |

**符号冲突提醒：**

- 题面用 $B$ 表示基线，又用 $B_1$–$B_5$ 表示建筑；正文统一为 $\ell_b$（基线）和 $\mathcal B_j$（建筑）。
- $R$ 是告警中相对调度中心的距离，$r$ 是站到目标的水平距离，$D$ 是 Q3 固定的 40 m；三者不要互换。
- $T$ 是目标，$t$ 是时间；建议目标用 $T_a$，时间用 $t$，不要用 $T$ 表示时间。
- $V$ 是车辆，$\mathcal V$ 是车心可行域（队友稿已用）；保留但要在符号表说明。
- $S$ 是开测时刻，$s$ 是累计里程；大小写要分清。
- $\sigma$ 是标准差，$\sigma_*$ 是临界值；不要用 $\sigma$ 同时表示两者。

---

## 4. 文献地图：每条文献能支撑什么

正文不需要“文献综述大拼盘”。B 题只需要三类引用：**几何/估计的理论依据**、**同步/动态调度的研究背景**、**不确定度/验证的标准做法**。每条文献都要能回答“它支撑正文哪一句”，不能支撑的就不要引。

| 文献 | 主题 | 能支撑正文哪一句 | 不能支撑什么 | 建议引用位置 |
| --- | --- | --- | --- | --- |
| Bishop et al. (2010), *Automatica* | 传感器—目标几何、Fisher 信息、精度最优布局 | “评价双站几何需要同时指定测量模型和精度准则；一阶协方差可写成 $J^{\mathsf T}J$ 的形式。” | 不能支撑遮挡、路径、调度；也不能证明本题的具体站对最优 | Q2(1) 指标定义 |
| Lee & Civera (2019), ICCV | 角度误差下的两视图三角测量 | “以角度残差定义三角测量时，误差传播与基线/距离有关。” | 不能支撑三维建筑遮挡和车体约束 | Q2(1) 相关工作 |
| JCGM 100:2008 | 不确定度传播（GUM） | “一阶协方差传播的适用条件和线性化含义。” | 不能支撑非线性的投影正态；不能代替 Q3 的数值验证 | Q2(1)、Q3 |
| JCGM 101:2008 | 分布传播、蒙特卡洛 | “用抽样/数值方法传播分布时，要报告样本量、区间和收敛性。” | 不能支撑具体临界值；不能把抽样误差当模型误差 | Q3 验证 |
| JCGM 102:2011 | 多输出量、联合覆盖区域 | “二维误差椭圆是联合覆盖区域，不是两个一维区间的拼接。” | 不能支撑调度指标 | Q2(1) 误差椭圆（若用） |
| Wang & Gelfand (2013) | 投影正态分布 | “二维高斯向量的方向服从投影正态，不是普通一维正态。” | 不能支撑叠加云台误差后的精确分布；不能代替数值积分 | Q3 角度密度 |
| LaValle (2006), *Planning Algorithms* | 配置空间、最短路径 | “圆盘车的可行域可用 C-space 障碍描述；避障路长不是直线距离。” | 不能支撑三维 LOS；不能支撑调度 | Q1 车体约束、Q2 路由 |
| Bredström & Rönnqvist (2008), EJOR | 带时间先后与同步约束的 VRP | “两车必须同时到场会改变整个车队的时间表，不能把两车路线分开优化。” | 不能支撑本题的具体目标函数和 5 s 观测规则 | Q2(2) 相关工作 |
| Drexl (2012), *Transportation Science* | 同步车辆路径综述 | “同步约束是 VRP 的一类独立约束。” | 不能支撑本题算法；不能代替建模 | Q2(2) 相关工作 |
| Bent & Van Hentenryck (2004), *Operations Research* | 情景式动态 VRP | “动态请求下，情景设计和信息条件是策略比较的一部分。” | 不能支撑 Q4 的具体扰动分布；本题没有给分布 | Q4 情景设计 |
| Ouelhadj & Petrovic (2009), *Journal of Scheduling* | 动态调度综述 | “扰动后的重调度要区分事件触发、周期触发和滚动时域。” | 不能支撑本题的具体策略 | Q4 相关工作 |
| Pinedo (2022), *Scheduling* | 调度理论 | “最晚开测、资源占用、同步约束属于调度问题。” | 不能支撑本题的几何和误差 | Q2(2)、Q4 背景 |
| Morello & Ratti (2009), *Environment and Planning B* | 3D isovist / 城市可视域 | “从给定位置计算可见空间有独立的研究传统。” | 不能支撑三维闭楼体 LOS；不能支撑车体约束 | Q1 相关工作（可选） |
| Burri et al. (2016), IJRR | EuRoC 数据集与参考真值 | “实测验证要说明参考系统、对时和已知误差。” | 不能支撑本题的数值结果；本题没有实测 | 讨论/验证（可选） |

**引用数量建议：** 正文 10–14 条足够。优先 Bishop、JCGM 100/101、Wang & Gelfand、LaValle、Bredström、Bent、Ouelhadj；Drexl、Pinedo、Morello、Burri 按篇幅取舍。不要为了“参考文献多”而堆无关文献。

---

## 5. 文献怎么进正文：三种用法

### 5.1 背景句（只占一句，不展开）

**可以：**
> Bearing-only localization is classically analyzed through the Fisher information of the bearing model [1], and the resulting covariance depends on the baseline, the intersection angle, and the target range.

**不要：**
> With the rapid development of UAV technology, many scholars have studied localization from various perspectives. Smith et al. proposed..., Jones et al. improved..., and so on.

后者是“文献流水账”，没有说明这些工作与本题的关系。

### 5.2 方法依据（说明为什么用这个方法）

**可以：**
> Because the bearing errors are small relative to the target range, we use first-order covariance propagation [1], and we verify the linearization against a forward-intersection Monte Carlo test in Section 5.3.

**不要：**
> We use the Fisher information matrix because it is widely used and has good performance.

“widely used”不是理由；要说清“本题的误差尺度、模型假设、验证方式”。

### 5.3 对照/边界（说明文献不能覆盖什么）

**可以：**
> Synchronization constraints have been studied in vehicle routing [8], but these models assume fixed service locations; in our problem the station itself is a decision variable and must satisfy a 3D LOS constraint.

**不要：**
> There is no research on this problem.

B 题是应用题，通常不是“无人研究”，而是“现有研究的假设不覆盖本题的组合”。要具体说哪一条假设不覆盖。

---

## 6. 引用格式与核对

按官方格式规范：

- 正文引用用方括号编号，如 `[3]`；按正文首次引用次序排列。
- 期刊：作者，论文名，杂志名，卷期号，起止页码，出版年。
- 书籍：作者，书名，出版地，出版社，出版年；引用具体内容要给页码。
- 网上资源：作者，资源标题，网址，访问时间；尽量少用网址。
- 不引用没有读过的文献；不把 AI 生成的“看起来像”的文献写进参考文献。
- 每条引用在正文有对应 `\cite{}`；每条 `\bibitem` 在正文至少出现一次。
- 中文文献如果必须引，给英文译名并括注原文；图表中不能出现中文。

**模板处理：** 现有 `demo.tex` 用 `thebibliography`，不要为了换 BibTeX 改模板；把 `research/BC文献.bib` 的条目按官方格式手工排成 `\bibitem`，键名用 `Bishop2010`、`JCGM100` 等，正文用 `\cite{Bishop2010}`。如果队员更习惯 BibTeX，再统一改，不在续写中途混用两种。

---

## 7. 术语误用对照（续写时最常犯的 18 条）

| 不要写 | 为什么不对 | 改成 |
| --- | --- | --- |
| observability region | observability 是控制理论术语，指能否由输出反推状态 | observable region（首次定义） |
| triangulation | 在测绘里可指角度交会，但本题更精确的是 bearing-line intersection；trilateration 是测距，完全不对 | two-station bearing intersection |
| baseline | 与“基线方案”同词不同义 | baseline length $\ell_b$ / baseline scheme |
| accuracy $0.1436$ m | accuracy 需要真值参考；这里是一阶模型下的 RMSE | model-based first-order RMSE $0.1436$ m |
| optimal station | 没有说明目标函数和并列规则 | global optimum of $Q=1/r$; recommended station under the max-min clearance tie-break |
| proved by simulation | 仿真只能验证，不能证明 | verified by independent replay / supported by a Monte Carlo check |
| robust | 没有定义扰动集 | the score changes by X when parameter Y varies over [a,b] |
| real-time rescheduling | 没有实车时钟和通信时延 | event-triggered online rescheduling |
| completion rate | 题面定义的是 ratio，且不是单位时间比率 | completion ratio $\eta=k/N$ |
| makespan | makespan 是最后完成时间，题面是最晚开测 | latest observation start time $t_{\rm last}$ |
| random scenarios | 1,751 场是设计网格，不是随机抽样 | designed scenario grid; equal-weight aggregation |
| expected 100% | 没有概率分布，不能写期望 | all 1,751 designed scenarios started all alerts |
| saves 23.1 s | 均值差受 60 s 罚值影响 | mean $t_{\rm last}$ differs by 23.103 s, inflated by the 60 s penalty; among both-all-start pairs the mean difference is 0.601 s |
| collision-free | 只验证了标称轨迹 | nominal minimum center-to-center distance 3.13 m |
| safe | 没有现场误差证书 | satisfies the 0.45 m clearance case in the nominal trajectories |
| validated | 没有实测数据 | verified against independent replay / checked against a half-plane reference |
| significant improvement | 没有统计检验 | 0.616866 s (5.62%) earlier in the same candidate set |
| novel framework | 没有与已有工作逐项比较 | we formulate the joint station-and-schedule problem as ... |

---

## 8. 续写时的用词检查

### 8.1 Q2(1) 续写（接第 411 行）

队友已用 $\boldsymbol n_i$、$\varepsilon_i$、$\Delta T$。继续用：

- $\boldsymbol J_i=(-\sin\alpha_i,\cos\alpha_i)/r_i$；
- $\Sigma_{\rm lin}=\sigma_\alpha^2(\boldsymbol J^{\mathsf T}\boldsymbol J)^{-1}$；
- $E_{\rm lin}=\sqrt{\operatorname{tr}\Sigma_{\rm lin}}$；
- 结果写 “first-order horizontal RMSE”，不要写 “true localization error”；
- 退化写 “the linearized variance diverges as $|\sin\gamma|\to0$”，不要写 “the model fails”。

### 8.2 Q2(2) 新写

统一用 $S_j$、$d_{vj}$、$L_{vj}$、$g_{vj}$、$\operatorname{wrap}$；目标写 “minimize the maximum observation start time”，不要写 “minimize makespan”。基线写 “nearest-assignment heuristic” 和 “baseline scheme”，不要只写 “greedy”。

### 8.3 Q3 新写

统一用 $\Delta\psi_{\rm cmd}$、$\Delta\psi_{\rm act}$、$\sigma_*$、$\kappa$、$f_\kappa$、$W(u)$；不要用 $\Delta\theta$ 或 $\delta\psi$ 另起一套。写 “coverage probability”，不要写 “confidence level”；写 “projected normal distribution”，不要写 “wrapped normal”。

### 8.4 Q4 新写

统一用 $\eta$、$t_{\rm last}$、$k$、$N$、$t_{\rm rec}$；写 “scenario grid”“equal-weight aggregation”“penalty value”；不要写 “random sample”“expected value”“makespan”。策略名统一：`retain-and-append`（保留并追加）、`strictly frozen`（严格冻结）、`recourse`（允许换站的重调度）；首次出现给英文全称和中文括注只在队内文档，不进论文。

### 8.5 图表标题

- 图题在下：`Fig. 5. Five-vehicle timelines under the optimized and nearest-assignment schemes. The dashed lines mark the latest observation start times (10.352 s and 10.969 s).`
- 表题在上：`Table 4. Comparison of the optimized schedule and the nearest-assignment heuristic under identical constraints.`
- 图题要写条件、指标、结论；不要写 `The result of Problem 2`。
- 图例/坐标轴英文；单位用 m、s、deg 或 $^\circ$；不要中文。

---

## 9. 来源与核对范围

| 来源 | 用途 |
| --- | --- |
| 英文原题 PDF（五页） | 题面术语、参数、规则、问法 |
| 官方格式规范、提交流程、AI 规定 | 语言、页数、引用、提交、AI 声明 |
| `research/BC文献.bib` | 文献条目、DOI、作者、年份 |
| Bishop et al. (2010) | 传感器—目标几何、Fisher 信息 |
| JCGM 100/101/102 | 不确定度传播、分布传播、联合覆盖 |
| Wang & Gelfand (2013) | 投影正态 |
| LaValle (2006) | 配置空间、最短路径 |
| Bredström & Rönnqvist (2008)；Drexl (2012) | 同步车辆路径 |
| Bent & Van Hentenryck (2004)；Ouelhadj & Petrovic (2009) | 动态/情景式调度 |
| Pinedo (2022) | 调度理论 |
| Burri et al. (2016) | 实测验证与参考真值（可选） |

**维护：** 新增术语先查英文原题和 `BC文献.bib`；不确定的术语不要自造，先在队内文档标 `TODO(术语确认)`，等队员核对后再进论文。队友已写段落中的术语，除非与题面冲突，不按本表强行替换。
