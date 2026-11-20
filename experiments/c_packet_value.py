"""Training diagnostic of the realised error reduction from a fixed delayed packet.

AI-assisted fixed-action accounting, GPT-6.2 Astra routing group in OpenCode,
OpenAI GPT family, 2026-11-20. Exact release date unavailable.
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

if __package__:
    from .c_palm_motion import palm_points
    from .c_palm_scale import SECOND, SIDES, history_scale, lengths
    from .c_q2_diagnostic import replay
    from .c_q2_windows import qualified_runs, windows, wrist_inputs
    from .c_records import ROOT, split_records
else:
    from c_palm_motion import palm_points
    from c_palm_scale import SECOND, SIDES, history_scale, lengths
    from c_q2_diagnostic import replay
    from c_q2_windows import qualified_runs, windows, wrist_inputs
    from c_records import ROOT, split_records


METHODS = [('no_request', 0, 0), ('request_first', 500, 0), ('request_at_1s', 500, SECOND)]


def training(root):
    totals = []
    for record, folder, meta in split_records(root, 'train'):
        if folder.parent.name == 'EgoDemo':
            continue
        times, _, wrists, masks = wrist_inputs(folder)
        pairs = np.stack([wrists[s] for s in SIDES], axis=1)
        size = {}
        for side in SIDES:
            hand = palm_points(folder, side, times)
            size[side] = lengths(hand[:, 0], hand[:, 2])
        for run in qualified_runs(times, masks['both']):
            for start, first, end in windows(times, run, SECOND, 2*SECOND):
                scale = [history_scale(size[s][start:first]) for s in SIDES]
                if any(value is None for value in scale):
                    raise ValueError('Changed paired denominator relative to the audited scale pool')
                clock = times[first:end]
                anchor = times[first-1]

                def receive(source_ns):
                    i = clock.searchsorted(source_ns)
                    if i >= len(clock) or clock[i] != source_ns:
                        raise ValueError('Returned packet must refer to an actual requested sample')
                    return pairs[first+i].copy()

                for method, delay, wait_ns in METHODS:
                    prediction, (requests, receipts, cost, pending) = replay(clock, pairs[first-1], delay, receive,
                                                                             request_not_before_ns=anchor+wait_ns)
                    errors = np.linalg.norm(prediction - pairs[first:end], axis=2)
                    totals.append(dict(method=method, environment=meta['environment_id'], record=record,
                                       windows=1, points=len(clock), requests=requests, receipts=receipts,
                                       cost=cost, pending_s=pending, left_cm=100*errors[:, 0].sum(),
                                       right_cm=100*errors[:, 1].sum(), left_relative=errors[:, 0].sum()/scale[0],
                                       right_relative=errors[:, 1].sum()/scale[1]))
    return pd.DataFrame(totals)


def summarize(data):
    counts = ['windows', 'points', 'requests', 'receipts', 'cost', 'pending_s']
    loss = ['left_cm', 'right_cm', 'left_relative', 'right_relative']
    records = data.groupby(['method', 'environment', 'record'], as_index=False)[counts+loss].sum()
    records[loss] = records[loss].div(records.points, axis=0)
    records['records'] = 1
    reducer = {key: 'sum' for key in ['records', *counts]} | {key: 'mean' for key in loss}
    envs = records.groupby(['method', 'environment'], as_index=False).agg(reducer)
    envs['environments'] = 1
    macro = envs.groupby('method', as_index=False).agg(reducer | {'environments': 'sum'})
    records['scope'], envs['scope'], macro['scope'] = 'record', 'environment', 'macro'
    return pd.concat([macro, envs, records], ignore_index=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--out', type=Path, default=Path('runs/c_quality_information/packet_value.csv'))
    args = parser.parse_args()
    table = summarize(training(args.root))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.out, index=False, float_format='%.12g')
    print(table.query("scope == 'macro'").to_string(index=False))
