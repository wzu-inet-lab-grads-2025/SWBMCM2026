"""Training-only audit of candidate wrist orientation and four finger bend angles.

AI-assisted descriptive geometry, OpenCode, OpenAI GPT family, GPT-6.2 Astra
routing group, 2026-11-21. Exact routed model release date unavailable.
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from .c_palm_scale import SECOND, SIDES, history_scale, lengths
from .c_q2_windows import qualified_runs, windows, wrist_inputs
from .c_records import ROOT, split_records


def read_states(folder, side, time):
    # Consecutive triples are MCP, PIP, DIP on each non-thumb finger.
    joints = [base + offset for base in (5, 9, 13, 17) for offset in range(3)]
    coords = [f'transforms[{j}].pos.{a}' for j in joints for a in 'xyz']
    quat = [f'transforms[0].quat.{a}' for a in 'wxyz']
    frame = pd.read_csv(folder/f'tables/pose_{side}_hand.csv', usecols=['header.ts', *coords, *quat],
                        dtype={'header.ts': 'string'})
    if not np.array_equal(time, frame['header.ts'].to_numpy(np.int64)):
        raise ValueError('Joint and wrist clocks differ')
    pts = frame[coords].to_numpy(float).reshape(-1, 4, 3, 3)
    first = pts[:, :, 1] - pts[:, :, 0]
    second = pts[:, :, 2] - pts[:, :, 1]
    bend = np.degrees(np.arctan2(np.linalg.norm(np.cross(first, second), axis=-1), (first*second).sum(axis=-1)))
    valid = np.isfinite(pts).all(axis=(2, 3)) & (pts != 0).any(axis=-1).all(axis=-1)
    valid &= (np.linalg.norm(first, axis=-1) > 0) & (np.linalg.norm(second, axis=-1) > 0)
    bend[~valid] = np.nan
    q = frame[quat].to_numpy(float)
    norm = np.linalg.norm(q, axis=1, keepdims=True)
    q = np.divide(q, norm, out=np.full_like(q, np.nan), where=np.isfinite(norm) & (norm > 0))
    return pts[:, 1, 0], q, bend


def sustained(change):
    above = change >= 30
    return bool((above[:-2] & above[1:-1] & above[2:]).any())


def training(root):
    records = []
    for record, folder, meta in split_records(root, 'train'):
        if folder.parent.name == 'EgoDemo':
            continue
        time, _, wrist, masks = wrist_inputs(folder)
        for side in SIDES:
            middle_mcp, q, bend = read_states(folder, side, time)
            for run in qualified_runs(time, masks['both']):
                for start, first, end in windows(time, run, SECOND, 2*SECOND):
                    a = first-1
                    stop = min(end, time.searchsorted(time[a] + SECOND//2, side='right'))
                    span = slice(first, stop)
                    scale = history_scale(lengths(wrist[side][start:first], middle_mcp[start:first]))
                    usable = (scale is not None and stop-first >= 3 and np.isfinite(q[start:end]).all()
                              and np.isfinite(bend[start:end]).all())
                    row = dict(record=record, environment=meta['environment_id'], side=side, windows=1,
                               eligible=int(usable), quiet=0, quiet_rot=0, quiet_bend=0, quiet_both=0)
                    if usable and np.linalg.norm(wrist[side][span] - wrist[side][a], axis=-1).max() <= .5*scale:
                        angle = 2*np.degrees(np.arccos(np.clip(np.abs(q[span] @ q[a]), 0., 1.)))
                        turn = sustained(angle)
                        finger = sustained(np.abs(bend[span]-bend[a]))
                        row.update(quiet=1, quiet_rot=int(turn), quiet_bend=int(finger), quiet_both=int(turn and finger))
                    records.append(row)
    frame = pd.DataFrame(records)
    counts = ['windows', 'eligible', 'quiet', 'quiet_rot', 'quiet_bend', 'quiet_both']
    output = []
    for scope, keys in [('all', ['side']), ('environment', ['side','environment'])]:
        grouped = frame.groupby(keys, as_index=False)
        output.append(grouped.agg({k:'sum' for k in counts} | {'record':'nunique'})
                      .rename(columns={'record':'records'}).assign(scope=scope))
    return pd.concat(output, ignore_index=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=ROOT)
    p.add_argument('--out', type=Path, default=Path('runs/c_state_channels/summary.csv'))
    args = p.parse_args()
    table = training(args.root)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.out, index=False)
    print(table[table.scope=='all'].to_string(index=False))
