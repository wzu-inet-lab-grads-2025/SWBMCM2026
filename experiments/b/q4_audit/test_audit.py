"""Independent regression assertions, including exact expected hand scores."""
import copy
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    from .audit import (Context, audit_record, audit_pair, audit_files, segment_box,
                        segment_rectangle_distance, path_at, independent_axes_at)
    from .examples import cases, scenario, record, fixture_context, trim_failure
except ImportError:
    from audit import (Context, audit_record, audit_pair, audit_files, segment_box,
                       segment_rectangle_distance, path_at, independent_axes_at)
    from examples import cases, scenario, record, fixture_context, trim_failure

HERE = Path(__file__).resolve().parent
RUNS = HERE.parents[2] / "runs/b/q4_audit"


class WitnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.witnesses = cases()
        cls.by_id = {c["id"]: c for c in cls.witnesses}

    def inspect(self, name):
        c = self.by_id[name]
        return audit_record(c["record"], c["scenario"], context=Context.from_dict(c["context"]))

    def test_all_hand_witnesses(self):
        for c in self.witnesses:
            with self.subTest(c["id"]):
                report = self.inspect(c["id"])
                self.assertEqual(report["status"], c["expected_status"], report)
                codes = {e["code"] for e in report["errors"] + report["missing_evidence"]}
                self.assertTrue(set(c["expected_codes"]) <= codes, codes)
                self.assertEqual(report["certified"], report["status"] == "pass")

    def test_independent_axes_V4_hand_endpoint_and_linear_counterexample(self):
        c = self.by_id["independent_axes_V4_failure_at_5_ok"]
        event = next(e for e in c["record"]["events"] if e["vehicle"] == "V4" and e["kind"] == "slew")
        elapsed = 5 - 0.9335681693539041
        expected_pan = elapsed * 30
        expected_tilt = 23.184672017939654  # reached before the still-moving pan
        self.assertAlmostEqual(event["gimbal1_deg"][0], expected_pan, places=10)
        self.assertAlmostEqual(event["gimbal1_deg"][1], expected_tilt, places=10)
        self.assertLess(expected_pan, 178.73579121051677)
        self.assertEqual(self.inspect(c["id"])["status"], "pass")
        wrong = self.inspect("independent_axes_proportional_endpoint_bad")
        self.assertEqual(wrong["status"], "fail")
        self.assertIn("independent_axis_endpoint", {x["code"] for x in wrong["errors"]})

    def test_rotation_model_is_not_inferred_from_endpoints(self):
        # Same full endpoints do not imply equal intermediate history. Using an
        # independent-axis clipped endpoint under linear_axes changes prefix.
        c = self.by_id["independent_axes_V4_failure_at_5_ok"]
        value = copy.deepcopy(c["record"])
        value["rotation_model"] = "linear_axes"
        result = audit_record(value, c["scenario"])
        self.assertEqual(result["status"], "fail")
        self.assertIn("prefix_rotation", {x["code"] for x in result["errors"]})

    def test_pair_mixed_models_cannot_claim_identical_physical_prefix(self):
        linear = self.by_id["nominal_ok"]
        independent = copy.deepcopy(self.by_id["independent_axes_nominal_ok"]["record"])
        independent["scenario_id"] = linear["scenario"]["id"]
        report = audit_pair(linear["record"], independent, linear["scenario"])
        self.assertEqual(report["left"]["status"], "pass")
        self.assertEqual(report["right"]["status"], "pass")
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["prefix_status"], "fail")
        self.assertIsNone(report["winner"])
        self.assertTrue(report["pair_errors"])

    def test_independent_rotation_goal_and_declared_rate_crosschecked(self):
        c = self.by_id["independent_axes_V4_failure_at_5_ok"]
        for field, changed, code in (("gimbal_goal_deg", [0, 0], "gimbal_goal_mismatch"),
                                     ("axis_speed_deg_s", 15, "axis_rate_mismatch")):
            value = copy.deepcopy(c["record"])
            e = next(e for e in value["events"] if e["vehicle"] == "V4" and e["kind"] == "slew")
            e[field] = changed
            report = audit_record(value, c["scenario"])
            self.assertIn(code, {x["code"] for x in report["errors"]})

    def test_exact_nominal_score(self):
        report = self.inspect("nominal_ok")
        self.assertEqual(report["recomputed"]["k"], 3)
        self.assertEqual(report["recomputed"]["N"], 3)
        self.assertEqual(report["recomputed"]["eta"], 1.0)
        self.assertAlmostEqual(report["recomputed"]["t_last_s"], 10.3521185907042, places=10)

    def test_hand_endpoint_scores(self):
        expected = {
            "at_5_without_failure_ok": (1, 1, 1, 5),
            "at_5_failure_precedes_start_ok": (1, 0, 0, 60),
            "at_60_ok": (1, 1, 1, 60),
            "after_60_not_counted_ok": (1, 0, 0, 60),
            "failure_during_observation_ok": (3, 2, 2/3, 60),
            "new_alert_counted_ok": (4, 3, .75, 60),
        }
        for name, values in expected.items():
            with self.subTest(name):
                score = self.inspect(name)["recomputed"]
                self.assertEqual(tuple(score[k] for k in ("N", "k", "eta", "t_last_s")), values)

    def test_both_vehicles_must_start_by_exact_deadline(self):
        sc = scenario('deadline-float')
        ctx = fixture_context(start=60.0)
        value = record(ctx, sc)
        late = next(e for e in value['events'] if e['vehicle'] == 'V2' and e['kind'] == 'observe')
        late['t0'] = math.nextafter(60.0, math.inf)
        report = audit_record(value, sc, context=ctx)
        self.assertEqual(report['recomputed']['k'], 0)
        self.assertEqual(report['status'], 'fail')

    def test_started_just_before_failure_remains_locked(self):
        ctx = fixture_context(start=math.nextafter(5., 0.))
        sc = scenario('last-start-before-failure', failed='V1')
        value = trim_failure(record(ctx, sc), ctx, 'V1')
        report = audit_record(value, sc, context=ctx)
        self.assertEqual(report['status'], 'pass', report['errors'])
        self.assertEqual(report['recomputed']['k'], 1)

    def test_unknown_path_never_certified(self):
        report = self.inspect("path_evidence_missing")
        self.assertIsNone(report["recomputed"])
        self.assertEqual(report["score_bounds"], {"k_min": 0, "k_max": 3, "N": 3})
        self.assertEqual(report["event_score"]["k"], 3)

    def test_invalid_earlier_slew_not_laundered_by_observe(self):
        report = self.inspect("not_ready_bad")
        self.assertLess(report["score_bounds"]["k_min"], 3)
        self.assertNotEqual(report["recomputed"], report["event_score"])

    def test_default_context_not_trusted_from_record(self):
        c = self.by_id["at_5_without_failure_ok"]
        value = dict(c["record"], context=c["context"])
        report = audit_record(value, c["scenario"])
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["event_score"]["N"], 3)

    def test_no_events_is_unknown_not_false_zero_score(self):
        ctx = Context.default()
        sc = scenario("no-events")
        value = record(ctx, sc)
        value.pop("events")
        value.pop("trace_complete_until_s")
        report = audit_record(value, sc)
        self.assertIsNone(report["recomputed"])
        self.assertEqual(report["score_bounds"], {"k_min": 0, "k_max": 3, "N": 3})
        self.assertIn("events_missing", {x["code"] for x in report["missing_evidence"]})

    def test_no_mutation(self):
        c = copy.deepcopy(self.by_id["nominal_ok"])
        before = copy.deepcopy(c)
        audit_record(c["record"], c["scenario"], context=Context.from_dict(c["context"]))
        self.assertEqual(c, before)

    def test_false_initial_timestamp_not_a_free_pass(self):
        c = self.by_id["in_transit_handoff_ok"]
        value = copy.deepcopy(c["record"])
        second = next(e for e in value["events"] if e["kind"] == "drive" and e["t0"] == 5)
        second["command_effective_s"] = second["command_issued_s"] = 0
        unchanged = audit_record(value, c["scenario"], context=Context.from_dict(c["context"]))
        self.assertEqual(unchanged["status"], "pass")  # splitting telemetry is not a new command
        second["path"][-1][0] += .1
        result = audit_record(value, c["scenario"], context=Context.from_dict(c["context"]))
        self.assertEqual(result["status"], "fail")
        self.assertIn("initial_command_route", {x["code"] for x in result["errors"]})

    def test_missing_scenario_fields(self):
        c = self.by_id["nominal_ok"]
        sc = dict(c["scenario"])
        del sc["new_alert"]
        report = audit_record(c["record"], sc)
        self.assertEqual(report["status"], "incomplete")
        self.assertNotIn("recomputed", report)

    def test_missing_station_and_rotation_declaration(self):
        c = self.by_id["nominal_ok"]
        for field, code in (("station", "station_evidence_missing"), ("rotation_model", "rotation_model_missing")):
            value = copy.deepcopy(c["record"])
            if field == "station":
                for e in value["events"]:
                    e.pop(field, None)
            else:
                value.pop(field)
            result = audit_record(value, c["scenario"])
            self.assertEqual(result["status"], "incomplete")
            self.assertIn(code, {x["code"] for x in result["missing_evidence"]})

    def test_malformed_event_fields_return_issues_not_exceptions(self):
        c = self.by_id["nominal_ok"]
        for key in ("vehicle", "target", "kind", "t0", "t1", "station", "path"):
            for malformed in (None, [], {}, True, "bad", [1, 2]):
                with self.subTest(key=key, malformed=malformed):
                    value = copy.deepcopy(c["record"])
                    value["events"][0][key] = malformed
                    report = audit_record(value, c["scenario"])
                    self.assertIn(report["status"], {"fail", "incomplete", "pass"})

    def test_post_start_error_does_not_erase_locked_success(self):
        report = self.inspect("locked_partner_released_bad")
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["tasks"]["T1"]["status"], "verified_start")
        self.assertIn("locked_healthy_occupancy", {x["code"] for x in report["errors"]})

    def test_nonfinite_event(self):
        c = self.by_id["nominal_ok"]
        value = copy.deepcopy(c["record"])
        value["events"][0]["t0"] = float("nan")
        report = audit_record(value, c["scenario"])
        self.assertEqual(report["status"], "fail")
        self.assertIn("event_time", {x["code"] for x in report["errors"]})


