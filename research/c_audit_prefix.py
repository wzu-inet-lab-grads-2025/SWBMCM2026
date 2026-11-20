"""Read-only C/Q3 information-access and existing-prefix audit (not a policy).

Run from any directory with Python 3.10+ (standard library only):
    PYTHONDONTWRITEBYTECODE=1 python3 research/c_audit_prefix.py
Optional local PDF phrase checks need the already installed pypdf package:
    PYTHONDONTWRITEBYTECODE=1 python3 research/c_audit_prefix.py --pdf-quotes

Only stdout is written. No sampling, fitting, scheduler, strategy, network, video
extraction, interpolation, filling, correction, or raw-data modification occurs.
IDs, endpoints, file hashes and full-file counts printed here are RETROSPECTIVE
AUDIT EVIDENCE, not a policy observation. This is not a sandbox/access-control
implementation. It checks one training unit, not every record or a full replay.

Time convention: use the common device unix_start_time (milliseconds) as record
zero; verify it against header.ts minus _offset_from_first_log_s. Compare integer
nanoseconds to Decimal annotation boundaries. Report both [a,a+w] and [a,a+w),
without epsilon. The PDF does not specify endpoint inclusivity or nanosecond
rounding; alternative first-sample anchoring and unsafe float arithmetic are
reported, not silently substituted for recorded time.

Normative references: local C statement pp. 3-6; Problem_3_Requirements.pdf
section 2 (pp. 1-2), sections 3-5 (pp. 2-3); common_conditions.json.
"""

import argparse
import csv
import hashlib
import io
import json
import math
import re
from decimal import Decimal
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "data/raw/C"
RULES = ROOT / "Restocking_Tasks_and_Constraints"
DEFAULT_UNIT = "U_883c40426953"
NS = 1_000_000_000
INPUT_HASHES = {}
POSE_CHANNELS = {
    "pose_left_hand": 21,
    "pose_right_hand": 21,
    "pose_head": 1,
    "pose_headcam": 1,
    "pose_right_eye_cam": 1,
}
# Canonical anatomical/channel indices are not source identifiers.
COMPONENTS = ("pos.x", "pos.y", "pos.z", "quat.w", "quat.x", "quat.y", "quat.z")
TRANSPORT_FIELDS = (
    "_message_index", "_log_time_ns", "_publish_time_ns", "_sequence",
    "_offset_from_first_log_s", "header.sender", "header.count",
)

# A static audit checklist, NOT executable action logic. "Returned" is essential.
ACCESS_RULES = [
    ("trial_reset/before_release",
     "Public tasks: case_id, job_id, family_id, release_s, due_s, priority_weight, "
     "zone_id, predecessors; public zones, mapping, budgets, frozen model; reset trial state.",
     "No source prefix before release. No policy seed, assignment, source index/IDs, "
     "endpoint, duration, semantic text, videos, or validation/test truth cache."),
    ("release",
     "Existing timestamps and allowed pose/quality samples in cumulative 0.2 source seconds.",
     "No later samples, source IDs, endpoints, total duration, descriptions or full-file statistics."),
    ("review_requested_before_return",
     "Previously returned prefix and public resource changes only; delay is 1 scenario second, "
     "cost is 1. Review only released/unattempted jobs, at most twice, within K/B and return<=H; "
     "completed predecessors are NOT required for review.",
     "Do not expose newly requested rows/flags/features until return; no parallel action."),
    ("review_1_return",
     "Cumulative 0.6 source seconds after delay, not 0.2 + 0.6; same allowed channels.",
     "No samples past 0.6, no hidden-source lookup from any returned signature."),
    ("review_2_return",
     "Cumulative 1.0 source seconds after delay, not 0.2 + 0.6 + 1.0.",
     "No third review, future samples, endpoint or duration disclosure."),
    ("wait",
     "Public clock/resources and already returned data; another job may reach its release.",
     "Waiting does not extend any existing job prefix; no automatic source-time streaming."),
    ("execute_acceptance",
     "Release, attempted/completed status, predecessor feedback, zone, cost, b, H; "
     "check s+b against H without unrevealed p.",
     "No p/d/endpoint oracle in feasibility or reservation; no retroactive decision revision."),
    ("execution_feedback_returned",
     "Only actual feedback already returned in this trial: completion/time, occupancy, "
     "observed overrun/idle/censoring. Completed C and s can reveal p=C-s and hence "
     "d=(p-beta)/alpha. Current assessments may update, but equipment stays occupied "
     "until s+max(p,b).",
     "No exact censored p from the scorer: unfinished at H gives p>H-s only. "
     "No executed-source lookup/prefix matching; no validation/test truth fitting for later trials."),
    ("termination/offline_scoring",
     "Scorer may use source d/p and hidden references; log executed-source references "
     "after completion/termination for retrospective checking. Report observed overrun "
     "max(min(p,H-s)-b,0), completed-only idle, and all common metrics.",
     "Offline truth, source-reference logs, hidden-state errors and full trajectories "
     "must not flow back to an earlier/current policy decision or later-trial fitting."),
]


