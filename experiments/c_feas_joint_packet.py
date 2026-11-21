"""Value of the other wrist's binary packet feature on the official training split.

AI-assisted tabulation in OpenCode, 2026-11-21. Only environment aggregates
are exported. Window timestamps are used locally to align the two hands.
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from .c_packet_entropy import conditional_entropy, fold_predictions, get_windows
from .c_quality_information import log_loss
from .c_records import ROOT


def paired_hands(raw):
    identity = ['env', 'record', 'window_start', 'timing']
    values = ['f', 'y', 'z']
    hands = raw.pivot(index=identity, columns='side', values=values)
    if hands.isna().any().any() or set(hands.columns.get_level_values('side')) != {'left', 'right'}:
        raise ValueError('Expected two complete hands per request')
    hands.columns = [f'{name}_{hand}' for name, hand in hands.columns]
    return hands.reset_index()


def conditional_scores(data, side):
    other = 'left' if side == 'right' else 'right'
    # Both wrists' histories are available before the common request.
    history = [f'f_{side}', f'f_{other}']
    own = [*history, f'y_{side}']
    both = [*own, f'y_{other}']
    group = data.assign(z=data[f'z_{side}'])
    env_scores = []
    for env, held in group.groupby('env'):
        train = group[group.env != env]
        priors = np.full(len(held), (train.z.sum() + .5) / (len(train) + 1))
        hist_prob = fold_predictions(train, held, history, priors)
        own_prob = fold_predictions(train, held, own, hist_prob)
        full_prob = fold_predictions(train, held, both, own_prob)
        full_cells = set(train[both].itertuples(index=False, name=None))
        missing = sum(cell not in full_cells for cell in held[both].itertuples(index=False, name=None))
        scores = pd.DataFrame(dict(record=held.record, history_bit=log_loss(held.z, hist_prob),
                                   own_bit=log_loss(held.z, own_prob), both_bit=log_loss(held.z, full_prob),
                                   own_brier=(held.z-own_prob)**2, both_brier=(held.z-full_prob)**2))
        means = scores.groupby('record').mean().mean().to_dict()
        env_scores.append(dict(scope='environment', environment=env, windows=len(held),
                               records=held.record.nunique(), unseen_full_cells_windows=missing, **means))
    rows = pd.DataFrame(env_scores)
    overall = dict(scope='macro', environment='all')
    for col in ('windows', 'records', 'unseen_full_cells_windows'):
        overall[col] = rows[col].sum()
    for col in ('history_bit', 'own_bit', 'both_bit', 'own_brier', 'both_brier'):
        overall[col] = rows[col].mean()
    overall['empirical_cross_cmi_bit'] = conditional_entropy(group, own) - conditional_entropy(group, both)
    result = pd.concat([rows, pd.DataFrame([overall])], ignore_index=True)
    result['fold_cross_gain_bit'] = result.own_bit - result.both_bit
    return result.assign(side=side)


def report(raw):
    paired = paired_hands(raw)
    return pd.concat([conditional_scores(group, side).assign(timing=timing)
                      for timing, group in paired.groupby('timing') for side in ('left', 'right')], ignore_index=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--out', type=Path, default=Path('runs/c_feas_joint_packet/summary.csv'))
    args = parser.parse_args()
    table = report(get_windows(args.root))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.out, index=False, float_format='%.12g')
    print(table[table.scope == 'macro'].to_string(index=False))
