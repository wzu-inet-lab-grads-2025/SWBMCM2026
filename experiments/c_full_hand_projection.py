"""All-record head-camera geometric projection; field of view versus hand flags.

AI-assisted preprocessing and visual checks in OpenCode / OpenAI GPT family,
GPT-6.2 Astra routing group, 2026-11-21. Exact release date unavailable.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from PIL import Image, ImageDraw
from scipy.spatial.transform import Rotation

from .c_quality_video_examples import marked_clock, read_tiles
from .c_records import ROOT, official_records, rows
from .c_state_examples import sample_records


def project(hand, center, rot, intrinsics, frame_size):
    w,h = frame_size
    fx,fy,cx,cy = intrinsics
    # Row vector form of R_c2w.T @ (point_world - center_world).
    local = np.einsum('nij,njk->nik', hand-center[:,None,:], rot)
    depth = local[:,:,2]
    uv = np.divide(local[:,:,:2],depth[:,:,None],out=np.zeros_like(local[:,:,:2]),where=depth[:,:,None]>0)
    pixels = uv*[fx,fy]+[cx,cy]
    inside = (depth>0) & np.all((pixels>=0) & (pixels<[w,h]),axis=2)
    return pixels, inside


def inputs(folder, clock):
    n = len(clock)
    demo = folder.parent.name == 'EgoDemo'
    if demo:
        features = json.loads((folder/'meta/info.json').read_text())['features']
        meta = pq.read_table(sorted((folder/'meta/episodes').rglob('*.parquet')))
        cols = [f'observation.state.hand_{s}_world' for s in ('left','right')]
        cols += [f'observation.state.head_{eye}_camera_{k}' for eye in ('left','right') for k in ('position','rotation')]
        t = pq.read_table(sorted((folder/'data').rglob('*.parquet')), columns=cols)
        hands = [np.asarray(t[c].to_pylist(), dtype=float) for c in cols[:2]]
    else:
        tb = folder/'tables'
        hand_cols = [f'transforms[{i}].pos.{axis}' for i in range(21) for axis in 'xyz']
        hands = []
        for side in ('left','right'):
            hand = pd.read_csv(tb/f'pose_{side}_hand.csv',usecols=hand_cols+['header.ts'],dtype={'header.ts':'int64'})
            if not np.array_equal(hand['header.ts'],clock):
                raise ValueError('Unaligned hand streams')
            hands.append(hand[hand_cols].to_numpy(dtype=float).reshape(-1,21,3))
    if any(h.shape != (n,21,3) for h in hands):
        raise ValueError('Unexpected hand array')
    for eye in ('left','right'):
        view = f'head_{eye}'
        if demo:
            center = np.array(t[f'observation.state.{view}_camera_position'].to_pylist())
            quat = np.array(t[f'observation.state.{view}_camera_rotation'].to_pylist())[:, [1,2,3,0]]
            h,w,_ = features[f'observation.images.{view}']['shape']
            fx,fy,cx,cy,*dist = meta[f'camera_intrinsics/{view}'][0].as_py()
            if any(dist):
                raise ValueError('Undistortion convention needs explicit treatment')
        else:
            ext = pd.read_csv(tb/f'sensor_camera_{view}_extrinsic.csv',dtype={'_log_time_ns':'int64'})
            if not np.array_equal(clock,ext['_log_time_ns']):
                raise ValueError('Camera and pose clock mismatch')
            if not (ext['transforms[0].parent_frame_id'].eq('world') & ext['transforms[0].child_frame_id'].eq(view+'_camera')).all():
                raise ValueError('Unexpected camera reference frame')
            center = ext[[f'transforms[0].translation.{a}' for a in 'xyz']].to_numpy()
            quat = ext[[f'transforms[0].rotation.{a}' for a in 'xyzw']].to_numpy()
            intr = pd.read_csv(tb/f'sensor_camera_{view}_intrinsic.csv')
            k = intr[['width','height','K[0]','K[4]','K[2]','K[5]']].to_numpy()
            if np.any(np.ptp(k,axis=0)) or intr.filter(regex=r'^D\[').fillna(0).to_numpy().any():
                raise ValueError('This check expects fixed undistorted intrinsics')
            w,h,fx,fy,cx,cy = k[0]
        rot = Rotation.from_quat(quat).as_matrix()
        for side, hand in zip(('left','right'), hands):
            pixels, inside = project(hand,center,rot,(fx,fy,cx,cy),(w,h))
            yield view, side, pixels, inside, w, h


def audit(root, out):
    loc = {r['episode_uuid']:root/'Raw_Data'/r['dataset']/r['sample'] for r in rows(root/'Restocking_Tasks_and_Constraints/source_segment_index.csv')}
    selected = dict(sample_records(root))
    canvas = Image.new('RGB',(3*288,260),'white')
    results = []
    for r in official_records(root):
        ds = r['dataset']
        folder = loc[r['episode_uuid']]
        clock, labels, video = marked_clock(folder)
        n = len(clock)
        sample = folder == selected[ds]
        if sample:
            im, = read_tiles(video,[n//2])
            draw = ImageDraw.Draw(im)
        for view, side, pixels, inside, w, h in inputs(folder,clock):
            states = []
            for reasons in labels:
                own = sorted(x.split(':',1)[1] for x in reasons if x.startswith(side+':'))
                state = '+'.join(own) if own else ('other_hand_only' if reasons else ('unlisted' if ds=='EgoDemo' else 'normal'))
                states.append(state)
            states = np.array(states)
            for state in np.unique(states):
                subset = inside[states==state]
                results.append(dict(dataset=ds, view=view, hand=side, quality=state, hand_ticks=len(subset),
                                    wrist_inside=int(subset[:,0].sum()),all21_inside=int(subset.all(axis=1).sum()),
                                    none_inside=int((~subset.any(axis=1)).sum()),joints_inside=int(subset.sum())))
            if sample and view=='head_left':
                for xy in pixels[n//2][inside[n//2]]:
                    u,v = xy * [im.width/w, im.height/h]
                    draw.ellipse((u-1,v-1,u+1,v+1), fill='lime' if side=='left' else 'magenta')
        if sample:
            pos = list(selected).index(ds)*288
            canvas.paste(im,(pos,20))
            ImageDraw.Draw(canvas).text((pos+4,2), ds+' | head-left, midpoint', fill='black')
    out.mkdir(parents=True, exist_ok=True)
    canvas.save(out/'projection_examples.jpg',quality=92)
    table = pd.DataFrame(results).groupby(['dataset','view','hand','quality'],as_index=False).sum(numeric_only=True)
    table.to_csv(out/'hand_projection.csv',index=False)
    print(table.groupby('quality')[['hand_ticks','wrist_inside','all21_inside','none_inside','joints_inside']].sum())


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=ROOT)
    p.add_argument('--out', type=Path, default=Path('runs/c_full_overview'))
    args = p.parse_args()
    audit(args.root,args.out)
