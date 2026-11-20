"""Fixed train-only leave-one-environment-out age-radius coverage diagnostic.

Pre-run specification: research/C题时距误差半径训练内核对.md.
AI-assisted implementation/checking, 2026-11-20: OpenCode, Sonus / Qoder /
Remote magpie (provider magpie, model remote-magpie/qoder/smodel; release date
unavailable). Human competition review required. Shared notices are unchanged.
Only aggregate output; no re-observation, model selection, validation or test.
"""

import argparse
import csv
import hashlib
from pathlib import Path
import sys

sys.dont_write_bytecode = True
import numpy as np
import pandas as pd

if __package__:
    from .c_q2_diagnostic import BINS, SECOND, replay, training_quantiles
    from .c_q2_windows import ROOT, SIDES, qualified_runs, split_records, windows, wrist_inputs
else:
    from c_q2_diagnostic import BINS, SECOND, replay, training_quantiles
    from c_q2_windows import ROOT, SIDES, qualified_runs, split_records, windows, wrist_inputs


OUT = Path(__file__).resolve().parents[1] / 'runs/q2_diagnostic/train_age_coverage_probe.csv'
EDGES_NS = [SECOND // 2, SECOND, 3 * SECOND // 2]
METHODS = ('age_p90', 'global_p90')
EXPECTED_POINTS = dict(zip(BINS, (4894, 4912, 4907, 4926)))
FIELDS = (
    'split', 'scope', 'method', 'hand', 'age_bin', 'environment', 'record', 'dataset', 'reason',
    'indexed_records', 'indexed_environments', 'records', 'environments', 'windows', 'timestamps',
    'min_age_s', 'max_age_s', 'fit_age_bin', 'fit_environments', 'fit_records', 'fit_windows',
    'fit_timestamps', 'coverage', 'radius_cm', 'radius_min_cm', 'radius_max_cm',
    'coverage_env_min', 'coverage_env_max', 'radius_env_min_cm', 'radius_env_max_cm',
    'environments_below_90',
)


def age_bins(age_ns):
    return np.asarray(BINS)[np.searchsorted(EDGES_NS, age_ns, side='left')]


def no_receive(_):
    raise AssertionError('No observation may be requested/received in this probe')


def load_training(root):
    """Same shared window/hold-last entry, without running other delay conditions."""
    parts, audit = [], []
    for record, folder, meta in split_records(root, 'train'):
        env, dataset = meta['environment_id'], folder.parent.name
        n_windows = n_points = 0
        if dataset != 'EgoDemo':
            times, _, points, masks = wrist_inputs(folder)
            pairs = np.stack([points[side] for side in SIDES], axis=1)
            for run in qualified_runs(times, masks['both']):
                for _, first, end in windows(times, run, SECOND, 2 * SECOND):
                    n_windows += 1
                    clock = times[first:end]
                    age = clock - times[first - 1]
                    predictions, bill = replay(clock, pairs[first - 1], 0, no_receive)
                    assert bill == (0, 0, 0, 0)
                    error = np.linalg.norm(predictions - pairs[first:end], axis=2)
                    n_points += len(clock)
                    # Errors remain in original units until reported as centimeters.
                    parts.append(pd.DataFrame(dict(
                        record=record, environment=env, window=n_windows, delay_ms=0,
                        age_bin=age_bins(age), age_s=age / SECOND,
                        left=error[:, 0], right=error[:, 1])))
        audit.append(dict(
            split='train', scope='audit_record', age_bin='all', record=record,
            environment=env, dataset=dataset, indexed_records=1, indexed_environments=1,
            records=int(n_points > 0), environments=int(n_points > 0),
            windows=n_windows, timestamps=n_points,
            reason='explicit_normal_unavailable' if dataset == 'EgoDemo' else ''))
    if not parts:
        raise ValueError('No training windows; do not overwrite the aggregate')
    return pd.concat(parts, ignore_index=True), audit


def counts(group):
    return dict(records=group.record.nunique(), environments=group.environment.nunique(),
                windows=len(group[['record', 'window']].drop_duplicates()), timestamps=len(group))


def check_pool(samples, audit):
    """Fail rather than silently change the prescribed 328-window denominator."""
    assert len(audit) == 55 and len({r['environment'] for r in audit}) == 16
    assert counts(samples) == dict(records=28, environments=10, windows=328, timestamps=19639)
    assert sum(r['windows'] for r in audit) == 328
    assert sum(r['timestamps'] for r in audit) == 19639
    assert sum(r['windows'] == 0 for r in audit) == 27
    assert not samples.duplicated(['record', 'window', 'age_s']).any()
    assert (samples.age_s > 0).all() and np.isfinite(samples[list(SIDES)]).all().all()
    for label, n in EXPECTED_POINTS.items():
        assert counts(samples[samples.age_bin == label]) == dict(
            records=28, environments=10, windows=328, timestamps=n)
    for row in audit:
        group = samples[samples.record == row['record']]
        assert len(group) == row['timestamps'] and group.window.nunique() == row['windows']


def fit_radii(fitting):
    """Exactly the existing hierarchical empirical quantile; retain p90 only."""
    radii = {(r['age_bin'], r['hand']): r['p90'] for r in training_quantiles(fitting)}
    radii.update({('all', r['hand']): r['p90']
                 for r in training_quantiles(fitting.assign(age_bin='all'))})
    assert len(radii) == 2 * (len(BINS) + 1)
    return radii


def record_equal_mean(group, values):
    return float(pd.Series(np.asarray(values), index=group.index).groupby(group.record).mean().mean())


def score_environment(fitting, held, radii):
    """Called only after fitting; held-out errors do not enter the radius lookup."""
    assert set(fitting.environment).isdisjoint(held.environment)
    assert held.environment.nunique() == 1
    for method in METHODS:
        for side in SIDES:
            for label in (*BINS, 'all'):
                group = held if label == 'all' else held[held.age_bin == label]
                fit_bin = 'all' if method == 'global_p90' else label
                fit_group = fitting if fit_bin == 'all' else fitting[fitting.age_bin == fit_bin]
                keys = group.age_bin if method == 'age_p90' else ['all'] * len(group)
                radius = np.array([radii[key, side] for key in keys])
                coverage = record_equal_mean(group, group[side].to_numpy() <= radius)
                mean_radius_cm = 100 * record_equal_mean(group, radius)
                yield dict(
                    split='train', scope='held_environment', method=method, hand=side,
                    age_bin=label, environment=held.environment.iloc[0], **counts(group),
                    min_age_s=float(group.age_s.min()), max_age_s=float(group.age_s.max()),
                    fit_age_bin='by_age' if method == 'age_p90' and label == 'all' else fit_bin,
                    **{'fit_' + key: value for key, value in counts(fit_group).items()},
                    coverage=coverage, radius_cm=mean_radius_cm,
                    radius_min_cm=100 * float(radius.min()), radius_max_cm=100 * float(radius.max()),
                    coverage_env_min=coverage, coverage_env_max=coverage,
                    radius_env_min_cm=mean_radius_cm, radius_env_max_cm=mean_radius_cm,
                    environments_below_90=int(coverage < .9))


def leave_environment_out(samples):
    rows = []
    for env in sorted(samples.environment.unique()):
        fitting = samples[samples.environment != env]
        held = samples[samples.environment == env]
        assert fitting.environment.nunique() == samples.environment.nunique() - 1
        radii = fit_radii(fitting)
        rows.extend(score_environment(fitting, held, radii))
    return rows


def summarize(folds, audit):
    macro = []
    for method in METHODS:
        for side in SIDES:
            for label in (*BINS, 'all'):
                members = [r for r in folds if (r['method'], r['hand'], r['age_bin']) == (method, side, label)]
                row = dict(split='train', scope='macro', method=method, hand=side, age_bin=label,
                           fit_age_bin=members[0]['fit_age_bin'], fit_environments=9,
                           indexed_records=len(audit), indexed_environments=len({r['environment'] for r in audit}))
                for key in ('records', 'environments', 'windows', 'timestamps', 'environments_below_90'):
                    row[key] = sum(r[key] for r in members)
                for key in ('coverage', 'radius_cm'):
                    row[key] = float(np.mean([r[key] for r in members]))
                for key in ('min_age_s', 'radius_min_cm', 'coverage_env_min', 'radius_env_min_cm'):
                    row[key] = min(r[key] for r in members)
                for key in ('max_age_s', 'radius_max_cm', 'coverage_env_max', 'radius_env_max_cm'):
                    row[key] = max(r[key] for r in members)
                macro.append(row)
    return macro + folds + audit


def self_test():
    origin = 1_700_000_000_000_000_000
    ages = np.array([1, SECOND // 2, SECOND // 2 + 1, SECOND, SECOND + 1,
                     3 * SECOND // 2, 3 * SECOND // 2 + 1], dtype=np.int64)
    assert age_bins((origin + ages) - origin).tolist() == [
        BINS[0], BINS[0], BINS[1], BINS[1], BINS[2], BINS[2], BINS[3]]
    pair = np.array([[1., 2., 3.], [4., 5., 6.]])
    predictions, bill = replay(origin + ages, pair, 0, no_receive)
    np.testing.assert_array_equal(predictions, np.broadcast_to(pair, (len(ages), 2, 3)))
    assert bill == (0, 0, 0, 0)

    # Unequal records/points: environment A gets 1/2, record c in B gets 1/2.
    # The 99 points at b dominate a point-pooled CDF, but B has half the mass here.
    parts = []
    for record, env, n, error in (('a', 'A', 1, 1.), ('b', 'A', 99, 2.), ('c', 'B', 1, 10.)):
        for i, label in enumerate(BINS):
            parts.append(pd.DataFrame(dict(record=record, environment=env, window=1, delay_ms=0,
                                          age_bin=label, age_s=.25 + .5 * i,
                                          left=np.full(n, error + i), right=np.full(n, 2 * (error + i)))))
    fitting = pd.concat(parts, ignore_index=True)
    radii = fit_radii(fitting)
    for i, label in enumerate(BINS):
        assert radii[label, 'left'] == 10 + i and radii[label, 'right'] == 2 * (10 + i)
    assert radii['all', 'left'] == 13 and radii['all', 'right'] == 26
    # Record weighting alone: a single high point is half of its environment.
    within = fitting[fitting.environment == 'A'].copy()
    for side in SIDES:
        within[side] = np.where(within.record == 'a', 100., 0.)
    assert set(fit_radii(within).values()) == {100.}
    # Environment weighting alone: twenty zero records cannot swamp one environment.
    across = pd.concat([fitting[fitting.record == 'a'].assign(record=f'z{i}', left=0., right=0.)
                        for i in range(20)] + [fitting[fitting.record == 'c'].assign(left=100., right=100.)],
                       ignore_index=True)
    assert set(fit_radii(across).values()) == {100.}
    one_bin = fitting[fitting.age_bin == BINS[0]]
    assert record_equal_mean(one_bin[one_bin.environment == 'A'], np.r_[1, np.zeros(99)]) == .5

    # Held environment changes cannot influence its fitted radii; <= includes ties.
    held = fitting[fitting.record == 'c'].copy().assign(record='d', environment='C')
    original = list(score_environment(fitting, held, radii))
    assert all(r['coverage'] == 1 for r in original)
    changed = pd.concat([fitting, held.assign(left=1e6, right=2e6)], ignore_index=True)
    np.testing.assert_equal(fit_radii(changed[changed.environment != 'C']), radii)
    rejected = list(score_environment(fitting, changed[changed.environment == 'C'], radii))
    assert all(r['coverage'] == 0 for r in rejected)
    assert all(a['radius_cm'] == b['radius_cm'] for a, b in zip(original, rejected))

    # all uses point weights inside each record, not equal bin weights.
    uneven = pd.concat([held, held[held.age_bin == BINS[0]]], ignore_index=True)
    scored = list(score_environment(fitting, uneven, radii))
    all_left = next(r for r in scored if (r['method'], r['hand'], r['age_bin']) == ('age_p90', 'left', 'all'))
    assert all_left['radius_cm'] == 1120 and all_left['timestamps'] == 5
    assert all_left['fit_timestamps'] == len(fitting)
    print('Self-test passed: ns/bin boundaries, per-hand hold, no receipt, hierarchical p90, '
          'inclusive coverage, held-label isolation, centimeters and all-period weights.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT, help='Read-only attachment root; official train only')
    parser.add_argument('--self-test', action='store_true', help='Synthetic data only; no data access/output file')
    args = parser.parse_args()
    self_test()
    if args.self_test:
        return
    samples, audit = load_training(args.root)
    check_pool(samples, audit)
    folds = leave_environment_out(samples)
    assert len(folds) == 200 and all(r['fit_environments'] == 9 for r in folds)
    result = summarize(folds, audit)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator='\n')
        writer.writeheader()
        writer.writerows({k: f'{v:.12f}' if isinstance(v, float) else v for k, v in row.items()} for row in result)
    for row in result:
        if row['scope'] == 'macro':
            print(f"{row['method']:>10} {row['hand']:>5} {row['age_bin']:>7} "
                  f"coverage={100 * row['coverage']:.3f}% radius={row['radius_cm']:.3f}cm "
                  f"env_coverage={100 * row['coverage_env_min']:.3f}..{100 * row['coverage_env_max']:.3f}% "
                  f"below90={row['environments_below_90']}/10")
    print('Pool: 55 indexed records/16 environments; 28 eligible records/10 environments; '
          '328 paired windows; 19639 timestamps per hand; no extra exclusions.')
    print(f'Python {sys.version.split()[0]}; NumPy {np.__version__}; pandas {pd.__version__}')
    print(f'{OUT.relative_to(Path(__file__).resolve().parents[1])}: {len(result)} aggregate rows; '
          f'SHA256 {hashlib.sha256(OUT.read_bytes()).hexdigest()}')


if __name__ == '__main__':
    main()
