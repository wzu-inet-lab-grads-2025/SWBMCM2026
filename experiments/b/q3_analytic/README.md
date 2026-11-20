# Q3 独立解析：复跑说明

主报告见 [`REPORT.md`](REPORT.md)。本目录只从官方 PDF、中文译文和 `experiments/b/scene.json` 独立建模；不引用其他 Q3 代理稿或代码。

## 直接复跑（仓库根目录）

```bash
python3 experiments/b/q3_analytic/solve.py --extract-source
python3 experiments/b/q3_analytic/verify.py
```

- 第一条默认在 0 m、圆周模型实际指向上限、80 m 各模拟 1,000,000 样本，并做确定性积分验证。种子起始值 `20261120`。
- 第二条运行 6 组确定性测试，已全部通过。
- 输出只允许在 `runs/b/q3_analytic/` 或本实验目录内；公共 `scene.json` 为只读。
- 此环境的 Python 命令为 `python3`，不是 `python`。

只需解析和积分结果时可省略模拟及 PDF 提取：

```bash
python3 experiments/b/q3_analytic/solve.py --mc-samples 0
```

更高样本量（每个检查点）：

```bash
python3 experiments/b/q3_analytic/solve.py --mc-samples 10000000 --seed 20261120
```

只要输入未改变，确定性数值可复跑；不同 NumPy / SciPy 版本可能在末几位或随机序列实现上有微小差异。

## 依赖

本次实际使用 Python 3.10.12、NumPy 2.2.4、SciPy 1.15.2、pypdf 6.21.4，锁定版本列于 `requirements.txt`。NumPy / SciPy 是计算必需；pypdf 只用于 `--extract-source`。

若当前环境缺依赖，可在允许的结果目录内隔离安装，不修改公共环境：

```bash
python3 -m venv runs/b/q3_analytic/venv
runs/b/q3_analytic/venv/bin/python -m pip install -r experiments/b/q3_analytic/requirements.txt
runs/b/q3_analytic/venv/bin/python experiments/b/q3_analytic/solve.py --extract-source
runs/b/q3_analytic/venv/bin/python experiments/b/q3_analytic/verify.py
```

原文页图只是人工核对留档，复跑计算不依赖 PDF 渲染器；`--extract-source` 重建第 4–5 页文本即可。

## 产物

均位于 `runs/b/q3_analytic/`：

- `compact.json`：单行紧凑结果；含两口径的一阶／圆周阈值、80 m 可行区域及 80 m 概率。
- `summary.json`：输入校验、一阶方差贡献、交叉里程、两积分核验、极限检验和可选蒙特卡洛。
- `quadrature_checks.json`：16 次独立积分比较。
- `english_pages_4_5.txt`：官方英文原文摘录（`--extract-source`）。
- `english_page_4.png`、`english_page_5.png`：本次渲染查看的页图留档。

## 口径与核心数字

- 原题字面 Δψ 为指令偏差 `wrap(command - true)`。
- 工程主报告另定义实际指向为 `wrap(command - true + zero + control)`；不是在不声明的情况下偷换原定义。
- 实际指向圆周上限 **66.837258 m**，指令口径 **73.936066 m**。
- 80 m 时分别要求 **a + 80b ≤ 1.636745169**、**a + 80b ≤ 1.778721312**，且 a、b 非负。
- 一阶限程分别为 66.922299、74.049047 m；卡 0.95 时需按圆周模型略收紧。

运行 `solve.py` 会覆盖本实验自己的结果文件；不会提交或改写公共文件。
