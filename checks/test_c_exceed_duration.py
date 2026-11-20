"""Synthetic boundary checks. AI-assisted in OpenCode / OpenAI GPT family,
GPT-6.2 Astra routing group, 2026-11-20. Exact release date unavailable.
"""

import unittest

import numpy as np
import pandas as pd

from experiments.c_exceed_duration import survival_cells, window_comparison
from experiments.c_empirical_mask_lengths import compare
from experiments.c_occlusion_return_clock import events
from experiments.c_packet_value import PairedWindow


class DurationChecks(unittest.TestCase):
    def test_nested_recoveries_before_at_and_after_receipt(self):
        ms = 10**6
        clock = np.array([100, 300, 500, 700])*ms
        truth = np.zeros((4, 2, 3))
        truth[:, 0, 0] = [0, 2, 2, 2]
        w = PairedWindow('R', 'E', clock, 900*ms, 0, np.zeros((2, 3)), truth, np.ones(2))
        ends = np.array([300, 350, 450, 650])*ms
        left, right = compare(w, ends, 1, 150)
        self.assertEqual((left['requested'], left['timely'], left['at_end'], left['late']), (3, 1, 1, 1))
        self.assertEqual(left['cost'], 6)
        self.assertAlmostEqual(left['pending_in_mask_s'], (.05+.15+.15)/4)
        self.assertAlmostEqual(left['pending_to_receipt_s'], .15*3/4)
        self.assertAlmostEqual(left['saved_s'], .2/4)
        self.assertAlmostEqual(left['baseline_s'], (.05+.15+.35)/4)
        self.assertEqual(right['timely_tied'], 1)

    def test_receipt_between_ticks_and_strict_error_boundary(self):
        clock = np.array([100, 400, 800, 1000, 1200], dtype=np.int64)*10**6
        truth = np.zeros((5, 2, 3))
        truth[:, 0, 0] = [2, 2, 0, 1, 3]
        w = PairedWindow('R', 'E', clock, 1600*10**6, 0, np.zeros((2,3)), truth, np.ones(2))
        left, right = window_comparison(w, 500, 0)
        for key, value in [('scored_s',1.5), ('post_s',1.), ('base_s',1.1), ('updated_s',.7),
                           ('avoided_s',.6), ('induced_s',.2), ('longest_base_s',.7), ('longest_updated_s',.5),
                           ('ongoing_base_s',.2), ('ongoing_updated_s',0.)]:
            self.assertAlmostEqual(left[key], value)
        self.assertEqual((right['unchanged'], right['base_s'], right['updated_s']), (1, 0., 0.))

    def test_reasons_and_boundaries(self):
        def row(ms, reason):
            return {'header.ts': ms*10**6, 'is_bad': str(bool(reason)), 'problem_type.length': int(bool(reason)),
                    'problem_type[0]': reason}
        samples = [row(0, ''), row(50, 'right:occlusion'), row(100,'right:near edge'), row(150,''),
                   row(220,'left:occlusion'), row(270,'')]
        joint = list(events(samples))
        strict = list(events(samples, continuous_occlusion=True))
        self.assertEqual(joint[0]['duration_ns'], 100*10**6)
        self.assertEqual(strict[0]['duration_ns'], 50*10**6)
        self.assertFalse(strict[1]['start_seen'])
        cells = list(survival_cells(pd.DataFrame(joint).assign(record='R', environment='E')))
        cell = next(x for x in cells if (x['h_ms'],x['delta_ms'])==(0,100))
        self.assertEqual((cell['at_risk'],cell['before_recovery'],cell['simultaneous']), (1,0,1))
        empty = next(x for x in cells if (x['h_ms'],x['delta_ms'])==(100,100))
        self.assertTrue(np.isnan(empty['fraction_strict']))
