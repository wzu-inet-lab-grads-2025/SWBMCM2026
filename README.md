# SWBMCM2026

本仓库用于三人团队备战和参加 2026 年数维杯全国大学生数学建模挑战赛（秋季赛），记录备赛调研与历年赛题演练，并组织正式比赛的建模代码、实验结果和论文。

## 项目目录

下面是团队约定的目录结构。目录随第一份实际内容创建，`qN` 代表实际小问编号，例如 `q3`。

```text
SWBMCM2026/
├── research/              # 专题调研，每篇笔记附参考文献
├── experiments/           # 可复跑的数据探索、候选方法和验证原型
├── docs/
│   ├── modeling-notes.md  # 题意拆解、初步思路、假设和验证计划
│   └── decisions.md       # 关键决定、依据和受影响的结果
├── problem/
│   ├── A/、B/、C/、D/     # 官方题面及各题说明
│   ├── translations/     # 中文翻译和配图
│   ├── official/         # 承诺书、格式规范和提交流程
│   └── archives/         # 原始赛题包和模板包
├── data/
│   ├── README.md          # 数据来源、版本、获取方式和预期路径
│   └── raw/               # 原始数据，按题号存放
├── src/
│   ├── run.py             # 统一运行入口
│   ├── qN/                # 各小问的代码，按需要拆分模块
│   └── shared/            # 已有跨问复用的数据处理、模型或指标
├── configs/               # 各问的参数和实验配置
├── checks/                # 手算样例、约束复核和指标重算
├── results/
│   ├── README.md          # 关键结果的复跑方式和复核记录
│   └── qN/                # 正文采用的图表、底表和运行记录
├── paper/                 # 论文源稿、引用和样式，官方示例入口为 demo.tex
├── tools/                 # 提交打包等独立脚本
├── runs/                  # 本地实验输出
├── scratch/               # 本地草稿和临时探索
└── submission/            # 本地组装的最终提交材料
```

## B 题正式回放入口

B 题正式代码的第一条流程是二（2）固定计划的无扰动回放。从仓库根目录运行，使用 Python 3.10+ 标准库：

```bash
python3 src/run.py replay
python3 checks/check_q22_nominal.py
python3 src/run.py replay --config configs/b/q22-analytic.json
```

默认配置固定了场景与数值路线的输入校验值。两种配置的结果分别写入 `runs/b/formal/q22-numerical.json` 和 `runs/b/formal/q22-analytic.json`，含计划、配置与代码校验值、逐车事件、三个共同开始时刻、最晚开始时刻及 5 s 状态。可用 `--config` 与 `--output` 分别指定其他计划配置和输出文件。`plan` 输入可取原实验的 `q22.jobs` 快照、带 `schema=b.plan.v1` 的计划，或同一方法与场景生成的完整回放文件。配置中的路径相对于配置文件，输入校验值随选定文件更新。独立核验命令默认对照已验收的数值路线和 5 s 状态；可传入回放文件路径。[数值路线配置](configs/b/q22-numerical.json) · [正式架构决策](docs/decisions.md)

### 二（2）生成并比较就近基线

同一组 12 个候选站包含五个初始车位及两份已验收方案采用的站位。两个站之间只使用圆盘可通行的直线路线。单次运行输出可重放的 `plan`、逐车 `events` 和 `allocation`；其中每条腿记录实际路程、就绪时刻和等待同伴的时间。所有告警顺序的比较表保存在 `runs/b/formal/q22-baselines-comparison.json`。[候选站与默认顺序](configs/b/q22-baselines.json)

```bash
python3 src/run.py baseline --rule sum_time
python3 src/run.py baseline --rule ready
python3 src/run.py compare-baselines
python3 -m unittest discover -s checks
```

`sum` 按两车新增路程之和选车，`max` 按两车中较长的一段选车。两者并列时依次比较车号和对应站点坐标。`sum_time`、`max_time` 在相应路程并列时先选当前告警能更早开始的站对。`ready` 直接选最早共同就绪者，再按车号和站点坐标打破并列。比较使用未舍入的数值，`primary_ties` 为主指标同值的可行分配数。可用 `--order T3 T1 T2` 更改单次分配顺序。车队总等待时间为各车完成指向后等待同伴的时长之和，单位为车·秒。

