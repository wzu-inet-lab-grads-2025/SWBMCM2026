# 论文写作风格约束：反 AI 味清单

**用途：** 约束续写和终稿的语言，避免出现“模板化、空泛、过度整齐”的 AI 文风。它不是 AI 检测器，也不靠替换几个词；它约束的是**主张、证据、结构和节奏**。

**适用范围：** `paper/demo.tex` 的新增段落、摘要、图题表题、结论；中文队内文档可参考第 3 节。队友已经写好的段落不按本清单自动重写；发现可疑句时，先在对话里提出“原文 + 建议 + 理由”，等队员同意再改。

**优先级：** 官方格式规范与 AI 使用规定 > 本清单 > 个人偏好。官方要求英文、25 页内、图表无中文、AI 内容标注；本清单不能覆盖这些硬约束。

**依据：**

- 数维杯《人工智能工具使用规定（2026 年修订）》要求论文文字与结论由队伍自行改写，严禁直接照搬 AI 原始输出；AI 生成内容要标注，并附使用报告。
- Kobak 等对 PubMed 摘要的“超额词汇”分析发现，`delve`、`underscore`、`showcase` 等词在 ChatGPT 出现后显著超额使用；这类词是文风的统计信号，不是单个词的禁令。
- Wikipedia 的 *Signs of AI writing* 归纳了“夸大意义、三段式、否定平行、章节小结、破折号滥用”等结构特征；该页明确说这些是描述性迹象，不是逐条禁令。
- 数学建模竞赛的常见评阅口径看四件事：假设合理性、建模创造性、结果正确性、表述清晰性（韩中庚，《数学建模竞赛论文的写作方法》，2017）。空话和模板句伤害的是“表述清晰性”和“结果正确性”的可核验性。

---

## 1. 先定标准：什么叫“AI 味”

不是“用了某个词”，而是五种症状：

1. **空泛的正确**：每句都对，但没有本题的数字、条件或依据。
2. **均匀的节奏**：段落长度、句长、连接词高度一致，像模板填空。
3. **过度的路标**：每节开头“本节将……”，每节结尾“综上所述……”，读者被反复告知而不是被论证。
4. **虚假的平衡**：什么都“既……又……”“不仅……而且……”，没有取舍和优先级。
5. **夸大的意义**：把“算了一个站”写成“为低空安全提供了新范式”。

**四条总原则：**

- **具体优于抽象**：能写数字就不写形容词。
- **证据优于断言**：每个“最优/显著/有效”都要有目标函数、对照或检验。
- **主张分级**：证明、核验、观察、推测分开写；不把“回放通过”写成“数学证明”。
- **立场明确**：不预判批评、不写自我辩护；主张—证据—边界一次说清。

---

## 2. 英文硬禁用词

### 2.1 一级：默认删掉或替换