def relative(path):
    return str(path.relative_to(REPO))


def emit(label, value):
    print(label, json.dumps(value, ensure_ascii=True, sort_keys=True))


def read_bytes(path):
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if path in INPUT_HASHES:
        assert INPUT_HASHES[path] == digest, f"Input changed while reading: {relative(path)}"
    INPUT_HASHES[path] = digest
    return data


def read_json(path):
    return json.loads(read_bytes(path).decode("utf-8-sig"))


def read_csv(path):
    reader = csv.DictReader(io.StringIO(read_bytes(path).decode("utf-8-sig"), newline=""))
    return reader.fieldnames, list(reader)


def ns_from_seconds(value):
    scaled = Decimal(str(value)) * NS
    assert scaled == scaled.to_integral_value(), "Boundary needs sub-nanosecond precision"
    return int(scaled)


def seconds(value):
    return format(Decimal(value) / NS, ".9f")


def check_pose_table(fields, rows, points):
    pose_fields = [f"transforms[{i}].{component}"
                   for i in range(points) for component in COMPONENTS]
    assert set(fields) == set(TRANSPORT_FIELDS) | {"header.ts", "transforms.length"} | set(pose_fields)
    assert all(int(row["transforms.length"]) == points for row in rows)
    times = [int(row["header.ts"]) for row in rows]
    assert times == sorted(set(times)), "Timestamp duplicates or non-monotonic order"
    return pose_fields, times


def bad_flag(row):
    assert row["is_bad"] in ("True", "False"), "Unknown boolean encoding"
    return row["is_bad"] == "True"  # bool("False") would incorrectly be True.


def quality_reasons(row):
    # Use only this row's declared original flags, not the whole-file maximum
    # reason-array width. CSV column existence can otherwise disclose later flags.
    size = int(row["problem_type.length"])
    return [row[f"problem_type[{i}]"] for i in range(size)]


def verify_pdfs(requirements, statement):
    from pypdf import PdfReader

    requests = {
        requirements: {
            1: ("Returned execution feedback may update current assessments.",
                "Information returns only after the delay.",
                "contain only existing samples within the window"),
            2: ("No prior filling, interpolation or correction is applied.",
                "Do not use unrevealed p in this check.",
                "Log executed-source references after completion or termination"),
            3: ("Seeds are not policy inputs",),
        },
        statement: {
            3: ("returned information cannot be used before its delay has elapsed",),
            5: ("When hiding a reference state, also hide state features calculated from it",),
        },
    }
    for path, pages in requests.items():
        document = PdfReader(io.BytesIO(read_bytes(path)))
        for page_number, phrases in pages.items():
            compact = re.sub(r"\s+", "", document.pages[page_number - 1].extract_text())
            for phrase in phrases:
                assert re.sub(r"\s+", "", phrase) in compact, (relative(path), page_number, phrase)
            emit("pdf_verified", {"path": relative(path), "page": page_number, "phrases": phrases})