class PairAndCLITests(unittest.TestCase):
    def setUp(self):
        self.context = Context.default()
        self.sc = scenario("same-id")
        self.value = record(self.context, self.sc)
        RUNS.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=RUNS, prefix="test-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def test_pair_same_nominal_prefix(self):
        second = dict(self.value, policy="other")
        report = audit_pair(self.value, second, self.sc)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["winner"], "tie")
        self.assertEqual(report["prefix_status"], "pass")

    def test_pair_agreeing_on_wrong_prefix_still_fails(self):
        bad = copy.deepcopy(self.value)
        next(e for e in bad["events"] if e["kind"] == "drive" and e["vehicle"] == "V3")["path"][-1][0] += .1
        report = audit_pair(bad, bad, self.sc)
        self.assertEqual(report["prefix_status"], "fail")
        self.assertIsNone(report["winner"])

    def test_reversed_order_matching(self):
        second_sc = scenario("z", new=[0, 30, 50])
        second = record(self.context, second_sc)
        left, right = self.directory / "left.jsonl", self.directory / "right.jsonl"
        left.write_text("\n".join(map(json.dumps, [self.value, second])) + "\n")
        right.write_text("\n".join(map(json.dumps, [second, self.value])) + "\n")
        report = audit_files(left, [self.sc, second_sc], right)
        self.assertEqual(report["status"], "pass")
        self.assertEqual([r["scenario_id"] for r in report["reports"]], ["same-id", "z"])

    def test_duplicates_missing_unknown_and_bad_json(self):
        left, right = self.directory / "left.jsonl", self.directory / "right.jsonl"
        left.write_text(json.dumps(self.value) + "\n" + json.dumps(self.value) + "\n{" + "\n")
        right.write_text(json.dumps(dict(self.value, scenario_id="unknown")) + "\n")
        report = audit_files(left, [self.sc], right)
        self.assertEqual(report["status"], "fail")
        self.assertTrue({"jsonl_input", "unknown_scenario", "missing_record"} <= {x["code"] for x in report["input_issues"]})

    def test_cli_exit_codes_and_single_interface(self):
        source, sc = self.directory / "single.json", self.directory / "sc.json"
        sc.write_text(json.dumps(self.sc))
        for expected, mutate in [(0, lambda v: None), (1, lambda v: v.update(k=0)),
                                 (2, lambda v: v.pop("trace_complete_until_s"))]:
            with self.subTest(exit_code=expected):
                value = copy.deepcopy(self.value)
                mutate(value)
                source.write_text(json.dumps(value))
                completed = subprocess.run([sys.executable, str(HERE / "audit.py"), str(source),
                                            "--single", "--scenarios", str(sc)], text=True, capture_output=True)
                self.assertEqual(completed.returncode, expected, completed.stdout + completed.stderr)
                self.assertEqual(json.loads(completed.stdout)["status"], {0: "pass", 1: "fail", 2: "incomplete"}[expected])


