"""第四问确定性双车插入与事件触发 ALNS，使用共享的站对和时间模型。
输入一个实时调度实例与独立验收器，输出可执行计划、算子和多随机种子结果。
由 src.q4.experiment 调用：nohup python3 -B -m src.run q4 --profile full。
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol，2026-11-20（版本发布日期未提供）。
"""

from dataclasses import dataclass
from functools import lru_cache
from itertools import permutations
import math
import random
import time

from checks.q4_replay import Replay, Validator
from .problem import Instance, Pick, Plan


@dataclass(frozen=True)
class Solution:
    """一个求解器的经验证方案及统计；未获证明时不得把可行值称为最优。"""

    replay: Replay
    log: dict


class Neighborhood:
    """复用计算量主要来自 T4 站—车联合插入的邻域。"""

    def __init__(self, ins: Instance, check: Validator):
        """绑定实例和验证器；缓存只在一场景内有效。"""
        self.ins, self.check = ins, check
        self.insert = lru_cache(maxsize=4096)(self._insert)

    def _insert(self, plan: Plan, alert: int, top: int):
        """枚举时间可行插入；最终接受必须经过独立回放。"""
        return self.ins.insertions(plan, alert, top=top)

    def best(self, proposals) -> Replay:
        """返回候选中官方分数最优的可执行计划；等价平局按规范分配排序。"""
        unique = dict.fromkeys(proposals)
        empty = self.check.dynamic(())
        best = empty
        for plan in unique:
            replay = self.check.dynamic(plan)
            if replay.valid and (replay.score(self.ins), plan) < (best.score(self.ins), best.plan):
                best = replay
        return best


def insertion(ins: Instance, check: Validator, *, beam: int) -> Solution:
    """D：按可行配置少到多处理，并用少量次序、配对及站位重插入回查。"""
    begin = time.perf_counter()
    neighborhood = Neighborhood(ins, check)
    difficulty = sorted(ins.pending, key=lambda a: (len(ins.picks[a]), a))
    # 最难序优先，再试逆序与小规模邻接交换，降低严格顺序引起的同步锁死。
    orders = [tuple(difficulty)]
    if len(difficulty) > 1:
        orders.extend([tuple(reversed(difficulty)), (difficulty[1], difficulty[0], *difficulty[2:])])
    seeds: list[Plan] = [()]
    for order in dict.fromkeys(orders):
        plans: list[Plan] = [()]
        for a in order:
            proposals = {plan: ins.score(plan, ins.predict(plan) or {}) for plan in plans}
            for plan in plans:
                proposals.update(neighborhood.insert(plan, a, max(6, 2 * beam)))
            # 保留若干整数近优，再按真实轨迹选候选，避免只保留一个发生车距冲突的插入。
            ranked = sorted(proposals, key=lambda plan: (proposals[plan], plan))
            plans = [p for p in ranked[:max(12, 4 * beam)] if check.dynamic(p).valid][:beam] or [()]
        seeds.extend(plans)
    best = neighborhood.best(seeds)
    repairs = [best.plan]
    for a in ins.pending:
        remainder = tuple(p for p in best.plan if p.alert != a)
        repairs.extend(p for p, _ in neighborhood.insert(remainder, a, 10))
    best = neighborhood.best(repairs)
    return Solution(best, {"status": "HEURISTIC", "solve_s": time.perf_counter() - begin,
                           "strategy_detail": "difficulty-ranked synchronized insertion + reinsert", "beam_width": beam})


DESTROY = ("failed_related", "new_alert_related", "random", "longest_wait", "busiest_vehicle", "poor_configuration")
REPAIR = ("best_joint", "regret_2", "fewest_options", "randomized_station_vehicle", "preserve_original_order")


def _destroy(ins: Instance, replay: Replay, op: str, rng: random.Random) -> tuple[Plan, list[int]]:
    """从收到扰动后的可重排后缀移除 1—3 个任务，保留既有物理前缀。"""
    plan = replay.plan
    if not plan:
        return (), list(ins.pending)
    count = rng.randint(1, len(plan))
    if op == "failed_related":
        priority = lambda p: p.alert < 3 and ins.prefix.failed in ins.archive.assignment[p.alert]
    elif op == "new_alert_related":
        t4 = next((p for p in plan if p.alert == 3), None)
        priority = lambda p: 2 * (p.alert == 3) + bool(t4 and set(p.vehicles) & set(t4.vehicles))
    elif op == "longest_wait":
        waits = {a: sum(phase.end - phase.start for track in replay.tracks for phase in track
                        if phase.alert == a and phase.start >= ins.prefix.dispatch and phase.kind == "READY_WAITING")
                 for a in ins.pending}
        priority = lambda p: waits[p.alert]
    elif op == "busiest_vehicle":
        workloads = [sum(v in p.vehicles for p in plan) for v in range(len(ins.prefix.states))]
        priority = lambda p: sum(workloads[v] for v in p.vehicles)
    elif op == "poor_configuration":
        # 用站位相关的首就绪/指向负担作为代理，不为题面另设精度硬阈值。
        priority = lambda p: max(int(ins.first[v, p.alert, site]) for v, site in ins.legs(p))
    else:
        priority = lambda p: 0
    ordered = sorted(plan, key=lambda p: (-priority(p), rng.random()))
    removed = {p.alert for p in ordered[:count]}
    kept = tuple(p for p in plan if p.alert not in removed)
    return kept, [a for a in ins.pending if a not in {p.alert for p in kept}]


