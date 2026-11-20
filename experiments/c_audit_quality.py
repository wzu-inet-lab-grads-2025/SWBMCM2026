"""Read-only, full-row C hand-trajectory / identifier / split audit.

Reuses ROOT and rows() from check_c_data.py, and its raw-metadata UUID lookup
(not folder names). Unlike inspect_record(), does not assert away anomalies or
interpret absent sparse annotations as normal. No network, video decoding,
training, original-data writes, or output files. Run from the repository root
(data paths are resolved independently of the working directory):

  /mnt/disk/zhoutianyu/miniconda3/bin/python -B experiments/c_audit_quality.py
  /mnt/disk/zhoutianyu/miniconda3/bin/python -B experiments/c_audit_quality.py --json --records

To capture output, redirect either command to a file outside data/raw/.

Default output is compact TSV + labelled JSON diagnostics. --json provides the
complete counters, definitions and optional per-record relative paths/UUIDs.
Prepared for the C attachment audit on 2026-11-20.
"""

import argparse
from collections import Counter, defaultdict
from itertools import combinations
import json
from pathlib import Path
import sys
import time
from uuid import UUID

# Importing our sibling must not create a second file (__pycache__).
sys.dont_write_bytecode = True
import numpy as np
import pandas as pd
import pyarrow
import pyarrow.parquet as pq

if __package__:
    from .check_c_data import ROOT, rows
else:
    from check_c_data import ROOT, rows


DATASETS = ("EgoDemo", "EgoPro", "EgoStandard")
SIDES = ("left", "right")
MISSING_TOKENS = {"", "nan", "na", "n/a", "null", "none", "<na>"}


def count(mask):
    return int(np.count_nonzero(mask))


def duplicate_report(values):
    counts = Counter(values)
    return {"duplicate_keys": sum(v > 1 for v in counts.values()),
            "extra_rows": sum(v - 1 for v in counts.values()),
            "examples": [str(k) for k, v in counts.items() if v > 1][:5]}


def timestamp_stats(values, nanoseconds, gap):
    """Original order, no cross-record pairs; subtract integer ns before float."""
    keys = []
    for value in values:
        try:
            key = int(str(value)) if nanoseconds else float(value)
            if not nanoseconds and not np.isfinite(key):
                key = None
        except (ValueError, TypeError, OverflowError):
            key = None
        keys.append(key)
    deltas = np.array([(b - a) / (1e9 if nanoseconds else 1)
                       for a, b in zip(keys, keys[1:])
                       if a is not None and b is not None], dtype=float)
    finite_keys = [k for k in keys if k is not None]
    stats = {"rows": len(keys), "pairs": max(0, len(keys) - 1),
             "valid_pairs": len(deltas), "invalid_ts_rows": keys.count(None),
             "reverse": count(deltas < 0), "adjacent_equal": count(deltas == 0),
             "duplicate_ts_extra": duplicate_report(finite_keys)["extra_rows"],
             "gap": count(deltas > gap), "gap_gt_100ms": count(deltas > .1),
             "dt_min_s": float(deltas.min()) if len(deltas) else None,
             "dt_max_s": float(deltas.max()) if len(deltas) else None}
    return keys, stats


def coordinate_stats(xyz, actual_missing=None, parse_bad=None, declared_length=None):
    """63 position scalars / hand-row. Zero sentinel is an entire XYZ joint."""
    n = len(xyz)
    assert xyz.shape == (n, 21, 3), xyz.shape
    missing = np.isnan(xyz) if actual_missing is None else actual_missing
    parse_bad = np.zeros_like(missing) if parse_bad is None else parse_bad
    missing_n = missing.sum(axis=(1, 2))
    zero_joint = (xyz == 0).all(axis=2)
    zero_n = zero_joint.sum(axis=1)
    finite = np.isfinite(xyz).all(axis=(1, 2))
    complete = finite & ~zero_joint.any(axis=1)
    collapsed = finite & (xyz == xyz[:, :1, :]).all(axis=(1, 2))
    stats = {"rows": n, "scalar_denominator": n * 63, "joint_denominator": n * 21,
             "missing_scalars": count(missing), "missing_full": count(missing_n == 63),
             "missing_partial": count((missing_n > 0) & (missing_n < 63)),
             "parse_bad_scalars": count(parse_bad),
             "parse_bad_rows": count(parse_bad.any(axis=(1, 2))),
             "infinite_scalars": count(np.isinf(xyz)),
             "nonfinite_rows": count(~finite),
             "zero_full": count(zero_n == 21),
             "zero_partial": count((zero_n > 0) & (zero_n < 21)),
             "zero_joints": count(zero_joint),
             "collapsed_all_joints": count(collapsed),
             "complete_finite_no_zero_joint": count(complete),
             "length_supported_rows": n if declared_length is not None else 0,
             "length_not_21": count(np.asarray(declared_length) != 21)
             if declared_length is not None else 0}
    return stats, complete


