"""Integration cases for Q2 plan handoff, failed prerequisites and Q4 time boundaries."""

import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.q4.retention import RetainedPlan, outcome
from src.run import run_baselines, run_retention
from src.shared.plan import Plan
from src.shared.scene import Scene


class RetentionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = ROOT / 'configs/b/q22-numerical.json'
        cfg = json.loads(cls.config.read_text())
        data = json.loads((cls.config.parent / cfg['scene']).read_text())
        plan = Plan.load(json.loads((cls.config.parent / cfg['plan']).read_text()), cfg['method'], cfg['scene_sha256'])
        cls.rules = data['q4']
        cls.scene = Scene.from_dict(data)
        cls.policy = RetainedPlan(cls.scene, plan, cls.rules)

    @staticmethod
    def case(failed=None, new=None, rec=None):
        return dict(id='one', failed_vehicle=failed, new_alert=new, recovery_s=rec)

    def test_failure_just_before_joint_start_and_exact_tie(self):
        reference = self.policy.run(self.case())
        first = reference['jobs']['T1']['start_s']
        for cut, success in ((first, False), (math.nextafter(first, math.inf), True)):
            policy = RetainedPlan(self.scene, self.policy.plan, dict(self.rules, disturbance_s=cut))
            result = policy.run(self.case('V5'))
            self.assertEqual(result['jobs']['T1']['locked_at_5s'], success)
            self.assertEqual(result['k'], 3 if success else 1)
            self.assertEqual(result['jobs']['T3']['start_s'], reference['jobs']['T3']['start_s'] if success else None)
            self.assertEqual(result['jobs']['T2']['start_s'], reference['jobs']['T2']['start_s'])

    def test_in_transit_failure_retains_real_position_and_pose(self):
        archived = json.loads((ROOT / 'experiments/b/q4_retention/state_5s.json').read_text())['after_each_failure']
        for failed in self.scene.homes:
            result = self.policy.run(self.case(failed, [270, 50, 50], 30))
            self.assertEqual(result['N'], 4)
            self.assertEqual(result['t_last_s'], 60)
            for v, state in result['state_after_disturbance'].items():
                ref = archived[failed][v]
                self.assertEqual((state['phase'], state['target']), (ref['phase'], ref['target']))
                field = 'last_position_xy_m' if v == failed else 'position_xy_m'
                self.assertLess(math.dist(state[field], ref[field]), 1e-8)
                self.assertLess(math.dist(state['gimbal_deg'], ref['gimbal_deg']), 1e-8)
            self.assertEqual(result['events'], self.policy.run(self.case(failed))['events'])

    def test_cutoff_is_a_closed_window_on_starts(self):
        for t, expected in [(60., 1), (math.nextafter(60., math.inf), 0)]:
            pair = [dict(vehicle=v, target='T1', kind='observe', t0=t, t1=t+5) for v in ('V1', 'V2')]
            metrics = outcome(pair, ['T1'], 5, [0, 60])
            self.assertEqual((metrics['k'], metrics['t_last_s']), (expected, 60.))

    def test_native_plan_drives_score_and_errors_preserve_output(self):
        ready = run_baselines(ROOT / 'configs/b/q22-baselines.json', 'ready')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, native, output = [root / name for name in ('manifest.json', 'plan.json', 'out.jsonl')]
            manifest.write_text(json.dumps({'scenarios': [self.case('V5')]}))
            native.write_text(json.dumps(ready['plan']))
            run_retention(self.config, manifest, native, output)
            old = output.read_bytes()
            r = json.loads(old)
            self.assertEqual(r['provenance']['plan_method'], 'ready')
            self.assertIsNone(r['jobs']['T3']['start_s'])
            self.assertEqual(r['k'], 2)
            self.assertEqual(self.policy.run(self.case('V5'))['k'], 3)
            manifest.write_text(json.dumps({'scenarios': [self.case(rec=35)]}))
            with self.assertRaisesRegex(ValueError, 'stress'):
                run_retention(self.config, manifest, native, output)
            self.assertEqual(output.read_bytes(), old)
            run_retention(self.config, manifest, native, output, allow_stress=True)
            self.assertTrue(json.loads(output.read_bytes())['stress_recovery'])


if __name__ == '__main__':
    unittest.main()
