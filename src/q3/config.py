"""q3 配置定义与读取，输入 configs/q3.yaml 及 debug/full 档位。
供第三问运行器、单测和输出模块共用，统一单位和数值控制参数。
单独检查配置可运行 nohup python3 -B -m src.run q3 --profile debug。
AI 辅助 OpenCode（Anomaly）与 Magpie gpt-6.2-sol（OpenAI，版本发布日期未公开），2026-11-20。
"""

from dataclasses import dataclass
import math
from pathlib import Path

import yaml

from .model import BearingModel


@dataclass(frozen=True)
class ValidationSettings:
    """保存积分、求根、交叉核对容差，及图表和采样工况的设置。"""

    quad_tolerance: float
    alternate_quad_tolerance: float
    wrap_tail_tolerance: float
    root_tolerance_m: float
    quadrature_agreement: float
    hermite_agreement: float
    root_agreement_m: float
    deterministic_probe_m: float
    stochastic_probe_m: float
    plot_max_distance_m: float
    plot_points: int
    monotonic_points: int
    confidence_alpha: float
    sobol_absolute_tolerance: float


@dataclass(frozen=True)
class SamplingSettings:
    """保存求积阶数、MC 规模及种子、Sobol 幂次及独立 scrambling 种子。"""

    hermite_orders: tuple[int, ...]
    mc_samples: int
    mc_batch: int
    mc_seeds: tuple[int, ...]
    sobol_powers: tuple[int, ...]
    sobol_seeds: tuple[int, ...]


@dataclass(frozen=True)
class Config:
    """第三问题设、统计可靠性指标与指定档位所需运行参数。"""

    model: BearingModel
    initial_sigma_m: float
    growth: float
    minimum_probability: float
    design_distance_m: float
    validation: ValidationSettings
    sampling: SamplingSettings
    profile: str

    def sigma_at(self, distance_m: float) -> float:
        """按题定基准方案计算指定累计里程处的每轴定位标准差（m）。"""
        return self.initial_sigma_m + self.growth * distance_m

    def distance_at(self, sigma_m: float) -> float:
        """反解累计里程（m）；负值表示临界点位于基准模型的初始状态之前。"""
        return (sigma_m - self.initial_sigma_m) / self.growth


def load_config(path: Path, profile: str) -> Config:
    """读取题设及数值配置，拒绝无法执行的档位、非物理参数或重复种子。"""
    with path.open(encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    given = dict(raw["model"])
    settings = ValidationSettings(**raw["validation"])
    model_args = {name: given.pop(name) for name in ("range_m", "threshold_deg", "zero_deg", "control_deg")}
    model = BearingModel(**model_args, wrap_tail_tolerance=settings.wrap_tail_tolerance)
    sampling = dict(raw["profiles"][profile])
    for name in ("hermite_orders", "mc_seeds", "sobol_powers", "sobol_seeds"):
        sampling[name] = tuple(sampling[name])
    sample = SamplingSettings(**sampling)
    cfg = Config(model, **given, validation=settings, sampling=sample, profile=profile)
    if not all(map(math.isfinite, given.values())) or cfg.initial_sigma_m < 0 or cfg.growth <= 0 or cfg.design_distance_m <= 0:
        raise ValueError("初始定位标准差须非负，基准里程增长率和设计里程须为正")
    if not model.threshold_deg / 180.0 < cfg.minimum_probability < 1.0:
        raise ValueError("达标概率下限须高于均匀角基线且低于 1")
    if not (0.0 < settings.alternate_quad_tolerance < settings.quad_tolerance
            and 0.0 < settings.confidence_alpha < 1.0
            and 0.0 < settings.deterministic_probe_m < settings.stochastic_probe_m
            and min(settings.root_tolerance_m, settings.quadrature_agreement, settings.hermite_agreement,
                    settings.root_agreement_m, settings.sobol_absolute_tolerance, settings.plot_max_distance_m) > 0.0
            and min(settings.plot_points, settings.monotonic_points) > 1):
        raise ValueError("数值验证参数须为有效容差、工况距离和网格规模")
    for levels in (sample.hermite_orders, sample.sobol_powers):
        if len(levels) < 2 or levels[0] < 1 or tuple(sorted(set(levels))) != levels:
            raise ValueError("Hermite 阶数和 Sobol 幂次至少有两档，且为递增正整数")
    for seeds in (sample.mc_seeds, sample.sobol_seeds):
        if len(seeds) < 2 or min(seeds) < 0 or len(set(seeds)) != len(seeds):
            raise ValueError("每种抽样至少使用两个互异非负种子")
    if not 0 < sample.mc_batch <= sample.mc_samples:
        raise ValueError("MC 每批及总样本数须为正，且前者不超过后者")
    return cfg
