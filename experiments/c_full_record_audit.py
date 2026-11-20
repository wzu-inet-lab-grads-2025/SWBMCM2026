"""Full non-video pose, calibration, quality and interval audit of the C attachment.

Run from the repository with Python, numpy, pandas and pyarrow. Only group-level
CSV tables are written. All splits are used for descriptive checks only.
AI-assisted audit in OpenCode, general-pro, 2026-11-21. Exact routed model
version and release date unavailable.
"""

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
import csv
from fractions import Fraction
import json
from itertools import combinations
from pathlib import Path
from statistics import median

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

if __package__:
    from .c_audit_quality import DATASETS, ROOT, audit_record, rows
else:
    from c_audit_quality import DATASETS, ROOT, audit_record, rows


REPO = Path(__file__).resolve().parents[1]
POSES = {
    'left_hand': ('hand_left_world', 'hand_left_world_rotation', 21),
    'right_hand': ('hand_right_world', 'hand_right_world_rotation', 21),
    'head': ('head_world', 'head_world_rotation', 1),
    'headcam': ('head_left_camera_position', 'head_left_camera_rotation', 1),
    'right_eye_cam': ('head_right_camera_position', 'head_right_camera_rotation', 1),
}
NS = 10**9


def write_csv(path, data):
    with path.open('w', encoding='utf-8', newline='') as handle:
        out = csv.DictWriter(handle, fieldnames=list(data[0]), lineterminator='\n')
        out.writeheader()
        out.writerows(data)


def combine(items):
    """Sum disjoint counts and durations; retain extrema of record extrema."""
    result = {}
    for row in items:
        for k, v in row.items():
            if v is None:
                result.setdefault(k, None)
            elif result.get(k) is None:
                result[k] = v
            elif k.endswith('_min'):
                result[k] = min(result[k], v)
            elif k.endswith('_max'):
                result[k] = max(result[k], v)
            else:
                result[k] += v
    return result


def groups(records, split=False, cross=False):
    yield 'all', 'all', records
    for dataset in DATASETS:
        yield dataset, 'all', [r for r in records if r['dataset'] == dataset]
    if split:
        for part in ('train', 'validation', 'test'):
            rr = [r for r in records if r['split'] == part]
            yield 'all', part, rr
            if cross:
                for dataset in DATASETS:
                    sub = [r for r in rr if r['dataset'] == dataset]
                    if sub:
                        yield dataset, part, sub


def demo_data(folder):
    info = json.loads((folder / 'meta/info.json').read_text())
    stats = json.loads((folder / 'meta/stats.json').read_text())
    table = pq.read_table(next((folder / 'data').rglob('*.parquet')))
    episode = pq.read_table(next((folder / 'meta/episodes').rglob('*.parquet'))).to_pylist()[0]
    tasks = pq.read_table(folder / 'meta/tasks.parquet').to_pylist()
    assert episode['tasks'] == [r['task'] for r in tasks]
    assert set(table['task_index'].to_pylist()) <= {r['task_index'] for r in tasks}
    assert set(stats) == set(table.column_names)
    assert all(v['count'] == [table.num_rows] for v in stats.values())
    poses = {}
    for name, (position, rotation, points) in POSES.items():
        arrays = []
        for field, axes in ((position, list('xyz')), (rotation, ['qw', 'qx', 'qy', 'qz'])):
            key = 'observation.state.' + field
            feature = info['features'][key]
            names = feature['names']
            assert (names[-1] if isinstance(names[-1], list) else names) == axes
            if points == 21:
                assert len(names[0]) == len(set(names[0])) == points
                assert names[0][0].endswith('Wrist') and names[0][9].endswith('MiddleMCP')
            a = np.asarray(table[key].to_pylist(), dtype=np.float64)
            assert list(a.shape[1:]) == feature['shape']
            arrays.append(a.reshape(table.num_rows, points, len(axes)))
        poses[name] = arrays
    vectors = [v for k, v in episode.items() if k.startswith('camera_intrinsics/')]
    assert all(len(v) == 8 and np.isfinite(v).all() for v in vectors)
    times = [Fraction(float(t)) for t in table['timestamp'].to_numpy()]
    fps = Fraction(str(info['fps']))
    nominal = [Fraction(i, fps) for i in table['frame_index'].to_pylist()]
    annotation = json.loads((folder / 'annotation.json').read_text(), parse_float=Fraction)
    spans = [(Fraction(s['start_time']), Fraction(s['end_time']), s['description'])
             for s in annotation['subtask_annotations']]
    q = json.loads((folder / 'bad_frame_ratio.json').read_text())['hand_bad_frame_details']
    marked = {r['frame'] for r in q if r['left_bad_reason'] or r['right_bad_reason']}
    diagnostics = {'intrinsic_vectors': len(vectors),
                   'demo_stored_vs_nominal_s_max': float(max(abs(t-u) for t, u in zip(times, nominal))),
                   'demo_nonempty_sparse_lists': int(bool(marked)),
                   'demo_nonempty_both_bases_in_bounds': int(bool(marked) and min(marked) >= 1 and max(marked) < len(times))}
    frame = np.asarray(table['frame_index'].to_pylist())
    quality = np.full(len(frame), 'U')
    quality[np.isin(frame, list(marked))] = 'B'
    one_based = np.isin(frame + 1, list(marked))
    return poses, times, spans, {'frame_over_fps': (nominal, spans)}, diagnostics, quality, one_based


