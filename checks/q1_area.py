"""q1 连续面积复核，从题面几何的竖向截面包围可观测域。
输入为一次运行的配置与网格结果，输出为 runs/q1 下的新面积复核记录。
单独运行 nohup python3 -B -m checks.q1_area --run <q1 运行目录>。
"""

import argparse
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
from itertools import pairwise
import json
import math
from pathlib import Path
import shlex
import shutil
import sys
import tempfile

import numpy as np
import yaml

from src.q1.config import Config, load_config
from src.q1.geometry import camera_shadow_hull


@dataclass(frozen=True)
class AreaBounds:
    """连续可观测面积的数值包围（m²）与用于包围的竖条参数。"""

    lower_m2: float
    upper_m2: float
    strip_count: int
    max_strip_width_m: float
    endpoint_padding_m: float

    @property
    def midpoint_m2(self) -> float:
        """返回上下界的中点（m²）。"""
        return (self.lower_m2 + self.upper_m2) / 2


@dataclass(frozen=True)
class VerticalBand:
    """凸禁入区的截面函数，断点间的上下端各自单调。"""

    knots: tuple[float, ...]
    limits: Callable[[np.ndarray], tuple[np.ndarray, np.ndarray]]


def _rounded_rectangle(x_min, x_max, y_min, y_max, radius) -> VerticalBand:
    """把底面和圆盘的 Minkowski 和写成竖向区间，坐标和半径均为 m。"""
    def limits(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """在横向距离固定时，用圆弧计算上下偏置的 m 制坐标。"""
        dx = np.maximum(np.maximum(x_min - x, x - x_max), 0.0)
        dy = np.sqrt(np.maximum((radius - dx) * (radius + dx), 0.0))
        return y_min - dy, y_max + dy

    return VerticalBand((x_min - radius, x_min, x_max, x_max + radius), limits)


def _projected_shadow(cfg: Config, b) -> VerticalBand:
    """构造楼体在相机标高平面的阴影，适用相机至目标单调升高的题设。"""
    hull = camera_shadow_hull(cfg, b)
    planes = hull.equations
    lower = planes[planes[:, 1] < 0]
    upper = planes[planes[:, 1] > 0]

    def limits(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """用凸包半平面求下包络和上包络，输出 y 坐标（m）。"""
        bottom = -(lower[:, :1] * x + lower[:, 2:3]) / lower[:, 1:2]
        top = -(upper[:, :1] * x + upper[:, 2:3]) / upper[:, 1:2]
        return np.max(bottom, axis=0), np.min(top, axis=0)

    knots = tuple(sorted(set(hull.points[hull.vertices, 0])))
    return VerticalBand(knots, limits)


def _union_lengths(lowers: list[np.ndarray], uppers: list[np.ndarray], y_min: float) -> np.ndarray:
    """按截面下端归并各楼、车辆和云台的区间，返回并集长度（m）。"""
    lo, hi = np.array(lowers), np.array(uppers)
    order = np.argsort(lo, axis=0)
    lo, hi = np.take_along_axis(lo, order, axis=0), np.take_along_axis(hi, order, axis=0)
    before = np.vstack((np.full(lo.shape[1], y_min), np.maximum.accumulate(hi, axis=0)[:-1]))
    return np.maximum(hi - np.maximum(lo, before), 0.0).sum(axis=0)


def _strip_visible_heights(
    bands: list[VerticalBand], edges: np.ndarray, y_min: float, y_max: float, pad: float,
) -> tuple[np.ndarray, np.ndarray]:
    """由各禁入截面的内外包络求每根竖条的可见长度下界和上界（m）。"""
    inner_lows, inner_highs, outer_lows, outer_highs = [], [], [], []
    midpoints = (edges[:-1] + edges[1:]) / 2
    for band in bands:
        valid = (midpoints >= band.knots[0]) & (midpoints <= band.knots[-1])
        if not valid.any():
            continue
        low, high = band.limits(edges)
        # 竖条已在边界曲线拐点处分开，上下端的极值均来自两端。
        candidates = (
            (inner_lows, np.maximum(low[:-1], low[1:]) + pad),
            (inner_highs, np.minimum(high[:-1], high[1:]) - pad),
            (outer_lows, np.minimum(low[:-1], low[1:]) - pad),
            (outer_highs, np.maximum(high[:-1], high[1:]) + pad),
        )
        for stack, values in candidates:
            stack.append(np.where(valid, np.clip(values, y_min, y_max), y_min))
    height = y_max - y_min
    if not inner_lows:
        whole = np.full(midpoints.size, height)
        return whole, whole
    return (
        height - _union_lengths(outer_lows, outer_highs, y_min),
        height - _union_lengths(inner_lows, inner_highs, y_min),
    )


def enclose_observable_area(cfg: Config, max_strip_width_m: float = 0.00005) -> AreaBounds:
    """按连续几何包围可观测面积；浮点截面与累加均留余量。"""
    if not math.isfinite(max_strip_width_m) or max_strip_width_m <= 0:
        raise ValueError("竖条间距应为有限正数")
    rho = cfg.vehicle_radius
    x_min, x_max = rho, cfg.field_width - rho
    y_min, y_max = rho, cfg.field_height - rho
    tx, ty, _ = cfg.target_xyz
    bands = [_rounded_rectangle(tx, tx, ty, ty, cfg.distance_lower_bound)]
    for b in cfg.buildings:
        bands.append(_rounded_rectangle(b.x_min, b.x_max, b.y_min, b.y_max, rho))
        if b.height >= cfg.camera_height:
            bands.append(_projected_shadow(cfg, b))

    knots = sorted({x_min, x_max, *(x for band in bands for x in band.knots if x_min < x < x_max)})
    parts_lower, parts_upper = [], []
    strip_count = 0
    pad = cfg.numerics.position_review_m
    for left, right in pairwise(knots):
        count = math.ceil((right - left) / max_strip_width_m)
        strip_count += count
        step = (right - left) / count
        for start in range(0, count, cfg.numerics.los_chunk_size):
            stop = min(start + cfg.numerics.los_chunk_size, count)
            edges = left + step * np.arange(start, stop + 1, dtype=float)
            if stop == count:
                edges[-1] = right
            low, high = _strip_visible_heights(bands, edges, y_min, y_max, pad)
            widths = np.diff(edges)
            parts_lower.append(float(np.sum(low * widths)))
            parts_upper.append(float(np.sum(high * widths)))

    # 扩张项覆盖截面接近拐点时的坐标舍入，求和仅占更小的量级。
    area_padding = 2 * (x_max - x_min + y_max - y_min) * pad
    return AreaBounds(math.fsum(parts_lower) - area_padding, math.fsum(parts_upper) + area_padding,
                      strip_count, max_strip_width_m, pad)


def main() -> None:
    """复核一轮 q1 的网格面积，写可复跑的独立 JSON 和中文摘要。"""
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="q1 连续面积独立复核")
    parser.add_argument("--run", required=True, type=Path, help="待复核的 q1 运行目录")
    parser.add_argument("--baseline", type=Path, help="同一题设与网格下的原版运行目录")
    parser.add_argument("--strip-width", type=float, default=0.00005, help="竖条最大宽度（m）")
    args = parser.parse_args()
    run = args.run.resolve()
    original = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    snapshot = run / "config.yaml"
    config_digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    if original["question"] != "q1" or original["input"]["config_sha256"] != config_digest:
        raise ValueError("待复核的结果与 q1 配置快照不匹配")

    comparison = None
    if args.baseline:
        baseline_path = args.baseline.resolve()
        baseline = json.loads((baseline_path / "summary.json").read_text(encoding="utf-8"))
        baseline_config = (baseline_path / "config.yaml").read_bytes()
        if (baseline["question"] != "q1"
                or baseline["input"]["config_sha256"] != hashlib.sha256(baseline_config).hexdigest()
                or yaml.safe_load(baseline_config) != yaml.safe_load(snapshot.read_bytes())):
            raise ValueError("原版与复核版必须使用相同的配置值，且原版快照须与结果匹配")
        metrics = {
            "primary_quality_per_m": (baseline["primary_optimum"]["q_opt_per_m"], original["primary_optimum"]["q_opt_per_m"]),
            **{name: (baseline["tie_break"][name], original["tie_break"][name])
               for name in ("minimum_clearance_m", "optimality_gap_m")},
        }
        comparison = {"baseline_run": str(baseline_path), "metrics": {
            name: {"baseline": a, "revised": b, "difference": b - a} for name, (a, b) in metrics.items()
        }}

    area = enclose_observable_area(load_config(snapshot), args.strip_width)
    rows = [{
        "resolution_m": row["resolution_m"],
        "estimated_area_m2": row["estimated_area_m2"],
        "estimate_minus_reference_midpoint_m2": row["estimated_area_m2"] - area.midpoint_m2,
    } for row in original["region"]["resolution_studies"]]
    command = shlex.join(["nohup", sys.executable, "-B", "-m", "checks.q1_area", *sys.argv[1:]])
    output_root = root / "runs" / "q1"
    output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = Path(tempfile.mkdtemp(prefix=f"{stamp}_连续面积复核_", dir=output_root))
    shutil.copyfile(snapshot, output / "config.yaml")
    report = {
        "question": "q1", "source_run": str(run), "config_sha256": config_digest, "command": command,
        "area_bounds": asdict(area), "midpoint_m2": area.midpoint_m2, "grid_comparison": rows,
        "baseline_comparison": comparison,
        "audit_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    (output / "continuous_area.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# q1 连续面积复核", "",
        f"连续可观测面积的数值包围为 **[{math.floor(1e6 * area.lower_m2) / 1e6:.6f}, "
        f"{math.ceil(1e6 * area.upper_m2) / 1e6:.6f}] m²**，"
        f"区间中点为 {area.midpoint_m2:.6f} m²。竖条最大宽度为 {area.max_strip_width_m:g} m。", "",
        "在相机所在平面上，每栋楼的闭遮挡区域是楼底矩形与楼顶中心投影的凸包。"
        "缩边场地内对楼体遮挡、车辆圆角扩张和仰角圆求并集并扣除。"
        "竖条在截面边界的拐点处分割，各截面的内外包络分别给出面积上界和下界。", "",
        "| 网格间距（m） | 网格估计（m²） | 估计减区间中点（m²） |", "|---:|---:|---:|",
        *(f"| {row['resolution_m']:g} | {row['estimated_area_m2']:.6f} | {row['estimate_minus_reference_midpoint_m2']:+.4f} |"
          for row in rows),
        "", f"对照运行 `{run.name}`。", "", "```bash", command, "```", "",
    ]
    if comparison:
        lines.extend(["## 原版与复核版", "", f"原版运行 `{Path(comparison['baseline_run']).name}`。", "",
                      "| 指标 | 原版 | 复核版 |", "|---|---:|---:|",
                      *(f"| `{name}` | {values['baseline']:.12g} | {values['revised']:.12g} |"
                        for name, values in comparison["metrics"].items()), ""])
    (output / "continuous_area.md").write_text("\n".join(lines), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
