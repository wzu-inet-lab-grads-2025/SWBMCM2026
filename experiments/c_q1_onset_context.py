"""Fixed training-only, same-record/hand matched description of occlusion onsets.

AI-assisted implementation and tabulation in OpenCode, 2026-11-20,
Sonus / Qoder / Remote magpie (magpie, remote-magpie/qoder/smodel).
No model fit, video, held-out record content, or row-level output.
Specification: research/C题坏段起点同记录训练核对.md, fixed before results.
"""

import argparse
from collections import Counter
import os
from pathlib import Path
import sys

import numpy as np
import pandas as pd

if __package__:
    from .c_palm_motion import palm_points
    from .c_palm_scale import SECOND, SIDES, history_scale, lengths
    from .c_q2_windows import wrist_inputs
    from .c_records import ROOT, official_records, raw_metadata, rows
else:
    from c_palm_motion import palm_points
    from c_palm_scale import SECOND, SIDES, history_scale, lengths
    from c_q2_windows import wrist_inputs
    from c_records import ROOT, official_records, raw_metadata, rows


# Frozen, not CLI-tunable: this is one descriptive design, not a parameter search.
HISTORY_NS = 200_000_000
GAP_NS = 50_000_000
MIN_LAG_NS = SECOND
MAX_LAG_NS = 5 * SECOND
MATCH_TOL_NS = 1_000_000
METRICS = ('wrist_speed_L_s', 'palm_rate_deg_s')
HISTORY_EXCLUSIONS = ('short', 'gap', 'not_normal', 'wrist', 'palm')
COUNT_FIELDS = (
    'hand_ticks', 'joint_bad_ticks', 'side_bad_ticks', 'side_unresolved_ticks',
    'occlusion_ticks', 'occlusion_runs', 'boundary_unknown',
    'preceded_by_same_hand_other_bad', 'preceded_by_unresolved_side',
    'observed_starts', 'starts_with_same_hand_other_reason', 'starts_with_both_occlusion',
    *('exclude_history_' + key for key in HISTORY_EXCLUSIONS),
    'history_eligible_starts', 'normal_targets', 'normal_history_eligible',
    *('exclude_normal_history_' + key for key in HISTORY_EXCLUSIONS),
    'no_pre_control', 'no_post_control', 'no_either_control', 'matched_starts',
)


def training_records(root):
    """Reuse index validation, but filter before opening *any* raw metadata.

    split_records() opens metadata before yielding a record, including Demo.
    Here only the official split/location indices contain nontrain membership.
    """
    train = [r for r in official_records(root) if r['split'] == 'train']
    wanted = {r['episode_uuid'] for r in train if r['dataset'] in ('EgoPro', 'EgoStandard')}
    locations = {}
    for item in rows(root / 'Restocking_Tasks_and_Constraints/source_segment_index.csv'):
        if item['episode_uuid'] not in wanted:
            continue
        if item['split'] != 'train':
            raise ValueError('Training source index has a conflicting split')
        location = root / 'Raw_Data' / item['dataset'] / item['sample']
        if locations.setdefault(item['episode_uuid'], location) != location:
            raise ValueError('Conflicting training locations')
    for ordinal, item in enumerate(train, 1):
        if item['episode_uuid'] not in wanted:
            continue
        folder = locations[item['episode_uuid']]
        meta = raw_metadata(folder)
        if folder.parent.name != item['dataset'] or any(
                meta[key] != item[key] for key in ('episode_uuid', 'environment_id', 'scene_id', 'operator_id')):
            raise ValueError('Training metadata and official membership disagree')
        yield f'T{ordinal:02}', folder, meta


