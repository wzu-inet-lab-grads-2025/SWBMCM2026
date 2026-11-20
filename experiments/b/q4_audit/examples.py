#!/usr/bin/env python3
"""Hand-constructed regression witnesses, NOT a scheduling policy or optimizer."""
from copy import deepcopy
import json
import math
from pathlib import Path

try:
    from .audit import Context, audit_record, nominal_events, pointing, path_at, path_length
except ImportError:
    from audit import Context, audit_record, nominal_events, pointing, path_at, path_length

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def scenario(name, *, new=None, failed=None, recovery=None):
    return {"id": name, "new_alert": new, "failed_vehicle": failed, "recovery_s": recovery}


def record(context, scenario_value, *, policy="synthetic"):
    events = nominal_events(context)
    targets = context.targets(scenario_value)
    jobs = {target: {"start_s": None, "vehicles": []} for target in targets}
    for job in context.nominal_jobs:
        jobs[job["target"]] = {"start_s": job["start_s"], "vehicles": [leg["vehicle"] for leg in job["legs"]]}
    value = {"scenario_id": scenario_value["id"], "policy": policy,
             "N": len(targets), "k": len(context.nominal_jobs),
             "eta": len(context.nominal_jobs) / len(targets),
             "t_last_s": max(j["start_s"] for j in context.nominal_jobs) if len(context.nominal_jobs) == len(targets) else 60.0,
             "jobs": jobs, "events": events, "trace_complete_until_s": 60.0,
             "rotation_model": "linear_axes"}
    # Observe known evidence through reset, then explicit stationary idle to 60.
    for vehicle, initial in context.scene["vehicles_xy_m"].items():
        rows = [e for e in events if e["vehicle"] == vehicle]
        end = max((e["t1"] for e in rows), default=0)
        position = max(rows, key=lambda e: e["t1"])["station"] if rows else initial
        if end < 60:
            events.append({"vehicle": vehicle, "target": None, "kind": "idle", "t0": end, "t1": 60, "station": position})
    return value


def fixture_context(start=5.0, ready=None, *, driving=False, two_jobs=False):
    """Tiny alternate model: one hand-timed task, 2 vehicles, no buildings.

    Explicit context is essential: these are boundary unit witnesses, not the
    competition's nominal q22. Angle/speed geometry is still physically checked.
    """
    ready = start if ready is None else ready
    scene = deepcopy(Context.default().scene)
    scene["buildings_xyxy_height_m"] = {}
    scene["alerts_azimuth_deg_range_m_altitude_m"] = {"T1": [0, 40, 50]}
    if two_jobs:
        scene["alerts_azimuth_deg_range_m_altitude_m"]["T2"] = [90, 40, 50]
    scene["vehicles_xy_m"] = {"V1": [15, 14], "V2": [88, 10]}
    job = {"target": "T1", "start_s": start, "legs": []}
    for vehicle, initial in scene["vehicles_xy_m"].items():
        station = [initial[0], initial[1] + 18] if driving else list(initial)
        travel = 6.0 if driving else 0.0
        angles = pointing(station, [90, 50, 50], 2)
        minimum = max(abs(angles[0]), abs(angles[1])) / 30
        arrival = travel
        # Exact nominal-speed pointing, followed by an explicit wait to start.
        end_slew = arrival + minimum
        job["legs"].append({"vehicle": vehicle, "station": station,
                            "path": [initial, station], "depart_s": 0.0,
                            "arrival_s": arrival, "ready_s": end_slew,
                            "observation_end_s": start + 5,
                            "reset_end_s": start + 5 + minimum})
    return Context(scene, [job])


