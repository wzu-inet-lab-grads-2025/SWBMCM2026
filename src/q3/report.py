"""q3 结果导出，输入已计算的概率、验证记录和参数配置。
供运行器输出 CSV、JSON、中文结论、复跑记录及英文论文图到本次运行目录。
单独生成 nohup python3 -B -m src.run q3 --profile full。
AI 辅助 OpenCode（Anomaly）与 Magpie gpt-6.2-sol（OpenAI，版本发布日期未公开），2026-11-20。
"""

import csv
import json
from pathlib import Path
import shlex

from .config import Config
from .model import MODES


MODE_LABELS = {"actual": "实际指向", "command": "软件指令角"}
SOURCE_LABELS = {"localization": "车辆定位", "zero": "云台零位", "control": "云台控制"}
SITE_LABELS = {"initial": "初始", "critical": "临界", "at_design": "设计里程"}
METHOD_LABELS = {
    "taylor": ("一阶泰勒", "解析近似"),
    "hermite": ("Gauss–Hermite", "原始高斯坐标复核"),
    "projected_normal": ("一维投影正态积分", "几何主结果"),
    "monte_carlo": ("直接 Monte Carlo", "独立四元抽样"),
    "sobol": ("Scrambled Sobol", "重复低差异积分"),
}


def _write_csv(path: Path, rows: list[dict]) -> None:
    """统一用 UTF-8 和稳定列顺序写出底表，None 为留空。"""
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, data: dict) -> None:
    """写可复核 JSON，拒绝非标准无穷或 NaN 数值。"""
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _mode_sites(summary: dict, mode: str) -> dict:
    """按标签取得同一口径的关键工况。"""
    return {row["label"]: row for row in summary["sites"] if row["mode"] == mode}


def _inputs_csv(cfg: Config) -> list[dict]:
    """列出第三问题设参数、单位和回绕区间。"""
    return [{"parameter": name, "value": value, "unit": unit} for name, value, unit in (
        ("target_horizontal_distance", cfg.model.range_m, "m"),
        ("baseline_localization_sigma(s)", f"{cfg.initial_sigma_m:g} + {cfg.growth:g} s", "m per axis"),
        ("gimbal_zero_offset_sigma", cfg.model.zero_deg, "deg"),
        ("gimbal_control_sigma", cfg.model.control_deg, "deg"),
        ("absolute_error_threshold", cfg.model.threshold_deg, "deg"),
        ("minimum_probability", cfg.minimum_probability, "dimensionless"),
        ("angular_wrapping_interval", "[-180, 180)", "deg"),
        ("design_cumulative_distance", cfg.design_distance_m, "m"),
    )]


def write_records(directory: Path, cfg: Config, result: dict, metadata: dict) -> None:
    """完整保存数值及验收证据，供通过和失败的运行都能复盘。"""
    summary, methods = result["summary"], result["methods"]
    stochastic = summary["validation"]["stochastic"]
    start = min(row["started_monotonic_s"] for row in methods.values())
    metadata["profile"] = cfg.profile
    metadata["workers"] = [{"method": name, "pid": row["pid"], "start_offset_s": row["started_monotonic_s"] - start,
                            "runtime_s": row["elapsed_s"]} for name, row in methods.items()]
    _write_json(directory / "summary.json", summary)
    _write_json(directory / "run_record.json", metadata)
    definitions = [
        {"mode": "actual", "error": "wrap180(alpha + zero + control)", "localization": True,
         "zero": True, "control": True, "use": "primary, includes all stated sources"},
        {"mode": "command", "error": "wrap180(alpha)", "localization": True,
         "zero": False, "control": False, "use": "literal wording sensitivity"},
    ]
    limits = [{"mode": mode, **limit, **{f"probability_{label}": sites.get(label, {}).get("probability")
                                        for label in ("initial", "below_deterministic", "above_deterministic", "operational_floor", "at_design")}}
              for mode, limit in summary["limits"].items() for sites in (_mode_sites(summary, mode),)]
    for stem, rows in {
        "parameters": _inputs_csv(cfg), "definitions": definitions, "limits": limits,
        "method_comparison": summary["comparison_at_critical"], "method_runs": metadata["workers"],
        "taylor_sites": methods["taylor"]["sites"], "projected_sites": methods["projected_normal"]["sites"],
        "projected_segments": methods["projected_normal"]["segments"], "gauss_hermite": methods["hermite"]["levels"],
        "monte_carlo_sites": stochastic["monte_carlo_sites"], "monte_carlo_seeds": methods["monte_carlo"]["replicates"],
        "sobol_convergence": stochastic["sobol_levels"], "sobol_scrambles": methods["sobol"]["replicates"],
        "monotonicity": result["scan"], "reliability": result["curve"], "ab_boundary": result["ab_curve"],
        "ab_boundary_checks": summary["ab_boundary_checks"], "error_contributions": summary["contributions"]["rows"],
    }.items():
        if rows:
            _write_csv(directory / f"{stem}.csv", rows)
    _write_run_record(directory, metadata, summary)


