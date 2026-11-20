"""Synthetic-only Q3 reliability checks; no policy/model fitting or real-data I/O.

From the repository root:
    python3 -B -m unittest checks.test_c_replay_reliability -v
    python3 -B checks/test_c_replay_reliability.py -v

The constants reproduce the existing EARLY/CROSS hand calculation and A/B
synthetic ledger, but all inputs below are constructed in memory. Importing
acceptance does NOT call pair_trials(), validation_trial(), smoke(), or loaders.
The only files read by a test are temporary synthetic JSON exports.

Three distinct oracles are used: literal hand answers, the existing verifier,
and narrow public-interface/admission invariants plus an inside-callback witness.
Fault injections edit deep copies of outputs and synthetic source rows, never
patch the engine. These checks are not a second simulator, a new strategy, or
evidence about official scores.
"""
from copy import deepcopy
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

# Allow direct invocation without a permanent old-package import fallback.
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiments.q3_replay.acceptance import packed, verify_export, verify_ledger
from experiments.q3_replay.engine import Source, Trial, fixed_actions, run_trial

if not __debug__:
    raise RuntimeError("Run without -O: the existing acceptance verifier uses assert.")


PUBLIC_KEYS = {
    "time", "zone", "budget_left", "reviews_left", "case", "rules", "zones",
    "jobs", "alpha", "beta", "completed", "observations",
}
ROW_KEYS = {
    "timestamp_s", "left_hand", "right_hand", "head_pose",
    "left_head_camera_pose", "right_head_camera_pose", "hand_quality_flags",
}
HIDDEN_KEYS = {
    "source_ref", "source_ref_offline", "source_id", "duration", "d_s", "p_s",
    "d", "p", "seed", "ledger", "actual_end", "available_at",
    "annotation_start_s", "annotation_end_s", "semantic_description",
}
STOP = {"action": "STOP"}
EARLY_ACTIONS = [
    {"action": "REVIEW", "job_id": "J"},
    {"action": "EXECUTE", "job_id": "J", "b_s": 5}, STOP,
]
CROSS_ACTIONS = [
    {"action": "WAIT", "seconds": 94},
    {"action": "REVIEW", "job_id": "J"},
    {"action": "EXECUTE", "job_id": "J", "b_s": 3},
]
PAIR_ACTIONS = [
    {"action": "EXECUTE", "job_id": "A", "b_s": 4},
    {"action": "EXECUTE", "job_id": "B", "b_s": 2}, STOP,
]
EARLY_METRICS = dict(
    weighted_completion_rate=1, weighted_on_time_rate=1, weighted_tardiness_s=0,
    elapsed_s=7.5, cost=3.7, observations=1, observed_overrun_s=0,
    reserved_idle_s=2.5, censored_units=0, invalid_actions=0, valid_protocol=True,
)
CROSS_METRICS = dict(
    weighted_completion_rate=0, weighted_on_time_rate=0, weighted_tardiness_s=6,
    elapsed_s=100, cost=3.5, observations=1, observed_overrun_s=0.5,
    reserved_idle_s=0, censored_units=1, invalid_actions=0, valid_protocol=True,
)


def job(name, release=0, due=6, weight=3, zone="Z1", predecessors=()):
    return dict(job_id=name, family_id="SYNTHETIC", release_s=release,
                due_s=due, priority_weight=weight, zone_id=zone,
                predecessors=list(predecessors))


def synthetic_rows(marker):
    """Distinct jobs and samples, including either side of each prefix boundary."""
    rows = []
    for i, stamp in enumerate((-0.1, 0, 0.1, 0.2, 0.200001, 0.4, 0.6,
                               0.600001, 0.8, 1, 1.000001)):
        pose = dict(position=[marker + i, 0, 0], orientation_xyzw=[0, 0, 0, 1])
        hand = dict(positions=[[marker + i, k, 0] for k in range(21)],
                    orientations_xyzw=[[0, 0, 0, 1] for _ in range(21)])
        rows.append(dict(timestamp_s=stamp, left_hand=deepcopy(hand),
                         right_hand=deepcopy(hand), head_pose=deepcopy(pose),
                         left_head_camera_pose=deepcopy(pose),
                         right_head_camera_pose=deepcopy(pose),
                         hand_quality_flags={"is_bad": False, "reasons": []}))
    return rows


def synthetic_trial(jobs=None, durations=None, *, horizon=100, budget=32, reviews=4):
    jobs = [job("J")] if jobs is None else jobs
    durations = {} if durations is None else durations
    rules = dict(
        initial_zone="Z0", max_actions=200,
        movement=dict(speed=1, changeover_s=0.5),
        fees=dict(service_fixed=2.0, reserve_per_second=0.1,
                  distance_per_unit=0.2, wait=0.0),
        observation=dict(prefix_seconds=[0.2, 0.6, 1.0], delay_s=1,
                         cost=1, max_per_job=2),
        service=dict(reserve_min_s=0.2),
    )
    return Trial(
        jobs={j["job_id"]: deepcopy(j) for j in jobs},
        sources={j["job_id"]: Source("synthetic://reliability/" + j["job_id"],
                                    durations.get(j["job_id"], 2),
                                    synthetic_rows(1000 * (i + 1)))
                 for i, j in enumerate(jobs)},
        zones={"Z0": [0, 0], "Z1": [1, 0], "Z2": [3, 4]},
        case=dict(case_id="SYNTHETIC", jobs=len(jobs), horizon_s=horizon,
                  cost_budget=budget, observation_limit=reviews),
        rules=rules,
    )


def early_trial():
    return synthetic_trial()


