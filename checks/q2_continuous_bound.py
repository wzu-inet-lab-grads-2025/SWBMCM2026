"""B 题 2（2）连续场地复核：用车辆复用链和站位矩形包围验证最晚开测下界。
输入原题标称场地与车辆，输出三十条复用链的排除记录及秒制下界。
运行：nohup env OPENBLAS_NUM_THREADS=1 python3 -B -m checks.q2_continuous_bound --output <runs/q2 下的 JSON>
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol（版本发布日期未提供），2026-11-20。
"""

import argparse
from dataclasses import replace
from itertools import permutations, product
import json
from pathlib import Path

import numpy as np

from src.q1.config import load_config
from src.q1.geometry import camera_shadow_hull

from .q2_geometry import load_scene


# 下界取比搜索结果略保守的秒数。以下试探值由全场矩形逐项验证后才进入复用链计算。
LOWER_BOUND_S = 10.352
READY_TRIALS_S = {"T1": ("V3", 1.023, 1.532), "T2": ("V1", 3.129, 3.413), "T3": ("V1", 1.658, 2.765)}
SHADOW_PAD_M = 1e-8
TIME_PAD_S = 1e-8
QUADRANTS = np.array(list(product((False, True), repeat=2)))


def _split(boxes: np.ndarray) -> np.ndarray:
    """将每个 m 制闭矩形均分为四个闭子矩形，保留坐标边界。"""
    lo, hi = boxes[:, 0, None], boxes[:, 1, None]
    mid = (lo + hi) / 2
    lower = np.where(QUADRANTS, mid, lo)
    upper = np.where(QUADRANTS, hi, mid)
    return np.stack((lower, upper), axis=2).reshape(-1, 2, 2)


def _possibly_visible(boxes: np.ndarray, shadows: list[np.ndarray]) -> np.ndarray:
    """仅删除整块严格落在某栋楼阴影内的矩形，缩进阴影处理浮点误差。"""
    keep = np.ones(len(boxes), dtype=bool)
    for planes in shadows:
        normal = planes[:, :2]
        maximum = boxes[:, 1] @ np.maximum(normal, 0).T + boxes[:, 0] @ np.minimum(normal, 0).T + planes[:, 2]
        keep &= np.max(maximum, axis=1) >= -SHADOW_PAD_M
    return keep


