"""Offline validation inputs. Only samples in the largest permitted prefix are loaded into a trial."""
import csv
import json
import random
from decimal import Decimal
from pathlib import Path

from .engine import Source, Trial

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / 'data/raw/C/Raw_Data'
RULES = ROOT / 'data/raw/C/Restocking_Tasks_and_Constraints'


def records(path):
    with path.open(encoding='utf-8-sig', newline='') as handle:
        yield from csv.DictReader(handle)


def configuration():
    rules = json.loads((RULES/'common_conditions.json').read_text())
    zones = {r['zone_id']: [float(r['x']), float(r['y'])] for r in records(RULES/'service_zones.csv')}
    return rules, zones


def csv_samples(folder, unit):
    tables = folder/'tables'
    # The timestamp of this semantic message denotes the annotation start.
    annotation = next(r for i,r in enumerate(records(tables/'annotation_semantic_segments.csv'))
                      if i == int(unit['source_segment_index']))
    assert Decimal(annotation['segment.start_time']) == Decimal(unit['start_s'])
    origin = int(annotation['header.ts'])
    samples = {}
    channels = [('pose_left_hand','left_hand',21), ('pose_right_hand','right_hand',21),
                ('pose_head','head_pose',1), ('pose_headcam','left_head_camera_pose',1),
                ('pose_right_eye_cam','right_head_camera_pose',1),
                ('annotation_bad_frame_pose_hand','hand_quality_flags',0)]
    for table, channel, count in channels:
        for r in records(tables/(table+'.csv')):
            offset = int(r['header.ts']) - origin
            if offset < 0:
                continue
            if offset > 10**9:
                break
            row = samples.setdefault(offset, {'timestamp_s': offset/10**9})
            if count == 0:
                row[channel] = {'is_bad': {'True': True, 'False': False}[r['is_bad']],
                                'reasons': [r[f'problem_type[{i}]'] for i in range(int(r['problem_type.length']))]}
                continue
            def vectors(field, order):
                def component(i, axis):
                    value = r[f'transforms[{i}].{field}.{axis}']
                    return float(value) if value else None
                return [[component(i, axis) for axis in order] for i in range(count)]
            positions, rotations = vectors('pos','xyz'), vectors('quat','xyzw')
            row[channel] = ({'positions': positions, 'orientations_xyzw': rotations} if count == 21 else
                            {'position': positions[0], 'orientation_xyzw': rotations[0]})
    return [samples[i] for i in sorted(samples)]


def demo_samples(folder, unit):
    import pyarrow.parquet as pq
    annotation = json.loads((folder/'annotation.json').read_text())['subtask_annotations'][int(unit['source_segment_index'])]
    start = Decimal(unit['start_s'])
    assert Decimal(str(annotation['start_time'])) == start
    quality = json.loads((folder/'bad_frame_ratio.json').read_text())
    flags = {r['frame']: r for r in quality['hand_bad_frame_details']}
    channels = {'left_hand': ('hand_left_world','hand_left_world_rotation'),
                'right_hand': ('hand_right_world','hand_right_world_rotation'),
                'head_pose': ('head_world','head_world_rotation'),
                'left_head_camera_pose': ('head_left_camera_position','head_left_camera_rotation'),
                'right_head_camera_pose': ('head_right_camera_position','head_right_camera_rotation')}
    prefix = 'observation.state.'
    cols = ['timestamp','frame_index'] + [prefix+k for pair in channels.values() for k in pair]
    table = pq.read_table(next((folder/'data').rglob('*.parquet')), columns=cols)
    result = []
    for i, stamp in enumerate(table['timestamp'].to_pylist()):
        offset = Decimal.from_float(stamp)-start
        if not 0 <= offset <= 1:
            continue
        flag = flags.get(table['frame_index'][i].as_py())
        row = {'timestamp_s': float(offset), 'hand_quality_flags': None if flag is None else
               {key:flag[key] for key in ('left_bad_reason','right_bad_reason')}}
        for channel,(xyz,quat) in channels.items():
            pos, rot = (table[prefix+k][i].as_py() for k in (xyz,quat))
            def xyzw(q):
                return q[1:] + q[:1]
            if channel.endswith('_hand'):
                row[channel] = {'positions':pos, 'orientations_xyzw':[xyzw(q) for q in rot]}
            else:
                if channel == 'head_pose':
                    pos, rot = pos[0], rot[0]
                row[channel] = {'position':pos, 'orientation_xyzw':xyzw(rot)}
        result.append(row)
    return result


def validation_trial(case_id='S1', seed=2026111700):
    rules, zones = configuration()
    if seed not in rules['validation_seeds']:
        raise ValueError('expected a public validation seed')
    case = next(c for c in rules['cases'] if c['case_id'] == case_id)
    jobs = {r['job_id']: dict(job_id=r['job_id'], family_id=r['family_id'], zone_id=r['zone_id'],
                              release_s=float(r['release_s']), due_s=float(r['due_s']),
                              priority_weight=float(r['priority_weight']),
                              predecessors=r['predecessors'].split('|') if r['predecessors'] else [])
            for r in records(RULES/'tasks.csv') if r['case_id']==case_id}
    pool = [r for r in records(RULES/'source_segment_index.csv') if r['split']=='validation' and r['eligible']=='1']
    rng = random.Random(seed)
    sources = {}
    for j in sorted(jobs):
        unit = rng.choice([r for r in pool if r['family_id']==jobs[j]['family_id']])
        folder = RAW/unit['dataset']/unit['sample']
        reader = demo_samples if unit['dataset']=='EgoDemo' else csv_samples
        d = float(Decimal(unit['end_s'])-Decimal(unit['start_s']))
        sources[j] = Source(unit['unit_id'],d,reader(folder,unit))
    return Trial(jobs,sources,zones,case,rules)
