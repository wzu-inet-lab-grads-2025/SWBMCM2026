"""Train-only paired hand-channel feasibility; public output is aggregate CSV only.

Fixed specification: research/C题腕姿指形回访可行性探索.md.
AI assistance, 2026-11-21: OpenCode, Sonus / Qoder / Remote magpie,
provider magpie, model remote-magpie/qoder/smodel; release date unavailable.
No fitting, price claim, formal channel selection, or held-out entry.
Run with python -B -m experiments.c_feas_hand_channels [--self-test|--verify].
"""

import argparse
import csv
import hashlib
import os
from pathlib import Path
import sys
from unittest.mock import patch

sys.dont_write_bytecode = True
import numpy as np
import pandas as pd

from .c_hand_state_channels import read_states
from .c_palm_scale import SECOND, SIDES
from .c_q2_delayed_update_probe import DELAY_MS, request_clock
from .c_q2_diagnostic import replay
from .c_q2_windows import qualified_runs, windows, wrist_inputs
from .c_records import ROOT, split_records

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / 'research/feas_hand_channels'
FINGERS = ('index', 'middle', 'ring', 'little')
JOINTS = tuple(base + offset for base in (5, 9, 13, 17) for offset in range(3))
COORDS = [f'transforms[{j}].pos.{axis}' for j in JOINTS for axis in 'xyz']
QUATS = [f'transforms[0].quat.{axis}' for axis in 'wxyz']
METHODS = ('no_receipt', 'position_only', 'position_quat', 'position_fingers', 'position_quat_fingers')
READ_SLICES = {
    'position_only': (slice(0, 3),),
    'position_quat': (slice(0, 7),),
    'position_fingers': (slice(0, 3), slice(7, 43)),
    'position_quat_fingers': (slice(0, 43),),
}
PHASES = ('pre', 'post', 'all')
TOL = 1e-10  # Numerical tie only, in the metric's unit, not an application threshold.
KEYS = ['method', 'reference', 'phase', 'hand', 'metric', 'unit']
DIRECTIONS = ('improved', 'worsened', 'tied')
LEVELS = ('windows', 'records', 'environments')
CHANGES = [f'{d}_{level}' for level in LEVELS for d in DIRECTIONS]
FIELDS = ['split', 'scope', 'environment', *KEYS, 'windows', 'points', 'records', 'environments',
          'mean_error', 'reference_error', 'delta_error', *CHANGES]
OLD_TABLES = (PROJECT / 'research/q2_diagnostic/train_packet_value.csv',
              PROJECT / 'research/q2_diagnostic/train_delayed_update_probe.csv')


def normalized(q):
    norm = np.linalg.norm(q, axis=-1, keepdims=True)
    return np.divide(q, norm, out=np.full_like(q, np.nan), where=np.isfinite(norm) & (norm > 0))


def rotation_error(a, b):
    """Shortest SO(3) angle in degrees, stable at q==+/-r; arrays use wxyz.

    4 atan2(min(||q-r||, ||q+r||), max(...)) == 2 acos(|q dot r|)
    for unit quaternions, without acos's loss of precision near zero.
    """
    a, b = normalized(a), normalized(b)
    minus, plus = np.linalg.norm(a-b, axis=-1), np.linalg.norm(a+b, axis=-1)
    return np.degrees(4*np.arctan2(np.minimum(minus, plus), np.maximum(minus, plus)))


def bends_from_points(points):
    first, second = points[..., 1, :] - points[..., 0, :], points[..., 2, :] - points[..., 1, :]
    angles = np.degrees(np.arctan2(np.linalg.norm(np.cross(first, second), axis=-1),
                                 (first*second).sum(axis=-1)))
    valid = np.isfinite(points).all(axis=(-1, -2)) & (points != 0).any(axis=-1).all(axis=-1)
    valid &= (np.linalg.norm(first, axis=-1) > 0) & (np.linalg.norm(second, axis=-1) > 0)
    return np.where(valid, angles, np.nan)


