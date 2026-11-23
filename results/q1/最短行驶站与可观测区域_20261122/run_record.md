# 运行记录｜第一问最短行驶择优

- 冻结来源为 `explore/b-first-round@30d54fa397c370742cd34e5cd79808ebdb59ae46`，输入来自 `experiments/b/q1_independent/scene.json`。求解快照 `experiments/b/snapshots/q1/` 的 `result.json`、两份核验 CSV 和两种图件格式均逐字节归档。原始运行命令、Python 版本和输入哈希见 `result.json` 的 `provenance`。
- 跨分支复核用 `checks.q2_geometry.check_route` 和 `station_pointing` 独立计算整条直线的车体约束、闭合楼体视线和仰角。`station.csv` 记录原始坐标及复算指标。两处分支的轨迹长度在 \(10^{-11}\) m 内一致。OpenCode / Magpie `group/gpt-6.2-sol` 于 2026-11-22 协助复核和归档。

在队友工作树的上述提交运行，依赖见 `experiments/b/q1_independent/requirements.txt`。输出位于该工作树的 `runs/b/q1_independent/`。

```bash
nohup env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MPLBACKEND=Agg \
  python3 -B experiments/b/q1_independent/solve.py \
  --seed 20261120 --samples 250000 --plot-step 0.1 --grid-steps 0.2 0.1 0.05
```

比较 `result.json` 的 `certificate`、连续圆弧和面积积分。随机验证与图件的重现还取决于依赖版本。
