"""Read-only, offline C-problem field/timeline audit of one fixed record per source.

Run from any directory with an existing Python containing numpy and pyarrow:
  /mnt/disk/zhoutianyu/miniconda3/envs/collm/bin/python -B experiments/c_audit_fields.py

Only stdout is produced: no extraction, downloads, image decoding/models, caches,
reports, or modifications to the dataset. ffprobe reads local MP4 packets; it does
not decode pictures. --skip-video omits that optional check. Paths are relative to
--root (default: repository/data/raw/C). JSON lines distinguish declared metadata,
measured facts, and unresolved interpretations. PASS is structural consistency of
these three samples, NOT proof of physical accuracy or all-record consistency.

Evidence read before implementing this audit:
* experiments/check_c_data.py (existing identifier/quality inventory).
* problem/C/2026_“ShuWei Cup”C_Problem.pdf pp. 2, 5, 6, text read locally:
  21 keypoints per hand; independent world origins across records; wrist world
  extrinsics unavailable; hand quality is not product occlusion/success; complete
  numbers may be poor observations. MCAP is described, but local Pro/Standard
  payloads are CSV/MP4. No explicit metric scale/axis convention on those pages.
* Selected local info.json, episode parquet, CSV headers and semantic JSON/CSV.
* Restocking_Tasks_and_Constraints/source_segment_index.csv and record_split.csv.

Meters and world-frame hand poses are stated in the upstream documentation cited
by research/C题调研.md, but were NOT independently revalidated online here. Local
pose values alone do not establish units, axis handedness, or active/passive
rotation. Demo info.json declares quaternion order qw,qx,qy,qz. CSV hand/head pose
columns use w,x,y,z; extrinsic CSV rotation columns instead appear as x,y,z,w.
Demo bad-frame IDs admit both zero/one-based mappings in the selected sample;
this audit reports both, never silently certifies a zero-based convention.
"""

import argparse
import csv
import json
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

sys.dont_write_bytecode = True

import numpy as np
import pyarrow
import pyarrow.parquet as pq


SAMPLES = {
    "EgoDemo": "E001",
    "EgoPro": "291aff24-8b62-4a5e-b470-85114bdf86dc",
    "EgoStandard": "01e98c50-1164-448f-a7ff-b722d7a1c19b",
}
CHECKS = []


def emit(kind, **values):
    print(json.dumps({"kind": kind, **values}, ensure_ascii=False, allow_nan=False))


def check(label, passed):
    CHECKS.append((label, bool(passed)))


