"""Describe training skeleton scale and proportions by operator ID and hand.

AI-assisted data preparation in OpenCode, OpenAI GPT family, GPT-6.2 Astra
routing group, 2026-11-20. Exact routed release date unavailable.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

if __package__:
    from .c_palm_scale import SIDES, lengths
    from .c_records import ROOT, official_records, split_records
    from .c_q2_windows import wrist_inputs
else:
    from c_palm_scale import SIDES, lengths
    from c_records import ROOT, official_records, split_records
    from c_q2_windows import wrist_inputs


CHAINS = {'index_over_L': (5, 6, 7, 8), 'little_over_L': (17, 18, 19, 20),
          **{f'thumb_{i}_{i+1}_over_L': (i, i+1) for i in range(4)}}
JOINTS = sorted({0, 9}.union(*CHAINS.values()))


def inputs(folder):
    if folder.parent.name == 'EgoDemo':
        cols = [f'observation.state.hand_{s}_world' for s in SIDES]
        table = pq.read_table(sorted((folder/'data').rglob('*.parquet')), columns=['frame_index', *cols])
        entries = json.loads((folder/'bad_frame_ratio.json').read_text())['hand_bad_frame_details']
        selected = ~np.isin(table['frame_index'].to_pylist(), [r['frame'] for r in entries])
        for side, col in zip(SIDES, cols):
            xyz = np.asarray(table[col].to_pylist(), dtype=float)[selected]
            yield side, {j: xyz[:, j] for j in JOINTS}
    else:
        times, normal, _, _ = wrist_inputs(folder)
        cols = [f'transforms[{j}].pos.{axis}' for j in JOINTS for axis in 'xyz']
        for side in SIDES:
            hand = pd.read_csv(folder/f'tables/pose_{side}_hand.csv', usecols=['header.ts', *cols],
                               dtype={'header.ts': 'string'})
            if not np.array_equal(times, hand['header.ts'].to_numpy(dtype=np.int64)):
                raise ValueError('Quality and pose clocks differ')
            xyz = hand[cols].to_numpy(dtype=float)[normal].reshape(-1, len(JOINTS), 3)
            yield side, {j: xyz[:, index] for index, j in enumerate(JOINTS)}


def record_values(root):
    official = {r['episode_uuid']: r['operator_id'] for r in official_records(root) if r['split'] == 'train'}
    data = []
    for record, folder, meta in split_records(root, 'train'):
        op = meta['operator_id']
        if not op or op != official[meta['episode_uuid']]:
            raise ValueError('Unmatched operator metadata')
        for side, points in inputs(folder):
            scale = lengths(points[0], points[9])
            series = {'L_cm': 100 * scale}
            for name, joints in CHAINS.items():
                chain = sum(lengths(points[i], points[j]) for i, j in zip(joints, joints[1:]))
                series[name] = chain / scale
            for name, values in series.items():
                usable = values[np.isfinite(values)]
                if len(usable):
                    data.append(dict(record=record, operator=op, dataset=folder.parent.name,
                                     environment=meta['environment_id'], side=side, metric=name,
                                     points=len(usable), value=np.median(usable)))
    return pd.DataFrame(data)


def summary(records):
    for pool, group in [('all', records), ('explicit_csv', records[records.dataset != 'EgoDemo'])]:
        for (side, metric), data in group.groupby(['side', 'metric'], sort=False):
            by_operator = data.groupby('operator').value
            typical = by_operator.median()
            repeat = by_operator.agg(['size', 'min', 'max', 'median']).query('size >= 2')
            ranges = (repeat['max']-repeat['min']) / repeat['median']
            row = dict(pool=pool, side=side, metric=metric, records=len(data), operators=len(typical),
                       points=int(data.points.sum()), min=typical.min(), p50=typical.median(), max=typical.max(),
                       repeated_operators=len(repeat), within_range_pct_p50=100*ranges.median(),
                       within_range_pct_max=100*ranges.max())
            if pool == 'all':
                formats = data.assign(format_group=np.where(data.dataset == 'EgoDemo', 'LeRobot', 'CSV'))
                paired = formats.groupby(['operator', 'format_group']).value.median().unstack().dropna()
                diff = 200 * (paired.CSV-paired.LeRobot).abs() / (paired.CSV+paired.LeRobot)
                row.update(paired_format_operators=len(diff), paired_diff_pct_p50=diff.median(),
                           paired_diff_pct_max=diff.max())
            yield row


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--out', type=Path, default=Path('runs/c_palm_scale/operator_summary.csv'))
    args = parser.parse_args()
    table = pd.DataFrame(summary(record_values(args.root)))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.out, index=False, float_format='%.10g')
    print(table.to_string(index=False, float_format='%.6g'))