def trim_failure(value, context, failed):
    """Hand witness: withdraw failed car, preserve locked observation, drop later pairs."""
    original = deepcopy(value["events"])
    dropped = {job["target"] for job in context.nominal_jobs if job["start_s"] >= 5 and any(l["vehicle"] == failed for l in job["legs"])}
    kept = []
    for e in original:
        if e["vehicle"] == failed:
            if e["t0"] >= 5:
                continue
            if e["t1"] > 5:
                fraction = (5 - e["t0"]) / (e["t1"] - e["t0"])
                if e["kind"] == "drive":
                    e["path"] = [e["path"][0], path_at(e["path"], fraction)]
                elif e["kind"] in {"slew", "reset"}:
                    target = context.targets({"new_alert": None})[e["target"]]
                    desired = pointing(e["station"], target, 2)
                    e["interrupted"] = True
                    e["gimbal1_deg"] = [a * fraction for a in desired]
                e["t1"] = 5.0
            kept.append(e)
        elif e["target"] in dropped and e["kind"] in {"observe", "reset"}:
            continue
        elif e["kind"] == "idle" and any(x["vehicle"] == e["vehicle"] and x["target"] in dropped for x in original):
            continue
        else:
            kept.append(e)
    kept.append({"vehicle": failed, "target": None, "kind": "fail", "t0": 5.0, "t1": 5.0})
    for target in dropped:
        value["jobs"][target] = {"start_s": None, "vehicles": []}
        for job in context.nominal_jobs:
            if job["target"] != target:
                continue
            for leg in job["legs"]:
                if leg["vehicle"] == failed:
                    continue
                waits = [e for e in kept if e["vehicle"] == leg["vehicle"] and e["target"] == target and e["kind"] == "wait"]
                for e in waits:
                    e["t1"] = 60.0
    value["events"] = kept
    value["k"] -= len(dropped)
    value["eta"] = value["k"] / value["N"]
    if dropped:
        value["t_last_s"] = 60.0
    return value


def independent_record(value, context):
    """Add hand-witness telemetry for independent saturated axes (separate formula)."""
    value = deepcopy(value)
    value["rotation_model"] = "independent_rate_limited_axes"
    targets = context.targets({"new_alert": None})
    for vehicle in context.scene["vehicles_xy_m"]:
        current = [0.0, 0.0]
        rows = sorted((e for e in value["events"] if e["vehicle"] == vehicle), key=lambda e: e["t0"])
        for e in rows:
            if e["kind"] == "drive":
                current = [0.0, 0.0]
            elif e["kind"] in {"slew", "reset"}:
                goal = pointing(e["station"], targets[e["target"]], 2) if e["kind"] == "slew" else [0.0, 0.0]
                delta = [(goal[0] - current[0] + 180) % 360 - 180, goal[1] - current[1]]
                budget = 30 * (e["t1"] - e["t0"])
                actual = [x + max(-budget, min(budget, d)) for x, d in zip(current, delta)]
                e.update(gimbal0_deg=list(current), gimbal_goal_deg=goal,
                         gimbal1_deg=actual, axis_speed_deg_s=30,
                         planned_end_s=e["t0"] + max(abs(d) for d in delta) / 30)
                current = actual
    return value


