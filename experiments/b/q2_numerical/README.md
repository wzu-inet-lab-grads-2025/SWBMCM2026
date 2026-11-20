# 独立路线 B：Problem 2(1) 与 2(2)

只读共享 `experiments/b/scene.json`；不导入基线脚本、第一问程序或其他代理实现。报告见 `REPORT.md`。

## 复跑

仓库根目录，Python 3.10+、NumPy、SciPy（本次 2.2.4 / 1.15.2）。不需要绘图包或 Shapely。

```bash
OPENBLAS_NUM_THREADS=1 python3 experiments/b/q2_numerical/search.py
OPENBLAS_NUM_THREADS=1 python3 experiments/b/q2_numerical/bounds.py
OPENBLAS_NUM_THREADS=1 python3 experiments/b/q2_numerical/strengthen_bound.py
OPENBLAS_NUM_THREADS=1 python3 experiments/b/q2_numerical/check_bounds.py
OPENBLAS_NUM_THREADS=1 python3 experiments/b/q2_numerical/baselines.py
OPENBLAS_NUM_THREADS=1 python3 experiments/b/q2_numerical/finite_search.py
OPENBLAS_NUM_THREADS=1 python3 experiments/b/q2_numerical/sensitivity.py
OPENBLAS_NUM_THREADS=1 python3 experiments/b/q2_numerical/replay.py
python3 experiments/b/q2_numerical/finalize.py
```

总运行时间约数分钟；`strengthen_bound.py` 的运行时间随盒细分数变化。没有下载、外部服务或随机秘密参数。搜索种子见程序及 JSON，输出目录固定为 `runs/b/q2_numerical/`。

## 文件

- `geometry.py`：独立圆盘避障、有限三维视线、指向与可行折线路由。楼角路由使用保守方形外偏移顶点；实际交付路线全部为经精确扫掠检查的直线，所以不涉及近似绕行最短性。
- `search.py`：T2 正交等距离站对构造、百万独立高斯噪声交会、连续多起点调度搜索。
- `bounds.py`：鸽巢复用下界与连续盒分支定界，含楼体圆盘/视线阴影不可行盒剔除。
- `strengthen_bound.py`：先求首次单车就绪下界，再加入第一项任务同伴约束。
- `baselines.py`：同候选、同路线、同处理顺序的两种距离规则和当前最早共同就绪；独立零行驶穷举。
- `finite_search.py`：9 个共同候选上所有车对、所有复用和六种顺序的精确有限搜索。
- `sensitivity.py`：闭仰角边界、开遮挡边界及 1 mm 内缩部署。
- `replay.py`：**不导入上述实现**；重新读取场景，分段二次函数检查扫掠圆盘；先二维裁剪再检查视线高度，重建逐车事件。
- `finalize.py`：把证据索引与界并入 `summary.json`，生成报告所需文本摘要。

## 主要产物

- `runs/b/q2_numerical/summary.json`：双精度可交换结果，含要求的 `q21.legs` 和按实际共同开始排序的 `q22.jobs`。
- `events.csv`：30 个逐车阶段（含零长行驶/等待）及各时刻。
- `replay.json`：独立几何检查、事件、所有基线复核、脚本与输入哈希。
- `baselines.csv` / `baselines.json`：90 份配对计划及原始分配。
- `q22_search.json` / `single_ready.json`：包括未成功及不可行局部尝试的原始记录。
- `q22_lower_bound.json` / `q22_strong_lower_bound.json`：连续下界细分过程与停止原因。
- `finite_search.json`：共同候选有限最优证据。
- `sensitivity.json`：毫米安全内缩后的完整方案。
- `english_source.txt`：英文 PDF 文本提取（部分数学字形编码损坏，数值按正文与共享场景核对）。

不得把 `q21` 的 30 s 行驶限制施加给 `q22`；二（2）的 32.907 m / 17.907 m 等搜索界均由已知可行目标值推导。初始静止云台取正东水平，水平轴允许最短转角，与团队基线一致。站位车体相切允许，光学相切禁止；未加入第三问噪声或第四问扰动。
