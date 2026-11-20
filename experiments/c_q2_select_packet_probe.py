"""Train-only, fixed-feature selection versus conditional equal-count random buying.

Pre-run specification: research/C题同支出选窗训练诊断.md.
AI assistance, 2026-11-20: OpenCode, Sonus / Qoder / Remote magpie,
provider magpie, model remote-magpie/qoder/smodel (release date unavailable).
No validation/test trajectory access, parameter search or deployable policy export.
"""

import argparse
from dataclasses import dataclass
import hashlib
from itertools import combinations
from pathlib import Path
import sys

sys.dont_write_bytecode = True
import numpy as np
import pandas as pd

if __package__:
    from .c_exceed_duration import runs_ns, window_comparison
    from .c_packet_value import PairedWindow, replay
    from .c_palm_motion import palm_points
    from .c_palm_scale import SECOND, SIDES, history_scale, lengths
    from .c_q2_windows import qualified_runs, windows, wrist_inputs
    from .c_records import ROOT, split_records
else:
    from c_exceed_duration import runs_ns, window_comparison
    from c_packet_value import PairedWindow, replay
    from c_palm_motion import palm_points
    from c_palm_scale import SECOND, SIDES, history_scale, lengths
    from c_q2_windows import qualified_runs, windows, wrist_inputs
    from c_records import ROOT, split_records


OUT = Path(__file__).resolve().parents[1] / 'runs/q2_select_packet_probe.csv'
PROBES = ('gain', 'mean', 'risk', 'entropy')
HANDS = (*SIDES, 'both')
EPS = 1e-12
SEED = 20261120


def history_feature(history, scale):
    """Only visible positions and the already locked per-hand scales are accepted."""
    assert history.ndim == 3 and history.shape[1:] == (2, 3) and len(history) >= 2
    return int(np.mean(np.linalg.norm(history[-1] - history[0], axis=1) / scale) > 1)


def load_training(root):
    references, visible, audit = [], [], []
    for record, folder, meta in split_records(root, 'train'):
        env, dataset = meta['environment_id'], folder.parent.name
        count = ticks = high = 0
        ages = []
        if dataset != 'EgoDemo':
            times, _, wrists, masks = wrist_inputs(folder)
            pairs = np.stack([wrists[s] for s in SIDES], axis=1)
            size = {}
            for side in SIDES:
                hand = palm_points(folder, side, times)
                size[side] = lengths(hand[:, 0], hand[:, 2])
            for run in qualified_runs(times, masks['both']):
                for start, first, end in windows(times, run, SECOND, 2 * SECOND):
                    scale = [history_scale(size[s][start:first]) for s in SIDES]
                    if any(value is None for value in scale):
                        raise ValueError('Prescribed 328-window scale pool changed')
                    scale = np.asarray(scale)
                    f = history_feature(pairs[start:first], scale)
                    w = PairedWindow(record, env, times[first:end], int(times[start]) + 3 * SECOND,
                                     int(times[first - 1]), pairs[first - 1], pairs[first:end], scale)
                    idx = w.clock.searchsorted(w.anchor_ns + SECOND)
                    assert idx < len(w.clock) and w.clock[idx] + SECOND // 2 < w.clock[-1]
                    ages.append((int(w.clock[idx]) - w.anchor_ns) / SECOND)
                    visible.append(dict(record=record, environment=env, f=f, ticks=len(w.clock)))
                    references.append(w)
                    count += 1
                    ticks += len(w.clock)
                    high += f
        audit.append(dict(block='audit', scope='indexed_record', record=record, environment=env,
                          dataset=dataset, windows=count, timestamps=ticks, f1_windows=high,
                          indexed_records=1, indexed_environments=1, records=int(count > 0),
                          environments=int(count > 0),
                          request_age_min_s=min(ages) if ages else np.nan,
                          request_age_max_s=max(ages) if ages else np.nan,
                          note='no_explicit_normal_reference' if dataset == 'EgoDemo' else ''))
    features = pd.DataFrame(visible)
    assert len(audit) == 55 and len({r['environment'] for r in audit}) == 16
    assert len(features) == len(references) == 328
    assert features.record.nunique() == 28 and features.environment.nunique() == 10
    assert int(features.ticks.sum()) == 19639 and sum(r['windows'] == 0 for r in audit) == 27
    return references, features, audit