def cases():
    result = []
    def add(name, context, sc, value, status="pass", codes=(), notes=""):
        sc, value = deepcopy(sc), deepcopy(value)
        sc["id"] = name
        value["scenario_id"] = name
        result.append({"id": name, "context": context.to_dict(), "scenario": sc, "record": value,
                       "expected_status": status, "expected_codes": list(codes), "notes": notes})

    ctx = Context.default()
    sc = scenario("nominal")
    base = record(ctx, sc, policy="frozen-q22-replay")
    add("nominal_ok", ctx, sc, base, notes="Frozen q22; N=k=3, latest start=10.3521185907042, not latest end.")
    bad = deepcopy(base)
    bad["t_last_s"] += 5
    add("end_not_start_bad", ctx, sc, bad, "fail", ["metric_mismatch"])
    bad = deepcopy(base)
    bad["jobs"]["T1"]["start_s"] += 5
    add("job_end_not_start_bad", ctx, sc, bad, "fail", ["job_event_mismatch"])
    new = scenario("new", new=[90, 40, 50])
    added = record(ctx, new)
    add("new_alert_counted_ok", ctx, new, added, notes="N=4,k=3,eta=.75,t_last=60 even with no event for T4.")
    bad = deepcopy(added)
    bad.update(N=3, eta=1, t_last_s=base["t_last_s"])
    del bad["jobs"]["T4"]
    add("new_alert_omitted_bad", ctx, new, bad, "fail", ["metric_mismatch", "job_fields_missing"])

    fail_sc = scenario("during", failed="V3")
    failed = trim_failure(record(ctx, fail_sc), ctx, "V3")
    add("failure_during_observation_ok", ctx, fail_sc, failed, notes="T1 remains locked despite failed V3 exiting at 5; V5 observes to 6.532 and resets. T3 unstarted.")
    bad = deepcopy(failed)
    bad["jobs"]["T1"] = {"start_s": None, "vehicles": []}
    bad.update(k=1, eta=1/3)
    add("locked_score_removed_bad", ctx, fail_sc, bad, "fail", ["job_event_mismatch", "metric_mismatch"])
    bad = deepcopy(failed)
    next(e for e in bad["events"] if e["vehicle"] == "V5" and e["kind"] == "observe")["t1"] = 5.0
    add("locked_partner_released_bad", ctx, fail_sc, bad, "fail", ["locked_healthy_occupancy", "observation_duration"])
    bad = deepcopy(failed)
    e = deepcopy(next(e for e in base["events"] if e["vehicle"] == "V3" and e["target"] == "T3" and e["kind"] == "drive"))
    bad["events"].append(e)
    add("failed_vehicle_reused_bad", ctx, fail_sc, bad, "fail", ["failed_vehicle_activity"])

    five = fixture_context(5)
    sf = scenario("five")
    five_record = record(five, sf)
    add("at_5_without_failure_ok", five, sf, five_record, notes="A start exactly at 5 is permitted when the pair is healthy.")
    sfail = scenario("five_fail", failed="V1")
    five_failed = trim_failure(record(five, sfail), five, "V1")
    add("at_5_failure_precedes_start_ok", five, sfail, five_failed, notes="Exactly-at-5 start is NOT locked; failed V1 makes it unstarted: k=0,t_last=60.")
    bad = record(five, sfail)
    bad["events"].append({"vehicle": "V1", "target": None, "kind": "fail", "t0": 5, "t1": 5})
    add("at_5_failure_ignored_bad", five, sfail, bad, "fail", ["failed_vehicle_activity", "metric_mismatch"])

    sixty = fixture_context(60, ready=2)
    s60 = scenario("sixty")
    sixty_record = record(sixty, s60)
    add("at_60_ok", sixty, s60, sixty_record, notes="Observation begins at 60, ends at 65; k=1,t_last=60.")
    late = deepcopy(sixty_record)
    late["jobs"]["T1"]["start_s"] += .01
    for e in late["events"]:
        if e["kind"] == "wait":
            e["t1"] += .01
        elif e["kind"] in {"observe", "reset"}:
            e["t0"] += .01
            e["t1"] += .01
    late.update(k=0, eta=0, t_last_s=60)
    add("after_60_not_counted_ok", sixty, s60, late)
    bad = deepcopy(late)
    bad.update(k=1, eta=1)
    add("after_60_counted_bad", sixty, s60, bad, "fail", ["metric_mismatch"])
    bad = deepcopy(sixty_record)
    bad.update(k=0, eta=0)
    add("at_60_scored_by_end_bad", sixty, s60, bad, "fail", ["metric_mismatch"])

    transit_ctx = fixture_context(10, ready=8, driving=True)
    transit_sc = scenario("transit")
    transit = record(transit_ctx, transit_sc)
    for vehicle in transit_ctx.scene["vehicles_xy_m"]:
        old = next(e for e in transit["events"] if e["vehicle"] == vehicle and e["kind"] == "drive")
        index = transit["events"].index(old)
        mid = path_at(old["path"], 5/6)
        first, second = deepcopy(old), deepcopy(old)
        first.update(t1=5, path=[old["path"][0], mid])
        second.update(t0=5, path=[mid, old["path"][-1]], command_issued_s=5, command_effective_s=5)
        transit["events"][index:index+1] = [first, second]
    add("in_transit_handoff_ok", transit_ctx, transit_sc, transit, notes="Split at t=5: actual path endpoint, not planned station, is the new drive origin.")
    transit_fail_sc = scenario("transit-fail", failed="V1")
    transit_failed = trim_failure(record(transit_ctx, transit_fail_sc), transit_ctx, "V1")
    add("failure_in_transit_ok", transit_ctx, transit_fail_sc, transit_failed, notes="Drive clipped to true 5 s position; healthy partner waits, k=0.")
    bad_failure = deepcopy(transit_failed)
    next(e for e in bad_failure["events"] if e["kind"] == "drive" and e["vehicle"] == "V1")["t1"] = 6
    add("failure_in_transit_late_exit_bad", transit_ctx, transit_fail_sc, bad_failure, "fail", ["failed_vehicle_activity"])
    bad = deepcopy(transit)
    second = next(e for e in bad["events"] if e["kind"] == "drive" and e["t0"] == 5)
    second["path"][0] = list(second["path"][-1])
    add("in_transit_teleport_bad", transit_ctx, transit_sc, bad, "fail", ["position_discontinuity", "drive_speed"])

    outage = scenario("outage", recovery=12)
    add("outage_initial_instructions_ok", ctx, outage, base, notes="All q22 instructions received at t=0; post-5 execution is legal during outage.")
    restored = deepcopy(transit)
    # At t=5, hold east-horizontal at the true position until recovery t=12.
    for vehicle in transit_ctx.scene["vehicles_xy_m"]:
        second = next(e for e in restored["events"] if e["vehicle"] == vehicle and e["kind"] == "drive" and e["t0"] == 5)
        hold = {"vehicle": vehicle, "target": "T1", "kind": "wait", "t0": 5, "t1": 12, "station": second["path"][0]}
        for e in restored["events"]:
            if e["vehicle"] != vehicle:
                continue
            if e["t0"] >= 5:
                if e["kind"] == "idle":
                    e["t0"] += 7
                else:
                    e["t0"] += 7
                    e["t1"] += 7
                    e.update(command_issued_s=5, command_effective_s=12)
        restored["events"].append(hold)
    restored["jobs"]["T1"]["start_s"] += 7
    restored["t_last_s"] += 7
    add("outage_new_command_at_recovery_ok", transit_ctx, outage, restored)
    bad = deepcopy(restored)
    for e in bad["events"]:
        if e.get("command_effective_s") == 12:
            e["command_effective_s"] = 11.99
    add("outage_early_command_bad", transit_ctx, outage, bad, "fail", ["outage_new_command"])
    bad = deepcopy(base)
    for e in bad["events"]:
        e.pop("command_issued_s", None)
        e.pop("command_effective_s", None)
    add("command_evidence_missing", ctx, outage, bad, "incomplete", ["command_evidence_missing"])
    bad = deepcopy(base)
    for e in bad["events"]:
        if e["kind"] == "drive":
            e.pop("path")
    add("path_evidence_missing", ctx, sc, bad, "incomplete", ["drive_path_missing"])
    bad = deepcopy(base)
    bad.pop("trace_complete_until_s")
    add("trace_completeness_missing", ctx, sc, bad, "incomplete", ["trace_completeness_missing"])
    bad = deepcopy(base)
    bad["events"].append({"vehicle": "V3", "target": "T1", "kind": "idle", "t0": 2, "t1": 3})
    add("event_overlap_bad", ctx, sc, bad, "fail", ["event_overlap", "prefix_changed"])
    bad = deepcopy(base)
    for e in bad["events"]:
        if e["kind"] == "observe" and e["vehicle"] == "V5" and e["target"] == "T1":
            e["vehicle"] = "V3"
    add("same_vehicle_twice_bad", ctx, sc, bad, "fail", ["joint_start"])
    bad = deepcopy(base)
    next(e for e in bad["events"] if e["kind"] == "observe" and e["vehicle"] == "V5")["t0"] += .1
    add("overlapping_not_synchronized_bad", ctx, sc, bad, "fail", ["joint_start"])
    bad = deepcopy(base)
    next(e for e in bad["events"] if e["kind"] == "slew" and e["vehicle"] == "V3")["t1"] -= .1
    add("not_ready_bad", ctx, sc, bad, "fail", ["slew_speed"])
    bad = deepcopy(base)
    path = next(e for e in bad["events"] if e["kind"] == "drive" and e["vehicle"] == "V3")["path"]
    path[-1][0] += 1
    add("prefix_geometry_changed_bad", ctx, sc, bad, "fail", ["prefix_position"])
    # Rotation interrupted by failure: require actual angular endpoint evidence.
    slew_fail_sc = scenario("slew-fail", failed="V4")
    slew_failed = trim_failure(record(ctx, slew_fail_sc), ctx, "V4")
    add("failure_during_slew_ok", ctx, slew_fail_sc, slew_failed)
    bad = deepcopy(slew_failed)
    next(e for e in bad["events"] if e.get("interrupted")).pop("gimbal1_deg")
    add("interrupted_angle_missing", ctx, slew_fail_sc, bad, "incomplete", ["interrupted_gimbal_missing"])
    independent = independent_record(base, ctx)
    add("independent_axes_nominal_ok", ctx, sc, independent)
    saturated_failure = independent_record(slew_failed, ctx)
    add("independent_axes_V4_failure_at_5_ok", ctx, slew_fail_sc, saturated_failure,
        notes="At 5s V4 pan=121.99295491938287, tilt=23.184672017939654; tilt already stopped, pan not yet at 178.73579121051677.")
    implicit_clip = deepcopy(saturated_failure)
    next(e for e in implicit_clip["events"] if e.get("interrupted")).pop("interrupted")
    add("independent_axes_planned_end_clip_ok", ctx, slew_fail_sc, implicit_clip)
    bad = deepcopy(saturated_failure)
    e = next(e for e in bad["events"] if e.get("interrupted"))
    fraction = (e["t1"] - e["t0"]) / (e["planned_end_s"] - e["t0"])
    e["gimbal1_deg"] = [g * fraction for g in e["gimbal_goal_deg"]]
    add("independent_axes_proportional_endpoint_bad", ctx, slew_fail_sc, bad, "fail", ["independent_axis_endpoint"])
    bad = deepcopy(saturated_failure)
    next(e for e in bad["events"] if e.get("interrupted")).pop("gimbal_goal_deg")
    add("independent_axes_goal_missing", ctx, slew_fail_sc, bad, "incomplete", ["gimbal_goal_missing"])
    bad = deepcopy(saturated_failure)
    next(e for e in bad["events"] if e.get("interrupted")).pop("gimbal1_deg")
    add("independent_axes_endpoint_missing", ctx, slew_fail_sc, bad, "incomplete", ["interrupted_gimbal_missing"])
    # Split V4 before disturbance, then resume from a NONZERO actual attitude.
    split = deepcopy(independent)
    e = next(e for e in split["events"] if e["vehicle"] == "V4" and e["kind"] == "slew")
    index = split["events"].index(e)
    first, second = deepcopy(e), deepcopy(e)
    actual = [30 * (2 - e["t0"]), e["gimbal_goal_deg"][1]]
    first.update(t1=2.0, gimbal1_deg=actual, interrupted=True)
    second.update(t0=2.0, gimbal0_deg=actual)
    split["events"][index:index + 1] = [first, second]
    add("independent_axes_nonzero_t0_resume_ok", ctx, sc, split)
    bad = deepcopy(split)
    next(e for e in bad["events"] if e["vehicle"] == "V4" and e["kind"] == "slew" and e["t0"] == 2)["gimbal0_deg"] = [0, 0]
    add("independent_axes_restart_from_zero_bad", ctx, sc, bad, "fail", ["gimbal_discontinuity", "rotation_planned_end"])
    # A missing path is not a proven teleport; a missing ready_s is harmless when
    # the complete physical drive/slew chain independently reconstructs readiness.
    return result


