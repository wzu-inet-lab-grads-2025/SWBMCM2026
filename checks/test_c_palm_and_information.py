"""Synthetic geometry and binary information checks for C training diagnostics.

AI assistance, OpenCode, OpenAI GPT family, GPT-6.2 Astra routing group,
2026-11-20. Exact release date unavailable.
"""

import unittest

import numpy as np

from experiments.c_palm_motion import describe
from experiments.c_q2_diagnostic import replay
from experiments.c_quality_information import information


class DiagnosticChecks(unittest.TestCase):
    def test_pure_translation_and_wrist_centred_rotation(self):
        palm = np.array([[0., 0., 0.], [1., 0., 0.], [.7, .3, 0.], [.3, .7, 0.], [0., 1., 0.]])
        origin = np.array([4., 5., 6.])
        translated = palm[None] + origin + np.arange(4)[:, None, None] * np.array([1, 0, 0])
        result = describe(translated, 1.)
        self.assertFalse(result['quiet'] or result['turned'])
        self.assertAlmostEqual(result['palm_relative_max_cm'], 0.)
        rot = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]])
        turned = np.stack([palm, palm @ rot, palm @ rot, palm @ rot]) + origin
        result = describe(turned, 1.)
        self.assertTrue(result['quiet'] and result['turned'])
        self.assertAlmostEqual(result['angle_max_deg'], 90.)
        turned[-1] = palm + origin
        self.assertFalse(describe(turned, 1.)['turned'])
        turned[:, 4] = turned[:, 1]
        self.assertIsNone(describe(turned, 1.))

    def test_binary_information_limits(self):
        for counts, answer in [([5, 5, 5, 5], 0.), ([5, 0, 0, 5], 1.), ([10, 0, 0, 0], 0.)]:
            row = dict(zip(('good_good', 'good_bad', 'bad_good', 'bad_bad'), counts))
            self.assertAlmostEqual(information(row)['mi_bit'], answer)

    def test_midwindow_request_buys_the_requested_source_time(self):
        clock = np.array([20, 990, 1002, 1499, 1501, 1502, 1999]) * 10**6
        initial = np.zeros((2, 3))
        packet = np.array([[1., 2., 3.], [4., 5., 6.]])
        predictions, bill = replay(clock, initial, 500, {1002*10**6: packet}.__getitem__,
                                   request_not_before_ns=10**9)
        np.testing.assert_array_equal(predictions[:5], [initial]*5)
        np.testing.assert_array_equal(predictions[5:], [packet]*2)
        self.assertEqual(bill, (1, 1, 2, .5))
