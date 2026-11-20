"""Training descriptions of wrist positions relative to the head and the other hand.

AI-assisted data preparation in OpenCode, OpenAI GPT family, GPT-6.2 Astra
routing group, 2026-11-20. Exact routed model release date unavailable.
"""

import argparse
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from .c_q1_onset_context import reason_arrays, training_records
from .c_q2_windows import qualified_runs, windows, wrist_inputs
from .c_records import ROOT, rows


def magnitude_cm(delta):
    return 100*np.linalg.norm(delta, axis=-1)


def training(root):
    records, reasons = [], Counter()
    for record, folder, meta in training_records(root):
        time, normal, wrists, valid = wrist_inputs(folder)
        tables = folder/'tables'
        _, occ, _ = reason_arrays(rows(tables/'annotation_bad_frame_pose_hand.csv'), time, normal)
        reasons.update(ticks=len(time), left_only=int((occ['left'] & ~occ['right']).sum()),
                       right_only=int((occ['right'] & ~occ['left']).sum()), both=int((occ['left'] & occ['right']).sum()))
        cols = [f'transforms[0].pos.{a}' for a in 'xyz']
        frame = pd.read_csv(tables/'pose_head.csv', usecols=['header.ts', *cols], dtype={'header.ts': 'string'})
        if not np.array_equal(time, frame['header.ts'].to_numpy(dtype=np.int64)):
            raise ValueError('Head and wrist clocks differ')
        head = frame[cols].to_numpy(dtype=float)
        blocks = []
        for run in qualified_runs(time, valid['both']):
            for _, start, end in windows(time, run, 10**9, 2*10**9):
                anchor = start-1
                if not np.isfinite(head[anchor:end]).all():
                    raise ValueError('Missing head geometry in the shared comparison pool')
                dh = head[start:end] - head[anchor]
                dl = wrists['left'][start:end] - wrists['left'][anchor]
                dr = wrists['right'][start:end] - wrists['right'][anchor]
                # The two one-sided experiments share the magnitude of (dl - dr).
                blocks.append(np.column_stack([magnitude_cm(v) for v in (dh, dl, dr, dl-dh, dr-dh, dl-dr)]))
        if blocks:
            values = np.concatenate(blocks)
            names = ('head', 'left', 'right', 'left_minus_head', 'right_minus_head', 'interwrist_change')
            records.append(dict(environment=meta['environment_id'], record=record, windows=len(blocks),
                                ticks=len(values), **dict(zip(names, values.mean(axis=0)))))
    return pd.DataFrame(records), reasons


def summarize(records):
    cols = [c for c in records.columns if c not in ('record','environment','windows','ticks')]
    rule = {c:'mean' for c in cols} | {'ticks':'sum', 'windows':'sum', 'records':'sum'}
    records = records.assign(records=1)
    envs = records.groupby('environment', as_index=False).agg(rule)
    macro = pd.DataFrame([{k:getattr(envs[k], op)() for k, op in rule.items()}])
    return pd.concat([macro.assign(scope='macro'), envs.assign(scope='environment'), records.assign(scope='record')],
                     ignore_index=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=ROOT)
    p.add_argument('--out', type=Path, default=Path('runs/c_design_support/reference_frames.csv'))
    args = p.parse_args()
    records, reasons = training(args.root)
    table = summarize(records)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.out, index=False, float_format='%.12g')
    print('Raw reason counts:', dict(reasons))
    print(table.query("scope == 'macro'").to_string(index=False))
    env = table.query("scope == 'environment'")
    for side in ('left', 'right'):
        print(side, 'head-relative change smaller in', (env[side+'_minus_head'] < env[side]).sum(),
              'environments; copying visible-hand displacement improves in', (env.interwrist_change < env[side]).sum(),
              'of', len(env))