def rows(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def ints(table, key):
    # Never parse epoch nanoseconds through float/pandas default mixed dtypes.
    return np.asarray([int(row[key]) for row in table], dtype=np.int64)


def stats(values):
    a = np.asarray(values, dtype=np.float64)
    a = a[np.isfinite(a)]
    return {"min": float(a.min()), "median": float(np.median(a)),
            "max": float(a.max())} if a.size else None


def maxabs(values):
    a = np.asarray(values)
    return float(np.max(np.abs(a))) if a.size else 0.0


def relative_ns(stamps):
    # Integer subtraction BEFORE conversion avoids catastrophic epoch cancellation.
    return (stamps - stamps[0]).astype(np.float64) / 1e9


def timeline(stamps, ns=False):
    a = np.asarray(stamps)
    d = np.diff(a)
    scale = 1e9 if ns else 1.0
    return {
        "n": len(a), "first": a[0].item(), "last": a[-1].item(),
        "span_s": float(a[-1] - a[0]) / scale,
        "duplicate_timestamps": int(len(a) - len(np.unique(a))),
        "nonpositive_dt": int(np.count_nonzero(d <= 0)),
        "dt_s": stats(d / scale),
        "dt_gt_1_5_median": int(np.count_nonzero(d > 1.5 * np.median(d))),
        "dt_counts_native_top8": Counter(d.tolist()).most_common(8),
    }


def numeric_summary(a, quaternion=False):
    a = np.asarray(a, dtype=np.float64)
    result = {"shape": list(a.shape), "nonfinite_elements": int((~np.isfinite(a)).sum()),
              "first": a[0].reshape(-1)[:7].tolist()}
    if quaternion:
        result["quaternion_norm"] = stats(np.linalg.norm(a, axis=-1))
    else:
        result["coordinate_range_raw_unit"] = stats(a)
    return result


def exact_alignment(stamps, reference):
    same_shape = len(stamps) == len(reference)
    return {"n": len(stamps), "same_order_exact": bool(np.array_equal(stamps, reference)),
            "missing_reference_keys": len(set(reference.tolist()) - set(stamps.tolist())),
            "extra_keys": len(set(stamps.tolist()) - set(reference.tolist())),
            "max_row_delta_ns": maxabs(stamps - reference) if same_shape else None}


def iso_ns(text):
    """Parse ISO UTC nanoseconds exactly; datetime alone truncates to microseconds."""
    body = text.removesuffix("Z")
    whole, dot, fraction = body.partition(".")
    seconds = int(datetime.fromisoformat(whole + "+00:00").timestamp())
    return seconds * 10**9 + int((fraction if dot else "").ljust(9, "0"))


def probe(path, expected_times, ffprobe, root):
    cmd = [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
           "stream=codec_name,width,height,r_frame_rate,avg_frame_rate,time_base,"
           "start_pts,start_time,duration,nb_frames:packet=pts,dts,duration", "-of", "json", str(path)]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=90)
    data = json.loads(result.stdout)
    stream = data["streams"][0]
    packets = data.get("packets", [])
    ticks = [int(p["pts"]) for p in packets if "pts" in p]
    # H.264 B-frames in Demo reorder packets. Sort PTS for presentation order;
    # packet order / DTS are NOT display-frame indices.
    time_base = Fraction(stream["time_base"])
    presentation = np.asarray([float(p * time_base) for p in sorted(ticks)])
    matching_count = len(presentation) == len(expected_times)
    delta = presentation - expected_times if matching_count else np.asarray([])
    check(f"{path.relative_to(root)} packet/display count", matching_count)
    check(f"{path.relative_to(root)} unique PTS", len(set(ticks)) == len(ticks))
    check(f"{path.relative_to(root)} nb_frames", int(stream["nb_frames"]) == len(expected_times))
    emit("measured.video_container", path=str(path.relative_to(root)), stream=stream,
         packets=len(packets), packets_missing_pts=len(packets) - len(ticks),
         packet_order_is_presentation_order=all(a < b for a, b in zip(ticks, ticks[1:])),
         first_packet=packets[0], last_packet=packets[-1],
         presentation_first_last_s=[float(presentation[0]), float(presentation[-1])],
         presentation_dt_s=stats(np.diff(presentation)),
         packet_duration_ticks=sorted({p.get("duration", 0) for p in packets}),
         delta_pts_minus_source_s=stats(delta), max_abs_delta_s=maxabs(delta),
         final_delta_s=float(delta[-1]) if delta.size else None,
         mapping="sorted presentation PTS vs row/frame index; timing consistency only, not visual synchronization")


def semantics(dataset, folder, uuid, segments, time_s, index_rows, root, nominal_time_s=None):
    indexed = sorted((r for r in index_rows if r["episode_uuid"] == uuid),
                     key=lambda r: int(r["source_segment_index"]))
    matches = len(indexed) == len(segments) and all(
        int(r["source_segment_index"]) == i
        and r["dataset"] == dataset and r["sample"] == folder.name
        and float(r["start_s"]) == s["start"] and float(r["end_s"]) == s["end"]
        and r["description"] == s["description"]
        and abs(float(r["duration_s"]) - (s["end"] - s["start"])) < 1e-12
        for i, (r, s) in enumerate(zip(indexed, segments)))
    check(f"{dataset} semantic source index", matches)
    membership = np.zeros(len(time_s), dtype=int)
    details = []
    for i, segment in enumerate(segments):
        lo, hi = segment["start"], segment["end"]
        # Explicit audit convention, not claimed to be an official endpoint rule.
        selected = (time_s >= lo) & (time_s < hi)
        membership += selected
        details.append({"index": i, "start_s": lo, "end_s": hi,
                        "rows_half_open": int(selected.sum()),
                        "nearest_start_error_s": float(np.min(np.abs(time_s - lo))),
                        "nearest_end_error_s": float(np.min(np.abs(time_s - hi)))})
    nominal_counts = None
    nominal_uncovered = None
    if nominal_time_s is not None:
        nominal_masks = [(nominal_time_s >= s["start"]) & (nominal_time_s < s["end"])
                         for s in segments]
        nominal_counts = [int(mask.sum()) for mask in nominal_masks]
        nominal_uncovered = int(np.count_nonzero(np.sum(nominal_masks, axis=0) == 0))
    emit("measured.semantics", dataset=dataset, path=str(folder.relative_to(root)),
         source_index_matches=matches, indexed_family_ids=sorted({r["family_id"] for r in indexed}),
         nominal_frame_over_fps_half_open_counts=nominal_counts,
         nominal_frame_over_fps_uncovered_rows=nominal_uncovered,
         boundary_caveat="float32 timestamp and frame/fps can classify an endpoint differently; neither is silently substituted",
         first_segment=segments[0], segments=details,
         endpoint_rule="audit-only [start,end), exact stored timestamp comparisons; no epsilon/snapping",
         uncovered_rows=int(np.count_nonzero(membership == 0)),
         multiply_covered_rows=int(np.count_nonzero(membership > 1)),
         annotation_end_s=max(s["end"] for s in segments),
         last_trajectory_minus_annotation_end_s=float(time_s[-1] - max(s["end"] for s in segments)),
         caveat="semantic text/source-index agreement is not video/content or family-label validation")