| 禁用 | 为什么像 AI / 空话 | 改成 |
| --- | --- | --- |
| delve / delve into | 典型超额词；没有信息量 | examine, analyze, study |
| intricate | 泛泛夸复杂 | complex, detailed；或直接描述复杂在哪 |
| tapestry | 比喻，无技术含义 | 删除 |
| testament | 夸大意义 | 删除 |
| underscore (verb) | 典型超额词；常接空泛结论 | show, confirm, imply |
| showcase | 典型超额词 | show, demonstrate |
| leverage (verb) | 商业腔 | use, exploit |
| utilize | 比 use 更绕 | use |
| facilitate | 空泛 | allow, help, enable |
| elucidate | 空泛 | explain, clarify |
| paramount | 没有说清后果 | necessary；或写“without X, Y fails” |
| pivotal | 夸大 | necessary, decisive；或写具体作用 |
| crucial / vital | 形容词代替论证 | necessary；或写“if X is removed, Y changes by Z” |
| comprehensive | 无法核验 | complete, full；或列出覆盖了什么 |
| multifaceted | 不列因素 | 列出 2–3 个因素 |
| nuanced | 不说明差别 | 写出差别 |
| meticulous | 自夸 | careful；或删除 |
| seamless | 没有定义 | without reconfiguration；或删除 |
| holistic | 空泛 | joint, integrated |
| paradigm | 大词 | model, formulation, approach |
| realm | 比喻 | area, field |
| landscape (metaphor) | 比喻 | field, setting |
| journey | 比喻 | process |
| cornerstone | 比喻 | basis |
| beacon | 比喻 | 删除 |
| game-changer | 宣传语 | 删除 |
| cutting-edge / state-of-the-art | 没有比较对象 | 具体方法名 + 年份 + 引用 |
| novel | 没有与已有工作比较 | new；并说明新在哪 |
| robust (bare) | 没有扰动集 | the result changes by X when Y varies over [a,b] |
| significant (bare) | 没有统计检验 | 给出数值差；统计显著时写 test + p-value |
| substantially / dramatically | 没有幅度 | by X% / by X s / by X m |
| effectively / efficiently | 没有指标 | 给出指标和对照 |
| reasonable | 没有依据 | justified by ...；或给约束 |
| obvious / clearly | 通常不显然 | 证明；或删除 |
| interesting / interestingly | 作者感受，不是结果 | 直接陈述事实 |
| importantly / notably | 路标词 | 删除；让内容自己重要 |
| it is worth noting that | 空路标 | 删除 |
| it is important to note that | 空路标 | 删除 |
| in today's rapidly evolving ... | 背景套话 | 删除；直接写问题 |
| plays a crucial role | 空泛 | affects X by Y |
| not only ... but also ... | 否定平行，AI 高频 | 拆成两句 |
| serves as / stands as | 绕 | is |
| paves the way | 夸大 | enables |
| sheds light on | 比喻 | explains |
| holds promise | 空泛 | can；或给条件 |
| in conclusion / overall | 模板结尾 | 直接写结果；需要总结时用 “The results support ...” |
| due to space limitations | 借口 | 移到附录；不要解释 |
| it can be seen that | 空路标 | 直接写观察 |
| from Fig. X, we can see | 模板句 | Fig. X shows ...；并写读数 |
| the results show that our model is effective | 没有指标 | 给出 RMSE/时间/比例的对照 |
| etc. | 偷懒 | 列全，或写 “including ...” |
| various | 不具体 | 列出类型或数量 |
| a wide range of | 不具体 | 给范围或数量 |
| approximately | 没有容差 | 给 tolerance 或有效数字 |
| more and more | 口语 | increasing；或给数量 |
| with the rapid development of ... | 背景套话 | 删除 |
| last but not least | 口语套话 | 删除 |
| firstly / secondly / finally | 机械三段 | 用逻辑关系；确需枚举时用 (i), (ii) |
| on the one hand / on the other hand | 虚假平衡 | 只在真对比时用；否则删 |

### 2.2 二级：只在满足条件时使用

| 词 | 允许条件 | 例子 |
| --- | --- | --- |
| optimal | 写明目标函数、约束、证明层次 | “the global optimum of $Q=1/r$ over the continuous observable region” |
| robust | 给出扰动集和变化幅度 | “the score changes by 0.6 s when the clock step varies from 0.05 to 0.2 s” |
| significant | 有统计检验，或明确写“substantial”并给数值 | “0.617 s (5.62%) earlier”；统计显著时给 test |
| novel | 与已有工作逐项比较 | “unlike [8], the station is a decision variable” |
| framework | 全文最多一次，且定义了什么被框住 | “a feasibility–accuracy–time framework” |
| baseline | 技术词：基线长度 / 基线方案；首次出现写清 | “baseline length $\ell_b$”；“baseline scheme” |
| observable | 题面术语；首次定义 | “observable region (the set of admissible station centers ...)” |
| nominal | 标称参数、无扰动 | “nominal trajectories” |
| feasible | 满足约束 | “a feasible station pair” |
| valid | 通过规则/回放检查 | “the plan is valid under the replay checks” |

