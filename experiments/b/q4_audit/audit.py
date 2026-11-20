#!/usr/bin/env python3
"""Independent, standard-library-only Q4 trace verifier (never a scheduler)."""
from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
EPS = 1e-6
KINDS = {"drive", "slew", "wait", "observe", "reset", "idle", "fail"}
ACTIVE = KINDS - {"fail"}
ROTATION_MODELS = {"linear_axes", "independent_rate_limited_axes"}


def number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def vector(x, n=2):
    return isinstance(x, (list, tuple)) and len(x) == n and all(number(a) for a in x)


def close(a, b, eps=EPS):
    return number(a) and number(b) and abs(a - b) <= eps


def dist(a, b):
    return math.dist(a, b)


def wrap(x):
    return (x + 180) % 360 - 180


def angle_distance(a, b):
    return max(abs(wrap(a[0] - b[0])), abs(a[1] - b[1]))


def independent_axes_at(initial, goal, elapsed, rate=30.0):
    """Each axis runs at rate toward goal and individually stops on arrival.

    Pan takes the wrapped shortest displacement; tilt is not circular. This is
    NOT endpoint interpolation: a fast-finishing tilt can be stationary while
    pan is still moving. The initial state is the actual event t0 state.
    """
    deltas = [wrap(goal[0] - initial[0]), goal[1] - initial[1]]
    budget = max(0.0, elapsed) * rate
    return [initial[i] + math.copysign(min(abs(delta), budget), delta)
            for i, delta in enumerate(deltas)]


def path_length(path):
    return sum(dist(a, b) for a, b in zip(path, path[1:]))


def path_at(path, fraction):
    """Arc-length interpolation, including zero-length segments."""
    left = max(0.0, min(1.0, fraction)) * path_length(path)
    for a, b in zip(path, path[1:]):
        length = dist(a, b)
        if length > 0 and left <= length:
            return [a[i] + (b[i] - a[i]) * left / length for i in range(2)]
        left -= length
    return list(path[-1])


def follows_subpath(actual, original):
    """Accept a logged split of an initial drive, but never a shortcut/new route."""
    total = path_length(original)
    if total <= EPS:
        return all(dist(p, original[0]) <= EPS for p in actual)
    possible_starts, offset = [], 0.0
    for a, b in zip(original, original[1:]):
        length = dist(a, b)
        if length > EPS:
            fraction = sum((actual[0][i] - a[i]) * (b[i] - a[i]) for i in range(2)) / length ** 2
            projected = [a[i] + max(0, min(1, fraction)) * (b[i] - a[i]) for i in range(2)]
            if dist(projected, actual[0]) <= EPS:
                possible_starts.append(offset + max(0, min(1, fraction)) * length)
        offset += length
    for start in possible_starts:
        distance = path_length(actual)
        if start + distance > total + EPS:
            continue
        traversed, ok = start, True
        for a, b in zip(actual, actual[1:]):
            traversed += dist(a, b)
            ok &= dist(b, path_at(original, traversed / total)) <= EPS
        if ok:
            return True
    return False


def segment_box(a, b, lo, hi):
    """Closed slab intersection of a finite segment with an axis-aligned box."""
    enter, leave = 0.0, 1.0
    for x, y, low, high in zip(a, b, lo, hi):
        delta = y - x
        if abs(delta) < 1e-14:
            if x < low or x > high:
                return False
        else:
            u, v = sorted(((low - x) / delta, (high - x) / delta))
            enter, leave = max(enter, u), min(leave, v)
            if enter > leave:
                return False
    return True


def point_segment_distance(p, a, b):
    d = [b[i] - a[i] for i in range(2)]
    norm = sum(x * x for x in d)
    f = 0 if norm == 0 else max(0, min(1, sum((p[i] - a[i]) * d[i] for i in range(2)) / norm))
    return dist(p, [a[i] + f * d[i] for i in range(2)])


def segment_rectangle_distance(a, b, box):
    x0, y0, x1, y1 = box[:4]
    if segment_box(a, b, (x0, y0), (x1, y1)):
        return 0.0
    corners = [(x0, y0), (x0, y1), (x1, y1), (x1, y0)]
    edges = list(zip(corners, corners[1:] + corners[:1]))
    return min([point_segment_distance(p, a, b) for p in corners]
               + [point_segment_distance(p, c, d) for p in (a, b) for c, d in edges])


def pointing(position, target, height):
    dx, dy, dz = target[0] - position[0], target[1] - position[1], target[2] - height
    return [math.degrees(math.atan2(dy, dx)), math.degrees(math.atan2(dz, math.hypot(dx, dy)))]


@dataclass
class Context:
    scene: dict
    nominal_jobs: list

    @classmethod
    def default(cls):
        return cls(json.loads((HERE / "data/scene.json").read_text()),
                   json.loads((HERE / "data/nominal_q22.json").read_text())["q22"]["jobs"])

    @classmethod
    def from_dict(cls, value):
        return cls(value["scene"], value["nominal_jobs"])

    def to_dict(self):
        return {"scene": self.scene, "nominal_jobs": self.nominal_jobs}

    def targets(self, scenario):
        alerts = dict(self.scene["alerts_azimuth_deg_range_m_altitude_m"])
        if scenario.get("new_alert") is not None:
            alerts["T4"] = scenario["new_alert"]
        cx, cy = self.scene["centre_xy_m"]
        return {key: [cx + r * math.cos(math.radians(theta)),
                      cy + r * math.sin(math.radians(theta)), h]
                for key, (theta, r, h) in alerts.items()}


def nominal_events(context):
    """Expand only the frozen input plan, not any policy's claimed timing."""
    events = []
    for job in context.nominal_jobs:
        for leg in job["legs"]:
            base = {"vehicle": leg["vehicle"], "target": job["target"],
                    "station": leg["station"], "command_issued_s": 0.0,
                    "command_effective_s": 0.0}
            phases = [("drive", leg["depart_s"], leg["arrival_s"]),
                      ("slew", leg["arrival_s"], leg["ready_s"]),
                      ("wait", leg["ready_s"], job["start_s"]),
                      ("observe", job["start_s"], leg["observation_end_s"]),
                      ("reset", leg["observation_end_s"], leg["reset_end_s"])]
            for kind, t0, t1 in phases:
                event = dict(base, kind=kind, t0=t0, t1=t1)
                if kind == "drive":
                    event["path"] = leg["path"]
                    event["gimbal_deg"] = [0, 0]
                events.append(event)
    return events