def identity(dataset, folder, uuid, split_rows, root):
    matches = [r for r in split_rows if r["episode_uuid"] == uuid]
    check(f"{dataset} split identity", len(matches) == 1 and matches[0]["dataset"] == dataset)
    emit("measured.identity", dataset=dataset, path=str(folder.relative_to(root)),
         episode_uuid=uuid, folder_equals_uuid=folder.name == uuid,
         record_split=matches[0] if matches else None)


def audit_demo(root, split_rows, index_rows, ffprobe):
    folder = root / "Raw_Data/EgoDemo" / SAMPLES["EgoDemo"]
    info = json.loads((folder / "meta/info.json").read_text())
    meta_path = folder / "meta/episodes/chunk-000/file-000.parquet"
    meta = pq.read_table(meta_path).to_pylist()[0]
    data_path = folder / "data/chunk-000/file-000.parquet"
    table = pq.read_table(data_path)
    n = table.num_rows
    identity("EgoDemo", folder, meta["episode_uuid"], split_rows, root)
    features = info["features"]
    emit("declared.demo_metadata", path=str((folder / "meta/info.json").relative_to(root)),
         fps=info["fps"], total_frames=info["total_frames"],
         note="info.json splits.train is local packaging, NOT official environment split",
         joint_order=features["observation.state.hand_right_world"]["names"][0],
         xyz_order=features["observation.state.hand_right_world"]["names"][1],
         quaternion_order=features["observation.state.hand_right_world_rotation"]["names"][1],
         intrinsic_vectors={k: v for k, v in meta.items() if k.startswith("camera_intrinsics/")},
         intrinsic_caveat="eight floats per camera; component meanings/units are not declared in these metadata")
    check("Demo total_frames", n == info["total_frames"] == meta["length"])
    fields = {}
    for field in table.schema:
        a = np.asarray(table[field.name].to_pylist())
        fields[field.name] = {"arrow_type": str(field.type), "declared_shape": features[field.name]["shape"],
                              "actual_shape": list(a.shape), "top_level_nulls": table[field.name].null_count}
        if field.name.startswith("observation.state"):
            fields[field.name].update(numeric_summary(a, "rotation" in field.name))
            check(f"Demo {field.name} shape", list(a.shape[1:]) == features[field.name]["shape"])
    emit("measured.demo_fields", path=str(data_path.relative_to(root)), table_shape=[n, table.num_columns], fields=fields)
    t = np.asarray(table["timestamp"].to_pylist(), dtype=np.float64)
    frame = np.asarray(table["frame_index"].to_pylist(), dtype=np.int64)
    check("Demo frame index contiguous zero based", np.array_equal(frame, np.arange(n)))
    check("Demo timestamp strictly increasing", np.all(np.diff(t) > 0))
    emit("measured.demo_time", timeline=timeline(t),
         frame_index_first_last=[int(frame[0]), int(frame[-1])],
         max_timestamp_minus_frame_over_fps_s=maxabs(t - frame / info["fps"]),
         inferred_basis="relative seconds; agrees with frame_index/fps to float32 rounding; no epoch retained",
         meta_video_intervals={k: v for k, v in meta.items() if k.endswith(("/from_timestamp", "/to_timestamp"))})
    quality = json.loads((folder / "bad_frame_ratio.json").read_text())
    marked = [r for r in quality["hand_bad_frame_details"] if r["left_bad_reason"] or r["right_bad_reason"]]
    ids = [r["frame"] for r in marked]
    check("Demo quality total_frames", quality["total_frames"] == n)
    check("Demo quality count metadata", len(set(ids)) == meta["quality/hand_bad_frame_count"])
    zero_valid, one_valid = all(0 <= x < n for x in ids), all(1 <= x <= n for x in ids)
    emit("measured.demo_quality", path=str((folder / "bad_frame_ratio.json").relative_to(root)),
         total_frames=quality["total_frames"], sparse_entries=len(marked), unique_frames=len(set(ids)),
         reasons=dict(Counter(f"{side}:{r[side + '_bad_reason']}" for r in marked for side in ("left", "right") if r[side + '_bad_reason'])),
         first_entry=marked[0] if marked else None, zero_based_in_bounds=zero_valid, one_based_in_bounds=one_valid,
         first_candidate_times_s={"zero_based": float(t[ids[0]]) if ids and zero_valid else None,
                                  "one_based": float(t[ids[0] - 1]) if ids and one_valid else None},
         conclusion="index base unresolved when both candidates are in bounds; absent reason means unflagged, not independent truth")
    annotations = json.loads((folder / "annotation.json").read_text())
    segments = [{"start": float(s["start_time"]), "end": float(s["end_time"]), "description": s["description"]}
                for s in annotations["subtask_annotations"]]
    semantics("EgoDemo", folder, meta["episode_uuid"], segments, t, index_rows, root,
              nominal_time_s=frame / info["fps"])
    if ffprobe:
        for path in sorted((folder / "videos").rglob("*.mp4")):
            probe(path, t, ffprobe, root)


