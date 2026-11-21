"""Extra information in a delayed binary finger reading, for two terminal events.

Training report by default. OpenCode assisted tabulation, OpenAI GPT family, GPT-6.2 Astra
routing group, 2026-11-21. Exact model release date unavailable.
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from .c_feas_hand_channels import rotation_error
from .c_hand_state_channels import read_states
from .c_packet_entropy import conditional_entropy, fold_predictions
from .c_palm_scale import SECOND, SIDES, history_scale, lengths
from .c_q2_windows import qualified_runs, windows, wrist_inputs
from .c_quality_information import log_loss
from .c_records import ROOT, split_records


def events_for_hand(record, env, side, time, usable, xyz, middle, quat, bend):
    palm_length = lengths(xyz, middle)
    for run in qualified_runs(time, usable):
        for start, first, end in windows(time, run, SECOND, 2*SECOND):
            anchor, last = first - 1, end - 1
            scale = history_scale(palm_length[start:first])
            if scale is None or not np.isfinite(bend[start:end]).all() or not np.isfinite(quat[start:end]).all():
                raise ValueError('Reference state missing inside a qualified window')
            offset = np.linalg.norm(xyz[[start, last]] - xyz[anchor], axis=1) > scale
            bent = np.abs(bend[[start, last]] - bend[anchor]).max(axis=1) >= 30
            for timing, source in [('first', first), ('at_1s', time.searchsorted(time[anchor] + SECOND))]:
                if not first <= source < end or time[source] + SECOND//2 >= time[last]:
                    raise ValueError('Expected receipt to precede terminal event')
                yield dict(record=record, env=env, side=side, timing=timing,
                           history_move=offset[0], history_bend=bent[0],
                           wrist_move=np.linalg.norm(xyz[source] - xyz[anchor]) > scale,
                           wrist_turn=rotation_error(quat[source], quat[anchor]) >= 30,
                           finger=np.abs(bend[source] - bend[anchor]).max() >= 30,
                           final_move=offset[1], final_bend=bent[1])


def window_events(root, split='train'):
    rows = []
    for record, folder, meta in split_records(root, split):
        if folder.parent.name == 'EgoDemo':
            continue
        time, _, wrist, masks = wrist_inputs(folder)
        for side in SIDES:
            xyz = wrist[side]
            middle, quat, bend = read_states(folder, side, time)
            rows.extend(events_for_hand(record, meta['environment_id'], side, time, masks['both'],
                                        xyz, middle, quat, bend))
    return pd.DataFrame(rows)


HISTORY = ['history_move', 'history_bend']
WRIST = [*HISTORY, 'wrist_move', 'wrist_turn']
FULL = [*WRIST, 'finger']


def score_environment(train, held):
    prior = np.full(len(held), (train.z.sum() + .5) / (len(train) + 1))
    p_history = fold_predictions(train, held, HISTORY, prior)
    p_wrist = fold_predictions(train, held, WRIST, p_history)
    p_full = fold_predictions(train, held, FULL, p_wrist)
    known = set(train[FULL].itertuples(index=False, name=None))
    unseen = sum(cell not in known for cell in held[FULL].itertuples(index=False, name=None))
    losses = pd.DataFrame(dict(record=held.record, wrist_bits=log_loss(held.z, p_wrist),
                               full_bits=log_loss(held.z, p_full))).groupby('record').mean().mean()
    return dict(environment=held.env.iloc[0], windows=len(held), records=held.record.nunique(),
                positives=int(held.z.sum()), finger_positive=int(held.finger.sum()),
                unseen_full_windows=unseen, **losses.to_dict())


def summarize(data):
    output = []
    for (side, timing), batch in data.groupby(['side', 'timing']):
        for target in ('final_bend', 'final_move'):
            group = batch.assign(z=batch[target].astype(int))
            gain_by_env = []
            for env, held in group.groupby('env'):
                train = group[group.env != env]
                gain_by_env.append(score_environment(train, held))
            env_rows = pd.DataFrame(gain_by_env)
            env_rows['fold_gain_bits'] = env_rows.wrist_bits - env_rows.full_bits
            overall = dict(environment='all')
            for key in ('windows', 'records', 'positives', 'finger_positive', 'unseen_full_windows'):
                overall[key] = env_rows[key].sum()
            for key in ('wrist_bits', 'full_bits', 'fold_gain_bits'):
                overall[key] = env_rows[key].mean()
            overall['empirical_cmi_bits'] = conditional_entropy(group, WRIST) - conditional_entropy(group, FULL)
            output.append(pd.concat([env_rows, pd.DataFrame([overall])]).assign(side=side, timing=timing, target=target))
    return pd.concat(output, ignore_index=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--out', type=Path, default=Path('runs/c_finger_information/summary.csv'))
    args = parser.parse_args()
    table = summarize(window_events(args.root))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.out, index=False, float_format='%.12g')
    print(table[table.environment == 'all'].to_string(index=False))
