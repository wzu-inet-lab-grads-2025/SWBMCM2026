"""Count disjoint, reference-qualified validation windows. No predictions or video.

AI-assisted data preparation, 2026-11-20: OpenCode, GPT-6.2 Astra routing group
(OpenAI GPT family; exact routed model release date is unavailable in the harness).
"""

import argparse
from bisect import bisect_left
import csv
from pathlib import Path
import sys

import numpy as np
import pandas as pd

if __package__:
    from .c_audit_quality import ROOT, SIDES, raw_metadata, rows
else:
    from c_audit_quality import ROOT, SIDES, raw_metadata, rows


def qualified_runs(times, usable):
    good = np.flatnonzero(usable)
    if len(good):
        breaks = (np.diff(good) != 1) | (np.diff(times[good]) > 50_000_000)
        yield from np.split(good, np.flatnonzero(breaks) + 1)


def windows(times, indices, history_ns, mask_ns):
    """Earliest disjoint [a,a+h+m) blocks, requiring an observed end boundary."""
    run = times[indices]
    i = 0
    while i < len(run):
        masked = bisect_left(run, run[i] + history_ns, i + 1)
        following = bisect_left(run, run[i] + history_ns + mask_ns, masked)
        if following == len(run):
            break
        if masked < following:
            yield indices[i], indices[masked], indices[following]
            i = following
        else:
            i += 1


def wrist_inputs(folder):
    tables = folder / 'tables'
    quality = pd.read_csv(tables / 'annotation_bad_frame_pose_hand.csv',
                          usecols=['header.ts', 'is_bad'], dtype={'header.ts': 'string', 'is_bad': str})
    # Parse epoch nanoseconds directly from text. Floating-point inference may
    # round away a stream mismatch before the clock comparison.
    times = quality['header.ts'].to_numpy(dtype=np.int64)
    flags = quality['is_bad'].str.strip().str.lower()
    if not len(times) or not (np.diff(times) > 0).all() or not flags.isin(['true', 'false', '0', '1']).all():
        raise ValueError('Unexpected quality clock or flag')
    normal = flags.isin(['false', '0']).to_numpy()
    coords = [f'transforms[0].pos.{axis}' for axis in 'xyz']
    masks, points = {}, {}
    for side in SIDES:
        hand = pd.read_csv(tables / f'pose_{side}_hand.csv', usecols=['header.ts', *coords],
                           dtype={'header.ts': 'string'})
        if not np.array_equal(times, hand['header.ts'].to_numpy(dtype=np.int64)):
            raise ValueError(f'{side} hand clock differs from quality clock')
        xyz = hand[coords].to_numpy(dtype=float)
        points[side] = xyz
        masks[side] = normal & np.isfinite(xyz).all(axis=1) & (xyz != 0).any(axis=1)
    masks['both'] = masks['left'] & masks['right']
    return times, normal, points, masks


def split_records(root, split):
    if split not in ('train', 'validation'):
        raise ValueError('The diagnostic supports train and validation')
    rules = root / 'Restocking_Tasks_and_Constraints'
    records = [r for r in rows(rules / 'record_split.csv') if r['split'] == split]
    folders = {}
    for r in rows(rules / 'source_segment_index.csv'):
        if r['split'] == split:
            folder = root / 'Raw_Data' / r['dataset'] / r['sample']
            if folders.setdefault(r['episode_uuid'], folder) != folder:
                raise ValueError('Conflicting folders for one episode')
    for ordinal, record in enumerate(records, 1):
        folder = folders[record['episode_uuid']]
        meta = raw_metadata(folder)
        if any(meta[key] != record[key] for key in ('episode_uuid', 'environment_id')):
            raise ValueError('Raw metadata and split index disagree')
        prefix = 'V' if split == 'validation' else 'T'
        yield f'{prefix}{ordinal:02}', folder, meta


def validation_records(root):
    yield from split_records(root, 'validation')


def inventory(root, history_ms, mask_ms):
    for record, folder, meta in validation_records(root):
        dataset = folder.parent.name
        if dataset == 'EgoDemo':
            # Sparse bad reasons never supply the explicit-normal reference required here.
            counts = dict(timestamps=meta['length'], explicit_normal=0, both_qualified=0,
                          runs=0, longest_span_s=0, left_windows=0, right_windows=0, both_windows=0,
                          csv_bad_runs='', csv_max_bad_span_s='')
        else:
            times, normal, _, masks = wrist_inputs(folder)
            runs = list(qualified_runs(times, masks['both']))
            longest = max((times[r[-1]] - times[r[0]] for r in runs), default=0)
            counts = dict(timestamps=len(times), explicit_normal=int(normal.sum()), both_qualified=int(masks['both'].sum()),
                          runs=len(runs), longest_span_s=round(longest / 1e9, 3))
            for side, usable in masks.items():
                counts[f'{side}_windows'] = sum(1 for run in qualified_runs(times, usable)
                                               for _ in windows(times, run, history_ms * 1_000_000, mask_ms * 1_000_000))
            bad = list(qualified_runs(times, ~normal))
            counts['csv_bad_runs'] = len(bad)
            counts['csv_max_bad_span_s'] = max(((times[r[-1]] - times[r[0]]) / 1e9 for r in bad), default=0)
        yield dict(record=record, dataset=dataset, environment=meta['environment_id'],
                   history_ms=history_ms, mask_ms=mask_ms, **counts)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--history-ms', type=int, required=True)
    parser.add_argument('--mask-ms', type=int, required=True)
    args = parser.parse_args()
    if min(args.history_ms, args.mask_ms) <= 0:
        parser.error('history and mask must be positive')
    results = list(inventory(args.root, args.history_ms, args.mask_ms))
    writer = csv.DictWriter(sys.stdout, fieldnames=results[0], lineterminator='\n')
    writer.writeheader()
    writer.writerows(results)
