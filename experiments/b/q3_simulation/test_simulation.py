#!/usr/bin/env python3
"""Fast deterministic and small-sample tests of the independent Q3 simulator."""
import json
import unittest

import numpy as np
from scipy.special import ndtr

from simulation import (
    DEG, ROOT, angles, clopper_pearson, count_modes, deterministic_checks,
    forward_geometry, quadrature_probability, wilson, wrap,
)

CFG = json.loads((ROOT / "experiments/b/scene.json").read_text())["q3"]


class ForwardTests(unittest.TestCase):
    def test_deterministic_geometry(self):
        self.assertGreaterEqual(len(deterministic_checks()), 10)

    def test_coincident_positions_have_no_bearing(self):
        for true, estimated_error in [([40., 0.], [0., 1.]), ([0., 0.], [40., 0.])]:
            with self.subTest(true=true), self.assertRaises(ValueError):
                forward_geometry(true, [40., 0.], estimated_error, 0., 0.)

    def test_random_rotation_and_translation(self):
        rng = np.random.default_rng(9331)
        delta = rng.standard_normal((10000, 2)) * 4
        zero = rng.standard_normal(10000) * 0.8 * DEG
        control = rng.standard_normal(10000) * 0.6 * DEG
        p, t = np.array([13.0, 29.0]), np.array([53.0, 29.0])
        a = 2.89
        r = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
        reference = forward_geometry(p, t, delta, zero, control)
        transformed = forward_geometry(r @ p + 100, r @ t + 100, delta @ r.T, zero, control)
        for got, expected in zip(transformed, reference):
            np.testing.assert_allclose(wrap(got - expected), 0.0, atol=1e-13)

    def test_position_sign_in_all_quadrants(self):
        p = np.array([0., 0.])
        for phi in np.arange(0, 360, 15) * DEG:
            unit = np.array([np.cos(phi), np.sin(phi)])
            lateral = np.array([-np.sin(phi), np.cos(phi)])
            cmd, _ = forward_geometry(p, 40 * unit, lateral, 0., 0.)
            self.assertAlmostEqual(float(cmd), -np.arctan2(1., 40.), places=13)

    def test_sources_are_not_double_counted(self):
        z = np.random.default_rng(453).standard_normal((10000, 4))
        command, total = angles(z, 1.9, CFG)
        position = angles(z, 1.9, CFG, source="position")[1]
        zero = angles(z, 1.9, CFG, source="zero")[1]
        control = angles(z, 1.9, CFG, source="control")[1]
        np.testing.assert_allclose(command, position, atol=1e-14)
        np.testing.assert_allclose(wrap(position + zero + control), total, atol=1e-14)
        gimbal_cmd, _ = angles(z, 1.9, CFG, source="gimbal")
        np.testing.assert_array_equal(gimbal_cmd, np.zeros(len(z)))

    def test_chunk_does_not_change_counts(self):
        z = np.random.default_rng(191).standard_normal((12347, 4))
        self.assertEqual(count_modes(z, 1.9, CFG, 101), count_modes(z, 1.9, CFG, 100000))

    def test_intervals_at_zero_and_full_success(self):
        self.assertEqual(clopper_pearson(0, 1000)[0], 0.)
        self.assertEqual(clopper_pearson(1000, 1000)[1], 1.)
        self.assertLess(clopper_pearson(1000, 1000)[0], 1.)
        for k in [0, 1, 950, 999, 1000]:
            for interval in [wilson(k, 1000), clopper_pearson(k, 1000)]:
                self.assertLessEqual(interval[0], k / 1000 + 1e-14)
                self.assertGreaterEqual(interval[1], k / 1000 - 1e-14)

    def test_only_command_wedge_reference(self):
        for sigma in [0.3, 1.6, 1.9, 2.5]:
            got = quadrature_probability(sigma, CFG, "command_only")["coverage"]
            wedge = 2 * ndtr(40 * np.sin(5 * DEG) / sigma) - 1
            self.assertAlmostEqual(got, wedge, places=12)

    def test_quadrature_monotonicity_and_baselines(self):
        for mode in ["total", "command_only"]:
            values = [quadrature_probability(sigma, CFG, mode)["coverage"]
                      for sigma in [0.3, 1.0, 1.5, 1.6, 1.7, 1.9, 2.5]]
            self.assertTrue(all(a > b for a, b in zip(values, values[1:])))
            self.assertGreater(values[0], 0.95)
            self.assertLess(values[-2], 0.95)
        for sigma in [1.6, 1.9]:
            self.assertLess(quadrature_probability(sigma, CFG, "total")["coverage"],
                            quadrature_probability(sigma, CFG, "command_only")["coverage"])

    def test_known_gimbal_variance_and_rad_conversion(self):
        z = np.random.default_rng(310).standard_normal((100000, 4))
        cmd, total = angles(z, 0., CFG)
        np.testing.assert_array_equal(cmd, np.zeros(len(z)))
        self.assertAlmostEqual(float(np.var(total / DEG)), 1., delta=0.02)

    def test_feasible_parameter_origin(self):
        self.assertEqual(quadrature_probability(0., CFG, "command_only")["coverage"], 1.)
        self.assertAlmostEqual(quadrature_probability(0., CFG, "total")["coverage"],
                               1. - 2. * ndtr(-5.), places=14)


if __name__ == "__main__":
    unittest.main(verbosity=2)