class Audit:
    def __init__(self, record, scenario, context):
        self.record, self.scenario, self.ctx = record, scenario, context
        self.issues = []
        self.events = []
        self.rows = defaultdict(list)
        self.evidence = {}
        self.checks = defaultdict(int)
        self.targets = {}
        self.event_log_present = False
        self.trace_declared_complete = False
        self.global_evidence_gap = False

    def issue(self, code, message, *, severity="error", event=None, target=None, **details):
        row = {"severity": severity, "code": code, "message": message}
        if event is not None:
            row.update(event_index=event.get("_index"), vehicle=event.get("vehicle"))
        if target is not None:
            row["target"] = target
        if details:
            row["details"] = details
        # Avoid repeated generic missing-evidence notices; keep per-event evidence.
        if row not in self.issues:
            self.issues.append(row)

    def check(self, ok, code, message, **kwargs):
        self.checks[code] += 1
        if not ok:
            self.issue(code, message, **kwargs)
        return ok

    def missing(self, code, message, **kwargs):
        self.issue(code, message, severity="missing", **kwargs)

    def scenario_check(self):
        s = self.scenario
        if not isinstance(s, dict) or not isinstance(s.get("id"), str):
            self.issue("scenario_schema", "External scenario must contain string id.")
            return False
        good = True
        for key in ("new_alert", "failed_vehicle", "recovery_s"):
            if key not in s:
                self.missing("scenario_field_missing", f"External scenario lacks {key}.")
                good = False
        good &= self.check(self.record.get("scenario_id") == s["id"], "scenario_id", "Record and external scenario IDs differ.")
        if not isinstance(self.record.get("policy"), str) or not self.record["policy"]:
            self.missing("policy_missing", "Record requires a nonempty policy label.")
        if s.get("failed_vehicle") is not None:
            good &= self.check(isinstance(s["failed_vehicle"], str) and s["failed_vehicle"] in self.ctx.scene["vehicles_xy_m"], "scenario_failure", "Unknown failed vehicle.")
        new = s.get("new_alert")
        if new is not None:
            valid = vector(new, 3) and 0 <= new[0] < 360 and 30 <= new[1] <= 50 and close(new[2], 50)
            good &= self.check(valid, "scenario_new_alert", "New alert must satisfy theta in [0,360), range in [30,50], altitude=50.")
        rec = s.get("recovery_s")
        if rec is not None:
            good &= self.check(number(rec) and rec > 5, "scenario_recovery", "Recovery must be finite and later than 5 s.")
            if number(rec) and rec > 30 and s.get("recovery_scope") != "sensitivity":
                good &= self.check(False, "scenario_recovery_scope", "Recovery after 30 s requires an explicitly marked stress test.")
        return bool(good)

    def parse_events(self):
        raw = self.record.get("events")
        if not isinstance(raw, list):
            self.missing("events_missing", "events must be a complete per-vehicle list.")
            return
        self.event_log_present = True
        self.trace_declared_complete = number(self.record.get("trace_complete_until_s")) and self.record["trace_complete_until_s"] >= 60
        for index, raw_event in enumerate(raw):
            if not isinstance(raw_event, dict):
                self.issue("event_schema", "Event is not an object.", index=index)
                continue
            e = dict(raw_event, _index=index)
            required = {"vehicle", "target", "kind", "t0", "t1"}
            if not required <= e.keys():
                self.missing("event_field_missing", "Event lacks mandatory fields.", event=e, fields=sorted(required - e.keys()))
                continue
            if not self.check(isinstance(e["kind"], str) and e["kind"] in KINDS
                              and isinstance(e["vehicle"], str) and e["vehicle"] in self.ctx.scene["vehicles_xy_m"]
                              and (e["target"] is None or isinstance(e["target"], str)),
                              "event_schema", "Unknown/malformed event kind, vehicle or target.", event=e):
                continue
            if not self.check(number(e["t0"]) and number(e["t1"]) and e["t0"] >= -EPS and e["t1"] >= e["t0"] - EPS,
                              "event_time", "Times must be finite, nonnegative and ordered.", event=e):
                continue
            if e["target"] is not None and e["target"] not in self.targets:
                self.issue("event_target", "Event target is absent from the external scenario.", event=e)
            if e["kind"] in {"slew", "observe", "wait"} and e["target"] is None:
                self.issue("event_target", "Task phase requires target.", event=e)
            self.events.append(e)
            self.rows[e["vehicle"]].append(e)
        # Input order resolves instantaneous events at a shared timestamp. No implicit
        # kind ordering that might hide an impossible zero-duration drive/reset.
        for rows in self.rows.values():
            rows.sort(key=lambda e: (e["t0"], e["_index"]))

    def route(self, path, event):
        if not isinstance(path, list) or len(path) < 2 or not all(vector(p) for p in path):
            self.missing("drive_path_missing", "A drive needs its actual traversed polyline, including interrupted endpoint.", event=event)
            return False
        radius = self.ctx.scene["vehicle_radius_m"]
        side = self.ctx.scene["field_side_m"]
        for a, b in zip(path, path[1:]):
            self.check(all(radius - EPS <= x <= side - radius + EPS for p in (a, b) for x in p),
                       "field_boundary", "Swept vehicle disk leaves field.", event=event)
            for building, box in self.ctx.scene["buildings_xyxy_height_m"].items():
                self.check(segment_rectangle_distance(a, b, box) >= radius - EPS,
                           "building_clearance", "Swept vehicle disk enters building interior.", event=event, building=building)
        return True

    def visible(self, position, target, event):
        pointing_deg = pointing(position, target, self.ctx.scene["camera_height_m"])
        low, high = self.ctx.scene["elevation_deg"]
        ok = self.check(low - EPS <= pointing_deg[1] <= high + EPS, "elevation", "Station elevation out of range.", event=event)
        camera = list(position) + [self.ctx.scene["camera_height_m"]]
        for name, (x0, y0, x1, y1, h) in self.ctx.scene["buildings_xyxy_height_m"].items():
            ok &= self.check(not segment_box(camera, target, (x0, y0, 0), (x1, y1, h)),
                             "line_of_sight", "Finite camera-target segment touches/intersects building.", event=event, building=name)
        return ok

    def instructions(self, e):
        if e["kind"] not in {"drive", "slew", "observe"}:
            return
        issued, effective = e.get("command_issued_s"), e.get("command_effective_s")
        if not number(effective) or not number(issued):
            self.missing("command_evidence_missing", "Instruction-bearing phases need command_issued_s and command_effective_s.", event=e)
            return
        self.check(issued >= -EPS and issued <= effective + EPS and effective <= e["t0"] + EPS,
                   "command_time_order", "Require 0 <= issued <= effective <= execution.", event=e)
        if effective <= EPS and issued <= EPS:
            # Initial command identity is tested against original per-vehicle target
            # and station/path, not trusted merely because a timestamp is zero.
            legs = [leg for job in self.ctx.nominal_jobs if job["target"] == e["target"]
                    for leg in job["legs"] if leg["vehicle"] == e["vehicle"]]
            self.check(bool(legs), "initial_command_identity", "A t=0 instruction cannot add a new target/vehicle assignment.", event=e)
            if legs:
                leg = legs[0]
                if e["kind"] == "drive" and isinstance(e.get("path"), list) and len(e["path"]) >= 2 and all(vector(p) for p in e["path"]):
                    self.check(follows_subpath(e["path"], leg["path"]), "initial_command_route",
                               "Initial instruction has a changed route (use a new effective command timestamp for rerouting).", event=e)
                elif vector(e.get("station")):
                    self.check(dist(e["station"], leg["station"]) <= EPS, "initial_command_station", "Initial instruction has a changed station.", event=e)
        else:
            self.check(issued >= 5 and effective >= 5, "anticipatory_command", "New instructions cannot precede the disturbance.", event=e)
            if e["target"] == "T4":
                self.check(issued >= 5, "new_alert_before_release", "T4 is not known before 5 s.", event=e)
            rec = self.scenario.get("recovery_s")
            if rec is not None:
                self.check(effective >= rec, "outage_new_command", "New instruction becomes effective before communication recovery.", event=e)
            # Every changed command is forbidden for a task already locked at 5.
            for job in self.ctx.nominal_jobs:
                if job["start_s"] < 5 and job["target"] == e["target"]:
                    self.check(False, "locked_reschedule", "A task started before 5 s cannot receive a reassignment.", event=e)

    def independent_rotation(self, event, initial, physical_goal):
        """Reconstruct a saturated per-axis rotation; return actual t1 attitude."""
        e = event
        rate = self.ctx.scene["axis_speed_deg_s"]
        duration = e["t1"] - e["t0"]
        goal = e.get("gimbal_goal_deg")
        if not vector(goal):
            self.missing("gimbal_goal_missing", "Independent-axis rotation needs gimbal_goal_deg, including on a clipped segment.", event=e)
            return None
        if physical_goal is None:
            self.missing("gimbal_goal_unverified", "Cannot cross-check declared goal without a reconstructed target/station.", event=e)
        else:
            self.check(angle_distance(goal, physical_goal) <= EPS,
                       "gimbal_goal_mismatch", "Rotation goal differs from target direction or reset-to-east goal.", event=e)
        if "axis_speed_deg_s" in e:
            self.check(close(e["axis_speed_deg_s"], rate), "axis_rate_mismatch", "Declared axis speed differs from nominal 30 deg/s.", event=e)
        if initial is None:
            self.missing("slew_unverified", "Cannot reconstruct actual t0 attitude for independent-axis rotation.", event=e)
            return None
        expected = independent_axes_at(initial, goal, duration, rate)
        minimum = angle_distance(initial, goal) / rate
        complete = duration >= minimum - EPS
        planned = e.get("planned_end_s")
        if planned is not None:
            self.check(number(planned) and close(planned, e["t0"] + minimum), "rotation_planned_end",
                       "planned_end_s must be independently reproduced from actual t0 attitude and goal.", event=e,
                       expected_end_s=e["t0"] + minimum)
        # Failure or explicit planned-end clipping is independent evidence of a
        # partial phase; no need to force a redundant interrupted boolean.
        truncated = bool(e.get("interrupted")) or number(planned) and e["t1"] < planned - EPS or (
            e["vehicle"] == self.scenario.get("failed_vehicle") and close(e["t1"], 5) and not complete)
        if not complete and not truncated:
            self.issue("rotation_truncation_unmarked", "Rotation ends before goal without interrupted/planned-end/failure evidence.", event=e)
        if truncated and not vector(e.get("gimbal1_deg")):
            self.missing("interrupted_gimbal_missing", "Clipped independent-axis rotation needs actual gimbal1_deg to check computed endpoint.", event=e)
        elif "gimbal1_deg" in e:
            self.check(vector(e["gimbal1_deg"]) and angle_distance(expected, e["gimbal1_deg"]) <= EPS * rate,
                       "independent_axis_endpoint", "Recorded gimbal1_deg disagrees with independent 30 deg/s axes (each stops at its own goal).", event=e,
                       expected_gimbal1_deg=expected)
        if not truncated:
            self.check(complete, "slew_speed", "Completed rotation has insufficient time to reach goal.", event=e, minimum_s=minimum)
        e["_rotation_goal"] = list(goal)
        # Trust the independently propagated physical state, not an asserted
        # endpoint that was just disproved. Error state still propagates forward.
        return expected

    def replay(self):
        scene = self.ctx.scene
        failed = self.scenario.get("failed_vehicle")
        for vehicle, initial in scene["vehicles_xy_m"].items():
            rows = self.rows[vehicle]
            position, attitude = list(initial), [0.0, 0.0]
            end, need_reset = 0.0, False
            history_bad, history_unknown = False, False
            completed_initial_tasks = set()
            nominal_order = [job["target"] for job in sorted(self.ctx.nominal_jobs, key=lambda j: j["start_s"])
                             if any(l["vehicle"] == vehicle for l in job["legs"])]
            for e in rows:
                kind, t0, t1 = e["kind"], e["t0"], e["t1"]
                duration = t1 - t0
                issue_start = len(self.issues)
                if kind == "fail":
                    self.check(vehicle == failed and t0 == 5 and t1 == 5, "failure_marker", "Only the externally failed vehicle may fail, exactly at 5 s.", event=e)
                    continue
                self.instructions(e)
                if close(e.get("command_effective_s"), 0) and e["target"] in nominal_order:
                    predecessors = nominal_order[:nominal_order.index(e["target"])]
                    self.check(all(t in completed_initial_tasks for t in predecessors), "initial_command_order",
                               "Initial queued task executes before its preceding observation and reset completed.", event=e)
                self.check(t0 >= end - EPS, "event_overlap", "Vehicle has overlapping phases.", event=e, previous_end_s=end)
                if t0 > end + EPS:
                    self.missing("timeline_gap", "Gap in per-vehicle trace: cannot assume a stationary wait or unchanged gimbal.", event=e, previous_end_s=end)
                    self.global_evidence_gap = True
                    position, attitude = None, None
                if failed == vehicle:
                    self.check(t0 < 5 and t1 <= 5, "failed_vehicle_activity", "Failed vehicle executes a phase at/after withdrawal at 5 s.", event=e)
                if e["target"] == "T4":
                    self.check(t0 >= 5, "new_alert_before_release", "Activity for T4 precedes its release.", event=e)
                if vector(e.get("station")) and kind != "drive":
                    if position is not None:
                        self.check(dist(position, e["station"]) <= EPS, "position_discontinuity", "Station differs from actual current position.", event=e)
                    else:
                        self.missing("station_arrival_unverified", "Station asserted but preceding route is unknown.", event=e)
                        position = list(e["station"])
                if "gimbal0_deg" in e:
                    if not vector(e["gimbal0_deg"]):
                        self.missing("gimbal_initial_malformed", "Declared gimbal0_deg is not a finite [pan,tilt] pair.", event=e)
                    else:
                        if attitude is not None:
                            self.check(angle_distance(attitude, e["gimbal0_deg"]) <= EPS, "gimbal_discontinuity", "Initial gimbal angles differ from prior physical state.", event=e)
                        attitude = list(e["gimbal0_deg"])
                e["_position0"] = deepcopy(position)
                e["_attitude0"] = deepcopy(attitude)
                if kind == "drive":
                    self.check(not need_reset, "reset_before_reuse", "Vehicle drives before completing post-observation reset.", event=e)
                    if attitude is None:
                        self.missing("drive_gimbal_unverified", "Gimbal state on drive departure unknown.", event=e)
                    else:
                        self.check(angle_distance(attitude, [0, 0]) <= EPS, "drive_gimbal", "Driving requires due-east horizontal gimbal.", event=e)
                    if "gimbal_deg" in e:
                        self.check(vector(e["gimbal_deg"]) and angle_distance(e["gimbal_deg"], [0, 0]) <= EPS,
                                   "drive_gimbal", "Declared driving attitude is not due-east horizontal.", event=e)
                    path = e.get("path")
                    if self.route(path, e):
                        if position is not None:
                            self.check(dist(position, path[0]) <= EPS, "position_discontinuity", "Drive does not start from actual current position (including in-transit rerouting).", event=e)
                        else:
                            self.missing("drive_origin_unverified", "Previous position is unknown.", event=e)
                        self.check(close(path_length(path), scene["vehicle_speed_m_s"] * duration, EPS * 3),
                                   "drive_speed", "Polyline distance does not equal 3 m/s times actual duration.", event=e)
                        position = list(path[-1])
                    else:
                        position = None
                    attitude = [0.0, 0.0]
                else:
                    if kind in {"slew", "observe", "wait", "reset"} and not vector(e.get("station")):
                        self.missing("station_evidence_missing", "Task phase needs a numeric station [x,y] to cross-check reconstructed position.", event=e)
                    if position is not None:
                        self.route([position, position], e)
                    else:
                        self.missing("position_missing", "Physical station cannot be reconstructed.", event=e)
                    target = self.targets.get(e["target"])
                    desired = pointing(position, target, scene["camera_height_m"]) if target is not None and position is not None else None
                    if kind in {"slew", "reset"}:
                        if kind == "slew":
                            self.check(not need_reset, "reset_before_reuse", "A new slew starts before post-observation reset.", event=e)
                        destination = desired if kind == "slew" else [0.0, 0.0]
                        if self.record.get("rotation_model") == "independent_rate_limited_axes":
                            attitude = self.independent_rotation(e, attitude, destination)
                        else:
                            if e.get("interrupted"):
                                if not vector(e.get("gimbal1_deg")):
                                    self.missing("interrupted_gimbal_missing", "Interrupted rotation needs actual gimbal1_deg, not its planned destination.", event=e)
                                    destination = None
                                else:
                                    destination = e["gimbal1_deg"]
                            elif "gimbal1_deg" in e:
                                self.check(vector(e["gimbal1_deg"]) and destination is not None and angle_distance(e["gimbal1_deg"], destination) <= EPS,
                                           "gimbal_destination", "Completed rotation does not end at target/reset orientation.", event=e)
                            if attitude is not None and destination is not None:
                                minimum = angle_distance(attitude, destination) / scene["axis_speed_deg_s"]
                                self.check(duration >= minimum - EPS, "slew_speed", "Rotation exceeds the 30 deg/s axis speed bound.", event=e, minimum_s=minimum)
                            else:
                                self.missing("slew_unverified", "Rotation endpoints are insufficient to verify readiness/reset timing.", event=e)
                            attitude = deepcopy(destination)
                        if kind == "reset" and attitude is not None and angle_distance(attitude, [0, 0]) <= EPS:
                            if need_reset:
                                completed_initial_tasks.add(e["target"])
                            need_reset = False
                    elif kind == "observe":
                        self.check(not need_reset, "reset_before_reuse", "Observation starts before previous reset.", event=e)
                        if attitude is None or desired is None:
                            self.missing("ready_unverified", "Cannot reconstruct pointing at observation start.", event=e)
                        else:
                            self.check(angle_distance(attitude, desired) <= EPS, "not_ready", "Observation starts before gimbal is aligned with target.", event=e)
                        if target is not None and position is not None:
                            self.visible(position, target, e)
                        # Scoring is at START. A later illegal release/reset is
                        # still an audit error, but must not erase an already
                        # successful initiation (especially a pre-5 locked job).
                        start_issues = self.issues[issue_start:]
                        e["_start_bad"] = history_bad or any(x["severity"] == "error" for x in start_issues)
                        e["_start_unknown"] = history_unknown or any(x["severity"] == "missing" for x in start_issues)
                        expected_end = t0 + scene["joint_observation_s"]
                        if failed == vehicle and t0 < 5:
                            expected_end = min(expected_end, 5.0)
                        self.check(close(t1, expected_end), "observation_duration", "Observation occupancy must last 5 s, except immediate withdrawal of failed vehicle.", event=e, expected_end_s=expected_end)
                        need_reset = True
                    if kind in {"wait", "idle", "observe"} and "gimbal1_deg" in e:
                        self.check(vector(e["gimbal1_deg"]) and attitude is not None and angle_distance(attitude, e["gimbal1_deg"]) <= EPS,
                                   "unlogged_gimbal_change", "Stationary hold changes gimbal without a rotation event.", event=e)
                e["_position1"] = deepcopy(position)
                e["_attitude1"] = deepcopy(attitude)
                new_issues = self.issues[issue_start:]
                history_bad |= any(x["severity"] == "error" for x in new_issues)
                history_unknown |= any(x["severity"] == "missing" for x in new_issues)
                # A fabricated drive or impossible slew cannot be washed away by
                # a later correctly-shaped observe row. Carry causal uncertainty.
                e["_bad"], e["_unknown"] = history_bad, history_unknown
                end = max(end, t1)
            required_end = 5.0 if vehicle == failed else 60.0
            if end < required_end - EPS:
                self.global_evidence_gap = True
                self.missing("timeline_tail_missing", "Trace does not cover this vehicle through withdrawal/window end; log explicit idle/wait.", vehicle=vehicle, last_event_end_s=end, required_end_s=required_end)
            if need_reset and vehicle != failed:
                self.missing("final_reset_missing", "Healthy vehicle has no complete reset after its last observation.", vehicle=vehicle)
        markers = [e for e in self.events if e["kind"] == "fail"]
        if failed:
            self.check(len(markers) == 1 and markers[0]["vehicle"] == failed and close(markers[0]["t0"], 5),
                       "failure_marker_missing", "Expected exactly one failure event at 5 s for externally specified vehicle.")
        else:
            self.check(not markers, "unexpected_failure", "Failure logged in a scenario without failure.")

    def prefix(self):
        """Compare physical phase/route functions on [0,5), independent of splits."""
        reference = nominal_events(self.ctx)
        for v in self.ctx.scene["vehicles_xy_m"]:
            nominal = [e for e in reference if e["vehicle"] == v and e["t1"] - e["t0"] > EPS and e["t0"] < 5 - EPS]
            actual = [e for e in self.rows[v] if e["kind"] != "fail" and e["t1"] - e["t0"] > EPS and e["t0"] < 5 - EPS]
            cuts = {0.0, 5.0}
            for e in nominal + actual:
                cuts.update([max(0.0, min(5.0, e["t0"])), max(0.0, min(5.0, e["t1"]))])
                path = e.get("path")
                if e["kind"] == "drive" and isinstance(path, list) and len(path) >= 2 and all(vector(p) for p in path):
                    total, traversed = path_length(path), 0.0
                    for a, b in zip(path, path[1:]):
                        traversed += dist(a, b)
                        if total:
                            cuts.add(max(0.0, min(5.0, e["t0"] + (e["t1"] - e["t0"]) * traversed / total)))
            if self.record.get("rotation_model") == "independent_rate_limited_axes":
                # Saturation creates a kink when EACH axis first reaches goal.
                # Include all kinks: equal endpoints/midpoint alone are not a
                # general proof of equality between piecewise-linear profiles.
                for e in nominal + actual:
                    if e["kind"] not in {"slew", "reset"}:
                        continue
                    if "_index" in e:
                        initial, goal = e.get("_attitude0"), e.get("_rotation_goal")
                    else:
                        target_angles = pointing(e["station"], self.targets[e["target"]], self.ctx.scene["camera_height_m"])
                        initial, goal = ([0, 0], target_angles) if e["kind"] == "slew" else (target_angles, [0, 0])
                    if initial is not None and goal is not None:
                        for delta in (wrap(goal[0] - initial[0]), goal[1] - initial[1]):
                            stop = e["t0"] + abs(delta) / self.ctx.scene["axis_speed_deg_s"]
                            if e["t0"] <= stop <= e["t1"]:
                                cuts.add(max(0.0, min(5.0, stop)))
            cuts = sorted(cuts)
            for left, right in zip(cuts, cuts[1:]):
                if right - left <= EPS:
                    continue
                t = (left + right) / 2
                a = [e for e in actual if e["t0"] <= t < e["t1"]]
                n = [e for e in nominal if e["t0"] <= t < e["t1"]]
                if not n and not a:
                    continue
                if not a:
                    self.missing("prefix_missing", "No event covers a non-idle nominal prefix interval.", vehicle=v, interval=[left, right])
                    continue
                if len(a) != 1 or len(n) != 1 or (a[0]["kind"], a[0]["target"]) != (n[0]["kind"], n[0]["target"]):
                    self.issue("prefix_changed", "Pre-disturbance physical phases differ from nominal q22.", vehicle=v, interval=[left, right])
                    continue
                a, n = a[0], n[0]
                if a["kind"] == "drive":
                    if not isinstance(a.get("path"), list) or len(a["path"]) < 2 or not all(vector(p) for p in a["path"]):
                        self.missing("prefix_route_unverified", "Prefix driving trajectory lacks a usable path.", event=a)
                        continue
                    for time in (left, t, right):
                        ap = path_at(a["path"], (time - a["t0"]) / (a["t1"] - a["t0"]))
                        np = path_at(n["path"], (time - n["t0"]) / (n["t1"] - n["t0"]))
                        self.check(dist(ap, np) <= EPS * 3, "prefix_position", "Pre-5 trajectory differs, even if event labels match.", event=a, time_s=time)
                else:
                    pos = a.get("_position0")
                    if pos is None:
                        self.missing("prefix_station_unverified", "Prefix station cannot be reconstructed.", event=a)
                    else:
                        self.check(dist(pos, n["station"]) <= EPS, "prefix_position", "Pre-5 station differs from nominal.", event=a)
                    if a["kind"] in {"slew", "reset"}:
                        # Compare physical profiles, not phase t0 equality:
                        # splitting an ongoing rotation into telemetry segments
                        # is legal if the true state and original goal continue.
                        angle0, angle1 = a.get("_attitude0"), a.get("_attitude1")
                        original_goal = pointing(n["station"], self.targets[n["target"]], self.ctx.scene["camera_height_m"])
                        n0, n1 = ([0, 0], original_goal) if n["kind"] == "slew" else (original_goal, [0, 0])
                        model = self.record.get("rotation_model")
                        missing = angle0 is None or angle1 is None or not isinstance(model, str) or model not in ROTATION_MODELS
                        missing |= model == "independent_rate_limited_axes" and a.get("_rotation_goal") is None
                        if missing:
                            self.missing("prefix_gimbal_unverified", "Prefix rotation lacks angular endpoints/goal or declared interpolation model.", event=a)
                        else:
                            for time in (left, t, right):
                                if model == "independent_rate_limited_axes":
                                    av = independent_axes_at(angle0, a["_rotation_goal"], time - a["t0"], self.ctx.scene["axis_speed_deg_s"])
                                    nv = independent_axes_at(n0, n1, time - n["t0"], self.ctx.scene["axis_speed_deg_s"])
                                else:
                                    f = (time - a["t0"]) / (a["t1"] - a["t0"])
                                    g = (time - n["t0"]) / (n["t1"] - n["t0"])
                                    av = [angle0[0] + wrap(angle1[0] - angle0[0]) * f, angle0[1] + (angle1[1] - angle0[1]) * f]
                                    nv = [n0[0] + wrap(n1[0] - n0[0]) * g, n0[1] + (n1[1] - n0[1]) * g]
                                self.check(angle_distance(av, nv) <= EPS * 30, "prefix_rotation", "Pre-5 rotation differs from nominal under declared rotation model.", event=a, time_s=time, rotation_model=model)
        for job in self.ctx.nominal_jobs:
            if job["start_s"] >= 5:
                continue
            target, start = job["target"], job["start_s"]
            observations = [e for e in self.events if e["kind"] == "observe" and e["target"] == target]
            if len(observations) < 2:
                self.missing("locked_evidence_missing", "Locked task lacks both original observation-event records.", target=target)
            else:
                self.check(len(observations) == 2 and {e["vehicle"] for e in observations} == {l["vehicle"] for l in job["legs"]}
                           and all(close(e["t0"], start) for e in observations),
                           "locked_task_changed", "Task started before 5 s must retain original start and vehicle pair.", target=target)
            for e in observations:
                if e["vehicle"] != self.scenario.get("failed_vehicle"):
                    self.check(close(e["t1"], start + 5), "locked_healthy_occupancy", "Healthy partner must retain original observation occupancy.", event=e)

    def score(self):
        assertions = self.record.get("jobs")
        if not isinstance(assertions, dict):
            self.missing("jobs_missing", "jobs must be an object keyed by all external target IDs.")
            assertions = {}
        for target in assertions.keys() - self.targets.keys():
            self.issue("unexpected_job", "Job not present in external scenario.", target=target)
        starts, event_starts, unknown = {}, {}, []
        for target in self.targets:
            obs = [e for e in self.events if e["kind"] == "observe" and e["target"] == target]
            pair = len(obs) == 2 and len({e["vehicle"] for e in obs}) == 2 and close(obs[0]["t0"], obs[1]["t0"])
            if obs:
                self.check(pair, "joint_start", "Exactly two DIFFERENT vehicles must initiate observation at the same time (not just have overlapping intervals).", target=target)
            start = max(e["t0"] for e in obs) if pair else None
            timely = start is not None and 0 <= start <= 60
            failed_start = pair and self.scenario.get("failed_vehicle") in {e["vehicle"] for e in obs} and start >= 5
            if timely and not failed_start:
                event_starts[target] = start
            physical_bad = any(e.get("_start_bad", e.get("_bad")) for e in obs)
            physical_unknown = any(e.get("_start_unknown", e.get("_unknown")) for e in obs)
            if pair and all(e.get("_position0") is not None for e in obs):
                a, b = [e["_position0"] for e in obs]
                q = self.targets[target]
                u, v = [q[i] - a[i] for i in range(2)], [q[i] - b[i] for i in range(2)]
                denom = math.hypot(*u) * math.hypot(*v)
                valid = denom > 0 and abs(u[0] * v[1] - u[1] * v[0]) / denom > 1e-10
                if not self.check(valid, "degenerate_pair", "Bearing lines are collinear/coincident; distinct vehicle IDs alone are insufficient.", target=target):
                    physical_bad = True
            elif pair:
                physical_unknown = True
                self.missing("pair_geometry_unverified", "Missing station prevents independent distinct-bearing verification.", target=target)
            if timely and not failed_start and not physical_bad:
                if physical_unknown:
                    unknown.append(target)
                else:
                    starts[target] = start
            if not obs and (not self.event_log_present or not self.trace_declared_complete or self.global_evidence_gap):
                unknown.append(target)
                self.missing("unstarted_task_unverified", "No observation row in an incomplete trace cannot establish that this task never started.", target=target)
            state = "verified_start" if target in starts else "unverified_start" if target in unknown else "no_valid_start"
            self.evidence[target] = {"status": state, "event_start_s": start,
                                     "vehicles": sorted({e["vehicle"] for e in obs}),
                                     "observation_event_indices": [e["_index"] for e in obs]}
            job = assertions.get(target)
            if not isinstance(job, dict) or "start_s" not in job or "vehicles" not in job:
                self.missing("job_fields_missing", "Each job needs start_s (or null) and vehicles (or []).", target=target)
            else:
                claimed_start, claimed_pair = job["start_s"], job["vehicles"]
                valid_claim = (claimed_start is None or number(claimed_start)) and isinstance(claimed_pair, list) and all(isinstance(v, str) for v in claimed_pair)
                if not self.check(valid_claim, "job_schema", "Malformed job assertion.", target=target):
                    continue
                same = (claimed_start is None and start is None and claimed_pair == []) or (
                    start is not None and close(claimed_start, start) and len(claimed_pair) == 2 and set(claimed_pair) == {e["vehicle"] for e in obs})
                if not obs and target in unknown:
                    self.missing("job_start_unverified", "Claimed job cannot be checked against an absent/incomplete event log.", target=target)
                else:
                    self.check(same, "job_event_mismatch", "Claimed job start/vehicle pair disagrees with raw synchronized observe starts.", target=target,
                               claimed_start_s=claimed_start, observed_start_s=start)
        n = len(self.targets)
        def metrics(values):
            k = len(values)
            return {"N": n, "k": k, "eta": k / n if n else 0.0,
                    "t_last_s": max(values.values(), default=0.0) if k == n else 60.0}
        event_score = metrics(event_starts)
        # Even without a full physical certificate, claims must match raw starts.
        for key, expected in event_score.items():
            claimed = self.record.get(key)
            if not number(claimed):
                self.missing("metric_missing", f"Missing or nonfinite metric {key}.", metric=key)
            elif key == "N" or self.event_log_present and self.trace_declared_complete and not self.global_evidence_gap:
                if key in {"N", "k"}:
                    self.check(isinstance(claimed, int) and not isinstance(claimed, bool), "metric_integer", f"{key} must be an integer count.", metric=key)
                self.check(close(claimed, expected), "metric_mismatch", f"{key} is not independently reproduced from observe starts and external N.", metric=key, claimed=claimed, recomputed=expected)
        complete = self.record.get("trace_complete_until_s")
        if not number(complete) or complete < 60:
            self.missing("trace_completeness_missing", "Declare trace_complete_until_s >= 60; absence of a task in a partial log is not evidence of failure.")
        score = metrics(starts) if not unknown else None
        if score is not None:
            for key, expected in score.items():
                if number(self.record.get(key)):
                    self.check(close(self.record[key], expected), "verified_metric_mismatch", f"{key} disagrees with physically verified joint starts.", metric=key, recomputed=expected)
        return {"recomputed": score, "event_score": event_score,
                "score_bounds": {"k_min": len(starts), "k_max": len(starts) + len(unknown), "N": n},
                "unverified_start_targets": unknown}

    def run(self):
        if not isinstance(self.record, dict):
            self.issue("record_schema", "Result record must be an object.")
            return self.result({})
        if not self.scenario_check():
            return self.result({})
        self.targets = self.ctx.targets(self.scenario)
        model = self.record.get("rotation_model")
        if not isinstance(model, str) or model not in ROTATION_MODELS:
            self.missing("rotation_model_missing", "Declare rotation_model='linear_axes' or 'independent_rate_limited_axes'; otherwise intermediate gimbal history is unverified.")
        self.parse_events()
        self.replay()
        self.prefix()
        scoring = self.score()
        return self.result(scoring)

    def result(self, scoring):
        errors = [x for x in self.issues if x["severity"] == "error"]
        missing = [x for x in self.issues if x["severity"] == "missing"]
        status = "fail" if errors else "incomplete" if missing else "pass"
        return {"schema_version": "q4-audit/1", "scenario_id": self.record.get("scenario_id") if isinstance(self.record, dict) else None,
                "policy": self.record.get("policy") if isinstance(self.record, dict) else None,
                "status": status, "certified": status == "pass", **scoring,
                "tasks": self.evidence, "errors": errors, "missing_evidence": missing,
                "checked_counts": dict(sorted(self.checks.items())),
                "scope_exclusions": ["Hidden policy use of future information cannot be proved from a finite trace; only pre-5 physical prefix and command times are checked.",
                                     "Truth/completeness of supplied telemetry is not independently observable; trace_complete_until_s is an explicit log assertion.",
                                     "No strategy optimality, vehicle-vehicle collision model, or Q3 noise is evaluated.",
                                     "Rotation endpoint speed bounds are checked; intra-event angular motion profiles are not observed."]}