def csv_tensor(frame, field, axes, points):
    cols = [f'transforms[{i}].{field}.{a}' for i in range(points) for a in axes]
    numeric = frame[cols].apply(pd.to_numeric, errors='coerce')
    return numeric.to_numpy(dtype=np.float64).reshape(len(frame), points, len(axes))


def csv_data(folder):
    tables = folder / 'tables'
    meta = {r['field']: r['value'] for r in rows(folder / 'session_metadata.csv')}
    annotations = rows(tables / 'annotation_semantic_segments.csv')
    spans = [(Fraction(r['segment.start_time']), Fraction(r['segment.end_time']),
              r['segment.subtask_description']) for r in annotations]
    origins = [int(r['header.ts']) - start * NS for r, (start, _, _) in zip(annotations, spans)]
    assert all(x.denominator == 1 for x in origins)
    origin = int(origins[0])
    poses = {}
    timestamps = None
    for key, (_, _, count) in POSES.items():
        table = pd.read_csv(tables / f'pose_{key}.csv', float_precision='round_trip')
        ts = table['header.ts'].to_numpy(dtype=np.int64)
        if timestamps is None:
            timestamps = ts
        assert np.array_equal(ts, timestamps) and (table['transforms.length'] == count).all()
        assert np.array_equal(table['header.count'], np.arange(len(ts)))
        poses[key] = csv_tensor(table, 'pos', 'xyz', count), csv_tensor(table, 'quat', 'wxyz', count)
    audit = {'extrinsic_streams': 0, 'extrinsic_rows': 0, 'extrinsic_position_abs_max': 0.,
             'extrinsic_q_sign_invariant_l2_max': 0., 'intrinsic_streams': 0, 'intrinsic_rows': 0,
             'intrinsic_varying_streams': 0, 'extrinsic_nonfinite_vectors': 0}
    for view, name in [('head_left', 'headcam'), ('head_right', 'right_eye_cam')]:
        f = pd.read_csv(tables / f'sensor_camera_{view}_extrinsic.csv', float_precision='round_trip')
        assert np.array_equal(f['_log_time_ns'], timestamps) and (f['transforms.length'] == 1).all()
        assert (f['transforms[0].parent_frame_id'] == 'world').all()
        assert (f['transforms[0].child_frame_id'] == view + '_camera').all()
        p, q = poses[name]
        v = csv_tensor(f, 'translation', 'xyz', 1)
        w = csv_tensor(f, 'rotation', 'wxyz', 1)
        audit['extrinsic_streams'] += 1
        audit['extrinsic_rows'] += len(f)
        audit['extrinsic_nonfinite_vectors'] += int((~np.isfinite(v).all(axis=-1)).sum() + (~np.isfinite(w).all(axis=-1)).sum())
        dp = np.abs(v - p)
        dq = np.minimum(np.linalg.norm(q-w, axis=-1), np.linalg.norm(q+w, axis=-1))
        audit['extrinsic_position_abs_max'] = max(audit['extrinsic_position_abs_max'], np.nanmax(dp))
        audit['extrinsic_q_sign_invariant_l2_max'] = max(audit['extrinsic_q_sign_invariant_l2_max'], np.nanmax(dq))
    for path in sorted(tables.glob('*_intrinsic.csv')):
        f = pd.read_csv(path, float_precision='round_trip')
        assert np.array_equal(f['_log_time_ns'], timestamps)
        for key, size in [('K', 9), ('R', 9), ('P', 12), ('D', 0)]:
            assert (f[key + '.length'] == size).all()
            assert np.isfinite(f[[f'{key}[{i}]' for i in range(size)]].to_numpy()).all()
        fields = [k for k in f if not k.startswith('_') and k != 'timestamp']
        audit['intrinsic_streams'] += 1
        audit['intrinsic_rows'] += len(f)
        audit['intrinsic_varying_streams'] += int((f[fields].nunique(dropna=False) > 1).any())
    times = [Fraction(int(x)-origin, NS) for x in timestamps]
    delta_ns = origin - int(meta['header.ts'])
    shifted = [(s + (o-origin)/NS, e + (o-origin)/NS, text) for (s, e, text), o in zip(spans, origins)]
    alternatives = {'metadata_header': ([t + Fraction(delta_ns, NS) for t in times], spans),
                    'per_segment_header': (times, shifted)}
    audit.update(csv_origin_offset_ns_min=delta_ns, csv_origin_offset_ns_max=delta_ns,
                 csv_origin_differs_from_metadata=int(delta_ns != 0),
                 csv_origin_differs_from_first_pose=int(origin != int(timestamps[0])),
                 csv_derived_origin_spread_ns_max=int(max(origins)-min(origins)),
                 csv_records_with_origin_spread=int(max(origins) != min(origins)))
    flags = pd.read_csv(tables / 'annotation_bad_frame_pose_hand.csv', usecols=['header.ts', 'is_bad'])
    assert np.array_equal(flags['header.ts'], timestamps)
    flags = flags['is_bad'].astype(str).str.lower()
    assert flags.isin(['true', 'false', '0', '1']).all()
    quality = np.where(flags.isin(['true', '1']), 'B', 'N')
    return poses, times, spans, alternatives, audit, quality, None


