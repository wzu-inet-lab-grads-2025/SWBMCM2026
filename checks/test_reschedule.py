"""Online handoff cases that a latest-start score alone cannot distinguish."""

import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.q4.replay import ScenarioReplay, clip_phase, outcome
from src.q4.reschedule import BeamPlanner
from src.run import read_scene_inputs, run_baselines, run_q4
from src.shared.plan import Plan
from src.shared.scene import Scene


class OnlineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = ROOT / 'configs/b/q22-numerical.json'
        cls.settings = ROOT / 'configs/b/q4-reschedule.json'
        config, raw, provenance = read_scene_inputs(cls.cfg)
        cls.scene = Scene.from_dict(raw)
        plan = Plan.load(json.loads((cls.cfg.parent / config['plan']).read_text()), config['method'], provenance['scene_sha256'])
        cls.engine = ScenarioReplay(cls.scene, plan, raw['q4'])
        cls.planner = BeamPlanner(cls.scene, plan, json.loads(cls.settings.read_text()))

    @staticmethod
    def case(rec=None, failed='V3', alert=(30, 30, 50)):
        return dict(id='test', recovery_s=rec, failed_vehicle=failed, new_alert=alert)

    def test_recovery_prefix_and_cancellation_pose(self):
        early = self.engine.run(self.case(10), self.planner)
        late = self.engine.run(self.case(30), self.planner)
        def prefix(row):
            return [clip_phase(self.scene, e, 10) for e in row['events'] if e['t0'] < 10]
        self.assertEqual(prefix(early), prefix(late))
        self.assertTrue(all(c['effective_s'] >= 30 for c in late['commands'] if c['source'] == 'reschedule'))
        reset = next(e for e in early['events'] if e.get('reason') == 'cancel_unstarted_task' and e['vehicle'] == 'V2')
        self.assertEqual(reset['t0'], 10)
        self.assertAlmostEqual(reset['gimbal0_deg'][0], 125.049465500948, places=9)
        self.assertAlmostEqual(reset['t1'], 14.168315516698, places=9)

    def test_in_transit_handoff_and_preserved_occupancy(self):
        s = self.case()
        retained = self.engine.run(s)
        online = self.engine.run(s, self.planner)
        self.assertEqual((retained['k'], online['k']), (2, 4))
        self.assertEqual(online['decision']['mode'], 'replace_pending_queues')
        new_drive = next(e for e in online['events'] if e['vehicle'] == 'V2' and e['t0'] == 5 and e['kind'] == 'drive')
        self.assertLess(math.dist(new_drive['path'][0], retained['state_after_disturbance']['V2']['position_xy_m']), 1e-9)
        locked_end = next(e['t1'] for e in retained['events'] if e['vehicle'] == 'V5' and e['target'] == 'T1' and e['kind'] == 'reset')
        self.assertTrue(all(e['t0'] >= locked_end for e in online['events'] if e['vehicle'] == 'V5' and e['target'] == 'T4'))
        self.assertAlmostEqual(online['jobs']['T4']['start_s'], 10.692800687876, places=9)
        self.assertEqual(outcome(online['events'], online['jobs'], 5, [0,60])['t_last_s'], online['t_last_s'])

    def test_ties_and_outages_through_window_keep_original_events(self):
        for s in (self.case(None, None, None), self.case(10, None, None), self.case(30, 'V5', None), self.case(70)):
            a, b = self.engine.run(s), self.engine.run(s, self.planner)
            self.assertEqual(a['events'], b['events'])
            self.assertEqual(a['commands'], b['commands'])
            self.assertEqual(a['jobs'], b['jobs'])

    def test_cli_runner_uses_alternative_plan_even_when_it_repairs_all_jobs(self):
        ready = run_baselines(ROOT / 'configs/b/q22-baselines.json', 'ready')
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            plan, manifest = root / 'plan.json', root / 'manifest.json'
            plan.write_text(json.dumps(ready['plan']))
            manifest.write_text(json.dumps({'scenarios': [self.case(None, 'V5', None)]}))
            rows = []
            for action, settings in [('retain', None), ('reschedule', self.settings)]:
                path, _ = run_q4(self.cfg, manifest, plan, root / f'{action}.jsonl', planner_config=settings)
                rows.append(json.loads(path.read_text()))
            self.assertEqual(rows[0]['provenance']['plan_sha256'], rows[1]['provenance']['plan_sha256'])
            self.assertEqual(rows[1]['provenance']['plan_method'], 'ready')
            self.assertEqual((rows[0]['k'], rows[1]['k']), (2, 3))


if __name__ == '__main__':
    unittest.main()