def csv_coordinates(frame):
    columns = [f"transforms[{i}].pos.{axis}" for i in range(21) for axis in "xyz"]
    raw = frame.reindex(columns=columns)
    # Empty / explicit NA tokens are separate from nonnumeric nonmissing strings.
    missing = raw.isna()
    for col in columns:
        if raw[col].dtype == object or isinstance(raw[col].dtype, pd.StringDtype):
            missing[col] |= raw[col].astype(str).str.strip().str.lower().isin(MISSING_TOKENS)
    numeric = raw.apply(pd.to_numeric, errors="coerce")
    bad = numeric.isna() & ~missing
    xyz = numeric.to_numpy(dtype=float).reshape(-1, 21, 3)
    stats, complete = coordinate_stats(
        xyz, missing.to_numpy().reshape(-1, 21, 3), bad.to_numpy().reshape(-1, 21, 3),
        pd.to_numeric(frame["transforms.length"], errors="coerce").to_numpy())
    stats["absent_xyz_columns"] = sum(c not in frame for c in columns)
    return stats, complete


def parquet_coordinates(column):
    values = column.to_pylist()
    malformed = 0
    try:
        xyz = np.asarray(values, dtype=float)
        if xyz.shape != (len(values), 21, 3):
            raise ValueError("unexpected nested shape")
    except (TypeError, ValueError):
        # Preserve null parents as missing coordinates; do not silently drop rows.
        xyz = np.full((len(values), 21, 3), np.nan)
        for i, value in enumerate(values):
            if value is None:
                continue
            if len(value) != 21:
                malformed += 1
            for j, joint in enumerate(value[:21]):
                if joint is not None and len(joint) == 3:
                    xyz[i, j] = joint
                elif joint is not None:
                    malformed += 1
    stats, complete = coordinate_stats(xyz)
    stats["malformed_nested_shapes"] = malformed
    return stats, complete


def aligned_quality(keys, annotations, side=None):
    """State partition: U=unlisted, N=explicit normal, B=bad, X=listed unresolved."""
    states = []
    for key in keys:
        entry = annotations.get(key) if key is not None else None
        if entry is None:
            states.append("U")
        elif (entry["bad"] if side is None else entry[side]):
            states.append("B")
        elif entry["normal"]:
            states.append("N")
        else:
            states.append("X")
    return np.asarray(states)


def state_counts(states):
    return {state: count(states == state) for state in ("U", "N", "B", "X")}


def put_annotation(mapping, key, entry):
    # Duplicates are counted separately. Union-positive precedence is explicit;
    # normal/bad conflicts are diagnosed, not multiplied by a many-to-many join.
    if key not in mapping:
        mapping[key] = entry.copy()
    else:
        for field in ("bad", "normal", "left", "right"):
            mapping[key][field] |= entry[field]


def raw_metadata(folder):
    if folder.parent.name == "EgoDemo":
        paths = sorted((folder / "meta/episodes").rglob("*.parquet"))
        fields = ["episode_uuid", "environment_id", "scene_id", "operator_id",
                  "length", "quality/hand_bad_frame_count"]
        meta = pq.read_table(paths, columns=fields).to_pylist()
        if len(meta) != 1:
            raise ValueError(f"expected one episode metadata row, got {len(meta)}")
        return meta[0]
    metadata = {r["field"]: r["value"] for r in rows(folder / "session_metadata.csv")}
    return {"episode_uuid": metadata["task_info.episode_uuid"],
            "environment_id": metadata["task_info.environment_id"],
            "scene_id": metadata["task_info.scene_id"],
            "operator_id": metadata["operator.operator_id"]}