def _write_run_record(directory: Path, metadata: dict, summary: dict) -> None:
    """生成运行命令、版本、各进程计时和验收结果的中文运行记录。"""
    validation = summary["validation"]
    command = f"nohup python3 -B -m src.run q3 --profile {summary['profile']} --config {shlex.quote(str(directory / 'config.yaml'))}"
    lines = ["# 第三问运行记录", "", f"运行目录 `{directory.name}`，档位 `{summary['profile']}`。",
             f"计算及验收耗时 {metadata['elapsed_s']:.3f} s。Python {metadata['python']}。",
             f"Git 基准 `{metadata['git_head'] or '无 Git 元数据'}`。本次源文件与参数快照的 SHA-256 见 `run_record.json`。",
             "", "```bash", f"nohup {metadata['command']}", "```", "",
             "在仓库根目录使用本次参数复算。", "", "```bash", command, "```", "",
             "| 方法 | 进程 PID | 相对首个任务启动 (s) | 执行耗时 (s) |", "|---|---:|---:|---:|"]
    for row in metadata["workers"]:
        lines.append(f"| {row['method']} | {row['pid']} | {row['start_offset_s']:.6f} | {row['runtime_s']:.6f} |")
    lines.extend(["", "方法进程在同一屏障后启动。进程耗时含各自核对工况，排除父进程启动及绘图。",
                  f"验收{'通过' if validation['passed'] else '失败'}，逐项记录详见 `summary.json`。",
                  "", "配置快照保留 Hermite 阶数、Monte Carlo 样本规模与种子、Sobol 幂次与 scrambling 种子。",
                  "AI 辅助信息见 `run_record.json`，参赛使用按组委会要求披露。[本届规定](https://www.nmmcm.org.cn/News/220.html)"])
    (directory / "run_record.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _format(value: float | None, digits: int = 8) -> str:
    """固定小数位显示有限数，未定义的结果以长横线表示。"""
    return f"{value:.{digits}f}" if value is not None else "—"


def _mode_results(lines: list[str], cfg: Config, summary: dict, mode: str) -> None:
    """写单一角度口径的临界里程、边界回代及设计参数约束。"""
    cap = cfg.design_distance_m
    limit = summary["limits"][mode]
    sites = _mode_sites(summary, mode)
    end = sites["at_design"]
    travel = limit["travel_m"]
    lines.extend([f"### {MODE_LABELS[mode]}", "",
                  f"临界每轴定位标准差为 **{limit['sigma_m']:.10f} m**，最大累计里程为 **{travel:.10f} m**。"
                  if travel is not None else f"临界每轴定位标准差为 {limit['sigma_m']:.10f} m，初始状态已低于达标概率。",
                  f"基准定位模型在 {cap:g} m 的标准差为 {end['sigma_m']:.4f} m，达标概率为 **{end['probability']:.8%}**。",
                  "", "| 检验点 | 累计里程 (m) | 达标概率 |", "|---|---:|---:|"])
    for label, text in (("initial", "初始"), ("below_deterministic", "临界前"), ("operational_floor", "厘米档上限"),
                        ("critical", "临界"), ("above_deterministic", "临界后"), ("at_design", "设计里程")):
        if label in sites:
            row = sites[label]
            lines.append(f"| {text} | {_format(row['distance_m'], 8)} | {row['probability']:.12f} |")
    lines.extend(["", f"对 $a,b\\geq0$，{cap:g} m 的每轴标准差 $a+{cap:g}b$ 必须不超过该口径的 $\\sigma_*$。"
                  f"边界截距分别为 $a={limit['a_intercept_m']:.10f}$ m 和 $b={limit['b_intercept_m_per_m']:.10f}$ m/m。", "",
                  f"保留 $a={cfg.initial_sigma_m:g}$ m 时，$b$ 上限为 {_format(limit['b_max_at_nominal_a_m_per_m'], 10)} m/m。"
                  f"保留 $b={cfg.growth:g}$ m/m 时，$a$ 上限为 {_format(limit['a_max_at_nominal_b_m'], 10)} m。"
                  "上限不可行时表中留空。两端和中点的概率复算见 `ab_boundary_checks.csv`。", ""])


def _method_tables(lines: list[str], summary: dict) -> None:
    """按两种角度口径分别输出同工况的五方法表。"""
    for mode in MODES:
        lines.extend([f"### {MODE_LABELS[mode]}临界工况", "",
                      "| 方法 | 临界工况概率 | 相对主积分差值 | 该方法求得的里程 (m) | 概率区间 | 耗时 (s) | 用途 |",
                      "|---|---:|---:|---:|---|---:|---|"])
        for row in summary["comparison_at_critical"]:
            if row["mode"] != mode:
                continue
            name, role = METHOD_LABELS[row["method"]]
            interval = (f"[{row['interval_low']:.8f}, {row['interval_high']:.8f}]"
                        if row["interval_low"] is not None else "—")
            lines.append(f"| {name} | {row['probability_at_reference_root']:.10f} | {row['probability_difference']:+.3g} | "
                         f"{_format(row['maximum_distance_m'])} | {interval} | {row['runtime_s']:.4f} | {role} |")
        lines.append("")


def _write_conclusions(directory: Path, cfg: Config, summary: dict) -> None:
    """按题面六项要求组织模型、临界结果、验证和部署依据。"""
    val = summary["validation"]
    det, stochastic = val["deterministic"], val["stochastic"]
    limits = summary["limits"]
    a = limits["actual"]
    target = cfg.minimum_probability
    cap = cfg.design_distance_m
    actual_sites = _mode_sites(summary, "actual")
    taylor = next(row for row in summary["comparison_at_critical"] if row["mode"] == "actual" and row["method"] == "taylor")
    lines = ["# B 题第三问｜水平测向误差与累计行驶里程", ""]
    if cfg.profile == "debug":
        lines.extend(["此档用于入口冒烟。提交候选数值须以 full 档独立复核。", ""])
    if a["travel_m"] is not None:
        lines.extend([f"**主结果**　实际指向的最大累计里程为 **{a['travel_m']:.9f} m**，"
                      f"按厘米设置上限时可采用 **{_format(a['floor_to_centimeter_m'], 2)} m**。在 {cap:g} m 仍保持 "
                      f"{target:.0%} 达标率的条件为 $a,b\\geq0$ 且 $a+{cap:g}b\\leq\\sigma_*$，"
                      f"其中 $\\sigma_*\\approx {a['sigma_m']:.10f}$ m。", ""])
    lines.extend([
        "## 误差传播与概率", "",
        f"以车辆真实位置为原点，真实视线为正 $X$ 轴。已知目标在 $(D,0)$，$D={cfg.model.range_m:g}$ m。"
        f"两个定位误差分量独立同分布为 $N(0,\\sigma^2)$，基准 $\\sigma={cfg.initial_sigma_m:g}+{cfg.growth:g}s$ m。"
        "各向同性保证旋转后仍为独立的同方差正态误差。软件指令偏差为 $\\alpha=\\operatorname{atan2}(-\\delta_y,D-\\delta_x)$。", "",
        f"实际指向再加上独立的零位和控制误差，标准差分别为 {cfg.model.zero_deg:g}° 与 {cfg.model.control_deg:g}°。"
        f"相加后回绕至 $[-180^\\circ,180^\\circ)$，得 $\\Delta\\psi_{{\\rm act}}$。计算 $P(|\\Delta\\psi|\\leq {cfg.model.threshold_deg:g}^\\circ)$。"
        "原题将角度写作软件指令与真实方位之差，同时要求比较三类误差的影响。因此主表用计入三个误差源的实际指向，"
        "另计算 $\\Delta\\psi_{\\rm cmd}=\\operatorname{wrap}(\\alpha)$ 的字面口径。", "",
        "给定 $\\alpha$，云台合成误差为 $N(0,\\sigma_g^2)$。在所有间隔 $360^\\circ$ 的成功窗口内对该正态分布求和，得到 $G(\\alpha)$。"
        "式中 $\\alpha$、$\\theta$、$\\sigma_g$ 均使用度。", "",
        r"$$ G(\alpha)=\sum_{k\in\mathbb Z}\left[\Phi\!\left(\frac{\theta-\alpha+360k}{\sigma_g}\right)-\Phi\!\left(\frac{-\theta-\alpha+360k}{\sigma_g}\right)\right]. $$", "",
        "在极坐标下对二维高斯密度积分掉径向变量，得到方位角的投影正态密度。令 $\\kappa=D/\\sigma$，密度变量 $u$ 为弧度。", "",
        r"$$ f_{\alpha}(u)=\frac{e^{-\kappa^2/2}}{2\pi}+\frac{\kappa\cos u}{\sqrt{2\pi}}e^{-\kappa^2\sin^2 u/2}\Phi(\kappa\cos u). $$", "",
        r"$$ R_{\rm act}(\sigma)=\int_{-\pi}^{\pi}f_{\alpha}(u)G(180u/\pi)\,du,\qquad R_{\rm cmd}(\sigma)=\int_{-\pi\theta/180}^{\pi\theta/180}f_{\alpha}(u)\,du. $$", "",
        "以同一对标准正态变量表示定位误差时，$|\\alpha|=\\operatorname{atan2}(|Z_y|,D/\\sigma+Z_x)$ 随 $\\sigma$ 严格增大。"
        "纯指令的成功楔形随之严格收缩，云台回绕正态在半圆上严格递减也使实际指向的条件成功概率严格递减。"
        "从零定位误差到均匀方位极限，两种概率都穿过可靠性门槛，故唯一根同时给出全局最大允许标准差。"
        "[单调性完整证明](../../../docs/B题第三问_方案审核与实现说明.md)", "",
        "## 最大累计里程及设计参数", "",
    ])
    for mode in MODES:
        _mode_results(lines, cfg, summary, mode)
    lines.extend(["## 五方法核验与选择", "",
                  f"主结果选择**一维投影正态分段自适应积分**。分段和容差复算的最大概率差为 {det['maximum_quad_split_difference']:.3g}，"
                  f"角密度归一化最大偏差为 {det['maximum_density_mass_error']:.3g}。两个高阶 Hermite 口径与主积分的最大概率差为 "
                  f"{det['maximum_hermite_difference']:.3g}。指令角用横向条件化的 Hermite 求积，原始二维指示函数的跳变记录见 `gauss_hermite.csv`。", ""])
    if a["travel_m"] is not None and taylor["maximum_distance_m"] is not None:
        gap = taylor["maximum_distance_m"] - a["travel_m"]
        lines.extend([f"主口径临界处 $\\sigma/D={a['sigma_m'] / cfg.model.range_m:.4f}$，一阶横向传播适用于估计尺度和误差来源。"
                      f"一阶里程减去原始几何里程为 {gap:+.6f} m，精确根采用 `atan2` 几何。", ""])
    bound = summary["gimbal"]["actual"]["log10_omitted_wrap_probability_bound"]
    if bound is not None:
        lines.extend([f"周期窗截断的概率误差上限为 $10^{{{bound:.2f}}}$。"
                      f"单调性扫描包含 {cfg.validation.monotonic_points + 1} 个标准差点，密度最小值与极限趋势见 `monotonicity.csv`。", ""])
    _method_tables(lines, summary)
    mc = stochastic["monte_carlo_sites"]
    wilson_count = sum(row["wilson_contains_reference"] for row in mc)
    intervals_count = sum(row["simultaneous_contains_reference"] for row in mc)
    boundary_message = (f"临界点两侧的 {stochastic['boundary_tests']} 个抽样工况均通过门槛判别。"
                        if stochastic["checks"]["mc_boundary_classification"] else "冒烟档样本量仅供查看估计区间。")
    lines.extend([f"Monte Carlo 每个工况汇总 {mc[0]['samples']:,} 次试验。逐点 Wilson 区间覆盖 {wilson_count}/{len(mc)} 个确定性概率，"
                  f"Bonferroni 修正的精确同时区间覆盖 {intervals_count}/{len(mc)} 个。"
                  "同一口径下相邻工况使用共同随机数，区间偏移可以相关。"
                  f"{boundary_message}", "",
                  f"Sobol 的表中区间由 {len(cfg.sampling.sobol_seeds)} 个独立 scrambling 的组间波动估计。"
                  f"最大的样本档在全部工况的概率 RMSE 为 {stochastic['sobol_final_rmse']:.3g}，"
                  f"最小档为 {stochastic['sobol_first_rmse']:.3g}。", "",
                  "## 三类误差贡献与部署", "",
                  "线性化下位置方差为 $[(180/\\pi)\\sigma/D]^2$，两项云台方差由给定标准差平方得到。"
                  "精确几何列使用投影角的二阶矩，与独立云台角方差在回绕前相加。逐项消融改善量为消除该误差源后"
                  "的成功概率减去三项均存在时的成功概率，单位为百分点。", "",
                  "| 工况 | 来源 | 一阶方差占比 | 几何方差占比 | 消除后的达标概率 | 改善 (百分点) |",
                  "|---|---|---:|---:|---:|---:|"])
    rows = summary["contributions"]["rows"]
    for row in rows:
        linear = f"{row['linear_share']:.2%}" if row["linear_share"] is not None else "—"
        geometry = f"{row['geometry_share']:.2%}" if row["geometry_share"] is not None else "—"
        distance = f"{row['distance_m']:.5g} m" if row["distance_m"] is not None else "—"
        lines.append(f"| {SITE_LABELS[row['label']]} {distance} | {SOURCE_LABELS[row['source']]} | {linear} | "
                     f"{geometry} | {row['without_probability']:.10%} | {100 * row['delta_probability']:.8f} |")
    crossover = summary["contributions"]["first_order_crossover_distance_m"]
    initial = [row for row in rows if row["label"] == "initial"]
    critical = [row for row in rows if row["label"] == "critical"]
    first = max(initial, key=lambda r: r["delta_probability"])
    near_limit = max(critical, key=lambda r: r["delta_probability"])
    command_at_design = _mode_sites(summary, "command")["at_design"]["probability"]
    lines.extend(["", f"初始工况单项消融改善最大的是{SOURCE_LABELS[first['source']]}。"
                  f"一阶定位方差在 {crossover['zero']:.2f} m 与零位方差相等，在 {crossover['gimbal']:.2f} m 与两项云台合计方差相等。"
                  f"临界工况下改善最大的是{SOURCE_LABELS[near_limit['source']]}。"
                  f"基准方案在 {cap:g} m 的达标概率为 {actual_sites['at_design']['probability']:.5%}。"
                  f"把两项云台误差都设为零时，该里程的达标概率为 {command_at_design:.5%}。"
                  f"若维持初始标准差 {cfg.initial_sigma_m:g} m，可用前述斜率上限衡量定位校准和中途重定位的要求。", "",
                  "模型适用于题定固定水平距离和独立、零均值误差。曲线横轴是累计行驶里程，目标水平距离保持固定。", "",
                  "英文图提供 PDF 矢量版与 PNG 预览。运行参数、依赖、进程耗时和验收记录见 [运行记录](run_record.md)。", "",
                  "本结果用于队内复算。参赛队员应独立完成核心求解与结论，并按本届规定披露 AI 辅助环节。"
                  "[本届 AI 使用规定](https://www.nmmcm.org.cn/News/220.html)"])
    (directory / "conclusions.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_results(directory: Path, cfg: Config, result: dict) -> None:
    """写出通过数值验收后可用于核对和排版的图表及解释。"""
    from .plots import write_plots

    write_plots(result, directory)
    _write_conclusions(directory, cfg, result["summary"])