def score_reference(w):
    """Evaluator only: both potential losses; never pass this result to a held action."""
    scores = list(window_comparison(w, 500, SECOND))
    out = {}
    for hand, row in zip(SIDES, scores):
        for key, source in (('base_total', 'base_s'), ('packet_total', 'updated_s'),
                            ('base_longest', 'longest_base_s'), ('packet_longest', 'longest_updated_s'),
                            ('avoided', 'avoided_s'), ('induced', 'induced_s')):
            out[f'{key}_{hand}'] = row[source]
    for key in ('base_total', 'packet_total', 'base_longest', 'packet_longest', 'avoided', 'induced'):
        out[f'{key}_both'] = np.mean([out[f'{key}_{s}'] for s in SIDES])
    terminal = np.linalg.norm(w.truth[-1] - w.initial, axis=1) / w.scale
    out.update(g=out['base_total_both'] - out['packet_total_both'],
               mu=float(terminal.mean()), z=int((terminal > 1).sum()), scored_s=scores[0]['scored_s'])
    assert abs(out['g'] - out['avoided_both'] + out['induced_both']) < EPS
    return out


def binary_entropy(p):
    if p == 0 or p == 1:
        return 0.
    return float(-p * np.log2(p) - (1 - p) * np.log2(1 - p))


def estimate(group):
    n = len(group)
    assert n > 0
    p = (float(group.z.sum()) + .5) / (2 * n + 1)
    return dict(n=n, gain=float(group.g.mean()), mean=float(group.mu.mean()),
                risk=p, entropy=binary_entropy(p))


@dataclass(frozen=True)
class FoldModel:
    cells: dict
    thresholds: dict
    overall: dict


def fit_model(training):
    """Window-equal two-cell lookup. Environment/record IDs never enter a score."""
    cells = {int(f): estimate(group) for f, group in training.groupby('f')}
    thresholds = {score: sum(c['n'] * c[score] for c in cells.values()) / len(training)
                  for score in PROBES}
    return FoldModel(cells, thresholds, estimate(training))


def decide_one(f, model):
    """Scalar API: no held fold, quota, packet callback, reference or future feature."""
    assert isinstance(f, (int, np.integer)) and f in (0, 1)
    cell = model.cells.get(f, model.thresholds)
    return {score: bool(cell[score] > model.thresholds[score] + EPS) for score in PROBES}


def conditional_random(n, realised):
    """Offline evaluation only, after actions: exact k-of-n marginal inclusion."""
    assert n > 0 and 0 <= realised <= n and int(realised) == realised
    return np.full(n, realised / n)


def forecast_rows(features, held_scores, model):
    rows = []
    for i, row in features.iterrows():
        y = held_scores.loc[i]
        cell = model.cells.get(int(row.f), model.overall)
        result = dict(record=row.record, environment=row.environment)
        for name, fit in (('age_only', model.overall), ('history', cell)):
            p, fraction = fit['risk'], y.z / 2
            result[f'{name}_mse'] = (y.mu - fit['mean']) ** 2
            result[f'{name}_logloss_bit'] = -fraction * np.log2(p) - (1 - fraction) * np.log2(1 - p)
            result[f'{name}_brier'] = fraction * (1 - p) ** 2 + (1 - fraction) * p ** 2
        rows.append(result)
    return rows


