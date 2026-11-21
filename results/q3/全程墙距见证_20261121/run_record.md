# B 题第三问 · 墙距见证复核记录

本地输出为 `runs/q3/全程墙距见证_20261121`，Linux、Python 3.13.9。验收来自基于 `23158125c5fdecb80e9882e32a135c79a765ec02` 的工作树。场地来自 `configs/q1.yaml`；路点复制自 `configs/q3_wall_route.json`。PDF/PNG 为据同一场地和路点绘制的示意图，不参与几何证书。AI 辅助：OpenCode / Magpie `group/gpt-6.2-sol`，版本发布日期未提供。

```bash
q3_safe=results/q3/全程墙距见证_20261121
nohup python3 -B -m checks.b_wall_clearance q3 "$q3_safe/route.json" \
  --wall-clearance-m 0.45 --execution-error-bound-m 0.40 \
  --output runs/q3/wall_route_45cm_replay.json
cmp "$q3_safe/route_validation.json" runs/q3/wall_route_45cm_replay.json
```

独立入口检查整段和停车路点。全量回归另覆盖“两端安全但中间穿楼”的拒收样例。测向里程与参数域另由 `checks.q3_reference` [独立复算](../测向可靠性与80米定位约束_五方法对照_20261120/run_record.md)。
