"""Training-only information in a returned wrist point about a fixed terminal event.

AI-assisted data tabulation in OpenCode / OpenAI GPT family, GPT-6.2 Astra
routing group, 2026-11-20. Exact routed release date unavailable.
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

if __package__:
    from .c_palm_motion import palm_points
    from .c_palm_scale import SECOND, SIDES, history_scale, lengths
    from .c_q2_windows import qualified_runs, windows, wrist_inputs
    from .c_quality_information import entropy, log_loss
    from .c_records import ROOT, split_records
else:
    from c_palm_motion import palm_points
    from c_palm_scale import SECOND, SIDES, history_scale, lengths
    from c_q2_windows import qualified_runs, windows, wrist_inputs
    from c_quality_information import entropy, log_loss
    from c_records import ROOT, split_records


def get_windows(root):
    observations = []
    for record, folder, meta in split_records(root, 'train'):
        if folder.parent.name == 'EgoDemo':
            continue
        times, _, wrists, masks = wrist_inputs(folder)
        size = {}
        for side in SIDES:
            hand = palm_points(folder, side, times)
            size[side] = lengths(hand[:, 0], hand[:, 2])
        for run in qualified_runs(times, masks['both']):
            for start, first, end in windows(times, run, SECOND, 2*SECOND):
                anchor, last = first - 1, end - 1
                middle = times.searchsorted(times[anchor] + SECOND)
                for side in SIDES:
                    L = history_scale(size[side][start:first])
                    if L is None:
                        raise ValueError('Training scale pool changed')
                    xyz = wrists[side]
                    f = np.linalg.norm(xyz[anchor]-xyz[start]) > L
                    z = np.linalg.norm(xyz[last]-xyz[anchor]) > L
                    for timing, index in [('first', first), ('at_1s', middle)]:
                        if not first <= index < end or times[index] + SECOND//2 >= times[last]:
                            raise ValueError('Expected actual packet return before the target tick')
                        y = np.linalg.norm(xyz[index]-xyz[anchor]) > L
                        updated = np.linalg.norm(xyz[last]-xyz[index]) > L
                        observations.append(dict(record=record, env=meta['environment_id'], window_start=int(times[start]), side=side,
                                                 timing=timing, f=int(f), y=int(y), z=int(z), updated=int(updated)))
    return pd.DataFrame(observations)


def conditional_entropy(data, keys):
    groups = data.groupby(keys).z.agg(['sum', 'count'])
    return sum(r['count']*entropy(r['sum'], r['count']) for _, r in groups.iterrows()) / len(data)


def fold_predictions(train, held, keys, fallback):
    counts = train.groupby(keys).z.agg(['sum', 'count'])
    probs = ((counts['sum']+.5)/(counts['count']+1)).to_dict()
    cells = held[keys[0]].tolist() if len(keys) == 1 else list(held[keys].itertuples(index=False, name=None))
    return np.array([probs.get(cell, default) for cell, default in zip(cells, fallback)])


def describe(data):
    for (side, timing), group in data.groupby(['side', 'timing']):
        scores = []
        for env, held in group.groupby('env'):
            train = group[group.env != env]
            default = (train.z.sum()+.5)/(len(train)+1)
            p_hist = fold_predictions(train, held, ['f'], np.full(len(held), default))
            p_packet = fold_predictions(train, held, ['f', 'y'], p_hist)
            loss = pd.DataFrame(dict(rec=held.record, before=log_loss(held.z, p_hist),
                                     after=log_loss(held.z, p_packet))).groupby('rec').mean().mean()
            scores.append(loss)
            yield dict(scope='environment', name=env, side=side, timing=timing, windows=len(held),
                       records=held.record.nunique(), packet_large=int(held.y.sum()),
                       original_exceed=int(held.z.sum()), avoided=int(((held.z==1)&(held.updated==0)).sum()),
                       induced=int(((held.z==0)&(held.updated==1)).sum()),
                       held_loss_before=loss.before, held_loss_after=loss.after, held_gain=loss.before-loss.after)
        loss = pd.DataFrame(scores).mean()
        h0, h1 = conditional_entropy(group, ['f']), conditional_entropy(group, ['f','y'])
        yield dict(scope='overall', name='all', side=side, timing=timing, windows=len(group),
                   records=group.record.nunique(), packet_large=int(group.y.sum()), original_exceed=int(group.z.sum()),
                   avoided=int(((group.z==1)&(group.updated==0)).sum()), induced=int(((group.z==0)&(group.updated==1)).sum()),
                   h_before=h0, h_after=h1, empirical_cmi=h0-h1,
                   held_loss_before=loss.before, held_loss_after=loss.after, held_gain=loss.before-loss.after)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--out', type=Path, default=Path('runs/c_quality_information/terminal_packet.csv'))
    args = parser.parse_args()
    data = get_windows(args.root)
    table = pd.DataFrame(describe(data))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.out, index=False, float_format='%.12g')
    print(table.query("scope == 'overall'").to_string(index=False))
