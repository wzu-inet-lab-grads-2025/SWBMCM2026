"""Validation-only paired scoring, with an optional team-supplied model factory."""

import argparse
import csv
import importlib
import json
import math
from collections import defaultdict
from statistics import mean, median

from .data import DEFAULT_DB, TIERS, open_db, write_csv


def inputs(db, split, tier):
    query = """SELECT i.pair_key, i.payload FROM inputs i JOIN pairing p USING(pair_key)
               WHERE p.split=? AND i.tier=? ORDER BY i.pair_key"""
    return [(key, json.loads(text)) for key, text in db.execute(query, (split, tier))]


def training_labels(db):
    return dict(
        db.execute("""SELECT pair_key, duration_s FROM labels JOIN pairing USING(pair_key)
                              WHERE split='train' """)
    )


def family_median_predictions(db):
    labels = training_labels(db)
    durations = defaultdict(list)
    for key, example in inputs(db, "train", "family"):
        durations[example["family_id"]].append(labels[key])
    medians = {family: median(values) for family, values in durations.items()}
    return [
        dict(pair_key=key, tier="family", pred_s=medians[example["family_id"]])
        for key, example in inputs(db, "validation", "family")
    ]


def model_predictions(db, factory):
    """factory(tier) -> fresh object with fit(X, y) and predict(X) -> prediction dicts."""
    labels = training_labels(db)
    predictions = []
    for tier in TIERS:
        model = factory(tier)
        train = inputs(db, "train", tier)
        model.fit([x for _, x in train], [labels[k] for k, _ in train])
        valid = inputs(db, "validation", tier)
        output = list(model.predict([x for _, x in valid]))
        if len(output) != len(valid):
            raise ValueError(
                f"{tier}: expected {len(valid)} validation predictions, got {len(output)}"
            )
        for (key, _), pred in zip(valid, output):
            if set(pred) - {"pred_s", "lower_s", "upper_s"}:
                raise ValueError("unexpected model output fields")
            predictions.append(dict(pair_key=key, tier=tier, **pred))
    return predictions


def finite(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite prediction or interval")
    return number


def indexed_predictions(rows, keys, tiers=TIERS):
    result = {}
    for row in rows:
        pair = row["pair_key"], row["tier"]
        if pair in result or pair[0] not in keys or pair[1] not in tiers:
            raise ValueError("duplicate, unknown or non-validation prediction key/tier")
        pred = finite(row["pred_s"])
        lo, hi = row.get("lower_s"), row.get("upper_s")
        interval = None
        if lo not in ("", None) or hi not in ("", None):
            if lo in ("", None) or hi in ("", None):
                raise ValueError("both interval bounds are required")
            interval = finite(lo), finite(hi)
            if interval[0] > interval[1]:
                raise ValueError("reversed interval")
        result[pair] = pred, interval
    for tier in tiers:
        missing = sum((key, tier) not in result for key in keys)
        if missing:
            raise ValueError(
                f"{tier}: missing {missing}/{len(keys)} paired predictions"
            )
    return result


def summarize(db, prediction_rows=None, tiers=TIERS):
    meta = {
        key: (env, record, family)
        for key, env, record, family in db.execute(
            "SELECT pair_key,env,record,family FROM pairing WHERE split='validation' ORDER BY pair_key"
        )
    }
    groups = defaultdict(list)
    for key, (env, _, family) in meta.items():
        for group in [("all", "all"), ("environment", env), ("family", family)]:
            groups[group].append(key)
    preds = None
    if prediction_rows is not None:
        preds = indexed_predictions(prediction_rows, meta, tiers)
        truth = dict(
            db.execute("""SELECT pair_key,duration_s FROM labels JOIN pairing USING(pair_key)
                                   WHERE split='validation' """)
        )
    for (by, name), keys in sorted(groups.items()):
        previous = None
        for tier in tiers:
            row = dict(
                group=by,
                value=name,
                tier=tier,
                compared_with=previous or "",
                n=len(keys),
                records=len({meta[k][1] for k in keys}),
                environments=len({meta[k][0] for k in keys}),
                mae_s="",
                paired_gain_s="",
                interval_n="",
                coverage="",
                mean_width_s="",
            )
            if preds is not None:

                def error(key, which):
                    return abs(preds[key, which][0] - truth[key])

                row["mae_s"] = mean(error(k, tier) for k in keys)
                if previous:
                    row["paired_gain_s"] = mean(
                        error(k, previous) - error(k, tier) for k in keys
                    )
                intervals = [
                    (truth[k], preds[k, tier][1])
                    for k in keys
                    if preds[k, tier][1] is not None
                ]
                row["interval_n"] = len(intervals)
                if intervals:
                    row["coverage"] = mean(lo <= y <= hi for y, (lo, hi) in intervals)
                    row["mean_width_s"] = mean(hi - lo for _, (lo, hi) in intervals)
            yield row
            previous = tier


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--template", action="store_true")
    source.add_argument(
        "--family-median",
        action="store_true",
        help="Score only the training-set median per family",
    )
    source.add_argument("--model", metavar="module:factory")
    source.add_argument("--predictions", metavar="CSV")
    parser.add_argument("--summary", required=True, help="Aggregate output CSV")
    args = parser.parse_args()
    db = open_db(args.db)
    try:
        predictions = None
        tiers = TIERS
        if args.family_median:
            tiers = ("family",)
            predictions = family_median_predictions(db)
        if args.model:
            module, attr = args.model.split(":")
            predictions = model_predictions(
                db, getattr(importlib.import_module(module), attr)
            )
        if args.predictions:
            with open(args.predictions, encoding="utf-8-sig", newline="") as f:
                predictions = list(csv.DictReader(f))
        write_csv(args.summary, summarize(db, predictions, tiers))
    finally:
        db.close()


if __name__ == "__main__":
    main()
