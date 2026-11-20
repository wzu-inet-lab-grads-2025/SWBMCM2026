#!/usr/bin/env python3
"""Paired TWO-job synthetic ledger, based on a59642e; NOT official replay.

Run:   python3 experiments/c_q3_fixture/pair_test.py
Check: python3 experiments/c_q3_fixture/pair_test.py --check
Only the first command writes, and only to pair_events.jsonl. Standard library;
no randomness, network, videos, raw-data writes, or legacy main()/b_output writes.

There are exactly two fresh trials per invocation. Each chains A then B using
unchanged FixedTrial.execute/accept/emit, preserving time, zone, cost and action
count between jobs. The old executor's hard-coded action alias J is plumbing
only: actual job objects stay A/B, and exported actions restore those names.
Only per-job state is reset at the switch. Both free prefixes release at t=0.
The old single-job intermediate metrics are NOT used as two-job metrics.

Exports are retrospective synthetic PUBLIC LOG PROJECTIONS, assembled from
legacy snapshots plus the other job's retained feedback and shared inputs.
They are NOT a new policy interface. No policy callback is installed or run;
callback noninterference is NOT RUN, not passed. Legacy offline midpoint probes
are deliberately retained: their times depend on the hidden occupancy end.
Full prefix rows are stored once and referenced from event projections.
"""
import argparse
import copy
import csv
import hashlib
import json
import runpy
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUTPUT = HERE / "pair_events.jsonl"
BASIS = "a59642e95da6faa604ed4b8d9e616a048a54b4ea"
CONDITIONS = "data/raw/C/Restocking_Tasks_and_Constraints/common_conditions.json"
ZONES = "data/raw/C/Restocking_Tasks_and_Constraints/service_zones.csv"
LEGACY = "experiments/c_q3_fixture/b_fixture.py"
SPECS = "experiments/c_q3_fixture/b_truth_spec.json"
SOURCES = [CONDITIONS, ZONES, LEGACY, SPECS,
           "experiments/c_q3_fixture/b_inputs.json",
           "research/C题第三问合成对账.md",
           "data/raw/C/Restocking_Tasks_and_Constraints/Problem_3_Requirements.pdf",
           "problem/C/2026_“ShuWei Cup”C_Problem.pdf",
           "experiments/c_q3_fixture/pair_test.py"]
JOBS = [
    {"job_id": "A", "release_s": 0, "due_s": 20, "priority_weight": 1,
     "zone_id": "Z1", "predecessors": []},
    {"job_id": "B", "release_s": 0, "due_s": 8, "priority_weight": 3,
     "zone_id": "Z1", "predecessors": []},
]
ACTIONS = [{"action": "EXECUTE", "job_id": "A", "b_s": 4},
           {"action": "EXECUTE", "job_id": "B", "b_s": 2},
           {"action": "STOP"}]
FORBIDDEN = {"variant", "verification_variant", "trial_id", "source_id", "source_ref",
             "annotation_start_s", "annotation_end_s", "d_s", "p_s", "seed",
             "semantic_description", "marker_base", "state_hash_before", "state_hash_after"}
CHECKS = []


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def sha(value):
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    CHECKS.append({"assertion": name, "passed": True})