def pose_metrics(p, q):
    norm = np.linalg.norm(q, axis=-1)
    finite = np.isfinite(norm)
    values = norm[finite]
    valid = finite & (norm > 0)
    eligible_pair = valid[1:] & valid[:-1]
    dot = (q[1:] * q[:-1]).sum(axis=-1)
    negative = eligible_pair & (dot < 0)
    cos_half = np.abs(dot[negative]) / (norm[1:][negative] * norm[:-1][negative])
    angles = np.rad2deg(2*np.arccos(np.clip(cos_half, 0, 1)))
    return {'ticks': len(q), 'vectors': norm.size, 'xyz_scalars': p.size, 'quat_scalars': q.size,
            'nonfinite_xyz_scalars': int((~np.isfinite(p)).sum()), 'zero_xyz_vectors': int((p == 0).all(axis=-1).sum()),
            'nonfinite_quat_scalars': int((~np.isfinite(q)).sum()), 'nonfinite_quaternions': int((~finite).sum()),
            'zero_quaternions': int((norm == 0).sum()), 'finite_norm_error_gt_1e_minus_5': int((abs(values-1) > 1e-5).sum()),
            'norm_min': values.min() if values.size else None, 'norm_max': values.max() if values.size else None,
            'finite_nonzero_adjacent_pairs': int(eligible_pair.sum()), 'negative_dot_adjacent_pairs': int(negative.sum()),
            'negative_dot_angle_deg_max': float(angles.max()) if angles.size else None}