**技术白名单：** `optimal`、`robust`、`significant`、`baseline`、`observable`、`synchronization`、`clearance`、`feasible`、`nominal`、`scenario`、`policy`、`heuristic`、`bound`、`certificate` 在本题里有确定含义，不能因为“像 AI 词”就全部删掉；要用定义和条件把它们钉住。

---

## 3. 中文硬禁用词（队内文档、翻译、答辩稿）

| 禁用 | 问题 | 改成 |
| --- | --- | --- |
| 值得注意的是 | 空路标 | 删除；直接写事实 |
| 不难发现 | 空路标，且常常不显然 | 删除；给推导或读数 |
| 综上所述 | 模板结尾 | 直接写结论；或“因此” |
| 由此可见 | 空连接 | 删除；写因果 |
| 众所周知 | 没有引用 | 删除；给依据 |
| 毋庸置疑 | 过度断言 | 删除；给证据 |
| 首先 / 其次 / 最后 | 机械三段 | 用逻辑顺序；确需枚举用 (1)(2)(3) |
| 不仅……而且…… | 虚假递进 | 拆成两句，分主次 |
| 在当今……背景下 | 背景套话 | 直接写问题 |
| 随着……的不断发展 | 背景套话 | 删除；给数量或时间 |
| 赋能 | 商业腔 | 使能、支持；或写具体功能 |
| 闭环 | 商业腔 | 反馈、迭代；或写具体环节 |
| 抓手 | 商业腔 | 切入点、措施 |
| 范式 | 大词 | 模型、方法、框架 |
| 维度 / 层面（滥用） | 空泛分层 | 写具体因素 |
| 全方位 / 多维度 / 系统性 | 无法核验 | 列出覆盖了什么 |
| 有效地 / 显著地 | 没有指标 | 给数值 |
| 显著提升 / 大幅提高 / 极大改善 | 没有幅度 | 给差值、百分比、对照 |
| 至关重要 / 核心关键 | 形容词代替论证 | 写“去掉它会怎样” |
| 深刻变革 / 前所未有 | 夸大 | 删除；写具体变化 |
| 广阔的应用前景 | 空话 | 写一个可验证的应用条件 |
| 具有重要的理论意义和实际价值 | 空话 | 写具体贡献和证据 |
| 丰富了……理论 | 空话 | 写“给出了什么下界/构造/验证” |
| 提供了新思路 | 空话 | 写“把哪两个子问题接在一起” |
| 抛砖引玉 | 客套 | 删除 |
| 由于时间关系 | 借口 | 删除；写实际范围 |
| 限于篇幅 | 借口 | 移到附录；不要解释 |
| 本文认为 | 没有论证 | 写“由……可得” |
| 从图中可以看出 | 没有读数 | 写“图 X 的最晚开测为 10.352 s” |
| 模型具有较高的精度和较强的鲁棒性 | 两个形容词都没有定义 | 给 RMSE 和参数变化范围 |
| 结果与实际情况相符 | 没有实测 | 写“与独立回放/解析下界一致” |
| 验证了模型的正确性 | “正确性”太大 | 写“核验了哪条约束/哪个数值” |
| 为……提供了有力支撑 | 空话 | 写具体支撑了什么结论 |
| 助力 / 打通 / 颗粒度 / 底层逻辑 / 组合拳 | 商业/网络词 | 删除，换普通词 |

---

## 4. 禁用句式与结构

