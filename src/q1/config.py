"""q1 题面配置和坐标换算，供统一入口及几何模块使用。
输入为 configs/q1.yaml，输出为参数对象、T1 坐标和仰角距离下界。
单独核对 nohup python3 -B -m pytest -p no:cacheprovider checks/test_q1_geometry.py。
"""

import math
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Building:
    """轴对齐的闭建筑棱柱，底面标高为 0，位置与高度单位为 m。"""

    id: str
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    height: float

    @property
    def bounds(self) -> tuple[tuple[float, ...], tuple[float, ...]]:
        """返回三维闭盒下端和上端坐标，单位 m。"""
        return (self.x_min, self.y_min, 0.0), (self.x_max, self.y_max, self.height)


@dataclass(frozen=True)
class Numerics:
    """q1 临界复核、证书和分块参数，单位反映在字段名中。"""

    position_review_m: float
    parameter_review: float
    distance_check_m: float
    angle_check_deg: float
    clearance_gap_m: float
    arc_scan_deg: float
    los_chunk_size: int


@dataclass(frozen=True)
class Grid:
    """调试、出图与收敛对照的单元宽度，单位 m。"""

    debug_m: float
    plot_m: float
    study_m: tuple[float, ...]


@dataclass(frozen=True)
class Config:
    """第一问题面场景与数值配置，目标与相机高度以地面为基准。"""

    field_width: float
    field_height: float
    dispatch_center: tuple[float, float]
    target_xyz: tuple[float, float, float]
    buildings: tuple[Building, ...]
    vehicle_radius: float
    camera_height: float
    elevation_bounds_deg: tuple[float, float]
    distance_lower_bound: float
    numerics: Numerics
    grid: Grid


def compute_target_xyz(
    center: tuple[float, float], azimuth_deg: float, range_m: float, height_m: float,
) -> tuple[float, float, float]:
    """将中心、方位角（度）、水平距离和高度（m）转换为目标三维坐标（m）。"""
    azimuth = math.radians(azimuth_deg)
    return (
        center[0] + range_m * math.cos(azimuth),
        center[1] + range_m * math.sin(azimuth),
        height_m,
    )


def load_config(path: Path) -> Config:
    """读取场景与参数。缺失字段或超出 q1 模型范围时报告配置错误。"""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    field_width, field_height = map(float, data["field_size_m"])
    center = tuple(map(float, data["dispatch_center_xy_m"]))
    elevation_bounds = tuple(map(float, data["elevation_bounds_deg"]))
    target = compute_target_xyz(center, **data["alert"])
    camera_height = float(data["camera_height_m"])
    radius = float(data["vehicle_radius_m"])
    if not (0 < radius < min(field_width, field_height) / 2):
        raise ValueError("场地必须容得下完整车辆圆盘")
    # q1 的高处目标让负仰角下界恒满足，硬约束可统一为水平距离下界。
    if not (0 <= camera_height < target[2] and -90 <= elevation_bounds[0] <= 0 < elevation_bounds[1] < 90):
        raise ValueError("q1 需要目标高于相机，且云台区间包含水平与正仰角")
    buildings = tuple(Building(**row) for row in data["buildings"])
    if not buildings or len({b.id for b in buildings}) != len(buildings):
        raise ValueError("场景必须包含编号唯一的建筑")
    for b in buildings:
        if not (0 <= b.x_min < b.x_max <= field_width and 0 <= b.y_min < b.y_max <= field_height and b.height > 0):
            raise ValueError(f"建筑 {b.id} 的边界或高度无效")
    numerics = Numerics(**data["numerics"])
    grid = Grid(data["grid"]["debug_m"], data["grid"]["plot_m"], tuple(data["grid"]["study_m"]))
    if not all(math.isfinite(value) and value > 0 for value in vars(numerics).values()):
        raise ValueError("数值参数均须为有限正数，LOS 分块大小须为正整数")
    if not isinstance(numerics.los_chunk_size, int):
        raise ValueError("LOS 分块大小须为正整数")
    distance_lower_bound = (target[2] - camera_height) / math.tan(math.radians(elevation_bounds[1]))
    return Config(
        field_width, field_height, center, target, buildings, radius, camera_height,
        elevation_bounds, distance_lower_bound, numerics, grid,
    )