def audit_record(folder, root, split_index, gap, example_limit):
    meta = raw_metadata(folder)
    uuid = meta["episode_uuid"]
    indexed = split_index.get(uuid, [])
    record = {"dataset": folder.parent.name, "folder": folder.name,
              "path": folder.relative_to(root).as_posix(), **meta,
              "split": indexed[0]["split"] if len(indexed) == 1 else "UNRESOLVED",
              "folder_differs_from_uuid": int(folder.name != uuid),
              "split_index_matches": len(indexed), "metadata_mismatches": [],
              "time": {}, "hand": {}, "examples": []}
    if len(indexed) == 1:
        for field in ("dataset", "environment_id", "scene_id", "operator_id"):
            if record[field] != indexed[0][field]:
                record["metadata_mismatches"].append(field)
    annotations = {}
    annotation_keys = []
    diagnostics = Counter()
    reasons = Counter()
    hand_keys = {}
    hand_complete = {}
    all_keys = {}

    if record["dataset"] == "EgoDemo":
        info = json.loads((folder / "meta/info.json").read_text())
        record["local_splits"] = info.get("splits", {})
        record["nominal_fps"] = info.get("fps")
        files = sorted((folder / "data").rglob("*.parquet"))
        columns = ["timestamp", "frame_index", "episode_index", "index"] + [
            f"observation.state.hand_{side}_world" for side in SIDES]
        table = pq.read_table(files, columns=columns)
        ts, record["time"]["trajectory"] = timestamp_stats(table["timestamp"].to_pylist(), False, gap)
        frame_ids = table["frame_index"].to_pylist()
        diagnostics["frame_index_duplicate_extra"] = duplicate_report(frame_ids)["extra_rows"]
        diagnostics["frame_index_not_row_number"] = sum(v != i for i, v in enumerate(frame_ids))
        diagnostics["frame_index_nonunit_adjacent"] = sum(b - a != 1 for a, b in zip(frame_ids, frame_ids[1:]))
        diagnostics["metadata_length_mismatch"] = int(meta["length"] != len(ts))
        diagnostics["info_total_frames_mismatch"] = int(info.get("total_frames") != len(ts))
        diagnostics["episode_index_not_single_records"] = int(len(set(table["episode_index"].to_pylist())) != 1)
        anchor_keys = frame_ids
        for side in SIDES:
            record["hand"][side], hand_complete[side] = parquet_coordinates(
                table[f"observation.state.hand_{side}_world"])
            hand_keys[side] = frame_ids
        quality = json.loads((folder / "bad_frame_ratio.json").read_text())
        diagnostics["quality_total_frames_mismatch"] = int(quality["total_frames"] != len(ts))
        for item in quality["hand_bad_frame_details"]:
            key = item["frame"]
            annotation_keys.append(key)
            entry = {"left": bool(item.get("left_bad_reason")),
                     "right": bool(item.get("right_bad_reason")), "normal": False}
            entry["bad"] = entry["left"] or entry["right"]
            for side in SIDES:
                if entry[side]:
                    reasons[f"{side}:{item[side + '_bad_reason']}"] += 1
                diagnostics[f"{side}_reason_key_absent"] += int(side + "_bad_reason" not in item)
            put_annotation(annotations, key, entry)
        diagnostics["metadata_bad_count_mismatch"] = int(
            meta["quality/hand_bad_frame_count"] != sum(v["bad"] for v in annotations.values()))
        record["side_support"] = "sparse left/right reason fields; null reason is NOT explicit normal"
    else:
        for path in sorted((folder / "tables").glob("pose_*.csv")):
            stream = path.stem.removeprefix("pose_")
            if stream in ("left_hand", "right_hand"):
                frame = pd.read_csv(path, keep_default_na=False, dtype={"header.ts": str})
                side = stream.split("_")[0]
                record["hand"][side], hand_complete[side] = csv_coordinates(frame)
            else:
                frame = pd.read_csv(path, usecols=["header.ts"], keep_default_na=False,
                                    dtype={"header.ts": str})
            keys, record["time"][stream] = timestamp_stats(frame["header.ts"], True, gap)
            all_keys[stream] = keys
            if stream in ("left_hand", "right_hand"):
                hand_keys[stream.split("_")[0]] = keys
        anchor_keys = hand_keys["right"]
        quality = pd.read_csv(folder / "tables/annotation_bad_frame_pose_hand.csv",
                              keep_default_na=False, dtype={"header.ts": str})
        annotation_keys, record["time"]["quality"] = timestamp_stats(quality["header.ts"], True, gap)
        reason_cols = [c for c in quality if c.startswith("problem_type[")]
        for key, item in zip(annotation_keys, quality.to_dict("records")):
            flag = str(item["is_bad"]).strip().lower()
            bad, normal = flag in ("true", "1"), flag in ("false", "0")
            types = [str(item[c]).strip() for c in reason_cols if str(item[c]).strip()]
            reasons.update(types)
            entry = {"bad": bad, "normal": normal,
                     "left": any(t.startswith("left:") for t in types),
                     "right": any(t.startswith("right:") for t in types)}
            diagnostics["invalid_is_bad"] += int(not (bad or normal))
            diagnostics["normal_with_reason"] += int(normal and bool(types))
            diagnostics["bad_without_reason"] += int(bad and not types)
            diagnostics["bad_without_side_reason"] += int(bad and not (entry["left"] or entry["right"]))
            diagnostics["unlocalized_reason_items"] += sum(not t.startswith(("left:", "right:")) for t in types)
            diagnostics["problem_type_length_mismatch"] += int(float(item["problem_type.length"]) != len(types))
            if key is not None:
                put_annotation(annotations, key, entry)
        all_keys["quality"] = annotation_keys
        record["side_support"] = "joint is_bad; side bad from left:/right: reasons; other side of bad frame unresolved"

    record["frames"] = len(anchor_keys)
    record["quality"] = state_counts(aligned_quality(anchor_keys, annotations))
    anchor_set = set(anchor_keys) - {None}
    diagnostics["annotation_rows"] = len(annotation_keys)
    diagnostics["annotation_unique_valid_keys"] = len(annotations)
    diagnostics["annotation_duplicate_extra"] = duplicate_report([k for k in annotation_keys if k is not None])["extra_rows"]
    diagnostics["annotation_keys_outside_trajectory"] = len(set(annotations) - anchor_set)
    diagnostics["annotation_bad_normal_conflict_keys"] = sum(v["bad"] and v["normal"] for v in annotations.values())
    diagnostics["both_hands_bad_frames"] = count(
        (aligned_quality(anchor_keys, annotations, "left") == "B") &
        (aligned_quality(anchor_keys, annotations, "right") == "B"))
    diagnostics["left_right_order_mismatch"] = int(hand_keys["left"] != hand_keys["right"])
    diagnostics["left_only_keys"] = len(set(hand_keys["left"]) - set(hand_keys["right"]))
    diagnostics["right_only_keys"] = len(set(hand_keys["right"]) - set(hand_keys["left"]))
    for stream, keys in all_keys.items():
        record["time"][stream]["order_differs_from_right"] = int(keys != anchor_keys)
        record["time"][stream]["keys_only_in_stream"] = len((set(keys) - {None}) - anchor_set)
        record["time"][stream]["keys_missing_from_stream"] = len(anchor_set - set(keys))
    for side in SIDES:
        states = aligned_quality(hand_keys[side], annotations, side)
        joint_states = aligned_quality(hand_keys[side], annotations)
        hand = record["hand"][side]
        hand["quality"] = state_counts(states)
        hand["side_bad_numeric_complete"] = count((states == "B") & hand_complete[side])
        hand["joint_bad_numeric_complete"] = count((joint_states == "B") & hand_complete[side])
        hand["unlisted_numeric_incomplete"] = count((states == "U") & ~hand_complete[side])
        hand["normal_numeric_incomplete"] = count((states == "N") & ~hand_complete[side])
        hand["bad_numeric_incomplete"] = count((states == "B") & ~hand_complete[side])
        for i in np.flatnonzero((states == "B") & hand_complete[side])[:example_limit]:
            record["examples"].append({"side": side, "row_0based": int(i),
                                       "frame_index" if record["dataset"] == "EgoDemo" else "header.ts": hand_keys[side][i],
                                       "case": "side quality bad despite 63 finite xyz scalars and no zero joint"})
    record["quality_diagnostics"] = dict(diagnostics)
    record["reasons"] = dict(reasons)
    assert sum(record["quality"].values()) == record["frames"]
    for side in SIDES:
        assert sum(record["hand"][side]["quality"].values()) == record["hand"][side]["rows"]
    return record


