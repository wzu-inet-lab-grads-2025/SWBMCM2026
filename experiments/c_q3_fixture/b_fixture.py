#!/usr/bin/env python3
"""B-only fixed-action synthetic checks. NOT an official replayer or policy.
Run from the repository root: python3 experiments/c_q3_fixture/b_fixture.py
From another directory, use the absolute script path; resources stay repository-relative.
Only writes b_output/ next to this file; Python standard library only.
"""
import copy
import csv
import hashlib
import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = HERE / "b_output"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def save(name, value, jsonl=False):
    text = ("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in value)
            if jsonl else json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    (OUT / name).write_text(text, encoding="utf-8")


def numeric_rows(spec, recipe):
    """Offline recipe; policy receives only copies of rows in the released window."""
    rows = []
    for i, offset in enumerate(recipe["source_offsets_s"]):
        v = spec["marker_base"] + 100 * i
        pose = lambda x, y, z: {"position": [x, y, z], "orientation_xyzw": [0, 0, 0, 1]}
        rows.append({
            "timestamp_s": spec["annotation_start_s"] + offset,
            "left_hand": {
                "positions": [[v + k, v + k + .25, v + k + .5] for k in range(21)],
                "orientations_xyzw": [[0, 0, 0, 1] for _ in range(21)]},
            "right_hand": {
                "positions": [[-v-k, -v-k-.25, -v-k-.5] for k in range(21)],
                "orientations_xyzw": [[0, 0, 0, 1] for _ in range(21)]},
            "head_pose": pose(v + 50, 1, 2),
            "left_head_camera_pose": pose(v + 60, 3, 4),
            "right_head_camera_pose": pose(v + 70, 5, 6),
            "hand_quality_flags": {"left": {"occlusion": bool(i % 2)},
                                   "right": {"near_edge": i % 3 == 2}},
        })
    return rows


class FixedTrial:
    def __init__(self, trial, truth, conditions, zones):
        self.trial, self.truth, self.conditions, self.zones = trial, truth, conditions, zones
        self.job = trial["job"]
        self.H, self.B, self.K = 100, 32, 4
        self.t, self.zone, self.cost = 0., "Z0", 0.
        self.reviews, self.returned, self.actions = 0, 0, 0
        self.prefix, self.pending, self.released = 0., None, False
        self.phase, self.occupied, self.ended = "IDLE", False, False
        self.attempted, self.completed, self.censored = False, False, False
        self.s, self.b, self.C, self.commitment_end = None, None, None, None
        self.events, self.views, self.audit = [], [], []

    def policy_view(self):
        # This is the entire decision-input boundary. Never return self.truth.
        visible = []
        if self.released:
            start = self.truth["annotation_start_s"]
            visible = [r for r in self.truth["numeric_rows"]
                       if start <= r["timestamp_s"] <= start + self.prefix]
        return copy.deepcopy({
            "t_s": self.t,
            "public_conditions": {"case_id": "S1", "synthetic_job_count": 1,
                                  "H": self.H, "B": self.B, "K": self.K,
                                  "alpha": 1, "beta_s": .5, "zones": self.zones},
            "job": self.job,
            "released": self.released,
            "zone": self.zone,
            "budget": {"cost": round(self.cost, 10), "remaining": round(self.B-self.cost, 10),
                       "reviews_committed": self.reviews, "reviews_returned": self.returned},
            "device": {"phase": self.phase, "occupied": self.occupied,
                       "may_submit_action": not self.ended and not self.occupied,
                       "committed_service_end_s": self.commitment_end},
            "pending_review": self.pending,
            "prefix_s": self.prefix,
            "numeric_rows": visible,
            "feedback": {"attempted": self.attempted, "completed": self.completed,
                         "completion_s": self.C, "censored_at_H": self.censored},
        })

    def metrics(self):
        # Observable elapsed service only, not latent source duration.
        observed = 0. if self.s is None else max(0., (self.C if self.completed else self.t)-self.s)
        overrun = 0. if self.b is None else max(observed-self.b, 0.)
        idle = max(self.b-(self.C-self.s), 0.) if self.completed else 0.
        effective_C = self.C if self.completed else self.H
        return {"weighted_completion_rate": float(self.completed),
                "weighted_on_time_rate": float(self.completed and self.C <= self.job["due_s"]),
                "weighted_tardiness_s": self.job["priority_weight"] * max(effective_C-self.job["due_s"], 0.),
                "cost": round(self.cost, 10), "observations": self.reviews, "elapsed_s": self.t,
                "observed_overrun_s": overrun, "reserved_idle_s": idle,
                "censored_units": int(self.censored), "invalid_actions": 0, "valid_protocol": True}

    def emit(self, event, rules, **detail):
        view = self.policy_view()
        ident = f'{self.trial["trial_id"]}:{len(self.events):02d}'
        self.views.append({"view_id": ident, "view": view})
        elapsed_idle = (max(0., min(self.t, self.commitment_end)-self.C)
                        if self.completed else 0.)
        self.events.append({
            "event_id": ident, "trial_id": self.trial["trial_id"], "event": event,
            "t_s": self.t, "zone": self.zone, "cost": round(self.cost, 10),
            "budget_remaining": round(self.B-self.cost, 10),
            "reviews_committed": self.reviews, "reviews_returned": self.returned,
            "reviews_remaining": self.K-self.reviews, "action_count": self.actions,
            "device": view["device"], "policy_view_id": ident,
            "visible_prefix_s": self.prefix,
            "visible_timestamps_s": [r["timestamp_s"] for r in view["numeric_rows"]],
            "witness_left_keypoint0_x": [r["left_hand"]["positions"][0][0] for r in view["numeric_rows"]],
            "metrics_if_ended_now": self.metrics(), "reserved_idle_elapsed_s": elapsed_idle,
            "trial_ended": self.ended, "rule_refs": rules, "detail": detail,
        })

    def probe(self, label):
        # Snapshot is a pure read at a scheduled observation time, not an action.
        before = digest(self.__dict__)
        view = self.policy_view()
        after = digest(self.__dict__)
        assert before == after
        if view["numeric_rows"]:
            view["numeric_rows"][0]["left_hand"]["positions"][0][0] = -999999
        assert digest(self.__dict__) == after  # caller cannot mutate engine through the view
        self.emit("READ_ONLY_SNAPSHOT", ["R_INFO", "R_DELAY"], probe=label,
                  submitted_action=False, added_time_s=0, state_hash_before=before,
                  state_hash_after=after, detached_copy=True)

    def release(self):
        assert self.t == self.job["release_s"] and not self.released
        self.released, self.prefix = True, .2
        self.emit("RELEASE", ["R_INFO"], free_prefix_s=.2)

    def accept(self, action):
        assert not self.ended and not self.occupied
        assert self.actions < self.conditions["max_actions"]
        self.actions += 1
        if action["action"] != "STOP":
            self.occupied = True

    def wait(self, action):
        dt = action["wait_s"]
        assert math.isfinite(dt) and dt > 0 and self.t + dt <= self.H
        self.accept(action)
        self.phase = "WAIT"
        end = self.t + dt
        self.emit("WAIT_ACCEPTED", ["R_ACTIONS"], action=action, return_at_s=end)
        # Only the requested fixed WAIT, whose endpoint is J's release.
        assert end == self.job["release_s"]
        self.t, self.phase, self.occupied = end, "IDLE", False
        self.emit("WAIT_RETURN", ["R_ACTIONS"])
        self.release()

    def review(self, action):
        assert action["job_id"] == "J" and self.released and not self.attempted
        assert self.reviews < min(2, self.K) and self.cost + 1 <= self.B
        assert self.t + 1 <= self.H
        self.accept(action)
        self.phase, self.cost, self.reviews = "REVIEW", self.cost + 1, self.reviews + 1
        self.pending = {"requested_at_s": self.t, "return_at_s": self.t+1, "target_prefix_s": .6}
        self.emit("REVIEW_ACCEPTED", ["R_INFO", "R_DELAY"], action=action,
                  return_at_s=self.t+1, commitment="cost and review count charged at acceptance")
        self.t += .5
        self.probe("review_request_midpoint")
        self.t += .5
        self.prefix, self.returned, self.pending = .6, self.returned+1, None
        self.phase, self.occupied = "IDLE", False
        self.emit("REVIEW_RETURN", ["R_INFO", "R_DELAY"], cumulative_prefix_s=.6)

    def audit_source(self, reason):
        # Retrospective file only; source IDs and d never enter policy_view().
        observed = (self.C if self.completed else self.t) - self.s
        self.audit.append({"trial_id": self.trial["trial_id"], "disclosed_at_s": self.t,
                           "reason": reason, "source_ref": self.truth["source_ref"],
                           "offline_truth_pointer": f'b_offline_truth.json#/trials/{self.trial["trial_id"]}',
                           "d_s_offline": self.truth["d_s"], "p_s_offline": self.truth["p_s"],
                           "source_annotation_start_s": self.truth["annotation_start_s"],
                           "source_annotation_end_s": self.truth["annotation_end_s"],
                           "service_start_s": self.s, "observed_completion_s": self.C,
                           "observed_service_s": observed,
                           "unobserved_service_s_offline": self.truth["p_s"]-observed,
                           "latent_completion_s_offline_NOT_observed": self.s+self.truth["p_s"],
                           "rule_refs": ["R_SOURCE_AUDIT", "R_METRICS"]})

    def execute(self, action):
        # Admission intentionally has no reference to truth, d or p.
        D = math.dist(self.zones[self.zone], self.zones[self.job["zone_id"]])
        b, t0 = action["b_s"], self.t
        s = self.t + D + .5
        fee = 2 + .1*b + .2*D
        guard = {"t_s": t0, "released": self.released, "unattempted": not self.attempted,
                 "predecessors_complete": self.job["predecessors"] == [], "D": D, "b_s": b,
                 "s_s": s, "planned_service_end_s": s+b, "cost_before": self.cost,
                 "execution_fee": fee, "cost_after": round(self.cost+fee, 10), "H": self.H, "B": self.B}
        assert action["job_id"] == "J"
        assert guard["released"] and guard["unattempted"] and guard["predecessors_complete"]
        assert math.isfinite(b) and b >= .2 and s+b <= self.H and self.cost+fee <= self.B
        self.accept(action)
        self.attempted, self.b, self.s = True, b, s
        self.cost, self.commitment_end, self.phase = self.cost+fee, s+b, "TRAVEL"
        self.emit("EXECUTE_ACCEPTED", ["R_EXECUTE"], action=action, admission_inputs=guard,
                  commitment="b, full fee, destination and single attempt locked now; no refund")
        # Only the offline executor now uses latent duration to generate physical events.
        p = self.truth["p_s"]
        latent_C, occupied_end = s+p, s+max(p, b)
        observable_end = min(self.H, occupied_end)
        points = [(t0+D, "ARRIVE"), (s, "SERVICE_START"),
                  ((t0+observable_end)/2, "EXECUTION_MIDPOINT")]
        if latent_C <= self.H:
            points.append((latent_C, "COMPLETE"))
            if p < b:
                points.append(((latent_C+occupied_end)/2, "IDLE_MIDPOINT"))
            points.append((occupied_end, "EQUIPMENT_AVAILABLE"))
        else:
            if s+b < self.H:
                points.append((s+b, "RESERVATION_END_BUSY"))
            points.append((self.H, "HORIZON_TERMINATE"))
        for at, event in sorted(points, key=lambda pair: pair[0]):
            assert self.t <= at <= self.H
            self.t = at
            if event == "ARRIVE":
                self.zone, self.phase = self.job["zone_id"], "CHANGEOVER"
            elif event == "SERVICE_START":
                self.phase = "SERVICE"
            elif event == "COMPLETE":
                self.completed, self.C = True, at
                self.phase = "RESERVED_IDLE" if p < b else "SERVICE"
                self.audit_source("completion")
            elif event == "EQUIPMENT_AVAILABLE":
                self.phase, self.occupied = "IDLE", False
            elif event == "HORIZON_TERMINATE":
                self.ended, self.censored, self.phase = True, True, "TERMINATED_CENSORED"
                # No availability or completion has been observed, even at termination.
                self.audit_source("termination_at_H")
            elif event.endswith("MIDPOINT"):
                self.probe("execute_request_midpoint" if event == "EXECUTION_MIDPOINT" else "reserved_idle_midpoint")
                continue
            self.emit(event, ["R_EXECUTE", "R_METRICS", "R_SOURCE_AUDIT"])
        if self.t == self.H:
            self.ended = True

    def run(self):
        self.emit("RESET", ["R_COMMON"])
        if self.job["release_s"] == 0:
            self.release()
        for action in self.trial["actions"]:
            if action["action"] == "WAIT":
                self.wait(action)
            elif action["action"] == "REVIEW":
                self.review(action)
            elif action["action"] == "EXECUTE":
                self.execute(action)
            else:
                assert action["action"] == "STOP"
                self.accept(action)
                self.ended = True
                self.emit("STOP", ["R_ACTIONS", "R_METRICS"], action=action)
        assert self.ended
        return self


def independent_checks(runs):
    """Literal analytic assertions, not generated by the simulator's formulas."""
    results = []
    def check(name, condition):
        results.append({"assertion": name, "passed": bool(condition)})
        if not condition:
            raise AssertionError(name)
    def one(run, event):
        matches = [e for e in run.events if e["event"] == event]
        assert len(matches) == 1, event
        return matches[0]
    forbidden = {"source_id", "source_ref", "annotation_start_s", "annotation_end_s", "d_s", "p_s",
                 "semantic_description", "source_segment_index", "seed", "total_duration", "marker_base"}
    def keys(obj):
        if isinstance(obj, dict):
            return set(obj).union(*(keys(v) for v in obj.values()))
        if isinstance(obj, list):
            return set().union(*(keys(v) for v in obj))
        return set()
    row_keys = {"timestamp_s", "left_hand", "right_hand", "head_pose", "left_head_camera_pose",
                "right_head_camera_pose", "hand_quality_flags"}
    expected = {
        "EARLY": {"review": 0, "mid": .5, "return": 1, "accept": 1, "start": 2.5,
                  "planned_end": 7.5, "last": 7.5, "cost": 3.7, "base": 11000, "source_start": 1000,
                  "metrics": {"weighted_completion_rate": 1., "weighted_on_time_rate": 1.,
                              "weighted_tardiness_s": 0., "observed_overrun_s": 0.,
                              "reserved_idle_s": 2.5, "censored_units": 0}},
        "CROSS": {"review": 94, "mid": 94.5, "return": 95, "accept": 95, "start": 96.5,
                  "planned_end": 99.5, "last": 100, "cost": 3.5, "base": 22000, "source_start": 2000,
                  "metrics": {"weighted_completion_rate": 0., "weighted_on_time_rate": 0.,
                              "weighted_tardiness_s": 6., "observed_overrun_s": .5,
                              "reserved_idle_s": 0., "censored_units": 1}},
    }
    check("exactly two separately reset trials; one J each", len(runs) == 2 and all(r.job["job_id"] == "J" for r in runs))
    for r in runs:
        tid = r.trial["trial_id"]
        e = expected[tid]
        check(tid+": reset resources", r.events[0]["t_s"] == 0 and r.events[0]["cost"] == 0
              and r.events[0]["reviews_committed"] == 0 and r.events[0]["zone"] == "Z0")
        req, ret, exe = one(r, "REVIEW_ACCEPTED"), one(r, "REVIEW_RETURN"), one(r, "EXECUTE_ACCEPTED")
        mid = next(x for x in r.events if x["detail"].get("probe") == "review_request_midpoint")
        check(tid+": review request/midpoint/return times", [req["t_s"], mid["t_s"], ret["t_s"]] == [e["review"], e["mid"], e["return"]])
        check(tid+": review commitment before information return", all(x["cost"] == 1 and x["reviews_committed"] == 1
              and x["reviews_returned"] == 0 and x["visible_prefix_s"] == .2 for x in [req, mid]))
        check(tid+": return reveals 0.6 only now", ret["visible_prefix_s"] == .6 and ret["reviews_returned"] == 1)
        check(tid+": execution immediate at availability", exe["t_s"] == e["accept"] == ret["t_s"])
        g = exe["detail"]["admission_inputs"]
        check(tid+": pre-accept guard independent of hidden duration", not forbidden.intersection(keys(g))
              and g["s_s"] == e["start"] and g["planned_service_end_s"] == e["planned_end"])
        check(tid+": execute cost and reservation committed immediately", exe["cost"] == e["cost"]
              and exe["device"]["committed_service_end_s"] == e["planned_end"]
              and all(x["cost"] == e["cost"] for x in r.events[r.events.index(exe):]))
        check(tid+": all decision inputs exclude offline fields", all(not forbidden.intersection(keys(x["view"])) for x in r.views))
        exact_rows, shape, flags = True, True, True
        for record in r.views:
            v = record["view"]
            expected_n = 0 if not v["released"] else (3 if v["t_s"] < e["return"] else 7)
            visible = v["numeric_rows"]
            exact_rows &= len(visible) == expected_n
            exact_rows &= [x["left_hand"]["positions"][0][0] for x in visible] == [e["base"]+100*i for i in range(expected_n)]
            exact_rows &= [x["timestamp_s"] for x in visible] == [e["source_start"]+i/10 for i in range(expected_n)]
            for i, row in enumerate(visible):
                shape &= set(row) == row_keys
                shape &= all(len(row[h][field]) == 21 for h in ["left_hand", "right_hand"] for field in ["positions", "orientations_xyzw"])
                flags &= row["hand_quality_flags"] == {"left": {"occlusion": bool(i%2)}, "right": {"near_edge": i%3 == 2}}
                exact_rows &= row == r.truth["numeric_rows"][i]
            if v["t_s"] < (5 if tid == "EARLY" else 101):
                exact_rows &= v["feedback"]["completion_s"] is None
        check(tid+": distinctive rows prove gating/no future rows/no filling", exact_rows)
        check(tid+": permitted numerical channel shapes and original flags", shape and flags)
        probes = [x for x in r.events if x["event"] == "READ_ONLY_SNAPSHOT"]
        check(tid+": midpoint snapshots are pure detached reads, not actions", all(
            x["detail"]["state_hash_before"] == x["detail"]["state_hash_after"]
            and x["detail"]["added_time_s"] == 0 and not x["detail"]["submitted_action"] for x in probes))
        check(tid+": fixed action count excludes probes", r.actions == 3)
        check(tid+": all events and metrics clipped to H", all(x["t_s"] <= 100 for x in r.events))
        check(tid+": movement ledger", one(r, "ARRIVE")["t_s"] == e["accept"]+1
              and exe["zone"] == "Z0" and one(r, "ARRIVE")["zone"] == "Z1"
              and one(r, "SERVICE_START")["t_s"] == e["start"])
        metrics = r.metrics()
        for key, val in {**e["metrics"], "cost": e["cost"], "observations": 1,
                         "elapsed_s": e["last"], "invalid_actions": 0, "valid_protocol": True}.items():
            check(tid+": final "+key, metrics[key] == val)
        check(tid+": source references only released retrospectively", len(r.audit) == 1
              and r.audit[0]["disclosed_at_s"] == (5 if tid == "EARLY" else 100)
              and "source_ref" not in keys(r.events))
    early, cross = runs
    done = one(early, "COMPLETE")
    idle_probe = next(x for x in early.events if x["detail"].get("probe") == "reserved_idle_midpoint")
    check("EARLY: C=5 meets due=6; reservation still occupied", done["t_s"] == 5
          and done["device"]["occupied"] and not done["device"]["may_submit_action"])
    check("EARLY: reserved-idle midpoint is busy at 6.25", idle_probe["t_s"] == 6.25
          and idle_probe["device"]["phase"] == "RESERVED_IDLE" and idle_probe["device"]["occupied"]
          and idle_probe["reserved_idle_elapsed_s"] == 1.25)
    check("EARLY: full idle booked at completion; actually elapsed at release", done["metrics_if_ended_now"]["reserved_idle_s"] == 2.5
          and done["reserved_idle_elapsed_s"] == 0 and one(early, "EQUIPMENT_AVAILABLE")["reserved_idle_elapsed_s"] == 2.5)
    check("EARLY: STOP waits for equipment at 7.5 not C=5", one(early, "EQUIPMENT_AVAILABLE")["t_s"] == 7.5
          and one(early, "STOP")["t_s"] == 7.5)
    check("CROSS: WAIT does not reveal unreleased rows", all(not x["view"]["numeric_rows"] for x in cross.views if x["view"]["t_s"] < 94))
    check("CROSS: feasible reservation accepted even though hidden C=103", one(cross, "EXECUTE_ACCEPTED")["detail"]["admission_inputs"]["planned_service_end_s"] == 99.5
          and cross.audit[0]["latent_completion_s_offline_NOT_observed"] == 103)
    check("CROSS: still busy at reservation end 99.5", one(cross, "RESERVATION_END_BUSY")["t_s"] == 99.5
          and one(cross, "RESERVATION_END_BUSY")["device"]["occupied"])
    check("CROSS: only 3.5 service seconds observed, not full p=6.5", cross.audit[0]["observed_service_s"] == 3.5
          and cross.audit[0]["unobserved_service_s_offline"] == 3 and cross.audit[0]["observed_completion_s"] is None)
    check("CROSS: H automatic termination, never completion/availability/STOP", cross.events[-1]["event"] == "HORIZON_TERMINATE"
          and not any(x["event"] in {"COMPLETE", "EQUIPMENT_AVAILABLE", "STOP"} for x in cross.events))
    return results


def main():
    inputs, specs = read_json(HERE/"b_inputs.json"), read_json(HERE/"b_truth_spec.json")
    conditions = read_json(ROOT/inputs["condition_source"])
    with (ROOT/inputs["zone_source"]).open(newline="", encoding="utf-8-sig") as f:
        zones = {r["zone_id"]: [float(r["x"]), float(r["y"])] for r in csv.DictReader(f)}
    s1 = next(x for x in conditions["cases"] if x["case_id"] == "S1")
    assert (s1["horizon_s"], s1["cost_budget"], s1["observation_limit"]) == (100, 32, 4)
    assert inputs["expected_common"] == {"H": 100, "B": 32, "K": 4, "initial_zone": "Z0", "alpha": 1, "beta_s": .5}
    assert conditions["initial_zone"] == "Z0" and conditions["worker_count"] == 1
    assert conditions["observation"]["prefix_seconds"] == [.2, .6, 1.]
    assert conditions["observation"]["delay_s"] == conditions["observation"]["cost"] == 1.
    assert conditions["observation"]["max_per_job"] == 2
    assert conditions["service"]["alpha"] == 1 and conditions["service"]["beta_s"] == .5
    assert conditions["service"]["reserve_min_s"] == .2 and not conditions["service"]["reservation_released_early"]
    assert conditions["movement"]["speed"] == 1 and conditions["movement"]["changeover_s"] == .5
    assert conditions["fees"] == {"service_fixed": 2., "reserve_per_second": .1, "distance_per_unit": .2, "wait": 0.}
    assert zones["Z0"] == [0, 0] and zones["Z1"] == [1, 0]
    runs = []
    offline = {"audience": "OFFLINE ONLY; never supplied to fixed-action caller", "synthetic": True, "trials": {}}
    for trial in inputs["trials"]:
        truth = copy.deepcopy(specs["trials"][trial["trial_id"]])
        assert truth["d_s"] == truth["annotation_end_s"]-truth["annotation_start_s"]
        truth["p_s"] = truth["d_s"] + .5
        truth["numeric_rows"] = numeric_rows(truth, specs["row_recipe"])
        offline["trials"][trial["trial_id"]] = truth
        runs.append(FixedTrial(trial, truth, conditions, zones).run())
    assertions = independent_checks(runs)
    OUT.mkdir(exist_ok=True)
    paths = [inputs["condition_source"], inputs["zone_source"],
             "data/raw/C/Restocking_Tasks_and_Constraints/Problem_3_Requirements.pdf",
             "problem/C/2026_“ShuWei Cup”C_Problem.pdf",
             "problem/C/2026年第十二届数维杯全国大学生数学建模挑战赛（秋季赛）C题数据集说明.pdf",
             "experiments/c_q3_fixture/b_inputs.json", "experiments/c_q3_fixture/b_truth_spec.json",
             "experiments/c_q3_fixture/b_fixture.py"]
    sources = {"files": [{"path": p, "sha256": hashlib.sha256((ROOT/p).read_bytes()).hexdigest()} for p in paths],
               "requirements_pdf": paths[2],
               "rule_locators": {
                   "R_COMMON": "Requirements p1 section 1; common_conditions.json /cases/0, /initial_zone",
                   "R_INFO": "Requirements pp1-2 section 2: allowed fields, existing samples; JSON /information_rule, /observation",
                   "R_DELAY": "Requirements p1 section 2: paid review returns only after 1s; JSON /observation",
                   "R_ACTIONS": "Requirements p2 section 3: one equipment, waiting, stopping, <=200 actions",
                   "R_EXECUTE": "Requirements p2 section 3: admission, charge, s, C, occupancy and H; JSON /service, /movement, /fees",
                   "R_SOURCE_AUDIT": "Requirements p2 section 3 and p3 section 5: references after completion or termination",
                   "R_METRICS": "Requirements pp2-3 section 4: every common metric, unfinished C=H, overrun observed only until H"},
               "official_replay_run": False,
               "discovery_scope": "Only official problem/C, data/raw/C and names in data/raw/C/archives/C_Problem_Data.zip; no executable/replayer found. No research/agent files searched or read; *hand_expected* excluded."}
    save("b_sources.json", sources)
    save("b_offline_truth.json", offline)
    save("b_events.jsonl", [e for r in runs for e in r.events], True)
    save("b_policy_views.jsonl", [v for r in runs for v in r.views], True)
    save("b_retrospective_sources.jsonl", [a for r in runs for a in r.audit], True)
    save("b_assertions.json", {"passed": len(assertions), "failed": 0, "assertions": assertions})
    summary = {"kind": "synthetic_fixed_action_only", "official_replay_run": False,
               "trial_count": 2, "case_override": "S1 constants but ONE synthetic J per independent trial, not official S1 eight jobs",
               "results": [{"trial_id": r.trial["trial_id"], "final_zone": r.zone, "action_count": r.actions,
                            "budget_remaining": round(r.B-r.cost, 10), **r.metrics()} for r in runs]}
    save("b_summary.json", summary)
    lines = ["B fixed-action synthetic fixture; official replay NOT RUN."]
    for r in runs:
        lines.append(r.trial["trial_id"]+" events: "+" -> ".join(f'{x["t_s"]:g}:{x["event"]}' for x in r.events))
        lines.append(r.trial["trial_id"]+" metrics: "+json.dumps(r.metrics(), sort_keys=True))
    lines.append(f"PASS {len(assertions)} independent assertions; 2 fresh trials; output: {OUT.relative_to(ROOT)}")
    text = "\n".join(lines)+"\n"
    (OUT/"b_run_output.txt").write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
