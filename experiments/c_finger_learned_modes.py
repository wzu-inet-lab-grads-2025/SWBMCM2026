"""Train-only comparison of a shared unsupervised finger code at receipt and target.

OpenCode-assisted diagnostic, OpenAI GPT family, GPT-6.2 Astra routing group,
2026-11-21. Exact model release date unavailable. Export only aggregates.
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

from .c_finger_one_bit_encoders import ANGLE_COLUMNS
from .c_finger_packet_information import ROOT, window_events
from .c_packet_entropy import conditional_entropy, fold_predictions
from .c_quality_information import log_loss


WRIST = ['history_wrist_L', 'history_wrist_deg', 'packet_wrist_L', 'packet_wrist_deg']
HISTORY_ANGLES = ['history_' + c for c in ANGLE_COLUMNS]
TERMINAL_ANGLES = ['terminal_' + c for c in ANGLE_COLUMNS]
CONTEXT = ['hist', 'wrist']


def two_modes(values):
    return KMeans(n_clusters=2, n_init=10, random_state=20261121, algorithm='lloyd').fit(values)


def fit_codes(train):
    fingers = two_modes(train[ANGLE_COLUMNS].to_numpy())
    scaler = StandardScaler().fit(train[WRIST])
    wrists = two_modes(scaler.transform(train[WRIST]))
    return fingers, scaler, wrists


def encode(batch, codes):
    fingers, scaler, wrists = codes
    higher = fingers.cluster_centers_.mean(axis=1).argmax()
    return pd.DataFrame(dict(record=batch.record, hist=fingers.predict(batch[HISTORY_ANGLES].to_numpy()) == higher,
                             wrist=wrists.predict(scaler.transform(batch[WRIST])),
                             finger=fingers.predict(batch[ANGLE_COLUMNS].to_numpy()) == higher,
                             z=(fingers.predict(batch[TERMINAL_ANGLES].to_numpy()) == higher).astype(int)))


def evaluate(train, held):
    codes = fit_codes(train)
    fitted, scored = encode(train, codes), encode(held, codes)
    default = np.full(len(held), (fitted.z.sum() + .5) / (len(fitted) + 1))
    history = fold_predictions(fitted, scored, ['hist'], default)
    before = fold_predictions(fitted, scored, CONTEXT, history)
    after = fold_predictions(fitted, scored, [*CONTEXT, 'finger'], before)
    centers = codes[0].cluster_centers_
    low, high = centers[np.argsort(centers.mean(axis=1))]
    raw_target = held[TERMINAL_ANGLES].to_numpy()

    # Check how much the held target assignment changes when one more fitting
    # environment is omitted. Align cluster names by mean angular change.
    label_agreement = []
    for omitted in train.env.unique():
        perturbed = two_modes(train.loc[train.env != omitted, ANGLE_COLUMNS].to_numpy())
        high_label = perturbed.cluster_centers_.mean(axis=1).argmax()
        perturbed_z = perturbed.predict(raw_target) == high_label
        label_agreement.append(np.mean(perturbed_z == scored.z.to_numpy()))

    def degree_error(p):
        prediction = low + p[:, None] * (high - low)
        return np.abs(raw_target - prediction).mean(axis=1)

    losses = pd.DataFrame(dict(record=scored.record, before_bits=log_loss(scored.z, before),
                               after_bits=log_loss(scored.z, after), before_deg=degree_error(before),
                               after_deg=degree_error(after))).groupby('record').mean().mean()
    return dict(environment=held.env.iloc[0], windows=len(held), records=held.record.nunique(),
                terminal_high=int(scored.z.sum()), min_inner_agreement=min(label_agreement), **losses.to_dict())


def summarize(data):
    results = []
    for hand, batch in data.query("timing == 'at_1s'").groupby('side'):
        rows = [evaluate(batch[batch.env != env], held) for env, held in batch.groupby('env')]
        table = pd.DataFrame(rows)
        encoded = encode(batch, fit_codes(batch))
        aggregate = dict(environment='all', windows=len(batch), records=batch.record.nunique(),
                         terminal_high=int(table.terminal_high.sum()),
                         min_inner_agreement=table.min_inner_agreement.min(),
                         empirical_cmi_bits=conditional_entropy(encoded, CONTEXT) - conditional_entropy(encoded, [*CONTEXT, 'finger']))
        for key in ('before_bits', 'after_bits', 'before_deg', 'after_deg'):
            aggregate[key] = table[key].mean()
        table = pd.concat([table, pd.DataFrame([aggregate])], ignore_index=True)
        results.append(table.assign(side=hand, gain_bits=table.before_bits - table.after_bits))
    return pd.concat(results, ignore_index=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--out', type=Path, default=Path('runs/c_finger_information/learned_modes.csv'))
    args = parser.parse_args()
    result = summarize(window_events(args.root, include_finger_angles=True))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.out, index=False, float_format='%.12g')
    print(result.query("environment == 'all'").to_string(index=False))
