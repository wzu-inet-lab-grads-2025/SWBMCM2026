"""Compare the production entry points to frozen Q2(2) allocations and hand cases."""

from dataclasses import replace
import json
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.q2.baselines import generate_plan
from src.run import load_scene_config, run_baselines
from src.shared.plan import Plan
from src.shared.timeline import replay

CONFIG = ROOT / 'configs/b/q22-baselines.json'


class BaselineTests(unittest.TestCase):
    def test_all_archived_choices_and_independent_waits(self):
        result = run_baselines(CONFIG)
        reference = json.loads((ROOT / 'experiments/b/snapshots/q2_comparison/common_baselines.json').read_text())
        self.assertEqual(result['config']['stations'], reference['stations'])
        expected = {(tuple(r['order']), r['rule']): r for r in reference['rows']}
        self.assertEqual(len(result['rows']), 30)
        _, scene, _ = load_scene_config(CONFIG)
        for row in result['rows']:
            archived = expected.pop((tuple(row['order']), row['rule']))
            self.assertAlmostEqual(row['latest_s'], archived['latest_s'], places=10)
            positions, free = scene.homes.copy(), dict.fromkeys(scene.homes, 0.)
            for job, old in zip(row['jobs'], archived['events'], strict=True):
                self.assertEqual(job['target'], old['target'])
                self.assertEqual(job['primary_ties'], old['ties'])
                self.assertAlmostEqual(job['start_s'], old['start_s'], places=10)
                tx, ty, tz = scene.targets[old['target']]
                for side, leg in enumerate(job['legs'], 1):
                    station = tuple(old[f'station_{side}'])
                    vehicle = old[f'vehicle_{side}']
                    self.assertEqual((leg['vehicle'], leg['station']), (vehicle, station))
                    dx, dy = tx - station[0], ty - station[1]
                    tilt = math.atan2(tz - scene.camera_height, math.hypot(dx, dy))
                    slew = math.degrees(max(tilt, abs(math.atan2(dy, dx)))) / scene.axis_speed
                    distance = math.dist(positions[vehicle], station)
                    ready = free[vehicle] + distance / scene.speed + slew
                    self.assertAlmostEqual(leg['drive_m'], distance, places=10)
                    self.assertAlmostEqual(leg['ready_s'], ready, places=10)
                    self.assertAlmostEqual(leg['wait_s'], old['start_s'] - ready, places=10)
                    positions[vehicle] = station
                    free[vehicle] = old['start_s'] + scene.observation_s + slew
        self.assertFalse(expected)

    def test_distance_sum_and_bottleneck_make_different_assignments(self):
        _, base, provenance = load_scene_config(CONFIG)
        scene = replace(base, homes={'A': (49, 3), 'B': (46, 7)}, targets={'T3': base.targets['T3']})
        stations = [(49, 3), (54, 3)]
        for rule, assignment in [('sum', stations), ('max', stations[::-1])]:
            plan, _ = generate_plan(scene, provenance['scene_sha256'], stations, ['T3'], rule)
            self.assertEqual([leg.station for leg in plan.jobs[0].legs], assignment)
        with self.assertRaisesRegex(ValueError, 'no feasible pair'):
            generate_plan(scene, provenance['scene_sha256'], [(25, 20)], ['T3'], 'ready')

    def test_generated_plan_can_be_replayed_without_the_planner(self):
        report = run_baselines(CONFIG, 'ready', ['T2', 'T3', 'T1'])
        config, scene, provenance = load_scene_config(CONFIG)
        plan = Plan.load(json.loads(json.dumps(report)), 'ready', provenance['scene_sha256'])
        self.assertEqual(replay(scene, plan, config['record_until_s']), report['events'])
        self.assertEqual(report['allocation']['order'], ['T2', 'T3', 'T1'])


if __name__ == '__main__':
    unittest.main()
