"""Training descriptive check of motion direction consistency and extrapolation.

The direction test sees only the visible history. No mode labels or official
model are fitted. AI-assisted exploratory comparison, OpenCode, 2026-11-21.
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from .c_q2_velocity_probe import HISTORY_NS, MASK_NS, SECOND, predict_pair
from .c_q2_windows import SIDES, qualified_runs, windows, wrist_inputs
from .c_records import ROOT, split_records


def aligned(time, positions):
    mid = time.searchsorted(time[-1] - SECOND//2)
    d1 = positions[mid] - positions[0]
    d2 = positions[-1] - positions[mid]
    return np.dot(d1, d2) > 0


def aggregates(root):
    pieces = []
    for record, folder, meta in split_records(root, 'train'):
        if folder.parent.name == 'EgoDemo':
            continue
        t, _, wrists, masks = wrist_inputs(folder)
        for run in qualified_runs(t, masks['both']):
            for start, first, end in windows(t, run, HISTORY_NS, MASK_NS):
                seen, future = slice(start, first), slice(first, end)
                visible = {s: wrists[s][seen] for s in SIDES}
                hold, moving, _ = predict_pair(t[seen], visible, {s: masks[s][seen] for s in SIDES}, t[future])
                if moving is None:
                    raise ValueError('Expected common velocity support')
                for i, side in enumerate(SIDES):
                    same = aligned(t[seen], visible[side])
                    target = wrists[side][future]
                    h = 100 * np.linalg.norm(hold[:, i] - target, axis=1)
                    v = 100 * np.linalg.norm(moving[:, i] - target, axis=1)
                    for horizon, keep in [('first_half_s', t[future] <= t[first-1] + SECOND//2),
                                          ('all', np.ones(len(h), dtype=bool))]:
                        pieces.append(dict(environment=meta['environment_id'], record=record, side=side,
                                           horizon=horizon, windows=1, aligned_windows=int(same), ticks=keep.sum(),
                                           sum_hold=h[keep].sum(), sum_velocity=v[keep].sum(),
                                           sum_gated=(v if same else h)[keep].sum()))
    keys = ['side', 'horizon']
    records = pd.DataFrame(pieces).groupby([*keys, 'environment', 'record'], as_index=False).sum(numeric_only=True)
    scores = []
    for method in ('hold', 'velocity', 'gated'):
        column = f'{method}_cm'
        records[column] = records[f'sum_{method}'] / records.ticks
        scores.append(column)
    records['records'] = 1
    counts = ['records', 'windows', 'aligned_windows', 'ticks']
    agg = {**dict.fromkeys(counts, 'sum'), **dict.fromkeys(scores, 'mean')}
    env = records.groupby([*keys, 'environment'], as_index=False).agg(agg)
    macro = env.groupby(keys, as_index=False).agg(agg).assign(environment='all')
    return pd.concat([env, macro], ignore_index=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=ROOT)
    p.add_argument('--out', type=Path, default=Path('runs/c_feas_motion_mode/summary.csv'))
    args = p.parse_args()
    table = aggregates(args.root)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.out, index=False, float_format='%.12g')
    print(table[table.environment == 'all'].to_string(index=False))
