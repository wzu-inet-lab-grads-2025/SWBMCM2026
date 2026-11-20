"""Compare candidate motion quantities in one training record per source.

AI-assisted data preparation and figure, OpenCode, OpenAI GPT family,
GPT-6.2 Astra routing group, 2026-11-20. Exact release date unavailable.
"""

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

if __package__:
    from .c_records import ROOT, official_records, raw_metadata, rows
else:
    from c_records import ROOT, official_records, raw_metadata, rows


JOINTS = (0, 5, 9, 13, 17)


def sample_records(root):
    training = {r['episode_uuid']: r for r in official_records(root) if r['split'] == 'train'}
    selected = {}
    for unit in rows(root / 'Restocking_Tasks_and_Constraints/source_segment_index.csv'):
        if unit['episode_uuid'] in training:
            selected.setdefault(unit['dataset'], unit)
    for dataset in ('EgoDemo', 'EgoPro', 'EgoStandard'):
        unit = selected[dataset]
        folder = root / 'Raw_Data' / dataset / unit['sample']
        meta = raw_metadata(folder)
        official = training[unit['episode_uuid']]
        assert dataset == official['dataset'] and all(meta[k] == official[k] for k in ('episode_uuid', 'environment_id'))
        yield dataset, folder


def demo(folder):
    keys = ['observation.state.hand_right_world', 'observation.state.hand_left_world']
    table = pq.read_table(sorted((folder/'data').rglob('*.parquet')), columns=['timestamp', 'frame_index', *keys])
    time = np.array(table['timestamp'].to_pylist(), dtype=float)
    frame = table['frame_index'].to_pylist()
    # Work in source time until all event markers have been aligned.
    labels = json.loads((folder/'bad_frame_ratio.json').read_text())['hand_bad_frame_details']
    marked = {r['frame'] for r in labels if r.get('left_bad_reason') or r.get('right_bad_reason')}
    assert marked.issubset(frame)
    bad = np.isin(frame, list(marked))
    segments = json.loads((folder/'annotation.json').read_text())['subtask_annotations']
    starts = [s['start_time'] - time[0] for s in segments]
    right, left = [np.array(table[k].to_pylist()) for k in keys]
    return time-time[0], right[:, JOINTS], left[:, 0], bad, starts, 'sparse flags'


def csv_record(folder):
    path = folder/'tables'
    quality = pd.read_csv(path/'annotation_bad_frame_pose_hand.csv', usecols=['header.ts', 'is_bad'], dtype=str)
    time = quality['header.ts'].to_numpy(dtype=np.int64)
    flag = quality['is_bad'].str.strip().str.lower()
    if not flag.isin(['true', 'false', '0', '1']).all():
        raise ValueError('Unrecognized explicit quality label')
    hands = []
    for side, joints in [('right', JOINTS), ('left', (0,))]:
        fields = [f'transforms[{j}].pos.{a}' for j in joints for a in 'xyz']
        data = pd.read_csv(path/f'pose_{side}_hand.csv', usecols=['header.ts', *fields], dtype={'header.ts': str})
        if not np.array_equal(time, data['header.ts'].to_numpy(dtype=np.int64)):
            raise ValueError('Hand and quality clocks differ')
        hands.append(data[fields].to_numpy(dtype=float).reshape(len(time), len(joints), 3))
    segments = rows(path/'annotation_semantic_segments.csv')
    starts = [(int(r['header.ts']) - int(time[0])) / 10**9 for r in segments]
    return (time-time[0])/10**9, hands[0], hands[1][:, 0], flag.isin(['true', '1']).to_numpy(), starts, 'explicit flags'


def draw(root, out):
    fig, axes = plt.subplots(3, 1, figsize=(9, 7.5), layout='constrained')
    for ax, (dataset, folder) in zip(axes, sample_records(root)):
        time, right, left, bad, starts, quality = demo(folder) if dataset == 'EgoDemo' else csv_record(folder)
        palm = right.mean(axis=1)
        wrist = right[:, 0]
        span = np.linalg.norm(wrist-left, axis=1)
        assert np.all(np.diff(time) > 0) and np.isfinite(right).all() and np.isfinite(left).all()
        for series, label in [(np.linalg.norm(wrist-wrist[0], axis=1), 'Right wrist displacement'),
                              (np.linalg.norm(palm-palm[0], axis=1), 'Right 5-point centre displacement'),
                              (np.abs(span-span[0]), 'Absolute change in inter-wrist distance')]:
            ax.plot(time, series, lw=1.25, label=label)
        for start in starts:
            ax.axvline(start, color='0.7', ls=':', lw=1)
        ax.plot(time[bad], np.full(bad.sum(), .03), '|', color='black', transform=ax.get_xaxis_transform())
        ax.set(xlim=(0,time[-1]), ylabel='Raw units', title=f'{dataset} | {len(time)} samples | {len(starts)} action intervals | {bad.sum()} marked ({quality})')
        print(dataset, len(time), 'timestamps,', len(starts), 'action intervals,', int(bad.sum()), 'marked')
    axes[0].legend(fontsize=8, frameon=False)
    axes[-1].set_xlabel('Seconds from first hand sample | dotted lines: annotation starts | black ticks: marked samples')
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=160)
    plt.close(fig)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    draw(args.root, Path('runs/c_data_walkthrough/three_sources.png'))
