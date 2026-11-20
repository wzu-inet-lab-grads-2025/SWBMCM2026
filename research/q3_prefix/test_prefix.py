"""Synthetic regression witnesses for the model boundary and paired scoring."""

import json
import sqlite3
import unittest
from collections import Counter, defaultdict
from copy import deepcopy
from unittest.mock import patch

from .data import TIERS, coverage_rows, create_tables, model_input, prepare
from .evaluate import family_median_predictions, model_predictions, summarize


def sample(t):
    return {
        "timestamp_s": t,
        "head_pose": {"position": [t, 0, 0], "orientation_xyzw": [0, 0, 0, 1]},
        "hand_quality_flags": {"is_bad": False, "reasons": []},
    }


class PrefixTests(unittest.TestCase):
    def test_split_gate_and_shortest_prefix_gap(self):
        # The test row deliberately has no endpoint, label, UUID, or accessible source file.
        meta = dict(
            split="train", episode_uuid="r1", environment_id="e1", dataset="EgoPro"
        )
        unit = dict(
            meta,
            sample="synthetic",
            unit_id="u1",
            family_id="L",
            eligible="1",
            start_s="0",
            end_s="3",
        )
        tables = {
            "record_split.csv": [meta],
            "source_segment_index.csv": [{"split": "test"}, unit],
        }
        with sqlite3.connect(":memory:") as db:
            coverage = defaultdict(Counter)
            with (
                patch(
                    "research.q3_prefix.data.records",
                    side_effect=lambda p: tables[p.name],
                ),
                patch(
                    "research.q3_prefix.data.csv_samples",
                    return_value=[sample(0.4), sample(0.8)],
                ) as reader,
            ):
                prepare(db, coverage)
            reader.assert_called_once()
            self.assertEqual(db.execute("SELECT count(*) FROM labels").fetchone()[0], 0)
            aggregate = next(r for r in coverage_rows(coverage) if r["group"] == "all")
            self.assertEqual(
                [aggregate[k] for k in (*TIERS, "paired")], [1, 0, 1, 1, 0]
            )
            self.assertIn("excluded:empty_free", aggregate["reasons"])

    def test_future_mutations_and_hidden_columns(self):
        rows = [sample(t) for t in (0, 0.2, 0.21, 0.6, 0.61, 1.0, 1.01, 5.0)]
        for tier, width in TIERS.items():
            with self.subTest(tier=tier):
                old = model_input("P", rows, tier)
                amended = deepcopy(rows)
                for r in amended:
                    r.update(
                        end_s=77,
                        source_id="hidden",
                        whole_segment_mean=222,
                        total_frames=999,
                    )
                    r["head_pose"]["source_id"] = "hidden"
                    r["hand_quality_flags"]["whole_bad_fraction"] = 0.5
                    if width is None or r["timestamp_s"] > width:
                        r["head_pose"]["position"][0] = 12345
                        r["hand_quality_flags"]["is_bad"] = True
                amended.append(sample(77))
                self.assertEqual(old, model_input("P", amended, tier))
                if width is None:
                    self.assertEqual(old, {"family_id": "P"})
                else:
                    self.assertEqual(old["rows"][-1]["timestamp_s"], width)
                    # Positive control: an authorized change exactly at the endpoint must survive.
                    next(r for r in amended if r["timestamp_s"] == width)["head_pose"][
                        "position"
                    ][0] = 999
                    self.assertNotEqual(old, model_input("P", amended, tier))

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        create_tables(self.db)
        for key, split, y, family in [
            ("fit", "train", 5, "A"),
            ("v1", "validation", 2, "P"),
            ("v2", "validation", 8, "L"),
        ]:
            self.db.execute(
                "INSERT INTO pairing VALUES (?,?,?,?,?)",
                (key, split, "env" + key, "rec" + key, family),
            )
            self.db.execute("INSERT INTO labels VALUES (?,?)", (key, y))
            for tier in TIERS:
                payload = model_input(family, [sample(0)], tier)
                self.db.execute(
                    "INSERT INTO inputs VALUES (?,?,?)",
                    (key, tier, json.dumps(payload)),
                )

    def test_family_baseline_uses_only_training_medians(self):
        for key, family, y in [("p1", "P", 1), ("p2", "P", 5), ("l", "L", 9)]:
            self.db.execute(
                "INSERT INTO pairing VALUES (?,?,?,?,?)",
                (key, "train", "env", "rec", family),
            )
            self.db.execute("INSERT INTO labels VALUES (?,?)", (key, y))
            self.db.execute(
                "INSERT INTO inputs VALUES (?,?,?)",
                (key, "family", json.dumps({"family_id": family})),
            )
        predictions = family_median_predictions(self.db)
        self.assertEqual([row["pred_s"] for row in predictions], [3, 9])
        result = list(summarize(self.db, predictions, ("family",)))
        self.assertEqual({r["tier"] for r in result}, {"family"})
        self.assertEqual(next(r["mae_s"] for r in result if r["group"] == "all"), 1)
        self.db.execute("UPDATE labels SET duration_s=100 WHERE pair_key='v1'")
        self.assertEqual(family_median_predictions(self.db), predictions)

    def test_fit_and_predict_only_receive_allowed_arguments(self):
        calls = []

        class Spy:
            def fit(self, x, y):
                calls.append(("fit", x, y))

            def predict(self, x):
                calls.append(("predict", x))
                return [
                    {"pred_s": 3} for _ in x
                ]  # Interface test constant on fabricated samples only.

        predictions = model_predictions(self.db, lambda tier: Spy())
        self.assertEqual(len(predictions), 8)
        for i, tier in enumerate(TIERS):
            _, fit, y = calls[2 * i]
            _, valid = calls[2 * i + 1]
            self.assertEqual(y, [5])
            self.assertEqual([x["family_id"] for x in fit + valid], ["A", "P", "L"])
            self.assertTrue(
                all(
                    set(x)
                    == ({"family_id"} if tier == "family" else {"family_id", "rows"})
                    for x in fit + valid
                )
            )

    def test_score_by_pair_not_file_order(self):
        preds = []
        for tier, estimates in zip(TIERS, [(4, 4), (3, 6), (2, 7), (2, 8)]):
            for key, val in zip(("v1", "v2"), estimates):
                preds.append(
                    dict(
                        pair_key=key,
                        tier=tier,
                        pred_s=val,
                        lower_s=val - 1,
                        upper_s=val + 1,
                    )
                )
        rows = list(summarize(self.db, reversed(preds)))
        all_rows = [r for r in rows if r["group"] == "all"]
        self.assertEqual([r["mae_s"] for r in all_rows], [3, 1.5, 0.5, 0])
        self.assertEqual([r["paired_gain_s"] for r in all_rows], ["", 1.5, 1, 0.5])
        self.assertEqual([r["coverage"] for r in all_rows], [0, 0.5, 1, 1])
        self.assertTrue(all(r["mean_width_s"] == 2 for r in rows))
        l_free = next(
            r
            for r in rows
            if (r["group"], r["value"], r["tier"]) == ("family", "L", "free")
        )
        self.assertEqual((l_free["n"], l_free["paired_gain_s"]), (1, 2))
        for invalid in (
            preds[1:],
            preds + [preds[0]],
            [dict(preds[0], pred_s="nan")] + preds[1:],
        ):
            with self.assertRaises(ValueError):
                list(summarize(self.db, invalid))