def packed_states(folder, side, time, wrist):
    """Reference loader only: reuse existing geometry and independently check raw packet fields."""
    middle, quat, bend = read_states(folder, side, time)
    frame = pd.read_csv(folder / f'tables/pose_{side}_hand.csv',
                        usecols=['header.ts', *COORDS, *QUATS], dtype={'header.ts': 'string'})
    np.testing.assert_array_equal(time, frame['header.ts'].to_numpy(np.int64))
    points = frame[COORDS].to_numpy(float).reshape(-1, 4, 3, 3)
    np.testing.assert_array_equal(points[:, 1, 0], middle)
    np.testing.assert_allclose(normalized(frame[QUATS].to_numpy(float)), quat, rtol=0, atol=1e-14, equal_nan=True)
    np.testing.assert_allclose(bends_from_points(points), bend, rtol=0, atol=1e-12, equal_nan=True)
    return np.concatenate((wrist, quat, points.reshape(-1, 36)), axis=-1), bend


def predict(clock, anchor_ns, initial, receive):
    """No hidden trajectory/quality input. The scheduler alone discloses the source packet."""
    source, arrival = request_clock(clock, anchor_ns)
    if initial.shape != (2, 43) or not np.isfinite(initial).all():
        raise ValueError('Expected a complete, finite historical pair')
    predictions = {}
    for method in METHODS:
        calls = []

        def disclosed(ns):
            if calls or ns != source:
                raise ValueError('One fixed common source per hypothetical packet')
            calls.append(ns)
            full = np.asarray(receive(ns), dtype=float)
            if full.shape != initial.shape or not np.isfinite(full).all():
                raise ValueError('Malformed ideal expanded packet')
            visible = initial.copy()
            for selected in READ_SLICES[method]:
                visible[:, selected] = full[:, selected]
            return visible

        delay = 0 if method == 'no_receipt' else DELAY_MS
        prediction, bill = replay(clock, initial.copy(), delay, disclosed,
                                  request_not_before_ns=anchor_ns+SECOND)
        # The shared scheduler's legacy price is NOT a price for expanded fields.
        assert bill[:2] == ((0, 0) if not delay else (1, 1))
        assert bill[3] == (0 if not delay else .5)
        assert calls == ([] if not delay else [source])
        predictions[method] = prediction
    pre = clock < arrival
    for method in METHODS:
        np.testing.assert_array_equal(predictions[method][pre], predictions['no_receipt'][pre])
    for method in METHODS[2:]:
        np.testing.assert_array_equal(predictions[method][..., :3], predictions['position_only'][..., :3])
    np.testing.assert_array_equal(predictions['position_only'][..., 3:], predictions['no_receipt'][..., 3:])
    np.testing.assert_array_equal(predictions['position_quat'][..., 7:], predictions['position_only'][..., 7:])
    np.testing.assert_array_equal(predictions['position_fingers'][..., 3:7], predictions['position_only'][..., 3:7])
    return predictions


def error_arrays(prediction, truth):
    """Reference scoring only. No scalar mixes degrees and centimeters."""
    result = {}
    rot = rotation_error(prediction[..., 3:7], truth[..., 3:7])
    bend = np.abs(bends_from_points(prediction[..., 7:].reshape(-1, 2, 4, 3, 3)) -
                  bends_from_points(truth[..., 7:].reshape(-1, 2, 4, 3, 3)))
    pos = 100*np.linalg.norm(prediction[..., :3]-truth[..., :3], axis=-1)
    for j, side in enumerate(SIDES):
        result[side, 'rotation', 'deg'] = rot[:, j]
        result[side, 'wrist_position', 'cm'] = pos[:, j]
        for k, finger in enumerate(FINGERS):
            result[side, 'bend_'+finger, 'deg'] = bend[:, j, k]
        result[side, 'bend_mean4', 'deg'] = bend[:, j].mean(axis=-1)
    actual = truth[:, 1, :3] - truth[:, 0, :3]
    relative = prediction[:, 1, :3] - prediction[:, 0, :3]
    result['pair', 'relative_vector', 'cm'] = 100*np.linalg.norm(relative-actual, axis=-1)
    result['pair', 'wrist_distance', 'cm'] = 100*np.abs(np.linalg.norm(relative, axis=-1) - np.linalg.norm(actual, axis=-1))
    assert all(np.isfinite(a).all() for a in result.values())
    return result


