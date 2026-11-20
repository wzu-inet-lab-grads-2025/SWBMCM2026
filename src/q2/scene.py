"""B 题第二问配置与候选生成，复用 q1 场地、仰角与三维视线判定。
输入 configs/q2.yaml；输出各告警的 m 制站点及用于调度的时间/距离表。
运行：nohup python3 -B -m src.run q2 --profile full
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol（版本发布日期未提供），2026-11-20。
"""

from dataclasses import dataclass, replace
from itertools import product
import math
from pathlib import Path

import numpy as np
from scipy.spatial.distance import cdist
import yaml

from src.q1.config import Config, compute_target_xyz, load_config
from src.q1.region import station_masks

from .paths import Point, VisibilityRouter
from .schedule import Candidates, Problem


@dataclass(frozen=True)
class Scene:
    """第二问题设与复核参数；长度 m、时间 s、角度度。"""

    field: Config
    field_file: Path
    vehicles: tuple[str, ...]
    origins: np.ndarray
    targets: tuple[str, ...]
    target_cfg: tuple[Config, ...]
    speed_m_s: float
    gimbal_deg_s: float
    observation_s: float
    q21_travel_limit_s: float
    bearing_sigma_deg: float
    grid_m: tuple[float, ...]
    path_polygon_sides: int
    path_comparison_sides: int
    monte_carlo_samples: int
    random_seed: int
    refinement_sites_m: tuple[Point, ...] = ()


def read_scene(path: Path) -> Scene:
    """读取第二问 YAML 与引用的共享场地快照，计算目标全精度坐标。"""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    field_file = (path.parent / data.pop("scene_config")).resolve()
    field = load_config(field_file)
    vehicles = data.pop("vehicles")
    targets = data.pop("alerts")
    if len(vehicles) != 5 or set(targets) != {"T1", "T2", "T3"}:
        raise ValueError("B 题第二问要求五辆观测车及 T1、T2、T3 三条告警")
    camera_to_goal = []
    for alert in targets.values():
        xyz = compute_target_xyz(field.dispatch_center, **alert)
        if xyz[2] <= field.camera_height:
            raise ValueError("该场景要求三个警报目标高于相机")
        r_min = (xyz[2] - field.camera_height) / math.tan(math.radians(field.elevation_bounds_deg[1]))
        camera_to_goal.append(replace(field, target_xyz=xyz, distance_lower_bound=r_min))
    data["grid_m"] = tuple(data["grid_m"])
    positive = ("speed_m_s", "gimbal_deg_s", "observation_s", "q21_travel_limit_s", "bearing_sigma_deg")
    if any(not math.isfinite(data[key]) or data[key] <= 0 for key in positive):
        raise ValueError("速度、计时和测角标准差必须为有限正数")
    if not data["grid_m"] or any(not math.isfinite(h) or h <= 0 for h in data["grid_m"]):
        raise ValueError("候选间距必须为有限正数")
    if not isinstance(data["monte_carlo_samples"], int) or data["monte_carlo_samples"] <= 0:
        raise ValueError("随机复核样本量必须为正整数")
    # 归档配置记录连续细化得到的米制车心坐标，两套方案共享这些候选点。
    extra_sites = data.pop("refinement_sites_m", [])
    if not isinstance(extra_sites, list):
        raise ValueError("细化候选站须为 [x, y] 坐标列表，单位 m")
    if extra_sites:
        points = np.asarray(extra_sites, dtype=float)
        if points.ndim != 2 or points.shape[1] != 2 or not np.isfinite(points).all():
            raise ValueError("细化候选站须为有限的 [x, y] 坐标对，单位 m")
        data["refinement_sites_m"] = tuple(map(tuple, points.tolist()))
    return Scene(field, field_file, tuple(vehicles), np.array(list(vehicles.values()), dtype=float),
                 tuple(targets), tuple(camera_to_goal), **data)


def build_router(scene: Scene, sides: int) -> VisibilityRouter:
    """构造题场统一路径口径，供两问与就近对照使用。"""
    f = scene.field
    return VisibilityRouter(f.field_width, f.field_height, f.vehicle_radius, f.buildings, sides=sides)


def pointing(scene: Scene, target: int, sites: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """给一批站点返回方位（度）、仰角（度）、指向及复位的公共耗时（s）。"""
    tx, ty, height = scene.target_cfg[target].target_xyz
    dx, dy = (np.array([tx, ty]) - sites).T
    yaw = np.degrees(np.arctan2(dy, dx))
    pitch = np.degrees(np.arctan2(height - scene.field.camera_height, np.hypot(dx, dy)))
    # 题面双轴并行且未另限水平旋转方向，取从正东出发的最短角差。
    return yaw, pitch, np.maximum(np.abs(yaw), np.abs(pitch)) / scene.gimbal_deg_s


def build_problem(scene: Scene, router: VisibilityRouter, steps: tuple[float, ...], added: list[Point], *,
                  relax_travel: bool = False) -> Problem:
    """合并场界网格、初始点和附加站；松弛模式使用真站点域和欧氏路长下界。"""
    points = [scene.origins, np.array(added, dtype=float).reshape(-1, 2)]
    for h in steps:
        axes = [np.append(np.arange(lo, hi, h), hi) for lo, hi in zip(router.lower, router.upper)]
        points.append(np.array(list(product(*axes))))
    unique = np.unique(np.concatenate(points), axis=0)
    usable = unique if relax_travel else unique[router.admits(unique)]
    distance = cdist if relax_travel else router.distances
    jobs = []
    for target, cfg in enumerate(scene.target_cfg):
        vehicle_ok, pitch_ok, los_ok = station_masks(*usable.T, cfg)
        allowed = usable[vehicle_ok & pitch_ok & los_ok]
        if len(allowed) < 2:
            raise ValueError(f"{scene.targets[target]} 不足两个可观测候选点")
        bearing = np.array(cfg.target_xyz[:2]) - allowed
        bearing /= np.linalg.norm(bearing, axis=1)[:, None]
        _, _, times = pointing(scene, target, allowed)
        jobs.append(Candidates(allowed, times, bearing, distance(scene.origins, allowed)))
    return Problem(tuple(jobs), scene.origins, distance, scene.speed_m_s, scene.observation_s,
                   2 * scene.field.vehicle_radius)