def memberships(times, spans, closed=False):
    ends = bisect_right if closed else bisect_left
    return [(bisect_left(times, start), ends(times, end)) for start, end, _ in spans]


def multiplicity(n, slices):
    delta = np.zeros(n+1, dtype=int)
    for lo, hi in slices:
        delta[lo] += 1
        delta[hi] -= 1
    return np.cumsum(delta[:-1])


def coverage(times, spans):
    half = memberships(times, spans)
    m = multiplicity(len(times), half)
    c = multiplicity(len(times), memberships(times, spans, closed=True))
    # Sweep in exact seconds on the observed first-to-last support. Include each
    # endpoint once so touching half-open annotations have zero overlap duration.
    first, last = times[0], times[-1]
    events = Counter({first: 0, last: 0})
    for a, b, _ in spans:
        lo, hi = max(a, first), min(b, last)
        if lo < hi:
            events[lo] += 1
            events[hi] -= 1
    active, prev = 0, first
    measure = Counter()
    for t, change in sorted(events.items()):
        measure[active] += t-prev
        active += change
        prev = t
    assert active == 0 and sum(measure.values()) == last-first
    covered = sum(v for k, v in measure.items() if k > 0)
    repeated = sum(k*v for k, v in measure.items())
    sorted_spans = sorted(spans)
    gaps = [b[0]-a[1] for a, b in zip(sorted_spans, sorted_spans[1:])]
    duration = sum(b-a for a, b, _ in spans)
    tail = min(last-first, max(0, last-max(b for _, b, _ in spans))) if spans else last-first
    lead = min(last-first, max(0, min(a for a, _, _ in spans)-first)) if spans else 0
    result = {'records': 1, 'ticks': len(times), 'segments': len(spans), 'half_unique': int((m > 0).sum()),
              'half_uncovered': int((m == 0).sum()), 'half_multiply_covered': int((m > 1).sum()),
              'half_segment_sum': int(m.sum()), 'closed_unique': int((c > 0).sum()), 'closed_segment_sum': int(c.sum()),
              'closed_multiply_covered': int((c > 1).sum()), 'right_endpoint_hits': int(c.sum()-m.sum()),
              'touching_adjacent_segments': gaps.count(0), 'positive_adjacent_gaps': sum(x > 0 for x in gaps),
              'overlapping_adjacent_segments': sum(x < 0 for x in gaps), 'observed_span_s': last-first,
              'duration_as_annotated_s': duration, 'union_on_span_s': covered,
              'multiply_covered_on_span_s': sum(v for k, v in measure.items() if k > 1),
              'outside_span_segment_sum_s': duration-repeated, 'uncovered_on_span_s': measure[0],
              'leading_uncovered_s': lead, 'internal_uncovered_s': measure[0]-lead-tail, 'trailing_uncovered_s': tail,
              'end_past_last_tick': sum(b > last for _, b, _ in spans),
              'end_past_last_by_gt_10us': sum(b-last > Fraction(1, 100000) for _, b, _ in spans)}
    assert result['half_segment_sum'] == sum(hi-lo for lo, hi in half)
    return result, half


def clock_difference(n, base, other):
    changes = np.zeros(n, dtype=bool)
    changed_segments = 0
    for (lo, hi), (a, b) in zip(base, other):
        x = np.zeros(n, dtype=bool)
        x[lo:hi] = True
        x[a:b] ^= True
        changes |= x
        changed_segments += int(x.any())
    m, c = multiplicity(n, base), multiplicity(n, other)
    return {'records': 1, 'ticks': n, 'segments': len(base), 'changed_segments': changed_segments,
            'any_membership_changed': int(changes.sum()), 'union_membership_changed': int(((m > 0) != (c > 0)).sum()),
            'alternative_half_unique': int((c > 0).sum()), 'alternative_half_segment_sum': int(c.sum()),
            'alternative_half_multiply_covered': int((c > 1).sum())}