def index_audit(index_rows, segments, records):
    by_uuid = defaultdict(list)
    for row in index_rows:
        by_uuid[row["episode_uuid"]].append(row)
    raw_by_uuid = defaultdict(list)
    for record in records:
        raw_by_uuid[record["episode_uuid"]].append(record)
    result = {"record_split_rows": len(index_rows), "raw_records_read": len(records),
              "raw_uuid_duplicates": duplicate_report(r["episode_uuid"] for r in records),
              "index_uuid_duplicates": duplicate_report(r["episode_uuid"] for r in index_rows),
              "raw_missing_in_record_split": sorted(set(raw_by_uuid) - set(by_uuid)),
              "record_split_missing_raw": sorted(set(by_uuid) - set(raw_by_uuid)),
              "invalid_uuid_strings": [], "metadata_mismatch_records": [],
              "folder_differs_from_uuid": sum(r["folder_differs_from_uuid"] for r in records),
              "raw_environments": len({r["environment_id"] for r in records}),
              "raw_scenes": len({r["scene_id"] for r in records}),
              "raw_operators": len({r["operator_id"] for r in records})}
    for uuid in set(raw_by_uuid) | set(by_uuid):
        try:
            if str(UUID(uuid)) != uuid:
                result["invalid_uuid_strings"].append(uuid)
        except (ValueError, TypeError):
            result["invalid_uuid_strings"].append(uuid)
    for field in ("split", "environment_id", "dataset"):
        result[f"index_uuid_multiple_{field}"] = [u for u, rr in by_uuid.items() if len({r[field] for r in rr}) > 1]
        result[f"raw_uuid_multiple_{field}"] = [u for u, rr in raw_by_uuid.items() if len({r[field] for r in rr}) > 1]
    for r in records:
        if r["metadata_mismatches"] or r["split_index_matches"] != 1:
            result["metadata_mismatch_records"].append({"path": r["path"], "uuid": r["episode_uuid"],
                                                       "fields": r["metadata_mismatches"], "index_matches": r["split_index_matches"]})
    splits = sorted({r["split"] for r in index_rows})
    result["cross_split_intersections"] = {}
    for field in ("episode_uuid", "environment_id", "scene_id", "operator_id"):
        sets = {s: {r[field] for r in index_rows if r["split"] == s} for s in splits}
        result["cross_split_intersections"][field] = {
            f"{a}/{b}": len(sets[a] & sets[b]) for a, b in combinations(splits, 2)}
    seg_diag = Counter(rows=len(segments), eligible_rows=sum(r["eligible"] == "1" for r in segments))
    for segment in segments:
        matches = by_uuid.get(segment["episode_uuid"], [])
        if len(matches) != 1:
            seg_diag["missing_or_ambiguous_record_uuid"] += 1
            continue
        for field in ("split", "dataset", "environment_id"):
            seg_diag[f"{field}_mismatches"] += int(segment[field] != matches[0][field])
        raw_matches = raw_by_uuid.get(segment["episode_uuid"], [])
        seg_diag["sample_folder_mismatches"] += int(len(raw_matches) != 1 or segment["sample"] != raw_matches[0]["folder"])
    result["segments"] = dict(seg_diag)
    result["segment_unit_id_duplicates"] = duplicate_report(r["unit_id"] for r in segments)
    result["segment_source_key_duplicates"] = duplicate_report((r["episode_uuid"], r["source_segment_index"]) for r in segments)
    result["demo_nominal_fps_record_counts"] = dict(Counter(str(r["nominal_fps"])
                                                            for r in records if "nominal_fps" in r))
    result["demo_local_split_counts"] = dict(Counter(json.dumps(r["local_splits"], sort_keys=True)
                                                     for r in records if "local_splits" in r))
    result["demo_local_train_but_official_nontrain"] = [
        {"path": r["path"], "episode_uuid": r["episode_uuid"], "official_split": r["split"]}
        for r in records if "train" in r.get("local_splits", {}) and r["split"] != "train"]
    return result


