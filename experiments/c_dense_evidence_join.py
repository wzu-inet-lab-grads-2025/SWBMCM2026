"""Align stereo and temporal pixel evidence at the same source tick, hand and joint.

AI-assisted descriptive audit in OpenCode / OpenAI GPT family, GPT-6.2 Astra
routing group, 2026-11-21. Exact routed release date unavailable.
"""

import argparse
import os
from pathlib import Path

import numpy as np
import pandas as pd

from .c_dense_stereo_patches import STATES
from .c_records import ROOT, official_records


def flow_metrics(path, clock):
    with np.load(path, allow_pickle=False) as f:
        if not np.array_equal(clock,f['clock']):
            raise ValueError('Source clocks differ across private evidence files')
        xy, mask, motion = f['xy'], f['inside'], f['motion']
        delta = np.full_like(xy,np.nan)
        delta[1:] = xy[1:]-xy[:-1]
        available = mask & np.isfinite(motion).all(axis=-1)
        available[0] = False
        available[1:] &= mask[:-1]
        epe = np.where(available,np.linalg.norm(delta-motion,axis=-1),np.nan)
        zero = np.where(available,np.linalg.norm(delta,axis=-1),np.nan)
        return epe, zero


def quantized_feature(values, scale):
    valid = np.isfinite(values)
    codes = np.full(values.shape,-32768,dtype='<i2')
    codes[valid] = np.clip(np.rint(scale*values[valid]),-32767,32767).astype(np.int16)
    return codes


def hand_medians(values):
    count = np.isfinite(values).sum(axis=2)
    median = np.full(count.shape,np.nan)
    valid = count>0
    median[valid] = np.nanmedian(values[valid],axis=1)
    return count, median


def run(root, stereo, flow, out, detector):
    out.mkdir(parents=True,exist_ok=True)
    stats, matches = [], []
    locations = {}
    if detector:
        from .c_records import rows
        locations = {r['episode_uuid']: r['sample'] for r in rows(root/'Restocking_Tasks_and_Constraints/source_segment_index.csv')}
    for i, record in enumerate(official_records(root)):
        with np.load(stereo/f'{i:03d}.npz', allow_pickle=False) as z:
            clock, quality = z['clock'], z['quality']
            stereo_margin = z['projected']-z['shifted']
        both = [flow_metrics(flow/f"{record['episode_uuid']}_head_{side}.npz",clock) for side in ('left','right')]
        epe = (both[0][0] + both[1][0])/2
        zero = (both[0][1] + both[1][1])/2
        common = np.isfinite(epe) & np.isfinite(stereo_margin)
        stereo_wins = stereo_margin > 0
        motion_wins = epe < zero
        if detector:
            associated = []
            for view in ('head_left','head_right'):
                name = f"{record['dataset']}_{locations[record['episode_uuid']]}_{view}.npz"
                with np.load(detector/name,allow_pickle=False) as det:
                    if not np.array_equal(clock,det['source_ns']):
                        raise ValueError('Detector and stereo clocks disagree')
                    associated.append(det['slots']>=0)
            eyes = np.sum(associated,axis=0)
            for code in np.unique(quality):
                for matched_eyes in (0,1,2):
                    sel = (quality==code) & (eyes==matched_eyes)
                    valid = common[sel]
                    matches.append(dict(quality=STATES[code], stable_eyes=matched_eyes,
                               hand_ticks=int(sel.sum()), hand_ticks_with_common=int(valid.any(axis=1).sum()),
                               common_points=int(valid.sum()),
                               both_controls_beaten=int((valid & motion_wins[sel] & stereo_wins[sel]).sum())))
        # Per-hand readouts for a local evidence panel. Store support for each
        # diagnostic on its own denominator; the aggregate table also gives the intersection.
        sm_n, sm = hand_medians(stereo_margin)
        fm_n, fm = hand_medians(epe)
        measurements = np.stack([sm_n,quantized_feature(sm,1000),fm_n,quantized_feature(fm,100)],axis=-1).astype('<i2')
        np.save(out/f'{i:03d}.npy', measurements, allow_pickle=False)
        for code in np.unique(quality):
            selected = quality==code
            temporal = motion_wins[selected]; spatial = stereo_wins[selected]; support = common[selected]
            stats.append(dict(dataset=record['dataset'], split=record['split'], quality=STATES[code],
                       candidates=support.size, joint_support=int(support.sum()),
                       agree_both=int((support & temporal & spatial).sum()),
                       only_temporal=int((support & temporal & ~spatial).sum()),
                       only_stereo=int((support & ~temporal & spatial).sum()),
                       neither=int((support & ~temporal & ~spatial).sum())))
    table = pd.DataFrame(stats)
    groups = []
    for scope,fields in [('all',['quality']),('source',['quality','dataset']),('split',['quality','split'])]:
        agg = table.groupby(fields,as_index=False).sum(numeric_only=True)
        groups.append(agg.assign(scope=scope))
    result = pd.concat(groups,ignore_index=True)
    assert (result.joint_support == result[['agree_both','only_temporal','only_stereo','neither']].sum(axis=1)).all()
    result.to_csv(out/'cross_evidence.csv',index=False)
    if matches:
        by_detector = pd.DataFrame(matches).groupby(['quality','stable_eyes'],as_index=False).sum(numeric_only=True)
        by_detector.to_csv(out/'cross_detector.csv',index=False)
    print(result.query("scope=='all'").to_string(index=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=ROOT)
    p.add_argument('--stereo',type=Path,default=Path('runs/c_dense_stereo'))
    p.add_argument('--flow',type=Path,default=Path('runs/c_dense_motion'))
    p.add_argument('--out',type=Path,default=Path('runs/c_dense_joint_check'))
    p.add_argument('--detector',type=Path,help='Join the independently generated head-view associations, if supplied')
    args=p.parse_args(); os.umask(0o077); run(args.root,args.stereo,args.flow,args.out,args.detector)
