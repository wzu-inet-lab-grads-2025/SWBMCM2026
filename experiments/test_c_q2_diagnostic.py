"""Synthetic accounting and aggregation checks for paired wrists.

AI assistance: OpenCode, OpenAI GPT-6 family, GPT-6.2 Astra routing group,
2026-11-20. Exact model release date unavailable.
"""

import unittest

import numpy as np
import pandas as pd

if __package__:
    from .c_q2_diagnostic import replay, summaries
else:
    from c_q2_diagnostic import replay, summaries


class DiagnosticChecks(unittest.TestCase):
    def test_receipt_boundary_survives_epoch_shift_and_hand_swap(self):
        times = np.array([10, 500_000_009, 500_000_010, 500_000_011], dtype=np.int64)
        initial = np.array([[1, 2, 3], [10, 20, 30]])
        packet = np.array([[2, 3, 4], [20, 30, 40]])
        expected = np.stack([initial, initial, packet, packet])
        for shift, order in [(0, [0, 1]), (1_700_000_000_000_000_000, [1, 0])]:
            samples = {int(times[0]) + shift: packet[order]}
            prediction, bill = replay(times + shift, initial[order], 500, samples.__getitem__)
            np.testing.assert_array_equal(prediction, expected[:, order])
            self.assertEqual(bill, (1, 1, 2, 0.5))

    def test_paired_disclosure_and_fee(self):
        times = np.array([1000, 1499, 1500, 1900]) * 10**6
        initial = np.zeros((2, 3))
        packet = np.array([[1, 2, 3], [6, 5, 4]])
        reads = []

        def receive(t):
            reads.append(t)
            return packet

        for delay in (0, 500):
            pred, bill = replay(times, initial, delay, receive)
            np.testing.assert_array_equal(pred[:2], [initial] * 2)
            np.testing.assert_array_equal(pred[2:], [packet if delay else initial] * 2)
            self.assertEqual(bill, (1, 1, 2, 0.5) if delay else (0, 0, 0, 0))
            self.assertEqual(reads, [times[0]] if delay else [])

    def test_unequal_records_and_environments(self):
        # One long record and two short ones. The record and environment means
        # have different denominators from the pooled timestamp mean.
        rows = [('E1', 'A', 1, 0)] * 7 + [('E1', 'B', 1, 2), ('E2', 'C', 1, 9)]
        sample = pd.DataFrame(rows, columns=['environment', 'record', 'window', 'left'])
        result = summaries(sample.assign(delay_ms=0, age_bin='all', age_s=1, right=sample.left * 2))
        macro = result[result.scope == 'macro'].iloc[0]
        self.assertEqual((macro.left, macro.right), (5, 10))
        self.assertEqual((macro.records, macro.environments, macro.timestamps, macro.windows), (3, 2, 9, 3))


if __name__ == '__main__':
    unittest.main()