def account_records(features, scores, actions):
    """All reference-dependent calculations happen after the scalar decisions."""
    rows = []
    for record, group in features.groupby('record', sort=False):
        n = len(group)
        potentials = scores.loc[group.index]
        selections = {'no_request': np.zeros(n), 'all_request': np.ones(n)}
        for method in PROBES:
            selected = actions.loc[group.index, method].to_numpy(dtype=float)
            selections[method] = selected
            selections[f'random_{method}'] = conditional_random(n, int(selected.sum()))
        for method, selection in selections.items():
            k = int(round(selection.sum()))
            assert abs(k - selection.sum()) < EPS
            for hand in HANDS:
                b = potentials[f'base_total_{hand}'].to_numpy()
                a = potentials[f'packet_total_{hand}'].to_numpy()
                bl = potentials[f'base_longest_{hand}'].to_numpy()
                al = potentials[f'packet_longest_{hand}'].to_numpy()
                actual, longest = b + selection * (a - b), bl + selection * (al - bl)
                avoided = selection * potentials[f'avoided_{hand}'].to_numpy()
                induced = selection * potentials[f'induced_{hand}'].to_numpy()
                row = dict(block='accounting', scope='record', method=method, hand=hand,
                           record=record, environment=group.environment.iloc[0], records=1, environments=1,
                           windows=n, timestamps=int(group.ticks.sum()), f1_windows=int(group.f.sum()),
                           requests=k, receipts=k, cost=2 * k, pending_s=.5 * k,
                           total_s=float(actual.mean()), longest_s=float(longest.mean()),
                           net_saved_s=float((b - actual).mean()),
                           raw_total_s=float(actual.sum()), raw_net_saved_s=float((b - actual).sum()),
                           raw_avoided_s=float(avoided.sum()), raw_induced_s=float(induced.sum()),
                           scored_s=float(potentials.scored_s.mean()))
                assert abs(row['raw_net_saved_s'] - row['raw_avoided_s'] + row['raw_induced_s']) < EPS
                if method in PROBES:
                    random = selections[f'random_{method}']
                    row['selection_total_s'] = float(np.mean((random - selection) * (a - b)))
                    row['selection_longest_s'] = float(np.mean((random - selection) * (al - bl)))
                rows.append(row)
    return rows


def leave_environment_out(references, features):
    record_rows, forecasts, cell_rows, redundancy = [], [], [], []
    cache = {}

    def labels(indices):
        # Reuse evaluations across folds, never models. No held labels are fitted.
        for i in indices:
            if i not in cache:
                cache[i] = score_reference(references[i])
        return pd.DataFrame([cache[i] for i in indices], index=indices)

    for env in sorted(features.environment.unique()):
        train = features[features.environment != env]
        held = features[features.environment == env]
        assert train.environment.nunique() == 9 and set(train.index).isdisjoint(held.index)
        model = fit_model(train.join(labels(train.index)))
        # No quota computation or held outcomes here; processing order cannot change a decision.
        actions = pd.DataFrame([decide_one(int(row.f), model) for row in held.itertuples()], index=held.index)
        scores = labels(held.index)
        record_rows.extend(account_records(held, scores, actions))
        forecasts.extend(forecast_rows(held, scores, model))
        for f in (0, 1):
            cell = model.cells.get(f)
            cell_rows.append(dict(block='fit_cell', scope='held_environment', environment=env, f=f,
                                  fit_environments=9, fit_records=train.record.nunique(), fit_windows=len(train),
                                  cell_windows=0 if cell is None else cell['n'],
                                  **{f'fit_{k}': np.nan if cell is None else cell[k] for k in PROBES},
                                  **{f'threshold_{k}': model.thresholds[k] for k in PROBES}))
        cells = [model.cells.get(f) for f in (0, 1)]
        both = all(c is not None for c in cells)
        mu_encodes_f = both and abs(cells[1]['mean'] - cells[0]['mean']) > EPS
        redundancy.append(dict(block='redundancy', scope='held_environment', environment=env,
                               windows=len(held), records=held.record.nunique(), environments=1,
                               f1_windows=int(held.f.sum()), mean_encodes_f=int(mu_encodes_f),
                               both_cells_risk_above_half=int(both and min(c['risk'] for c in cells) > .5),
                               risk_mean_same_order=int(both and (cells[1]['risk'] - cells[0]['risk']) *
                                                        (cells[1]['mean'] - cells[0]['mean']) > EPS),
                               **{f'agree_{a}_{b}': int((actions[a] == actions[b]).sum())
                                  for a, b in combinations(PROBES, 2)}))
    return record_rows, forecasts, cell_rows, redundancy


SUM_FIELDS = ('records', 'windows', 'timestamps', 'f1_windows', 'requests', 'receipts', 'cost', 'pending_s',
              'raw_total_s', 'raw_net_saved_s', 'raw_avoided_s', 'raw_induced_s')
