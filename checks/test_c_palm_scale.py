"""Metamorphic checks of the scale used for relative wrist error.

AI-assisted verification in OpenCode, OpenAI GPT family, GPT-6.2 Astra routing
group, 2026-11-20. Exact release date unavailable.
"""

import unittest

import numpy as np

from experiments.c_palm_scale import history_scale, lengths


class PalmScaleChecks(unittest.TestCase):
    def test_unit_conversion_and_change_of_origin(self):
        wrist = np.array([[1., 2., 3.], [2., 3., 4.], [3., 4., 5.]])
        middle = wrist + [0, .08, 0]
        prediction, reference = wrist[0], wrist[0] + [.03, .04, 0]
        for factor, offset in [(1, 0), (100, np.array([1000, -70, 12]))]:
            scale = history_scale(lengths(wrist * factor + offset, middle * factor + offset))
            error = np.linalg.norm((prediction * factor + offset) - (reference * factor + offset))
            self.assertAlmostEqual(scale, .08 * factor)
            self.assertAlmostEqual(error / scale, .625)

    def test_collapsed_missing_or_insufficient_history(self):
        wrist = np.ones((3, 3))
        for bad in ([1, 1, 1], [0, 0, 0], [np.nan, 1, 1]):
            middle = wrist + .1
            middle[-1] = bad
            self.assertIsNone(history_scale(lengths(wrist, middle)))
        self.assertIsNone(history_scale(np.array([.08])))