def audit(unit_id, pdf_quotes):
    requirements = RULES / "Problem_3_Requirements.pdf"
    statement = REPO / "problem/C/2026_\u201cShuWei Cup\u201dC_Problem.pdf"
    # Always fingerprint the references, even in standard-library-only mode.
    for path in (requirements, statement, REPO / "research/check_c_data.py"):
        read_bytes(path)
    if pdf_quotes:
        verify_pdfs(requirements, statement)
    conditions = read_json(RULES / "common_conditions.json")
    widths = [ns_from_seconds(w) for w in conditions["observation"]["prefix_seconds"]]
    assert widths == [200_000_000, 600_000_000, 1_000_000_000]
    assert conditions["observation"]["delay_s"] == 1.0
    assert conditions["observation"]["cost"] == 1.0
    assert conditions["observation"]["max_per_job"] == 2
    emit("scope", {"read_only": True, "training_unit_only": True, "policy_implemented": False,
                   "output_role": "offline audit; contains IDs/truth forbidden as policy input",
                   "rules_version": conditions["version"]})
    for node, allowed, forbidden in ACCESS_RULES:
        emit("access_node", {"node": node, "allowed": allowed, "forbidden": forbidden})

    _, units = read_csv(RULES / "source_segment_index.csv")
    matches = [(line, row) for line, row in enumerate(units, 2) if row["unit_id"] == unit_id]
    assert len(matches) == 1, "Unknown/nonunique unit_id"
    unit_line, unit = matches[0]
    assert unit["eligible"] == "1" and unit["split"] == "train", "Audit selects training units only"
    assert unit["dataset"] in ("EgoPro", "EgoStandard"), "This numeric audit covers CSV records only"
    _, splits = read_csv(RULES / "record_split.csv")
    split_line, split = next((line, row) for line, row in enumerate(splits, 2)
                            if row["episode_uuid"] == unit["episode_uuid"])
    assert all(split[key] == unit[key] for key in ("dataset", "environment_id", "split"))
    folder = ROOT / "Raw_Data" / unit["dataset"] / unit["sample"]
    _, metadata_rows = read_csv(folder / "session_metadata.csv")
    metadata = {row["field"]: row["value"] for row in metadata_rows}
    assert metadata["task_info.episode_uuid"] == unit["episode_uuid"]
    assert metadata["task_info.environment_id"] == unit["environment_id"]
    starts = {Decimal(value) * 1_000_000 for key, value in metadata.items()
              if re.fullmatch(r"devices\[\d+\]\.unix_start_time", key)}
    assert len(starts) == 1 and all(v == v.to_integral_value() for v in starts)
    origin = int(next(iter(starts)))
    start_ns = ns_from_seconds(unit["start_s"])
    low = origin + start_ns
    _, annotations = read_csv(folder / "tables/annotation_semantic_segments.csv")
    annotation_index = int(unit["source_segment_index"])
    annotation = annotations[annotation_index]
    assert Decimal(annotation["segment.start_time"]) == Decimal(unit["start_s"])
    assert Decimal(annotation["segment.end_time"]) == Decimal(unit["end_s"])
    # Trust official eligible membership, not a floating-point d >= 1.2 refilter.
    assert Decimal(unit["end_s"]) - Decimal(unit["start_s"]) >= Decimal(max(widths)) / NS
    emit("audit_source_only", {
        "unit_id": unit_id, "folder": relative(folder), "internal_episode_uuid": unit["episode_uuid"],
        "folder_name_equals_uuid": folder.name == unit["episode_uuid"],
        "split": unit["split"], "family": unit["family_id"],
        "source_index_csv_line": unit_line, "split_csv_line": split_line,
        "annotation_csv_line": annotation_index + 2, "source_segment_index_zero_based": annotation_index,
        "annotation_start_s": unit["start_s"], "annotation_end_s": unit["end_s"],
        "annotation_duration_s": str(Decimal(unit["end_s"]) - Decimal(unit["start_s"])),
        "record_zero_ns": origin,
    })

    tables, pose_fields, times = {}, {}, {}
    for name, points in POSE_CHANNELS.items():
        fields, rows = read_csv(folder / "tables" / f"{name}.csv")
        pose_fields[name], times[name] = check_pose_table(fields, rows, points)
        tables[name] = rows
        for row in rows:
            # Decimal preserves the source export's exact decimal seconds.
            assert Decimal(int(row["header.ts"]) - origin) == Decimal(row["_offset_from_first_log_s"]) * NS
        emit("pose_schema", {"table": name, "rows_offline_only": len(rows),
                             "pose_scalar_fields": len(pose_fields[name]),
                             "transport_not_feature_whitelist": TRANSPORT_FIELDS,
                             "time_field_permitted_but_not_lookup_key": "header.ts"})
    reference = times["pose_right_hand"]
    assert all(value == reference for value in times.values()), "Do not silently align/interpolate channels"
    quality_fields, quality = read_csv(folder / "tables/annotation_bad_frame_pose_hand.csv")
    assert [int(row["header.ts"]) for row in quality] == reference
    for row in quality:
        assert Decimal(int(row["header.ts"]) - origin) == Decimal(row["_offset_from_first_log_s"]) * NS
        assert bad_flag(row) == bool(quality_reasons(row)), "Flag/reason mismatch"
    emit("quality_schema", {"columns": quality_fields,
                            "allowed": "is_bad and original per-row problem_type flags within returned window",
                            "warning": "quality reasons are flags, not semantic task descriptions; no future/global quality ratio"})

    # Verify left/right head-camera naming locally against explicit child-frame
    # extrinsics. These calibration files/IDs are audit evidence, not extra inputs.
    for pose, side in (("pose_headcam", "left"), ("pose_right_eye_cam", "right")):
        path = folder / "tables" / f"sensor_camera_head_{side}_extrinsic.csv"
        _, extrinsics = read_csv(path)
        assert [int(row["_log_time_ns"]) for row in extrinsics] == reference
        max_delta = 0.0
        for row, ext in zip(tables[pose], extrinsics):
            assert ext["transforms[0].child_frame_id"] == f"head_{side}_camera"
            assert ext["transforms[0].parent_frame_id"] == "world"
            for component in COMPONENTS:
                other = component.replace("pos.", "translation.").replace("quat.", "rotation.")
                delta = abs(float(row[f"transforms[0].{component}"]) - float(ext[f"transforms[0].{other}"]))
                max_delta = max(max_delta, delta)
        assert max_delta < 1e-10
        emit("head_camera_alias_audit", {"pose": pose, "child_frame": f"head_{side}_camera",
                                         "max_component_difference": max_delta})

    video_frames = {}
    for side in ("left", "right"):
        path = folder / "videos" / f"head_{side}_frames.csv"
        fields, rows = read_csv(path)
        assert [int(row["video_timestamp_ns"]) for row in rows] == reference
        video_frames[side] = rows
        emit("video_manifest_audit_only", {"path": relative(path), "rows": len(rows),
                                          "columns": fields, "policy_input": False,
                                          "frame_id_is_channel_name_not_source_uuid": sorted({r["frame_id"] for r in rows})})

    previous = set()
    counts, bad_counts = [], []
    for width in widths:
        high = low + width
        selected = [i for i, time in enumerate(reference) if low <= time <= high]
        half_open = [i for i, time in enumerate(reference) if low <= time < high]
        assert selected and previous <= set(selected)
        new = set(selected) - previous
        first, last = selected[0], selected[-1]
        counts.append(len(selected))
        bad_indices = [i for i in selected if bad_flag(quality[i])]
        bad_counts.append(len(bad_indices))
        nonfinite = {name: sum(not row[field] or not math.isfinite(float(row[field]))
                              for i in selected for row in (rows[i],) for field in pose_fields[name])
                     for name, rows in tables.items()}
        prefix = {
            "window_source_s": seconds(width), "integer_bounds_ns_closed": [low, high],
            "counts_each_of_five_pose_channels_and_quality": len(selected),
            "new_rows_not_cumulative_sum": len(new), "half_open_count": len(half_open),
            "exact_endpoint_rows": len(selected) - len(half_open),
            "csv_lines_1based_including_header": [first + 2, last + 2],
            "row_indices_0based_audit_only": [first, last],
            "header_count_audit_only": [tables["pose_right_hand"][i]["header.count"] for i in (first, last)],
            "existing_time_ns_first_last": [reference[first], reference[last]],
            "time_from_annotation_start_s_first_last": [seconds(reference[i] - low) for i in (first, last)],
            "hand_keypoints_each": 21, "pose_scalar_fields_total_per_time": sum(map(len, pose_fields.values())),
            "nonfinite_or_missing_pose_cells_by_channel": nonfinite,
            "bad_timepoints": len(bad_indices),
            "bad_rows_audit_only": [{"row_index": i, "csv_line": i + 2,
                                     "relative_s": seconds(reference[i] - low),
                                     "reasons": quality_reasons(quality[i])} for i in bad_indices],
            "head_video_manifest_matching_rows_each_audit_only": {
                side: sum(low <= int(row["video_timestamp_ns"]) <= high for row in rows)
                for side, rows in video_frames.items()},
        }
        emit("prefix", prefix)
        assert all(value == 0 for value in nonfinite.values()), "Selected witness has incomplete pose values"
        for index in sorted({first, last}):
            emit("existing_value_witness", {
                "window_source_s": seconds(width), "csv_line": index + 2,
                "timestamp_ns_audit": reference[index], "is_bad": bad_flag(quality[index]),
                "problem_type": quality_reasons(quality[index]),
                "pose_point0_pos_xyz_quat_wxyz": {
                    name: [tables[name][index][f"transforms[0].{component}"] for component in COMPONENTS]
                    for name in POSE_CHANNELS},
            })
        next_index = next((i for i, time in enumerate(reference) if time > high), None)
        emit("boundary_precision", {
            "window_s": seconds(width),
            "first_excluded_row_index": next_index,
            "first_excluded_csv_line": None if next_index is None else next_index + 2,
            "first_excluded_relative_s": None if next_index is None else seconds(reference[next_index] - low),
            "excess_over_boundary_ns": None if next_index is None else reference[next_index] - high,
            "unsafe_float_seconds_then_subtract_count": sum(
                0 <= float(str(time)) / NS - float(low) / NS <= width / NS for time in reference),
            "unsafe_float_ns_then_subtract_count": sum(
                0 <= (float(str(time)) - float(low)) / NS <= width / NS for time in reference),
            "alternative_first_record_sample_zero_closed_count": sum(
                start_ns <= time - reference[0] <= start_ns + width for time in reference),
            "alternative_first_record_sample_zero_half_open_count": sum(
                start_ns <= time - reference[0] < start_ns + width for time in reference),
            "boundary_rule": "strict integer/Decimal, no epsilon or timestamp correction",
        })
        previous = set(selected)

    # Golden checks detect changed witness data or a changed extraction convention.
    if unit_id == DEFAULT_UNIT:
        assert counts == [6, 18, 30] and bad_counts == [0, 3, 7]
        assert reference[0] - origin == 64
        assert [reference[i] - (low + width) for i, width in zip((6, 18, 30), widths)] == [64] * 3
    emit("limitations", [
        "Single training CSV record; no whole-dataset timing/quality certification and no simulated replay.",
        "EgoDemo is not read as Parquet here: timestamp float32 and frame/index/episode_index/task_index "
        "need an explicit precision and source-ID whitelist; global stats/total_frames and future bad-frame lists are not prefixes.",
        "Official timestamps are permitted; segment-relative integer times are a suggested anti-fingerprint interface, "
        "not a quoted official requirement. Original source-frame counters and hashes remain audit-only.",
        "Pose derivatives, interpolation, smoothing, missingness features and normalization may use only "
        "already returned data; global endpoints, future quality, prefix matching and seed inversion are forbidden.",
        "Quality-marked but numerically complete coordinates are not certified accurate reference states.",
    ])
    for path, digest in sorted(INPUT_HASHES.items()):
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, f"Input modified: {relative(path)}"
        emit("input_unchanged_sha256", {"path": relative(path), "sha256": digest})
    emit("PASS", {"input_files_unchanged": len(INPUT_HASHES), "prefix_counts": counts,
                  "bad_counts": bad_counts, "new_files_written_by_script": 0})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unit-id", default=DEFAULT_UNIT, help="Eligible train CSV unit, default witness is fixed")
    parser.add_argument("--pdf-quotes", action="store_true", help="Verify local PDF phrases with pypdf; no download")
    args = parser.parse_args()
    audit(args.unit_id, args.pdf_quotes)


if __name__ == "__main__":
    main()