def flags(delta, level):
    return {f'improved_{level}': int(delta < -TOL), f'worsened_{level}': int(delta > TOL),
            f'tied_{level}': int(abs(delta) <= TOL)}


def window_scores(record, environment, clock, anchor_ns, mask_end, initial, truth):
    source, arrival = request_clock(clock, anchor_ns)
    assert clock[0] > anchor_ns and arrival < mask_end
    assert int(clock[-1]) < mask_end
    source_index = int(clock.searchsorted(source))
    assert clock[source_index] == source
    grid = np.union1d(clock, np.array([arrival], dtype=np.int64))

    def receive(ns):
        assert ns == source
        return truth[source_index].copy()

    predictions = predict(grid, anchor_ns, initial, receive)
    real = np.isin(grid, clock)
    assert real.sum() == len(clock)
    # Do not invent a reference at the inserted return event.
    errors = {m: error_arrays(p[real], truth) for m, p in predictions.items()}
    for phase in PHASES:
        selected = clock < arrival if phase == 'pre' else clock >= arrival if phase == 'post' else np.ones(len(clock), bool)
        n = int(selected.sum())
        assert n > 0
        for method in METHODS:
            reference = 'no_receipt' if method in ('no_receipt', 'position_only') else 'position_only'
            for (side, metric, unit), error in errors[method].items():
                other = errors[reference][side, metric, unit]
                a, b = float(error[selected].sum()), float(other[selected].sum())
                yield dict(record=record, environment=environment, method=method, reference=reference,
                           phase=phase, hand=side, metric=metric, unit=unit, windows=1, points=n,
                           error_sum=a, reference_sum=b, **flags((a-b)/n, 'windows'))


def summarize(samples):
    """Point-weighted within records, then equal records and equal environments."""
    data = pd.DataFrame(samples)
    sums = ['windows', 'points', 'error_sum', 'reference_sum', *[f'{d}_windows' for d in DIRECTIONS]]
    records = data.groupby(['environment', 'record', *KEYS], as_index=False, sort=True)[sums].sum()
    records['mean_error'] = records.error_sum / records.points
    records['reference_error'] = records.reference_sum / records.points
    records['delta_error'] = records.mean_error-records.reference_error
    for key in [f'{d}_records' for d in DIRECTIONS]:
        records[key] = [flags(v, 'records')[key] for v in records.delta_error]
    records['records'] = 1
    sum_keys = ['windows', 'points', 'records', *[f'{d}_{level}' for level in ('windows', 'records') for d in DIRECTIONS]]
    rule = {k: 'sum' for k in sum_keys} | {'mean_error': 'mean', 'reference_error': 'mean'}
    envs = records.groupby(['environment', *KEYS], as_index=False, sort=True).agg(rule)
    envs['delta_error'] = envs.mean_error-envs.reference_error
    for key in [f'{d}_environments' for d in DIRECTIONS]:
        envs[key] = [flags(v, 'environments')[key] for v in envs.delta_error]
    envs['environments'] = 1
    rule |= {k: 'sum' for k in ('environments', *[f'{d}_environments' for d in DIRECTIONS])}
    macro = envs.groupby(KEYS, as_index=False, sort=True).agg(rule).assign(environment='all')
    macro['delta_error'] = macro.mean_error-macro.reference_error
    return pd.concat([macro.assign(scope='macro'), envs.assign(scope='environment')], ignore_index=True).assign(split='train')[FIELDS]


