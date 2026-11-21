"""B 题第四问验收，核对第二问接续、扰动边界和四种求解器输出。
输入为归档方案和 configs/q4.yaml；pytest 输出验收结果。
单独运行：nohup python3 -B -m pytest -q -p no:cacheprovider checks/test_q4.py。
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol，2026-11-20（版本发布日期未提供）。
"""

from itertools import permutations, product
import math
from pathlib import Path

import pytest
import yaml

from checks.q4_replay import Validator, separation
from src.q4.candidates import CandidateBank, Geometry, Scenario, scenarios
from src.q4.exact import exact
from src.q4.heuristics import alns, insertion
from src.q4.physics import Clock, Phase, aim_time, build_prefix, load_archive, parking
from src.q4.problem import Instance, Pick


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "results/q2/双站定位与并发调度_连续站位_20261120"


@pytest.fixture(scope="module")
def archive():
    """提供独立回放过的第二问归档计划。"""
    return load_archive(ARCHIVE)


@pytest.fixture(scope="module")
def geometry(archive):
    """提供与第二问一致的车辆外扩可见图。"""
    return Geometry(archive)


@pytest.fixture(scope="module")
def config():
    """读取实际实验配置，不改测试的时间窗口或统一分辨率。"""
    return yaml.safe_load((ROOT / "configs/q4.yaml").read_text())


def instance(archive, geometry, *, fail=None, recovery=None, location=None, config=None):
    """通过实际场景输入建立小规模验收实例。"""
    sc = Scenario("E7", *(location or (None, None, None)), fail, recovery)
    prefix = build_prefix(archive, fail, sc.dispatch)
    catalog = CandidateBank(archive, geometry, config).catalog(location) if location else None
    return Instance(archive, geometry, sc, prefix, Clock(0.1), catalog)


def test_original_plan_continues_during_outage_without_advancing_recovery_information(archive):
    """纯断联全程沿用先前命令，5 秒和恢复快照来自同一条连续轨迹。"""
    first = build_prefix(archive, failed=None, dispatch_s=5)
    later = build_prefix(archive, failed=None, dispatch_s=10)
    recovered = build_prefix(archive, failed=None, dispatch_s=30)

    assert first.states[1].kind == "TRAVELING"
    assert first.states[1].point == pytest.approx((74.87997, 2.72917), abs=1e-4)
    assert later.states[1].kind == "SLEWING"
    assert later.states[1].pose[1] > 0
    assert first.locked == later.locked == {0: pytest.approx(1.5320327144569215)}
    assert recovered.locked == {
        0: pytest.approx(1.5320327144569215),
        1: pytest.approx(10.204156744024587),
        2: pytest.approx(10.352848885352655),
    }


def test_current_trip_authorization_parks_v3_at_t1_until_recovery(archive):
    """V3 在 T1 复位后等待恢复，V4 仍在 T3 等搭档，已就绪的 T2 可以开测。"""
    prefix = build_prefix(archive, failed=None, dispatch_s=30, outage_rule="current_trip_only")
    assert prefix.locked == {0: pytest.approx(1.5320327144569215), 1: pytest.approx(10.204156744024587)}
    assert prefix.states[2].point == (15.042, 87.078)
    assert prefix.states[2].kind == "IDLE"
    assert prefix.fixed[2][-1].start == pytest.approx(7.564193203201075)
    assert prefix.fixed[2][-1].end == 30
    assert prefix.states[3].kind == "READY_WAITING"
    assert prefix.original_starts[2] == pytest.approx(32.78865568215158)


def test_full_queue_without_car_to_car_confirmation_holds_both_starts(archive):
    """完整队列可以转场，但恢复前只准备，恢复后先核验仍健康的搭档。"""
    healthy = build_prefix(archive, failed=None, dispatch_s=30, outage_rule="confirm_after_recovery")
    failed = build_prefix(archive, failed=2, dispatch_s=30, outage_rule="confirm_after_recovery")
    assert healthy.locked == failed.locked == {0: pytest.approx(1.5320327144569215)}
    assert healthy.fixed[3] == failed.fixed[3]
    assert healthy.states[2].point == (16.767, 88.660)
    assert healthy.states[2].pose[1] > 0
    assert healthy.states[2].kind == healthy.states[3].kind == "READY_WAITING"
    assert healthy.original_starts[1] == healthy.original_starts[2] == 30
    assert 2 not in failed.original_starts