| 结构 | 为什么像 AI | 改成 |
| --- | --- | --- |
| 每段都“三段式”（A、B、C 并列） | 节奏过于整齐；AI 高频 | 按重要性排序，允许两段、四段；不要凑三 |
| “不是 X，而是 Y” 否定平行 | AI 高频修辞 | 直接写 Y；或“X 不成立，因为……” |
| “不仅 X，而且 Y” | 虚假递进 | 拆成两句，说明主次 |
| 每段开头一个连接词 | 机械过渡 | 最多每三段一个；用内容衔接 |
| 每节开头“本节将……” | 过度路标 | 直接进入问题；必要时一句 |
| 每节结尾“综上所述……” | 模板小结 | 删除；让最后一段落在证据上 |
| 用 bullet list 写论证 | 把逻辑拆成清单 | 用段落；只有参数、步骤、对照表才用列表 |
| 反问句 / 感叹句 | 演讲腔 | 陈述句 |
| “Let us ...” | 口语 | “Let $x$ denote ...” 只用于数学定义 |
| 破折号滥用 | AI 高频 | 用逗号、括号或分句；每两页最多一个 |
| 标题全是“Model Establishment and Solution” | 无信息 | 用具体标题，如“Two-Station Bearing Intersection” |
| 每个小节都“定义—定理—证明” | 装饰性形式 | 只在真有定理时用 |
| 过度加粗/斜体 | 视觉噪音 | 只加粗关键术语或结论 |
| “Future work” 万能结尾 | 空话 | 写具体未解决的一个问题和一个验证计划 |
| 虚假平衡“一方面……另一方面……” | 没有取舍 | 给优先级和条件 |

---

## 5. 数学建模论文专属空话

| 空话 | 问题 | 改成 |
| --- | --- | --- |
| “本文建立了……模型，取得了良好效果” | 没有指标 | “模型给出 $r^*=7.582$ m；独立面积证书与网格估计相差 6.17 m$^2$” |
| “模型具有较高的精度” | 没有基准 | “一阶 RMSE 0.143623 m，达到解析下界” |
| “算法具有较高的效率” | 没有时间/规模 | “单场中位求解 0.04 s；1,751 场全量回放 2,290,719 条事件” |
| “结果与实际情况相符” | 没有实测 | “与独立回放、半平面核查一致到 $10^{-15}$ m 量级” |
| “验证了模型的正确性” | 太大 | “核验了三类约束、同步开测和复位时序” |
| “由于篇幅限制，省略……” | 借口 | 放附录，正文给索引 |
| “未来可以推广到更复杂场景” | 空话 | 写“若加入跟踪误差，需要重验墙距和指向概率” |
| “本文的创新点是……”（列 3 条套话） | 没有对照 | 每条创新点后给“与什么相比、证据是什么” |
| “该模型简单易行” | 主观 | 给实现复杂度、运行时间、可复现命令 |
| “具有广泛的应用前景” | 空话 | 写一个具体可迁移的条件和限制 |
| “假设合理” | 没有论证 | 每条假设后写“依据题面第几页/为什么不会改变结论” |
| “灵敏度分析表明模型稳定” | 没有范围 | 给参数范围、指标变化、最坏情况 |
| “结果具有鲁棒性” | 没有扰动集 | 给扰动集和变化幅度 |
| “有效解决了……问题” | 没有验收 | 写“全部情景满足 X 约束；独立回放通过” |
| “为后续研究提供了新思路” | 空话 | 写“下一步需要实车跟踪误差模型和现场墙距证书” |

---

## 5.5 拒绝防御性思考（专项）

**定义：** 防御性思考不是“写局限”，而是为了躲开可能的批评，先给自己铺一层缓冲：过度限定、预判反驳、替自己辩护、把“没做”说成“条件不允许”、用“虽然……但是……”把结论说软。它会让论文显得心虚，也会把真正的限制埋掉。

**三条规则：**

1. **先给主张，再给证据，最后给边界。** 不要开头先写“本文可能存在不足”；先把做了什么、结果是什么说清楚。
2. **局限只写一次。** 写在 “Limitations” 或每节最后一段；写具体条件、影响和重验范围，不写“水平有限/仅供参考”。
3. **不预判评委的批评，不写自我辩护。** 把“虽然模型不完美，但仍然有效”换成“模型在 X 条件下成立；超出 X 后误差增加到 Y”。

### 5.5.1 英文防御性表达

