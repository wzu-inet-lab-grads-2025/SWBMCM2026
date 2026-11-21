"""Event-level checks of stereo-deduplicated image-hand matches at natural flag transitions.

AI-assisted descriptive audit, OpenCode, OpenAI GPT family, GPT-6.2 Astra
routing group, 2026-11-21. Exact release date unavailable.
"""

import argparse
from itertools import groupby
from pathlib import Path

import numpy as np
import pandas as pd

from .c_records import ROOT, official_records, rows


def spells(clock, state):
    for section in np.split(np.arange(len(clock)), np.flatnonzero(np.diff(clock)>50_000_000)+1):
        for value, group in groupby(section,state.__getitem__):
            if value not in ('occlusion','near edge'):
                continue
            ix = list(group)
            first,last=ix[0],ix[-1]
            before = first > section[0] and state[first-1]=='explicit_normal'
            after = last < section[-1] and state[last+1]=='explicit_normal'
            yield value, first, last, before, after


def gather(root, detector):
    folders = {u['episode_uuid']: u['sample'] for u in rows(root/'Restocking_Tasks_and_Constraints/source_segment_index.csv')}
    result=[]
    for ordinal,record in enumerate(official_records(root)):
        if record['dataset']=='EgoDemo':
            continue  # No observed explicit-normal bracketing in the released sparse lists.
        prefix = record['dataset']+'_'+folders[record['episode_uuid']]
        with np.load(detector/(prefix+'_head_left.npz'),allow_pickle=False) as left, np.load(detector/(prefix+'_head_right.npz'),allow_pickle=False) as right:
            clock,states = left['source_ns'],left['quality']
            if not np.array_equal(clock,right['source_ns']) or not np.array_equal(states,right['quality']):
                raise ValueError('Misaligned eye or side annotations')
            matched = (left['slots']>=0)|(right['slots']>=0)
        for hand in (0,1):
            for quality,first,last,before,after in spells(clock,states[:,hand]):
                match=matched[:,hand]
                one=dict(quality=quality,side=('left','right')[hand],dataset=record['dataset'],record=ordinal,
                         spells=1,with_leading_normal=int(before),with_trailing_normal=int(after),complete=int(before and after))
                if before and after:
                    # One vote per spell (and hand); no weighting by the event's length.
                    one.update(prev_match=int(match[first-1]),first_match=int(match[first]),last_match=int(match[last]),
                               next_match=int(match[last+1]),lost_on_entry=int(match[first-1] and not match[first]),
                               gained_on_entry=int(not match[first-1] and match[first]),
                               already_absent=int(not match[first-1] and not match[first]),
                               regained_on_exit=int(not match[last] and match[last+1]),
                               lost_on_exit=int(match[last] and not match[last+1]),
                               ever_matched=int(match[first:last+1].any()))
                result.append(one)
    raw=pd.DataFrame(result)
    tables=[]
    for scope,keys in [('all',['quality']),('source',['quality','dataset']),('hand',['quality','side'])]:
        grouped=raw.groupby(keys,as_index=False)
        summary=grouped.agg({k:'sum' for k in raw if k not in ('quality','dataset','record','side')} | {'record':'nunique'})
        tables.append(summary.rename(columns={'record':'records'}).assign(scope=scope))
    return pd.concat(tables,ignore_index=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=ROOT)
    p.add_argument('--detector',type=Path,default=Path('runs/c_dense_detector/comparison'))
    p.add_argument('--out',type=Path,default=Path('runs/c_dense_joint_check/onset_matches.csv'))
    args=p.parse_args()
    table=gather(args.root,args.detector)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    table.to_csv(args.out,index=False,float_format='%.0f')
    print(table.query("scope=='all'").to_string(index=False))
