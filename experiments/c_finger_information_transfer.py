"""Evaluate fixed finger-event tables on added records, with separate reference strata.

OpenCode-assisted tabulation, OpenAI GPT family, GPT-6.2 Astra routing group,
2026-11-21. Model release date unavailable. No test data or fitted validation data.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from .c_feas_hand_channels import JOINTS, bends_from_points, normalized
from .c_finger_packet_information import WRIST, events_for_hand, score_environment, window_events
from .c_palm_scale import ROOT, SECOND, SIDES, lengths
from .c_records import split_records


def demo_events(root, split):
    events = []
    for record, folder, meta in split_records(root, split):
        if folder.parent.name != 'EgoDemo':
            continue
        fields = {s: f'observation.state.hand_{s}_world' for s in SIDES}
        features = json.loads((folder/'meta/info.json').read_text())['features']
        cols = [k + suffix for k in fields.values() for suffix in ('', '_rotation')]
        data = pq.read_table(sorted((folder/'data').rglob('*.parquet')), columns=['timestamp', 'frame_index', *cols])
        indices = data['frame_index'].to_numpy()
        time = np.rint(data['timestamp'].to_numpy().astype(float) * SECOND).astype(np.int64)
        flags = json.loads((folder/'bad_frame_ratio.json').read_text())
        if not (np.array_equal(indices, np.arange(flags['total_frames'])) and np.all(np.diff(time) > 0)):
            raise ValueError('Invalid Demo clock or frame index')
        listed = [r['frame'] for r in flags['hand_bad_frame_details']]
        # Exclude a flagged frame under either previously audited indexing convention.
        usable = ~(np.isin(indices, listed) | np.isin(indices + 1, listed))
        geometry = {}
        for side, field in fields.items():
            positions, rotations = features[field], features[field + '_rotation']
            names = [f'{side}{finger}{joint}' for finger in ('Index','Middle','Ring','Little') for joint in ('MCP','PIP','DIP')]
            if (positions['shape'] != [21, 3] or rotations['shape'] != [21, 4] or
                    positions['names'][0] != rotations['names'][0] or positions['names'][0][0] != side + 'Wrist'
                    or [positions['names'][0][j] for j in JOINTS] != names or
                    positions['names'][1] != list('xyz') or rotations['names'][1] != ['qw','qx','qy','qz']):
                raise ValueError('Unexpected Demo hand convention')
            points = np.asarray(data[field].to_pylist(), dtype=float)
            wrist = points[:, 0]
            middle = points[:, 9]
            quat = normalized(np.asarray(data[field + '_rotation'].to_pylist(), dtype=float)[:, 0])
            bend = bends_from_points(points[:, JOINTS].reshape(-1, 4, 3, 3))
            usable &= np.isfinite(lengths(wrist, middle)) & np.isfinite(quat).all(axis=1) & np.isfinite(bend).all(axis=1)
            geometry[side] = wrist, middle, quat, bend
        for side in SIDES:
            events.extend(events_for_hand(record, meta['environment_id'], side, time, usable, *geometry[side]))
    return pd.DataFrame(events)


def transfer(training, sample, pool):
    results = []
    for (side, timing), batch in sample.groupby(['side', 'timing']):
        matching = training[(training.side == side) & (training.timing == timing)]
        for target in ['final_bend', 'final_move']:
            rows = []
            for env, group in batch.groupby('env'):
                # In Demo-train, some environments also have explicit-normal training records.
                train = matching[matching.env != env]
                row = score_environment(train.assign(z=train[target]), group.assign(z=group[target]))
                rows.append(row)
            table = pd.DataFrame(rows)
            counts = ['windows','records','positives','finger_positive','unseen_full_windows']
            macro = {'environment':'all', **table[counts].sum().to_dict(),
                     **table[['wrist_bits','full_bits']].mean().to_dict(), 'scored_environments':len(table),
                     'improved_environments':int(((table.wrist_bits - table.full_bits) > 1e-12).sum())}
            table = pd.concat([table, pd.DataFrame([macro])], ignore_index=True)
            table['gain_bits'] = table.wrist_bits - table.full_bits
            results.append(table.assign(pool=pool, side=side, timing=timing, target=target))
    return pd.concat(results, ignore_index=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--out', type=Path, default=Path('runs/c_finger_information/transfer.csv'))
    args = parser.parse_args()
    training = window_events(args.root)
    validation = window_events(args.root, 'validation')
    if set(training.env) & set(validation.env):
        raise ValueError('Overlap between official training and validation environments')
    pools = {'validation_explicit': validation, 'train_demo_unlisted': demo_events(args.root, 'train'),
             'validation_demo_unlisted': demo_events(args.root, 'validation')}
    table = pd.concat([transfer(training, sample, name) for name, sample in pools.items()], ignore_index=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.out, index=False, float_format='%.12g')
    # The Demo sensitivity data include a one-window environment. Release only their pooled counts and macro scores.
    public = table[(table.pool == 'validation_explicit') | (table.environment == 'all')]
    public.to_csv(args.out.with_name('aggregate_' + args.out.name), index=False, float_format='%.12g')
    print(table[(table.environment == 'all') & (table.timing == 'at_1s')].to_string(index=False))
    # Fix the example by training frequency, without choosing by its outcome or gain.
    right = training[(training.side == 'right') & (training.timing == 'at_1s')]
    key = right.groupby(WRIST, sort=True).size().idxmax()
    cell = right[(right[WRIST] == pd.Series(dict(zip(WRIST, key)))).all(axis=1)]
    print('Most frequent right-hand training context:', tuple(bool(v) for v in key))
    print(cell.groupby('finger').final_bend.agg(['sum','count']).to_string())


if __name__ == '__main__':
    main()
