"""Grouped, training-only comparison of empirical and lognormal duration summaries.

AI-assisted in OpenCode, OpenAI GPT family, GPT-6.2 Astra routing group,
2026-11-21. Model release date unavailable. No policy or reserve is chosen.
"""

import argparse
import csv
from decimal import Decimal
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd


def load(index):
    segments = []
    with index.open(encoding='utf-8-sig', newline='') as stream:
        for r in csv.DictReader(stream):
            if r['split'] == 'train' and r['eligible'] == '1':
                seconds = Decimal(r['end_s']) - Decimal(r['start_s'])
                if seconds <= 0:
                    raise ValueError('Eligible annotation must have a positive duration')
                segments.append(dict(family=r['family_id'], record=r['episode_uuid'],
                                     environment=r['environment_id'], seconds=float(seconds)))
    return pd.DataFrame(segments)


def compare(segments):
    rows = []
    levels = (0.25, 0.50, 0.75)
    normal_q = np.array([NormalDist().inv_cdf(level) for level in levels])
    for family, group in segments.groupby('family'):
        for env, held in group.groupby('environment'):
            train = group.loc[group.environment != env, 'seconds'].to_numpy()
            if len(train) < 2:
                raise ValueError('Expected at least two training examples in each family fold')
            logged = np.log(train)
            fits = {'empirical': np.quantile(train, levels, method='linear'),
                    'lognormal': np.exp(logged.mean() + normal_q * logged.std(ddof=0))}
            for method, (lo, middle, hi) in fits.items():
                d = held.seconds
                delta = d-hi
                scores = pd.DataFrame(dict(record=held.record, median_mae_s=abs(d-middle),
                                           middle50_coverage=((d >= lo) & (d <= hi)).astype(float),
                                           q75_coverage=(d <= hi).astype(float),
                                           q75_loss_s=np.maximum(.75*delta, -.25*delta)))
                rows.append(dict(family=family, environment=env, method=method, segments=len(held),
                                 records=held.record.nunique(), middle50_width_s=hi-lo,
                                 **scores.groupby('record').mean().mean().to_dict()))
    env = pd.DataFrame(rows)
    groups = env.groupby(['family','method'], as_index=False)
    macro = groups.agg(dict(segments='sum', records='sum', middle50_width_s='mean', median_mae_s='mean',
                            middle50_coverage='mean', q75_coverage='mean', q75_loss_s='mean')).assign(environment='all')
    return pd.concat([env, macro], ignore_index=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--index', type=Path, default=Path('data/raw/C/Restocking_Tasks_and_Constraints/source_segment_index.csv'))
    p.add_argument('--out', type=Path, default=Path('runs/c_feas_duration_shape/summary.csv'))
    args = p.parse_args()
    table = compare(load(args.index))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.out, index=False, float_format='%.12g')
    print(table[table.environment == 'all'].to_string(index=False))
