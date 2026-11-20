# 第三问运行记录

原始运行目录 `runs/q3/20261120T132344Z_ixasqkep`，档位 `full`。
计算及验收耗时 8.056 s。Python 3.13.9。
Git 基准 `e22186b25a6b7187405522f6cf0758e6b2a737b8`。本次源文件与参数快照的 SHA-256 见 `run_record.json`。

```bash
nohup python3 -B -m src.run q3 --profile full
```

在仓库根目录使用本次参数复算。

```bash
nohup python3 -B -m src.run q3 --profile full \
  --config results/q3/测向可靠性与80米定位约束_五方法对照_20261120/config.yaml
```

| 方法 | 进程 PID | 相对首个任务启动 (s) | 执行耗时 (s) |
|---|---:|---:|---:|
| taylor | 1809794 | 0.000056 | 0.000172 |
| hermite | 1809793 | 0.000084 | 0.112962 |
| projected_normal | 1809792 | 0.000201 | 0.113257 |
| monte_carlo | 1809788 | 0.000251 | 2.144978 |
| sobol | 1809791 | 0.000000 | 6.168478 |

方法进程在同一屏障后启动。进程耗时含各自核对工况，排除父进程启动及绘图。
验收通过，逐项记录见 [summary.json](summary.json)，进程和独立复算的实际输出见 [run.log](run.log)。

配置快照保留 Hermite 阶数、Monte Carlo 样本规模与种子、Sobol 幂次与 scrambling 种子。
归档的中文结论按题面定义先列软件指令角结果。此调整只涉及文字呈现，数值底表与原始运行一致。
AI 辅助信息见 `run_record.json`，参赛使用按组委会要求披露。[本届规定](https://www.nmmcm.org.cn/News/220.html)

## 独立复核

高斯半平面法在关键工况复算达标概率，并独立求两条临界根。两种口径的临界定位标准差与主方法的绝对差分别为 $4.44\times10^{-16}$ m 和 $7.77\times10^{-15}$ m。全部 68 项仓库检查通过。[复算明细](reference_check.json)

```bash
nohup python3 -B -m checks.q3_reference \
  results/q3/测向可靠性与80米定位约束_五方法对照_20261120
nohup python3 -B -m pytest -q -p no:cacheprovider checks
```