def sum_dicts(items):
    total = Counter()
    for item in items:
        total.update({k: v for k, v in item.items() if isinstance(v, (int, np.integer))})
    return dict(total)


def summarize(records, fields):
    groups = defaultdict(list)
    for record in records:
        groups[tuple(record[f] for f in fields)].append(record)
    result = []
    for key, members in sorted(groups.items()):
        item = dict(zip(fields, key))
        item.update(records=len(members), frames=sum(r["frames"] for r in members),
                    environments=len({r["environment_id"] for r in members}),
                    quality=sum_dicts(r["quality"] for r in members),
                    hand={}, time={}, reasons=dict(sum((Counter(r["reasons"]) for r in members), Counter())),
                    quality_diagnostics=sum_dicts(r["quality_diagnostics"] for r in members))
        for side in SIDES:
            item["hand"][side] = sum_dicts(r["hand"][side] for r in members)
            item["hand"][side]["quality"] = sum_dicts(r["hand"][side]["quality"] for r in members)
        for stream in sorted({s for r in members for s in r["time"]}):
            stats = [r["time"][stream] for r in members if stream in r["time"]]
            combined = sum_dicts(stats)
            combined["records"] = len(stats)
            for field, fn in (("dt_min_s", min), ("dt_max_s", max)):
                values = [s[field] for s in stats if s[field] is not None]
                combined[field] = fn(values) if values else None
            item["time"][stream] = combined
        result.append(item)
    return result


