"""第四问精确方案：CP-SAT 与 SCIP MILP 两个后端求解相同的席位—车序模型。
输入共享候选、状态与可行上界；输出两阶段求解状态、界和经过连续验收的方案。
由 src.q4.experiment 调用：nohup python3 -B -m src.run q4 --profile full。
AI 辅助：OpenCode / Magpie group/gpt-6.2-sol，2026-11-20（版本发布日期未提供）。
"""

from dataclasses import dataclass
from itertools import product
import math
import time

from ortools.linear_solver import pywraplp
from ortools.sat.python import cp_model

from checks.q4_replay import Replay, Validator
from .heuristics import Solution
from .problem import Instance, Pick, Plan


@dataclass(frozen=True)
class Run:
    """一次优化阶段的后端状态、变量值与全局目标界（该阶段目标单位）。"""

    status: str
    values: dict
    bound: float | None


class Cp:
    """CP-SAT 后端使用整数时间、原生条件约束与双车 max 等式。"""

    def __init__(self):
        """构造单线程、固定随机种子的 CP-SAT 模型。"""
        self.model = cp_model.CpModel()
        self.vars = {}

    def boolean(self, key):
        """以同一规范键创建、登记一个布尔变量。"""
        var = self.model.new_bool_var(str(key))
        self.vars[key] = var
        return var

    def ticks(self, key, hi):
        """返回 [0,hi] 上的相对时间（整数 tick）。"""
        var = self.model.new_int_var(0, hi, str(key))
        self.vars[key] = var
        return var

    def add(self, expression):
        """添加线性约束。"""
        self.model.add(expression)

    def conditional(self, enabled, left, right, lower_m, upper_m):
        """条件为真时约束就绪等式，不需要使用 Big-M。"""
        self.model.add(left == right).only_enforce_if(enabled)

    def maximum(self, out, first, second, hi, alert):
        """观察窗口的同步启动是两个就绪变量的最大值。"""
        self.model.add_max_equality(out, (first, second))

    def hint(self, values):
        """用相同的启发式设计热启动两个精确后端，不限制可行域。"""
        self.model.clear_hints()
        for key, value in values.items():
            if key in self.vars:
                self.model.add_hint(self.vars[key], int(value))

    def solve(self, objective, minimize, seconds) -> Run:
        """在剩余墙钟内求解一个阶段并读其状态与上下界。"""
        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = 1
        solver.parameters.random_seed = 2026
        solver.parameters.max_time_in_seconds = seconds
        if minimize:
            self.model.minimize(objective)
        else:
            self.model.maximize(objective)
        result = solver.solve(self.model)
        status = solver.status_name(result)
        values = {k: solver.value(v) for k, v in self.vars.items()} if status in {"OPTIMAL", "FEASIBLE"} else {}
        bound = solver.best_objective_bound if status in {"OPTIMAL", "FEASIBLE"} else None
        return Run(status, values, bound)