| 不要写 | 问题 | 改成 |
| --- | --- | --- |
| admittedly / it is true that | 先让步再转折，虚 | 直接写事实 |
| it should be noted that | 空路标 | 删除 |
| we acknowledge that ... but ... | 自我辩护 | 写条件 + 结果 |
| while our model has limitations, it still performs well | 没有指标 | “The model is valid for X; outside X, the error grows to Y.” |
| due to limited data/time | 借口 | “The analysis covers X; Y is not sampled.” |
| this paper only considers ... | 负面前置 | “The scope is X.” |
| it may be imperfect / this is not perfect | 空泛 | 给误差、条件、对照 |
| of course / needless to say | 废话 | 删除 |
| presumably / it is believed | 躲责任 | 给依据；或写 “we assume” |
| on the one hand ... on the other hand | 假平衡 | 给优先级 |
| future work should ... | 万能收尾 | 一个具体下一步 + 验证方式 |
| to the best of our knowledge | 没有检索证据 | 删除；或给检索范围 |

### 5.5.2 中文防御性表达

| 不要写 | 改成 |
| --- | --- |
| 诚然 / 不可否认 | 删除；直接写事实 |
| 需要指出的是 | 删除 |
| 必须承认 | 删除；写证据 |
| 本文难免存在不足 | 写具体限制和影响 |
| 由于水平有限 / 时间关系 | 删除；写实际范围 |
| 仅供参考 / 抛砖引玉 | 删除 |
| 不当之处敬请指正 | 删除（论文不是征求意见稿） |
| 虽然……但是…… | 拆成“条件 + 结果” |
| 本文仅…… | “本文范围是……” |
| 笔者认为 / 本文认为 | 写“由……可得”；或明确写 “we assume” |
| 可能 / 也许 / 大概（连续出现） | 给区间、容差或条件概率 |

### 5.5.3 协作中的防御性思考

- 被指出错误时，先核对事实；错了就改，不写“但是之前……”“其实是因为……”。
- 不确定就说不确定，给候选方案；不要用一段辩护掩盖没验证的东西。
- 不把“你没要求”当挡箭牌；如果影响结论，主动说明。
- 评审意见先复述再回应；不回击、不揣测动机。
- 不用“这是 AI 写的/这是队友写的”分担责任；论文署名是队伍。

**一个测试：** 把一段里的“虽然/但是/可能/仅供参考”全部删掉。如果主张仍然成立且更清楚，说明这些词是防御性填充；如果删掉后主张不成立，说明本来就没有证据，应该补证据或降级主张。

---

## 6. 正面规则

### 6.1 段落

- 一段一个主张；主张句在前，证据在后，解释收尾。
- 每段 3–6 句、80–160 词；超过 180 词拆段。
- 段间靠内容衔接，不靠 “Moreover/Furthermore/Additionally” 堆叠。
- 每节最后一段落在证据或限制上，不写“综上所述”。

### 6.2 数字

- 每个数字带单位、条件、对照或来源。
- 写“0.143623 m”而不是“0.14 m 左右”；四舍五入要说明。
- 百分比和百分点分开：方差占比是 `%`，概率增益是 `percentage points`。
- 均值差要说明是否受罚值影响；不能把 60 s 罚值当实际时间。
- 区间写清开闭：仰角 $[-10^\circ,80^\circ]$ 含端点；60 s 窗是严格 $S<60$（另有 $S\le60$ 对照）。

### 6.3 方法

- 先写“为什么用这个方法”，再写“它保证什么”，最后写“它不保证什么”。
- 精确算法写最优性条件；启发式写可行性和对照；离散化写步长和连续回放。
- 不要把“调用了求解器”写成“证明了最优”；`OPTIMAL` 只对送入的模型成立。
- 近似要写适用尺度：Q2(1) 的 $\sigma/D\simeq0.04$，Q3 的小角度近似只在短里程；超出范围要重验。

### 6.4 图表

- 一张图一个消息；图题写条件、指标、结论。
- 表题在上、图题在下；图表编号连续；正文必须引用每张图表。
- 图里不出现中文；坐标轴、图例、标注全英文；单位写清。
- 不从像素读关键数值；关键数值回到底表。
- 图题不要写 “The result of Problem X”；写“在什么条件下、看到什么、说明什么”。