def definitions(gap):
    return {
        "scope": "All rows of extracted Raw_Data/EgoDemo,EgoPro,EgoStandard, not zip copies. All pose-stream timestamps; 21x3 XYZ positions of both hands. No videos/rotations/models.",
        "identity": "Raw episode_uuid from Demo meta/episodes Parquet or task_info.episode_uuid in CSV; compare raw env/scene/operator against record_split.csv. Folder IDs are NOT episode UUIDs.",
        "split": "Official Restocking_Tasks_and_Constraints/record_split.csv; source_segment_index checked by raw UUID, sample folder, dataset, environment, split. Demo local train:0:1 is packaging metadata, not official split.",
        "frames_denominator": "N=right-hand rows (Demo shared trajectory rows). Hand statistics use each hand's own N; left/right alignment explicitly checked. Micro-count sums, not means of record percentages.",
        "timestamp_denominator": "For each stream, P=sum(max(N_record-1,0)); original stored order, no inter-record pairs. V=adjacent pairs with both valid timestamps. Reverse dt<0, duplicate dt==0 exactly; duplicate_ts_extra also detects nonadjacent repeats.",
        "timestamp_units": "Demo float32 relative seconds promoted before subtraction. CSV header.ts parsed as exact integer nanoseconds and subtracted BEFORE division by 1e9; epoch timestamps never cast to float first.",
        "gap_threshold": f"dt > {gap:g} seconds, strict; default fixed 0.05s = 1.5 periods at 30Hz or 1.25 at 25Hz (NOT per-record adaptive). Also dt>0.1s. Demo metadata FPS distribution reported separately. Heuristic gaps, NOT proven missing video frames; no start/end coverage claim.",
        "actual_missing": "NaN/null/blank or case-insensitive tokens ['',nan,na,n/a,null,none,<na>]; full=all 63 XYZ scalars missing; partial=1..62 missing. Absent columns separately counted; malformed numeric strings and +/-inf reported separately.",
        "zero_sentinel": "Candidate only: exact (x,y,z)==(0,0,0) joint, not a single zero axis. Full=all21 joints zero; partial=1..20. Separate from actual missing; no epsilon/imputation. Joint denominator 21*N; scalar denominator 63*N.",
        "quality_states": "U/N/B/X partition the trajectory-row denominator: annotation unlisted / explicitly normal / marked bad / listed unresolved. Demo null/no reason is NOT explicit normal. CSV False implies joint normal; True implies joint bad; invalid flag is X.",
        "side_support": "Demo explicit left/right bad reason fields; CSV left:/right: problem_type items. A null reason or other-hand-only bad annotation is side X, NOT explicit normal. CSV normal rows support both hands. Side B totals are not a partition because both hands can be bad.",
        "numeric_complete": "All63 position scalars finite and no all-zero XYZ joint. A quality-bad frame with such values is still flagged unreliable: numbers present != valid tracking; reason does not prove every joint is wrong.",
        "duplicates": "Duplicate extra rows=count minus distinct keys. Duplicate quality keys merged union-positive (B precedes N precedes X); conflicts counted. This is identifier duplication, not a content/perceptual duplicate search.",
        "limitations": "No physical accuracy/occlusion verification, video/frame alignment, quaternion validity, coordinate-frame/unit calibration, causal leakage claim, or official test predictions. Bad rates are annotation prevalence, not accuracy; unlisted Demo rows have unknown quality. Timestamp continuity cannot prove trajectory correctness. No file hashing of original archives; no raw writes.",
    }


def print_table(title, headers, values):
    print("\n# " + title)
    print("\t".join(headers))
    for row in values:
        print("\t".join(str(v) for v in row))


def format_quality(quality):
    return "/".join(str(quality.get(s, 0)) for s in ("U", "N", "B", "X"))


