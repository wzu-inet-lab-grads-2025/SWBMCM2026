#!/usr/bin/env python3
"""Deterministic checks for the independently derived Q3 circular model."""
from __future__ import annotations

import math
import unittest

import numpy as np
from scipy.integrate import quad
from scipy.optimize import brentq

from solve import (angular_probability, circular_probability, linear_probability,
                   projected_normal_density, wrap)


class CircularModelTests(unittest.TestCase):
    r = 40.0
    tau = math.radians(5.0)
    g = math.radians(1.0)

    def test_sign_jacobian_and_rotation(self):
        rng = np.random.default_rng(20261120)
        delta = rng.normal(size=(1000, 2)) * 1.9
        for beta in [0.0, 0.7, 2.5, -3.1]:
            e = np.array([math.cos(beta), math.sin(beta)])
            n = np.array([-math.sin(beta), math.cos(beta)])
            sight_est = self.r * e - delta
            global_error = wrap(np.arctan2(sight_est[:, 1], sight_est[:, 0]) - beta)
            local_error = np.arctan2(-delta @ n, self.r - delta @ e)
            self.assertLess(float(np.max(np.abs(wrap(global_error - local_error)))), 2e-14)
            numerical = []
            step = 1e-5
            for direction in np.eye(2):
                plus = self.r * e - step * direction
                minus = self.r * e + step * direction
                derivative = float(wrap(math.atan2(plus[1], plus[0])
                                        - math.atan2(minus[1], minus[0]))) / (2 * step)
                numerical.append(derivative)
            expected = np.array([math.sin(beta), -math.cos(beta)]) / self.r
            self.assertLess(float(np.max(np.abs(numerical - expected))), 5e-11)
        self.assertLess(math.atan2(-0.1, self.r), 0.0)

    def test_projected_normal_density_normalization(self):
        for kappa in [0.0, 0.001, 0.1, 1.0, 20.0, 40.0 / 0.3]:
            points = [] if kappa == 0 else [x / kappa for x in (1, 4, 12) if x / kappa < math.pi]
            mass = 2 * quad(lambda angle: projected_normal_density(angle, kappa),
                            0, math.pi, points=points, epsabs=1e-12, epsrel=1e-12)[0]
            self.assertAlmostEqual(mass, 1.0, places=10)

    def test_two_independent_integrals(self):
        for sigma in [0.3, 0.7, 1.1, 1.6367451685588885, 1.9]:
            for gimbal in [0.0, self.g]:
                full = angular_probability(sigma, self.r, self.tau, gimbal)[0]
                cone = circular_probability(sigma, self.r, self.tau, gimbal)
                self.assertAlmostEqual(full, cone, places=11)

    def test_chance_boundary_and_linear_optimism(self):
        caps = []
        for gimbal in [self.g, 0.0]:
            cap = brentq(lambda sig: circular_probability(sig, self.r, self.tau, gimbal) - 0.95, 1, 2)
            caps.append(cap)
            self.assertGreater(circular_probability(cap - 1e-4, self.r, self.tau, gimbal), 0.95)
            self.assertLess(circular_probability(cap + 1e-4, self.r, self.tau, gimbal), 0.95)
            self.assertGreater(linear_probability(cap, self.r, self.tau, gimbal), 0.95)
        self.assertAlmostEqual(caps[0], 1.6367451685588885, places=10)
        self.assertAlmostEqual(caps[1], 1.7787213119247403, places=10)
        self.assertLess(caps[0], caps[1])

    def test_global_monotonicity_and_uniform_limit(self):
        for gimbal in [self.g, 0.0]:
            sigmas = [0.3, 1.0, 1.9, 4.0, 10.0, 40.0, 400.0, 4000.0, 40000.0, 4e7]
            probabilities = [angular_probability(sig, self.r, self.tau, gimbal)[0] for sig in sigmas]
            self.assertTrue(all(a > b for a, b in zip(probabilities, probabilities[1:])))
            self.assertAlmostEqual(probabilities[-1], self.tau / math.pi, delta=4e-8)

    def test_small_sigma_limit_and_80m_failure(self):
        self.assertAlmostEqual(angular_probability(0.0, self.r, self.tau, self.g)[0],
                               math.erf(5 / math.sqrt(2)), places=14)
        self.assertEqual(angular_probability(0.0, self.r, self.tau, 0)[0], 1.0)
        for gimbal in [0.0, self.g]:
            self.assertLess(circular_probability(1.9, self.r, self.tau, gimbal), 0.95)
        self.assertGreater(circular_probability(0.3 + 0.02 * 66.8, self.r, self.tau, self.g), 0.95)


if __name__ == "__main__":
    unittest.main(verbosity=2)