def main():
    dest = HERE / "samples"
    dest.mkdir(exist_ok=True)
    witnesses = cases()
    with (dest / "cases.jsonl").open("w") as output:
        for witness in witnesses:
            output.write(json.dumps(witness, ensure_ascii=False, allow_nan=False) + "\n")
    # Standalone nominal-policy JSONLs: deliberately reverse the right file.
    selected = [c for c in witnesses if c["id"] in {"nominal_ok", "new_alert_counted_ok", "failure_during_observation_ok", "outage_initial_instructions_ok"}]
    (dest / "scenarios.json").write_text(json.dumps({"scenarios": [c["scenario"] for c in selected]}, indent=2) + "\n")
    for side, rows in (("left", selected), ("right", reversed(selected))):
        records = []
        for c in rows:
            value = deepcopy(c["record"])
            value["policy"] = f"hand-witness-{side}"
            records.append(json.dumps(value))
        (dest / f"{side}.jsonl").write_text("\n".join(records) + "\n")
    (dest / "single.json").write_text(json.dumps(selected[0]["record"], indent=2) + "\n")
    summaries = []
    for c in witnesses:
        result = audit_record(c["record"], c["scenario"], context=Context.from_dict(c["context"]))
        codes = sorted({x["code"] for x in result["errors"] + result["missing_evidence"]})
        good = result["status"] == c["expected_status"] and set(c["expected_codes"]) <= set(codes)
        summaries.append({"id": c["id"], "expected_status": c["expected_status"], "actual_status": result["status"],
                          "expectation_met": good, "codes": codes, "recomputed": result.get("recomputed")})
    out = ROOT / "runs/b/q4_audit"
    out.mkdir(parents=True, exist_ok=True)
    (out / "synthetic_results.json").write_text(json.dumps({"passed": all(r["expectation_met"] for r in summaries), "cases": summaries}, indent=2) + "\n")
    print(f"{sum(r['expectation_met'] for r in summaries)}/{len(summaries)} witness expectations met")
    for row in summaries:
        if not row["expectation_met"]:
            print(json.dumps(row))
    return 0 if all(r["expectation_met"] for r in summaries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