def print_report(report, include_records):
    print("# C read-only trajectory quality audit; all counts are full-row micro totals")
    print("RUNTIME " + json.dumps(report["runtime"], ensure_ascii=False))
    for key, value in report["definitions"].items():
        print(f"# {key}: {value}")
    print("INDEX " + json.dumps(report["index"], ensure_ascii=False, sort_keys=True))
    for title, fields in (("dataset", ["dataset"]), ("split", ["split"]),
                          ("dataset_split", ["dataset", "split"]),
                          ("environment_split", ["environment_id", "split"])):
        values = []
        for item in report[title]:
            hands = item["hand"]
            # Right/shared pairs, not sum of duplicated clocks across pose streams.
            primary = [s for name, s in item["time"].items() if name in ("trajectory", "right_hand")]
            values.append([*(item[f] for f in fields), item["records"], item["environments"],
                           item["frames"], sum(s["pairs"] for s in primary), format_quality(item["quality"]),
                           format_quality(hands["left"]["quality"]), format_quality(hands["right"]["quality"]),
                           *(f'{hands[side]["missing_full"]}/{hands[side]["missing_partial"]}' for side in SIDES),
                           *(f'{hands[side]["zero_full"]}/{hands[side]["zero_partial"]}' for side in SIDES),
                           *(hands[side]["side_bad_numeric_complete"] for side in SIDES)])
        print_table(title + " (L/R quality and coordinates each use own hand rows; see coordinate table)",
                    fields + ["records", "envs", "N", "P_primary", "joint_U/N/B/X", "left_U/N/B/X", "right_U/N/B/X",
                              "L_NA_full/part", "R_NA_full/part", "L_zero_full/part", "R_zero_full/part",
                              "L_bad_numeric", "R_bad_numeric"], values)
        time_values = []
        for item in report[title]:
            primary = [s for name, s in item["time"].items() if name in ("trajectory", "right_hand")]
            all_streams = list(item["time"].values())
            time_values.append([*(item[f] for f in fields),
                                *(sum(s[k] for s in primary) for k in ("pairs", "valid_pairs", "invalid_ts_rows",
                                                                      "reverse", "adjacent_equal", "duplicate_ts_extra", "gap")),
                                sum(s["pairs"] for s in all_streams),
                                sum(s["reverse"] + s["adjacent_equal"] + s["gap"] for s in all_streams)])
        print_table(title + " time summary; P_all_streams repeats synchronized clocks, not independent frames",
                    fields + ["P_primary", "V_primary", "invalid_ts", "reverse", "adj_eq", "dup_extra", "gap",
                              "P_all_streams", "all_stream_reverse+equal+gap"], time_values)
    times = []
    coords = []
    for item in report["dataset"]:
        for stream, stat in item["time"].items():
            times.append([item["dataset"], stream, stat["rows"], stat["pairs"], stat["valid_pairs"],
                          stat["invalid_ts_rows"], stat["reverse"], stat["adjacent_equal"],
                          stat["duplicate_ts_extra"], stat["gap"], stat["gap_gt_100ms"],
                          f'{stat["dt_min_s"]:.9g}', f'{stat["dt_max_s"]:.9g}',
                          stat.get("order_differs_from_right", 0), stat.get("keys_only_in_stream", 0),
                          stat.get("keys_missing_from_stream", 0)])
        for side, stat in item["hand"].items():
            coords.append([item["dataset"], side, stat["rows"], stat["scalar_denominator"], stat["joint_denominator"],
                           stat["missing_scalars"], stat["parse_bad_scalars"], stat["infinite_scalars"],
                           stat["nonfinite_rows"], stat["zero_joints"], stat["collapsed_all_joints"],
                           stat["length_supported_rows"], stat["length_not_21"],
                           stat.get("absent_xyz_columns", 0), stat.get("malformed_nested_shapes", 0),
                           stat["complete_finite_no_zero_joint"], stat["normal_numeric_incomplete"]])
        print("QUALITY_DETAIL " + json.dumps({"dataset": item["dataset"], "reasons": item["reasons"],
                                               "diagnostics": item["quality_diagnostics"]}, sort_keys=True))
    print_table("timestamps, original order / stream", ["dataset", "stream", "N", "P", "V", "invalid_ts",
                "reverse", "adj_eq", "dup_extra", "gap", "gap100", "min_dt_s", "max_dt_s",
                "order_mismatch_records", "extra_keys", "missing_keys"], times)
    print_table("coordinates, full hands (quaternions excluded)", ["dataset", "hand", "N", "63*N", "21*N", "NA_scalars",
                "parse_bad_scalars", "inf_scalars", "nonfinite_rows", "zero_joints", "collapsed_rows",
                "length_supported_N", "length_not21", "absent_xyz_columns", "malformed_nested_shapes",
                "numeric_complete_N", "normal_numeric_incomplete"], coords)
    for example in report["examples"]:
        print("EXAMPLE " + json.dumps(example, ensure_ascii=False))
    for error in report["errors"]:
        print("ERROR " + json.dumps(error, ensure_ascii=False))
    if include_records:
        for record in report["records"]:
            print("RECORD " + json.dumps(record, ensure_ascii=False, sort_keys=True))
    print(f'\nDONE scanned={report["index"]["raw_records_read"]} errors={len(report["errors"])} seconds={report["runtime"]["elapsed_s"]}')


