"""Small synthetic cases for C clock, validity and next-label preprocessing.

AI-assisted checks, OpenCode / OpenAI GPT family (GPT-6.2 Astra routing
group), 2026-11-20. Exact model release date unavailable.
"""

import csv
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'experiments'))
from c_q1_quality_transitions import transitions
from c_q2_windows import qualified_runs, wrist_inputs


def write_table(path, header, data):
    with path.open('w', newline='') as out:
        writer = csv.writer(out)
        writer.writerow(header)
        writer.writerows(data)


class PreprocessingChecks(unittest.TestCase):
    def test_quality_boundaries(self):
        origin = 1_700_000_000_000_000_000
        rows = [{'header.ts': origin + delta, 'is_bad': flag}
                for delta, flag in [(0, 'False'), (50_000_000, 'True'), (100_000_001, 'False'),
                                    (100_000_002, 'False')]]
        result = transitions(rows)
        self.assertEqual([result[k] for k in ('good_bad', 'bad_good', 'good_good', 'gaps')], [1, 0, 1, 1])
        with self.assertRaises(ValueError):
            transitions(rows + rows[-1:])
        with self.assertRaises(ValueError):
            transitions([rows[0], rows[0]])
        with self.assertRaises(ValueError):
            transitions([dict(rows[0], is_bad='unknown')])

    def test_wrist_coordinates_and_joint_mask(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            tables = folder / 'tables'
            tables.mkdir()
            times = np.arange(6, dtype=np.int64) + 1_700_000_000_000_000_000
            cols = ['header.ts', *[f'transforms[0].pos.{c}' for c in 'xyz']]
            quality = ['False', 'False', 'True', 'False', 'False', 'False']
            write_table(tables / 'annotation_bad_frame_pose_hand.csv', ['header.ts', 'is_bad'], zip(times, quality))
            for side in ('left', 'right'):
                xyz = [[0, 1, 2] for _ in times]  # One zero component is a valid XYZ.
                if side == 'left':
                    xyz[3] = [0, 0, 0]
                else:
                    xyz[4] = ['', 1, 2]
                path = tables / f'pose_{side}_hand.csv'
                write_table(path, cols, [[t, *p] for t, p in zip(times, xyz)])
            clock, _, _, masks = wrist_inputs(folder)
            np.testing.assert_array_equal(times, clock)
            self.assertEqual([list(r) for r in qualified_runs(clock, masks['both'])], [[0, 1], [5]])
            self.assertTrue(masks['right'][3] and masks['left'][4])
            write_table(path, cols, [[t + 1, *p] for t, p in zip(times, xyz)])
            with self.assertRaisesRegex(ValueError, 'clock'):
                wrist_inputs(folder)
            write_table(path, cols, [[f'{t}.5', *p] for t, p in zip(times, xyz)])
            with self.assertRaises(ValueError):
                wrist_inputs(folder)


if __name__ == '__main__':
    unittest.main()