class Milp:
    """SCIP 后端：变量同义，条件等式用可计算的紧 Big-M 成对线性化。"""

    def __init__(self):
        """初始化 OR-Tools 自带的开源 SCIP；环境不可用时明确失败，而非替换模型。"""
        self.model = pywraplp.Solver.CreateSolver("SCIP")
        if self.model is None:
            raise RuntimeError("OR-Tools SCIP 后端不可用")
        self.model.SetNumThreads(1)
        self.model.SetSolverSpecificParametersAsString("randomization/randomseedshift = 2026")
        self.vars = {}

    def boolean(self, key):
        """登记二进制变量。"""
        var = self.model.BoolVar(str(key))
        self.vars[key] = var
        return var

    def ticks(self, key, hi):
        """相对时刻限制在 [0,hi] tick；等式和严格正的前驱弧蕴含时刻为整数。"""
        var = self.model.NumVar(0, hi, str(key))
        self.vars[key] = var
        return var

    def add(self, expression):
        """添加一个线性约束。"""
        self.model.Add(expression)

    def conditional(self, enabled, left, right, lower_m, upper_m):
        """M 下侧由 right 上界给定，上侧由 left 上界给定，均以相对评价窗 tick 计。"""
        self.add(left >= right - lower_m * (1 - enabled))
        self.add(left <= right + upper_m * (1 - enabled))

    def maximum(self, out, first, second, hi, alert):
        """一个选择布尔量确定达到同步最大值的车辆；两车相等时均可。"""
        p = self.boolean(("max", alert))
        self.add(out >= first)
        self.add(out >= second)
        self.add(out <= first + hi * (1 - p))
        self.add(out <= second + hi * p)

    def hint(self, values):
        """将同一初始设计映射到 SCIP 原生 warm start。"""
        keys = [key for key in values if key in self.vars]
        self.model.SetHint([self.vars[key] for key in keys], [values[key] for key in keys])

    def solve(self, objective, minimize, seconds) -> Run:
        """限制阶段墙钟，保留 SCIP 的可行上界/下界及终止状态。"""
        self.model.SetTimeLimit(max(1, int(seconds * 1000)))
        if minimize:
            self.model.Minimize(objective)
        else:
            self.model.Maximize(objective)
        code = self.model.Solve()
        status = {self.model.OPTIMAL: "OPTIMAL", self.model.FEASIBLE: "FEASIBLE", self.model.INFEASIBLE: "INFEASIBLE",
                  self.model.NOT_SOLVED: "UNKNOWN", self.model.UNBOUNDED: "UNBOUNDED", self.model.ABNORMAL: "ABNORMAL",
                  self.model.MODEL_INVALID: "MODEL_INVALID"}.get(code, "UNKNOWN")
        good = status in {"OPTIMAL", "FEASIBLE"}
        values = {key: var.solution_value() for key, var in self.vars.items()} if good else {}
        bound = self.model.Objective().BestBound() if good else None
        return Run(status, values, bound if bound is not None and math.isfinite(bound) else None)


