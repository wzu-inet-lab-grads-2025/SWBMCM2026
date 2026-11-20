"""Read-only Q3 source-to-job audit; stdout only, no network or data modification.

Run from the repository root with an existing pandas/pyarrow environment:
    python -B experiments/c_audit_segments.py
    python -B experiments/c_audit_segments.py --details

Reuses ROOT, rows and inspect_record from check_c_data.py. The formal authority is
Restocking_Tasks_and_Constraints/Problem_3_Requirements.pdf, sections 1-2 and 4.
The fixed eligible column is authoritative: length eligibility is NOT observation
quality certification. Additional flag/prefix counts below are descriptive only.

All validation/test truth printed here is retrospective AUDIT DATA, never policy
input. The single public validation seed demonstration makes assignments only;
it is not a model, scheduler, policy replay, or validation-performance result.
Prepared with AI assistance for attachment inventory on 2026-11-20.
"""

import sys

# Even without -B, importing the existing inventory must not create __pycache__.
sys.dont_write_bytecode = True

import argparse
import hashlib
import json
import random
import shutil
import subprocess
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from itertools import combinations
from pathlib import Path
from statistics import median
from zipfile import ZipFile

import numpy as np
import pandas as pd
import pyarrow
import pyarrow.parquet as pq

if __package__:
    from .check_c_data import ROOT, inspect_record, rows
else:
    from check_c_data import ROOT, inspect_record, rows


SPLITS = ("train", "validation", "test")
FAMILIES = ("P", "A", "L")
DATASETS = ("EgoDemo", "EgoPro", "EgoStandard")
POSES = ("pose_left_hand", "pose_right_hand", "pose_head", "pose_headcam", "pose_right_eye_cam")
# Auditor tolerances, NOT an organizer-specified quality threshold. A frame is
# never silently added to the playback interval or to the released prefix.
TIME_TOL = 1e-5  # 10 microseconds: float32 seconds and microsecond video clocks.
DURATION_TOL = Decimal("1e-9")  # Decimal endpoints vs serialized binary subtraction.
MIN_DURATION = Decimal("1.2")


def emit_value(value):
    if isinstance(value, (Path, Decimal)):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(type(value).__name__)


def relative(path):
    return str(path.relative_to(ROOT))


def read_json(path, exact=False):
    return json.loads(path.read_text(encoding="utf-8-sig"),
                      **({"parse_float": Decimal} if exact else {}))


def dec(value):
    return Decimal(str(value))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def short(unit):
    return {k: unit[k] for k in ("unit_id", "split", "family_id", "dataset", "sample",
                               "episode_uuid", "source_segment_index", "start_s", "end_s", "eligible")}


def probe_video(path):
    """Demux ALL packet timestamps: max PTS, not the final packet in B-frame order.

    No frame decoding; do not claim visual/bitstream quality or CRC verification.
    Integer PTS * rational time_base avoids rounded ffprobe *_time output.
    """
    command = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_packets",
               "-show_entries", "packet=pts,duration:stream=time_base,start_pts,duration_ts,nb_frames",
               "-of", "json", str(path)]
    try:
        run = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
        if run.returncode:
            return {"error": "ffprobe failed", "returncode": run.returncode}
        data = json.loads(run.stdout)
        stream = data["streams"][0]
        numerator, denominator = map(int, stream["time_base"].split("/"))
        tick = Decimal(numerator) / Decimal(denominator)
        packets = data.get("packets", [])
        stamped = [p for p in packets if "pts" in p]
        if not stamped:
            return {"error": "no packet PTS"}
        last_pts = max(int(p["pts"]) for p in stamped)
        # A maximum-PTS frame must have its own positive duration to certify end.
        complete = len(stamped) == len(packets) and all(int(p.get("duration", 0)) > 0 for p in stamped)
        last = [p for p in stamped if int(p["pts"]) == last_pts]
        end_known = all(int(p.get("duration", 0)) > 0 for p in last)
        result = {"packets": len(packets), "packets_with_pts": len(stamped),
                  "all_packet_durations_positive": complete,
                  "first_pts_s": float(min(int(p["pts"]) for p in stamped) * tick),
                  "last_pts_s": float(last_pts * tick), "stream": stream,
                  "stderr_present": bool(run.stderr.strip())}
        if end_known:
            result["playback_end_s"] = float(max(int(p["pts"]) + int(p.get("duration", 0))
                                                   for p in stamped) * tick)
        else:
            result["playback_end_unverified"] = "last PTS has no positive packet duration"
        return result
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, IndexError) as error:
        return {"error": type(error).__name__ + ": " + str(error)}


