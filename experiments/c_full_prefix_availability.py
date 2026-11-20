"""All eligible C source segments, actual Q3 adapter: schema/clock availability.

AI-assisted data description, OpenCode/OpenAI GPT family, GPT-6.2 Astra routing
group, 2026-11-21. Exact routed model release date unavailable.
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from .c_records import ROOT, official_records, rows
from .q3_replay.data import csv_samples, demo_samples


def numeric_row(row):
    for name in ('left_hand','right_hand','head_pose','left_head_camera_pose','right_head_camera_pose'):
        if name not in row:
            return False
        pose = row[name]
        is_hand = name.endswith('hand')
        for key, shape in ([('positions',(21,3)),('orientations_xyzw',(21,4))] if is_hand else
                           [('position',(3,)),('orientation_xyzw',(4,))]):
            x = np.asarray(pose[key], dtype=float)
            if x.shape != shape or not np.isfinite(x).all():
                return False
    return True


def label(row):
    flag = row['hand_quality_flags']
    if flag is None:
        return 'unknown'
    if 'is_bad' in flag:
        return 'bad' if flag['is_bad'] else 'normal'
    return 'bad' if flag['left_bad_reason'] or flag['right_bad_reason'] else 'unknown'


def summarize(root):
    rules = root/'Restocking_Tasks_and_Constraints'
    index = {r['episode_uuid']:r for r in official_records(root)}
    stable = []
    for unit in rows(rules/'source_segment_index.csv'):
        if unit['eligible'] != '1':
            continue
        record = index[unit['episode_uuid']]
        if any(unit[k] != record[k] for k in ('dataset','environment_id','split')):
            raise ValueError('Source index and record assignment disagree')
        folder = root/'Raw_Data'/unit['dataset']/unit['sample']
        read = demo_samples if unit['dataset']=='EgoDemo' else csv_samples
        samples = read(folder, unit)
        times = np.array([row['timestamp_s'] for row in samples])
        if np.any(np.diff(times) <= 0) or np.any((times<0) | (times>1)):
            raise ValueError('Invalid disclosed source-time clock')
        finite = np.array([numeric_row(r) for r in samples], dtype=bool)
        quality = np.array([label(r) for r in samples], dtype=str)
        previous = 0
        for width in (0.2,0.6,1.0):
            n = int((times<=width).sum())
            labels = quality[:n]
            stable.append(dict(dataset=unit['dataset'], split=unit['split'], family=unit['family_id'], width=width,
                               eligible=1, empty=int(n==0), no_new=int(n==previous), disclosed=n,
                               new=n-previous, incomplete=int((~finite[:n]).sum()),
                               has_bad=int(np.any(labels=='bad')), marked=int((labels=='bad').sum()),
                               normal=int((labels=='normal').sum()), unknown=int((labels=='unknown').sum())))
            previous = n
    data = pd.DataFrame(stable)
    cols = ['eligible','empty','no_new','disclosed','new','incomplete','has_bad','marked','normal','unknown']
    result = []
    for scope, keys in [('all',[]),('source',['dataset']),('split_family',['split','family'])]:
        group = data.groupby(['width', *keys]); totals = group[cols].sum()
        totals['min_rows'], totals['max_rows'] = group.disclosed.min(), group.disclosed.max()
        result.append(totals.reset_index().assign(scope=scope))
    return pd.concat(result, ignore_index=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=ROOT)
    p.add_argument('--out', type=Path, default=Path('runs/c_full_overview/q3_prefix.csv'))
    args = p.parse_args()
    stats = summarize(args.root)
    assert (stats.disclosed == stats.normal + stats.marked + stats.unknown).all()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    stats.to_csv(args.out,index=False)
    print(stats.query("scope=='all'").to_string(index=False))
