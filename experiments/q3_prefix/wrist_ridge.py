"""Paired wrist-prefix diagnostic for raw annotated duration. Fixed ridge alpha=1."""

import argparse
import math
from collections import Counter
from pathlib import Path

import numpy as np

from .data import DEFAULT_DB, TIERS, open_db, write_csv
from .evaluate import model_predictions, summarize


FAMILIES = ("P", "A", "L")
SIDES = ("left", "right")
FEATURES = tuple(
    f"{side}_{name}" for side in SIDES for name in ("displacement", "speed")
)


def wrist_quality(row, side):
    """Side-specific bad flags take precedence. Sparse unlisted is explicitly unknown."""
    if "hand_quality_flags" not in row:
        return "unknown"
    flags = row["hand_quality_flags"]
    if flags is None:
        return "demo_unlisted"
    if "is_bad" not in flags:
        return "bad" if flags.get(f"{side}_bad_reason") else "unknown"
    if not flags["is_bad"]:
        return "normal"
    tagged = {r.partition(":")[0] for r in flags.get("reasons", [])}
    if side in tagged or not tagged or tagged - set(SIDES):
        return "bad"
    return "unknown"  # Only the opposite hand was explicitly marked bad.


def wrist_point(row, side):
    positions = row.get(f"{side}_hand", {}).get("positions")
    if not positions:
        return None
    xyz = positions[0]
    if (
        xyz is None
        or len(xyz) != 3
        or any(v is None or not math.isfinite(v) for v in xyz)
    ):
        return None
    return xyz


def motion(example, tier, counts=None):
    """Displacement and distance per observed second. Only accept this tier's window."""
    width = TIERS[tier]
    if width is None:
        return []
    counts = counts if counts is not None else Counter()
    rows = [r for r in example["rows"] if 0 <= r["timestamp_s"] <= width]
    times = [r["timestamp_s"] for r in rows]
    if any(a >= b for a, b in zip(times, times[1:])):
        raise ValueError("Prefix timestamps must increase in their original order")
    counts["samples"] += len(rows)
    # The data adapter emits None only for an unlisted Demo frame; listed Demo
    # frames have per-hand reasons, while CSV frames have an explicit is_bad flag.
    demo_rows = [
        r
        for r in rows
        if "hand_quality_flags" in r
        and (r["hand_quality_flags"] is None or "is_bad" not in r["hand_quality_flags"])
    ]
    counts["demo_segments"] += bool(demo_rows)
    counts["demo_samples"] += len(demo_rows)
    unlisted = sum(r["hand_quality_flags"] is None for r in demo_rows)
    counts["demo_unlisted_samples"] += unlisted
    counts["segments_with_demo_unlisted"] += unlisted > 0
    values = []
    for side in SIDES:
        points = []
        for row in rows:
            quality = wrist_quality(row, side)
            counts[f"{side}_{quality}"] += 1
            p = None if quality == "bad" else wrist_point(row, side)
            if quality == "demo_unlisted" and p is not None:
                counts[f"{side}_usable_demo_unlisted"] += 1
            points.append(p)
        usable = [p for p in points if p is not None]
        displacement = (
            math.dist(usable[0], usable[-1]) if len(usable) >= 2 else math.nan
        )
        distance = seconds = 0.0
        for i, (a, b) in enumerate(zip(points, points[1:])):
            if a is not None and b is not None:
                distance += math.dist(a, b)
                seconds += times[i + 1] - times[i]
        values.extend((displacement, distance / seconds if seconds > 0 else math.nan))
    return values


class WristRidge:
    def __init__(self, tier):
        self.tier = tier
        self.coverage = {}

    def design(self, examples, *, fitting):
        counts = Counter(segments=len(examples))
        raw = np.array([motion(x, self.tier, counts) for x in examples], dtype=float)
        missing = np.isnan(raw)
        for i, name in enumerate(FEATURES[: raw.shape[1]]):
            counts[f"missing_{name}"] = int(missing[:, i].sum())
        self.coverage["train" if fitting else "validation"] = counts
        if fitting:
            good = (~missing).sum(axis=0)
            self.mean = np.nansum(raw, axis=0) / np.maximum(1, good)
            filled = np.where(missing, self.mean, raw)
            scale = filled.std(axis=0)
            self.scale = np.where(scale > 0, scale, 1.0)
        standardized = (np.where(missing, self.mean, raw) - self.mean) / self.scale
        family = np.zeros((len(examples), len(FAMILIES)))
        for i, x in enumerate(examples):
            family[i, FAMILIES.index(x["family_id"])] = 1
        return np.column_stack((family, standardized, missing))

    def fit(self, X, y):
        matrix = self.design(X, fitting=True)
        self.x_mean = matrix.mean(axis=0)
        self.y_mean = np.mean(y)
        centered = matrix - self.x_mean
        # Minimize sum of squared residuals + ||coef||², with a free intercept.
        self.coef = np.linalg.solve(
            centered.T @ centered + np.eye(centered.shape[1]),
            centered.T @ (np.asarray(y) - self.y_mean),
        )

    def predict(self, X):
        matrix = self.design(X, fitting=False)
        return [
            {"pred_s": float(v)}
            for v in self.y_mean + (matrix - self.x_mean) @ self.coef
        ]


def coverage_rows(models):
    for tier, model in models.items():
        if TIERS[tier] is None:
            continue
        for split, c in model.coverage.items():
            row = dict(
                tier=tier, split=split, segments=c["segments"], samples=c["samples"]
            )
            for name in FEATURES:
                n = c[f"missing_{name}"]
                row[f"missing_{name}"] = n
                row[f"missing_fraction_{name}"] = n / c["segments"]
            for k in (
                "demo_segments",
                "demo_samples",
                "segments_with_demo_unlisted",
                "demo_unlisted_samples",
            ):
                row[k] = c[k]
            for side in SIDES:
                for quality in (
                    "bad",
                    "normal",
                    "unknown",
                    "demo_unlisted",
                    "usable_demo_unlisted",
                ):
                    row[f"{side}_{quality}"] = c[f"{side}_{quality}"]
            yield row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=Path("runs/q3_prefix"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    models = {}

    def factory(tier):
        models[tier] = WristRidge(tier)
        return models[tier]

    db = open_db(args.db)
    try:
        predictions = model_predictions(db, factory)
        write_csv(args.out / "ridge_validation.csv", summarize(db, predictions))
        write_csv(args.out / "ridge_coverage.csv", coverage_rows(models))
    finally:
        db.close()


if __name__ == "__main__":
    main()
