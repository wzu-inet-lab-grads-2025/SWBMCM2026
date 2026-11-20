"""Build a private, paired duration dataset from train and validation only."""

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import tempfile
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from experiments.q3_replay.data import RAW, RULES, csv_samples, demo_samples, records

TIERS = {"family": None, "free": 0.2, "review1": 0.6, "review2": 1.0}
SPLITS = ("train", "validation")
DEFAULT_DB = Path("runs/q3_prefix/private.sqlite")
POSE_KEYS = {
    "left_hand": ("positions", "orientations_xyzw"),
    "right_hand": ("positions", "orientations_xyzw"),
    "head_pose": ("position", "orientation_xyzw"),
    "left_head_camera_pose": ("position", "orientation_xyzw"),
    "right_head_camera_pose": ("position", "orientation_xyzw"),
}


def model_input(family, rows, tier):
    """One unengineered model example. Metadata and labels are not arguments."""
    view = {"family_id": family}
    width = TIERS[tier]
    if width is None:
        return view
    view["rows"] = []
    for row in rows:
        if not 0 <= row["timestamp_s"] <= width:
            continue
        sample = {"timestamp_s": row["timestamp_s"]}
        for name, keys in POSE_KEYS.items():
            if name in row:
                sample[name] = {k: row[name][k] for k in keys}
        if "hand_quality_flags" in row:
            flags = row["hand_quality_flags"]
            sample["hand_quality_flags"] = (
                None
                if flags is None
                else {
                    k: flags[k]
                    for k in (
                        "is_bad",
                        "reasons",
                        "left_bad_reason",
                        "right_bad_reason",
                    )
                    if k in flags
                }
            )
        view["rows"].append(sample)
    return view


def open_db(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def create_tables(db):
    db.executescript("""
        CREATE TABLE pairing (pair_key TEXT PRIMARY KEY, split TEXT, env TEXT, record TEXT, family TEXT);
        CREATE TABLE labels (pair_key TEXT PRIMARY KEY, duration_s REAL);
        CREATE TABLE inputs (pair_key TEXT, tier TEXT, payload TEXT, PRIMARY KEY(pair_key,tier));
    """)


def prepare(db, coverage):
    create_tables(db)
    split_index = {
        r["episode_uuid"]: r
        for r in records(RULES / "record_split.csv")
        if r["split"] in SPLITS
    }
    environments = {
        s: {r["environment_id"] for r in split_index.values() if r["split"] == s}
        for s in SPLITS
    }
    if environments["train"] & environments["validation"]:
        raise ValueError("environment overlap between train and validation")
    record_alias = {uid: f"R{i:03}" for i, uid in enumerate(sorted(split_index), 1)}
    seen = set()
    for u in records(RULES / "source_segment_index.csv"):
        # The shared index mixes splits. Discard the test row before any label/endpoint access.
        if u["split"] not in SPLITS:
            continue
        uid = u["episode_uuid"]
        meta = split_index[uid]
        if any(meta[k] != u[k] for k in ("split", "environment_id", "dataset")):
            raise ValueError("source/split index disagreement")
        if u["unit_id"] in seen:
            raise ValueError("duplicate annotated unit")
        seen.add(u["unit_id"])
        split, env, record, family = (
            u["split"],
            u["environment_id"],
            record_alias[uid],
            u["family_id"],
        )
        group = coverage[split, env, record, family]
        group["indexed"] += 1
        if u["eligible"] != "1":
            group["excluded:" + u["exclusion_reason"]] += 1
            continue
        group["eligible"] += 1
        group["family"] += 1
        reader = demo_samples if u["dataset"] == "EgoDemo" else csv_samples
        try:
            rows = reader(RAW / u["dataset"] / u["sample"], u)
        except FileNotFoundError:
            group["excluded:missing_source_file"] += 1
            continue
        views = {tier: model_input(family, rows, tier) for tier in TIERS}
        gaps = []
        last_count = 0
        for tier in ("free", "review1", "review2"):
            count = len(views[tier]["rows"])
            if count:
                group[tier] += 1
            else:
                gaps.append(tier)
            if tier != "free" and count == last_count:
                group["no_extra:" + tier] += 1
            last_count = count
        if gaps:
            group["excluded:empty_" + "+".join(gaps)] += 1
            continue
        group["paired"] += 1
        key = hashlib.sha256(("q3-prefix-v1/" + u["unit_id"]).encode()).hexdigest()
        label = float(Decimal(u["end_s"]) - Decimal(u["start_s"]))
        db.execute(
            "INSERT INTO pairing VALUES (?,?,?,?,?)", (key, split, env, record, family)
        )
        db.execute("INSERT INTO labels VALUES (?,?)", (key, label))
        db.executemany(
            "INSERT INTO inputs VALUES (?,?,?)",
            (
                (key, tier, json.dumps(view, separators=(",", ":"), allow_nan=False))
                for tier, view in views.items()
            ),
        )


def coverage_rows(coverage):
    groups = defaultdict(list)
    for (split, env, record, family), counts in coverage.items():
        for by, value in [
            ("all", "all"),
            ("environment", env),
            ("record", record),
            ("family", family),
        ]:
            groups[split, by, value].append((env, record, counts))
    for (split, by, value), parts in sorted(groups.items()):
        total = sum((c for _, _, c in parts), Counter())
        yield dict(
            split=split,
            group=by,
            value=value,
            environment=parts[0][0] if by == "record" else "",
            records=len({r for _, r, _ in parts}),
            paired_records=len({r for _, r, c in parts if c["paired"]}),
            environments=len({e for e, _, _ in parts}),
            paired_environments=len({e for e, _, c in parts if c["paired"]}),
            **{k: total[k] for k in ("indexed", "eligible", *TIERS, "paired")},
            reasons=json.dumps({k: v for k, v in sorted(total.items()) if ":" in k}),
        )


def write_csv(path, rows):
    path = Path(path)
    rows = iter(rows)
    first = next(rows)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            out = csv.DictWriter(f, fieldnames=list(first), lineterminator="\n")
            out.writeheader()
            out.writerow(first)
            out.writerows(rows)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--coverage", type=Path, required=True, help="Aggregate counts only"
    )
    args = parser.parse_args()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=args.db.parent)
    os.close(fd)
    coverage = defaultdict(Counter)
    try:
        db = sqlite3.connect(tmp)
        try:
            with db:
                prepare(db, coverage)
        finally:
            db.close()
        os.replace(tmp, args.db)
    finally:
        Path(tmp).unlink(missing_ok=True)
    summary = list(coverage_rows(coverage))
    write_csv(args.coverage, summary)
    for r in summary:
        if r["group"] == "all":
            print(r)


if __name__ == "__main__":
    main()