def _box_distance(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    """返回两矩形的欧氏距离下界（m），点可传入零宽、零高的矩形。"""
    gaps = np.maximum(0, np.maximum(first[..., 0, :] - second[..., 1, :], second[..., 0, :] - first[..., 1, :]))
    return np.hypot(gaps[..., 0], gaps[..., 1])


def _pointing_floor(boxes: np.ndarray, target: tuple, camera_m: float, speed_deg_s: float) -> np.ndarray:
    """给闭矩形内任一点的双轴指向计时下界（s）。"""
    tx, ty, height = target
    lo, hi = boxes[:, 0], boxes[:, 1]
    far = np.maximum(np.abs(lo - (tx, ty)), np.abs(hi - (tx, ty)))
    near_y = np.maximum(0, np.maximum(lo[:, 1] - ty, ty - hi[:, 1]))
    dx = tx - lo[:, 0]
    # 先取向东分量的上界。目标在车西侧时，向南北偏得越多，绝对转角才越小。
    pan = np.arctan2(np.where(dx >= 0, near_y, far[:, 1]), dx)
    tilt = np.arctan2(height - camera_m, np.hypot(far[:, 0], far[:, 1]))
    return np.degrees(np.maximum(pan, tilt)) / speed_deg_s


def continuous_bound() -> dict:
    """覆盖全场连续站位。无法排除任一候选区间时以异常报告未通过。"""
    scene = load_scene()
    field = load_config(Path(__file__).resolve().parents[1] / "configs" / "q1.yaml")
    rho = scene.radius_m
    cells = np.array([[[rho, rho], [scene.width_m - rho, scene.height_m - rho]]])
    for _ in range(4):
        cells = _split(cells)
    shadows = {
        name: [camera_shadow_hull(replace(field, target_xyz=target), b).equations for b in scene.buildings]
        for name, target in scene.targets.items()
    }
    observable = {name: cells[_possibly_visible(cells, hulls)] for name, hulls in shadows.items()}
    origins = {name: np.array([point, point]) for name, point in scene.vehicles.items()}

    def slew(target: str, boxes: np.ndarray) -> np.ndarray:
        """计算指向耗时下界，复位可用同一个下界（s）。"""
        return _pointing_floor(boxes, scene.targets[target], scene.camera_height_m, scene.gimbal_speed_deg_s)

    ready = {}
    for target, (fastest, fast_s, other_s) in READY_TRIALS_S.items():
        ready[target] = {}
        for vehicle, origin in origins.items():
            trial = fast_s if vehicle == fastest else other_s
            boxes = observable[target]
            for _ in range(20):
                floor = _box_distance(origin, boxes) / scene.speed_m_s + slew(target, boxes)
                keep = floor <= trial + TIME_PAD_S
                if not keep.any():
                    ready[target][vehicle] = trial
                    break
                boxes = _split(boxes[keep])
                boxes = boxes[_possibly_visible(boxes, shadows[target])]
            else:
                raise RuntimeError(f"就绪下界未验证：{vehicle}/{target}")

    chains = []
    for vehicle, origin in origins.items():
        for first, second in permutations(scene.targets, 2):
            partner = min(value for v, value in ready[first].items() if v != vehicle)
            a, b = observable[first], observable[second]
            turn = slew(first, a)
            # 初筛可省去转场。未能排除的矩形对随后使用真实欧氏距离的下界。
            from_first = np.maximum(_box_distance(origin, a) / scene.speed_m_s + turn, partner)
            prefix = from_first + scene.observation_s + turn
            i, j = np.nonzero(prefix[:, None] + slew(second, b) <= LOWER_BOUND_S + TIME_PAD_S)
            a, b = a[i], b[j]
            checked = 0
            for level in range(38):
                turn = slew(first, a)
                from_first = np.maximum(_box_distance(origin, a) / scene.speed_m_s + turn, partner)
                floor = from_first + scene.observation_s + turn + _box_distance(a, b) / scene.speed_m_s + slew(second, b)
                keep = floor <= LOWER_BOUND_S + TIME_PAD_S
                checked += len(floor)
                if not keep.any():
                    chains.append({"vehicle": vehicle, "first": first, "second": second,
                                   "refinements": level, "box_pairs_checked": checked})
                    break
                if np.count_nonzero(keep) > 800_000:
                    raise RuntimeError(f"复用链超出矩形预算：{vehicle}/{first}→{second}")
                a, b = a[keep], b[keep]
                if level % 2 == 0:
                    a, b = _split(a), np.repeat(b, 4, axis=0)
                    visible = _possibly_visible(a, shadows[first])
                else:
                    a, b = np.repeat(a, 4, axis=0), _split(b)
                    visible = _possibly_visible(b, shadows[second])
                a, b = a[visible], b[visible]
            else:
                raise RuntimeError(f"未能排除复用链：{vehicle}/{first}→{second}")
    return {"latest_start_lower_bound_s": LOWER_BOUND_S, "ready_lower_bound_s": ready,
            "shadow_padding_m": SHADOW_PAD_M, "time_padding_s": TIME_PAD_S, "excluded_chains": chains}


def main() -> None:
    """校验所有复用链，打印或写入 JSON 记录。"""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, help="写入报告的 JSON 路径，默认打印")
    args = parser.parse_args()
    report = json.dumps(continuous_bound(), ensure_ascii=False, indent=2) + "\n"
    if args.output is None:
        print(report, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(args.output)


if __name__ == "__main__":
    main()