def pose_array(table, count, component, axes):
    return np.asarray([[[float(r[f"transforms[{j}].{component}.{axis}"])
                         if r[f"transforms[{j}].{component}.{axis}"] else np.nan
                         for axis in axes] for j in range(count)] for r in table])


def audit_csv(dataset, root, split_rows, index_rows, ffprobe):
    folder = root / "Raw_Data" / dataset / SAMPLES[dataset]
    metadata = {r["field"]: r["value"] for r in rows(folder / "session_metadata.csv")}
    uuid = metadata["task_info.episode_uuid"]
    identity(dataset, folder, uuid, split_rows, root)
    tables = {p.stem: rows(p) for p in sorted((folder / "tables").glob("*.csv"))}
    right = tables["pose_right_hand"]
    ts = ints(right, "header.ts")
    t = relative_ns(ts)
    n = len(ts)
    origin = int(metadata["header.ts"])
    check(f"{dataset} trajectory unique/ordered", np.all(np.diff(ts) > 0))
    emit("declared.csv_metadata", dataset=dataset, path=str((folder / "session_metadata.csv").relative_to(root)),
         metadata_header_ts=origin,
         task_name=metadata.get("task_info.task_name"),
         task_description=metadata.get("task_info.task_description"),
         timing_and_devices={k: v for k, v in metadata.items() if k.startswith(("devices", "global_time"))},
         caveat="declared devices (including depth) do not prove corresponding delivered files exist")
    emit("measured.csv_time", dataset=dataset, timeline=timeline(ts, ns=True),
         first_pose_minus_metadata_header_ns=int(ts[0]) - origin,
         metadata_device_start_scaled_ns=int(metadata["devices[0].unix_start_time"]) * 1000000,
         metadata_device_end_minus_last_pose_ns=int(metadata["devices[0].unix_end_time"]) * 1000000 - int(ts[-1]),
         first_pose_minus_global_iso_start_ns=int(ts[0]) - iso_ns(metadata["global_time.start_time_iso"]),
         naive_float_epoch_conversion_max_error_ns=int(np.max(np.abs(ts.astype(np.float64).astype(np.int64) - ts))),
         inferred_basis="header.ts equals *_time_ns; device unix times fit milliseconds; global ISO loses subsecond precision")
    inventories = []
    for name, table in tables.items():
        key = "header.ts" if "header.ts" in table[0] else "_log_time_ns"
        stamps = ints(table, key)
        inventories.append({"name": name, "rows": len(table), "columns": len(table[0]),
                            "timestamp_key": key,
                            "time_alignment": exact_alignment(stamps, ts),
                            "empty_cells": sum(v == "" for r in table for v in r.values())})
        if name != "annotation_semantic_segments":
            check(f"{dataset}/{name} time alignment", np.array_equal(stamps, ts))
        check(f"{dataset}/{name} log/publish timestamps", np.array_equal(ints(table, "_log_time_ns"), ints(table, "_publish_time_ns")))
        check(f"{dataset}/{name} source timestamp equals log", np.array_equal(stamps, ints(table, "_log_time_ns")))
        errors = [abs(Decimal(r["_offset_from_first_log_s"]) * Decimal(10**9) - (int(r["_log_time_ns"]) - origin)) for r in table]
        check(f"{dataset}/{name} exact offset consistency", max(errors) == 0)
        if "timestamp" in table[0]:
            check(f"{dataset}/{name} ISO timestamp", all(iso_ns(r["timestamp"]) == int(r["_log_time_ns"]) for r in table))
        if "transforms[0].timestamp" in table[0]:
            check(f"{dataset}/{name} transform ISO timestamp", all(iso_ns(r["transforms[0].timestamp"]) == int(r["_log_time_ns"]) for r in table))
    emit("measured.csv_inventory_alignment", dataset=dataset, tables=inventories,
         key_columns=["_message_index", "_log_time_ns", "_publish_time_ns", "_sequence", "_offset_from_first_log_s", "header.ts", "header.sender", "header.count"],
         note="semantic rows are interval starts, intentionally not one row per pose")
    for name in ("pose_left_hand", "pose_right_hand", "pose_head", "pose_headcam", "pose_right_eye_cam"):
        table = tables[name]
        length = int(table[0]["transforms.length"])
        check(f"{dataset}/{name} transform count", all(int(r["transforms.length"]) == length for r in table))
        check(f"{dataset}/{name} header.count", np.array_equal(ints(table, "header.count"), np.arange(n)))
        positions = pose_array(table, length, "pos", "xyz")
        rotations = pose_array(table, length, "quat", "wxyz")
        emit("measured.csv_pose", dataset=dataset, name=name, transforms_length=length,
             position=numeric_summary(positions), quaternion=numeric_summary(rotations, True),
             physical_unit="not declared in CSV; metric/world hand interpretation depends on upstream dictionary",
             keypoint_zero=("not named in CSV; wrist identity is upstream, explicitly named only in Demo metadata"
                            if "hand" in name else "single head/camera transform; not a hand keypoint"))
    quality = tables["annotation_bad_frame_pose_hand"]
    flagged = [r for r in quality if r["is_bad"].lower() == "true"]
    reason_counts = Counter(r[f"problem_type[{j}]"] for r in quality for j in range(int(r["problem_type.length"])))
    check(f"{dataset} quality flag/reasons agree", all((r["is_bad"].lower() == "true") == (int(r["problem_type.length"]) > 0) for r in quality))
    emit("measured.csv_quality", dataset=dataset, rows=len(quality), flagged=len(flagged),
         reasons=dict(reason_counts), fields=list(quality[0]), first_flagged=flagged[0] if flagged else None,
         note="CSV booleans parsed explicitly; bool('False') would incorrectly be True")
    for view, pose_name in (("head_left", "pose_headcam"), ("head_right", "pose_right_eye_cam")):
        extr = tables[f"sensor_camera_{view}_extrinsic"]
        intr = tables[f"sensor_camera_{view}_intrinsic"]
        p = pose_array(tables[pose_name], 1, "pos", "xyz")[:, 0, :]
        q = pose_array(tables[pose_name], 1, "quat", "wxyz")[:, 0, :]
        ep = pose_array(extr, 1, "translation", "xyz")[:, 0, :]
        eq = pose_array(extr, 1, "rotation", "wxyz")[:, 0, :]
        emit("measured.camera_calibration", dataset=dataset, view=view,
             frames=sorted({(r["transforms[0].parent_frame_id"], r["transforms[0].child_frame_id"]) for r in extr}),
             extrinsic_rotation_file_order=[k for k in extr[0] if ".rotation." in k],
             matches_pose=pose_name, max_translation_component_delta=maxabs(p - ep),
             max_quaternion_sign_invariant_l2_delta=float(np.max(np.minimum(np.linalg.norm(q - eq, axis=1), np.linalg.norm(q + eq, axis=1)))),
             intrinsic_first={k: v for k, v in intr[0].items() if not k.startswith("_")},
             intrinsic_static=all(all(r[k] == intr[0][k] for k in intr[0] if not k.startswith("_") and k != "timestamp") for r in intr),
             matrix_shapes="K:9 -> 3x3; R:9 -> 3x3; P:12 -> 3x4 (reshape is conventional inference)",
             wrist_world_extrinsic_files=list(p.name for p in (folder / "tables").glob("*wrist*extrinsic*")))
    semantic = tables["annotation_semantic_segments"]
    segments = [{"start": float(r["segment.start_time"]), "end": float(r["segment.end_time"]),
                 "description": r["segment.subtask_description"]} for r in semantic]
    semantic_ns_errors = [int(r["header.ts"]) - int(ts[0]) - int(Decimal(r["segment.start_time"]) * Decimal(10**9)) for r in semantic]
    check(f"{dataset} semantic timestamps match segment start", maxabs(semantic_ns_errors) == 0)
    emit("measured.semantic_clock", dataset=dataset, max_start_time_ns_error=maxabs(semantic_ns_errors),
         note="header.ts = first pose timestamp + segment.start_time seconds in this sample")
    semantics(dataset, folder, uuid, segments, t, index_rows, root)
    for path in sorted((folder / "videos").glob("*_frames.csv")):
        table = rows(path)
        stamps = ints(table, "video_timestamp_ns")
        align = exact_alignment(stamps, ts)
        check(f"{dataset}/{path.name} video timestamp alignment", align["same_order_exact"])
        check(f"{dataset}/{path.name} message indices", np.array_equal(ints(table, "_message_index"), np.arange(n)))
        emit("measured.video_sidecar", dataset=dataset, path=str(path.relative_to(root)), fields=list(table[0]),
             first_timing={k: table[0][k] for k in table[0] if k not in ("encoded_sha256", "encoded_bytes")},
             alignment=align, log_equals_video_ts=bool(np.array_equal(ints(table, "_log_time_ns"), stamps)),
             note="frame_id is camera name, not numeric frame index; _message_index is zero-based row index here")
        if ffprobe:
            probe(path.with_name(path.name.replace("_frames.csv", ".mp4")), relative_ns(stamps), ffprobe, root)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1] / "data/raw/C")
    parser.add_argument("--skip-video", action="store_true")
    parser.add_argument("--ffprobe", help="existing local ffprobe executable; never installed by this script")
    args = parser.parse_args()
    root = args.root.resolve()
    ffprobe = None if args.skip_video else (args.ffprobe or shutil.which("ffprobe") or ("/usr/bin/ffprobe" if Path("/usr/bin/ffprobe").is_file() else None))
    emit("audit_scope", root=str(root), samples=SAMPLES, python=sys.version.split()[0],
         numpy=np.__version__, pyarrow=pyarrow.__version__, ffprobe=ffprobe,
         video_status="enabled" if ffprobe else "SKIPPED: requested or ffprobe unavailable",
         access="local reads; stdout only; no extraction, network, image decoding or model inference")
    emit("evidence_limits",
         explicit_problem="PDF pp2,5,6: 21 hand points; world origins not shared; no wrist-world extrinsics; flags concern human hands, not truth/success",
         explicit_local="Demo feature dimensions/names and quaternion order; CSV component names, ns/s suffixes, camera parent/child labels",
         inferred="relative/epoch clock mappings, matrix layout, frame-to-PTS association are checked numerically on three samples",
         unresolved="physical units/axis direction/hand frame from CSV alone; Demo sparse-quality index base; semantic visual correctness; capture exposure/latency synchronization",
         prior_report_only="research/C题调研.md cites upstream metric/world/wrist conventions; no web verification in this audit")
    split_rows = rows(root / "Restocking_Tasks_and_Constraints/record_split.csv")
    index_rows = rows(root / "Restocking_Tasks_and_Constraints/source_segment_index.csv")
    audit_demo(root, split_rows, index_rows, ffprobe)
    for dataset in ("EgoPro", "EgoStandard"):
        audit_csv(dataset, root, split_rows, index_rows, ffprobe)
    failed = [label for label, passed in CHECKS if not passed]
    emit("validation", passed=len(CHECKS) - len(failed), failed=failed,
         scope="three fixed samples only; unresolved conventions and measured PTS drift are not silently treated as failures or truths")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