def _repair(hood: Neighborhood, plan: Plan, missing: list[int], op: str, rng: random.Random) -> Plan:
    """修复可用配置稀缺、或有明显同步时间 regret 的告警，也探索站位和车对替换。"""
    ins = hood.ins
    missing = list(missing)
    while missing:
        options = []
        for a in missing:
            choices = hood.insert(plan, a, 12)
            if not choices:
                continue
            if op == "regret_2":
                regret = choices[1][1][1] - choices[0][1][1] if len(choices) > 1 else ins.horizon
                if not regret and len(choices) > 1:
                    regret = choices[1][1][2] - choices[0][1][2]
                key = (-regret, choices[0][1], a)
            elif op == "fewest_options":
                key = (len(ins.picks[a]), choices[0][1], a)
            elif op == "preserve_original_order":
                key = (ins.archive.starts[a] if a < 3 else math.inf, choices[0][1], a)
            elif op == "randomized_station_vehicle":
                key = (rng.random(), choices[0][1], a)
            else:
                key = (choices[0][1], a)
            options.append((key, a, choices))
        if not options:
            break
        _, a, choices = min(options)
        if op == "randomized_station_vehicle":
            choices = list(choices)
            rng.shuffle(choices)
        for candidate, _ in choices:
            if hood.check.dynamic(candidate).valid:
                plan = candidate
                break
        missing.remove(a)
    return plan


def alns(ins: Instance, check: Validator, seed_plan: Plan, seeds: list[int], iterations: int) -> tuple[Solution, list[Solution]]:
    """C：每个种子从相同增量初稿开始，记录稳定性；始终单独保留最佳可行解。"""
    hood = Neighborhood(ins, check)
    begin = time.perf_counter()
    originals = tuple(Pick(a, 0, *ins.archive.assignment[a]) for a in ins.pending if a < 3
                      and ins.prefix.failed not in ins.archive.assignment[a])
    initial = hood.best((seed_plan, _repair(hood, originals, [a for a in ins.pending
                        if a not in {p.alert for p in originals}], "preserve_original_order", random.Random(0))))
    trials = []
    for seed in seeds:
        trial_begin = time.perf_counter()
        rng = random.Random(seed)
        favorite = current = initial
        dw, rw = [1.0] * len(DESTROY), [1.0] * len(REPAIR)
        improvements = 0
        calls = {name: 0 for name in (*DESTROY, *REPAIR)}
        for index in range(iterations if ins.pending else 0):
            do = rng.choices(range(len(DESTROY)), weights=dw)[0]
            ro = rng.choices(range(len(REPAIR)), weights=rw)[0]
            calls[DESTROY[do]] += 1
            calls[REPAIR[ro]] += 1
            part, missing = _destroy(ins, current, DESTROY[do], rng)
            plan = _repair(hood, part, missing, REPAIR[ro], rng)
            # 同步需求较小，可直接局部交换顺序、重插入并联合替换驻站与车辆。
            alterations = [plan]
            if plan:
                a = rng.choice(plan).alert
                remainder = tuple(p for p in plan if p.alert != a)
                alterations.extend(p for p, _ in hood.insert(remainder, a, 5))
            if len(plan) > 1:
                i, j = sorted(rng.sample(range(len(plan)), 2))
                switched = list(plan)
                switched[i], switched[j] = switched[j], switched[i]
                reorder = tuple(switched)
                if ins.predict(reorder) is not None:
                    alterations.append(reorder)
            proposal = hood.best(alterations)
            new, old = proposal.score(ins), current.score(ins)
            improved = new < favorite.score(ins)
            reward = 0.15
            if improved:
                favorite = proposal
                improvements += 1
                reward = 4
            if new <= old:
                current = proposal
                reward = max(reward, 1)
            elif new[:2] == old[:2]:
                temperature = max(0.01, 1.0 - (index + 1) / max(iterations, 1))
                gain = max(0, new[2] - old[2]) + 0.03 * max(0, new[3] - old[3])
                if rng.random() < math.exp(-gain / temperature):
                    current = proposal
                    reward = 0.5
            dw[do] = 0.8 * dw[do] + 0.2 * reward
            rw[ro] = 0.8 * rw[ro] + 0.2 * reward
        trials.append(Solution(favorite, {"status": "HEURISTIC", "seed": seed,
                                        "solve_s": time.perf_counter() - trial_begin, "iterations": iterations,
                                        "improvements": improvements, "operators": calls}))
    best = min(trials, key=lambda s: (s.replay.score(ins), s.replay.plan, s.log["seed"]))
    return Solution(best.replay, {"status": "HEURISTIC", "solve_s": time.perf_counter() - begin,
                                 "seed": best.log["seed"], "seeds": len(seeds), "iterations_per_seed": iterations}), trials
