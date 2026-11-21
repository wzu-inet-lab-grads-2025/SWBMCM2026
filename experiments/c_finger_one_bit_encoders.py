"""One-bit finger encodings under a fixed clock, using training environments only.

OpenCode-assisted exploratory comparison, OpenAI GPT family, GPT-6.2 Astra
routing group, 2026-11-21. Exact model release date unavailable.
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA

from .c_finger_packet_information import ROOT, score_environment, window_events


ANGLE_COLUMNS = ['index_deg', 'middle_deg', 'ring_deg', 'little_deg']


def encode(train, held):
    """Fit only on packet-source angle changes in the nine training environments."""
    yield 'max_30deg', train.max(axis=1) >= 30, held.max(axis=1) >= 30

    pca = PCA(n_components=1, svd_solver='full').fit(train)
    train_pc = pca.transform(train)[:, 0]
    cut = np.median(train_pc)
    yield 'pc1_median', train_pc > cut, pca.transform(held)[:, 0] > cut

    model = KMeans(n_clusters=2, n_init=10, random_state=20261121, algorithm='lloyd').fit(train)
    # Give the clusters a reproducible name; the probability table only needs their identity.
    higher_mean = model.cluster_centers_.mean(axis=1).argmax()
    yield 'kmeans_2', model.labels_ == higher_mean, model.predict(held) == higher_mean


def compare(data):
    rows = []
    for hand, batch in data[data.timing == 'at_1s'].groupby('side'):
        for env, held in batch.groupby('env'):
            train = batch[batch.env != env]
            encodings = encode(train[ANGLE_COLUMNS].to_numpy(), held[ANGLE_COLUMNS].to_numpy())
            for name, train_y, held_y in encodings:
                for target in ('final_bend', 'final_move'):
                    result = score_environment(train.assign(finger=train_y, z=train[target]),
                                               held.assign(finger=held_y, z=held[target]))
                    rows.append(dict(side=hand, target=target, encoder=name, **result))
    envs = pd.DataFrame(rows)
    envs['gain_bits'] = envs.wrist_bits - envs.full_bits
    envs['improved_environments'] = (envs.gain_bits > 1e-12).astype(int)
    macro = envs.groupby(['side', 'target', 'encoder'], as_index=False).agg(
        windows=('windows', 'sum'), records=('records', 'sum'), positives=('positives', 'sum'),
        finger_positive=('finger_positive', 'sum'), unseen_full_windows=('unseen_full_windows', 'sum'),
        wrist_bits=('wrist_bits', 'mean'), full_bits=('full_bits', 'mean'), gain_bits=('gain_bits', 'mean'),
        improved_environments=('improved_environments', 'sum'))
    return pd.concat([envs, macro.assign(environment='all')], ignore_index=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--out', type=Path, default=Path('runs/c_finger_information/one_bit_encoders.csv'))
    args = parser.parse_args()
    data = window_events(args.root, include_finger_angles=True)
    table = compare(data)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.out, index=False, float_format='%.12g')
    print(table.query("environment == 'all'").to_string(index=False))
