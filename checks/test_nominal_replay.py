"""Regression cases for the frozen-plan hand-off and nominal clock."""

import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.q2.scoring import score
from src.run import read_input
from src.shared.plan import Plan
from src.shared.scene import Scene
from src.shared.timeline import replay, state_at


class NominalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        inputs = ROOT / 'experiments/b'
        config, cls.scene_hash = read_input(inputs / 'scene.json')
        cls.scene = Scene.from_dict(config)
        cls.snapshot, _ = read_input(inputs / 'snapshots/q2_numerical/summary.json')

    def plan(self, document):
        return Plan.load(document, 'q2_numerical', self.scene_hash)

    def test_frozen_and_native_inputs_do_not_supply_the_clock(self):
        snapshot = copy.deepcopy(self.snapshot)
        for job in snapshot['q22']['jobs']:
            job['start_s'] = 999.
            for leg in job['legs']:
                for field in ('depart_s', 'arrival_s', 'ready_s', 'reset_end_s'):
                    leg[field] = 999.
        plan = self.plan(snapshot)
        native = json.loads(json.dumps(plan.to_dict()))
        events = replay(self.scene, self.plan(native))
        self.assertAlmostEqual(score(events, self.scene.targets)['latest_start_s'], 10.3521185907042, places=10)
        native['scene_sha256'] = 'wrong'
        with self.assertRaisesRegex(ValueError, 'scene'):
            self.plan(native)

    def test_stationary_disk_and_route_interior_are_checked(self):
        snapshot = copy.deepcopy(self.snapshot)
        leg = snapshot['q22']['jobs'][0]['legs'][0]
        leg['path'].insert(1, [17, 70])  # inside B3, endpoints still match
        with self.assertRaisesRegex(ValueError, 'B3'):
            replay(self.scene, self.plan(snapshot))

    def test_polyline_clock_handoff_and_final_idle(self):
        snapshot = copy.deepcopy(self.snapshot)
        leg = snapshot['q22']['jobs'][0]['legs'][0]
        leg['path'].insert(1, [14., 89.])  # first 3 m north of V3's home
        events = replay(self.scene, self.plan(snapshot))
        self.assertEqual(state_at(self.scene, events, .5)['V3']['position_xy_m'], (14., 87.5))
        reset = next(e for e in events if (e['vehicle'], e['target'], e['kind']) == ('V3', 'T1', 'reset'))
        after = state_at(self.scene, events, reset['t1'])['V3']
        self.assertEqual((after['phase'], after['target']), ('drive', 'T3'))
        last = state_at(self.scene, events, max(e['t1'] for e in events))
        self.assertTrue(all(s['phase'] == 'idle' and s['gimbal_deg'] == (0., 0.) for s in last.values()))

    def test_recording_horizon_does_not_truncate_q22(self):
        snapshot = copy.deepcopy(self.snapshot)
        leg = snapshot['q22']['jobs'][0]['legs'][0]
        leg['path'][1:1] = [[14., 92.], [14., 86.]] * 16
        events = replay(self.scene, self.plan(snapshot), 60.)
        self.assertGreater(score(events, self.scene.targets)['latest_start_s'], 60.)


if __name__ == '__main__':
    unittest.main()
