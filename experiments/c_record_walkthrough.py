"""Local illustration of one training sequence (EgoDemo E001), in raw units.

AI-assisted preprocessing and figure, OpenCode, OpenAI GPT family,
GPT-6.2 Astra routing group, 2026-11-20. Exact model release date unavailable.
"""

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pyarrow.parquet as pq

if __package__:
    from .c_q2_windows import ROOT, raw_metadata, rows
else:
    from c_q2_windows import ROOT, raw_metadata, rows


def draw(root, output):
    folder = root / 'Raw_Data/EgoDemo/E001'
    meta = raw_metadata(folder)
    split = rows(root / 'Restocking_Tasks_and_Constraints/record_split.csv')
    assert [r['split'] for r in split if r['episode_uuid'] == meta['episode_uuid']] == ['train']
    sides = ('left', 'right')
    channels = {s: f'observation.state.hand_{s}_world' for s in sides}
    table = pq.read_table(sorted((folder / 'data').rglob('*.parquet')),
                          columns=['timestamp', 'frame_index', *channels.values()])
    time = np.array(table['timestamp'].to_pylist(), dtype=float)
    frame_time = dict(zip(table['frame_index'].to_pylist(), time))
    xyz = {s: np.array(table[ch].to_pylist()) for s, ch in channels.items()}
    assert all(x.shape == (len(time), 21, 3) for x in xyz.values())
    segments = json.loads((folder / 'annotation.json').read_text())['subtask_annotations']
    quality = json.loads((folder / 'bad_frame_ratio.json').read_text())['hand_bad_frame_details']
    fig = plt.figure(figsize=(10, 5), layout='constrained')
    grid = fig.add_gridspec(2, 2, width_ratios=(1, 1.65), height_ratios=(2, 1))
    pose = fig.add_subplot(grid[:, 0], projection='3d')
    motion = fig.add_subplot(grid[0, 1])
    marks = fig.add_subplot(grid[1, 1], sharex=motion)
    pose.set(title='One recorded hand at t = 0', xlabel='x', ylabel='y', zlabel='z')
    hand = xyz['right'][0]
    for first in (1, 5, 9, 13, 17):
        finger = hand[[0, *range(first, first + 4)]]
        pose.plot(*finger.T, '-o', ms=3, lw=1.1)
    pose.scatter(*hand[0], color='black', marker='s', s=35, label='wrist (0)')
    pose.legend(frameon=False, loc='lower left')
    pose.set_box_aspect(np.maximum(np.ptp(hand, axis=0), 1e-12))
    for side, color in zip(sides, ('#0072b2', '#d55e00')):
        wrist = xyz[side][:, 0, :]
        motion.plot(time, np.linalg.norm(wrist - wrist[0], axis=1), color=color, label=side, lw=1.4)
        events = [frame_time[r['frame']] for r in quality if r.get(f'{side}_bad_reason')]
        level = 1 if side == 'left' else 0
        marks.plot(events, [level] * len(events), '|', color=color, ms=8)
    for i, seg in enumerate(segments):
        a, b = seg['start_time'], seg['end_time']
        marks.barh(2, b-a, left=a, height=.55, color=('#bcd7e6', '#d7dbb7')[i % 2], edgecolor='white')
        marks.text((a+b)/2, 2, str(i+1), va='center', ha='center', fontsize=8)
    marks.set(yticks=[0, 1, 2], yticklabels=['right flag', 'left flag', 'action interval'],
              ylim=(-.6, 2.6), xlabel='Record time (seconds)')
    motion.set(title='Both wrists through time', ylabel='Distance from first wrist point\n(raw units)', xlim=(0,time[-1]))
    motion.legend(frameon=False)
    motion.grid(axis='y', alpha=.2)
    fig.suptitle('EgoDemo E001 | training record | sparse quality markers')
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=170)
    plt.close(fig)
    print(f'{len(time)} samples, {len(segments)} action intervals, {len(quality)} listed quality entries. {output}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    draw(args.root, Path('runs/c_data_walkthrough/E001.png'))
