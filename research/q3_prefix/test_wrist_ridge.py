import math
import unittest
from collections import Counter
from copy import deepcopy

import numpy as np

from .data import TIERS
from .wrist_ridge import WristRidge, motion


def example(family, times, xs, flags=None):
    flags = flags if flags is not None else [None] * len(times)
    return dict(
        family_id=family,
        rows=[
            {
                "timestamp_s": t,
                "hand_quality_flags": q,
                "left_hand": {"positions": [[x, 0, 0]]},
                "right_hand": {"positions": [[x + 20, 5, 8]]},
            }
            for t, x, q in zip(times, xs, flags)
        ],
    )


class WristTests(unittest.TestCase):
    def test_observed_pairs_and_unknown_vs_bad(self):
        times = [0, 0.1, 0.2, 0.4, 0.6]
        ex = example(
            "P",
            times,
            [0, 2, 101, 60, 66],
            [None, None, {"is_bad": True, "reasons": ["left:occlusion"]}, None, None],
        )
        counts = Counter()
        a = motion(ex, "review1", counts)
        # Left path consists of two valid edges, 2+6 units over .1+.2 seconds.
        np.testing.assert_allclose(a, [66, 8 / 0.3, 66, (2 + 99 + 41 + 6) / 0.6])
        self.assertEqual(counts["left_bad"], 1)
        self.assertEqual(counts["right_unknown"], 1)
        self.assertEqual(counts["demo_unlisted_samples"], 4)
        # Translation leaves all four relative features unchanged.
        shifted = deepcopy(ex)
        for r in shifted["rows"]:
            for name in ("left_hand", "right_hand"):
                r[name]["positions"][0][0] += 190
        np.testing.assert_allclose(motion(shifted, "review1"), a)
        ex = example(
            "A",
            [0, 0.1, 0.2],
            [0, 8, 3],
            [None, {"left_bad_reason": "occlusion", "right_bad_reason": None}, None],
        )
        disp, speed, *_ = motion(ex, "free")
        self.assertEqual(disp, 3)
        self.assertTrue(math.isnan(speed))

    def test_unobserved_suffix_changes_neither_features_nor_prediction(self):
        times = [0, 0.1, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2]
        train = [
            example(f, times, [k * t * t for t in times])
            for k, f in enumerate("PALP", 1)
        ]
        labels = [2, 4, 3, 7]
        for tier, width in TIERS.items():
            with self.subTest(tier=tier):
                m = WristRidge(tier)
                m.fit(train, labels)
                old = deepcopy(train[0])
                new = deepcopy(old)
                for row in new["rows"]:
                    if width is None or row["timestamp_s"] > width:
                        row["left_hand"]["positions"][0] = [100000, 100000, 100000]
                        row["hand_quality_flags"] = {
                            "is_bad": True,
                            "reasons": ["right:occlusion"],
                        }
                self.assertEqual(motion(old, tier), motion(new, tier))
                self.assertEqual(m.predict([old]), m.predict([new]))
                if width is not None:
                    next(r for r in new["rows"] if r["timestamp_s"] == width)[
                        "left_hand"
                    ]["positions"][0][0] += 3
                    self.assertNotEqual(motion(old, tier), motion(new, tier))

    def test_missing_imputation_uses_fitted_statistics(self):
        train = [
            example("A", [0, 0.2], [0, 2]),
            example("P", [0, 0.2], [0, 4]),
            example("L", [0], [6]),
        ]
        m = WristRidge("free")
        m.fit(train, [3, 4, 2])
        mean = m.mean.copy()
        scale = m.scale.copy()
        z = m.design(
            [example("P", [0], [10]), example("L", [0, 0.2], [0, 2000])], fitting=False
        )
        np.testing.assert_array_equal(m.mean, mean)
        np.testing.assert_array_equal(m.scale, scale)
        np.testing.assert_allclose(mean, [3, 15, 3, 15])
        np.testing.assert_allclose(z[0, 3:7], 0)
        np.testing.assert_array_equal(z[0, -4:], 1)