def scan(folder, root, index, segment_index):
    q = audit_record(folder, root, index, .05, 0)
    assert q['split_index_matches'] == 1 and not q['metadata_mismatches']
    if q['dataset'] == 'EgoDemo':
        poses, times, spans, alternatives, camera, states, one_based = demo_data(folder)
        t = q['time']['trajectory']
    else:
        poses, times, spans, alternatives, camera, states, one_based = csv_data(folder)
        t = q['time']['right_hand']
        assert all(v['order_differs_from_right'] == 0 for v in q['time'].values())
    assert t['rows'] == len(times) and t['invalid_ts_rows'] == t['reverse'] == t['duplicate_ts_extra'] == 0
    assert all(np.count_nonzero(states == s) == n for s, n in q['quality'].items())
    units = sorted(segment_index[q['episode_uuid']], key=lambda u: int(u['source_segment_index']))
    assert len(units) == len(spans)
    for i, (u, (a, b, text)) in enumerate(zip(units, spans)):
        assert (int(u['source_segment_index']), Fraction(u['start_s']), Fraction(u['end_s']), u['description']) == (i, a, b, text)
        assert a >= 0 and b > a and (b-a >= Fraction('1.2')) == (u['eligible'] == '1')
        assert u['sample'] == folder.name and all(u[k] == q[k] for k in ('dataset', 'split', 'environment_id'))
    record = {k: q[k] for k in ('dataset', 'split', 'environment_id', 'scene_id', 'operator_id', 'episode_uuid')}
    record['durations'] = [(u['family_id'], b-a, u['eligible'] == '1') for u, (a, b, _) in zip(units, spans)]
    record['pose'] = {k: pose_metrics(*v) for k, v in poses.items()}
    for label, a, b in [('head_to_left', 'head', 'headcam'), ('left_to_right', 'headcam', 'right_eye_cam')]:
        dist = np.linalg.norm(poses[a][0]-poses[b][0], axis=-1)
        camera[label + '_distance_min'] = float(np.nanmin(dist))
        camera[label + '_distance_max'] = float(np.nanmax(dist))
        camera[label + '_within_record_range_max'] = float(np.nanmax(dist)-np.nanmin(dist))
    record['camera'] = camera
    quality = {'ticks': len(times), 'adjacent_pairs': t['pairs'], 'gt_50ms': t['gap'], 'dt_s_min': t['dt_min_s'],
               'dt_s_max': t['dt_max_s'], 'both_bad': q['quality_diagnostics']['both_hands_bad_frames']}
    for label, d in [('joint', q['quality']), ('left', q['hand']['left']['quality']), ('right', q['hand']['right']['quality'])]:
        quality.update({label + '_' + state: v for state, v in d.items()})
    for side in ('left', 'right'):
        h = q['hand'][side]
        quality[side + '_bad_with_complete_xyz'] = h['side_bad_numeric_complete']
        for reason in ('occlusion', 'near edge'):
            quality[side + '_' + reason.replace(' ', '_')] = q['reasons'].get(side + ':' + reason, 0)
    assert sum(q['reasons'].values()) == sum(quality[k] for k in quality if k.endswith(('_occlusion', '_near_edge')))
    record['quality'] = quality
    record['coverage'], record['clocks'] = {}, {}
    for pool in ('all', 'eligible'):
        selected = [i for i, u in enumerate(units) if pool == 'all' or u['eligible'] == '1']
        metrics, slices = coverage(times, [spans[i] for i in selected])
        m = multiplicity(len(times), slices)
        bad = states == 'B'
        metrics.update(half_unique_bad=int(np.count_nonzero((m > 0) & bad)),
                       half_unique_normal=int(np.count_nonzero((m > 0) & (states == 'N'))),
                       half_unique_unlisted=int(np.count_nonzero((m > 0) & (states == 'U'))),
                       half_segment_sum_bad=int(m[bad].sum()), half_uncovered_bad=int(np.count_nonzero((m == 0) & bad)),
                       half_segments_with_bad=sum(bool(bad[a:b].any()) for a, b in slices),
                       demo_one_based_unique_bad=int(np.count_nonzero((m > 0) & one_based)) if one_based is not None else None,
                       demo_one_based_segments_with_bad=sum(bool(one_based[a:b].any()) for a, b in slices) if one_based is not None else None)
        record['coverage'][pool] = metrics
        if pool == 'all':
            for basis, (alt_time, alt_spans) in alternatives.items():
                alt = memberships(alt_time, alt_spans)
                record['clocks'][basis] = clock_difference(len(times), slices, alt)
    return record


