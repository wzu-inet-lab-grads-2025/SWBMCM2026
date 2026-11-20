"""Fixed training-only paired-wrist velocity probe; not a model-selection entry.

Specification was written before the first training run in
research/C题常速度训练诊断.md. No validation/test or tunable-model arguments.
AI-assisted implementation/checking, 2026-11-20: OpenCode, Sonus / Qoder /
Remote magpie (provider magpie, model remote-magpie/qoder/smodel; release date
unavailable). Team-specified candidates only; human competition review required.
The imported shared scripts retain their own AI-assistance notices.
"""

import argparse
from collections import defaultdict
import csv
import hashlib
from pathlib import Path
import sys

sys.dont_write_bytecode = True
import numpy as np

if __package__:
    from .c_q2_windows import ROOT, SIDES, qualified_runs, split_records, windows, wrist_inputs
else:
    from c_q2_windows import ROOT, SIDES, qualified_runs, split_records, windows, wrist_inputs


SECOND = 1_000_000_000
HISTORY_NS, MASK_NS, RECENT_NS = SECOND, 2 * SECOND, SECOND // 2
BINS = ('0-0.5', '0.5-1', '1-1.5', '1.5-end')
EDGES_NS = np.array([SECOND // 2, SECOND, 3 * SECOND // 2], dtype=np.int64)
OUT = Path(__file__).resolve().parents[1] / 'runs/q2_diagnostic/train_velocity_probe.csv'
AUDIT_SUMS = ('indexed_records', 'pool_windows', 'excluded_windows', 'insufficient_history_windows')
FIELDS = (
    'split', 'scope', 'age_bin', 'hand', 'environment', 'record', 'dataset',
    'indexed_records', 'indexed_environments', 'records', 'environments',
    'pool_windows', 'paired_windows', 'excluded_windows', 'insufficient_history_windows',
    'recent_points_min', 'recent_points_max', 'timestamps', 'min_age_s', 'max_age_s',
    'hold_mean', 'velocity_mean', 'delta_mean',
    'environments_improved', 'environments_worsened', 'environments_tied',
)


def age_bins(age_ns):
    """Right endpoints included, identical to the shared hold-last diagnostic."""
    return np.asarray(BINS)[np.searchsorted(EDGES_NS, age_ns, side='left')]


def recent_fit(history_times, history_xyz, qualified):
    """Receive history only. OLS slope has an intercept; anchor is the observed last point."""
    indices = np.flatnonzero(qualified)
    if not len(indices):
        return None, None, None, 0
    last = indices[-1]
    recent = indices[history_times[indices] >= history_times[last] - RECENT_NS]
    last_ns, last_xyz = int(history_times[last]), history_xyz[last].copy()
    if len(recent) < 2:
        return last_ns, last_xyz, None, len(recent)
    # Subtract integer epoch nanoseconds BEFORE conversion to relative float seconds.
    relative_s = (history_times[recent] - history_times[last]) / SECOND
    centered_s = relative_s - relative_s.mean()
    xyz = history_xyz[recent]
    denominator = centered_s @ centered_s
    if denominator <= 0:
        raise ValueError('Recent qualified timestamps must be distinct')
    slope = (centered_s[:, None] * (xyz - xyz.mean(axis=0))).sum(axis=0) / denominator
    return last_ns, last_xyz, slope, len(recent)


def predict_pair(history_times, history_points, history_masks, future_times):
    """No future coordinates/quality enter the predictor; no fallback for <2 points."""
    hold, velocity, counts = [], [], {}
    for side in SIDES:
        last_ns, anchor, slope, count = recent_fit(
            history_times, history_points[side], history_masks[side])
        counts[side] = count
        if count == 0:
            hold.append(None)
            velocity.append(None)
            continue
        if np.any(future_times <= last_ns):
            raise ValueError('Prediction times must follow the observed history')
        age_s = (future_times - last_ns) / SECOND
        hold.append(np.broadcast_to(anchor, (len(future_times), 3)).copy())
        velocity.append(None if slope is None else anchor + age_s[:, None] * slope)
    if any(value is None for value in velocity):
        return None, None, counts
    return np.stack(hold, axis=1), np.stack(velocity, axis=1), counts


def record_rows(record, dataset, environment, ages, window_ids, errors, pool, insufficient, counts):
    """Point-weighted within each record/bin; zero-window records kept in all rows."""
    labels = age_bins(ages)
    for label in (*BINS, 'all'):
        selected = np.ones(len(ages), dtype=bool) if label == 'all' else labels == label
        n = int(selected.sum())
        if not n and label != 'all':
            continue
        for side in SIDES:
            hold, velocity = (float(errors[side][selected, i].mean()) if n else None for i in (0, 1))
            paired_windows = len(np.unique(window_ids[selected]))
            yield dict(
                split='train', scope='record', age_bin=label, hand=side,
                environment=environment, record=record, dataset=dataset,
                indexed_records=1 if label == 'all' else None,
                indexed_environments=1 if label == 'all' else None,
                records=int(n > 0), environments=int(n > 0),
                pool_windows=pool if label == 'all' else None,
                paired_windows=paired_windows,
                excluded_windows=pool - paired_windows if label == 'all' else None,
                insufficient_history_windows=insufficient[side] if label == 'all' else None,
                recent_points_min=min(counts[side], default=None) if label == 'all' else None,
                recent_points_max=max(counts[side], default=None) if label == 'all' else None,
                timestamps=n, min_age_s=float(ages[selected].min() / SECOND) if n else None,
                max_age_s=float(ages[selected].max() / SECOND) if n else None,
                hold_mean=hold, velocity_mean=velocity,
                delta_mean=velocity - hold if n else None,
                environments_improved=None, environments_worsened=None, environments_tied=None,
            )


def evaluate(root):
    """The only real-data entry is the official train split; no saved results are read."""
    rows = []
    for record, folder, meta in split_records(root, 'train'):
        dataset, env = folder.parent.name, meta['environment_id']
        age_parts, id_parts = [], []
        error_parts = {side: [] for side in SIDES}
        counts = {side: [] for side in SIDES}
        insufficient = dict.fromkeys(SIDES, 0)
        pool = 0
        if dataset != 'EgoDemo':
            times, _, points, masks = wrist_inputs(folder)
            pairs = np.stack([points[side] for side in SIDES], axis=1)
            for run in qualified_runs(times, masks['both']):
                for start, first, end in windows(times, run, HISTORY_NS, MASK_NS):
                    pool += 1
                    clock = times[first:end]
                    hold, velocity, sizes = predict_pair(
                        times[start:first], {s: points[s][start:first] for s in SIDES},
                        {s: masks[s][start:first] for s in SIDES}, clock)
                    for side in SIDES:
                        counts[side].append(sizes[side])
                        insufficient[side] += int(sizes[side] < 2)
                    if velocity is None:
                        continue  # Exclude the same whole paired window from BOTH methods/hands.
                    age_parts.append(clock - times[first - 1])
                    id_parts.append(np.full(len(clock), pool, dtype=int))
                    hold_error = np.linalg.norm(hold - pairs[first:end], axis=2)
                    velocity_error = np.linalg.norm(velocity - pairs[first:end], axis=2)
                    for i, side in enumerate(SIDES):
                        error_parts[side].append(np.column_stack((hold_error[:, i], velocity_error[:, i])))
        ages = np.concatenate(age_parts) if age_parts else np.empty(0, dtype=np.int64)
        ids = np.concatenate(id_parts) if id_parts else np.empty(0, dtype=int)
        errors = {s: np.concatenate(error_parts[s]) if error_parts[s] else np.empty((0, 2)) for s in SIDES}
        rows.extend(record_rows(record, dataset, env, ages, ids, errors, pool, insufficient, counts))
    if not rows:
        raise ValueError('No training records')
    return rows


def combine(members, scope):
    """Record means -> equal records in environment -> equal eligible environments."""
    first = members[0]
    scored = [r for r in members if r['timestamps']]
    row = dict.fromkeys(FIELDS)
    row.update(split='train', scope=scope, age_bin=first['age_bin'], hand=first['hand'],
               environment=first['environment'] if scope == 'environment' else '', record='', dataset='')
    for key in ('records', 'paired_windows', 'timestamps'):
        row[key] = sum(r[key] for r in members)
    row['environments'] = len(scored) if scope == 'macro' else int(bool(scored))
    if first['age_bin'] == 'all':
        for key in AUDIT_SUMS:
            row[key] = sum(r[key] for r in members)
        row['indexed_environments'] = len(members) if scope == 'macro' else 1
    for key, operation in (('min_age_s', min), ('max_age_s', max),
                           ('recent_points_min', min), ('recent_points_max', max)):
        values = [r[key] for r in members if r[key] is not None]
        row[key] = operation(values) if values else None
    for key in ('hold_mean', 'velocity_mean'):
        row[key] = float(np.mean([r[key] for r in scored])) if scored else None
    row['delta_mean'] = row['velocity_mean'] - row['hold_mean'] if scored else None
    for name, sign in (('improved', -1), ('worsened', 1), ('tied', 0)):
        key = 'environments_' + name
        row[key] = (sum(r[key] for r in members) if scope == 'macro'
                    else int(bool(scored) and np.sign(row['delta_mean']) == sign))
    return row


def summarize(records):
    by_env = defaultdict(list)
    for row in records:
        by_env[row['age_bin'], row['hand'], row['environment']].append(row)
    environments = [combine(members, 'environment') for members in by_env.values()]
    by_bin = defaultdict(list)
    for row in environments:
        by_bin[row['age_bin'], row['hand']].append(row)
    macro = [combine(members, 'macro') for members in by_bin.values()]
    return macro + environments + records


def self_test():
    # Irregular true-time fit, large epoch, inclusive 0.5 s history boundary.
    origin = 1_700_000_000_000_000_000
    relative = np.array([0, 500_000_000, 630_000_000, 880_000_000, SECOND], dtype=np.int64)
    times = origin + relative
    slope = np.array([2., -3., .5])
    points = np.array([5., 6., 7.]) + relative[:, None] / SECOND * slope
    points[0] += 10_000  # Outside the recent interval; must not affect the slope.
    last, anchor, fitted, count = recent_fit(times, points, np.ones(5, dtype=bool))
    assert last == times[-1] and count == 4
    np.testing.assert_allclose(fitted, slope, rtol=0, atol=1e-12)
    np.testing.assert_array_equal(anchor, points[-1])
    assert recent_fit(times, points, np.array([False] * 4 + [True]))[2:] == (None, 1)
    assert recent_fit(times, points, np.zeros(5, dtype=bool))[2:] == (None, 0)

    # The OLS fitted endpoint is NOT the measured endpoint used for extrapolation.
    bent_times = origin + np.array([0, 300_000_000, 500_000_000], dtype=np.int64)
    bent = np.array([[1., 2., 3.], [1., 2., 3.], [2., 2., 3.]])
    _, anchor, fitted, _ = recent_fit(bent_times, bent, np.ones(3, dtype=bool))
    np.testing.assert_allclose(fitted, [35 / 19, 0, 0], rtol=0, atol=1e-12)
    fitted_endpoint = bent.mean(axis=0) + fitted * (7 / 30)
    assert not np.allclose(anchor, fitted_endpoint)
    future = bent_times[-1] + np.array([1, 100_000_000, 2 * SECOND], dtype=np.int64)
    hp = dict(left=bent, right=bent + 10)
    hm = {s: np.ones(3, dtype=bool) for s in SIDES}
    hold, velocity, _ = predict_pair(bent_times, hp, hm, future)
    np.testing.assert_allclose(velocity[:, 0], anchor + (future - bent_times[-1])[:, None] / SECOND * fitted)
    np.testing.assert_array_equal(hold[:, 1], np.broadcast_to(bent[-1] + 10, (3, 3)))
    hm['right'] = np.array([False, False, True])
    unavailable = predict_pair(bent_times, hp, hm, future)
    assert unavailable[:2] == (None, None) and unavailable[2] == dict(left=3, right=1)

    # Future references change errors, never predictions (predictor takes no labels).
    references = velocity.copy()
    error_before = np.linalg.norm(velocity - references, axis=2)
    references += 100
    repeated = predict_pair(bent_times, hp, {s: np.ones(3, dtype=bool) for s in SIDES}, future)
    np.testing.assert_array_equal(repeated[1], velocity)
    assert np.all(np.linalg.norm(velocity - references, axis=2) > error_before)
    assert age_bins(np.array([1, SECOND // 2, SECOND // 2 + 1, SECOND,
                              SECOND + 1, 3 * SECOND // 2, 3 * SECOND // 2 + 1])).tolist() == [
                                  BINS[0], BINS[0], BINS[1], BINS[1], BINS[2], BINS[2], BINS[3]]

    # Unequal point/record/environment counts: macro is neither micro nor record-pooled.
    synthetic = []
    for record, env, n, h, v in (('a', 'A', 1, 1, 0), ('b', 'A', 9, 3, 2), ('c', 'B', 4, 10, 12)):
        synthetic.extend(record_rows(record, 'synthetic', env, np.full(n, SECOND // 4, dtype=np.int64),
                                     np.zeros(n, dtype=int), {s: np.tile([h, v], (n, 1)) for s in SIDES},
                                     1, dict.fromkeys(SIDES, 0), {s: [3] for s in SIDES}))
    synthetic.extend(record_rows('d', 'C', 'C', np.empty(0, dtype=np.int64), np.empty(0, dtype=int),
                                 {s: np.empty((0, 2)) for s in SIDES}, 0,
                                 dict.fromkeys(SIDES, 0), {s: [] for s in SIDES}))
    for row in summarize(synthetic):
        if row['scope'] == 'macro':
            assert row['hold_mean'] == 6 and row['velocity_mean'] == 6.5 and row['delta_mean'] == .5
            assert row['records'] == 3 and row['environments'] == 2 and row['timestamps'] == 14
            assert row['environments_improved'] == row['environments_worsened'] == 1
            if row['age_bin'] == 'all':
                assert row['indexed_records'] == 4 and row['indexed_environments'] == 3
                assert row['pool_windows'] == row['paired_windows'] == 3
    print('Self-test passed: true ns clock, inclusive recent interval, per-hand shortage, observed anchor, '
          'future-reference isolation, bin edges, hierarchical weights and zero-window coverage.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT, help='Read-only C attachment root; official train only')
    parser.add_argument('--self-test', action='store_true', help='In-memory tests only; no raw-data access/output file')
    args = parser.parse_args()
    self_test()
    if args.self_test:
        return
    rows = summarize(evaluate(args.root))
    macro = [r for r in rows if r['scope'] == 'macro']
    if not any(r['timestamps'] for r in macro):
        raise ValueError('No paired training samples; do not overwrite the aggregate')
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator='\n')
        writer.writeheader()
        writer.writerows({k: f'{v:.12f}' if isinstance(v, float) else v for k, v in row.items()} for row in rows)
    for row in macro:
        print(f"{row['age_bin']:>7} {row['hand']:>5} hold={row['hold_mean']:.9f} "
              f"velocity={row['velocity_mean']:.9f} delta={row['delta_mean']:+.9f} "
              f"env_improved/worsened/tied={row['environments_improved']}/"
              f"{row['environments_worsened']}/{row['environments_tied']} "
              f"windows={row['paired_windows']} timestamps={row['timestamps']}")
        if row['age_bin'] == 'all':
            print('Coverage:', {k: row[k] for k in ('hand', 'indexed_records', 'indexed_environments',
                  'records', 'environments', 'pool_windows', 'excluded_windows', 'insufficient_history_windows',
                  'recent_points_min', 'recent_points_max', 'min_age_s', 'max_age_s')})
    print(f'Python {sys.version.split()[0]}; NumPy {np.__version__}')
    print(f'{OUT.relative_to(Path(__file__).resolve().parents[1])}: {len(rows)} aggregate rows; '
          f'SHA256 {hashlib.sha256(OUT.read_bytes()).hexdigest()}')


if __name__ == '__main__':
    main()
