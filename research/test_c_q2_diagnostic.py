"""Synthetic accounting and aggregation checks for paired wrists.

AI assistance: OpenCode, OpenAI GPT-6 family, GPT-6.2 Astra routing group,
2026-11-20. Exact model release date unavailable.
"""

import unittest

import numpy as np
import pandas as pd

from c_q2_diagnostic import replay, summaries


class DiagnosticChecks(unittest.TestCase):
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