def keys(value):
    if isinstance(value, dict):
        return set(value).union(*(keys(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(keys(v) for v in value))
    return set()


def diff_paths(a, b, prefix=""):
    if isinstance(a, dict) and isinstance(b, dict) and a.keys() == b.keys():
        return [p for k in a for p in diff_paths(a[k], b[k], prefix + "/" + k)]
    return [] if a == b else [prefix]


def one(events, name, job_id):
    found = [e for e in events if e["event"] == name and e["job_id"] == job_id]
    if len(found) != 1:
        raise AssertionError((name, job_id, len(found)))
    return found[0]


def load_inputs():
    conditions = json.loads((ROOT / CONDITIONS).read_text(encoding="utf-8"))
    with (ROOT / ZONES).open(encoding="utf-8-sig", newline="") as f:
        zones = {r["zone_id"]: [float(r["x"]), float(r["y"])] for r in csv.DictReader(f)}
    s1 = next(c for c in conditions["cases"] if c["case_id"] == "S1")
    check("official S1 constants and initial resources", s1 == {
        "case_id": "S1", "jobs": 8, "horizon_s": 100, "cost_budget": 32,
        "observation_limit": 4} and conditions["initial_zone"] == "Z0"
        and conditions["worker_count"] == 1)
    check("official nominal mapping and occupancy rule", conditions["service"]["alpha"] == 1
          and conditions["service"]["beta_s"] == .5
          and conditions["service"]["reserve_min_s"] == .2
          and not conditions["service"]["reservation_released_early"])
    check("official travel, changeover and fees", conditions["movement"]["speed"] == 1
          and conditions["movement"]["changeover_s"] == .5
          and conditions["fees"] == {"service_fixed": 2., "reserve_per_second": .1,
                                     "distance_per_unit": .2, "wait": 0.}
          and zones["Z0"] == [0, 0] and zones["Z1"] == [1, 0])
    check("official information windows and action limit",
          conditions["observation"]["prefix_seconds"] == [.2, .6, 1.]
          and conditions["max_actions"] == 200)
    # run_path does not invoke the legacy main or create a module cache file.
    legacy = runpy.run_path(str(ROOT / LEGACY))
    recipe = json.loads((ROOT / SPECS).read_text(encoding="utf-8"))["row_recipe"]
    truth = {}
    for job_id, start, marker, p in [("A", 3000, 31000, 3), ("B", 4000, 42000, 2)]:
        spec = {"source_ref": "synthetic://PAIR/" + job_id, "source_id": "PAIR_" + job_id,
                "annotation_start_s": start, "annotation_end_s": start + p - .5,
                "d_s": p - .5, "p_s": p, "marker_base": marker,
                "semantic_description": "SYNTHETIC " + job_id + "; OFFLINE ONLY"}
        spec["numeric_rows"] = legacy["numeric_rows"](spec, recipe)
        truth[job_id] = spec
    changed = copy.deepcopy(truth)
    changed["A"].update(p_s=6, d_s=5.5, annotation_end_s=3005.5)
    check("only independent change is A p; d and endpoint are derived",
          set(diff_paths(truth, changed)) == {"/A/p_s", "/A/d_s", "/A/annotation_end_s"})
    prefixes = {j: [copy.deepcopy(r) for r in t["numeric_rows"]
                    if t["annotation_start_s"] <= r["timestamp_s"] <= t["annotation_start_s"] + .2]
                for j, t in truth.items()}
    check("both jobs have identical fixed three-row prefixes across variants",
          all(len(prefixes[j]) == 3 and truth[j]["numeric_rows"] == changed[j]["numeric_rows"]
              for j in truth))
    public = {"case_id": "S1", "synthetic_job_count": 2, "H": 100, "B": 32, "K": 4,
              "initial_zone": "Z0", "alpha": 1, "beta_s": .5, "zones": zones,
              "jobs": JOBS, "fixed_actions": ACTIONS,
              "dispatch_rule": "immediate EXECUTE A; at availability EXECUTE B; at availability STOP"}
    return legacy["FixedTrial"], conditions, zones, public, prefixes, recipe, (truth, changed)


def replay_pair(cls, conditions, zones, truth):
    """Offline orchestration only; there is no callable policy or policy callback."""
    engine = cls({"trial_id": "PAIR", "job": copy.deepcopy(JOBS[0])},
                 copy.deepcopy(truth["A"]), copy.deepcopy(conditions), copy.deepcopy(zones))
    engine.emit("RESET", ["R_COMMON"])
    engine.released, engine.prefix = True, .2
    engine.emit("RELEASE_ALL", ["R_INFO"], job_ids=["A", "B"], free_prefix_s=.2)
    settlements = []
    for index, job in enumerate(JOBS):
        if index:
            shared = (engine.t, engine.zone, engine.cost, engine.actions,
                      engine.reviews, engine.returned, engine.released, engine.prefix)
            if engine.occupied or engine.ended or engine.phase != "IDLE":
                raise AssertionError("job switch requires equipment availability")
            # Reset job-local state ONLY. B was already released with A at t=0.
            engine.job = copy.deepcopy(job)
            engine.truth = copy.deepcopy(truth[job["job_id"]])
            engine.trial = {"trial_id": "PAIR", "job": engine.job}
            engine.attempted = engine.completed = engine.censored = False
            engine.s = engine.b = engine.C = engine.commitment_end = None
            check("A p=%s: job switch preserves all shared resources" % truth["A"]["p_s"],
                  shared == (engine.t, engine.zone, engine.cost, engine.actions,
                             engine.reviews, engine.returned, engine.released, engine.prefix))
        command_s, cost_before = engine.t, engine.cost
        # Legacy execute asserts this literal J. No identity or variant inference.
        action = dict(ACTIONS[index], job_id="J")
        engine.execute(action)
        if not engine.completed or engine.occupied or engine.ended:
            raise AssertionError("this pair requires completion/availability strictly before H")
        m = engine.metrics()
        settlements.append({"job_id": job["job_id"], "command_s": command_s,
                            "service_start_s": engine.s, "completion_s": engine.C,
                            "equipment_available_s": engine.t, "b_s": engine.b,
                            "planned_service_end_s": engine.commitment_end,
                            "execution_fee": round(engine.cost - cost_before, 10),
                            "cumulative_cost": round(engine.cost, 10),
                            "weighted_tardiness_s": m["weighted_tardiness_s"],
                            "observed_overrun_s": m["observed_overrun_s"],
                            "reserved_idle_s": m["reserved_idle_s"]})
    engine.accept(ACTIONS[-1])
    engine.ended = True
    engine.emit("STOP", ["R_ACTIONS", "R_METRICS"], action=ACTIONS[-1])
    weight = sum(j["priority_weight"] for j in JOBS)
    metrics = {"weighted_completion_rate": 1.,
               "weighted_on_time_rate": sum(j["priority_weight"] for j, s in zip(JOBS, settlements)
                                            if s["completion_s"] <= j["due_s"]) / weight,
               "weighted_tardiness_s": sum(s["weighted_tardiness_s"] for s in settlements),
               "cost": round(engine.cost, 10), "observations": engine.reviews, "elapsed_s": engine.t,
               "observed_overrun_s": sum(s["observed_overrun_s"] for s in settlements),
               "reserved_idle_s": sum(s["reserved_idle_s"] for s in settlements),
               "censored_units": 0, "invalid_actions": 0, "valid_protocol": True}
    return engine, settlements, metrics


def export_events(engine, prefixes, public):
    """POST-RUN log assembly, never a decision interface or a callback."""
    feedback = {j["job_id"]: {"attempted": False, "completed": False,
                             "completion_s": None, "censored_at_H": False} for j in JOBS}
    result = []
    for event, record in zip(engine.events, engine.views):
        view = record["view"]
        job_id = view["job"]["job_id"]
        feedback[job_id] = copy.deepcopy(view["feedback"])
        expected_rows = prefixes[job_id] if view["released"] else []
        if view["numeric_rows"] != expected_rows:
            raise AssertionError("legacy active-job prefix differs from shared original rows")
        projection = {"t_s": view["t_s"], "shared_public_inputs_sha256": sha(public),
                      "active_job_id": job_id, "zone": view["zone"],
                      "budget": view["budget"], "device": view["device"],
                      "pending_review": view["pending_review"],
                      "jobs": {j: {"released": view["released"],
                                   "prefix_s": .2 if view["released"] else 0.,
                                   "returned_prefix_ref": "shared_prefixes/" + j if view["released"] else None,
                                   "returned_rows_sha256": sha(prefixes[j]) if view["released"] else sha([]),
                                   "feedback": copy.deepcopy(feedback[j])} for j in feedback}}
        row = {"event": event["event"], "job_id": job_id,
               "exported_public_log": projection, "action_count": event["action_count"],
               "reserved_idle_elapsed_s": event["reserved_idle_elapsed_s"],
               "rule_refs": event["rule_refs"]}
        detail = event["detail"]
        if "action" in detail:
            row["action"] = copy.deepcopy(detail["action"])
            if "job_id" in row["action"]:
                row["action"]["job_id"] = job_id
        if "admission_inputs" in detail:
            row["admission_inputs"] = detail["admission_inputs"]
        if event["event"] == "READ_ONLY_SNAPSHOT":
            row["offline_probe"] = {"label": detail["probe"], "policy_callback": False,
                                    "submitted_action": False, "added_time_s": 0,
                                    "timing_source": "legacy hidden-endpoint-dependent offline scheduler"}
        if event["event"] == "RELEASE_ALL":
            row["released_job_ids"] = ["A", "B"]
        if event["event"] == "COMPLETE":
            audit = next(a for a in engine.audit if a["source_ref"] == "synthetic://PAIR/" + job_id)
            row["retrospective_verifier_only"] = {
                k: audit[k] for k in ["source_ref", "disclosed_at_s", "d_s_offline", "p_s_offline",
                                     "source_annotation_start_s", "source_annotation_end_s"]}
        result.append(row)
    return result


def verify_run(label, engine, events, settlements, metrics, public, prefixes, short):
    def ck(name, value):
        check(label + ": " + name, value)
    expected = ([[0, 1.5, 4.5, 5.5, 5.5, 2.6, 2.6, 0., 0., 1.],
                 [5.5, 6., 8., 8., 8., 2.2, 4.8, 0., 0., 0.]] if short else
                [[0, 1.5, 7.5, 7.5, 5.5, 2.6, 2.6, 0., 2., 0.],
                 [7.5, 8., 10., 10., 10., 2.2, 4.8, 6., 0., 0.]])
    fields = ["command_s", "service_start_s", "completion_s", "equipment_available_s",
              "planned_service_end_s", "execution_fee", "cumulative_cost", "weighted_tardiness_s",
              "observed_overrun_s", "reserved_idle_s"]
    for s, values in zip(settlements, expected):
        for field, value in zip(fields, values):
            ck(s["job_id"] + " " + field, s[field] == value)
        j = s["job_id"]
        for name, field in [("EXECUTE_ACCEPTED", "command_s"), ("SERVICE_START", "service_start_s"),
                            ("COMPLETE", "completion_s"), ("EQUIPMENT_AVAILABLE", "equipment_available_s")]:
            ck(j + " log " + name, one(events, name, j)["exported_public_log"]["t_s"] == s[field])
        exe = one(events, "EXECUTE_ACCEPTED", j)
        ck(j + " admission only public inputs and reservation feasibility",
           not FORBIDDEN.intersection(keys(exe["admission_inputs"]))
           and exe["admission_inputs"]["planned_service_end_s"] <= 100
           and exe["admission_inputs"]["cost_after"] <= 32)
    expected_metrics = {"weighted_completion_rate": 1., "weighted_on_time_rate": 1. if short else .25,
                        "weighted_tardiness_s": 0. if short else 6., "cost": 4.8,
                        "observations": 0, "elapsed_s": 8. if short else 10.,
                        "observed_overrun_s": 0. if short else 2., "reserved_idle_s": 1. if short else 0.,
                        "censored_units": 0, "invalid_actions": 0, "valid_protocol": True}
    ck("all 11 common metrics (W=4, no tardiness normalization)", metrics == expected_metrics)
    ck("one reset; A then B then STOP; no reviews or WAIT", engine.actions == 3
       and [e["action"] for e in events if "action" in e] == ACTIONS
       and sum(e["event"] == "RESET" for e in events) == 1
       and engine.reviews == engine.returned == 0)
    ck("both jobs release together at t=0", events[1]["event"] == "RELEASE_ALL"
       and events[1]["exported_public_log"]["t_s"] == 0
       and all(j["released"] and j["prefix_s"] == .2
               for j in events[1]["exported_public_log"]["jobs"].values()))
    ck("B dispatch uses A availability, not A completion",
       settlements[1]["command_s"] == settlements[0]["equipment_available_s"])
    ck("STOP immediately at B availability; shared budget and location",
       events[-1]["event"] == "STOP" and engine.t == settlements[1]["equipment_available_s"]
       and round(engine.B - engine.cost, 10) == 27.2 and engine.zone == "Z1")
    ck("B has zero travel but still 0.5 changeover",
       one(events, "EXECUTE_ACCEPTED", "B")["admission_inputs"]["D"] == 0
       and settlements[1]["service_start_s"] - settlements[1]["command_s"] == .5)
    ck("same three raw prefix rows for each job; never review/extend",
       all(e["exported_public_log"]["jobs"][j]["returned_rows_sha256"] == sha(prefixes[j])
           and e["exported_public_log"]["jobs"][j]["prefix_s"] == .2
           for e in events[1:] for j in prefixes))
    ck("public log projection and shared payload exclude hidden fields and variant labels",
       not FORBIDDEN.intersection(keys([public, prefixes] + [e["exported_public_log"] for e in events]))
       and all(v not in encode([public, prefixes, engine.views]) for v in ["A_p3", "A_p6"]))
    ck("legacy views preserve actual A/B identity despite internal J action alias",
       {v["view"]["job"]["job_id"] for v in engine.views} == {"A", "B"}
       and all(v["view"]["job"] in JOBS for v in engine.views))
    ck("completion feedback not written before physical completion",
       all(e["exported_public_log"]["jobs"][s["job_id"]]["feedback"]["completion_s"] is None
           for s in settlements for e in events
           if e["exported_public_log"]["t_s"] < s["completion_s"]))
    ck("source references exported only at completion, outside public projection",
       sum("retrospective_verifier_only" in e for e in events) == 2
       and all(e["event"] == "COMPLETE" and e["retrospective_verifier_only"]["disclosed_at_s"]
               == e["exported_public_log"]["t_s"] for e in events if "retrospective_verifier_only" in e))
    complete = one(events, "COMPLETE", "A")["exported_public_log"]
    ck("A completion event does not itself free equipment", complete["device"]["occupied"]
       and not complete["device"]["may_submit_action"]
       and (not short or complete["device"]["phase"] == "RESERVED_IDLE"))
    ck("legacy offline probes are detached reads, not actions",
       all(e["detail"]["state_hash_before"] == e["detail"]["state_hash_after"]
           and e["detail"]["detached_copy"] and not e["detail"]["submitted_action"]
           and e["detail"]["added_time_s"] == 0 for e in engine.events if e["event"] == "READ_ONLY_SNAPSHOT"))


def build_log():
    CHECKS.clear()
    cls, conditions, zones, public, prefixes, recipe, truths = load_inputs()
    runs = []
    for label, truth in zip(["A_p3", "A_p6"], truths):
        before = sha([public, prefixes, truth])
        engine, settlements, metrics = replay_pair(cls, conditions, zones, truth)
        events = export_events(engine, prefixes, public)
        verify_run(label, engine, events, settlements, metrics, public, prefixes, label == "A_p3")
        check(label + ": immutable public and offline input objects", before == sha([public, prefixes, truth]))
        runs.append({"label": label, "events": events, "settlements": settlements, "metrics": metrics})
    left, right = [r["events"] for r in runs]
    def natural_prefix(events):
        return [e for e in events if e["event"] != "READ_ONLY_SNAPSHOT"
                and e["exported_public_log"]["t_s"] < 4.5]
    check("LOG ONLY: non-probe events before first completion are identical", natural_prefix(left) == natural_prefix(right))
    check("LOG ONLY: A admission is identical", one(left, "EXECUTE_ACCEPTED", "A") == one(right, "EXECUTE_ACCEPTED", "A"))
    midpoint_times = [[e["exported_public_log"]["t_s"] for e in events
                       if e.get("offline_probe", {}).get("label") == "execute_request_midpoint"]
                      for events in [left, right]]
    check("LOG ONLY: legacy execution midpoint times depend on hidden occupancy end",
          midpoint_times == [[2.75, 6.75], [3.75, 8.75]])
    idle_times = [[e["exported_public_log"]["t_s"] for e in events
                   if e.get("offline_probe", {}).get("label") == "reserved_idle_midpoint"]
                  for events in [left, right]]
    check("LOG ONLY: legacy early-idle probe occurs only in p=3 version", idle_times == [[5.], []])
    first_difference = next((a, b) for a, b in zip(left, right) if a != b)
    check("LOG ONLY: first exported divergence is offline midpoint, before completion",
          [e["event"] for e in first_difference] == ["READ_ONLY_SNAPSHOT"] * 2
          and [e["exported_public_log"]["t_s"] for e in first_difference] == [2.75, 3.75])
    invariant = copy.deepcopy(truths[0])
    for key in ["p_s", "d_s", "annotation_end_s"]:
        del invariant["A"][key]
    for spec in invariant.values():
        spec["numeric_rows_sha256"] = sha(spec.pop("numeric_rows"))
    manifest = {"record_type": "manifest", "basis_commit": BASIS, "synthetic": True,
                "trial_count": 2, "jobs_per_trial": 2, "official_replay_run": False,
                "scope": "fixed two-job ledger; not official S1 eight jobs, sampling, model validation or robot evidence",
                "policy_callback_noninterference": {"status": "NOT_RUN",
                    "reason": "Existing FixedTrial has snapshot export and a fixed action list, no independent policy callback; none added."},
                "export_boundary": "exported_public_log is post-run log assembly, NOT actual policy invocations; verifier envelope is not a policy input",
                "adapter": "Reuse unchanged legacy execute; action alias J only internally; job identity stays A/B; switch resets job-local state only; both prefixes released at zero",
                "conventions": ["closed source window [start,start+0.2]; no filling or interpolation",
                                "legacy immediate completion feedback; device remains occupied until availability event",
                                "same-time COMPLETE then EQUIPMENT_AVAILABLE then next command or STOP",
                                "legacy offline midpoint probes retained, not WAIT or callbacks",
                                "single-job intermediate metric projections omitted; final pair metrics use all weight W=4"],
                "shared_public_inputs": public, "shared_prefixes": prefixes,
                "shared_offline_inputs_verifier_only": invariant, "synthetic_row_recipe": recipe,
                "independent_intervention_verifier_only": {"A_p_s": [3, 6], "B_p_s": 2,
                    "derived": "A.d_s=A.p_s-0.5; A.annotation_end_s=A.annotation_start_s+A.d_s; all other inputs identical"},
                "rule_locators": {"R_COMMON": "Requirements p1 section 1; conditions /cases/0",
                    "R_INFO": "Requirements pp1-2 section 2", "R_ACTIONS": "Requirements p2 section 3",
                    "R_EXECUTE": "Requirements p2 section 3; conditions /service,/movement,/fees",
                    "R_METRICS": "Requirements pp2-3 section 4", "R_SOURCE_AUDIT": "Requirements pp2-3 sections 3,5"},
                "source_sha256": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in SOURCES}}
    records = [manifest]
    for r in runs:
        records.extend({"record_type": "event", "verification_variant": r["label"], "seq": n, **e}
                       for n, e in enumerate(r["events"]))
        records.append({"record_type": "trial_summary", "verification_variant": r["label"],
                        "jobs": r["settlements"], "second_job_command_s": r["settlements"][1]["command_s"],
                        "stop_s": r["metrics"]["elapsed_s"], "action_count": 3,
                        "budget_remaining": 27.2, "review_limit_remaining": 4, "final_zone": "Z1",
                        "total_weight": 4, "metrics": r["metrics"]})
    records.append({"record_type": "pair_log_comparison", "scope": "SYNTHETIC LOG CHECK ONLY",
                    "first_natural_feedback_difference": {"event": "A COMPLETE", "A_p3_s": 4.5, "A_p6_s": 7.5},
                    "first_exported_difference": {"event": "offline execution midpoint", "A_p3_s": 2.75, "A_p6_s": 3.75},
                    "legacy_execution_midpoints_s": midpoint_times, "legacy_idle_midpoints_s": idle_times,
                    "warning": "Full timestamped logs are NOT indistinguishable before completion. Legacy midpoint=(command+min(H,availability))/2 uses hidden endpoint; if delivered as a policy callback, its clock could leak future information.",
                    "policy_callback_noninterference_status": "NOT_RUN"})
    records.append({"record_type": "assertions", "scope": "synthetic ledger and log checks only",
                    "passed": len(CHECKS), "failed": 0, "checks": copy.deepcopy(CHECKS),
                    "policy_callback_noninterference_status": "NOT_RUN"})
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="recompute and compare existing JSONL byte-for-byte; write nothing")
    args = parser.parse_args()
    records = build_log()
    payload = ("\n".join(encode(r) for r in records) + "\n").encode("utf-8")
    if [json.loads(line) for line in payload.decode("utf-8").splitlines()] != records:
        raise AssertionError("JSONL round trip")
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_bytes() != payload:
            raise AssertionError("pair_events.jsonl is missing or differs; run without --check to regenerate")
    else:
        OUTPUT.write_bytes(payload)
    for row in records:
        if row["record_type"] == "trial_summary":
            print(row["verification_variant"] + " " + encode({"jobs": row["jobs"], "metrics": row["metrics"]}))
    print("PASS %d ledger/log assertions; %d JSONL records; %s; sha256=%s" %
          (len(CHECKS), len(records), "byte-identical, no writes" if args.check else "wrote pair_events.jsonl",
           hashlib.sha256(payload).hexdigest()))
    print("Policy callback noninterference: NOT RUN. Legacy A midpoint clocks: 2.75 vs 3.75; LOG CHECK ONLY.")


if __name__ == "__main__":
    main()