MEAN_FIELDS = ('total_s', 'longest_s', 'net_saved_s', 'scored_s', 'selection_total_s', 'selection_longest_s')


def aggregate_accounting(records):
    frame = pd.DataFrame(records)
    reducer = {k: 'sum' for k in SUM_FIELDS} | {k: 'mean' for k in MEAN_FIELDS}
    envs = frame.groupby(['method', 'hand', 'environment'], as_index=False).agg(reducer)
    envs['environments'], envs['scope'], envs['block'] = 1, 'held_environment', 'accounting'
    macro = envs.groupby(['method', 'hand'], as_index=False).agg(reducer | {'environments': 'sum'})
    macro['scope'], macro['block'] = 'macro', 'accounting'
    # Same environment resamples for all paired metrics, no pseudo-replicated frames.
    bootstrap = np.random.default_rng(SEED).integers(0, 10, size=(10000, 10))
    for i, row in macro.iterrows():
        if row.method not in PROBES:
            continue
        group = envs[(envs.method == row.method) & (envs.hand == row.hand)].sort_values('environment')
        assert len(group) == 10
        for metric in ('total', 'longest'):
            values = group[f'selection_{metric}_s'].to_numpy()
            low, high = np.quantile(values[bootstrap].mean(axis=1), [.025, .975])
            jack = (values.sum() - values) / 9
            for key, val in dict(positive=int((values > EPS).sum()), negative=int((values < -EPS).sum()),
                                 tied=int((np.abs(values) <= EPS).sum()), env_min=values.min(), env_max=values.max(),
                                 drop1_min=jack.min(), drop1_max=jack.max(), boot_low=low, boot_high=high).items():
                macro.loc[i, f'selection_{metric}_{key}'] = val
    result = pd.concat([macro, envs, frame], ignore_index=True)
    result['saving_per_packet_s'] = result.raw_net_saved_s / result.requests.replace(0, np.nan)
    result['request_fraction'] = result.requests / result.windows
    assert np.allclose(result.cost, 2 * result.requests) and np.allclose(result.pending_s, .5 * result.requests)
    for method in PROBES:
        a = result[result.method == method].sort_values(['scope', 'hand', 'environment', 'record'])
        b = result[result.method == f'random_{method}'].sort_values(['scope', 'hand', 'environment', 'record'])
        np.testing.assert_allclose(a[['requests', 'cost', 'pending_s']], b[['requests', 'cost', 'pending_s']])
        np.testing.assert_allclose(a.selection_total_s, b.total_s.to_numpy() - a.total_s.to_numpy(), atol=EPS)
        np.testing.assert_allclose(a.selection_longest_s, b.longest_s.to_numpy() - a.longest_s.to_numpy(), atol=EPS)
    return result.to_dict('records')


def aggregate_forecasts(rows):
    frame = pd.DataFrame(rows)
    metrics = [k for k in frame if k not in ('record', 'environment')]
    rec = frame.groupby(['environment', 'record'], as_index=False)[metrics].mean()
    rec['scope'] = 'record'
    env = rec.groupby('environment', as_index=False)[metrics].mean()
    env['scope'] = 'held_environment'
    macro = pd.DataFrame([dict(scope='macro', **env[metrics].mean().to_dict())])
    for metric in ('mse', 'logloss_bit', 'brier'):
        delta = env[f'age_only_{metric}'] - env[f'history_{metric}']
        macro[f'{metric}_improved_environments'] = int((delta > EPS).sum())
        macro[f'{metric}_worsened_environments'] = int((delta < -EPS).sum())
    result = pd.concat([macro, env, rec], ignore_index=True)
    result['block'] = 'forecast'
    for metric in ('mse', 'logloss_bit', 'brier'):
        result[f'gain_{metric}'] = result[f'age_only_{metric}'] - result[f'history_{metric}']
    return result.to_dict('records')