class Formulation:
    """每项服务由两个席位和一个共享站对表示；按车的有向前后继弧禁止交叉占用。"""

    def __init__(self, ins: Instance, backend):
        """在相同有限目录构造同一套可选任务、前继、就绪、同步和转移约束。"""
        self.ins, self.b = ins, backend
        self.alerts, self.vehicles = ins.pending, ins.alive
        self.slots = tuple(product(self.alerts, (0, 1)))
        self.edges = tuple((i, j) for i, j in product(self.slots, repeat=2) if i[0] != j[0])
        self.hi = ins.horizon - ins.dispatch_step  # 相对指令生效时钟，给 Big-M 的紧窗上界
        B, H = backend.boolean, self.hi
        self.y = {a: B(("y", a)) for a in self.alerts}
        self.z = {a: [B(("z", a, c)) for c in range(len(ins.sites[a]))] for a in self.alerts}
        self.x = {(v, i): B(("x", v, i)) for v in self.vehicles for i in self.slots}
        self.first = {(v, i): B(("f", v, i)) for v in self.vehicles for i in self.slots}
        self.end = {(v, i): B(("e", v, i)) for v in self.vehicles for i in self.slots}
        self.arc = {(v, i, j): B(("arc", v, i, j)) for v in self.vehicles for i, j in self.edges}
        self.start = {a: backend.ticks(("start", a), H) for a in self.alerts}
        self.ready = {i: backend.ticks(("ready", i), H) for i in self.slots}
        self.last = backend.ticks(("last",), H)
        self.joints = {}
        for a in self.alerts:
            backend.add(sum(self.z[a]) == self.y[a])
            backend.add(self.start[a] <= H * self.y[a])
            backend.add(self.last >= self.start[a])
            backend.maximum(self.start[a], self.ready[a, 0], self.ready[a, 1], H, a)
            for v in self.vehicles:
                backend.add(self.x[v, (a, 0)] + self.x[v, (a, 1)] <= self.y[a])
        for i in self.slots:
            a, seat = i
            backend.add(sum(self.x[v, i] for v in self.vehicles) == self.y[a])
            backend.add(self.ready[i] <= H * self.y[a])
            for v in self.vehicles:
                backend.add(self.first[v, i] + sum(g for (w, j, k), g in self.arc.items() if w == v and k == i)
                            == self.x[v, i])
                backend.add(self.end[v, i] + sum(g for (w, j, k), g in self.arc.items() if w == v and j == i)
                            == self.x[v, i])
                duration = [self._bounded(int(ins.first[v, a, pair[seat]]) - ins.dispatch_step) for pair in ins.sites[a]]
                backend.conditional(self.first[v, i], self.ready[i], self._dot(duration, self.z[a]),
                                    max(duration, default=0), H)
        for v in self.vehicles:
            backend.add(sum(self.first[v, i] for i in self.slots) <= 1)
            backend.add(sum(self.end[v, i] for i in self.slots) <= 1)
        for i, j in self.edges:
            duration, largest = self._transit(i, j)
            # 两席位都已唯一分车，因此沿一对席位的车序弧之和也必为 0/1。
            via = backend.boolean(("via", i, j))
            backend.add(via == sum(self.arc[v, i, j] for v in self.vehicles))
            backend.conditional(via, self.ready[j], self.start[i[0]] + duration, H + largest, H)
        self.count = sum(self.y.values())
        self.sum_start = sum(self.start.values())

    def _bounded(self, time_tick):
        """超过评价窗口的时长截到 H+1（等式仍保证不可选），避免把无穷塞进 Big-M。"""
        return min(self.hi + 1, max(0, time_tick))

    @staticmethod
    def _dot(values, variables):
        """对站对指示量加权，得到选定转移或首任务耗时（tick）。"""
        return sum(value * variable for value, variable in zip(values, variables) if value)

    def _transit(self, i, j):
        """两个站对都可变时以共享乘积表线性化；主实验仅 T4 站对可变。"""
        a, s = i
        b, t = j
        A, B = self.ins.sites[a], self.ins.sites[b]
        if not A or not B:
            return 0, 0  # 两席位任一不可占用时，转移弧不会选择
        term = lambda p, q: self._bounded(int(self.ins.transfer[a, p[s], b, q[t]]))
        if len(A) == 1:
            variables, values = self.z[b], [term(A[0], q) for q in B]
        elif len(B) == 1:
            variables, values = self.z[a], [term(p, B[0]) for p in A]
        else:
            lo, hi = sorted((a, b))
            if (lo, hi) not in self.joints:
                backend = self.b
                joint = {(l, r): backend.boolean(("w", lo, hi, l, r)) for l, r in
                         product(range(len(self.z[lo])), range(len(self.z[hi])))}
                self.joints[lo, hi] = joint
                for (l, r), w in joint.items():
                    x, y = self.z[lo][l], self.z[hi][r]
                    backend.add(w <= x)
                    backend.add(w <= y)
                    backend.add(w >= x + y - 1)
            joint = self.joints[lo, hi]
            variables, values = [], []
            for (l, r), variable in joint.items():
                variables.append(variable)
                values.append(term(A[l], B[r]) if a == lo else term(A[r], B[l]))
        return self._dot(values, variables), max(values, default=0)

    def design(self, plan: Plan):
        """用车内前继和站—车席位描述一个方案；不依赖跨车无关任务的全序。"""
        predecessors = {}
        used = {}
        active = set()
        for p in plan:
            a = p.alert
            active.update((("y", a), ("z", a, p.pair)))
            for s, v in enumerate(p.vehicles):
                i = (a, s)
                active.add(("x", v, i))
                predecessors[i] = used.get(v)
                if v in used:
                    active.add(("arc", v, used[v], i))
                    active.add(("via", used[v], i))
                else:
                    active.add(("f", v, i))
                used[v] = i
        active.update(("e", v, i) for v, i in used.items())
        return active, predecessors

    def give_hint(self, plan: Plan) -> None:
        """把预热计划的全部 bool 和整数时刻映射为一条完整的可行求解器 hint。"""
        ticks = self.ins.predict(plan)
        if ticks is None:
            return
        active, predecessors = self.design(plan)
        hint = {k: int(k in active) for k in self.b.vars}
        station = {(p.alert, s): (v, site) for p in plan for s, (v, site) in enumerate(self.ins.legs(p))}
        for a in self.alerts:
            if a not in ticks:
                continue
            hint["start", a] = ticks[a] - self.ins.dispatch_step
            for i in ((a, 0), (a, 1)):
                v, site = station[i]
                pred = predecessors[i]
                if pred is None:
                    ready = int(self.ins.first[v, a, site])
                else:
                    ready = ticks[pred[0]] + int(self.ins.transfer[pred[0], station[pred][1], a, site])
                hint["ready", i] = ready - self.ins.dispatch_step
            hint["max", a] = int(hint["ready", (a, 0)] >= hint["ready", (a, 1)])
        pair = {p.alert: p.pair for p in plan}
        for (a, b), joints in self.joints.items():
            if (pair.get(a), pair.get(b)) in joints:
                hint["w", a, b, pair[a], pair[b]] = 1
        hint["last",] = max((hint["start", a] for a in ticks), default=0)
        self.b.hint(hint)

    def decode(self, values) -> Plan:
        """只取分配、站对和车序；随后由独立物理验证器重算，不信任求解器时刻。"""
        chosen = []
        for a in self.alerts:
            if values["y", a] < 0.5:
                continue
            c = next(i for i in range(len(self.z[a])) if values["z", a, i] > 0.5)
            pair = [next(v for v in self.vehicles if values["x", v, (a, s)] > 0.5) for s in (0, 1)]
            chosen.append(Pick(a, c, *pair))
        # 正观测时间保证车序弧的前者启动严格早；不同车辆可以有相同开始时刻。
        return tuple(sorted(chosen, key=lambda p: (values["start", p.alert], p)))

    def exclude(self, plan: Plan) -> None:
        """排除独立验收证明无效的完整设计，不错删可改变复用次序/任务集合的方案。"""
        active, _ = self.design(plan)
        positives = [self.b.vars[k] for k in active if k[0] in {"y", "z", "x", "arc"}]
        drops = [self.y[a] for a in self.alerts if ("y", a) not in active]
        self.b.add(sum(positives) - sum(drops) <= len(positives) - 1)