def tables(records):
    out = defaultdict(list)
    for dataset, part, rr in groups(records, split=True, cross=True):
        label = {'dataset': dataset, 'split': part}
        out['quality'].append({**label, 'records': len(rr), 'environments': len({r['environment_id'] for r in rr}),
                               'scenes': len({r['scene_id'] for r in rr}), 'operators': len({r['operator_id'] for r in rr}),
                               **combine(r['quality'] for r in rr)})
        if dataset != 'all' and part != 'all':
            continue
        for pool in ('all', 'eligible'):
            metrics = combine(r['coverage'][pool] for r in rr)
            out['coverage'].append({**label, 'pool': pool, **{k: float(v) if isinstance(v, Fraction) else v for k,v in metrics.items()}})
        for family in ('all', 'P', 'A', 'L'):
            samples = [(d, eligible) for r in rr for f, d, eligible in r['durations'] if family == 'all' or family == f]
            d = [x for x, _ in samples]
            out['duration'].append({**label, 'family': family, 'segments': len(d), 'eligible': sum(b for _, b in samples),
                                    'sum_s': float(sum(d)), 'min_s': float(min(d)) if d else None,
                                    'median_s': float(median(d)) if d else None, 'max_s': float(max(d)) if d else None})
        if part != 'all':
            continue
        for stream in POSES:
            out['poses'].append({'dataset': dataset, 'stream': stream, **combine(r['pose'][stream] for r in rr)})
        if dataset != 'all':
            out['camera'].append({'dataset': dataset, 'records': len(rr), **combine(r['camera'] for r in rr)})
            for basis in rr[0]['clocks']:
                out['clock'].append({'dataset': dataset, 'alternative_clock': basis, **combine(r['clocks'][basis] for r in rr)})
    # Some calibration/clock diagnostics occur in only one file format.
    columns = dict.fromkeys(k for r in out['camera'] for k in r)
    out['camera'] = [{**columns, **r} for r in out['camera']]
    for axis, labels in [('split', ('train', 'validation', 'test')), ('dataset', DATASETS)]:
        subsets = {k: [r for r in records if r[axis] == k] for k in labels}
        for a, b in combinations(labels, 2):
            intersections = {k: len({r[k] for r in subsets[a]} & {r[k] for r in subsets[b]})
                             for k in ('episode_uuid', 'environment_id', 'scene_id', 'operator_id')}
            out['partition'].append({'axis': axis, 'group_a': a, 'group_b': b,
                                     **{'shared_' + k + '_count': v for k,v in intersections.items()}})
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--out', type=Path, default=REPO / 'runs/c_full_records/aggregates')
    args = parser.parse_args()
    root = args.root.resolve()
    rules = root / 'Restocking_Tasks_and_Constraints'
    index, units = defaultdict(list), defaultdict(list)
    for r in rows(rules / 'record_split.csv'):
        index[r['episode_uuid']].append(r)
    for r in rows(rules / 'source_segment_index.csv'):
        units[r['episode_uuid']].append(r)
    records = [scan(p, root, index, units) for d in DATASETS for p in sorted((root / 'Raw_Data' / d).iterdir()) if p.is_dir()]
    assert len(index) == len(records) == len({r['episode_uuid'] for r in records})
    assert index.keys() == units.keys() == {r['episode_uuid'] for r in records}
    output = tables(records)
    args.out.mkdir(parents=True, exist_ok=True)
    for key, data in output.items():
        write_csv(args.out / f'record_{key}.csv', data)
    print(f"{len(records)} records, {sum(r['quality']['ticks'] for r in records)} ticks, "
          f"{sum(len(r['durations']) for r in records)} intervals")


if __name__ == '__main__':
    main()