**图题坏例：**
> Fig. 4. The result of Problem 2.

**图题好例：**
> Fig. 4. Five-vehicle timelines under the optimized and nearest-assignment schemes. The dashed lines mark the latest observation start times (10.352 s and 10.969 s); the shaded blocks are joint observations, and the hatched blocks are gimbal resets.

### 6.5 摘要

- 五段：场景与任务 → 方法主线 → 四问关键结果（带数字）→ 验证 → 适用范围。
- 450–600 词；一页内；不引用文献、不引图号、不用未定义符号。
- 第一句写具体问题，不写“随着低空经济快速发展”。
- 每个结果后给一个对照或条件；不要只写“效果良好”。
- 最后一句写限制，不写“具有广阔前景”。

**摘要坏例：**
> With the rapid development of low-altitude economy, UAV localization has become increasingly important. This paper establishes a comprehensive framework and obtains good results, which provides a new idea for related research.

**摘要好例（结构示例，数字待正文定稿）：**
> We study two-station bearing intersection for a stationary UAV target under building occlusion and gimbal limits. For T1, the three constraints reduce to a continuous observable region, and the elevation bound gives a lower bound that is attained at a feasible station (r\* = 7.582060 m, Q\* = 0.131890 m^-1, elevation 80 deg). For T2, a first-order covariance metric attains 0.143623 m at a 90 deg intersection with both ranges at the elevation bound; the deployable millimeter version gives 0.146102 m and requires at most 13.270981 s of travel. For the three simultaneous alerts, a chain-based schedule starts the last observation at 10.352119 s (10.391510 s for the deployable version), 0.616866 s earlier than a nearest-assignment baseline under identical constraints. For pointing reliability, the 95% requirement gives 73.936 m under the literal command-angle definition and 66.837 m under the actual pointing definition. In 1,751 designed disturbance scenarios, the rescheduling policy starts all alerts in all scenarios (mean t_last = 24.746 s), while retain-and-append starts all in 583 scenarios (mean 47.835 s); the gain comes mainly from replacing a failed vehicle. The results are verified by independent replay and continuous lower bounds, and the reported clearances are nominal rather than field-safety guarantees.

---

## 7. 句长、段长与配额

| 项目 | 约束 | 检查方式 |
| --- | --- | --- |
| 平均句长 | 18–24 词 | 抽查 3 段，逐句数 |
| 最长句 | ≤35 词（公式句除外） | 超过就拆 |
| 句长节奏 | 不要连续 3 句都在 ±2 词内 | 读一遍，听节奏 |
| 段落 | 80–160 词，3–6 句 | 超过 180 词拆 |
| 连接词开头 | 最多每 3 段 1 次 | 搜 `Moreover|Furthermore|Additionally` |
| 破折号 | 每 2 页 ≤1 个 | 搜 `---` / `—` |
| 否定平行 | 全文 ≤1 次 | 搜 `not only|rather than|not ... but` |
| 三段式 | 每节 ≤1 次 | 读每节，数并列项 |
| “we” | 每段 ≤2 次 | 搜 `\bwe\b` |
| 被动语态 | 尽量 <30% | 抽查；方法步骤可用被动 |
| 缩写 | 首次定义，之后一致 | 搜大写缩写 |

---

## 8. 自查命令

在仓库根目录运行；这些命令只列出候选，不自动删改。`.tex` 里的队友段落要先看上下文，再决定是否提议修改。

