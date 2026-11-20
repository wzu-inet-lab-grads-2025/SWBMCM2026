"""Check numeric camera calibration support across all C records and video views.

AI-assisted data tabulation in OpenCode, OpenAI GPT family, GPT-6.2 Astra
routing group, 2026-11-21. Exact routed release date unavailable.
"""

import argparse
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

if __package__:
    from .c_records import ROOT, official_records, rows
else:
    from c_records import ROOT, official_records, rows


def geometry(folder):
    if folder.parent.name == 'EgoDemo':
        columns = [f'observation.state.head_{s}_camera_position' for s in ('left','right')]
        table = pq.read_table(sorted((folder/'data').rglob('*.parquet')), columns=columns)
        xyz = [np.asarray(col.to_pylist(), dtype=float) for col in table.columns]
        meta = pq.read_table(sorted((folder/'meta/episodes').rglob('*.parquet')))
        info = json.loads((folder/'meta/info.json').read_text())['features']
        cameras = {}
        for p in (folder/'videos').rglob('*.mp4'):
            field = p.relative_to(folder/'videos').parts[0]
            key = field.removeprefix('observation.images.')
            h,w,_ = info[field]['shape']
            intrinsic = np.array(meta[f'camera_intrinsics/{key}'][0].as_py())
            cameras[key] = (p, np.array([[w,h,*intrinsic[:4]]]), bool(np.any(intrinsic[4:])),
                            f'observation.state.{key}_camera_position' in info and f'observation.state.{key}_camera_rotation' in info)
    else:
        attrs = [f'transforms[0].pos.{a}' for a in 'xyz']
        pose = [pd.read_csv(folder/f'tables/{name}.csv', usecols=['header.ts',*attrs], dtype={'header.ts':str})
                for name in ('pose_headcam','pose_right_eye_cam')]
        if not np.array_equal(pose[0]['header.ts'], pose[1]['header.ts']):
            raise ValueError('Stereo pose clocks differ')
        xyz = [frame[attrs].to_numpy() for frame in pose]
        cameras = {}
        for p in (folder/'videos').glob('*.mp4'):
            key = p.stem
            k = pd.read_csv(folder/f'tables/sensor_camera_{key}_intrinsic.csv')
            vals = k[['width','height','K[0]','K[4]','K[2]','K[5]']].to_numpy()
            distortion = k[[col for col in k if col.startswith('D[')]].fillna(0).to_numpy()
            cameras[key] = (p, vals, bool(np.any(distortion)), (folder/f'tables/sensor_camera_{key}_extrinsic.csv').is_file())
    if xyz[0].shape != xyz[1].shape or xyz[0].shape[1:] != (3,):
        raise ValueError('Unexpected stereo camera position shape')
    baseline = 100*np.linalg.norm(xyz[0]-xyz[1], axis=1)
    if not np.isfinite(baseline).all() or not (baseline>0).all():
        raise ValueError('Invalid stereo baseline')
    return baseline, cameras


def audit(root):
    folders = {r['episode_uuid']:root/'Raw_Data'/r['dataset']/r['sample'] for r in rows(root/'Restocking_Tasks_and_Constraints/source_segment_index.csv')}
    baselines, clips = [], []
    for rec in official_records(root):
        dataset = rec['dataset']
        baseline, cameras = geometry(folders[rec['episode_uuid']])
        baselines.append(dict(dataset=dataset,records=1,points=len(baseline),min_cm=baseline.min(),
                              max_cm=baseline.max(),within_range_cm=np.ptp(baseline)))
        for key,(path, intr, distorted, ext) in cameras.items():
            probe = subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-show_entries',
                                             'stream=width,height','-of','json',str(path)])
            stream, = json.loads(probe)['streams']
            w,h = stream['width'],stream['height']
            valid = np.isfinite(intr).all() and (intr[:,2:4]>0).all()
            centers = (intr[:,4:]>=0).all() and (intr[:,4:]<intr[:,:2]).all()
            clips.append(dict(dataset=dataset,view=key,clips=1,finite_positive_focal=int(valid),
                              image_size_agrees=int((intr[:,:2]==[w,h]).all()),center_inside=int(centers),
                              intrinsics_change=int(np.any(np.ptp(intr,axis=0)>0)),distortion_nonzero=int(distorted),
                              world_pose_file_or_field=int(ext)))
    stereo = pd.DataFrame(baselines).groupby('dataset',as_index=False).agg(records=('records','sum'), points=('points','sum'),
              min_cm=('min_cm','min'), max_cm=('max_cm','max'), max_within_record_range_cm=('within_range_cm','max'))
    cams = pd.DataFrame(clips).groupby(['dataset','view'],as_index=False).sum(numeric_only=True)
    return stereo, cams


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=ROOT)
    p.add_argument('--out', type=Path, default=Path('runs/c_full_overview'))
    args = p.parse_args()
    stereo, cams = audit(args.root)
    args.out.mkdir(parents=True, exist_ok=True)
    for name, table in [('stereo_geometry',stereo),('camera_intrinsics',cams)]:
        table.to_csv(args.out/f'{name}.csv', index=False, float_format='%.12g')
        print(table.to_string(index=False))
