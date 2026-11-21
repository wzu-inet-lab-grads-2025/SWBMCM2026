"""Dense source-time stereo appearance agreement at the supplied 3D hand locations.

AI-assisted data audit, OpenCode / OpenAI GPT family, GPT-6.2 Astra routing group,
2026-11-21. Exact routed model release date unavailable.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from itertools import zip_longest
from pathlib import Path
import os
import subprocess
import tempfile
import zipfile

import numpy as np
import pandas as pd

from .c_full_hand_projection import inputs
from .c_quality_video_examples import marked_clock
from .c_records import ROOT, official_records, rows


WIDTH, HEIGHT = 480, 364  # Exact quarter of the native dimensions of both head cameras.
RADIUS, CONTROL_OFFSET = 4, 12
TEXTURE_SD = 5.0
PATCH_Y, PATCH_X = np.mgrid[-RADIUS:RADIUS+1, -RADIUS:RADIUS+1].reshape(2, -1)
STATES = ('normal', 'occlusion', 'near edge', 'other_hand_only', 'unlisted')


@contextmanager
def gray_video(path):
    args = ['ffmpeg','-nostdin','-v','error','-xerror','-err_detect','explode','-threads','1','-noautorotate',
            '-i',str(path),'-map','0:v:0','-filter_threads','1','-vf',
            f'scale={WIDTH}:{HEIGHT}:flags=area:out_range=full,format=gray','-vsync','0','-threads','1','-f','rawvideo','pipe:1']
    with tempfile.TemporaryFile() as err, subprocess.Popen(args, stdout=subprocess.PIPE, stderr=err) as proc:
        def frames():
            while block := proc.stdout.read(WIDTH*HEIGHT*12):
                yield np.frombuffer(block,np.uint8).reshape(-1,HEIGHT,WIDTH)
        try:
            yield frames()
        finally:
            proc.stdout.close()
            if proc.wait():
                err.seek(0)
                raise RuntimeError(f'Failed to decode {path}: {err.read()[-400:]}')


def compare(left, right, lp, rp, depths_ok):
    """Same candidate centres, without selecting a best offset; 12px vertical controls."""
    n = len(left)
    shape = (n, 2, 21)
    score = np.full(shape,np.nan,dtype=np.float32)
    control = np.full_like(score,np.nan)
    lx, ly = np.moveaxis(lp,-1,0)
    rx, ry = np.moveaxis(rp,-1,0)
    ok = (depths_ok & (lx>=RADIUS) & (lx<WIDTH-RADIUS) & (ly>=RADIUS) & (ly<HEIGHT-RADIUS)
          & (rx>=RADIUS) & (rx<WIDTH-RADIUS) & (ry>=RADIUS+CONTROL_OFFSET) & (ry<HEIGHT-RADIUS-CONTROL_OFFSET))
    ix = np.nonzero(ok)
    if len(ix[0]) == 0:
        return ok, score, control
    def patch(image, x, y, shift):
        a = image[ix[0][:,None], y[ix][:,None] + PATCH_Y + shift, x[ix][:,None] + PATCH_X].astype(np.float32)
        a -= a.mean(axis=1,keepdims=True)
        rms = np.sqrt(np.mean(a*a,axis=1))
        return a/np.maximum(rms,1e-12)[:,None], rms >= TEXTURE_SD

    a, textured = patch(left, lx, ly, 0)
    correlations = []
    for shift in (0, -CONTROL_OFFSET, CONTROL_OFFSET):
        b, valid = patch(right,rx,ry,shift)
        textured &= valid
        correlations.append(np.mean(a*b,axis=1))
    target = tuple(v[textured] for v in ix)
    score[target] = correlations[0][textured]
    control[target] = .5*(correlations[1][textured]+correlations[2][textured])
    return ok, score, control


def analyze(job):
    ordinal, folder, ds, out, resume = job
    clock, labels, left_file = marked_clock(folder)
    target = out/f'{ordinal:03d}.npz'
    if resume and target.is_file():
        try:
            with np.load(target,allow_pickle=False) as saved:
                shape = (len(clock),2,21)
                if (np.array_equal(clock,saved['clock']) and saved['quality'].shape == (len(clock),2)
                        and all(saved[k].shape==shape for k in ('geometry','projected','shifted'))):
                    return len(clock)
        except (OSError, EOFError, ValueError, KeyError, zipfile.BadZipFile):
            pass  # An interrupted archive is regenerated from the video pair.
    if ds == 'EgoDemo':
        right_file, = (folder/'videos/observation.images.head_right').rglob('*.mp4')
    else:
        right_file = folder/'videos/head_right.mp4'
    coords, inside = {}, {}
    for view, side, uv, in_frame, w, h in inputs(folder, clock):
        assert (w,h) == (4*WIDTH,4*HEIGHT)
        # The half-pixel accounts for the centres of the four-pixel source blocks.
        coords[view,side] = np.rint((np.where(in_frame[:,:,None],uv,0)+.5)/4-.5).astype(np.int32)
        inside[view,side] = in_frame
    sides = ('left','right')
    lp, rp = [np.stack([coords[v,s] for s in sides],axis=1) for v in ('head_left','head_right')]
    both = np.stack([inside['head_left',s] & inside['head_right',s] for s in sides], axis=1)
    quality = np.empty((len(clock),2),dtype=np.uint8)
    for i, reasons in enumerate(labels):
        for j, side in enumerate(sides):
            own = [x.split(':',1)[1] for x in reasons if x.startswith(side+':')]
            state, = own or ['other_hand_only' if reasons else 'unlisted' if ds=='EgoDemo' else 'normal']
            quality[i,j] = STATES.index(state)
    pos = 0
    masks, matched, shifted = [], [], []
    with gray_video(left_file) as lf, gray_video(right_file) as rf:
        for a,b in zip_longest(lf,rf):
            if a is None or b is None or a.shape != b.shape or pos+len(a)>len(clock):
                raise ValueError('Unequal stereo or trajectory lengths')
            sl = slice(pos,pos+len(a))
            valid, score, ctrl = compare(a,b,lp[sl],rp[sl],both[sl])
            masks.append(valid); matched.append(score); shifted.append(ctrl)
            pos += len(a)
    if pos!=len(clock):
        raise ValueError('Incomplete stereo record')
    partial = target.with_suffix('.partial.npz')
    np.savez_compressed(partial, clock=clock, quality=quality, geometry=np.concatenate(masks),
                        projected=np.concatenate(matched), shifted=np.concatenate(shifted))
    os.replace(partial,target)
    return pos


def summarize(out, index):
    summaries = []
    matches = []
    keys = ('points','interior','comparable','projected_sum','control_sum','wins')
    for i,r in enumerate(index):
        with np.load(out/f'{i:03d}.npz', allow_pickle=False) as values:
            geometrical = values['geometry']; projected, shifted = values['projected'],values['shifted']
            clock = values['clock']; n = len(clock); quality = values['quality']
            assert projected.shape == shifted.shape == geometrical.shape == (n,2,21)
            assert np.array_equal(np.isfinite(projected),np.isfinite(shifted))
            if r['dataset'] != 'EgoDemo':
                matches.extend(matched_times(clock, quality, projected, shifted, i, r))
            for state in np.unique(quality):
                selected = quality == state
                a, b = projected[selected], shifted[selected]
                comparable = np.isfinite(a)
                counts = dict(points=a.size,interior=int(geometrical[selected].sum()),comparable=int(comparable.sum()),
                              projected_sum=float(np.nansum(a,dtype=np.float64)),control_sum=float(np.nansum(b,dtype=np.float64)),
                              wins=int((a>b).sum()))
                summaries.append(dict(dataset=r['dataset'],split=r['split'],quality=STATES[state],**counts))
    table = pd.DataFrame(summaries)
    groups = []
    for scope, names in [('all',['quality']),('source',['dataset','quality']),('split',['split','quality'])]:
        agg = table.groupby(names,as_index=False)[list(keys)].sum()
        count = agg.comparable.replace(0,np.nan)
        agg['projected_zncc'] = agg.pop('projected_sum')/count
        agg['control_zncc'] = agg.pop('control_sum')/count
        groups.append(agg.assign(scope=scope))
    result = pd.concat(groups,ignore_index=True)
    result.to_csv(out/'stereo_patches.csv',index=False,float_format='%.9g')
    print(result.query("scope=='all'").to_string(index=False))
    paired = pd.DataFrame(matches)
    group = paired.groupby(['quality','record','environment'], as_index=False)
    recs = group.agg(flagged_ticks=('joints','size'), time_matches=('found','sum'), scorable=('delta','count'),
                     joints=('joints','sum'), delta=('delta','mean'), flag_margin=('flag_margin','mean'),
                     normal_margin=('normal_margin','mean'))
    output = []
    for state, data in recs.groupby('quality'):
        eligible = data.dropna(subset=['delta'])
        macro = eligible.groupby('environment')[['delta','flag_margin','normal_margin']].mean().mean()
        output.append(dict(quality=state, records_with_flag=len(data), records_scored=len(eligible),
                           envs_scored=eligible.environment.nunique(), **data[['flagged_ticks','time_matches','scorable','joints']].sum().to_dict(),
                           **macro.to_dict()))
    pd.DataFrame(output).to_csv(out/'stereo_matched.csv',index=False,float_format='%.9g')


def matched_times(clock, quality, actual, shifted, record, meta):
    """Same record, hand, landmarks. Nearest explicit normals 0.25--1 s away on each side."""
    # At a given camera time and hand, status covers both head views together.
    for side in range(2):
        labels = quality[:,side]
        normals = np.flatnonzero(labels==STATES.index('normal'))
        times = clock[normals]
        for pos in np.flatnonzero(np.isin(labels,[STATES.index('occlusion'),STATES.index('near edge')])):
            a = times.searchsorted(clock[pos]-250_000_000,side='right')-1
            b = times.searchsorted(clock[pos]+250_000_000)
            row = dict(record=record,environment=meta['environment_id'], quality=STATES[labels[pos]],
                       found=0, joints=0, flag_margin=np.nan, normal_margin=np.nan, delta=np.nan)
            if a>=0 and b<len(times) and clock[pos]-times[a]<=10**9 and times[b]-clock[pos]<=10**9:
                row['found'] = 1
                ix = [normals[a],pos,normals[b]]
                margins = (actual[ix,side]-shifted[ix,side]).astype(np.float64)
                keep = np.isfinite(margins).all(axis=0)
                row['joints'] = int(keep.sum())
                if keep.any():
                    before,middle,after = margins[:,keep].mean(axis=1)
                    row.update(flag_margin=middle,normal_margin=(before+after)/2,delta=middle-(before+after)/2)
            yield row


if __name__=='__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=ROOT)
    p.add_argument('--out',type=Path,default=Path('runs/c_dense_stereo'))
    p.add_argument('--summary-only',action='store_true')
    p.add_argument('--resume',action='store_true',help='Reuse complete record arrays from this same run and inputs')
    args = p.parse_args()
    os.umask(0o077)
    args.out.mkdir(parents=True,exist_ok=True)
    index = official_records(args.root)
    if not args.summary_only:
        locations = {r['episode_uuid']:args.root/'Raw_Data'/r['dataset']/r['sample']
                     for r in rows(args.root/'Restocking_Tasks_and_Constraints/source_segment_index.csv')}
        jobs = [(i,locations[r['episode_uuid']],r['dataset'],args.out,args.resume) for i,r in enumerate(index)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            print('Stereo source ticks decoded',sum(pool.map(analyze,jobs)),flush=True)
    summarize(args.out,index)