def install_read_guard(root):
    """Audit allowlist: only train Pro/Standard tables plus official routing indices.

    Optional runtime verification, not a change to the analysis. open() events
    under the raw root are counted and all writes there are forbidden.
    """
    root = root.resolve()
    rules = root / 'Restocking_Tasks_and_Constraints'
    selected = {r['episode_uuid'] for r in official_records(root)
                if r['split'] == 'train' and r['dataset'] in ('EgoPro', 'EgoStandard')}
    folders = {(r['dataset'], r['sample']) for r in rows(rules / 'source_segment_index.csv')
               if r['split'] == 'train' and r['episode_uuid'] in selected}
    allowed = {'session_metadata.csv', 'tables/annotation_bad_frame_pose_hand.csv',
               'tables/pose_left_hand.csv', 'tables/pose_right_hand.csv'}
    opened, touched = Counter(), set()

    def audit(event, args):
        if event != 'open' or not isinstance(args[0], (str, bytes)):
            return
        path = Path(os.fsdecode(args[0])).resolve()
        if not path.is_relative_to(root):
            return
        mode, flags = args[1:3]
        if (mode and any(v in mode for v in ('w', 'a', '+'))) or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT):
            raise PermissionError('Writes to raw data are forbidden')
        rel = path.relative_to(root)
        if rel.parts[0] == 'Restocking_Tasks_and_Constraints':
            if rel.name not in {'record_split.csv', 'source_segment_index.csv'}:
                raise PermissionError('Unexpected index read')
        elif (len(rel.parts) >= 4 and rel.parts[0] == 'Raw_Data'
              and tuple(rel.parts[1:3]) in folders and '/'.join(rel.parts[3:]) in allowed):
            opened[rel.parts[1]] += 1
            touched.add(rel)
        else:
            raise PermissionError('Read outside training Pro/Standard allowlist')

    sys.addaudithook(audit)
    return opened, touched


def reason_arrays(samples, times, normal):
    """Other-hand-only bad is unresolved for this hand, never explicit normal."""
    if len(samples) != len(times):
        raise ValueError('Quality reason row count differs')
    bad = {s: np.zeros(len(times), dtype=bool) for s in SIDES}
    occ = {s: np.zeros(len(times), dtype=bool) for s in SIDES}
    other = {s: np.zeros(len(times), dtype=bool) for s in SIDES}
    for i, row in enumerate(samples):
        token = row['is_bad'].strip().lower()
        if int(row['header.ts']) != times[i] or token not in ('true', 'false', '0', '1'):
            raise ValueError('Reason clock or explicit flag differs')
        if normal[i] != (token in ('false', '0')):
            raise ValueError('Reason and wrist quality disagree')
        count = int(row['problem_type.length'])
        types = [v.strip() for k, v in row.items() if k.startswith('problem_type[') and v.strip()]
        if count != len(types) or count < 0 or any(not v for v in types):
            raise ValueError('Malformed problem_type length')
        if bool(types) == bool(normal[i]) or any(not v.startswith(('left:', 'right:')) for v in types):
            raise ValueError('Contradictory or unlocalized explicit reasons')
        for side in SIDES:
            own = [v for v in types if v.startswith(side + ':')]
            bad[side][i] = bool(own)
            occ[side][i] = side + ':occlusion' in own
            other[side][i] = any(v != side + ':occlusion' for v in own)
    return bad, occ, other


def onset_indices(times, normal, side_bad, occlusion):
    counts = Counter(hand_ticks=len(times), joint_bad_ticks=int((~normal).sum()),
                     side_bad_ticks=int(side_bad.sum()),
                     side_unresolved_ticks=int((~normal & ~side_bad).sum()),
                     occlusion_ticks=int(occlusion.sum()), normal_targets=int(normal.sum()))
    starts = []
    for i in np.flatnonzero(occlusion):
        adjacent = i > 0 and 0 < times[i] - times[i-1] <= GAP_NS
        if adjacent and occlusion[i-1]:
            continue
        counts['occlusion_runs'] += 1
        if not adjacent:
            counts['boundary_unknown'] += 1
        elif normal[i-1]:
            starts.append(int(i))
            counts['observed_starts'] += 1
        elif side_bad[i-1]:
            counts['preceded_by_same_hand_other_bad'] += 1
        else:
            counts['preceded_by_unresolved_side'] += 1
    assert counts['occlusion_runs'] == sum(counts[k] for k in (
        'boundary_unknown', 'observed_starts', 'preceded_by_same_hand_other_bad', 'preceded_by_unresolved_side'))
    return starts, counts