def inventory_files():
    result = {}
    for dataset in DATASETS:
        root = ROOT / "Raw_Data" / dataset
        archive = root.with_suffix(".zip")
        actual = {relative(p): p for p in root.rglob("*") if p.is_file()}
        if not archive.is_file():
            result[dataset] = {"archive_missing": relative(archive), "extracted_files": len(actual)}
            continue
        with ZipFile(archive) as package:
            entries = {"Raw_Data/" + i.filename: i for i in package.infolist() if not i.is_dir()}
        result[dataset] = {
            "archive_files": len(entries), "extracted_files": len(actual),
            "missing": sorted(set(entries) - set(actual)),
            "extra": sorted(set(actual) - set(entries)),
            "size_mismatch": [name for name in sorted(set(entries) & set(actual))
                              if entries[name].file_size != actual[name].stat().st_size],
            "zero_byte_files": [name for name, path in actual.items() if path.stat().st_size == 0],
            "scope": "ZIP directory and extracted file sizes only; no CRC/full decode",
        }
    return result


def load_record(folder):
    """UUIDs come from actual metadata, never directory names or the source index."""
    dataset = folder.parent.name
    channels, videos = {}, {}
    if dataset == "EgoDemo":
        meta_files = sorted((folder / "meta/episodes").rglob("*.parquet"))
        data_files = sorted((folder / "data").rglob("*.parquet"))
        if len(meta_files) != 1 or len(data_files) != 1:
            raise ValueError("expected one episode metadata and one data parquet in supplied Demo layout")
        meta = pq.read_table(meta_files[0]).to_pylist()
        if len(meta) != 1:
            raise ValueError("expected exactly one episode metadata row")
        meta = meta[0]
        identity = {k: meta[k] for k in ("episode_uuid", "environment_id", "scene_id", "operator_id")}
        task_names = meta["tasks"]
        annotations_path = folder / "annotation.json"
        annotations = read_json(annotations_path, exact=True)["subtask_annotations"]
        normalized = [(dec(a["start_time"]), dec(a["end_time"]), a["description"]) for a in annotations]
        schema = pq.read_schema(data_files[0])
        table = pq.read_table(data_files[0], columns=["timestamp", "frame_index"])
        times = table["timestamp"].to_numpy()
        frames = table["frame_index"].to_numpy()
        feature_groups = {
            "pose_left_hand": ("hand_left_world", "hand_left_world_rotation"),
            "pose_right_hand": ("hand_right_world", "hand_right_world_rotation"),
            "pose_head": ("head_world", "head_world_rotation"),
            "pose_headcam": ("head_left_camera_position", "head_left_camera_rotation"),
            "pose_right_eye_cam": ("head_right_camera_position", "head_right_camera_rotation"),
        }
        for name, fields in feature_groups.items():
            for field in fields:
                if "observation.state." + field not in schema.names:
                    raise ValueError("missing numerical column: " + field)
            channels[name] = {"path": relative(data_files[0]), "times": times}
        quality_path = folder / "bad_frame_ratio.json"
        quality = read_json(quality_path)
        marks = {q["frame"] for q in quality["hand_bad_frame_details"]
                 if q["left_bad_reason"] or q["right_bad_reason"]}
        if quality["total_frames"] != len(times) or not marks <= set(frames.tolist()):
            raise ValueError("Demo quality frame reference mismatch")
        marked = np.isin(frames, list(marks))
        info = read_json(folder / "meta/info.json")
        for key, value in info["features"].items():
            if value["dtype"] != "video":
                continue
            prefix = "videos/" + key
            path = folder / info["video_path"].format(video_key=key,
                          chunk_index=meta[prefix + "/chunk_index"],
                          file_index=meta[prefix + "/file_index"])
            videos[key] = {"path": path, "source_offset_s": -float(meta[prefix + "/from_timestamp"]),
                           "metadata_from_s": meta[prefix + "/from_timestamp"],
                           "metadata_to_s": meta[prefix + "/to_timestamp"]}
        metadata_path = meta_files[0]
        origin = {"basis": "Demo timestamp seconds as stored; per-video metadata from_timestamp"}
    else:
        metadata_path = folder / "session_metadata.csv"
        meta = {r["field"]: r["value"] for r in rows(metadata_path)}
        identity = {k: meta["task_info." + k] for k in ("episode_uuid", "environment_id", "scene_id")}
        identity["operator_id"] = meta["operator.operator_id"]
        task_names = [meta["task_info.task_name"]]
        annotations_path = folder / "tables/annotation_semantic_segments.csv"
        annotations = rows(annotations_path)
        normalized = [(dec(a["segment.start_time"]), dec(a["segment.end_time"]),
                       a["segment.subtask_description"]) for a in annotations]
        origins = [dec(a["header.ts"]) - start * 10**9 for a, (start, _, _) in zip(annotations, normalized)]
        origin_ns = int(origins[0])
        if dec(origin_ns) != origins[0]:
            raise ValueError("noninteger annotation origin in nanoseconds")
        origin = {"basis": "first annotation header.ts - segment.start_time * 1e9; integer ns subtraction",
                  "origin_ns": str(origin_ns), "annotation_origin_spread_ns": max(origins) - min(origins),
                  "minus_metadata_header_ns": dec(origin_ns) - dec(meta["header.ts"])}
        quality_path = folder / "tables/annotation_bad_frame_pose_hand.csv"
        for name in POSES + ("quality",):
            path = quality_path if name == "quality" else folder / "tables" / (name + ".csv")
            usecols = ["header.ts"] + (["is_bad"] if name == "quality" else [])
            data = pd.read_csv(path, usecols=usecols, dtype={"header.ts": "int64"})
            times = (data["header.ts"].to_numpy() - origin_ns) / 1e9
            channels[name] = {"path": relative(path), "times": times}
            if name == "quality":
                values = data["is_bad"].astype(str).str.lower()
                if not values.isin(["true", "false", "0", "1"]).all():
                    raise ValueError("unknown original is_bad encoding")
                marked = values.isin(["true", "1"]).to_numpy()
        for channel in ("head_left", "head_right") + (("left_wrist", "right_wrist") if dataset == "EgoPro" else ()):
            path = folder / "videos" / (channel + ".mp4")
            frame_path = folder / "videos" / (channel + "_frames.csv")
            data = pd.read_csv(frame_path, usecols=["video_timestamp_ns"], dtype="int64")
            frame_times = (data["video_timestamp_ns"].to_numpy() - origin_ns) / 1e9
            videos[channel] = {"path": path, "frame_path": relative(frame_path), "frame_times": frame_times,
                               "source_offset_s": float(frame_times[0])}
    if "quality" not in channels:
        channels["quality"] = {"path": relative(quality_path), "times": times}
    # Reuse the predecessor audit's exact identity/quality crosschecks.
    uuid, count, flagged = inspect_record(folder)
    if uuid != identity["episode_uuid"] or count != len(channels["pose_right_hand"]["times"]) or flagged != int(marked.sum()):
        raise ValueError("existing inventory crosscheck failed")
    return {"folder": folder, "dataset": dataset, "identity": identity, "tasks": task_names,
            "metadata_path": relative(metadata_path), "annotation_path": relative(annotations_path),
            "annotations": annotations, "normalized": normalized, "channels": channels,
            "videos": videos, "marked": marked, "origin": origin, "count": count, "flagged": flagged}