def audit_record(record: dict, scenario: dict, *, context: Context | None = None) -> dict:
    """Audit one untrusted record against an EXTERNAL scenario; no filesystem writes."""
    return Audit(record, scenario, context or Context.default()).run()


def audit_pair(left: dict, right: dict, scenario: dict, *, context: Context | None = None) -> dict:
    """ID-matched comparison. Never rank a failing/incomplete trace as a valid policy."""
    ctx = context or Context.default()
    a, b = audit_record(left, scenario, context=ctx), audit_record(right, scenario, context=ctx)
    pair_errors = []
    if left.get("rotation_model") != right.get("rotation_model") and a["certified"] and b["certified"]:
        # Both can match q22's endpoint times yet execute DIFFERENT pre-5 angular
        # histories. Compare the two declared nominal profiles, not labels only.
        targets = ctx.targets(scenario)
        for e in nominal_events(ctx):
            if e["kind"] not in {"slew", "reset"} or e["t0"] >= 5 or e["t1"] - e["t0"] <= EPS:
                continue
            pointing_deg = pointing(e["station"], targets[e["target"]], ctx.scene["camera_height_m"])
            initial, goal = ([0, 0], pointing_deg) if e["kind"] == "slew" else (pointing_deg, [0, 0])
            duration = e["t1"] - e["t0"]
            end = min(duration, 5 - e["t0"])
            times = [end / 2, end]
            times.extend(min(end, abs(delta) / ctx.scene["axis_speed_deg_s"])
                         for delta in (wrap(goal[0] - initial[0]), goal[1] - initial[1]))
            for elapsed in times:
                linear = [initial[0] + wrap(goal[0] - initial[0]) * elapsed / duration,
                          initial[1] + (goal[1] - initial[1]) * elapsed / duration]
                independent = independent_axes_at(initial, goal, elapsed, ctx.scene["axis_speed_deg_s"])
                if angle_distance(linear, independent) > EPS * 30:
                    pair_errors.append({"severity": "error", "code": "pair_prefix_rotation_model",
                                        "message": "Both traces match nominal endpoint times but their declared rotation models have different pre-5 physical histories.",
                                        "vehicle": e["vehicle"], "target": e["target"], "time_s": e["t0"] + elapsed})
                    break
    certified = a["certified"] and b["certified"] and not pair_errors
    winner = None
    if certified:
        sa, sb = a["recomputed"], b["recomputed"]
        ka, kb = (sa["eta"], -sa["t_last_s"]), (sb["eta"], -sb["t_last_s"])
        if close(sa["eta"], sb["eta"]) and close(sa["t_last_s"], sb["t_last_s"]):
            winner = "tie"
        else:
            winner = "left" if ka > kb else "right"
    prefix_codes = {"prefix_changed", "prefix_position", "prefix_rotation", "prefix_missing", "prefix_route_unverified", "prefix_station_unverified", "prefix_gimbal_unverified"}
    prefix_issues = [x for report in (a, b) for x in report["errors"] + report["missing_evidence"] if x["code"] in prefix_codes] + pair_errors
    # Both individually match the same frozen nominal plan. A shared nonnominal
    # prefix therefore does not silently pass merely because the policies agree.
    return {"schema_version": "q4-pair-audit/1", "scenario_id": scenario.get("id"),
            "status": "pass" if certified else "fail" if pair_errors or "fail" in (a["status"], b["status"]) else "incomplete",
            "pair_errors": pair_errors,
            "prefix_status": "fail" if any(x["severity"] == "error" for x in prefix_issues) else "incomplete" if prefix_issues else "pass",
            "winner": winner, "left": a, "right": b}


