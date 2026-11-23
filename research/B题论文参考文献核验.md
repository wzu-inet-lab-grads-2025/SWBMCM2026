# B 题论文参考文献核验

2026-11-23 核对 q1–q4 所用的十篇期刊论文及第四问的一条命题答疑。以下编号按英文稿正文首次引用顺序排列。正文用方括号编号，期刊条目给出作者、题名、期刊、卷期、起止页与年份，网上答疑给出作者、标题、网址和访问日期。行号对应核对时的稿件，段名和引文短语可用于后续定位。[官方格式规范](../problem/official/2026年第十二届数维杯全国大学生数学建模挑战赛（秋季赛）论文格式规范.pdf) · [英文稿](../paper/demo.tex)

### `ref:bearing-geometry`

[1] A. N. Bishop, B. Fidan, B. D. O. Anderson, K. Doğançay, P. N. Pathirana, Optimality analysis of sensor-target localization geometries, Automatica, 46(3):479–492, 2010.

- **核验依据**采用期刊卷期年 2010。原文的 bearing-only 部分分析纯方位定位和站点几何，摘要说明 Fisher 信息矩阵、Cramér–Rao 下界与估计不确定性的关系。[Elsevier 原文及 DOI 10.1016/j.automatica.2009.12.003](https://www.sciencedirect.com/science/article/pii/S0005109809005500) · [ANU 收录的原文摘要](https://hdl.handle.net/1885/59179)
- **正文位置**在 Introduction → Background 的 “governs how bearing errors affect localization accuracy” 后首次引用（第 90 行）。Q2(1) 在两条方位雅可比行的句末复引，衔接随后的一阶协方差。
- **适用范围**引言表述限定在非平行方位线交会，以及测站几何对方位误差传播的影响。Q2(1) 的噪声参数、RMSE 推导和取等站位由题面与本文给出。

### `ref:configuration-space`

[2] T. Lozano-Pérez, Spatial Planning: A Configuration Space Approach, IEEE Transactions on Computers, C-32(2):108–120, 1983.

- **核验依据**卷号 `C-32` 见期刊原版页眉及 IEEE 的 Crossref 登记。原文把刚体位形表示为一点，把发生碰撞的位形表示为禁区，并讨论多边形和多面体的位形障碍。[IEEE 原文记录及 DOI 10.1109/TC.1983.1676196](https://www.computer.org/csdl/journal/tc/1983/02/01676196/13rRUx0xPul) · [MIT 作者存档的期刊版](https://lis.csail.mit.edu/pubs/tlp/spatial-planning.pdf)
- **正文位置**Q1 的 Observable Region and Optimal Station 段，在 “configuration-space representation” 后、允许车心位置集合的公式前引用（第 198 行）。
- **适用范围**圆盘平移时只需跟踪车心的两个坐标。本文据此对建筑足迹外扩、对场界内缩，并按题面处理相切。三维视线阴影、仰角约束和观测面积另由本文推导。

### `ref:branch-and-bound`

[3] A. H. Land, A. G. Doig, An Automatic Method of Solving Discrete Programming Problems, Econometrica, 28(3):497–520, 1960.

- **核验依据**作者顺序取印刷版首页及 JSTOR 的 Crossref 登记，完整页段取印刷版和学会书目。原文对部分变量取离散值的规划逐步构造更紧的界，据此排除分支。[JSTOR 原文及 DOI 10.2307/1910129](https://doi.org/10.2307/1910129) · [期刊原版扫描件](https://jmvidal.cse.sc.edu/library/land60a.pdf) · [Econometric Society 书目](https://www.econometricsociety.org/publications/econometrica/1960/07/01/automatic-method-solving-discrete-programming-problems)
- **正文位置**Q2(2) 的 Concurrent Assignment and Routing for Three Alerts 段，在 “branch-and-bound search” 后引用（第 299 行）。
- **适用范围**引用的是用界与当前最好解比较来剪枝的策略。本文自行定义就绪和转场时间下界。候选网格上的剪枝结论只适用于相应搜索域，连续站位的最优性需要单独论证。

### `ref:visibility-graph`

[4] T. Lozano-Pérez, M. A. Wesley, An algorithm for planning collision-free paths among polyhedral obstacles, Communications of the ACM, 22(10):560–570, 1979.

- **核验依据**卷期、页码和署名取 ACM 的 Crossref 登记。原文的二维 VGRAPH 段连接互相可见的顶点，在图上计算避障最短路。[ACM 原文及 DOI 10.1145/359156.359164](https://doi.org/10.1145/359156.359164) · [MIT 作者存档的期刊版](https://lis.csail.mit.edu/pubs/tlp/collision-free-planning-cacm.pdf)
- **正文位置**Q2(2) 同一段，在 “visibility graph” 后引用，排在 [3] 之后（第 299 行）。
- **适用范围**支持外扩障碍物后的可见性图路由。本文的路长来自保守的外切多边形模型，精确圆角边界上的最短路和多车同时行驶的可行性各需单独核查。[路由实现](../src/q2/paths.py#L46)

### `ref:sqp`

[5] D. Kraft, Algorithm 733: TOMP–Fortran modules for optimal control calculations, ACM Transactions on Mathematical Software, 20(3):262–281, 1994.

- **核验依据**书目信息取 ACM 的 Crossref 登记。原文的 TOMP 包含模拟器与 SLSQP 优化器，配套发布的 Algorithm 733 提供其代码。SciPy 1.17 文档将 `minimize` 的 SLSQP 实现追溯到 Kraft 的优化子程序，并给出其 1988 年技术报告出处。[ACM 原文及 DOI 10.1145/192115.192124](https://doi.org/10.1145/192115.192124) · [ACM CALGO 的 Algorithm 733](https://netlib.org/toms/733.gz) · [SciPy `minimize` 文档](https://docs.scipy.org/doc/scipy-1.17.0/reference/generated/scipy.optimize.minimize.html)
- **正文位置**Q2(2) 的关键链精修段，在 “Sequential quadratic programming” 后引用（第 301 行）。关键链精修和平行任务补点均调用 SciPy SLSQP。[关键链调用](../src/q2/refinement.py#L97) · [补点调用](../src/q2/refinement.py#L124)
- **适用范围**引用落实到 SLSQP 求解器的来源。精修是从候选解出发的局部优化，固定搭档、变量边界、接受条件与后续可行性验收均由本文设定。

### `ref:projected-normal`

[6] F. Wang, A. E. Gelfand, Directional data analysis under the general projected normal distribution, Statistical Methodology, 10(1):113–127, 2013.

- **核验依据**采用 Elsevier 卷期年 2013。作者稿第 2 节式 (1) 给出二维正态向量的角度密度。附录 A 对单位协方差的向量从极坐标联合密度积分消去半径，对应本文将高斯视向量除以 `σ` 后的特例。[Elsevier 原文及 DOI 10.1016/j.stamet.2012.07.005](https://doi.org/10.1016/j.stamet.2012.07.005) · [PMC 作者稿](https://pmc.ncbi.nlm.nih.gov/articles/PMC3773532/#S2)
- **正文位置**Q3 的 Bearing Reliability and the Travel Limit 段，在 “projected-normal angular density” 后、角度密度公式前引用（第 366 行）。
- **适用范围**引用覆盖定位误差诱导的方位角分布。云台误差、角度回绕、可靠性阈值和临界里程属于本文模型。

### `ref:adaptive-quadrature`

[7] P. Gonnet, A review of error estimation in adaptive quadrature, ACM Computing Surveys, 44(4):1–36, 2012.

- **核验依据**采用 ACM 的 Crossref 页段 `1–36`，ACM 文章号为 22。作者稿第 2.4、3.4 节分别讨论 Gauss–Kronrod 误差估计和采用 21 点基本规则的自适应外推，后者明确说明 QUADPACK 的 QAGS 实现。[ACM 原文及 DOI 10.1145/2333112.2333117](https://doi.org/10.1145/2333112.2333117) · [ACM 的 Crossref 登记](https://api.crossref.org/works/10.1145/2333112.2333117) · [作者稿](https://arxiv.org/html/1003.4629v2)
- **正文位置**Q3 的同一小节，在 “Adaptive quadrature” 后引用（第 385 行）。`_integrate_even` 手工切段，把每段映射到 `[0,1]`，再以无 `weight`、无 `points` 的形式调用 `quad`。SciPy 1.17 文档确认该输入走 `qagse`，在各自适应子区间上使用 21 点 Gauss–Kronrod 规则。[分段调用](../src/q3/model.py#L148) · [SciPy `quad` 文档](https://docs.scipy.org/doc/scipy-1.17.0/reference/generated/scipy.integrate.quad.html) · [QUADPACK 源码](https://netlib.org/quadpack/dqagse.f)
- **适用范围**引用覆盖自适应求积及其误差估计。利用对称性、按窄峰和阈值切段、保留云台中心窗口的尾界和数值核验都是本文的做法。

### `ref:brent`

[8] R. P. Brent, An algorithm with guaranteed convergence for finding a zero of a function, The Computer Journal, 14(4):422–425, 1971.

- **核验依据**卷期页取 OUP 的 Crossref 登记。原文摘要说明在函数变号的区间上，用二分、线性插值和逆二次插值求零点。[OUP 原文及 DOI 10.1093/comjnl/14.4.422](https://doi.org/10.1093/comjnl/14.4.422) · [Brent 的原文与摘要](https://maths-people.anu.edu.au/brent/pub/pub005.html)
- **正文位置**Q3 的同一段，在 “Brent's method” 后引用，排在 [7] 之后（第 385 行）。`solve_sigma_critical` 扩张上界至阈值函数变号，再调用 `brentq`。[求根调用](../src/q3/model.py#L210) · [SciPy `brentq` 文档](https://docs.scipy.org/doc/scipy-1.17.0/reference/generated/scipy.optimize.brentq.html)
- **适用范围**引用覆盖有变号括区的一维求根方法。阈值方程的唯一性依赖本文的概率模型，临界里程由求根结果按题面的标准差模型换算。

### `ref:rescheduling`

[9] G. E. Vieira, J. W. Herrmann, E. Lin, Rescheduling Manufacturing Systems: A Framework of Strategies, Policies, and Methods, Journal of Scheduling, 6(1):39–62, 2003.

- **核验依据**期号取 Springer 的 Crossref 登记，卷、页段和年份与期刊页面一致。原文将扰动后更新排程归入重调度，作者稿讨论事件驱动规则及故障与修复时触发的例子。[Springer 原文及 DOI 10.1023/A:1022235519958](https://link.springer.com/article/10.1023/A:1022235519958) · [马里兰大学作者稿](https://isr.umd.edu/Labs/CIM/projects/jos-rescheduling.pdf)
- **正文位置**Q4 的 Dispatch State and Scoring 段，在 “The rescheduling policy is event-driven” 句末引用（第 432 行）。
- **适用范围**引用覆盖因事件而修订排程的分类。5 s、断联恢复时刻、旧队列授权、状态读取顺序和评分按题面及本文规则处理。

### `ref:problem-b-outage`

[10] Problem B Author, Clarification on Communication Outage Duration in Problem B, Question 4 (pinned reply), 2026 ShuWei Cup Autumn Competition Q&A Forum, accessed 2026-11-23. [答疑原帖](https://pd.qq.com/g/pd52619943/post/B_e4b7ff6a0ac20e001441152187121863520X60?subc=745397906)

- **核验依据**组委会开赛公告指定了赛事答疑论坛。原题第 5 页将断联区间写作 $[5,t_{\rm rec}]$，命题人的置顶答复澄清了恢复时刻与历时的区分。[开赛公告](https://www.nmmcm.org.cn/News/225.html) · [命题答疑核验记录](B题四问_约束复核.md#官方答疑核验)
- **正文位置与范围**Q4 的 Dispatch State and Scoring 段，在 $t_{\rm rec}$ 的定义和 $t_{\rm rec}-5\,\mathrm s$ 的历时说明后引用。原队列预授权与搭档就绪确认按本文的执行假设处理。

### `ref:beam-search`

[11] I. Sabuncuoglu, M. Bayiz, Job shop scheduling with beam search, European Journal of Operational Research, 118(2):390–412, 1999.

- **核验依据**署名拼写、卷期和页码采用 Elsevier 的 Crossref 登记。Bilkent 收录的原文摘要明确说明，每一层只保留有希望的节点供继续扩展，并永久剪去其余节点。[Elsevier 原文及 DOI 10.1016/S0377-2217(98)00319-1](https://doi.org/10.1016/S0377-2217%2898%2900319-1) · [Bilkent 原文摘要及存档](https://hdl.handle.net/11693/25191)
- **正文位置**Q4 的 Field-Wide Search and Replay 段，在 “Each beam-search layer” 后引用（第 447 行）。
- **适用范围**引用覆盖逐层扩展、排序和保留有限节点的搜索框架。每层加入一个告警、状态去重、束宽、连续回放和车辆调度指标由本文实现。搜索结果的可行性依据连续回放，最优性需另有证明。[逐层搜索实现](../src/q4/recourse.py#L138)