def self_test():
    origin = 1_700_000_000_000_000_000
    clock = origin + np.array([100, 400, 800, 1000, 1200, 1500, 1700, 1900], dtype=np.int64) * 10**6
    initial = np.zeros((2, 3))
    truth = np.zeros((len(clock), 2, 3))
    truth[:, :, 0] = np.array([1, 2, 2, 2, 2, 2, 3, 4])[:, None]
    w = PairedWindow('synthetic', 'A', clock, origin + 2 * SECOND, origin, initial, truth, np.ones(2))
    calls = []

    def receive(source):
        calls.append(source)
        return w.receive(source)

    pred, bill = replay(clock, initial, 500, receive, request_not_before_ns=origin + SECOND)
    assert bill == (1, 1, 2, .5) and calls == [origin + SECOND]
    np.testing.assert_array_equal(pred[:5], np.broadcast_to(initial, (5, 2, 3)))
    np.testing.assert_array_equal(pred[5:], np.broadcast_to(truth[3], (3, 2, 3)))

    def forbidden(_):
        raise AssertionError('Unrequested packet disclosed')

    _, bill = replay(clock, initial, 0, forbidden)
    assert bill == (0, 0, 0, 0)
    score = score_reference(w)
    assert abs(score['base_total_both'] - 1.6) < EPS  # error == 1 is inside
    assert abs(score['packet_total_both'] - 1.2) < EPS
    # Receipt in the middle of a reference interval is split exactly.
    irregular = PairedWindow('s', 'A', np.delete(clock, 5), w.mask_end_ns, origin,
                             initial, np.delete(truth, 5, axis=0), np.ones(2))
    assert abs(score_reference(irregular)['packet_total_both'] - 1.2) < EPS
    changed = PairedWindow('s', 'A', clock, w.mask_end_ns, origin, initial, truth.copy(), np.ones(2))
    changed.truth[3:] += 10
    revised, _ = replay(clock, initial, 500, changed.receive, request_not_before_ns=origin + SECOND)
    np.testing.assert_array_equal(revised[:5], pred[:5])
    assert not np.array_equal(revised[5:], pred[5:])
    history = np.stack([initial, initial + np.array([2., 0., 0.])])
    assert history_feature(history, np.ones(2)) == 1
    assert history_feature(history / 2, np.ones(2)) == 0

    training = pd.DataFrame(dict(f=[0, 0, 1, 1], g=[.2, .2, .4, .4],
                                 mu=[1., 1., 2., 2.], z=[1, 2, 2, 2]))
    model = fit_model(training)
    assert decide_one(1, model) == dict(gain=True, mean=True, risk=True, entropy=False)
    assert decide_one(0, model) == dict(gain=False, mean=False, risk=False, entropy=True)
    # Verify the potential-loss mixture against a gated chronological replay:
    # no packet callback at all when not selected, including for held-out scoring.
    for f in (0, 1):
        take = decide_one(f, model)['gain']
        arrivals = [origin + 3 * SECOND // 2] if take else []
        timeline = np.union1d(clock, arrivals).astype(np.int64)
        delivery_calls = []

        def gated_receive(source):
            assert take
            delivery_calls.append(source)
            return w.receive(source)

        predictions, bill = replay(timeline, initial, 500 if take else 0, gated_receive,
                                   request_not_before_ns=origin + SECOND)
        assert bill == (int(take), int(take), 2 * int(take), .5 * int(take))
        assert len(delivery_calls) == int(take)
        edges = np.r_[timeline, w.mask_end_ns]
        reference = w.truth[w.clock.searchsorted(timeline, side='right') - 1]
        exceeds = np.linalg.norm(reference - predictions, axis=2) > w.scale
        prefix = 'packet' if take else 'base'
        for j, hand in enumerate(SIDES):
            assert abs(np.diff(edges)[exceeds[:, j]].sum() / SECOND - score[f'{prefix}_total_{hand}']) < EPS
            assert abs(runs_ns(exceeds[:, j], edges).max(initial=0) / SECOND -
                       score[f'{prefix}_longest_{hand}']) < EPS
    flat = fit_model(training.assign(g=.1, mu=1., z=1))
    assert not any(decide_one(0, flat).values()) and not any(decide_one(1, flat).values())
    unseen = fit_model(training[training.f == 0])
    assert not any(decide_one(1, unseen).values())
    assert abs(binary_entropy(.2) - binary_entropy(.8)) < EPS and binary_entropy(.5) == 1
    whole = pd.concat([training.assign(environment='fit'), training.assign(environment='held')])
    def isolated(data):
        return fit_model(data[data.environment != 'held'])
    before = isolated(whole)
    whole.loc[whole.environment == 'held', ['g', 'mu', 'z']] = 100
    assert before == isolated(whole)
    # Actual reference changes affect evaluation, not the model or frozen actions.
    frozen = decide_one(history_feature(history, np.ones(2)), before)
    assert score_reference(w) != score_reference(changed)
    assert frozen == decide_one(history_feature(history, np.ones(2)), before)
    # Any suffix, including many high scores, leaves every earlier decision intact.
    first = [decide_one(f, before) for f in [0, 1]]
    assert [decide_one(f, before) for f in [0, 1, 1, 1]][:2] == first

    base, packet = np.array([2., 4., 6., 8.]), np.array([1., 7., 2., 8.])
    for k in range(5):
        exact = []
        for subset in combinations(range(4), k):
            take = np.zeros(4)
            take[list(subset)] = 1
            exact.append(base + take * (packet - base))
        random = conditional_random(4, k)
        np.testing.assert_allclose(np.mean(exact, axis=0), base + random * (packet - base))
        assert random.sum() == k and 2 * random.sum() == 2 * k
    # Hierarchical aggregation must not pool windows across unequal records.
    example = []
    for record, n, loss in [('a', 1, 10.), ('b', 9, 0.)]:
        row = {k: 0 for k in SUM_FIELDS + MEAN_FIELDS}
        row.update(block='accounting', scope='record', method='no_request', hand='both',
                   record=record, environment='A', records=1, environments=1, windows=n,
                   timestamps=n, total_s=loss, raw_total_s=n * loss)
        example.append(row)
    summary = aggregate_accounting(example)
    assert next(r for r in summary if r['scope'] == 'macro')['total_s'] == 5
    print('Self-test passed: ns receipt/strict 1L, two wrists/one fee, no unrequested callback, '
          'future-label and prefix isolation, frozen two-cell lookups, exact k-of-n expectation, record weights.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT, help='Attachment root; official train only')
    parser.add_argument('--self-test', action='store_true', help='Synthetic only, no input data or output file')
    args = parser.parse_args()
    self_test()
    if args.self_test:
        return
    references, features, audit = load_training(args.root)
    records, forecasts, cells, redundancy = leave_environment_out(references, features)
    redundancy_macro = dict(block='redundancy', scope='macro', **{
        k: sum(r[k] for r in redundancy) for k in redundancy[0]
        if k not in ('block', 'scope', 'environment')})
    rows = aggregate_accounting(records) + aggregate_forecasts(forecasts) + cells + [redundancy_macro] + redundancy + audit
    output = pd.DataFrame(rows)
    output.insert(0, 'split', 'train')
    OUT.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(OUT, index=False, float_format='%.12g', lineterminator='\n')
    print(output.query("block == 'accounting' and scope == 'macro' and hand == 'both'")[
        ['method', 'requests', 'cost', 'total_s', 'longest_s', 'saving_per_packet_s',
         'selection_total_s', 'selection_total_positive', 'selection_total_boot_low', 'selection_total_boot_high',
         'selection_longest_s', 'selection_longest_positive', 'selection_longest_boot_low', 'selection_longest_boot_high']
    ].to_string(index=False))
    print(output.query("block == 'forecast' and scope == 'macro'")[
        ['age_only_mse', 'history_mse', 'gain_mse', 'age_only_logloss_bit', 'history_logloss_bit',
         'gain_logloss_bit', 'age_only_brier', 'history_brier']].to_string(index=False))
    print('Pool: 55 indexed records/16 environments; 28 eligible records/10 environments; '
          '328 windows, 19639 timestamps per hand. All 10 folds fit 9 environments. No extra exclusions.')
    print(f'Python {sys.version.split()[0]}; NumPy {np.__version__}; pandas {pd.__version__}')
    print(f'{OUT.relative_to(OUT.parents[1])}: {len(output)} aggregate rows; SHA256 '
          f'{hashlib.sha256(OUT.read_bytes()).hexdigest()}')


if __name__ == '__main__':
    main()