class GeometryUnitTests(unittest.TestCase):
    def test_disk_corner_exact_distance_not_expanded_square(self):
        box = [0, 0, 10, 10, 10]
        self.assertAlmostEqual(segment_rectangle_distance([11.1, 11.1], [11.1, 11.1], box), math_sqrt_242())
        self.assertGreater(segment_rectangle_distance([11.1, 11.1], [11.1, 11.1], box), 1.5)
        self.assertLess(segment_rectangle_distance([11, 11], [11, 11], box), 1.5)
        self.assertEqual(segment_rectangle_distance([-1, 5], [11, 5], box), 0)

    def test_finite_los_touch_is_blocked(self):
        self.assertTrue(segment_box([-1, 0, 10], [11, 0, 10], [0, 0, 0], [10, 10, 10]))
        self.assertFalse(segment_box([-1, 0, 10.001], [11, 0, 10.001], [0, 0, 0], [10, 10, 10]))
        self.assertFalse(segment_box([-1, -1, 5], [-.1, -.1, 5], [0, 0, 0], [10, 10, 10]))

    def test_independent_axes_saturation_wrapping_and_nonzero_origin(self):
        # Hand values deliberately do not call witness generator code.
        self.assertEqual(independent_axes_at([10, 5], [100, 20], 1), [40, 20])
        self.assertEqual(independent_axes_at([40, 20], [100, 20], 1), [70, 20])
        self.assertEqual(independent_axes_at([40, 20], [100, 20], 9), [100, 20])
        self.assertEqual(independent_axes_at([170, 10], [-170, -10], .5), [185, -5])
        self.assertEqual(independent_axes_at([60, 10], [0, 0], .5), [45, 0])
        self.assertEqual(independent_axes_at([-60, -10], [0, 0], 1), [-30, 0])
        self.assertEqual(independent_axes_at([10, 5], [100, 20], 0), [10, 5])

    def test_polyline_interpolation_at_corner(self):
        self.assertEqual(path_at([[0, 0], [3, 0], [3, 4]], 5/7), [3, 2])
        self.assertEqual(path_at([[0, 0], [0, 0]], .5), [0, 0])


def math_sqrt_242():
    return 2.42 ** .5


if __name__ == "__main__":
    unittest.main()
