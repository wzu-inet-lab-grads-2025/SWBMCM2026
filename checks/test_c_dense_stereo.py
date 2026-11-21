"""Synthetic checks for the paired-patch statistic; no original recordings used.

AI-assisted audit, OpenCode, OpenAI GPT family, GPT-6.2 Astra routing group,
2026-11-21. Exact release date unavailable.
"""

import unittest

import numpy as np

from experiments.c_dense_stereo_patches import HEIGHT, WIDTH, compare


class StereoAppearanceChecks(unittest.TestCase):
    def test_known_horizontal_disparity_beats_vertical_control(self):
        rng = np.random.default_rng(491)
        left = rng.integers(0, 256, (2,HEIGHT,WIDTH), dtype=np.uint8)
        right = np.roll(left, -5, axis=2)
        lp = rng.integers(70, 220, (2,2,21,2), dtype=np.int32)
        rp = lp - [5,0]
        ok,actual,control = compare(left,right,lp,rp,np.ones((2,2,21),dtype=bool))
        self.assertTrue(ok.all())
        np.testing.assert_allclose(actual, 1., atol=1e-6)
        self.assertLess(abs(control.mean()), .08)
        # Scalar spot check of the average of the +12 and -12 controls.
        x,y = lp[1,0,5]; b = left[1,y-4:y+5,x-4:x+5].ravel()
        checks = [np.corrcoef(b, right[1,y+d-4:y+d+5,x-9:x].ravel())[0,1] for d in (-12,12)]
        self.assertAlmostEqual(control[1,0,5], np.mean(checks), places=6)

    def test_boundaries_depth_and_flat_patches(self):
        image = np.zeros((1,HEIGHT,WIDTH), dtype=np.uint8)
        left = np.full((1,2,21,2),100); right=left.copy(); mask=np.ones((1,2,21),dtype=bool)
        right[0,0,0,1]=15  # y=16 is the first eligible centre with the upward control.
        mask[0,1,0]=False
        ok,values,controls = compare(image,image,left,right,mask)
        self.assertEqual(ok.sum(),40)
        self.assertTrue(np.isnan(values).all() and np.isnan(controls).all())


if __name__ == '__main__':
    unittest.main()
