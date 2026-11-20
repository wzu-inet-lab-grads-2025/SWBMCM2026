# C 题探索代码与轻量复现

本目录由原 `research/` 中的探索性代码等深度迁入，尚不代表队伍正式建模实现。成果笔记、参考文献和纯聚合目录 [`research/q2_diagnostic/`](../research/q2_diagnostic/) 留在 `research/`；经确认的正式实现以后按小问进入 `src/`。目录约定见[仓库说明](../README.md)。

## 文件与入口

- `check_c_data.py`、`c_audit_*.py`：附件盘点、字段/质量/片段/前缀核查。部分默认命令会遍历全量附件或视频，迁移验收只运行质量自测，不默认执行全量核查。
- `c_q1_quality_transitions.py` 统计训练集的坏段起点与持续，`c_q2_velocity_probe.py` 检验近期速度外推的适用时长，`c_q3_reservation_support.py` 核对分族预留估计的训练支持。三项的证据与待讨论事项见[工作树讨论记录](../docs/modeling-notes.md)。
- `c_q2_windows.py`、`c_q2_clock_check.py`、`c_q2_diagnostic.py`、`c_training_hand_check.py`、`test_c_q2_diagnostic.py`：Q2 窗口、固定动作时钟、双腕诊断及训练抽样检查。对应笔记和聚合图表见[Q2 诊断](../research/C题双腕缺测时长与回访诊断.md)。
- [`q3_replay/`](q3_replay/)：事件回放原型、官方数据适配、独立账本复核、排序基线及测试。配套合成日志和 `order_results/` 随代码迁移；[原验收笔记](../research/C题回放入口与评估验收.md)留在 `research/`。
- [`q3_prefix/`](q3_prefix/)：配对前缀、评分、类别中位数/腕部岭回归及测试，既有聚合 CSV 随目录迁移。真实输入数据库默认在 `runs/q3_prefix/private.sqlite`，不进 Git。
- [`c_q3_fixture/`](c_q3_fixture/)：固定动作合成台、输入、双作业测试及人工核对成品；[运行说明](c_q3_fixture/b_README.md)。独立[手算预期](../research/c_q3_hand_expected.md)及成果笔记保留在 `research/`。源码与笔记路径改变后，重生成来源清单及其哈希；合成事件、指标及历史提交标识不变。

## 环境、导入与资源

在仓库根目录运行以下命令。`PY` 应指向已安装 NumPy、pandas、PyArrow 的 Python；PDF 引文核查另需 pypdf，绘图另需 Matplotlib。当前机器可用：

```sh
PY=/mnt/disk/zhoutianyu/miniconda3/bin/python
CROOT=../SWBMCM2026/data/raw/C
```

顶层脚本支持 `"$PY" -B experiments/文件.py`，也支持 `"$PY" -B -m experiments.模块`。`q3_replay`、`q3_prefix` 使用包内相对导入，须从根目录以 `-m experiments.q3_replay.模块` / `-m experiments.q3_prefix.模块` 运行；不能将其单个文件直接执行。沿用 Python 命名空间包，不需 `__init__.py`；测试使用下方显式模块列表，避免普通 `unittest discover` 漏掉嵌套命名空间目录。

源码层级未变，`__file__` 定位的默认原始数据仍是本工作树的 `data/raw/C/`；支持 `--root` 的入口可以显式传 `"$CROOT"`。不支持该参数的合成台和 Q3 入口使用默认路径。同级主工作区已有附件时，仅在 `data/raw/C` 不存在的情况下创建本地链接（不要覆盖真实目录或现有链接）：

```sh
mkdir -p data/raw
ln -s ../../../SWBMCM2026/data/raw/C data/raw/C
```

`.gitignore` 同时忽略该位置的目录和符号链接。原始数据只读；真实前缀日志和新实验输出放在本工作树忽略的 `runs/`，不要放进探索源码目录。

## 轻量验收

以下为既有测试和固定样例，不重跑 Q3 九对排序实证，不读取测试集选模，也不扫视频。

```sh
"$PY" -B -m unittest -v \
  checks.test_c_preprocessing \
  checks.test_c_replay_reliability \
  experiments.test_c_q2_diagnostic \
  experiments.q3_replay.test_engine \
  experiments.q3_replay.test_order_baseline \
  experiments.q3_prefix.test_prefix \
  experiments.q3_prefix.test_wrist_ridge
"$PY" -B experiments/c_audit_quality.py --self-test
"$PY" -B experiments/c_q2_clock_check.py --root "$CROOT"
"$PY" -B experiments/c_training_hand_check.py --root "$CROOT"
"$PY" -B experiments/c_q3_fixture/pair_test.py --check
"$PY" -B -m experiments.q3_replay.acceptance pair
"$PY" -B -m experiments.q3_replay.acceptance verify-log \
  --input experiments/q3_replay/synthetic_calls.json
```

可选的真实数据冒烟仅使用固定 S1、公开验证种子 `2026111700` 和既定动作，不作策略比较；日志含真实前缀，必须留本地：

```sh
mkdir -p runs
"$PY" -B -m experiments.q3_replay.acceptance smoke --output runs/c-migration-smoke.json
"$PY" -B -m experiments.q3_replay.acceptance verify-log --input runs/c-migration-smoke.json
```

`b_fixture.py` 会覆写已跟踪的 `c_q3_fixture/b_output/` 合成成品，`pair_test.py` 不带 `--check` 会覆写 `pair_events.jsonl`；只有有意更新合成台或来源哈希时才运行。历史提交和提交内旧路径仍按原提交理解，不改历史链接。全量诊断、前缀建库/拟合及 Q3 九对实证的命令仅在相关成果笔记中保留，日常迁移检查无需执行。