def test_fault_withdrawal_does_not_release_a_previously_observing_partner(archive):
    """T1 继续计成功；故障 V3 退出，V5 到 8.064065 s 前不得复用。"""
    at_five = build_prefix(archive, failed=2, dispatch_s=5)
    at_ten = build_prefix(archive, failed=2, dispatch_s=10)
    assert at_five.states[2].kind == "FAILED"
    assert at_five.states[4].kind == "OBSERVING"
    assert at_five.states[4].release == pytest.approx(8.064065428913842)
    assert at_ten.states[3].kind == "READY_WAITING"
    assert at_ten.states[3].alert == 2
    assert at_ten.states[3].remaining_s is None
    assert 2 not in at_ten.original_starts


def test_outage_waiting_with_failed_partner_only_changes_after_recovery(archive, geometry):
    """V1 搭档 V2 故障，不借未来通信信息在恢复前重新指向或发车。"""
    ins = instance(archive, geometry, fail=1, recovery=20)
    check = Validator(ins)
    replay = check.dynamic((Pick(1, 0, 0, 3),))
    assert replay.valid
    track = replay.tracks[0]
    waiting = next(p for p in track if p.kind == "READY_WAITING" and p.end == 20)
    assert waiting.start == pytest.approx(3.129447985978156)
    assert track[track.index(waiting) + 1].start == 20
    v4 = [p for p in replay.tracks[3] if p.start >= 20 and p.alert == 1]
    assert v4[0].kind == "TRAVELING" and v4[0].start == 20
    assert len(v4[0].points) > 2  # V4 去南侧旧站不能沿穿越 B4 的直线。
    assert 20 < replay.starts[1] == v4[1].end < 60
    assert check.original().starts == {0: pytest.approx(1.5320327144569215), 2: pytest.approx(10.352848885352655)}


def test_reassignment_at_same_station_uses_current_pose_before_synchronized_start(archive, geometry):
    """V1 已对准 T2 且继续驻原站，不经复位重转；两车最晚就绪即共同开测。"""
    ins = instance(archive, geometry)
    check = Validator(ins)
    result = check.dynamic((Pick(1, 0, 0, 1),))
    assert result.valid
    for v in (0, 1):
        track = result.tracks[v]
        post = [p for p in track if p.start >= 5 and p.alert == 1]
        assert post[3].kind == "OBSERVING"
        assert post[3].end - post[3].start == pytest.approx(5)
        if v == 0:
            assert post[0].points == ((15, 14),)
            assert post[1].kind == "SLEWING" and post[1].start == post[1].end == 5
        else:
            assert post[0].points[0] == pytest.approx((74.87997, 2.72917), abs=1e-4)
            assert post[1].end == pytest.approx(10.204156744024587)
        assert result.starts[1] == post[3].start


def test_bad_plans_cannot_replay(archive, geometry):
    """拒绝同车占两席、故障车、重调已锁告警或一告警重复执行。"""
    ins = instance(archive, geometry, fail=2)
    check = Validator(ins)
    for plan in ((Pick(2, 0, 1, 1),), (Pick(1, 0, 0, 2),), (Pick(0, 0, 0, 1),),
                 (Pick(1, 0, 0, 1), Pick(1, 0, 0, 1))):
        assert not check.dynamic(plan).valid


def test_closed_clock_and_short_pan():
    """严格 60 秒、向上取整、跨 0° 的两轴同时转动边界。"""
    strict = Clock(.1)
    assert strict.ceil(2.31) == 24
    assert strict.last_step == 599
    assert strict.success(math.nextafter(60, 0))
    assert not strict.success(60)
    assert Clock(.05, inclusive=True).success(60)
    assert aim_time((359, 0), (1, 3), 30) == pytest.approx(.1)