def _solver_version(backend) -> str:
    """区分 MILP 真实底层版本，不把 OR-Tools 的软件包版本误作 SCIP 版本。"""
    if isinstance(backend, Milp):
        return backend.model.SolverVersion()
    from ortools import __version__
    return f"OR-Tools CP-SAT {__version__}"


def exact(ins: Instance, check: Validator, seed: Replay, model: str, budget: float) -> Solution:
    """先最大化完成数，再优化官方完成窗；接纳前排除违反连续车距的设计。"""
    begin = time.perf_counter()
    if not ins.pending:
        return Solution(check.original(), {"status": "NO_OP", "k_bound": len(ins.names), "solve_s": 0.0,
                                          "cuts": 0, "count_status": "NO_OP", "time_status": "NO_OP"})
    backend = Cp() if model == "cp_sat" else Milp()
    name = _solver_version(backend)
    form = Formulation(ins, backend)
    feasible = seed if seed.valid else check.dynamic(())
    incumbent = feasible
    form.give_hint(feasible.plan)
    limit = begin + budget
    cuts = []
    stages = []
    used_k = len(feasible.plan)
    k_bound = len(ins.pending)
    latest_bound = None
    tick_optimum = None

    def phase(label, objective, minimize, ceiling):
        """直到得出已验收可行计划或预算耗尽；无效计划只加排除割，不计为 incumbent。"""
        nonlocal incumbent, feasible
        result = Run("UNKNOWN", {}, None)
        kept_bound = None
        while (left := ceiling - time.perf_counter()) > 0.002:
            result = backend.solve(objective, minimize, left)
            if result.bound is not None:
                if kept_bound is None or (minimize and result.bound > kept_bound) or (not minimize and result.bound < kept_bound):
                    kept_bound = result.bound
            if not result.values:
                break
            plan = form.decode(result.values)
            path = check.dynamic(plan)
            if path.valid:
                reported = {a: round(result.values["start", a]) + ins.dispatch_step for a in path.starts_tick}
                if reported != path.starts_tick:
                    raise RuntimeError(f"{model} {ins.scenario.key}: 预测/独立 tick 时钟不一致")
                feasible = path
                if path.score(ins) < incumbent.score(ins):
                    incumbent = path
                stages.append({"phase": label, "status": result.status, "bound": kept_bound})
                return result.status, path, kept_bound
            cuts.append({"phase": label, "violations": path.violations, "plan": [vars(p) for p in plan]})
            form.exclude(plan)
        stages.append({"phase": label, "status": result.status, "bound": kept_bound})
        return result.status if not result.values else "UNKNOWN", None, kept_bound

    if used_k == len(ins.pending):
        count_status = "TRIVIAL_FULL"
    else:
        backend.add(form.count >= used_k)
        count_status, improved, bound = phase("maximize_k", form.count, False, min(limit, begin + budget * 0.6))
        if bound is not None:
            k_bound = min(k_bound, math.floor(bound + 1e-6))
        if improved is not None:
            used_k = len(improved.plan)
        if used_k == k_bound:
            count_status = "PROVEN_K"
    best_possible_count = used_k == k_bound
    backend.add(form.count == used_k)
    form.give_hint(feasible.plan)
    if used_k == len(ins.pending):
        upper = max(feasible.starts_tick.values(), default=ins.dispatch_step) - ins.dispatch_step
        backend.add(form.last <= upper)
        time_status, improved, bound = phase("minimize_t_last", form.last, True, limit)
        if bound is not None:
            latest_bound = ins.clock.seconds(math.ceil(bound - 1e-6) + ins.dispatch_step)
        if improved is not None and (time_status == "OPTIMAL" or max(improved.starts_tick.values()) ==
                                      math.ceil((bound if bound is not None else -1) - 1e-6) + ins.dispatch_step):
            tick_optimum = ins.clock.seconds(max(improved.starts_tick.values()))
            latest_bound = tick_optimum
            time_status = "OPTIMAL"
    else:
        time_status = "FIXED_60" if best_possible_count else "INCUMBENT_K_ONLY"
        # 当完成数已经证明而官方 t_last 固定为 60 时，仅用剩余预算尝试减小启动时刻总和。
        if best_possible_count and used_k:
            phase("aux_sum_start", form.sum_start, True, limit)
    overall = "OPTIMAL" if best_possible_count and (time_status in {"OPTIMAL", "FIXED_60"}) else "FEASIBLE"
    return Solution(incumbent, {"status": overall, "count_status": count_status, "time_status": time_status,
                                "k_bound": k_bound + len(ins.prefix.locked), "latest_tick_lower_s": latest_bound,
                                "optimal_latest_tick_s": tick_optimum, "cuts": len(cuts), "rejected": cuts,
                                "tick_witness": [vars(p) for p in feasible.plan] if tick_optimum is not None else None,
                                "stages": stages, "solve_s": time.perf_counter() - begin, "solver": name,
                                "seed": 2026, "budget_s": budget, "threads": 1})