def record_bounds(record):
    """Retrospective clock evidence, including successful (not only failed) bounds."""
    result = {"numerical": {}, "videos": {}}
    for name, channel in record["channels"].items():
        times = channel["times"]
        result["numerical"][name] = {"path": channel["path"], "samples": len(times),
                                      "first_s": float(times[0]), "last_s": float(times[-1])}
    for name, video in record["videos"].items():
        item = {"path": relative(video["path"]), "source_offset_s": video["source_offset_s"],
                "probe": video["probe"]}
        if "frame_times" in video:
            times = video["frame_times"]
            item["frame_csv"] = {"path": video["frame_path"], "samples": len(times),
                                  "first_s": float(times[0]), "last_s": float(times[-1])}
        if "playback_end_s" in video["probe"]:
            item["source_playback_end_s"] = video["source_offset_s"] + video["probe"]["playback_end_s"]
        result["videos"][name] = item
    return result


def window(times, start, end):
    # Strict stored sample timestamps, inclusive window; no tolerance extension,
    # interpolation, future filling or removal of the original bad flags.
    return int(np.searchsorted(times, start, side="left")), int(np.searchsorted(times, end, side="right"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--details", action="store_true", help="also print every UUID mapping and unit audit row")
    args = parser.parse_args()
    rules = ROOT / "Restocking_Tasks_and_Constraints"
    report = {"root": str(ROOT), "read_only": True, "audit_not_policy_input": True,
              "versions": {"python": sys.version.split()[0], "pandas": pd.__version__,
                           "pyarrow": pyarrow.__version__, "numpy": np.__version__},
              "formal_rules": {"path": relative(rules / "Problem_3_Requirements.pdf"),
                               "version": read_json(rules / "common_conditions.json")["version"],
                               "eligibility": "fixed eligible column, >=1.2s length only; NOT quality certification",
                               "quality_pass_definition": "No Q3 observation-quality passing threshold supplied",
                               "families": "source-index strata; no independent task-to-family mapping rule supplied; not robot skills/products"},
              "tolerances": {"duration_serialization_s": str(DURATION_TOL), "boundary_s": TIME_TOL,
                             "boundary_strict_also_reported": True,
                             "prefix": "strict stored timestamps in [start,start+prefix]; no epsilon extension",
                             "quality_full_segment": "[start,end), existing hand flags only",
                             "playback": "max(packet PTS + packet duration); never last-sample + assumed fps"},
              "input_sha256": {p.name: sha256(p) for p in sorted(rules.iterdir()) if p.is_file()},
              "file_inventory": inventory_files(), "errors": [], "not_verified": [
                  "No video decode/visual inspection, ZIP CRC, or physical task-success verification",
                  "No quality-pass inference from unflagged samples; no new quality thresholds",
                  "No pose value/rotation/NaN scan; Demo checks channel columns, CSV checks required files/timestamps only",
                  "Packet PTS playback extent assumes frame-CSV first frame / Demo metadata gives time origin; no retiming",
                  "No model, scheduling, policy-information isolation test, or final-test seed experiment",
              ]}
    errors = report["errors"]
    records = rows(rules / "record_split.csv")
    units = rows(rules / "source_segment_index.csv")
    jobs = rows(rules / "tasks.csv")
    zones = {z["zone_id"] for z in rows(rules / "service_zones.csv")}
    conditions = read_json(rules / "common_conditions.json")
    by_uuid = {r["episode_uuid"]: r for r in records}
    if len(by_uuid) != len(records):
        errors.append({"kind": "duplicate record_split UUID"})
    if len({u["unit_id"] for u in units}) != len(units):
        errors.append({"kind": "duplicate unit_id"})
    if {r["split"] for r in records} - set(SPLITS) or {u["family_id"] for u in units} - set(FAMILIES):
        errors.append({"kind": "unexpected split/family"})
    pools = {(split, family): [u for u in units if u["split"] == split and u["family_id"] == family and u["eligible"] == "1"]
             for split in SPLITS for family in FAMILIES}
    report["split_family"] = []
    for (split, family), pool in pools.items():
        all_units = [u for u in units if u["split"] == split and u["family_id"] == family]
        durations = [dec(u["end_s"]) - dec(u["start_s"]) for u in pool]
        report["split_family"].append({"split": split, "family": family, "eligible": len(pool), "total": len(all_units),
                                      "eligible_records": len({u["episode_uuid"] for u in pool}),
                                      "eligible_environments": len({u["environment_id"] for u in pool}),
                                      "duration_min_median_max_s": [min(durations), median(durations), max(durations)] if durations else []})
    report["empty_pools"] = [list(key) for key, pool in pools.items() if not pool]
    report["record_splits"] = {split: {"records": sum(r["split"] == split for r in records),
                                       "environments": len({r["environment_id"] for r in records if r["split"] == split})}
                                for split in SPLITS}
    report["split_overlap"] = {}
    for field in ("environment_id", "scene_id", "operator_id"):
        groups = {split: {r[field] for r in records if r["split"] == split} for split in SPLITS}
        report["split_overlap"][field] = {a + "/" + b: len(groups[a] & groups[b]) for a, b in combinations(SPLITS, 2)}

    actual, mappings = {}, []
    for dataset in DATASETS:
        for folder in sorted((ROOT / "Raw_Data" / dataset).iterdir()):
            if not folder.is_dir():
                continue
            try:
                record = load_record(folder)
            except (OSError, ValueError, KeyError, IndexError, AssertionError, StopIteration) as error:
                errors.append({"kind": "record unreadable/missing/inconsistent", "folder": relative(folder),
                               "error": type(error).__name__ + ": " + str(error)})
                continue
            uuid = record["identity"]["episode_uuid"]
            if uuid in actual:
                errors.append({"kind": "duplicate actual metadata UUID", "uuid": uuid})
                continue
            actual[uuid] = record
            official = by_uuid.get(uuid, {})
            mismatches = [key for key, value in record["identity"].items() if official.get(key) != value]
            if official.get("dataset") != dataset:
                mismatches.append("dataset")
            if mismatches:
                errors.append({"kind": "record metadata mismatch", "folder": relative(folder), "fields": mismatches})
            mappings.append({**record["identity"], "dataset": dataset, "split": official.get("split"),
                             "directory": relative(folder), "metadata": record["metadata_path"],
                             "directory_name_differs_from_uuid": folder.name != uuid})
            for name, channel in record["channels"].items():
                times = channel["times"]
                if len(times) == 0 or not np.isfinite(times).all() or not np.all(np.diff(times) > 0):
                    errors.append({"kind": "invalid/nonincreasing timestamps", "uuid": uuid, "channel": name})
                if not np.array_equal(times, record["channels"]["pose_right_hand"]["times"]):
                    errors.append({"kind": "numerical timestamp grid differs from right hand", "uuid": uuid, "channel": name})
    report["metadata_alignment"] = {"record_split_rows": len(records), "actual_metadata_UUIDs": len(actual),
        "missing_UUIDs": sorted(set(by_uuid) - set(actual)), "extra_UUIDs": sorted(set(actual) - set(by_uuid)),
        "directory_differs_from_UUID": sum(m["directory_name_differs_from_uuid"] for m in mappings),
        "by_dataset": {d: sum(m["dataset"] == d for m in mappings) for d in DATASETS}}
    report["record_quality_objective_counts"] = {d: {"samples": sum(r["count"] for r in actual.values() if r["dataset"] == d),
                                                         "hand_flagged": sum(r["flagged"] for r in actual.values() if r["dataset"] == d)} for d in DATASETS}
    report["origin_alignment"] = {"max_annotation_origin_spread_ns": max((r["origin"].get("annotation_origin_spread_ns", 0) for r in actual.values()), default=0),
                                  "max_abs_annotation_minus_metadata_header_ns": max((abs(r["origin"].get("minus_metadata_header_ns", 0)) for r in actual.values()), default=0)}

    all_videos = [v for r in actual.values() for v in r["videos"].values()]
    video_results = {}
    if shutil.which("ffprobe"):
        with ThreadPoolExecutor(max_workers=4) as executor:
            video_results = dict(zip([str(v["path"]) for v in all_videos], executor.map(probe_video, [v["path"] for v in all_videos])))
    else:
        report["not_verified"].append("ffprobe unavailable: MP4 PTS/playback bounds NOT VERIFIED")
    video_warnings, video_drifts = [], []
    for record in actual.values():
        for name, video in record["videos"].items():
            result = video_results.get(str(video["path"]), {"error": "ffprobe unavailable"})
            video["probe"] = result
            if not video["path"].is_file():
                errors.append({"kind": "missing declared video", "path": relative(video["path"])})
            if "error" in result or "playback_end_s" not in result:
                video_warnings.append({"path": relative(video["path"]), **result})
                continue
            if result["stderr_present"] or not result["all_packet_durations_positive"]:
                video_warnings.append({"path": relative(video["path"]), "probe_warning": result})
            if "frame_times" in video:
                times = video["frame_times"]
                if len(times) != result["packets"]:
                    errors.append({"kind": "frame CSV / video packet count mismatch", "path": relative(video["path"]),
                                   "csv_rows": len(times), "packets": result["packets"]})
                if not np.all(np.diff(times) > 0):
                    errors.append({"kind": "nonincreasing video frame timestamps", "path": video["frame_path"]})
                # Align the first encoded PTS to first source video frame timestamp.
                video["source_offset_s"] -= result["first_pts_s"]
                video_drifts.append(abs(float(times[-1]) - (video["source_offset_s"] + result["last_pts_s"])))
    report["video_probe"] = {"declared_videos": len(all_videos), "probed": len(video_results),
                              "playback_end_verified": sum("playback_end_s" in v for v in video_results.values()),
                              "warnings": video_warnings,
                              "max_frame_CSV_vs_encoded_last_PTS_drift_s": max(video_drifts, default=None)}

    boundaries = defaultdict(lambda: {"strict_units": set(), "tolerance_units": set(), "eligible_units": set(), "channel_checks": 0})
    boundary_details, unit_details, length_issues, raw_errors = [], [], [], []
    prefix_stats = defaultdict(list)
    flag_stats = defaultdict(Counter)
    indexed_pairs = Counter()
    matched = 0
    duration_diffs = []
    def check_boundary(unit, channel, kind, lower, upper, last=None):
        start, end = float(unit["start_s"]), float(unit["end_s"])
        for event, gap in (("start_before_first", lower - start), ("start_after_end", start - upper), ("end_after_end", end - upper)):
            key = kind + ":" + event
            group = boundaries[key]
            group["channel_checks"] += 1
            if gap > 0:
                group["strict_units"].add(unit["unit_id"])
            if gap > TIME_TOL:
                group["tolerance_units"].add(unit["unit_id"])
                if unit["eligible"] == "1":
                    group["eligible_units"].add(unit["unit_id"])
            if gap > 0:
                boundary_details.append({**short(unit), "channel": channel, "kind": kind, "event": event,
                                         "first_s": lower, "upper_s": upper, "excess_s": gap,
                                         "exceeds_auditor_tolerance": gap > TIME_TOL,
                                         "sample_interval_s": last})

    for unit in units:
        uuid, index = unit["episode_uuid"], int(unit["source_segment_index"])
        indexed_pairs[(uuid, index)] += 1
        start, end = dec(unit["start_s"]), dec(unit["end_s"])
        duration = end - start
        stored_duration = dec(unit["duration_s"])
        duration_diffs.append(abs(stored_duration - duration))
        expected = "1" if duration >= MIN_DURATION else "0"
        if unit["eligible"] != expected or duration <= 0 or start < 0 or abs(stored_duration - duration) > DURATION_TOL:
            length_issues.append({**short(unit), "duration_endpoint_decimal": duration, "duration_stored": stored_duration, "expected_eligible": expected})
        if (unit["eligible"] == "1" and unit["exclusion_reason"]) or (unit["eligible"] == "0" and unit["exclusion_reason"] != "shorter_than_1p2_seconds"):
            length_issues.append({**short(unit), "exclusion_reason": unit["exclusion_reason"]})
        record = actual.get(uuid)
        if record is None:
            raw_errors.append({**short(unit), "error": "no readable actual metadata record"})
            continue
        mismatch = []
        for key in ("dataset", "environment_id", "split"):
            if unit[key] != by_uuid.get(uuid, {}).get(key):
                mismatch.append(key)
        if unit["sample"] != record["folder"].name:
            mismatch.append("sample directory")
        if unit["task"] not in record["tasks"]:
            mismatch.append("task metadata")
        if index < 0 or index >= len(record["normalized"]):
            mismatch.append("zero-based annotation index")
        elif (start, end, unit["description"]) != record["normalized"][index]:
            mismatch.append("raw annotation start/end/description")
        if mismatch:
            raw_errors.append({**short(unit), "fields": mismatch})
        else:
            matched += 1
        detail = {**short(unit), "directory": relative(record["folder"]), "raw_annotation_matched": not mismatch,
                  "duration_decimal_s": duration, "prefixes": {}}
        for name, channel in record["channels"].items():
            times = channel["times"]
            kind = "quality_last_sample" if name == "quality" else "trajectory_last_sample"
            check_boundary(unit, name, kind, float(times[0]), float(times[-1]),
                           float(np.median(np.diff(times))) if len(times) > 1 else None)
            detail["prefixes"][name] = {}
            for prefix in conditions["observation"]["prefix_seconds"]:
                lo, hi = window(times, float(start), float(start + dec(prefix)))
                info = {"samples": hi - lo, "first_s": float(times[lo]) if hi > lo else None,
                        "last_s": float(times[hi - 1]) if hi > lo else None}
                if name == "quality":
                    info["flagged_samples"] = int(record["marked"][lo:hi].sum())
                detail["prefixes"][name][str(prefix)] = info
                if unit["eligible"] == "1":
                    prefix_stats[(unit["split"], unit["family_id"], name, prefix)].append(hi - lo)
        quality_times = record["channels"]["quality"]["times"]
        lo = int(np.searchsorted(quality_times, float(start), side="left"))
        hi = int(np.searchsorted(quality_times, float(end), side="left"))
        detail["segment_existing_quality_samples"] = hi - lo
        detail["segment_hand_flagged_samples"] = int(record["marked"][lo:hi].sum())
        if unit["eligible"] == "1":
            flag_stats[(unit["split"], unit["family_id"])].update(
                units=1, with_any_hand_flag=int(detail["segment_hand_flagged_samples"] > 0),
                no_quality_samples=int(hi == lo))
        for name, video in record["videos"].items():
            if "frame_times" in video:
                times = video["frame_times"]
                check_boundary(unit, name, "video_frame_CSV_last_sample", float(times[0]), float(times[-1]))
            probe = video["probe"]
            if "last_pts_s" not in probe:
                continue
            offset = video["source_offset_s"]
            check_boundary(unit, name, "video_encoded_last_PTS", probe["first_pts_s"] + offset, probe["last_pts_s"] + offset)
            if "playback_end_s" in probe:
                check_boundary(unit, name, "video_playback_end", probe["first_pts_s"] + offset, probe["playback_end_s"] + offset)
        unit_details.append(detail)
    all_raw_pairs = {(uuid, i) for uuid, record in actual.items() for i in range(len(record["annotations"]))}
    report["segment_alignment"] = {"indexed": len(units), "eligible": sum(u["eligible"] == "1" for u in units),
                                    "actual_raw_annotations": len(all_raw_pairs), "matched_row_by_row": matched,
                                    "mismatches": raw_errors,
                                    "unindexed_raw_pairs": sorted(all_raw_pairs - set(indexed_pairs)),
                                    "indexed_nonexistent_raw_pairs": sorted(set(indexed_pairs) - all_raw_pairs),
                                    "duplicate_raw_references": [(list(key), count) for key, count in indexed_pairs.items() if count != 1]}
    report["length_check"] = {"issues": length_issues, "max_abs_serialization_difference_s": max(duration_diffs, default=0),
                              "decimal_exact_1p2": [short(u) for u in units if dec(u["end_s"]) - dec(u["start_s"]) == MIN_DURATION],
                              "eligible_float_below_1p2": [{**short(u), "duration_s": u["duration_s"]} for u in units if u["eligible"] == "1" and float(u["duration_s"]) < 1.2],
                              "excluded": [{**short(u), "duration_s": u["duration_s"], "reason": u["exclusion_reason"]} for u in units if u["eligible"] != "1"]}
    report["boundary_summary"] = {key: {"strict_unique_units": len(value["strict_units"]),
                                         "beyond_tolerance_unique_units": len(value["tolerance_units"]),
                                         "beyond_tolerance_eligible_units": len(value["eligible_units"]),
                                         "unit_channel_checks": value["channel_checks"]} for key, value in sorted(boundaries.items())}
    # Merge equal anomalies on multiple numerical/video channels for concise output.
    grouped_bounds = {}
    for detail in boundary_details:
        channel = detail.pop("channel")
        key = json.dumps(detail, sort_keys=True)
        grouped_bounds.setdefault(key, {**detail, "channels": []})["channels"].append(channel)
    report["boundary_details"] = list(grouped_bounds.values())
    boundary_uuids = {d["episode_uuid"] for d in boundary_details}
    report["boundary_record_evidence"] = [{"episode_uuid": uuid, "split": by_uuid[uuid]["split"],
        "directory": relative(actual[uuid]["folder"]), **record_bounds(actual[uuid])} for uuid in sorted(boundary_uuids)]
    report["objective_flag_coverage_not_quality_pass"] = [{"split": key[0], "family": key[1], **value} for key, value in sorted(flag_stats.items())]
    report["objective_prefix_coverage_not_quality_pass"] = [{"split": key[0], "family": key[1], "channel": key[2], "prefix_s": key[3],
            "eligible_units": len(counts), "empty_windows": counts.count(0), "min_samples": min(counts), "max_samples": max(counts)}
            for key, counts in sorted(prefix_stats.items())]

    report["tasks"] = []
    official_cases = {case["case_id"] for case in conditions["cases"]}
    if len(jobs) != sum(case["jobs"] for case in conditions["cases"]) or any(j["case_id"] not in official_cases for j in jobs):
        errors.append({"kind": "unexpected case or total job count"})
    for case in conditions["cases"]:
        selected = sorted((j for j in jobs if j["case_id"] == case["case_id"]), key=lambda j: j["job_id"])
        ids = {j["job_id"] for j in selected}
        if len(ids) != len(selected) or len(selected) != case["jobs"]:
            errors.append({"kind": "job count/duplicate mismatch", "case": case["case_id"]})
        for job in selected:
            predecessors = set(filter(None, job["predecessors"].split("|")))
            if not predecessors <= ids or job["job_id"] in predecessors or job["zone_id"] not in zones or job["family_id"] not in FAMILIES:
                errors.append({"kind": "invalid job reference", "job": job})
        report["tasks"].append({**case, "actual_jobs": len(selected), "family_counts": dict(Counter(j["family_id"] for j in selected)),
                                "jobs_with_predecessors": [j for j in selected if j["predecessors"]]})
    # Formal reference sequence: preserve source CSV pool order; reset per case;
    # random.Random(int(seed)).choice in sorted job_id order, with replacement.
    seed = int(conditions["validation_seeds"][0])
    assignments = []
    for case in conditions["cases"]:
        rng = random.Random(seed)
        for job in sorted((j for j in jobs if j["case_id"] == case["case_id"]), key=lambda j: j["job_id"]):
            pool = pools[("validation", job["family_id"])]
            if not pool:
                errors.append({"kind": "empty assignment pool", "job": job["job_id"]})
                continue
            unit = rng.choice(pool)
            assignments.append({"case_id": case["case_id"], "job_id": job["job_id"], "split": "validation", "unit_id": unit["unit_id"]})
    report["offline_assignment_demo"] = {"public_validation_seed": seed, "scope": "assignment only; no executed jobs or policy inputs", "assignments": assignments}
    if assignments:
        assignment = assignments[0]
        job = next(j for j in jobs if j["job_id"] == assignment["job_id"])
        unit = next(u for u in units if u["unit_id"] == assignment["unit_id"])
        record = actual.get(unit["episode_uuid"])
        if record:
            detail = next(d for d in unit_details if d["unit_id"] == unit["unit_id"])
            trace = {"assignment": assignment, "task_csv_row": job, "source_index_row": unit,
                     "record_split_row": by_uuid[unit["episode_uuid"]], "actual_directory": relative(record["folder"]),
                     "actual_metadata_path": record["metadata_path"], "actual_metadata_identity": record["identity"],
                     "source_annotation_path": record["annotation_path"],
                     "original_annotation_row": record["annotations"][int(unit["source_segment_index"] )],
                     "source_time_origin": record["origin"], "prefix_coverage": detail["prefixes"],
                     "record_boundary_evidence": record_bounds(record),
                     "nominal_p_s_audit_truth_not_policy_input": dec(unit["end_s"]) - dec(unit["start_s"]) + dec(conditions["service"]["beta_s"])}
            if record["dataset"] != "EgoDemo":
                sample_index = window(record["channels"]["pose_right_hand"]["times"], float(unit["start_s"]), float(unit["start_s"]) + 0.2)[0]
                sample_path = record["folder"] / "tables/pose_right_hand.csv"
                sample = rows(sample_path)[sample_index]
                selected_keys = ["_message_index", "header.ts", "_offset_from_first_log_s", "transforms.length"] + ["transforms[0]." + suffix for suffix in ("pos.x", "pos.y", "pos.z", "quat.w", "quat.x", "quat.y", "quat.z")]
                trace["original_first_visible_right_wrist_sample"] = {"path": relative(sample_path), "CSV_line": sample_index + 2,
                        "selected_original_fields": {key: sample[key] for key in selected_keys},
                        "note": "original wrist fields only; remaining 20 keypoints not printed"}
            report["one_actual_job_to_original_record_trace"] = trace
    if args.details:
        report["all_metadata_mappings"] = mappings
        report["all_unit_audit_rows"] = unit_details
        report["all_record_bounds"] = [{"episode_uuid": uuid, **record_bounds(record)} for uuid, record in actual.items()]
    for dataset, check in report["file_inventory"].items():
        for key in ("archive_missing", "missing", "extra", "size_mismatch", "zero_byte_files"):
            if check.get(key):
                errors.append({"kind": "file inventory concern", "dataset": dataset, "field": key, "items": check[key]})
    for key in ("missing_UUIDs", "extra_UUIDs"):
        if report["metadata_alignment"][key]:
            errors.append({"kind": key, "items": report["metadata_alignment"][key]})
    for key in ("unindexed_raw_pairs", "indexed_nonexistent_raw_pairs", "duplicate_raw_references"):
        if report["segment_alignment"][key]:
            errors.append({"kind": key, "items": report["segment_alignment"][key]})
    if report["empty_pools"]:
        errors.append({"kind": "empty split/family pools", "items": report["empty_pools"]})
    # A completed audit can report data boundary concerns without declaring the
    # fixed official pool invalid. Structural errors are separately machine-readable.
    report["audit_completed"] = True
    report["integrity_checks_passed"] = not (errors or raw_errors or length_issues)
    report["all_video_playback_bounds_checked"] = len(video_results) == len(all_videos) and not video_warnings
    print(json.dumps(report, ensure_ascii=False, indent=2, default=emit_value, allow_nan=False))
    return 0 if report["integrity_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
