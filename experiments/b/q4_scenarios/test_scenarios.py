"""Input acceptance and negative regressions; standard library unittest."""
import copy
from collections import Counter, defaultdict
from fractions import Fraction
import hashlib
import json
import unittest
from pathlib import Path

import audit_geometry
import generate

HERE = Path(__file__).resolve().parent


class ScenarioAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))["scenarios"]
        cls.extension = json.loads((HERE / "sensitivity.json").read_text(encoding="utf-8"))["scenarios"]
        cls.design = json.loads((HERE / "design.json").read_text(encoding="utf-8"))

    def test_shape_uniqueness_counts(self):
        self.assertEqual(len(self.main), 126)
        self.assertLessEqual(len(self.main), 150)
        expected = {"none": 1, "A": 6, "F": 5, "C": 2, "AF": 30, "AC": 12, "FC": 10, "AFC": 60}
        self.assertEqual(Counter(generate.combination(row) for row in self.main), expected)
        for rows in (self.main, self.extension):
            self.assertEqual(len({row["id"] for row in rows}), len(rows))
            signatures = {(tuple(row["new_alert"]) if row["new_alert"] else None,
                           row["failed_vehicle"], row["recovery_s"]) for row in rows}
            self.assertEqual(len(signatures), len(rows))
            for row in rows:
                self.assertEqual(set(row), {"id", "new_alert", "failed_vehicle", "recovery_s"})

    def test_independent_factor_coverage(self):
        self.assertEqual({tuple(r["new_alert"]) for r in self.main if r["new_alert"]},
                         {(30, 30, 50), (30, 50, 50), (150, 30, 50), (150, 50, 50), (270, 30, 50), (270, 50, 50)})
        self.assertEqual({r["recovery_s"] for r in self.main}, {None, 10, 30})
        self.assertTrue(all(r["recovery_s"] is None or 10 <= r["recovery_s"] <= 30 for r in self.main))

    def test_each_failure_block_is_exactly_equal_weight(self):
        for rows in (self.main, self.extension):
            groups = defaultdict(list)
            for row in rows:
                if row["failed_vehicle"]:
                    groups[(tuple(row["new_alert"]) if row["new_alert"] else None,
                            row["recovery_s"])].append(row["failed_vehicle"])
            self.assertEqual(len(groups), 21)
            for key, vehicles in groups.items():
                self.assertEqual(Counter(vehicles), {"V1": 1, "V2": 1, "V3": 1, "V4": 1, "V5": 1}, key)
                self.assertEqual(sum(Fraction(1, 5) for _ in vehicles), Fraction(1))
            self.assertEqual(Counter(r["failed_vehicle"] for r in rows if r["failed_vehicle"]),
                             {"V1": 21, "V2": 21, "V3": 21, "V4": 21, "V5": 21})

    def test_sensitivity_separation_and_official_domain(self):
        self.assertEqual(Counter(r["recovery_s"] for r in self.extension), {8: 42, 20: 42, 35: 42})
        self.assertTrue({r["id"] for r in self.main}.isdisjoint(r["id"] for r in self.extension))
        self.assertEqual(self.design["official_clarification"]["recovery_s"], "(5,30]")
        self.assertIn("OUT OF", self.design["sensitivity_interpretation"]["35"])

    def test_no_unsupported_probabilities(self):
        policy = self.design["probability_policy"]
        self.assertIsNone(policy["alert_occurrence_or_location"])
        self.assertIsNone(policy["outage_occurrence_or_recovery"])
        self.assertIsNone(policy["disturbance_combination"])
        self.assertEqual(set(policy["failure_identity_conditional_on_fixed_alert_and_recovery"].values()), {"1/5"})

    def test_deterministic_regeneration_and_hashes(self):
        for name, content in generate.build().items():
            self.assertEqual((HERE / name).read_text(encoding="utf-8"), content, name)
        for name, key in (("manifest.json", "main_sha256"), ("sensitivity.json", "sensitivity_sha256")):
            self.assertEqual(hashlib.sha256((HERE / name).read_bytes()).hexdigest(), self.design[key])

    def test_reject_missing_scenario(self):
        with self.assertRaises(AssertionError):
            generate.validate(self.main[:-1], (10, 30), include_no_outage=True)

    def test_reject_duplicate_fault_identity_in_group(self):
        rows = copy.deepcopy(self.main)
        chosen = next(r for r in rows if r["failed_vehicle"] == "V5")
        chosen["failed_vehicle"] = "V4"
        with self.assertRaises(AssertionError):
            generate.validate(rows, (10, 30), include_no_outage=True)

    def test_reject_duplicate_id(self):
        rows = copy.deepcopy(self.main)
        rows[1]["id"] = rows[0]["id"]
        with self.assertRaises(AssertionError):
            generate.validate(rows, (10, 30), include_no_outage=True)

    def test_reject_wrong_units_ranges_or_extra_fields(self):
        for key, value in (("new_alert", [360, 30, 50]), ("new_alert", [30, 29, 50]),
                           ("new_alert", [30, 30, 51]), ("recovery_s", 35),
                           ("failed_vehicle", "V6"), ("probability", 0.2)):
            rows = copy.deepcopy(self.main)
            rows[0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(AssertionError):
                generate.validate(rows, (10, 30), include_no_outage=True)


class GeometryAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = audit_geometry.run()

    def test_saved_audit_matches(self):
        self.assertEqual(json.loads((HERE / "geometry_audit.json").read_text(encoding="utf-8")), self.report)

    def test_initial_identity_signatures(self):
        self.assertEqual([a["initial_observable_vehicles"] for a in self.report["alerts"]],
                         [["V3", "V4", "V5"], ["V2", "V3", "V4", "V5"],
                          ["V1", "V3", "V5"], ["V1", "V3", "V5"],
                          ["V2", "V4", "V5"], ["V1", "V2", "V4"]])

    def test_every_failure_has_static_pair_not_dynamic_guarantee(self):
        for alert in self.report["alerts"]:
            for failed, pair in alert["static_pair_witness_by_failed_vehicle"].items():
                self.assertNotIn(failed, pair["vehicles"])
                self.assertEqual(len(set(pair["vehicles"])), 2)
                self.assertGreater(pair["absolute_sin_intersection"], 1e-8)
                self.assertGreaterEqual(pair["baseline_m"], 3)
            self.assertEqual(sum(alert["grid_mutually_exclusive_counts"].values()), 2401)
        self.assertIn("not path reachability", self.report["scope"])

    def test_finite_closed_ray_and_elevation_regressions(self):
        scene = json.loads(audit_geometry.SCENE_PATH.read_text(encoding="utf-8"))
        audit_geometry.regression_checks(scene)
        self.assertAlmostEqual(self.report["minimum_horizontal_range_for_H50_m"], 8.463695074006323)


if __name__ == "__main__":
    unittest.main()