def self_test():
    keys, stat = timestamp_stats(["1700000000000000000", "1700000000000000001",
                                 "1700000000000000001", "1699999999999999999", "1700000000100000000"], True, .05)
    assert stat["adjacent_equal"] == 1 and stat["reverse"] == 1 and stat["gap"] == 1
    assert keys[1] - keys[0] == 1
    _, stat = timestamp_stats([0, .05, .100001, None], False, .05)
    assert stat["pairs"] == 3 and stat["valid_pairs"] == 2 and stat["gap"] == 1
    xyz = np.ones((6, 21, 3))
    xyz[0] = np.nan
    xyz[1, 0, 0] = np.nan
    xyz[2] = 0
    xyz[3, 0] = 0
    xyz[4, 0, 0] = np.inf
    stat, complete = coordinate_stats(xyz)
    assert [stat[k] for k in ("missing_full", "missing_partial", "zero_full", "zero_partial", "infinite_scalars")] == [1] * 5
    assert complete.tolist() == [False, False, False, False, False, True]
    mapping = {1: {"bad": True, "normal": False, "left": True, "right": False},
               2: {"bad": False, "normal": True, "left": False, "right": False}}
    assert aligned_quality([0, 1, 2], mapping).tolist() == ["U", "B", "N"]
    assert aligned_quality([0, 1, 2], mapping, "right").tolist() == ["U", "X", "N"]
    return "passed: exact ns, strict gap, actual/zero full/partial, side support"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=ROOT, help="C attachment root; read-only")
    parser.add_argument("--gap-seconds", type=float, default=.05)
    parser.add_argument("--json", action="store_true", help="machine-readable full summary JSON")
    parser.add_argument("--records", action="store_true", help="also emit complete per-record counters and original UUIDs")
    parser.add_argument("--examples", type=int, default=1, help="maximum numeric-but-bad examples per dataset/hand")
    parser.add_argument("--self-test", action="store_true", help="in-memory tests only, no dataset scan")
    args = parser.parse_args()
    if args.gap_seconds <= 0 or not np.isfinite(args.gap_seconds) or args.examples < 0:
        parser.error("gap must be finite and positive; examples must be nonnegative")
    test_result = self_test()
    if args.self_test:
        print(test_result)
        return 0
    start = time.monotonic()
    root = args.root.resolve()
    rules = root / "Restocking_Tasks_and_Constraints"
    index_rows = rows(rules / "record_split.csv")
    segments = rows(rules / "source_segment_index.csv")
    split_index = defaultdict(list)
    for item in index_rows:
        split_index[item["episode_uuid"]].append(item)
    records, errors = [], []
    for dataset in DATASETS:
        dataset_root = root / "Raw_Data" / dataset
        if not dataset_root.is_dir():
            errors.append({"path": str(dataset_root), "error": "missing dataset directory"})
            continue
        for folder in sorted(p for p in dataset_root.iterdir() if p.is_dir()):
            try:
                records.append(audit_record(folder, root, split_index, args.gap_seconds, args.examples))
            except Exception as error:
                errors.append({"path": folder.relative_to(root).as_posix(),
                               "error": f"{type(error).__name__}: {error}"})
    report = {"runtime": {"python": sys.executable, "pandas": pd.__version__, "pyarrow": pyarrow.__version__,
                           "numpy": np.__version__, "self_test": test_result, "elapsed_s": round(time.monotonic() - start, 3)},
              "definitions": definitions(args.gap_seconds), "index": index_audit(index_rows, segments, records),
              "dataset": summarize(records, ["dataset"]), "split": summarize(records, ["split"]),
              "dataset_split": summarize(records, ["dataset", "split"]),
              "environment_split": summarize(records, ["environment_id", "split"]), "errors": errors}
    seen_examples = Counter()
    report["examples"] = []
    for record in records:
        for example in record["examples"]:
            key = record["dataset"], example["side"]
            if seen_examples[key] < args.examples:
                seen_examples[key] += 1
                report["examples"].append({"path": record["path"], "episode_uuid": record["episode_uuid"], **example})
    if args.records:
        report["records"] = records
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        print_report(report, args.records)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