### 第四问从二（2）计划回放扰动

`retain` 使用 0 s 完整下发的协议。默认从已验收的数值方案读取车辆、路线与任务顺序，通过正式时间轴重新计算所有时刻。也可以用 `--plan` 直接读取 `baseline` 输出中的计划或独立的 `b.plan.v1` 文件。结果为逐情景 JSONL，每行含输入标识、逐车命令与事件、扰动后的状态，以及从双车观测事件重算的 `k/N`、`eta` 和 `t_last_s`。计划校验值写入每行，便于核对不同方法读到的初始计划。

```bash
python3 src/run.py retain
python3 checks/check_q4_retention.py
python3 src/run.py baseline --rule ready
python3 src/run.py retain --plan runs/b/formal/q22-baselines-ready-T1-T2-T3.json --output runs/b/formal/q4-retain-ready.jsonl
```

默认输出为 `runs/b/formal/q4-retain-q22-numerical-manifest.jsonl`。`check_q4_retention.py` 对照冻结的数值方案，独立审计逐车轨迹并逐行匹配探索版得分。扩展输入用 `--manifest experiments/b/q4_scenarios/sensitivity.json --allow-stress-recovery`，其中 35 s 恢复的记录标为域外压力测试。保持策略的后续命令已预装，因此通信恢复时刻只影响通信记录；健康车辆要等到原搭档就绪，原任务受阻会继续占用该车。[方案假设](experiments/b/q4/README.md) · [共同回放器](src/q4/replay.py)

### 第四问在线改派与成对复核

```bash
python3 src/run.py retain
python3 src/run.py reschedule
python3 checks/check_q4_pair.py
```

`reschedule` 接受与 `retain` 相同的 `--config`、`--plan`、`--manifest`、`--output` 和域外测试参数。默认输出为 `runs/b/formal/q4-reschedule-q22-numerical-manifest.jsonl`。两种策略从同一份计划出发，重调度器在可以传令时读取当前车辆位置、云台角、可用时刻及未锁定任务，返回建议的车对与路线。公共回放器执行命令，从真实接续点产生事件，并按照完成数优先、同数取最晚开始更早的口径与继续原计划比较。得分持平时沿用原队列。

`decision` 记录决策时刻、已开始的任务、保持及改派预测得分和最终采用的动作。最终得分从实际采用方案的双车观测事件重算。搜索设置沿用已验收实验，默认束宽 36，各车在其私有矩形内使用 4×4 网格、计划站位和当前点。更换初始计划后，如有健康车辆的决策位置落在其配置区域外，保留预装方案，原因写入 `decision.search`。设置可以通过 `--planner-config` 指定。[搜索参数](configs/b/q4-reschedule.json) · [调度策略](src/q4/reschedule.py)

扩展面板在两个运行命令上同时加 `--manifest experiments/b/q4_scenarios/sensitivity.json --allow-stress-recovery`，再执行 `python3 checks/check_q4_pair.py --panel sensitivity`。独立审计按情景 ID 检查两种策略的扰动前缀、物理事件与得分，并与冻结初始计划的归档对照表逐列比对。

## 协作约定

- 队员和 agent 开工前先查看本说明，优先沿用已有目录和文件。按任务范围创建文件，各问的代码、配置、结果和论文段落使用同一编号。
- 跨问共用的假设、数据口径或指标发生变化时，在 `docs/decisions.md` 记录依据和需要重跑的结果。待验证的思路先记在 `docs/modeling-notes.md`。
- 当前 B 题 worktree 的讨论入口为 [建模笔记](docs/modeling-notes.md)、[逐问验收清单](docs/b-acceptance.md) 和 [探索实验](experiments/README.md)。团队确认方法并用独立算例复核后，将采用的实现整理到 `src/`。
- 原始附件保持原样，实验产物先写入当前检出的 `runs/`。经队员复核并用于论文的产物放入 `results/qN/`，附上配置、运行命令和代码版本。
- `data/raw/C/`、`runs/`、`scratch/` 和 `submission/` 由 `.gitignore` 管理，保留在本地。手工创建的 worktree 默认放在同级的 `../SWBMCM2026-worktrees/`，由工具创建的 worktree 沿用工具自己的位置。
