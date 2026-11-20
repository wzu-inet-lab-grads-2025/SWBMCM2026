# B 题第一问运行摘要

可观测面积为等面积网格中心采样估计。主目标由仰角距离下界和可行见证点证明。净空择优用经几何核验的可行弧作分支定界，并检查东侧场界与 B2 东北角的解析上界。所报误差界含浮点余量。

| Cell size (m) | Observable cells | Estimated area (m²) |
|---:|---:|---:|
| 0.5 | 22432 | 5608.000000 |
| 0.25 | 89839 | 5614.937500 |
| 0.125 | 358908 | 5607.937500 |

## 完整并列最优解集

圆心为 $T_{xy}\approx(84.641016151378, 70)$ m，圆周角 $\varphi$ 从正东逆时针量取。所有主目标最优站位均达到 $r_0=(z_T-h_c)/\tan\alpha_{\max}\approx7.582060170464$ m，$Q^\star\approx0.131890274875$ m$^{-1}$，仰角均为 $80^\circ$。

$$\mathcal S_1=\{T_{xy}+r_0(\cos\varphi,\sin\varphi):\varphi\in\Phi^\star\}.$$

精确角域为 $\Phi^\star=[0^\circ,\varphi_N]\cup[\varphi_E,360^\circ)$，其中 $\varphi_N=180^\circ-\arcsin((y_{2,\max}+\rho-y_T)/r_0)$，$\varphi_E=360^\circ-\arccos((x_{2,\max}+\rho-x_T)/r_0)$。反三角函数取度，两个边界量为 B2 底面坐标上界，$\rho$ 为车体半径。

$\Phi^\star$ 的数值表示为 $[0^\circ,160.747873328^\circ] \cup [236.896046854^\circ,360^\circ)$（端点角度为近似值）。这是一条跨过正东方向的连续圆弧，因此有无穷多个并列解。两个 B2 车体相切端点均可行；360° 与 0° 是同一点。见 [最优圆可行弧图](optimal_arcs.png)。

### 代表站点

下表的站点均达到相同的主目标值，所列站点只用于展示，不是最优解集的枚举。

| Primary-optimal example | Circle angle (°) | Station (m) | Minimum vehicle clearance (m) |
|---|---:|---:|---:|
| Due east (witness) | 0.000000000 | (92.223076322, 70.000000000) | 6.276923678 |
| Due north | 90.000000000 | (84.641016151, 77.582060170) | 7.168597309 |
| B2 north-side tangency | 160.747873328 | (77.482969181, 72.500000000) | 0.000000000 |
| B2 east-side tangency | 236.896046854 | (80.500000000, 63.648651981) | 0.000000000 |
| Southeast clearance peak | 309.855433765 | (89.500000000, 64.179526446) | 9.000000000 |
| Recommended station | 56.503855631 | (88.825410107, 76.322854075) | 9.674589893 |

## 并列解选择规则

1. **主目标：**只在达到最小水平距离、即达到最大 $Q$ 的可行站点中择优。
2. **并列择优：**在整条 $\mathcal S_1$ 上最大化最小车辆净空，而不是仅在表中比较。

   $$c(P)=\min\left\{x-\rho, W-\rho-x, y-\rho, H_f-\rho-y, \min_i[d(P,R_i)-\rho]\right\}.$$

   $W$、$H_f$ 是场地宽度和高度，$R_i$ 是第 $i$ 栋建筑的底面，$d(P,R_i)$ 是车心到该底面的距离。较大的 $c(P)$ 给车辆停放留出更大余量，不牺牲 $Q$，也不改变视线和仰角约束。

3. **仍并列：**净空在浮点舍入余量内仍相同时，先取较小的 $x$，再取较小的 $y$；最终站位仍须通过几何复核和净空全域误差界检查。

## 最终推荐站位

| Station result | Value |
|---|---:|
| Station (m) | (88.825410107, 76.322854075) |
| Horizontal distance (m) | 7.582060170464 |
| Elevation (degrees) | 80.000000000 |
| Quality (m⁻¹) | 0.131890274875 |
| Minimum clearance (m) | 9.6745898929 |
| Secondary optimality gap (m) | 9.1482e-13 |

理论站位由 $P=T_{xy}+r_0(\cos\varphi,\sin\varphi)$ 定义，$\varphi\approx 56.503855631^\circ$。推荐点受东场界和 B2 东北角的净空共同限制。两张表的坐标与角度已舍入，几何复核使用 `summary.json` 中的完整坐标。

按本次快照配置复跑

```bash
nohup /mnt/disk/yuanyilun/miniconda3/bin/python3 -B -m src.run q1 --profile full --config /mnt/disk/yuanyilun/projects/SWBMCM2026/runs/q1/20261120T105624Z_byf9quhb/config.yaml
```

`summary.json` 记录实际运行命令、代码哈希、角段、上界与逐栋复核。
实现过程中使用 OpenCode 中的 Codex（gpt-6.2-sol）辅助编程与复核。
