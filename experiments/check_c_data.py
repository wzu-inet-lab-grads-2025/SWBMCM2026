"""Read-only C attachment inventory. Run with pandas and pyarrow installed.

Prepared with OpenAI Codex (GPT-6) for dataset inventory on 2026-11-20.
"""

import csv
import json
from collections import Counter
from itertools import combinations
from pathlib import Path
from statistics import median
from zipfile import ZipFile

import pandas as pd
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[1] / "data/raw/C"


def rows(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def inspect_record(folder):
    """Read identifiers, timestamp counts and hand flags, without scoring predictions."""
    if folder.parent.name == "EgoDemo":
        meta = next((folder / "meta/episodes").rglob("*.parquet"))
        uuid = pq.read_table(meta, columns=["episode_uuid"])["episode_uuid"][0].as_py()
        file = next((folder / "data").rglob("*.parquet"))
        count = pq.ParquetFile(file).metadata.num_rows
        quality = json.loads((folder / "bad_frame_ratio.json").read_text())
        marked = {r["frame"] for r in quality["hand_bad_frame_details"]
                  if r["left_bad_reason"] or r["right_bad_reason"]}
        assert quality["total_frames"] == count
        return uuid, count, len(marked)
    metadata = {r["field"]: r["value"] for r in rows(folder / "session_metadata.csv")}
    table = folder / "tables"
    right = pd.read_csv(table / "pose_right_hand.csv", usecols=["header.ts"])
    quality = pd.read_csv(table / "annotation_bad_frame_pose_hand.csv", usecols=["header.ts", "is_bad"])
    assert right["header.ts"].is_unique and quality["header.ts"].is_unique
    assert set(right["header.ts"]) == set(quality["header.ts"])
    return metadata["task_info.episode_uuid"], len(right), int(quality["is_bad"].sum())


def main():
    rules = ROOT / "Restocking_Tasks_and_Constraints"
    records = rows(rules / "record_split.csv")
    segments = rows(rules / "source_segment_index.csv")
    by_uuid = {r["episode_uuid"]: r for r in records}
    assert len(by_uuid) == len(records)
    assert all(by_uuid[r["episode_uuid"]]["split"] == r["split"] for r in segments)
    for split in ("train", "validation", "test"):
        subset = [r for r in records if r["split"] == split]
        pool = [r for r in segments if r["split"] == split and r["eligible"] == "1"]
        print(split, len(subset), "records,", len({r["environment_id"] for r in subset}), "environments,",
              "eligible units", dict(Counter(r["family_id"] for r in pool)))
        if split == "train":
            for family in ("P", "A", "L"):
                units = [r for r in pool if r["family_id"] == family]
                durations = [float(r["duration_s"]) for r in units]
                print(family, "records/environments", len({r["episode_uuid"] for r in units}),
                      len({r["environment_id"] for r in units}), "duration min/median/max",
                      tuple(round(x, 3) for x in (min(durations), median(durations), max(durations))))
    print("indexed/eligible", len(segments), sum(r["eligible"] == "1" for r in segments))
    print("eligible with floating-point duration below 1.2",
          sum(r["eligible"] == "1" and float(r["duration_s"]) < 1.2 for r in segments))
    for field in ("environment_id", "scene_id", "operator_id"):
        groups = {s: {r[field] for r in records if r["split"] == s} for s in ("train", "validation", "test")}
        print(field, {f"{a}/{b}": len(groups[a] & groups[b]) for a, b in combinations(groups, 2)})
    seen = set()
    for name in ("EgoDemo", "EgoPro", "EgoStandard"):
        root = ROOT / "Raw_Data" / name
        folders = sorted(d for d in root.iterdir() if d.is_dir())
        totals = Counter()
        for folder in folders:
            uuid, count, marked = inspect_record(folder)
            assert uuid not in seen and by_uuid[uuid]["dataset"] == name
            seen.add(uuid)
            totals.update(timestamps=count, flagged=marked, folder_differs_from_uuid=int(folder.name != uuid))
        archive = root.with_suffix(".zip")
        with ZipFile(archive) as package:
            suffixes = Counter(Path(item.filename).suffix for item in package.infolist() if not item.is_dir())
        print(name, len(folders), dict(totals), archive.stat().st_size, "bytes,", dict(suffixes))
    assert seen == set(by_uuid)


if __name__ == "__main__":
    main()
