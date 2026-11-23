# 第三问参数敏感性复跑记录

- 原始运行：`runs/q3/sensitivity_20261122T071314Z_mg9qk1nm/`。运行基线 `1a4374e0c28d55944a8dc69cd813075de6113486`，增补脚本与配置的确切指纹见 [run_record.json](run_record.json)。
- [nominal_config.yaml](nominal_config.yaml) 为第三问五方法主归档的配置快照，[config.yaml](config.yaml) 仅记录相对扰动档位。试验沿用一维投影正态分段积分，不对单项扰动重复运行五种方法。
- 底表 `sensitivity.csv` 的 `relative_change` 是相对标称值的小数；联合场景中它跟随四项误差参数，$D$ 则反向变化。`delta_probability_at_design_pp` 以百分点计。PDF/PNG 只画实际指向的单因素结果；联合情景和软件指令角均可在底表复核。

以下命令在仓库根目录运行，新结果写入本地 `runs/q3/`，不覆盖归档：

```bash
q3_sensitivity=results/q3/测向参数敏感性与联合情景_20261122
nohup env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MPLBACKEND=Agg python3 -B -m src.run q3 \
  --sensitivity "$q3_sensitivity/config.yaml" --config "$q3_sensitivity/nominal_config.yaml"
nohup env OPENBLAS_NUM_THREADS=1 python3 -B -m checks.q3_sensitivity_reference "$q3_sensitivity"
```

独立复算也可读取新输出路径。该程序使用[既有高斯半平面公式](../../../checks/q3_reference.py)和[工况验收](../../../checks/q3_sensitivity_reference.py)，对全部工况重新求根并复算 80 m 概率，最大里程差为 $1.30\times10^{-10}$ m，80 m 概率最大绝对差为 $3.33\times10^{-16}$。达标方向 66 条全部独立确认。[独立验收](reference_check.json) · [主实验归档](../测向可靠性与80米定位约束_五方法对照_20261120/conclusions.md)