def cross_trial():
    return synthetic_trial([job("J", release=94, due=98)], {"J": 6})


def pair_trial(long=False):
    return synthetic_trial([job("A", due=20, weight=1), job("B", due=8)],
                           {"A": 5.5 if long else 2.5, "B": 1.5})


def need(condition, label):
    if not condition:
        raise AssertionError(label)


def close(actual, expected, label):
    need(math.isfinite(actual) and math.isclose(actual, expected, rel_tol=0,
                                               abs_tol=1e-9),
         f"{label}: {actual!r} != {expected!r}")


def all_keys(value):
    if isinstance(value, dict):
        return set(value).union(*(all_keys(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(all_keys(v) for v in value))
    return set()


def check_metrics(log, expected):
    need(set(log["metrics"]) == set(expected), "metric schema")
    for name, value in expected.items():
        close(log["metrics"][name], value, "metric " + name)


def check_public_contract(log, trial):
    """Supplement the existing verifier, not another time/score implementation.

    Check exact callback payloads against known synthetic permitted samples,
    resource/admission bounds, release alignment and offline-reference timing.
    No private truth is made available to the callable under test.
    """
    H, B, K = (trial.case[k] for k in ("horizon_s", "cost_budget", "observation_limit"))
    calls, ledger = log["calls"], log["ledger"]
    rejected = [c for c in calls if "rejected" in c]
    need(len(rejected) <= 1, "at most one terminal rejection")
    need(len(calls) == len(ledger) + len(rejected), "call/ledger alignment")
    if rejected:
        need(rejected == calls[-1:], "rejection must be terminal")
    widths = trial.rules["observation"]["prefix_seconds"]
    public_rules = {k: trial.rules[k] for k in ("fees", "movement", "observation", "max_actions")}
    public_rules["reserve_min_s"] = trial.rules["service"]["reserve_min_s"]
    for i, call in enumerate(calls):
        view = call["input"]
        need(call["index"] == i, "callback index alignment")
        need(set(view) == PUBLIC_KEYS, "public-view schema")
        need(not (all_keys(view) & HIDDEN_KEYS), "hidden field in public view")
        for name, value in (("case", trial.case), ("jobs", trial.jobs),
                            ("zones", trial.zones), ("rules", public_rules),
                            ("alpha", trial.alpha), ("beta", trial.beta)):
            need(view[name] == value, "public conditions changed: " + name)
        need(0 <= view["time"] < H and call["time"] == view["time"], "callback horizon/time")
        need(-1e-9 <= view["budget_left"] <= B, "callback budget bounds")
        need(0 <= view["reviews_left"] <= K, "callback review bounds")
        prior = ledger[:i]
        expected = {}
        for name, spec in trial.jobs.items():
            if spec["release_s"] <= view["time"]:
                level = sum(e["action"] == "REVIEW" and e["job_id"] == name for e in prior)
                width = widths[level]
                expected[name] = dict(prefix_s=width, rows=[
                    r for r in trial.sources[name].rows if 0 <= r["timestamp_s"] <= width
                ])
        need(view["observations"] == expected, "released job/prefix/sample alignment")
        for observation in view["observations"].values():
            for row in observation["rows"]:
                need(set(row) == ROW_KEYS, "sample allowlist")
        finished = {e["job_id"]: e["completion"] for e in prior
                    if e["action"] == "EXECUTE" and e["completion"] is not None}
        need(view["completed"] == finished, "premature/misaligned completion feedback")
        if i == len(ledger):  # Rejected actions spend nothing and have no accepted row.
            continue
        event = ledger[i]
        need(call["action"]["action"] == event["action"], "action/ledger alignment")
        need(0 <= event["time"] <= event["end"] <= H, "equipment horizon bounds")
        need(-1e-9 <= event["cost_after"] <= B + 1e-9, "accepted cost exceeds budget")
        if event["action"] in ("REVIEW", "EXECUTE"):
            name = event["job_id"]
            need(name in expected and name not in finished, "accepted unavailable job")
        if event["action"] == "REVIEW":
            need(view["reviews_left"] > 0, "accepted review with no quota")
            need(event["prefix_returned_s"] == widths[
                sum(e["action"] == "REVIEW" and e["job_id"] == name for e in prior) + 1
            ], "review prefix must be cumulative")
        elif event["action"] == "EXECUTE":
            need(all(p in finished for p in trial.jobs[name]["predecessors"]), "predecessor admission")
            need(event["b"] >= trial.rules["service"]["reserve_min_s"], "minimum reservation")
            need(event["start"] + event["b"] <= H, "planned horizon admission")
            completion = event["completion"]
            need(completion is None or event["start"] <= completion <= H, "completion horizon bounds")
            need(event["source_ref_offline"] == trial.sources[name].ref, "offline source/job alignment")
            need(event["source_ref_logged_at"] == (H if completion is None else completion),
                 "offline reference timing")
    expected_releases = [dict(job_id=name, time=spec["release_s"], prefix_s=widths[0])
                         for name, spec in sorted(trial.jobs.items(),
                                                  key=lambda item: (item[1]["release_s"], item[0]))
                         if spec["release_s"] <= log["metrics"]["elapsed_s"]]
    need(log["releases"] == expected_releases, "release ledger alignment")


def check_hand(log, trial, cross=False):
    verify_ledger(log)
    check_public_contract(log, trial)
    check_metrics(log, CROSS_METRICS if cross else EARLY_METRICS)
    need([c["time"] for c in log["calls"]] == ([0, 94, 95] if cross else [0, 1, 7.5]),
         "hand callback times (not completion times)")
    e = log["ledger"][2 if cross else 1]
    expected = (dict(time=95, start=96.5, b=3, completion=None, end=100,
                     fee=2.5, cost_after=3.5, idle=0, overrun=0.5, censored=True,
                     source_ref_logged_at=100) if cross else
                dict(time=1, start=2.5, b=5, completion=5, end=7.5,
                     fee=2.7, cost_after=3.7, idle=2.5, overrun=0, censored=False,
                     source_ref_logged_at=5))
    for field, value in expected.items():
        if value is None:
            need(e[field] is None, "censored service is not completed")
        else:
            close(e[field], value, "hand execution " + field)


def observe(trial, actions, mutate=None):
    """Instrument a fixed script, not a scheduling policy or prediction model."""
    original, received = deepcopy(trial), []
    script = fixed_actions(actions)

    def callback(view):
        entry = dict(index=len(received), time=view["time"], input=deepcopy(view))
        if mutate is not None:
            mutate(view)
        action = script(view)
        entry["action"] = deepcopy(action)
        received.append(entry)
        return action

    log = run_trial(trial, callback)
    need(trial == original, "input objects mutated by replay/callback")
    logged = [{k: c[k] for k in ("index", "time", "input", "action")} for c in log["calls"]]
    need(logged == received, "engine log differs from inside-callback witness")
    check_public_contract(log, original)
    verify_ledger(log)  # Includes correctly recorded terminal rejections.
    return log


def replace_at(document, path, value):
    target = document
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = deepcopy(value)


class HandAndBoundaryTests(unittest.TestCase):
    def test_early_hand_completion_is_not_availability(self):
        trial = early_trial()
        log = observe(trial, EARLY_ACTIONS)
        check_hand(log, trial)
        self.assertEqual(log["calls"][-1]["input"]["completed"], {"J": 5})
        self.assertNotIn(5, [c["time"] for c in log["calls"]])

    def test_cross_H_hand_censoring_no_refund_no_hidden_feasibility(self):
        trial = cross_trial()
        log = observe(trial, CROSS_ACTIONS)
        check_hand(log, trial, cross=True)
        self.assertEqual(len(log["ledger"]), 3)  # Automatic H termination, no STOP callback.
        self.assertIsNone(log["ledger"][-1]["completion"])

    def test_existing_two_job_hand_answers_and_shared_resources(self):
        for long, times, complete_a, complete_b, idle, overrun, on_time, tardy in (
            (False, [0, 5.5, 8], 4.5, 8, 1, 0, 1, 0),
            (True, [0, 7.5, 10], 7.5, 10, 0, 2, 0.25, 6),
        ):
            with self.subTest(long=long):
                log = observe(pair_trial(long), PAIR_ACTIONS)
                a, b, stop = log["ledger"]
                self.assertEqual([c["time"] for c in log["calls"]], times)
                self.assertEqual((a["start"], a["completion"], b["start"], b["completion"]),
                                 (1.5, complete_a, times[1] + 0.5, complete_b))
                self.assertEqual((b["zone_before"], stop["zone_after"]), ("Z1", "Z1"))
                check_metrics(log, dict(EARLY_METRICS, elapsed_s=times[-1], cost=4.8,
                                       observations=0, reserved_idle_s=idle,
                                       observed_overrun_s=overrun,
                                       weighted_on_time_rate=on_time, weighted_tardiness_s=tardy))

    def test_completion_on_H_is_complete_but_just_after_H_is_censored(self):
        for duration, completion, censored, tardy in ((2, 4, 0, 0),
                                                      (2.000001, None, 1, 0)):
            with self.subTest(duration=duration):
                trial = synthetic_trial([job("J", due=4)], {"J": duration}, horizon=4)
                log = observe(trial, [{"action": "EXECUTE", "job_id": "J", "b_s": 2.5}])
                self.assertEqual(log["ledger"][0]["completion"], completion)
                self.assertEqual(log["metrics"]["censored_units"], censored)
                self.assertEqual(log["metrics"]["weighted_on_time_rate"], 1 - censored)
                self.assertEqual(log["metrics"]["weighted_tardiness_s"], tardy)
                self.assertEqual(log["metrics"]["elapsed_s"], 4)

    def test_exact_planned_horizon_and_budget_are_admitted(self):
        trial = synthetic_trial(horizon=4.5, budget=2.5)
        log = observe(trial, [{"action": "EXECUTE", "job_id": "J", "b_s": 3}])
        self.assertEqual(log["ledger"][0]["start"] + log["ledger"][0]["b"], 4.5)
        self.assertEqual(log["metrics"]["cost"], 2.5)
        self.assertTrue(log["metrics"]["valid_protocol"])

    def test_euclidean_distance_and_same_zone_changeover(self):
        trial = synthetic_trial([job("J", zone="Z2")])
        log = observe(trial, [{"action": "EXECUTE", "job_id": "J", "b_s": 3}, STOP])
        self.assertEqual(log["ledger"][0]["start"], 5.5)  # 3-4-5 triangle, not Manhattan 7.
        self.assertAlmostEqual(log["metrics"]["cost"], 3.3)
        pair = observe(pair_trial(), PAIR_ACTIONS)
        self.assertEqual(pair["ledger"][1]["start"] - pair["ledger"][1]["time"], 0.5)

    def test_verifier_uses_public_fee_speed_and_review_rules_not_constants(self):
        trial = early_trial()
        trial.rules["observation"].update(delay_s=2, cost=0.5)
        trial.rules["movement"].update(speed=2, changeover_s=0.25)
        trial.rules["fees"].update(service_fixed=1, reserve_per_second=0.25, distance_per_unit=0.5)
        log = observe(trial, EARLY_ACTIONS)
        self.assertEqual([c["time"] for c in log["calls"]], [0, 2, 7.75])
        self.assertEqual(log["ledger"][1]["start"], 2.75)
        self.assertEqual(log["ledger"][1]["completion"], 5.25)
        self.assertEqual(log["metrics"]["cost"], 3.25)

    def test_STOP_does_not_run_to_H_but_unfinished_tardiness_uses_H(self):
        trial = synthetic_trial([job("J"), job("L", release=90, due=99, weight=1)])
        log = observe(trial, EARLY_ACTIONS)
        check_metrics(log, dict(EARLY_METRICS, weighted_completion_rate=0.75,
                                weighted_on_time_rate=0.75, weighted_tardiness_s=1))
        self.assertNotIn("L", log["calls"][-1]["input"]["observations"])

    def test_release_during_occupied_interval_not_at_next_dispatch(self):
        trial = synthetic_trial([job("A"), job("B", release=2, predecessors=["A"])])
        log = observe(trial, [{"action": "EXECUTE", "job_id": "A", "b_s": 5}, STOP])
        self.assertEqual(log["releases"][1]["time"], 2)
        self.assertEqual(log["calls"][1]["time"], 6.5)
        self.assertEqual(log["calls"][1]["input"]["completed"], {"A": 4})


class AdmissionAndIsolationTests(unittest.TestCase):
    def assert_rejected(self, trial, actions, reason, cost=0, accepted=0):
        log = observe(trial, actions + [STOP])
        self.assertFalse(log["metrics"]["valid_protocol"])
        self.assertEqual(log["metrics"]["invalid_actions"], 1)
        self.assertAlmostEqual(log["metrics"]["cost"], cost)
        self.assertEqual(len(log["ledger"]), accepted)
        self.assertEqual(len(log["calls"]), accepted + 1)
        self.assertEqual(log["calls"][-1]["rejected"], reason)
        return log

    def test_invalid_numeric_and_action_shapes_are_terminal_and_free(self):
        actions = [None, [], {"action": "BOGUS"}]
        actions += [{"action": "WAIT", "seconds": x}
                    for x in (0, -1, 101, True, "1", float("nan"), float("inf"))]
        actions += [{"action": "EXECUTE", "job_id": "J", "b_s": x}
                    for x in (0.199999, -1, True, "2", float("nan"), float("inf"))]
        for action in actions:
            with self.subTest(action=action):
                log = run_trial(early_trial(), fixed_actions([action, STOP]))
                self.assertEqual(log["ledger"], [])
                self.assertEqual(len(log["calls"]), 1)
                self.assertEqual(log["metrics"]["invalid_actions"], 1)
                self.assertEqual(log["metrics"]["cost"], 0)
                verify_ledger(log)

    def test_unreleased_unknown_and_predecessor_blocked_jobs(self):
        execute = lambda name: {"action": "EXECUTE", "job_id": name, "b_s": 3}
        self.assert_rejected(synthetic_trial([job("J", release=2)]), [execute("J")], "job unavailable")
        self.assert_rejected(early_trial(), [execute("MISSING")], "job unavailable")
        trial = synthetic_trial([job("A"), job("B", predecessors=["A"])])
        self.assert_rejected(trial, [execute("B")], "predecessors")
        # REVIEW before predecessor completion is legal; EXECUTE still is not.
        log = self.assert_rejected(trial, [{"action": "REVIEW", "job_id": "B"}, execute("B")],
                                   "predecessors", cost=1, accepted=1)
        self.assertEqual(log["calls"][-1]["input"]["observations"]["B"]["prefix_s"], 0.6)

    def test_completed_job_cannot_be_executed_or_reviewed_twice(self):
        first = {"action": "EXECUTE", "job_id": "J", "b_s": 3}
        for again in (first, {"action": "REVIEW", "job_id": "J"}):
            with self.subTest(action=again):
                self.assert_rejected(early_trial(), [first, again], "job unavailable", cost=2.5, accepted=1)

    def test_budget_and_horizon_shortfall_do_not_spend_or_consult_hidden_duration(self):
        action = {"action": "EXECUTE", "job_id": "J", "b_s": 3}
        for trial in (synthetic_trial(budget=2.499999), synthetic_trial(horizon=4.499999)):
            with self.subTest(case=trial.case):
                # Invalid admission must not use or perform arithmetic with duration.
                trial.sources["J"].duration = None
                self.assert_rejected(trial, [action], "reservation, horizon or budget")
        self.assert_rejected(synthetic_trial(budget=3.499999),
                             [{"action": "REVIEW", "job_id": "J"}, action],
                             "reservation, horizon or budget", cost=1, accepted=1)

    def test_review_limits_budget_and_horizon(self):
        review = {"action": "REVIEW", "job_id": "J"}
        reason = "review limit, budget or horizon"
        self.assert_rejected(synthetic_trial(reviews=1), [review, review], reason, cost=1, accepted=1)
        self.assert_rejected(early_trial(), [review] * 3, reason, cost=2, accepted=2)
        self.assert_rejected(synthetic_trial(budget=0.999999), [review], reason)
        self.assert_rejected(synthetic_trial(horizon=0.999999), [review], reason)
        exact = observe(synthetic_trial(horizon=1, budget=1, reviews=1), [review])
        self.assertEqual(exact["metrics"]["observations"], 1)
        self.assertEqual(exact["metrics"]["elapsed_s"], 1)

    def test_action_limit_and_wait_at_H(self):
        trial = early_trial()
        trial.rules["max_actions"] = 1
        self.assert_rejected(trial, [{"action": "WAIT", "seconds": 1}, STOP],
                             "action limit", accepted=1)
        log = observe(early_trial(), [{"action": "WAIT", "seconds": 100}])
        self.assertEqual(len(log["calls"]), 1)
        self.assertEqual(log["metrics"]["cost"], 0)

    def test_callback_mutation_is_detached_and_trials_reset(self):
        trial = early_trial()
        reference = observe(trial, EARLY_ACTIONS)

        def vandalize(view):
            view["case"]["horizon_s"] = 0
            view["rules"]["fees"]["service_fixed"] = 999
            view["zones"]["Z1"][0] = 999
            view["jobs"]["J"]["predecessors"].append("MISSING")
            view["observations"]["J"]["rows"][0]["head_pose"]["position"][0] = -999
            view["completed"]["J"] = -1
            view["budget_left"] = -1
            view.clear()

        self.assertEqual(observe(trial, EARLY_ACTIONS, vandalize), reference)
        self.assertEqual(observe(trial, EARLY_ACTIONS), reference)


class PublicViewAndMetamorphicTests(unittest.TestCase):
    def test_prefix_is_cumulative_closed_source_window_not_elapsed_time(self):
        trial = early_trial()
        trial.alpha = 0.5  # Does not rescale the source-second observation windows.
        log = observe(trial, [{"action": "WAIT", "seconds": 10},
                              {"action": "REVIEW", "job_id": "J"},
                              {"action": "REVIEW", "job_id": "J"}, STOP])
        windows = [c["input"]["observations"]["J"] for c in log["calls"]]
        self.assertEqual([o["prefix_s"] for o in windows], [0.2, 0.2, 0.6, 1])
        self.assertEqual([[r["timestamp_s"] for r in o["rows"]] for o in windows], [
            [0, 0.1, 0.2], [0, 0.1, 0.2], [0, 0.1, 0.2, 0.200001, 0.4, 0.6],
            [0, 0.1, 0.2, 0.200001, 0.4, 0.6, 0.600001, 0.8, 1],
        ])
        self.assertEqual([c["time"] for c in log["calls"]], [0, 10, 11, 12])

    def test_unobserved_duration_reference_and_suffix_are_noninterfering(self):
        first = synthetic_trial([job("A"), job("B", release=2)])
        second = deepcopy(first)
        for name, source in second.sources.items():
            source.duration = 999
            source.ref = "synthetic://SECRET-CHANGED/" + name
            for row in source.rows:
                if row["timestamp_s"] > (0.6 if name == "A" else 0.2):
                    row["head_pose"]["position"] = [-999, -999, -999]
        actions = [{"action": "WAIT", "seconds": 2},
                   {"action": "REVIEW", "job_id": "A"},
                   {"action": "WAIT", "seconds": 10}, STOP]
        self.assertEqual(observe(first, actions), observe(second, actions))

    def test_prefix_difference_first_appears_after_review_returns(self):
        first, second = early_trial(), early_trial()
        next(r for r in second.sources["J"].rows if r["timestamp_s"] == 0.6)["head_pose"]["position"][0] = -9
        actions = [{"action": "REVIEW", "job_id": "J"}, STOP]
        a, b = observe(first, actions), observe(second, actions)
        self.assertEqual(a["calls"][0], b["calls"][0])
        self.assertNotEqual(a["calls"][1]["input"]["observations"], b["calls"][1]["input"]["observations"])
        self.assertEqual([c["time"] for c in a["calls"]], [0, 1])

    def test_hidden_service_time_can_change_only_later_dispatch_feedback(self):
        short, long = observe(pair_trial(), PAIR_ACTIONS), observe(pair_trial(True), PAIR_ACTIONS)
        self.assertEqual(short["calls"][0], long["calls"][0])
        self.assertEqual([c for c in short["calls"] if c["time"] < 5.5],
                         [c for c in long["calls"] if c["time"] < 5.5])
        self.assertEqual([c["time"] for c in short["calls"]], [0, 5.5, 8])
        self.assertEqual([c["time"] for c in long["calls"]], [0, 7.5, 10])

    def test_unseen_remaining_service_beyond_H_does_not_change_censored_log(self):
        short, long = cross_trial(), cross_trial()
        long.sources["J"].duration = 999
        self.assertEqual(observe(short, CROSS_ACTIONS), observe(long, CROSS_ACTIONS))

    def test_larger_reservation_changes_fee_and_idle_not_physical_completion(self):
        results = [observe(early_trial(), [{"action": "EXECUTE", "job_id": "J", "b_s": b}, STOP])
                   for b in (2, 5)]
        small, large = [r["ledger"][0] for r in results]
        self.assertEqual((small["start"], small["completion"]), (1.5, 4))
        self.assertEqual((large["start"], large["completion"]), (1.5, 4))
        self.assertEqual((small["end"], large["end"]), (4, 6.5))
        self.assertEqual((small["overrun"], large["idle"]), (0.5, 2.5))
        self.assertAlmostEqual(large["fee"] - small["fee"], 0.3)

    def test_clock_translation_preserves_cost_scores_and_source_timestamps(self):
        original = observe(early_trial(), EARLY_ACTIONS)
        shifted = synthetic_trial([job("J", release=10, due=16)], horizon=110)
        moved = observe(shifted, [{"action": "WAIT", "seconds": 10}] + EARLY_ACTIONS)
        check_metrics(moved, dict(EARLY_METRICS, elapsed_s=17.5))
        for before, after in zip(original["calls"], moved["calls"][1:]):
            self.assertEqual(after["time"] - before["time"], 10)
            self.assertEqual(before["input"]["observations"], after["input"]["observations"])

    def test_alpha_duration_compensation_preserves_physical_ledger(self):
        first, second = early_trial(), early_trial()
        second.sources["J"].duration = 4
        second.alpha = 0.5
        a, b = observe(first, EARLY_ACTIONS), observe(second, EARLY_ACTIONS)
        self.assertEqual(a["ledger"], b["ledger"])
        self.assertEqual(a["metrics"], b["metrics"])

    def test_job_insertion_order_does_not_change_simulation(self):
        trial = pair_trial()
        reverse = deepcopy(trial)
        reverse.jobs = dict(reversed(list(reverse.jobs.items())))
        reverse.sources = dict(reversed(list(reverse.sources.items())))
        self.assertEqual(observe(trial, PAIR_ACTIONS), observe(reverse, PAIR_ACTIONS))


class SourceProjectionTests(unittest.TestCase):
    def test_extra_source_metadata_at_every_object_level_is_removed(self):
        clean = early_trial()
        for row in clean.sources["J"].rows:
            row["hand_quality_flags"] = {
                "left": {"occlusion": False}, "right": {"near_edge": True},
                "is_bad": False, "reasons": [],
                "left_bad_reason": "occlusion", "right_bad_reason": None,
            }
        polluted = deepcopy(clean)
        for row in polluted.sources["J"].rows:
            row.update(source_ref="SECRET", future_duration=999, unknown_channel={"future": 123})
            for name in ("left_hand", "right_hand", "head_pose", "left_head_camera_pose", "right_head_camera_pose"):
                row[name].update(source_ref="SECRET", future_endpoint=999)
            row["hand_quality_flags"].update(whole_bad_fraction=0.9, future_frames=[999])
            row["hand_quality_flags"]["left"]["source_ref"] = "SECRET"
            row["hand_quality_flags"]["right"]["future_duration"] = 999
        before, actual = deepcopy(polluted), []
        script = fixed_actions(EARLY_ACTIONS)

        def callback(view):
            actual.append(deepcopy(view))
            return script(view)

        result = run_trial(polluted, callback)
        expected = observe(clean, EARLY_ACTIONS)
        self.assertEqual(result, expected)
        self.assertEqual(actual, [c["input"] for c in expected["calls"]])
        self.assertEqual(polluted, before)
        verify_ledger(result)

    def test_official_style_nullable_sparse_and_legacy_samples_are_preserved(self):
        row = synthetic_rows(1)[1]
        row["left_hand"]["positions"][0][0] = None
        row["head_pose"]["orientation_xyzw"][0] = None
        for flags in (None, {"is_bad": True, "reasons": ["left:occlusion"]},
                      {"left_bad_reason": ["occlusion"], "right_bad_reason": None},
                      {"left": {"occlusion": True}, "right": {"near_edge": False}}):
            with self.subTest(flags=flags):
                trial = early_trial()
                trial.sources["J"].rows = [dict(deepcopy(row), hand_quality_flags=flags),
                                            {"timestamp_s": 0.1}]
                result = run_trial(trial, fixed_actions([STOP]))
                self.assertEqual(result["calls"][0]["input"]["observations"]["J"]["rows"],
                                 trial.sources["J"].rows)
                verify_ledger(result)

    def test_metadata_cannot_hide_in_numeric_or_quality_leaves(self):
        faults = [
            (("head_pose", "position", 0), {"source_ref": "SECRET"}),
            (("left_hand", "positions", 0, 0), "SECRET"),
            (("right_hand", "orientations_xyzw", 0, 0), float("nan")),
            (("hand_quality_flags", "reasons"), [{"source_ref": "SECRET"}]),
            (("hand_quality_flags", "is_bad"), {"future": 1}),
        ]
        for path, value in faults:
            with self.subTest(path=path):
                trial = early_trial()
                replace_at(trial.sources["J"].rows[1], path, value)
                received = []
                with self.assertRaises(ValueError):
                    run_trial(trial, lambda view: received.append(view) or STOP)
                self.assertEqual(received, [])
        # Malformed hidden suffix is not projected until it becomes observable.
        trial = early_trial()
        trial.sources["J"].rows[-1]["head_pose"]["position"] = {"secret": 1}
        self.assertEqual(run_trial(trial, fixed_actions([STOP])),
                         run_trial(early_trial(), fixed_actions([STOP])))


class FaultInjectionTests(unittest.TestCase):
    def test_existing_verifier_kills_arithmetic_and_alignment_mutations(self):
        good = observe(early_trial(), EARLY_ACTIONS)
        faults = [
            ("completion confused with availability", ("ledger", 1, "completion"), 7.5),
            ("equipment released at completion", ("ledger", 1, "end"), 5),
            ("changeover omitted", ("ledger", 1, "start"), 2),
            ("reservation not charged", ("ledger", 1, "fee"), 2.2),
            ("cumulative cost reset", ("ledger", 1, "cost_after"), 2.7),
            ("callback shifted", ("calls", 2, "time"), 5),
            ("callback budget stale", ("calls", 2, "input", "budget_left"), 31),
            ("job identity mismatch", ("ledger", 1, "job_id"), "OTHER"),
            ("on-time score forged", ("metrics", "weighted_on_time_rate"), 0),
            ("reserved idle omitted", ("metrics", "reserved_idle_s"), 0),
        ]
        for name, path, value in faults:
            with self.subTest(fault=name):
                bad = deepcopy(good)
                replace_at(bad, path, value)
                with self.assertRaises(AssertionError):
                    verify_ledger(bad)

    def test_existing_verifier_kills_missing_duplicate_reordered_and_spliced_rows(self):
        good = observe(pair_trial(), PAIR_ACTIONS)
        other = observe(pair_trial(True), PAIR_ACTIONS)
        variants = {}
        variants["missing row"] = good["ledger"][:-1]
        variants["duplicated row"] = good["ledger"] + [good["ledger"][-1]]
        variants["reordered rows"] = list(reversed(good["ledger"]))
        variants["other trial's feedback"] = [other["ledger"][0]] + good["ledger"][1:]
        for name, ledger in variants.items():
            with self.subTest(fault=name):
                bad = deepcopy(good)
                bad["ledger"] = deepcopy(ledger)
                with self.assertRaises(AssertionError):
                    verify_ledger(bad)

    def test_hand_oracle_kills_coherent_but_wrong_completion(self):
        trial = early_trial()
        good = observe(trial, EARLY_ACTIONS)
        bad = deepcopy(good)
        # Internally self-consistent, but contradicts synthetic d=2 => p=2.5.
        bad["ledger"][1].update(completion=5.25, idle=2.25, source_ref_logged_at=5.25)
        bad["calls"][2]["input"]["completed"]["J"] = 5.25
        bad["metrics"]["reserved_idle_s"] = 2.25
        with self.assertRaises(AssertionError):
            check_hand(bad, trial)

    def test_cross_H_faults_are_killed(self):
        trial = cross_trial()
        good = observe(trial, CROSS_ACTIONS)
        faults = [
            ("latent overrun instead of observed", ("ledger", 2, "overrun"), 3.5),
            ("reserve exhaustion frees device", ("ledger", 2, "end"), 99.5),
            ("censored completion forged", ("ledger", 2, "completion"), 100),
            ("H censor flag lost", ("ledger", 2, "censored"), False),
            ("partial-service refund", ("ledger", 2, "fee"), 0),
            ("unfinished tardiness omitted", ("metrics", "weighted_tardiness_s"), 0),
        ]
        for name, path, value in faults:
            with self.subTest(fault=name):
                bad = deepcopy(good)
                replace_at(bad, path, value)
                with self.assertRaises(AssertionError):
                    check_hand(bad, trial, cross=True)

    def test_supplementary_guard_kills_self_consistent_service_beyond_H(self):
        trial = cross_trial()
        bad = observe(trial, CROSS_ACTIONS)
        bad["ledger"][2].update(completion=103, end=103, censored=False,
                                overrun=3.5, source_ref_logged_at=103)
        bad["metrics"].update(elapsed_s=103, weighted_completion_rate=1,
                              weighted_tardiness_s=15, censored_units=0,
                              observed_overrun_s=3.5)
        with self.assertRaisesRegex(AssertionError, "horizon"):
            check_public_contract(bad, trial)
        with self.assertRaises(AssertionError):
            verify_ledger(bad)

    def test_supplementary_guard_kills_consistent_overspending(self):
        trial = early_trial()
        bad = observe(trial, EARLY_ACTIONS)
        trial.case["cost_budget"] = 3  # Claimed budget smaller than 3.7 accepted cost.
        for call in bad["calls"]:
            call["input"]["case"]["cost_budget"] = 3
            call["input"]["budget_left"] -= 29
        with self.assertRaisesRegex(AssertionError, "budget"):
            check_public_contract(bad, trial)
        with self.assertRaises(AssertionError):
            verify_ledger(bad)

    def test_public_contract_kills_hidden_future_and_release_misalignment(self):
        trial = synthetic_trial([job("A"), job("B", release=2)])
        good = observe(trial, [{"action": "WAIT", "seconds": 2},
                               {"action": "REVIEW", "job_id": "A"}, STOP])
        faults = [
            ("future suffix row", ("calls", 0, "input", "observations", "A", "rows"),
             trial.sources["A"].rows),
            ("other job's numeric payload", ("calls", 1, "input", "observations", "A"),
             good["calls"][1]["input"]["observations"]["B"]),
            ("prefix increased merely by WAIT", ("calls", 1, "input", "observations", "A", "prefix_s"), 0.6),
            ("unreleased job visible", ("calls", 0, "input", "observations", "B"),
             good["calls"][1]["input"]["observations"]["B"]),
            ("future truth top-level", ("calls", 0, "input", "duration"), 999),
            ("future truth nested", ("calls", 0, "input", "observations", "A", "rows", 0, "source_ref"), "secret"),
            ("premature completion", ("calls", 0, "input", "completed", "A"), 4),
            ("release event at next dispatch", ("releases", 1, "time"), 3),
            ("review window additive not cumulative", ("ledger", 1, "prefix_returned_s"), 0.8),
            ("callback index shifted", ("calls", 1, "index"), 9),
        ]
        for name, path, value in faults:
            with self.subTest(fault=name):
                bad = deepcopy(good)
                replace_at(bad, path, value)
                with self.assertRaises(AssertionError):
                    check_public_contract(bad, trial)
                # A different job's first valid numeric prefix needs external truth.
                if name != "other job's numeric payload":
                    with self.assertRaises(AssertionError):
                        verify_ledger(bad)

    def test_offline_source_reference_must_match_job_and_disclosure_time(self):
        trial = early_trial()
        good = observe(trial, EARLY_ACTIONS)
        for field, value in (("source_ref_logged_at", 1),
                             ("source_ref_offline", "synthetic://wrong-job")):
            with self.subTest(field=field):
                bad = deepcopy(good)
                bad["ledger"][1][field] = value
                with self.assertRaises(AssertionError):
                    check_public_contract(bad, trial)
                if field == "source_ref_logged_at":
                    with self.assertRaises(AssertionError):
                        verify_ledger(bad)

    def test_inside_callback_witness_kills_self_consistent_trace_rewrite(self):
        good = observe(early_trial(), EARLY_ACTIONS)
        actual_entries = deepcopy(good["calls"])
        bad = deepcopy(good)
        # All redundant records agree on this different command; the witness does not.
        bad["calls"][1]["action"]["b_s"] = 4
        bad["ledger"][1].update(b=4, end=6.5, idle=1.5, fee=2.6, cost_after=3.6)
        bad["calls"][2].update(time=6.5)
        bad["calls"][2]["input"].update(time=6.5, budget_left=28.4)
        bad["ledger"][2].update(time=6.5, end=6.5, cost_after=3.6)
        bad["metrics"].update(elapsed_s=6.5, cost=3.6, reserved_idle_s=1.5)
        with self.assertRaisesRegex(AssertionError, "callback witness"):
            need(bad["calls"] == actual_entries, "callback witness mismatch")

    def test_verifier_rejects_coherently_rewritten_admission_and_terminal_faults(self):
        early = observe(early_trial(), EARLY_ACTIONS)
        review = run_trial(early_trial(), fixed_actions([
            {"action": "REVIEW", "job_id": "J"},
            {"action": "REVIEW", "job_id": "J"}, STOP]))
        faults = []
        for field, value in (("release_s", 2), ("predecessors", ["J"])):
            bad = deepcopy(early)
            for c in bad["calls"]:
                c["input"]["jobs"]["J"][field] = value
            if field == "release_s":
                bad["releases"][0]["time"] = value
                for c in bad["calls"][:2]:
                    del c["input"]["observations"]["J"]
            faults.append(("admission " + field, bad))
        bad = deepcopy(early)
        for c in bad["calls"]:
            c["input"]["case"]["horizon_s"] = 7
        faults.append(("planned end exceeds H although completion fits", bad))
        for kind in ("quota", "per-job limit", "action limit"):
            bad = deepcopy(review)
            for c in bad["calls"]:
                if kind == "quota":
                    c["input"]["case"]["observation_limit"] = 1
                    c["input"]["reviews_left"] -= 3
                elif kind == "per-job limit":
                    c["input"]["rules"]["observation"]["max_per_job"] = 1
                else:
                    c["input"]["rules"]["max_actions"] = 1
            faults.append((kind, bad))
        wait = run_trial(early_trial(), fixed_actions([{"action": "WAIT", "seconds": 1}, STOP]))
        bad = deepcopy(wait)
        bad["calls"][0]["action"]["seconds"] = 0
        bad["ledger"][0]["end"] = 0
        bad["calls"][1]["time"] = bad["calls"][1]["input"]["time"] = 0
        bad["ledger"][1].update(time=0, end=0)
        bad["metrics"]["elapsed_s"] = 0
        faults.append(("accepted zero wait", bad))
        for mutation in ("truncated", "after STOP", "false rejection"):
            bad = deepcopy(early)
            if mutation == "truncated":
                bad["calls"].pop()
                bad["ledger"].pop()
            elif mutation == "after STOP":
                bad["calls"].append(deepcopy(bad["calls"][-1]))
                bad["calls"][-1]["index"] += 1
                bad["ledger"].append(deepcopy(bad["ledger"][-1]))
            else:
                bad["calls"][-1]["rejected"] = "action limit"
                bad["ledger"].pop()
                bad["metrics"].update(valid_protocol=False, invalid_actions=1)
            faults.append((mutation, bad))
        for name, bad in faults:
            with self.subTest(fault=name):
                with self.assertRaises(AssertionError):
                    verify_ledger(bad)

    def test_verifier_rejects_changed_or_missing_already_disclosed_samples(self):
        good = observe(early_trial(), EARLY_ACTIONS)
        for missing in (False, True):
            with self.subTest(missing=missing):
                bad = deepcopy(good)
                rows = bad["calls"][1]["input"]["observations"]["J"]["rows"]
                if missing:
                    rows.pop(0)
                else:
                    rows[0]["head_pose"]["position"][0] += 1
                with self.assertRaises(AssertionError):
                    verify_ledger(bad)

    def test_synthetic_export_round_trip_and_hash_corruption(self):
        log = observe(early_trial(), EARLY_ACTIONS)
        before, payloads = deepcopy(log), {}
        document = dict(runs={"synthetic": packed(log, payloads)}, payloads=payloads)
        self.assertEqual(log, before)  # Packing cannot strip live callback rows.
        self.assertEqual(len(payloads), 2)  # .6 prefix reused at EXECUTE and STOP.
        with tempfile.TemporaryDirectory(prefix="q3-reliability-") as folder:
            path = Path(folder) / "synthetic.json"
            path.write_text(json.dumps(document, allow_nan=False), encoding="utf-8")
            verify_export(path)
            bad = deepcopy(document)
            rows = next(iter(bad["payloads"].values()))
            rows[0]["head_pose"]["position"][0] += 1
            path.write_text(json.dumps(bad, allow_nan=False), encoding="utf-8")
            with self.assertRaises(AssertionError):
                verify_export(path)
        # A digest proves payload integrity, NOT that a valid payload belongs to a job.
        # The swapped-job injection above is detected by the synthetic sample oracle.


if __name__ == "__main__":
    unittest.main()