def read_jsonl(path):
    records, issues = {}, []
    with Path(path).open() as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                key = row.get("scenario_id") if isinstance(row, dict) else None
                if not isinstance(key, str):
                    raise ValueError("scenario_id must be a string")
                if key in records:
                    raise ValueError(f"duplicate scenario_id {key!r}")
                records[key] = row
            except (ValueError, TypeError) as error:
                issues.append({"file": str(path), "line": line_no, "code": "jsonl_input", "message": str(error)})
    return records, issues


def audit_files(left_path, scenarios, right_path=None, *, context=None):
    """External panels can reuse this matcher; input order is irrelevant."""
    left, issues = read_jsonl(left_path)
    right = None
    if right_path is not None:
        right, extra = read_jsonl(right_path)
        issues.extend(extra)
    indexed = {}
    for scenario in scenarios:
        key = scenario.get("id") if isinstance(scenario, dict) else None
        if not isinstance(key, str) or key in indexed:
            issues.append({"code": "scenario_input", "message": "Missing/duplicate external scenario id", "scenario_id": key})
        else:
            indexed[key] = scenario
    for name, records in (("left", left), ("right", right)):
        if records is None:
            continue
        for key in records.keys() - indexed.keys():
            issues.append({"code": "unknown_scenario", "side": name, "scenario_id": key})
        for key in indexed.keys() - records.keys():
            issues.append({"code": "missing_record", "side": name, "scenario_id": key})
    reports = []
    for key, scenario in indexed.items():
        if key not in left or right is not None and key not in right:
            continue
        reports.append(audit_record(left[key], scenario, context=context) if right is None else audit_pair(left[key], right[key], scenario, context=context))
    return {"schema_version": "q4-batch-audit/1", "status": "fail" if issues or any(r["status"] == "fail" for r in reports) else "incomplete" if any(r["status"] == "incomplete" for r in reports) else "pass",
            "input_issues": issues, "reports": reports}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left", type=Path, help="JSONL with one record per scenario_id (or JSON with --single)")
    parser.add_argument("--right", type=Path, help="Second policy JSONL, matched by scenario_id, never line position")
    parser.add_argument("--scenarios", required=True, type=Path, help='External {"scenarios":[...]} JSON; --single also accepts a scenario object')
    parser.add_argument("--single", action="store_true", help="Read one JSON result rather than JSONL")
    parser.add_argument("--allow-stress-recovery", action="store_true", help="Include explicitly requested stress tests with recovery after 30 s")
    parser.add_argument("--context", type=Path, help="Explicit alternate {scene,nominal_jobs}; synthetic testing only, never automatic record-provided context")
    parser.add_argument("--output", type=Path, help="Write machine-readable JSON here; default stdout")
    args = parser.parse_args(argv)
    try:
        context = Context.from_dict(json.loads(args.context.read_text())) if args.context else Context.default()
        raw = json.loads(args.scenarios.read_text())
        scenarios = raw.get("scenarios", [raw]) if isinstance(raw, dict) else raw
        if args.allow_stress_recovery:
            scenarios = [dict(s, recovery_scope="sensitivity") if number(s.get("recovery_s")) and s["recovery_s"] > 30 else s for s in scenarios]
        if args.single:
            if args.right:
                parser.error("--single cannot be combined with --right")
            record = json.loads(args.left.read_text())
            matches = [s for s in scenarios if s.get("id") == record.get("scenario_id")]
            if len(matches) != 1:
                raise ValueError("Single-record mode requires exactly one external scenario_id match")
            report = audit_record(record, matches[0], context=context)
        else:
            report = audit_files(args.left, scenarios, args.right, context=context)
        report["provenance"] = {"auditor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                                "context_sha256": hashlib.sha256(json.dumps(context.to_dict(), sort_keys=True).encode()).hexdigest(),
                                "tolerance": EPS, "allow_stress_recovery": args.allow_stress_recovery}
    except (OSError, ValueError, KeyError, TypeError) as error:
        report = {"status": "fail", "input_issues": [{"code": "input_error", "message": str(error)}]}
    text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    else:
        sys.stdout.write(text)
    return 0 if report["status"] == "pass" else 1 if report["status"] == "fail" else 2


if __name__ == "__main__":
    raise SystemExit(main())