```bash
# 英文一级禁用词（只看 demo.tex）
grep -nEi 'delve|intricate|tapestry|testament|underscore|showcase|leverage|utilize|facilitate|elucidate|paramount|pivotal|crucial|vital|comprehensive|multifaceted|nuanced|meticulous|seamless|holistic|paradigm|realm|landscape|journey|cornerstone|beacon|game-changer|cutting-edge|state-of-the-art|robust|significant|substantially|dramatically|effectively|efficiently|reasonable|obvious|clearly|interestingly|importantly|notably|it is worth noting|in conclusion|overall|due to space limitations|it can be seen|from Fig' paper/demo.tex

# 英文结构词
grep -nEi 'not only|rather than|on the one hand|firstly|secondly|finally|moreover|furthermore|additionally' paper/demo.tex

# 中文空话（队内文档；论文里不应出现）
grep -nE '值得注意的是|不难发现|综上所述|由此可见|众所周知|毋庸置疑|赋能|闭环|抓手|范式|维度|层面|显著提升|大幅提高|极大改善|至关重要|广阔的应用前景|理论意义|实际价值|抛砖引玉|由于时间关系|限于篇幅' docs/*.md research/*.md

# 数字与单位抽查：列出所有数字附近 40 个字符，人工看单位/条件
grep -noE '.{0,40}[0-9]+(\.[0-9]+)?[^0-9].{0,20}' paper/demo.tex | head -80
```

**注意：** `robust`、`significant`、`optimal`、`baseline` 可能是技术词；先看上下文，不要机械替换。真正的检查标准是“这句话有没有数字、条件、对照或证明”。

---

## 9. 终稿检查表

### 9.1 语言

- [ ] 摘要没有背景套话，第一句就是具体问题。
- [ ] 每节第一段没有“本节将……”；每节最后一段没有“综上所述……”。
- [ ] 没有连续三段用同一个连接词开头。
- [ ] 没有“不是 X 而是 Y”“不仅 X 而且 Y”的堆叠。
- [ ] 每个形容词都能落到数字、条件或引用。
- [ ] 每个“最优/最小/最大”都写了目标函数和证明层次。
- [ ] 每个“验证/核验”都写了核验对象和结果。
- [ ] 没有“虽然……但是……”“诚然”“不可否认”“仅供参考”等防御性填充。
- [ ] 局限只写一次，写条件、影响和重验范围，不写自我辩护。
- [ ] 被指出问题时先核对事实，不写辩护段落。
- [ ] 图表标题能独立读懂；图里没有中文。

### 9.2 内容

- [ ] 四问的明文交付逐条回应。
- [ ] 每个关键数字有单位、条件、底表路径。
- [ ] 队内补充口径（45 cm、3 m、预授权、恢复确认）标成工程补充。
- [ ] Q2(2) 时间轴包含出发、到站、指向、观测起止、复位、后续出发。
- [ ] Q4 逐情景给 $(\eta,t_{\rm last})$、策略和时间轴；解释收益与不佳。
- [ ] 参考文献按引用次序；没有虚构文献；书籍有页码。

### 9.3 AI 合规

- [ ] 正文相应位置标注 AI 辅助内容；参考文献列工具、版本、机构、日期。
- [ ] 附《AI 工具使用情况报告》，写清工具、环节、提示词、输出要点、采纳与人工修改。
- [ ] 代码注释标注 AI 辅助情况。
- [ ] 所有 AI 给出的事实、公式、代码逐项核对；查不到来源的删除。
- [ ] 论文文字已由队员改写，不是 AI 原始输出。

### 9.4 人工复核

- [ ] 朗读摘要和 Q4 结论；听到空话就改。
- [ ] 反向提纲：每段用一句话概括主张，检查顺序是否成立。
- [ ] 数字审计：随机抽 10 个数字，回到 `results/qN/` 找底表。
- [ ] 引用审计：随机抽 5 条引用，核对作者/年份/结论是否被正确使用。
- [ ] 队员交叉复核：至少两人独立读摘要、Q4 结论、适用范围。

---

## 10. 维护

- 本清单随写作推进增补；新增词条要写“为什么”和“改成什么”，不要只列禁词。
- 队友已写段落中的词，除非与题面冲突或造成过度声明，不按本清单强行替换。
- 技术白名单随论文定稿扩充；不要把 `optimal`、`baseline`、`robust` 等本题必需的技术词误删。
- 每次终稿审计后，把新发现的“AI 味”句式补进第 2–5 节，保持一份可执行的清单，而不是一次性的口号。