def test_opposite_motion_collision_is_missed_by_endpoints_but_found_by_replay():
    """两个端点车距均 12 m，中点恰好相撞；退出车辆不再参与。"""
    p, q = (5.0, 10.0), (17.0, 10.0)
    move1 = Phase("TRAVELING", 0, 4, 1, (p, q))
    move2 = Phase("TRAVELING", 0, 4, 2, (q, p))
    assert separation(((move1,), (move2,)), 3) == (0, (0, 1, 2))
    assert math.isinf(separation(((move1,), (parking("FAILED", 0, 4, None, q),)), 3)[0])


def test_scenario_design_uses_all_failures_and_official_closed_range():
    """24×3 新告警网格、五故障车、三恢复时刻组成 1751 个唯一主情景。"""
    grid = list(product(range(0, 360, 15), (30, 40, 50), (50,)))
    rows = scenarios(grid, (10, 20, 30))
    assert len(rows) == len({s.key for s in rows}) == 1751
    assert [sum(row.category == f"E{i}" for row in rows) for i in range(1, 8)] == [72, 5, 3, 360, 216, 15, 1080]
    assert {r.failed for r in rows if r.category == "E2"} == set(range(5))


def test_four_algorithms_use_the_same_discrete_alphabet_and_match_small_enumeration(archive, geometry):
    """独立穷举故障 V4 的所有两任务次序与分配，验证两个后端目标上界。"""
    ins = instance(archive, geometry, fail=3)
    check = Validator(ins)
    brute = []
    for order in permutations(ins.pending):
        for plan in product(*(ins.picks[a] for a in order)):
            value = check.dynamic(plan)
            if value.valid:
                brute.append(max(value.starts_tick.values()))
    assert min(brute) == 273
    d = insertion(ins, check, beam=2)
    for name in ("cp_sat", "milp"):
        result = exact(ins, check, d.replay, name, 5)
        assert result.log["status"] == "OPTIMAL"
        assert result.log["optimal_latest_tick_s"] == pytest.approx(27.3)
        assert result.replay.metrics(ins)[:3] == (3, 3, 1.0)
    a, a_runs = alns(ins, check, d.replay.plan, [2026, 2027], 3)
    b, b_runs = alns(ins, check, d.replay.plan, [2026, 2027], 3)
    assert [r.replay.plan for r in a_runs] == [r.replay.plan for r in b_runs]
    assert a.replay.metrics(ins) == b.replay.metrics(ins)


def test_insertion_vectors_match_independent_replay_for_reused_t4(archive, geometry, config):
    """先做 T4 再复用及旧车先做 T2 时，检查整组合步长和由先到车产生的等待。"""
    ins = instance(archive, geometry, location=(120, 40, 50), config=config)
    check = Validator(ins)
    assert len(ins.pairs[3]) > 10
    options = ins.insertions((Pick(1, 0, 0, 1), Pick(2, 0, 2, 3)), 3, top=24)
    assert len(options) == 24
    seen = []
    for plan, score in options:
        ticks = ins.predict(plan)
        assert ins.score(plan, ticks) == score
        result = check.dynamic(plan)
        if result.valid:
            seen.append(result)
            assert result.starts_tick == ticks
    assert seen


def test_when_t4_has_no_configuration_both_exact_models_prove_only_three_starts(archive, geometry):
    """空 T4 目录代表第三种物理限制，须先证明最大启动数，不能把最后成功时刻当作 t_last。"""
    scenario = Scenario("E1", 90, 40, 50)
    ins = Instance(archive, geometry, scenario, build_prefix(archive, None, 5), Clock(.1), None)
    check = Validator(ins)
    first = insertion(ins, check, beam=2)
    for model in ("cp_sat", "milp"):
        candidate = exact(ins, check, first.replay, model, 5)
        assert candidate.replay.valid
        assert candidate.replay.metrics(ins) == (4, 3, .75, 60)
        assert candidate.log["k_bound"] == 3
        assert candidate.log["time_status"] == "FIXED_60"