def install_read_guard(root):
    """Python open audit: only training raw folders and two named training aggregates.

    Official split indices contain membership only. This is not a native-I/O sandbox.
    """
    root = root.resolve()
    index_dir = root / 'Restocking_Tasks_and_Constraints'
    indices = {index_dir / name for name in ('record_split.csv', 'source_segment_index.csv')}
    with (index_dir / 'source_segment_index.csv').open(encoding='utf-8-sig', newline='') as handle:
        sources = list(csv.DictReader(handle))
    allowed = {root / 'Raw_Data' / r['dataset'] / r['sample'] for r in sources if r['split'] == 'train'}
    audit = {'raw_paths': set(), 'aggregate_paths': set()}
    output_files = {OUT / n for n in ('summary.csv', 'coverage.csv', 'checks.csv')}

    def hook(event, args):
        if event != 'open' or not isinstance(args[0], (str, bytes, os.PathLike)):
            return
        path = Path(os.fsdecode(args[0])).resolve()
        mode, bits = args[1], args[2]
        writing = (isinstance(mode, str) and any(c in mode for c in 'wax+')) or bool(bits & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
        if writing and path not in output_files:
            raise PermissionError('This probe writes aggregate CSV files in its own output directory only')
        if path.is_relative_to(root):
            if writing or (path not in indices and not any(path.is_relative_to(folder) for folder in allowed)):
                raise PermissionError('Non-training raw-data access forbidden')
            audit['raw_paths'].add(path)
        if path.is_relative_to(PROJECT / 'research') or path.is_relative_to(PROJECT / 'runs'):
            if path not in output_files and path not in OLD_TABLES:
                raise PermissionError('Unrelated or held-out result access forbidden')
            if not writing:
                audit['aggregate_paths'].add(path)
    sys.addaudithook(hook)
    return audit


def evaluate(root):
    samples, inventory, clocks = [], [], []
    for record, folder, meta in split_records(root, 'train'):
        entry = dict(record=record, environment=meta['environment_id'], indexed_records=1,
                     demo_records=int(folder.parent.name == 'EgoDemo'), wrist_windows=0, common_windows=0,
                     excluded_windows=0, points_pre=0, points_post=0, points_all=0,
                     off_tick_receipts=0, pre_identical_windows=0)
        for side in SIDES:
            for field in ('quat', *FINGERS):
                entry[f'invalid_{side}_{field}_windows'] = 0
        inventory.append(entry)
        if entry['demo_records']:
            continue
        time, _, wrists, masks = wrist_inputs(folder)
        packed, bends = zip(*(packed_states(folder, side, time, wrists[side]) for side in SIDES))
        state, bend = np.stack(packed, axis=1), np.stack(bends, axis=1)
        for run in qualified_runs(time, masks['both']):
            for start, first, end in windows(time, run, SECOND, 2*SECOND):
                entry['wrist_windows'] += 1
                valid = True
                for j, side in enumerate(SIDES):
                    bad = not np.isfinite(state[start:end, j, 3:7]).all()
                    entry[f'invalid_{side}_quat_windows'] += int(bad)
                    valid &= not bad
                    for k, finger in enumerate(FINGERS):
                        bad = not np.isfinite(bend[start:end, j, k]).all()
                        entry[f'invalid_{side}_{finger}_windows'] += int(bad)
                        valid &= not bad
                if not valid:
                    entry['excluded_windows'] += 1
                    continue
                # Complete-case selection is OFFLINE reference eligibility, not an online quality signal.
                clock, anchor = time[first:end], int(time[first-1])
                source, arrival = request_clock(clock, anchor)
                mask_end = int(time[start])+3*SECOND
                entry['common_windows'] += 1
                entry['points_pre'] += int((clock < arrival).sum())
                entry['points_post'] += int((clock >= arrival).sum())
                entry['points_all'] += len(clock)
                entry['off_tick_receipts'] += int(not (clock == arrival).any())
                entry['pre_identical_windows'] += 1  # predict below asserts every method/field.
                clocks.append(dict(environment=entry['environment'], source_gap_s=(source-anchor)/SECOND,
                                   arrival_anchor_age_s=(arrival-anchor)/SECOND, packet_age_s=(arrival-source)/SECOND,
                                   first_post_lag_s=(int(clock[clock >= arrival][0])-arrival)/SECOND))
                q = state[start:end, :, 3:7]
                np.testing.assert_allclose(np.linalg.norm(q, axis=-1), 1, rtol=0, atol=1e-14)
                np.testing.assert_array_equal(rotation_error(q, -q), np.zeros(q.shape[:-1]))
                assert ((bend[start:end] >= 0) & (bend[start:end] <= 180)).all()
                samples.extend(window_scores(record, entry['environment'], clock, anchor, mask_end,
                                             state[first-1].copy(), state[first:end]))
    frame = pd.DataFrame(inventory)
    numeric = [c for c in frame if c not in ('record', 'environment')]
    coverage = frame.groupby('environment', as_index=False, sort=True)[numeric].sum()
    scored = frame.assign(scored_records=(frame.common_windows > 0).astype(int)).groupby('environment').scored_records.sum()
    coverage['scored_records'] = coverage.environment.map(scored)
    coverage['indexed_environments'] = 1
    coverage['scored_environments'] = (coverage.common_windows > 0).astype(int)
    ranges = pd.DataFrame(clocks).groupby('environment').agg(['min', 'max'])
    ranges.columns = [f'{a}_{b}' for a, b in ranges.columns]
    coverage = coverage.merge(ranges, on='environment', how='left')
    total = {c: coverage[c].sum() for c in [*numeric, 'scored_records', 'indexed_environments', 'scored_environments']}
    total |= {c: coverage[c].min() if c.endswith('_min') else coverage[c].max() for c in ranges.columns}
    total['environment'] = 'all'
    coverage = pd.concat([pd.DataFrame([total]).assign(scope='macro'), coverage.assign(scope='environment')], ignore_index=True)
    coverage.insert(0, 'split', 'train')
    assert len(samples) and total['wrist_windows'] == total['common_windows'] + total['excluded_windows']
    return summarize(samples), coverage


def validate_tables(summary, coverage):
    """No per-window rows are exported; check pairing and exact ablation invariances."""
    total = coverage[coverage.scope == 'macro'].iloc[0]
    macro = summary[summary.scope == 'macro']
    assert (total.indexed_records, total.indexed_environments, total.scored_records,
            total.scored_environments, total.wrist_windows, total.common_windows) == (55, 16, 28, 10, 328, 328)
    assert total.excluded_windows == 0
    assert (total.points_all, total.points_pre, total.points_post, total.off_tick_receipts) == (19639, 14422, 5217, 34)
    assert (macro.windows == total.common_windows).all()
    for phase in PHASES:
        assert (macro[macro.phase == phase].points == total['points_'+phase if phase != 'all' else 'points_all']).all()
    assert (summary.loc[summary.phase == 'pre', 'delta_error'].abs() <= TOL).all()
    for level in LEVELS:
        np.testing.assert_array_equal(summary[[f'{d}_{level}' for d in DIRECTIONS]].sum(axis=1), summary[level])
    identity = ['scope', 'environment', 'phase', 'hand', 'metric']
    pivot = summary.pivot(index=identity, columns='method', values='mean_error')
    for metric in ('wrist_position', 'relative_vector', 'wrist_distance'):
        rows = pivot.index.get_level_values('metric') == metric
        for method in METHODS[2:]:
            np.testing.assert_array_equal(pivot.loc[rows, method], pivot.loc[rows, 'position_only'])
    rows = pivot.index.get_level_values('metric') == 'rotation'
    np.testing.assert_array_equal(pivot.loc[rows, 'no_receipt'], pivot.loc[rows, 'position_only'])
    np.testing.assert_array_equal(pivot.loc[rows, 'position_fingers'], pivot.loc[rows, 'position_only'])
    np.testing.assert_array_equal(pivot.loc[rows, 'position_quat'], pivot.loc[rows, 'position_quat_fingers'])
    rows = pivot.index.get_level_values('metric').str.startswith('bend_')
    np.testing.assert_array_equal(pivot.loc[rows, 'no_receipt'], pivot.loc[rows, 'position_only'])
    np.testing.assert_array_equal(pivot.loc[rows, 'position_quat'], pivot.loc[rows, 'position_only'])
    np.testing.assert_array_equal(pivot.loc[rows, 'position_fingers'], pivot.loc[rows, 'position_quat_fingers'])
    finger = summary[summary.metric.str.startswith('bend_')].pivot(
        index=['scope', 'environment', 'method', 'phase', 'hand'], columns='metric', values='mean_error')
    np.testing.assert_allclose(finger['bend_mean4'], finger[['bend_'+f for f in FINGERS]].mean(axis=1), rtol=0, atol=1e-12)
    assert not {'record', 'window', 'source_ns', 'truth'}.intersection(summary.columns)


def check_old_training(summary):
    """Independent prior training aggregates only, never prior held-out scores."""
    old = pd.read_csv(OLD_TABLES[0]).query("method == 'request_at_1s' and scope != 'record'")
    delayed = pd.read_csv(OLD_TABLES[1]).query("scope != 'record' and windows > 0")
    largest, checked = 0., 0
    for prior, phase, fields in ((old, 'all', ('left_cm', 'right_cm')), (delayed, 'post', ('hold_cm', 'hold_cm'))):
        for _, row in prior.iterrows():
            if 'phase' in row and row.phase != phase:
                continue
            for side, field in zip(SIDES, fields):
                if 'hand' in row and row.hand != side:
                    continue
                match = summary[(summary.scope == row.scope) & (summary.phase == phase) &
                                (summary.method == 'position_only') & (summary.hand == side) &
                                (summary.metric == 'wrist_position')]
                if row.scope == 'environment':
                    match = match[match.environment == row.environment]
                assert len(match) == 1
                now = match.iloc[0]
                assert now.windows == row.windows and now.points == row.points
                difference = abs(now.mean_error-row[field])
                assert difference < 1e-8
                largest = max(largest, difference)
                checked += 1
    assert checked == 44
    return checked, largest


def synthetic_points(angles):
    theta = np.radians(np.asarray(angles, dtype=float))
    base = np.broadcast_to(np.array([2., 3., 4.]), (*theta.shape, 3)).copy()
    first = base + np.array([1., 0., 0.])
    second = first + np.stack([np.cos(theta), np.sin(theta), np.zeros_like(theta)], axis=-1)
    return np.stack([base, first, second], axis=-2)


def self_test():
    identity = np.array([1., 0., 0., 0.])
    q90 = np.array([np.sqrt(.5), np.sqrt(.5), 0., 0.])
    np.testing.assert_allclose(rotation_error(identity, q90), 90, atol=1e-12)
    assert rotation_error(identity, np.array([0., 0., 1., 0.])) == 180
    assert rotation_error(q90, -q90) == 0
    np.testing.assert_allclose(rotation_error(3*q90, -7*q90), 0, rtol=0, atol=1e-12)
    rng = np.random.default_rng(42)
    a, b = normalized(rng.normal(size=(300, 4))), normalized(rng.normal(size=(300, 4)))
    np.testing.assert_array_equal(rotation_error(a, b), rotation_error(a, -b))
    expected = 2*np.degrees(np.arccos(np.clip(np.abs((a*b).sum(axis=-1)), 0, 1)))
    np.testing.assert_allclose(rotation_error(a, b), expected, rtol=0, atol=1e-11)
    angles = np.array([0., 30., 90., 135.])
    pts = synthetic_points(angles)
    np.testing.assert_allclose(bends_from_points(pts), angles, rtol=0, atol=1e-12)
    np.testing.assert_allclose(bends_from_points(100*pts+10), angles, rtol=0, atol=1e-12)
    broken = pts.copy()
    broken[2, 2] = broken[2, 1]
    assert np.isnan(bends_from_points(broken)[2])
    time = np.array([1700000000000000000, 1700000000033333333], dtype=np.int64)
    fixture = pd.DataFrame(np.tile(pts.reshape(1, -1), (2, 1)), columns=COORDS)
    fixture['header.ts'] = [str(t) for t in time]
    fixture[QUATS] = np.stack([3*q90, -7*q90])
    with patch('experiments.c_hand_state_channels.pd.read_csv', return_value=fixture):
        middle, q, bend = read_states(Path('unused'), 'left', time)
        np.testing.assert_array_equal(middle, np.tile(pts[1, 0], (2, 1)))
        np.testing.assert_allclose(bend, np.tile(angles, (2, 1)), rtol=0, atol=1e-12)
        np.testing.assert_allclose(rotation_error(q[0], q[1]), 0, atol=1e-12)
        try:
            read_states(Path('unused'), 'left', time+1)
        except ValueError:
            pass
        else:
            raise AssertionError('Nanosecond clock mismatch must fail')
    origin = int(time[0])
    source, arrival = origin+1_002_000_000, origin+1_502_000_000
    clock = origin+np.array([20_000_000, 990_000_000, 1_002_000_000, 1_499_000_000,
                            1_501_999_999, 1_502_000_000, 1_502_000_001, 1_999_000_000], dtype=np.int64)
    initial = np.concatenate((np.array([[1., 2., 3.], [4., 6., 3.]]), np.tile(identity, (2, 1)),
                              np.tile(synthetic_points([0., 0., 0., 0.]).reshape(1, -1), (2, 1))), axis=1)
    packet = initial.copy()
    packet[:, :3] += [1., -2., 3.]
    packet[:, 3:7] = q90
    packet[:, 7:] = synthetic_points([90., 30., 60., 120.]).reshape(1, -1)
    seen = []

    def receive(ns):
        seen.append(ns)
        assert ns == source
        return packet.copy()

    predictions = predict(clock, origin, initial, receive)
    assert seen == [source]*4
    pre = clock < arrival
    for p in predictions.values():
        np.testing.assert_array_equal(p[pre], np.broadcast_to(initial, p[pre].shape))
    np.testing.assert_array_equal(predictions['position_quat_fingers'][~pre], np.broadcast_to(packet, predictions['position_quat_fingers'][~pre].shape))
    alternate = packet.copy()
    alternate[:, :3] += 20
    alternate[:, 3:7] = identity
    alternate[:, 7:] = initial[:, 7:]
    changed = predict(clock, origin, initial, lambda _: alternate)
    for m in METHODS:
        np.testing.assert_array_equal(predictions[m][pre], changed[m][pre])
    assert not np.array_equal(predictions['position_quat_fingers'][~pre], changed['position_quat_fingers'][~pre])
    def forbidden(_):
        raise AssertionError('Premature source disclosure')
    try:
        replay(clock[pre], initial, DELAY_MS, forbidden, request_not_before_ns=origin+SECOND)
    except ValueError as error:
        assert 'did not arrive' in str(error)
    else:
        raise AssertionError('Missing receipt did not fail')
    truth = np.broadcast_to(initial, (len(clock), 2, 43)).copy()
    truth[clock == source] = packet
    mutated = truth.copy()
    mutated[clock != source, :, :3] += 50
    for values in (truth, mutated):
        repeated = predict(clock, origin, initial, lambda ns: values[int(clock.searchsorted(ns))])
        for m in METHODS:
            np.testing.assert_array_equal(predictions[m], repeated[m])
    errors = error_arrays(predictions['position_quat_fingers'][~pre], truth[~pre])
    assert np.all(errors['left', 'rotation', 'deg'] > 89)
    assert np.all(errors['left', 'bend_index', 'deg'] > 89)  # Return-to-old-state counterexample.
    assert np.max(errors['pair', 'relative_vector', 'cm']) < 1e-12  # Common translation cancels.
    toy = initial.copy()
    toy[1, :3] = initial[0, :3] - (initial[1, :3]-initial[0, :3])
    err = error_arrays(toy[None], initial[None])
    assert err['pair', 'relative_vector', 'cm'][0] > 0 and err['pair', 'wrist_distance', 'cm'][0] == 0
    scored = list(window_scores('R', 'E', clock[clock != arrival], origin, origin+2*SECOND,
                                initial, truth[clock != arrival]))
    assert all(r['points'] == 2 for r in scored if r['phase'] == 'post')
    base = scored[0]
    samples = []
    for rec, env, n, value in [('a', 'A', 1, 1.), ('a', 'A', 3, 5.), ('b', 'A', 2, 10.), ('c', 'B', 7, 20.)]:
        samples.append(base | dict(record=rec, environment=env, points=n, error_sum=n*value,
                                   reference_sum=n*(value+1), **flags(-1, 'windows')))
    macro = summarize(samples).query("scope == 'macro'").iloc[0]
    assert macro.mean_error == 13.5 and macro.reference_error == 14.5 and macro.delta_error == -1
    assert (macro.improved_windows, macro.improved_records, macro.improved_environments) == (4, 3, 2)
    print('Synthetic checks passed: quaternion equivalence/units, finger mapping, exact delayed disclosure, '
          'same-packet reads, hidden-reference isolation, real-tick scoring, relative geometry, '
          'hierarchical weights and an expanded-packet worsening counterexample.')


def csv_text(frame):
    return frame.to_csv(index=False, float_format='%.12g', lineterminator='\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--self-test', action='store_true', help='Synthetic in-memory checks only')
    parser.add_argument('--verify', action='store_true', help='Recompute and compare all CSV bytes, writing nothing')
    args = parser.parse_args()
    self_test()
    if args.self_test:
        return
    audit = install_read_guard(args.root)
    summary, coverage = evaluate(args.root)
    validate_tables(summary, coverage)
    checked, difference = check_old_training(summary)
    checks = pd.DataFrame([
        dict(check='synthetic_suite', passed=1, value=1),
        dict(check='fixed_pool_and_common_denominators', passed=1, value=328),
        dict(check='all_pre_receipt_predictions_equal', passed=1, value=328),
        dict(check='quaternion_sign_equivalence_all_eligible_history_and_mask', passed=1, value=328),
        dict(check='raw_geometry_matches_existing_read_states', passed=1, value=28),
        dict(check='old_training_position_rows_checked', passed=1, value=checked),
        dict(check='old_training_max_abs_difference_cm', passed=1, value=difference),
        dict(check='python_open_guard_raw_unique_paths', passed=1, value=len(audit['raw_paths'])),
        dict(check='old_training_aggregate_files_read', passed=1, value=len(set(OLD_TABLES) & audit['aggregate_paths'])),
        dict(check='writes_only_aggregate_csv', passed=1, value=3),
    ])
    outputs = {'summary.csv': summary, 'coverage.csv': coverage, 'checks.csv': checks}
    if not args.verify:
        OUT.mkdir(parents=True, exist_ok=True)
    for name, frame in outputs.items():
        text = csv_text(frame)
        path = OUT / name
        if args.verify:
            if path.read_text(encoding='utf-8') != text:
                raise ValueError(f'Recomputed aggregate differs: {name}')
        else:
            path.write_text(text, encoding='utf-8')
        print(f'{name}: {len(frame)} aggregate rows; SHA256 {hashlib.sha256(text.encode()).hexdigest()}')
    selected = summary[(summary.scope == 'macro') & (summary.phase == 'post') &
                       (summary.method.isin(['position_only', 'position_quat_fingers']))]
    print(selected[['method', 'hand', 'metric', 'unit', 'mean_error', 'reference_error', 'delta_error',
                    'improved_windows', 'worsened_windows', 'improved_environments', 'worsened_environments']].to_string(index=False))
    print(f'Python {sys.version.split()[0]}; NumPy {np.__version__}; pandas {pd.__version__}; '
          f'old training max difference {difference:.3g} cm; verify={args.verify}')


if __name__ == '__main__':
    main()
