"""Describe quality-label information and score a fixed past-speed onset probe.

AI-assisted training diagnostics in OpenCode, 2026-11-20, OpenAI GPT family,
GPT-6.2 Astra routing group. Exact release date unavailable.
"""

import argparse
from math import log2
from pathlib import Path

import numpy as np
import pandas as pd

if __package__:
    from .c_q1_quality_transitions import run as transition_counts
    from .c_q2_windows import SIDES, wrist_inputs
    from .c_records import ROOT, split_records
else:
    from c_q1_quality_transitions import run as transition_counts
    from c_q2_windows import SIDES, wrist_inputs
    from c_records import ROOT, split_records


def entropy(bad, total):
    if not total:
        return np.nan
    return -sum((n/total)*log2(n/total) for n in (bad, total-bad) if n)


def information(counts):
    gg, gb, bg, bb = (counts[key] for key in ('good_good', 'good_bad', 'bad_good', 'bad_bad'))
    total = gg + gb + bg + bb
    h = entropy(gb+bb, total)
    cond = sum(n*entropy(k, n) for k, n in [(gb, gg+gb), (bb, bg+bb)] if n) / total
    return dict(pairs=total, next_bad=gb+bb, starts=gb, h_bit=h, conditional_h_bit=cond, mi_bit=h-cond)


def onset_inputs(root):
    frames = []
    for rec, folder, meta in split_records(root, 'train'):
        if folder.parent.name == 'EgoDemo':
            continue
        t, normal, points, masks = wrist_inputs(folder)
        dt = np.diff(t)
        close = (dt > 0) & (dt <= 50_000_000)
        # Rows i-1 and i contribute velocity. Only the explicit flag at i+1 is the target.
        keep = masks['both'][:-2] & masks['both'][1:-1] & close[:-1] & close[1:]
        speed = np.maximum.reduce([np.linalg.norm(points[s][1:-1] - points[s][:-2], axis=1)
                                   for s in SIDES]) / (dt[:-1] / 1e9)
        frames.append(pd.DataFrame(dict(environment=meta['environment_id'], record=rec,
                                        speed=speed[keep], onset=~normal[2:][keep])))
    return pd.concat(frames, ignore_index=True)


def probability(labels):
    return (labels.sum() + .5) / (len(labels) + 1)


def log_loss(labels, p):
    return -np.log2(np.where(labels, p, 1-p))


def score_folds(data):
    aggregates = []
    for env, held in data.groupby('environment'):
        train = data[data.environment != env]
        cutoff = train.speed.median()
        high = train.speed > cutoff
        p0 = probability(train.onset)
        p1 = np.where(held.speed > cutoff, probability(train.onset[high]), probability(train.onset[~high]))
        base_loss = log_loss(held.onset, p0)
        speed_loss = log_loss(held.onset, p1)
        # Record means keep long records from dominating an environment's loss.
        scores = pd.DataFrame(dict(record=held.record, base_loss=base_loss, speed_loss=speed_loss))
        mean = scores.groupby('record')[['base_loss', 'speed_loss']].mean().mean()
        aggregates.append(dict(scope='environment', name=env, records=held.record.nunique(),
                               pairs=len(held), starts=int(held.onset.sum()), base_bits=mean.base_loss,
                               speed_bits=mean.speed_loss, gain_bits=mean.base_loss-mean.speed_loss))
    table = pd.DataFrame(aggregates)
    macro = dict(scope='macro', name='equal_environments')
    for key in ('records', 'pairs', 'starts'):
        macro[key] = table[key].sum()
    for key in ('base_bits', 'speed_bits', 'gain_bits'):
        macro[key] = table[key].mean()
    return pd.concat([table, pd.DataFrame([macro])], ignore_index=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--out', type=Path, default=Path('runs/c_quality_information'))
    args = parser.parse_args()
    transitions = pd.DataFrame([dict(scope=r['scope'], name=r['name'], **information(r))
                                for r in transition_counts(args.root)])
    folds = score_folds(onset_inputs(args.root))
    args.out.mkdir(parents=True, exist_ok=True)
    transitions.to_csv(args.out/'persistence.csv', index=False, float_format='%.12g')
    folds.to_csv(args.out/'onset.csv', index=False, float_format='%.12g')
    print(transitions.query("scope == 'all'").to_string(index=False))
    print(folds.to_string(index=False))
