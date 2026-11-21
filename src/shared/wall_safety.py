"""B 题第二至四问的墙距误差预算，供求解入口和独立验收共用。
输入标称净空和全程误差硬上界（m），返回实际车体净空的确定性下界。
单独复核：nohup python3 -B -m pytest -q checks/test_q2_validate.py -k execution_wall_budget。
"""

import math


def execution_wall_budget(settings):
    """核查 m 制合成误差上界。若上界未知、非有限或可能耗尽净空，拒绝验收。"""
    wall = settings.get("wall_clearance_m")
    error = settings.get("execution_error_bound_m")
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in (wall, error)):
        raise ValueError("全程墙距和执行误差上界须为有限米数")
    # 距离对平移为 1-Lipschitz。合成上界含车心跟踪、地图、车体尺寸及安全停车的偏差。
    if not 0 <= error < wall:
        raise ValueError("全程执行误差上界须非负且严格小于标称车体净空")
    return {"scope": "conditional_on_a_certified_execution_error_bound",
            "planned_wall_clearance_m": wall, "execution_error_bound_m": error,
            "actual_clearance_lower_bound_m": math.nextafter(wall - error, 0.0)}