def history_features(times, normal, both_wrists, palm, j):
    """Read only [start,j); target j and later coordinates/quality are not used."""
    anchor = j - 1
    if anchor < 0:
        return None, 'short'
    start = int(times.searchsorted(times[anchor] - HISTORY_NS, side='right')) - 1
    if start < 0:
        return None, 'short'
    clock = times[start:j+1]
    if np.any(np.diff(clock) <= 0) or np.any(np.diff(clock) > GAP_NS):
        return None, 'gap'
    if not normal[start:j].all():
        return None, 'not_normal'
    if not both_wrists[start:j].all():
        return None, 'wrist'
    points = palm[start:j]
    if not np.isfinite(points).all() or (points == 0).all(axis=2).any():
        return None, 'palm'
    scale = history_scale(lengths(points[:, 0], points[:, 2]))
    index, little = points[:, 1] - points[:, 0], points[:, 4] - points[:, 0]
    cross = np.cross(index, little)
    area = np.linalg.norm(cross, axis=1)
    product = np.linalg.norm(index, axis=1) * np.linalg.norm(little, axis=1)
    if scale is None or np.any(product <= 0) or np.any(area < .1 * product):
        return None, 'palm'
    normals = cross / area[:, None]
    angles = np.rad2deg(np.arctan2(np.linalg.norm(np.cross(normals[:-1], normals[1:]), axis=1),
                                 np.sum(normals[:-1] * normals[1:], axis=1)))
    span_ns = int(times[anchor] - times[start])
    duration = span_ns / SECOND
    path = np.linalg.norm(np.diff(points[:, 0], axis=0), axis=1).sum()
    return dict(index=j, start=start, points=j-start, span_ns=span_ns,
                age_ns=int(times[j] - times[anchor]),
                quartile=min(3, int(4 * (times[j] - times[0]) // (times[-1] - times[0]))),
                wrist_speed_L_s=float(path / duration / scale),
                palm_rate_deg_s=float(angles.sum() / duration)), None


def match_controls(times, case, controls):
    """No metric values enter matching; sorted inputs give deterministic ties."""
    eligible = [c for c in controls if c['quartile'] == case['quartile']
                and c['points'] == case['points']
                and abs(c['span_ns'] - case['span_ns']) <= MATCH_TOL_NS
                and abs(c['age_ns'] - case['age_ns']) <= MATCH_TOL_NS]
    before, after = [], []
    for control in eligible:
        lag = int(times[control['index']] - times[case['index']])
        if MIN_LAG_NS <= abs(lag) <= MAX_LAG_NS:
            (before if lag < 0 else after).append((abs(lag), control['index'], control))
    return (min(before, key=lambda x: x[:2])[2] if before else None,
            min(after, key=lambda x: x[:2])[2] if after else None)


def inspect_record(record, folder, meta):
    times, normal, _, masks = wrist_inputs(folder)
    side_bad, occlusion, other = reason_arrays(
        rows(folder / 'tables/annotation_bad_frame_pose_hand.csv'), times, normal)
    units = []
    for side in SIDES:
        palm = palm_points(folder, side, times)
        starts, counts = onset_indices(times, normal, side_bad[side], occlusion[side])
        counts['starts_with_same_hand_other_reason'] = int(other[side][starts].sum())
        counts['starts_with_both_occlusion'] = sum(occlusion['left'][j] and occlusion['right'][j] for j in starts)
        histories, excluded = {}, {}
        for j in sorted(set(starts) | set(np.flatnonzero(normal).tolist())):
            feature, reason = history_features(times, normal, masks['both'], palm, j)
            if feature is not None:
                histories[j] = feature
            else:
                excluded[j] = reason
        controls = [h for j, h in histories.items() if normal[j]]
        counts['normal_history_eligible'] = len(controls)
        for j, reason in excluded.items():
            if normal[j]:
                counts['exclude_normal_history_' + reason] += 1
        assert counts['normal_targets'] == counts['normal_history_eligible'] + sum(
            counts['exclude_normal_history_' + k] for k in HISTORY_EXCLUSIONS)
        matches = []
        for j in starts:
            if j in excluded:
                counts['exclude_history_' + excluded[j]] += 1
                continue
            counts['history_eligible_starts'] += 1
            case = histories[j]
            before, after = match_controls(times, case, controls)
            if before is None or after is None:
                counts['no_either_control' if before is None and after is None else
                       'no_pre_control' if before is None else 'no_post_control'] += 1
                continue
            counts['matched_starts'] += 1
            # The 1 s separation must also prevent overlap of the actual histories.
            assert before['index'] - 1 < case['start'] and j - 1 < after['start']
            matches.append(dict(record=record, environment=meta['environment_id'], side=side,
                                case=case, before=before, after=after,
                                pre_lag_s=(times[j] - times[before['index']]) / SECOND,
                                post_lag_s=(times[after['index']] - times[j]) / SECOND))
        assert counts['observed_starts'] == counts['history_eligible_starts'] + sum(
            counts['exclude_history_' + k] for k in HISTORY_EXCLUSIONS)
        assert counts['history_eligible_starts'] == sum(counts[k] for k in (
            'matched_starts', 'no_either_control', 'no_pre_control', 'no_post_control'))
        units.append(dict(record=record, environment=meta['environment_id'], dataset=folder.parent.name,
                          side=side, counts=counts, starts=starts, matches=matches))
    return units


def metric_summary(matches, key):
    if not matches:
        return {}
    case = np.array([m['case'][key] for m in matches])
    before = np.array([m['before'][key] for m in matches])
    after = np.array([m['after'][key] for m in matches])
    control = (before + after) / 2
    diff = case - control
    table = pd.DataFrame(dict(record=[m['record'] for m in matches],
                              environment=[m['environment'] for m in matches], diff=diff))
    record_means = table.groupby(['environment', 'record'])['diff'].mean()
    env_means = record_means.groupby(level='environment').mean()
    loo = [(env_means.sum() - value) / (len(env_means)-1) for value in env_means] if len(env_means) > 1 else []
    return {key + '_' + k: v for k, v in dict(
        case_mean=float(case.mean()), control_mean=float(control.mean()),
        diff_mean=float(diff.mean()), diff_median=float(np.median(diff)),
        positive=int((diff > 0).sum()), zero=int((diff == 0).sum()),
        pre_diff_mean=float((case-before).mean()), post_diff_mean=float((case-after).mean()),
        macro_diff=float(env_means.mean()), positive_records=int((record_means > 0).sum()),
        positive_environments=int((env_means > 0).sum()),
        loo_macro_min=min(loo) if loo else np.nan, loo_macro_max=max(loo) if loo else np.nan,
    ).items()}


def aggregate(units, scope, name, side):
    counts = sum((u['counts'] for u in units), Counter())
    matches = [m for u in units for m in u['matches']]
    with_starts = [u for u in units if u['starts']]
    with_history = [u for u in units if u['counts']['history_eligible_starts']]
    with_matches = [u for u in units if u['matches']]
    uses = Counter((m['record'], m['side'], m[k]['index']) for m in matches for k in ('before', 'after'))
    row = dict(scope=scope, name=name, side=side, records=len({u['record'] for u in units}),
               environments=len({u['environment'] for u in units}),
               start_records=len({u['record'] for u in with_starts}),
               start_environments=len({u['environment'] for u in with_starts}),
               history_records=len({u['record'] for u in with_history}),
               history_environments=len({u['environment'] for u in with_history}),
               matched_records=len({u['record'] for u in with_matches}),
               matched_environments=len({u['environment'] for u in with_matches}),
               starts_unique_clock=len({(u['record'], j) for u in units for j in u['starts']}),
               matched_unique_clock=len({(m['record'], m['case']['index']) for m in matches}),
               **{key: int(counts[key]) for key in COUNT_FIELDS},
               control_assignments=sum(uses.values()), unique_controls=len(uses),
               reused_controls=sum(v > 1 for v in uses.values()),
               extra_control_uses=sum(v-1 for v in uses.values()), max_control_uses=max(uses.values(), default=0))
    if scope == 'record':
        row.update(dataset=units[0]['dataset'], environment=units[0]['environment'])
    if matches:
        for field in ('pre_lag_s', 'post_lag_s'):
            row[field + '_median'] = float(np.median([m[field] for m in matches]))
        for field, factor, label in [('span_ns', 1e-6, 'history_ms'), ('age_ns', 1e-6, 'lead_ms'),
                                     ('points', 1, 'history_points')]:
            values = [m['case'][field] * factor for m in matches]
            row[label + '_min'], row[label + '_max'] = min(values), max(values)
    for key in METRICS:
        row.update(metric_summary(matches, key))
    return row


def summarize(units):
    for side in ('both', *SIDES):
        hands = [u for u in units if side == 'both' or u['side'] == side]
        yield aggregate(hands, 'all', 'explicit_train', side)
        for field in ('dataset', 'environment', 'record'):
            for name in sorted({u[field] for u in hands}):
                yield aggregate([u for u in hands if u[field] == name], field, name, side)


def self_test():
    """Synthetic cases only; also exercise future isolation and exact boundaries."""
    t = np.arange(401, dtype=np.int64) * 40_000_000 + 1_700_000_000_000_000_000
    normal = np.ones(len(t), dtype=bool)
    normal[[0, 2, 3, 4, 7, 8]] = False
    bad = np.zeros(len(t), dtype=bool)
    bad[[0, 3, 4, 7, 8]] = True
    occ = np.zeros(len(t), dtype=bool)
    occ[[0, 3, 4, 8]] = True
    starts, counts = onset_indices(t, normal, bad, occ)
    assert starts == [] and counts['boundary_unknown'] == 1
    assert counts['preceded_by_unresolved_side'] == counts['preceded_by_same_hand_other_bad'] == 1
    normal[2] = True
    assert onset_indices(t, normal, bad, occ)[0] == [3]
    gapped = t.copy()
    gapped[3:] += 10_000_001
    assert onset_indices(gapped, normal, bad, occ)[1]['boundary_unknown'] == 2
    at_limit = t.copy()
    at_limit[3:] += 10_000_000
    assert onset_indices(at_limit, normal, bad, occ)[0] == [3]

    base = np.array([[0., 0., 0.], [1., 0., 0.], [.7, .3, 0.], [.3, .7, 0.], [0., 1., 0.]])
    palm = base[None] + np.array([4., 5., 6.]) + np.arange(len(t))[:, None, None] * [0.01, 0, 0]
    normal[:] = True
    usable = normal.copy()
    feature, reason = history_features(t, normal, usable, palm, 200)
    assert reason is None and feature['points'] == 6 and feature['span_ns'] == HISTORY_NS
    assert np.isclose(feature['wrist_speed_L_s'], .25 / np.sqrt(.58))
    assert abs(feature['palm_rate_deg_s']) < 1e-9
    altered = palm.copy()
    altered[200:] = np.nan
    altered_normal, altered_usable = normal.copy(), usable.copy()
    altered_normal[200:], altered_usable[200:] = False, False
    assert feature == history_features(t, altered_normal, altered_usable, altered, 200)[0]
    altered[198, 0] = 0
    assert history_features(t, normal, usable, altered, 200)[1] == 'palm'
    normal[197] = False
    assert history_features(t, normal, usable, palm, 200)[1] == 'not_normal'
    normal[:] = True
    rotating = np.empty_like(palm)
    for i in range(len(t)):
        angle = np.deg2rad(i * 2.)
        rot = np.array([[1., 0., 0.], [0., np.cos(angle), -np.sin(angle)], [0., np.sin(angle), np.cos(angle)]])
        rotating[i] = base @ rot.T + [4., 5., 6.]
    turned, _ = history_features(t, normal, usable, rotating, 200)
    assert turned['wrist_speed_L_s'] == 0 and np.isclose(turned['palm_rate_deg_s'], 50.)
    rotating[:, 4] = rotating[:, 1]
    assert history_features(t, normal, usable, rotating, 200)[1] == 'palm'

    # Synthetic match signatures are separate from geometry to test calipers.
    case = dict(feature, index=200, quartile=2)
    controls = [dict(case, index=i, wrist_speed_L_s=1e6*i) for i in (74, 75, 175, 225, 325, 326)]
    before, after = match_controls(t, case, controls)
    assert before['index'] == 175 and after['index'] == 225  # closed 1 s bounds
    assert match_controls(t, case, [controls[1], controls[4]])[0]['index'] == 75  # closed 5 s
    assert match_controls(t, case, [controls[0], controls[5]]) == (None, None)
    assert match_controls(t, case, [dict(controls[2], quartile=1)]) == (None, None)
    assert match_controls(t, case, [dict(controls[2], points=7)]) == (None, None)
    assert match_controls(t, case, [dict(controls[2], span_ns=HISTORY_NS+MATCH_TOL_NS+1)]) == (None, None)
    assert match_controls(t, case, [dict(controls[2], age_ns=case['age_ns']+MATCH_TOL_NS)])[0] is not None
    assert match_controls(t, case, [dict(controls[2], age_ns=case['age_ns']+MATCH_TOL_NS+1)]) == (None, None)

    samples = [{'header.ts': str(t[i]), 'is_bad': flag, 'problem_type.length': str(len(types)),
                **{f'problem_type[{j}]': value for j, value in enumerate(types)}}
               for i, (flag, types) in enumerate([
                   ('false', []), ('true', ['left:occlusion']),
                   ('true', ['right:other', 'right:occlusion'])])]
    b, o, mixed = reason_arrays(samples, t[:3], np.array([True, False, False]))
    assert b['left'].tolist() == o['left'].tolist() == [False, True, False]
    assert b['right'].tolist() == mixed['right'].tolist() == [False, False, True]
    invalid = [dict(samples[0], **{'problem_type.length': '1', 'problem_type[0]': 'left:occlusion'})]
    try:
        reason_arrays(invalid, t[:1], np.array([True]))
    except ValueError:
        pass
    else:
        raise AssertionError('Normal with bad reason was accepted')

    # Many events in one record must not turn into many independent environments.
    examples = [dict(record=record, environment=env, case={'x': value},
                     before={'x': 0.}, after={'x': 0.})
                for record, env, value in [('a', 'one', 2.), ('a', 'one', 4.),
                                         ('b', 'one', 9.), ('c', 'two', -2.)]]
    stats = metric_summary(examples, 'x')
    assert stats['x_macro_diff'] == 2. and stats['x_diff_mean'] == 3.25
    assert stats['x_loo_macro_min'] == -2. and stats['x_loo_macro_max'] == 6.
    print('passed: onset/reason states; history-only geometry; matching/calipers; nested aggregation')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--out', type=Path, default=Path('runs/q1_onset_context.csv'))
    parser.add_argument('--self-test', action='store_true', help='Synthetic checks only; no data opened')
    parser.add_argument('--guard-reads', action='store_true', help='Check a train-only file-open allowlist')
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    guard = install_read_guard(args.root) if args.guard_reads else None
    units = [unit for rec, folder, meta in training_records(args.root)
             for unit in inspect_record(rec, folder, meta)]
    if not units:
        raise ValueError('No explicit training records')
    output = pd.DataFrame(summarize(units))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(args.out, index=False, float_format='%.12g')
    columns = ['side', 'records', 'environments', 'occlusion_runs', 'observed_starts',
               'history_eligible_starts', 'matched_starts', 'matched_records', 'matched_environments',
               *[k + '_macro_diff' for k in METRICS if k + '_macro_diff' in output]]
    print(output.loc[output.scope.eq('all'), columns].to_string(index=False))
    if guard is not None:
        opened, touched = guard
        print('read_guard:', dict(opened), 'unique_raw_files:', len(touched))


if __name__ == '__main__':
    main()
