"""q1 全场网格计算，供最优圆扫描、运行入口和图表复用。
输入为题面配置和单元间距（m），输出为独立掩码、交集、计数与近似面积。
单独核对 nohup python3 -B -m pytest -p no:cacheprovider checks/test_q1_region.py。
"""

import math
from dataclasses import dataclass

import numpy as np

from .config import Building, Config
from .geometry import disk_intrudes_rectangle, intersects_closed_box_by_faces


@dataclass(frozen=True)
class RegionResult:
    """单元中心和四张布尔掩码，行对应 y，列对应 x。"""

    resolution: float
    x_centers: np.ndarray
    y_centers: np.ndarray
    vehicle_mask: np.ndarray
    gimbal_mask: np.ndarray
    los_clear_mask: np.ndarray
    observable_mask: np.ndarray

    @property
    def feasible_count(self) -> int:
        """返回通过全部约束的单元数。"""
        return int(self.observable_mask.sum())

    @property
    def estimated_area(self) -> float:
        """由等面积单元计数估算可观测面积（m²）。"""
        return self.feasible_count * self.resolution**2

    @property
    def counts(self) -> dict:
        """分别返回全场独立通过数和按车辆、仰角、LOS 的累计剩余数。"""
        return {
            "total_cells": int(self.observable_mask.size),
            "independent_pass": {
                "vehicle": int(self.vehicle_mask.sum()),
                "gimbal": int(self.gimbal_mask.sum()),
                "los": int(self.los_clear_mask.sum()),
            },
            "sequential_remaining": {
                "after_vehicle": int(self.vehicle_mask.sum()),
                "after_gimbal": int((self.vehicle_mask & self.gimbal_mask).sum()),
                "after_los": self.feasible_count,
            },
        }


def _vehicle_mask(x: np.ndarray, y: np.ndarray, cfg: Config) -> np.ndarray:
    """按闭场界与圆盘距离判定一批车心，临界圆角点使用精确判据。"""
    rho = cfg.vehicle_radius
    feasible = (rho <= x) & (x <= cfg.field_width - rho) & (rho <= y) & (y <= cfg.field_height - rho)
    for b in cfg.buildings:
        dx = np.maximum(np.maximum(b.x_min - x, x - b.x_max), 0.0)
        dy = np.maximum(np.maximum(b.y_min - y, y - b.y_max), 0.0)
        gap = np.hypot(dx, dy) - rho
        passes = gap >= 0
        for i in np.flatnonzero(np.abs(gap) <= cfg.numerics.position_review_m):
            passes[i] = not disk_intrudes_rectangle(float(x[i]), float(y[i]), rho, b)
        feasible &= passes
    return feasible


def _gimbal_mask(x: np.ndarray, y: np.ndarray, cfg: Config) -> np.ndarray:
    """用闭距离下界判定仰角，临界单元采用与标量同一 hypot。"""
    dx, dy = x - cfg.target_xyz[0], y - cfg.target_xyz[1]
    r = np.hypot(dx, dy)
    r0 = cfg.distance_lower_bound
    passes = r >= r0
    for i in np.flatnonzero(np.abs(r - r0) <= cfg.numerics.distance_check_m):
        passes[i] = math.hypot(dx[i], dy[i]) >= r0
    return passes


def _blocked_by_building(x: np.ndarray, y: np.ndarray, cfg: Config, b: Building) -> np.ndarray:
    """向量化 Slab 检测一个闭棱柱，复核近乎平行或接触的线段。"""
    count = x.size
    enter, leave = np.zeros(count), np.ones(count)
    parallel_outside = np.zeros(count, dtype=bool)
    near_parallel = np.zeros(count, dtype=bool)
    lower, upper = b.bounds
    for start, end, lo, hi in zip((x, y, cfg.camera_height), cfg.target_xyz, lower, upper):
        delta = end - start
        parallel = delta == 0
        parallel_outside |= parallel & ((start < lo) | (start > hi))
        regular = np.abs(delta) > cfg.numerics.position_review_m
        near_parallel |= ~np.asarray(parallel) & ~np.asarray(regular)
        # 非零小分量留给逐面法，避免用“平行”替代真实的参数区间。
        divisor = np.where(regular, delta, 1.0)
        a, z = (lo - start) / divisor, (hi - start) / divisor
        enter = np.maximum(enter, np.where(regular, np.minimum(a, z), -np.inf))
        leave = np.minimum(leave, np.where(regular, np.maximum(a, z), np.inf))
    blocked = ~parallel_outside & (enter <= leave)
    ambiguous = ~parallel_outside & (near_parallel | (np.abs(enter - leave) <= cfg.numerics.parameter_review))
    for i in np.flatnonzero(ambiguous):
        camera = (float(x[i]), float(y[i]), cfg.camera_height)
        blocked[i] = intersects_closed_box_by_faces(camera, cfg.target_xyz, lower, upper)
    return blocked


def station_masks(x: np.ndarray, y: np.ndarray, cfg: Config) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """广播 m 制 x、y，返回同形状的车辆、云台和 LOS 布尔掩码。"""
    x, y = np.broadcast_arrays(np.asarray(x, dtype=float), np.asarray(y, dtype=float))
    shape = x.shape
    xs, ys = x.ravel(), y.ravel()
    vehicle, gimbal = _vehicle_mask(xs, ys, cfg), _gimbal_mask(xs, ys, cfg)
    los = np.ones(xs.size, dtype=bool)
    for i in range(0, xs.size, cfg.numerics.los_chunk_size):
        section = slice(i, i + cfg.numerics.los_chunk_size)
        for b in cfg.buildings:
            los[section] &= ~_blocked_by_building(xs[section], ys[section], cfg, b)
    return tuple(mask.reshape(shape) for mask in (vehicle, gimbal, los))


def _grid_centers(length: float, step: float) -> np.ndarray:
    """返回等宽单元的中心（m），间距不能整除边长时明确报错。"""
    ratio = length / step
    count = round(ratio)
    if count <= 0 or abs(ratio - count) > 8 * math.ulp(ratio):
        raise ValueError(f"网格步长 {step} m 不能整除场地边长 {length} m")
    return (np.arange(count, dtype=float) + 0.5) * step


def build_observable_region(cfg: Config, resolution: float) -> RegionResult:
    """按间距 resolution（m）采样全场，返回各约束掩码及交集。

    间距为非正数或不能整除场地两边时引发 ValueError。
    """
    if not math.isfinite(resolution) or resolution <= 0:
        raise ValueError("网格步长须为有限正数")
    xs = _grid_centers(cfg.field_width, resolution)
    ys = _grid_centers(cfg.field_height, resolution)
    vehicle, gimbal, los = station_masks(xs[None, :], ys[:, None], cfg)
    return RegionResult(resolution, xs, ys, vehicle, gimbal, los, vehicle & gimbal & los)
