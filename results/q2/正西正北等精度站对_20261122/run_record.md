# 运行记录｜第二问（1）正西—正北解析站对

- 冻结来源为 `explore/b-first-round@30d54fa397c370742cd34e5cd79808ebdb59ae46`。`scene.json` 复制自 `experiments/b/scene.json`。`q21_result.json` 包含 `experiments/b/snapshots/q2_analytic/summary.json` 和 `replay.json` 的 `q21` 部分及两份文件的 SHA-256。
- 在本工作树用 `checks.q2_validate.verify_q21` 按原题 `30 s` 时限、闭仰角、闭楼棱遮挡、全程 0.45 m 车体净空和连续车距复放两条直线。重算指标见 `stations.csv` 和 `q21_result.json`。图按同一全精度路点绘制。OpenCode / Magpie `group/gpt-6.2-sol` 于 2026-11-22 协助复核、出图与归档。

在队友工作树的上述提交运行解析构造和独立复核。原环境为 Python 3.10.12、NumPy 2.2.4、SciPy 1.15.2。输出位于该工作树的 `runs/b/q2_analytic/`。

```bash
nohup env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 -B experiments/b/q2_analytic/solve.py
nohup python3 -B experiments/b/q2_analytic/verify.py
```

也可在当前仓库根目录直接复核归档的路点和 RMSE。

```bash
nohup python3 -B -m checks.q2_validate \
  --q21 results/q2/正西正北等精度站对_20261122/q21_result.json \
  --require-vehicle-separation --wall-clearance-m 0.45
```
