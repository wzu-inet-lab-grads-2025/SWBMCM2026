# SWBMCM2026

本仓库用于三人团队备战和参加 2026 年数维杯全国大学生数学建模挑战赛（秋季赛），记录备赛调研与历年赛题演练，并组织正式比赛的建模代码、实验结果和论文。

## 项目目录

下面是团队约定的目录结构。目录随第一份实际内容创建，`qN` 代表实际小问编号，例如 `q3`。

```text
SWBMCM2026/
├── research/              # 专题调研，每篇笔记附参考文献
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

## B 题四问解法与结果

每份文档包含题意、数学推导、实现方法和可用于正式作答的结果，附图表、底表及复跑入口。

1. [第一问｜可观测区域与最优观测站](docs/B题第一问_可观测区域与最优观测站.md)
2. [第二问｜双站定位与多车并发调度](docs/B题第二问_双站定位与多车并发调度.md)
3. [第三问｜水平测向误差与行驶限程](docs/B题第三问_水平测向误差与行驶限程.md) · [参数敏感性](results/q3/测向参数敏感性与联合情景_20261122/conclusions.md)
4. [第四问｜扰动重调度与方案对比](docs/B题第四问_扰动重调度与方案对比.md)

四份中文文档供队内复核。正式提交采用英文 PDF，并按本届规则披露 AI 辅助环节。[论文格式规范](problem/official/2026年第十二届数维杯全国大学生数学建模挑战赛（秋季赛）论文格式规范.pdf) · [AI 使用规定](https://www.nmmcm.org.cn/News/220.html) · [结果归档索引](results/README.md)

## B 题复跑环境

在仓库根目录运行，复现环境为 Linux、Python 3.13。已验收的依赖版本见 [configs/requirements-b.txt](configs/requirements-b.txt)。

```bash
python3 -m venv .venv
source .venv/bin/activate
nohup python3 -m pip install -r configs/requirements-b.txt
nohup env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MPLBACKEND=Agg python3 -B -m pytest -q -p no:cacheprovider checks
```

统一运行入口为 `python3 -B -m src.run qN`。各问的归档配置和验收命令见对应文档，新增运行写入本地 `runs/qN/`。

## 协作约定

- 队员和 agent 开工前先查看本说明，优先沿用已有目录和文件。按任务范围创建文件，各问的代码、配置、结果和论文段落使用同一编号。
- 跨问共用的假设、数据口径或指标发生变化时，在 `docs/decisions.md` 记录依据和需要重跑的结果。待验证的思路先记在 `docs/modeling-notes.md`。
- 原始附件保持原样，实验产物先写入当前检出的 `runs/`。经队员复核并用于论文的产物放入 `results/qN/`，附上配置、运行命令和代码版本。
- `data/raw/C/`、`runs/`、`scratch/` 和 `submission/` 由 `.gitignore` 管理，保留在本地。手工创建的 worktree 默认放在同级的 `../SWBMCM2026-worktrees/`，由工具创建的 worktree 沿用工具自己的位置。
