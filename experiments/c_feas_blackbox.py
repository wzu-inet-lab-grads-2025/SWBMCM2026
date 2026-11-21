"""Q2 训练内浅树/线性概率探针；只发布聚合 CSV，不导出逐窗值或模型。

AI 辅助，2026-11-21，OpenCode，Sonus / Qoder / Remote magpie，
provider magpie，model remote-magpie/qoder/smodel（发布日期未提供）。
固定规格见 research/C题黑盒与不确定性可行性探索.md。无验证/测试调参。
"""

import argparse
import csv
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import resource
import subprocess
import sys
import time

sys.dont_write_bytecode = True
# Small deterministic CPU-only algebra. Set before importing NumPy.
for variable in ('OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'OMP_NUM_THREADS'):
    os.environ[variable] = '1'

import numpy as np
import pandas as pd
import pyarrow

if __package__:
    from .c_packet_value import PairedWindow, replay
    from .c_palm_motion import palm_points
    from .c_palm_scale import SECOND, SIDES, history_scale, lengths
    from .c_q2_select_packet_probe import conditional_random, score_reference
    from .c_q2_select_packet_probe import self_test as packet_self_test
    from .c_q2_windows import qualified_runs, windows, wrist_inputs
    from .c_records import ROOT, split_records
else:
    from c_packet_value import PairedWindow, replay
    from c_palm_motion import palm_points
    from c_palm_scale import SECOND, SIDES, history_scale, lengths
    from c_q2_select_packet_probe import conditional_random, score_reference
    from c_q2_select_packet_probe import self_test as packet_self_test
    from c_q2_windows import qualified_runs, windows, wrist_inputs
    from c_records import ROOT, split_records


PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / 'research/feas_blackbox'
FEATURES = ('left_displacement_L', 'right_displacement_L', 'left_path_L', 'right_path_L')
METHODS = ('constant', 'linear', 'tree')
HANDS = (*SIDES, 'both')
EPS = 1e-12
MAX_DEPTH, MIN_LEAF, RIDGE = 2, 20, 1.0
OUTPUTS = ('support', 'folds', 'forecast', 'reliability', 'accounting', 'contrasts', 'provenance')


class TrainingAudit:
    """Python open guard, not a claim to intercept every native-library I/O."""

    def __init__(self, root):
        self.root = root.resolve()
        self.allowed_indices = {
            self.root / 'Restocking_Tasks_and_Constraints' / name
            for name in ('record_split.csv', 'source_segment_index.csv')
        }
        index = self.root / 'Restocking_Tasks_and_Constraints/source_segment_index.csv'
        with index.open(encoding='utf-8-sig', newline='') as stream:
            entries = list(csv.DictReader(stream))
        self.train_folders = {
            (self.root / 'Raw_Data' / row['dataset'] / row['sample']).resolve()
            for row in entries if row['split'] == 'train'
        }
        self.reads = set(self.allowed_indices)
        self.writes = set()

    def __call__(self, event, args):
        if event != 'open' or not isinstance(args[0], (str, bytes, os.PathLike)):
            return
        path = Path(os.fsdecode(args[0])).resolve()
        mode, flags = args[1:3]
        writing = (isinstance(mode, str) and any(c in mode for c in 'wax+')) or (
            isinstance(flags, int) and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)))
        if writing:
            if path not in {OUT / f'{name}.csv' for name in OUTPUTS}:
                raise PermissionError('Only the seven authorised aggregate CSV files may be written')
            self.writes.add(path)
        elif path.is_relative_to(self.root):
            if path not in self.allowed_indices and not any(path.is_relative_to(p) for p in self.train_folders):
                raise PermissionError('Non-training attachment access forbidden')
            self.reads.add(path)
        elif any(path.is_relative_to(PROJECT / d) for d in ('research', 'runs', 'results')):
            if path not in {OUT / f'{name}.csv' for name in OUTPUTS}:
                raise PermissionError('Existing empirical result access forbidden during fitting')

    def input_digest(self):
        digest = hashlib.sha256()
        for path in sorted(self.reads):
            digest.update(str(path.relative_to(self.root)).encode())
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        return digest.hexdigest()


def history_features(history, scale):
    """Only the pre-mask visible slice and its pre-mask scale enter this API."""
    assert history.ndim == 3 and history.shape[1:] == (2, 3) and len(history) >= 2
    assert scale.shape == (2,) and np.all(scale > 0)
    displacement = np.linalg.norm(history[-1] - history[0], axis=1) / scale
    path = np.linalg.norm(np.diff(history, axis=0), axis=2).sum(axis=0) / scale
    result = np.r_[displacement, path]
    assert result.shape == (4,) and np.isfinite(result).all()
    return result


def load_training(root):
    features, references, audit = [], [], []
    for record, folder, meta in split_records(root, 'train'):
        dataset, environment = folder.parent.name, meta['environment_id']
        count = ticks = 0
        if dataset != 'EgoDemo':
            times, _, wrists, masks = wrist_inputs(folder)
            pairs = np.stack([wrists[s] for s in SIDES], axis=1)
            scales = {}
            for side in SIDES:
                hand = palm_points(folder, side, times)
                scales[side] = lengths(hand[:, 0], hand[:, 2])
            previous_end = -1
            for run in qualified_runs(times, masks['both']):
                for start, first, end in windows(times, run, SECOND, 2 * SECOND):
                    assert start >= previous_end and start < first < end
                    previous_end = end  # Entire history + mask disjoint, not just mask.
                    scale = [history_scale(scales[s][start:first]) for s in SIDES]
                    if any(x is None for x in scale):
                        raise ValueError('The fixed audited window pool lost a history scale')
                    scale = np.asarray(scale)
                    x = history_features(pairs[start:first], scale)
                    w = PairedWindow(record, environment, times[first:end], int(times[start]) + 3 * SECOND,
                                     int(times[first - 1]), pairs[first - 1].copy(), pairs[first:end].copy(), scale)
                    request = w.clock[w.clock.searchsorted(w.anchor_ns + SECOND)]
                    assert request + SECOND // 2 < w.clock[-1]
                    features.append(dict(record=record, environment=environment, ticks=len(w.clock),
                                         request_age_s=(int(request) - w.anchor_ns) / SECOND,
                                         terminal_age_s=(int(w.clock[-1]) - w.anchor_ns) / SECOND,
                                         scale_min_cm=100 * scale.min(), scale_max_cm=100 * scale.max(),
                                         **dict(zip(FEATURES, x))))
                    references.append(w)
                    count += 1
                    ticks += len(w.clock)
        audit.append(dict(record=record, environment=environment, dataset=dataset,
                          indexed_records=1, eligible_records=int(count > 0), windows=count,
                          timestamps_per_hand=ticks,
                          excluded_reason='no_explicit_normal_reference' if dataset == 'EgoDemo' else ''))
    frame = pd.DataFrame(features)
    assert len(audit) == 55 and len({x['environment'] for x in audit}) == 16
    assert len(frame) == len(references) == 328 and frame.record.nunique() == 28
    assert frame.environment.nunique() == 10 and frame.ticks.sum() == 19639
    assert sum(row['windows'] == 0 for row in audit) == 27
    return frame, references, pd.DataFrame(audit)


@dataclass
class Node:
    n: int
    p: float
    feature: int = -1
    threshold: float = 0.0
    left: object = None
    right: object = None

    def predict_one(self, x):
        if self.feature < 0:
            return self.p
        child = self.left if x[self.feature] <= self.threshold else self.right
        return child.predict_one(x)

    def leaf_sizes(self):
        return [self.n] if self.feature < 0 else self.left.leaf_sizes() + self.right.leaf_sizes()

    def depth(self):
        return 0 if self.feature < 0 else 1 + max(self.left.depth(), self.right.depth())


def smoothed_risk(y):
    return (2 * float(np.sum(y)) + .5) / (2 * len(y) + 1)


def fit_tree(x, y, depth=0):
    n = len(y)
    node = Node(n, smoothed_risk(y))
    if depth >= MAX_DEPTH or n < 2 * MIN_LEAF:
        return node
    parent = float(np.sum((y - y.mean()) ** 2))
    best_gain, best = 0., None
    for feature in range(x.shape[1]):
        order = np.argsort(x[:, feature], kind='stable')
        values, labels = x[order, feature], y[order]
        sums, squares = np.cumsum(labels), np.cumsum(labels ** 2)
        for k in range(MIN_LEAF, n - MIN_LEAF + 1):
            if values[k - 1] == values[k]:
                continue
            left_loss = squares[k - 1] - sums[k - 1] ** 2 / k
            right_loss = squares[-1] - squares[k - 1] - (sums[-1] - sums[k - 1]) ** 2 / (n - k)
            gain = parent - left_loss - right_loss
            if gain > best_gain + EPS:
                best_gain = gain
                best = feature, values[k - 1] + (values[k] - values[k - 1]) / 2
    if best is not None:
        node.feature, node.threshold = best
        left = x[:, node.feature] <= node.threshold
        assert min(left.sum(), (~left).sum()) >= MIN_LEAF
        node.left = fit_tree(x[left], y[left], depth + 1)
        node.right = fit_tree(x[~left], y[~left], depth + 1)
    return node


@dataclass
class Model:
    baseline: float
    center: np.ndarray
    scale: np.ndarray
    coef: np.ndarray
    tree: Node

    def predict_one(self, x):
        assert x.shape == (4,) and np.isfinite(x).all()
        design = np.r_[1., (x - self.center) / self.scale]
        return dict(constant=self.baseline, linear=float(np.clip(design @ self.coef, .01, .99)),
                    tree=self.tree.predict_one(x))

    def decide_one(self, x):
        return {method: p > self.baseline + EPS for method, p in self.predict_one(x).items()}


def fit_model(x, y):
    assert x.ndim == 2 and x.shape == (len(y), 4) and len(y) > 0
    assert np.isfinite(x).all() and np.isin(y, [0., .5, 1.]).all()
    center, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale == 0] = 1
    design = np.column_stack([np.ones(len(y)), (x - center) / scale])
    penalty = np.diag([0., RIDGE, RIDGE, RIDGE, RIDGE])
    coef = np.linalg.solve(design.T @ design + penalty, design.T @ y)
    return Model(smoothed_risk(y), center, scale, coef, fit_tree(x, y))


def reference_scores(w):
    """Evaluator-only future reference, not a feature/decision argument."""
    result = score_reference(w)
    binary = np.linalg.norm(w.truth[-1] - w.initial, axis=1) > w.scale
    for j, side in enumerate(SIDES):
        result[f'y_{side}'] = int(binary[j])
    for prefix, delay in (('base', 0), ('packet', 500)):
        predicted, bill = replay(w.clock, w.initial, delay, w.receive,
                                 request_not_before_ns=w.anchor_ns + SECOND)
        assert bill == ((1, 1, 2, .5) if delay else (0, 0, 0, 0))
        error = np.linalg.norm(predicted - w.truth, axis=2)
        for j, side in enumerate(SIDES):
            result[f'{prefix}_error_sum_cm_{side}'] = 100 * error[:, j].sum()
            result[f'{prefix}_relative_sum_{side}'] = error[:, j].sum() / w.scale[j]
        for field in ('error_sum_cm', 'relative_sum'):
            result[f'{prefix}_{field}_both'] = np.mean([result[f'{prefix}_{field}_{s}'] for s in SIDES])
    assert result['z'] == sum(binary)
    return result


def leave_environment_out(features, references):
    cache, predictions, actions, fold_rows = {}, [], [], []

    def labels(indices):
        for i in indices:
            if i not in cache:
                cache[i] = reference_scores(references[i])
        return pd.DataFrame([cache[i] for i in indices], index=indices)

    for environment in sorted(features.environment.unique()):
        train = features[features.environment != environment]
        held = features[features.environment == environment]
        assert train.environment.nunique() == 9 and set(train.record).isdisjoint(held.record)
        assert set(train.index).isdisjoint(held.index)
        train_labels = labels(train.index)
        started = time.perf_counter()
        model = fit_model(train[list(FEATURES)].to_numpy(), train_labels.z.to_numpy() / 2)
        fit_seconds = time.perf_counter() - started
        # Deliberately scalar, in source order. No held labels, top-k, quotas or future rows.
        for i, row in held.iterrows():
            x = row[list(FEATURES)].to_numpy(dtype=float)
            predictions.append(dict(index=i, **model.predict_one(x)))
            actions.append(dict(index=i, **model.decide_one(x)))
        leaves = model.tree.leaf_sizes()
        fold_rows.append(dict(environment=environment, fit_environments=9,
                              fit_records=train.record.nunique(), fit_windows=len(train),
                              fit_positive_wrists=int(train_labels.z.sum()), fit_wrists=2 * len(train),
                              held_records=held.record.nunique(), held_windows=len(held),
                              constant_risk=model.baseline, tree_depth=model.tree.depth(),
                              tree_leaves=len(leaves), smallest_leaf=min(leaves), largest_leaf=max(leaves),
                              held_all_one_class=int(len(np.unique(labels(held.index)[['y_left', 'y_right']])) == 1),
                              fit_seconds=fit_seconds))
    predictions = pd.DataFrame(predictions).set_index('index').sort_index()
    actions = pd.DataFrame(actions).set_index('index').sort_index()
    assert list(predictions.index) == list(features.index) == list(actions.index)
    assert not actions.constant.any()
    return labels(features.index), predictions, actions, pd.DataFrame(fold_rows)


def hierarchical_weights(frame):
    counts = frame.groupby('record').record.transform('size').to_numpy()
    records = frame.groupby('environment').record.transform('nunique').to_numpy()
    weights = 1 / (frame.environment.nunique() * records * counts)
    np.testing.assert_allclose(weights.sum(), 1)
    return weights


def loss_vectors(y, p):
    assert y.ndim == 2 and p.ndim == 1 and len(y) == len(p)
    assert np.isin(y, [0, 1]).all() and np.all((p > 0) & (p < 1))
    brier = np.mean((y - p[:, None]) ** 2, axis=1)
    logloss = np.mean(-y * np.log2(p[:, None]) - (1 - y) * np.log2(1 - p[:, None]), axis=1)
    return brier, logloss


def forecast_tables(features, scores, predictions):
    outputs, reliability = [], []
    groups = [('macro', '', '', features, hierarchical_weights(features)),
              ('window_pool', '', '', features, np.full(len(features), 1 / len(features)))]
    for env, group in features.groupby('environment', sort=True):
        groups.append(('held_environment', env, '', group, hierarchical_weights(group)))
    # Publish environment or larger aggregates only: some records have a single window.
    for scope, environment, record, group, weight in groups:
        for hand in HANDS:
            columns = ['y_left', 'y_right'] if hand == 'both' else [f'y_{hand}']
            y = scores.loc[group.index, columns].to_numpy()
            event = y.mean(axis=1)
            for method in METHODS:
                p = predictions.loc[group.index, method].to_numpy()
                brier, logloss = loss_vectors(y, p)
                bins = np.minimum((p * 5).astype(int), 4)
                ece = 0.
                for b in range(5):
                    use = bins == b
                    mass = weight[use].sum()
                    pred = float(weight[use] @ p[use] / mass) if mass else np.nan
                    rate = float(weight[use] @ event[use] / mass) if mass else np.nan
                    if mass:
                        ece += mass * abs(pred - rate)
                    if scope in ('macro', 'window_pool'):
                        reliability.append(dict(scope=scope, environment=environment, hand=hand, method=method,
                                                bin=b, lower=.2 * b, upper=.2 * (b + 1),
                                                windows=int(use.sum()), wrists=int(use.sum() * y.shape[1]),
                                                positive_wrists=int(y[use].sum()), weight_mass=mass,
                                                mean_probability=pred, event_rate=rate))
                outputs.append(dict(scope=scope, environment=environment, record=record, method=method, hand=hand,
                                    records=group.record.nunique(), environments=group.environment.nunique(),
                                    windows=len(group), wrists=y.size, positive_wrists=int(y.sum()),
                                    single_class=int(len(np.unique(y)) == 1),
                                    event_rate=weight @ event, mean_probability=weight @ p,
                                    calibration_bias=weight @ (p - event), ece_5bin=ece,
                                    brier=weight @ brier, logloss_bit=weight @ logloss,
                                    probability_std=np.sqrt(weight @ (p - weight @ p) ** 2)))
    return pd.DataFrame(outputs), pd.DataFrame(reliability)


ACCOUNT_SUM = ('records', 'windows', 'timestamps_per_hand', 'requests', 'receipts', 'cost', 'pending_s',
               'raw_total_s', 'raw_saved_s', 'raw_avoided_s', 'raw_induced_s', 'no_selection_room_records')
ACCOUNT_MEAN = ('total_s', 'longest_s', 'net_saved_s', 'scored_s', 'mean_error_cm', 'mean_relative_error',
                'selection_total_s', 'selection_longest_s')


def accounting_table(features, scores, actions):
    rows = []
    for record, group in features.groupby('record', sort=True):
        n = len(group)
        potentials = scores.loc[group.index]
        choices = {'no_request': np.zeros(n), 'all_request': np.ones(n)}
        for method in METHODS:
            take = actions.loc[group.index, method].to_numpy(dtype=float)
            choices[method] = take
            choices[f'random_{method}'] = conditional_random(n, int(take.sum()))
        for method, take in choices.items():
            k = int(round(take.sum()))
            assert abs(take.sum() - k) < EPS
            for hand in HANDS:
                b = potentials[f'base_total_{hand}'].to_numpy()
                a = potentials[f'packet_total_{hand}'].to_numpy()
                bl = potentials[f'base_longest_{hand}'].to_numpy()
                al = potentials[f'packet_longest_{hand}'].to_numpy()
                actual, longest = b + take * (a - b), bl + take * (al - bl)
                avoided = take * potentials[f'avoided_{hand}'].to_numpy()
                induced = take * potentials[f'induced_{hand}'].to_numpy()
                row = dict(scope='record', environment=group.environment.iloc[0], record=record,
                           method=method, hand=hand, records=1, environments=1, windows=n,
                           timestamps_per_hand=int(group.ticks.sum()), requests=k, receipts=k,
                           cost=2 * k, pending_s=.5 * k, total_s=actual.mean(), longest_s=longest.mean(),
                           net_saved_s=(b - actual).mean(), raw_total_s=actual.sum(),
                           raw_saved_s=(b - actual).sum(), raw_avoided_s=avoided.sum(),
                           raw_induced_s=induced.sum(), scored_s=potentials.scored_s.mean(),
                           no_selection_room_records=int(k in (0, n)),
                           selection_total_s=np.nan, selection_longest_s=np.nan)
                for source, metric in (('error_sum_cm', 'mean_error_cm'), ('relative_sum', 'mean_relative_error')):
                    base = potentials[f'base_{source}_{hand}'].to_numpy()
                    packet = potentials[f'packet_{source}_{hand}'].to_numpy()
                    row[metric] = (base + take * (packet - base)).sum() / row['timestamps_per_hand']
                if method in METHODS:
                    random = choices[f'random_{method}']
                    row['selection_total_s'] = np.mean((random - take) * (a - b))
                    row['selection_longest_s'] = np.mean((random - take) * (al - bl))
                assert abs(row['raw_saved_s'] - row['raw_avoided_s'] + row['raw_induced_s']) < EPS
                rows.append(row)
    records = pd.DataFrame(rows)
    rule = {k: 'sum' for k in ACCOUNT_SUM} | {k: 'mean' for k in ACCOUNT_MEAN}
    environments = records.groupby(['method', 'hand', 'environment'], as_index=False).agg(rule)
    environments['scope'], environments['environments'] = 'held_environment', 1
    macro = environments.groupby(['method', 'hand'], as_index=False).agg(rule | {'environments': 'sum'})
    macro['scope'] = 'macro'
    result = pd.concat([macro, environments, records], ignore_index=True)
    result['saving_per_packet_s'] = result.raw_saved_s / result.requests.replace(0, np.nan)
    result['request_fraction'] = result.requests / result.windows
    for method in METHODS:
        a = result[result.method == method].sort_values(['scope', 'environment', 'record', 'hand'])
        b = result[result.method == f'random_{method}'].sort_values(['scope', 'environment', 'record', 'hand'])
        np.testing.assert_allclose(a[['requests', 'receipts', 'cost', 'pending_s']],
                                   b[['requests', 'receipts', 'cost', 'pending_s']])
        np.testing.assert_allclose(a.selection_total_s, b.total_s.to_numpy() - a.total_s, atol=EPS)
        np.testing.assert_allclose(a.selection_longest_s, b.longest_s.to_numpy() - a.longest_s, atol=EPS)
    # A one-window record would expose its outcome; publish environment and macro only.
    return result[result.scope != 'record'].reset_index(drop=True)


def support_table(features, scores, audit):
    rows = []
    groups = [('indexed_pool', '', '', audit)]
    groups += [('environment', env, '', g) for env, g in audit.groupby('environment', sort=True)]
    groups += [('record', g.environment.iloc[0], record, g) for record, g in audit.groupby('record', sort=True)]
    for scope, env, record, indexed in groups:
        group = features[features.record.isin(indexed.record)]
        label = scores.loc[group.index]
        row = dict(scope=scope, environment=env, record=record,
                   dataset=indexed.dataset.iloc[0] if indexed.dataset.nunique() == 1 else 'mixed',
                   indexed_records=len(indexed), indexed_environments=indexed.environment.nunique(),
                   eligible_records=group.record.nunique(), eligible_environments=group.environment.nunique(),
                   windows=len(group), timestamps_per_hand=int(group.ticks.sum()),
                   excluded_records=int((indexed.windows == 0).sum()),
                   excluded_reason='no_explicit_normal_reference' if not len(group) else '')
        # Record rows disclose eligibility counts only, never one-window outcomes/features.
        if len(group) and scope != 'record':
            record_classes = []
            zero_records = 0
            for _, record_group in group.groupby('record'):
                record_labels = scores.loc[record_group.index]
                record_classes.append([int(record_labels.y_left.nunique() == 1),
                                       int(record_labels.y_right.nunique() == 1),
                                       int(len(np.unique(record_labels[['y_left', 'y_right']])) == 1)])
                zero_records += int((record_labels.g.abs() <= EPS).all())
            row.update(single_class_left_records=sum(x[0] for x in record_classes),
                       single_class_right_records=sum(x[1] for x in record_classes),
                       single_class_both_records=sum(x[2] for x in record_classes),
                       all_zero_gain_records=zero_records,
                       positive_left=int(label.y_left.sum()), positive_right=int(label.y_right.sum()),
                       wrists=2 * len(group), single_class_left=int(label.y_left.nunique() == 1),
                       single_class_right=int(label.y_right.nunique() == 1),
                       single_class_both=int(len(np.unique(label[['y_left', 'y_right']])) == 1),
                       gain_positive_windows=int((label.g > EPS).sum()),
                       gain_zero_windows=int((label.g.abs() <= EPS).sum()),
                       gain_negative_windows=int((label.g < -EPS).sum()),
                       all_zero_gain=int((label.g.abs() <= EPS).all()),
                       scale_min_cm=group.scale_min_cm.min(), scale_max_cm=group.scale_max_cm.max(),
                       request_age_min_s=group.request_age_s.min(), request_age_max_s=group.request_age_s.max(),
                       terminal_age_min_s=group.terminal_age_s.min(), terminal_age_max_s=group.terminal_age_s.max())
            for name in FEATURES:
                row[f'{name}_min'], row[f'{name}_max'] = group[name].min(), group[name].max()
        rows.append(row)
    return pd.DataFrame(rows)


def contrasts_table(forecast, accounting):
    rows = []
    forecasts = forecast[forecast.scope == 'held_environment']
    accounts = accounting[accounting.scope == 'held_environment']

    def append(kind, hand, candidate, reference, metric, values):
        values = np.asarray(values)
        assert len(values) == 10
        delete_one = (values.sum() - values) / 9
        rows.append(dict(kind=kind, hand=hand, candidate=candidate, reference=reference, metric=metric,
                         environments=10, mean_improvement=values.mean(),
                         improved=int((values > EPS).sum()), worsened=int((values < -EPS).sum()),
                         tied=int((np.abs(values) <= EPS).sum()), environment_min=values.min(),
                         environment_max=values.max(), drop1_min=delete_one.min(), drop1_max=delete_one.max()))

    for hand in HANDS:
        for candidate, reference in (('linear', 'constant'), ('tree', 'constant'), ('tree', 'linear')):
            a = forecasts[(forecasts.hand == hand) & (forecasts.method == candidate)].set_index('environment')
            b = forecasts[(forecasts.hand == hand) & (forecasts.method == reference)].set_index('environment')
            for metric in ('brier', 'logloss_bit'):
                append('forecast', hand, candidate, reference, metric, b[metric] - a[metric])
        for method in METHODS:
            group = accounts[(accounts.hand == hand) & (accounts.method == method)]
            for metric in ('selection_total_s', 'selection_longest_s'):
                append('same_cost_action', hand, method, f'random_{method}', metric, group[metric])
    return pd.DataFrame(rows)


def self_test():
    packet_self_test()
    # Correct wrist-averaged Brier differs from squared fractional-target loss.
    brier, logloss = loss_vectors(np.array([[0, 1]]), np.array([.8]))
    np.testing.assert_allclose(brier, [.34])
    assert abs(brier[0] - (.5 - .8) ** 2 - .25) < EPS
    assert np.isfinite(logloss).all()
    rng = np.random.default_rng(20261121)  # Synthetic tests only; real fit has no randomness.
    x = rng.normal(size=(100, 4))
    y = ((x[:, 0] > 0).astype(float) + (x[:, 1] > 0)) / 2
    model = fit_model(x, y)
    assert model.tree.depth() <= 2 and len(model.tree.leaf_sizes()) <= 4
    assert min(model.tree.leaf_sizes()) >= 20
    # A hand-computable perfect split: 20 windows on each side, Jeffreys leaf risk.
    simple_x = np.zeros((40, 4))
    simple_x[20:, 0] = 1
    simple_y = np.r_[np.zeros(20), np.ones(20)]
    simple = fit_tree(simple_x, simple_y)
    assert simple.feature == 0 and simple.threshold == .5 and simple.depth() == 1
    np.testing.assert_allclose([simple.predict_one(simple_x[0]), simple.predict_one(simple_x[-1])],
                               [.5 / 41, 40.5 / 41])
    for label in (0., 1.):
        single = fit_model(x, np.full(100, label))
        assert single.tree.depth() == 0
        for p in single.predict_one(x[0]).values():
            assert 0 < p < 1
        assert not single.decide_one(x[0])['constant']
    frame = pd.DataFrame(x, columns=FEATURES)
    frame['environment'] = ['fit'] * 80 + ['held'] * 20
    target = y.copy()

    def fitting():
        indices = frame.environment != 'held'
        return fit_model(frame.loc[indices, FEATURES].to_numpy(), target[indices])

    before = fitting()
    target[80:] = 1 - target[80:]
    after = fitting()
    np.testing.assert_array_equal(before.center, after.center)
    np.testing.assert_array_equal(before.coef, after.coef)
    for row in x:
        assert before.predict_one(row) == after.predict_one(row)
    history = rng.normal(size=(30, 2, 3))
    scale = np.array([.1, .12])
    np.testing.assert_allclose(history_features(history, scale), history_features(history * 100, scale * 100))
    whole = np.concatenate([history, rng.normal(size=(60, 2, 3))])
    feature = history_features(whole[:30], scale)
    whole[30:] += 1000
    np.testing.assert_array_equal(feature, history_features(whole[:30], scale))
    times = np.arange(301, dtype=np.int64) * 10**7
    blocks = list(windows(times, np.arange(len(times)), 10**8, 2 * 10**8))
    assert all(a[2] <= b[0] for a, b in zip(blocks, blocks[1:]))
    # Unequal record sizes: weights are record-then-environment equal, not pooled.
    example = pd.DataFrame(dict(environment=['a'] * 10 + ['b'], record=['a1'] + ['a2'] * 9 + ['b1']))
    weights = hierarchical_weights(example)
    np.testing.assert_allclose(weights[[0, 10]], [.25, .5])
    assert np.isclose(weights[1:10].sum(), .25)
    # Entire synthetic environment has zero potential packet gain, but one billable request.
    flat_features = pd.DataFrame(dict(record=['s', 's'], environment=['zero', 'zero'], ticks=[8, 8]))
    flat_scores = pd.DataFrame({'scored_s': [2., 2.]})
    for hand in HANDS:
        for prefix in ('base', 'packet'):
            for metric, value in (('total', 1.), ('longest', 1.), ('error_sum_cm', 8.), ('relative_sum', 8.)):
                flat_scores[f'{prefix}_{metric}_{hand}'] = value
        flat_scores[f'avoided_{hand}'] = flat_scores[f'induced_{hand}'] = 0.
    flat_actions = pd.DataFrame(dict(constant=[False, False], linear=[True, False], tree=[True, False]))
    flat = accounting_table(flat_features, flat_scores, flat_actions)
    assert np.all(flat.raw_saved_s == 0)
    chosen = flat[(flat.scope == 'macro') & flat.method.isin(['linear', 'tree'])]
    assert np.all(chosen.requests == 1) and np.all(chosen.cost == 2)
    assert np.all(chosen.selection_total_s == 0) and np.all(chosen.selection_longest_s == 0)
    print('Blackbox self-test passed: fractional Brier, one-class finite scores, fixed tree, '
          'held-label isolation, units/history isolation, disjoint windows and hierarchical weights.')


def code_digest():
    digest = hashlib.sha256()
    modules = [Path(__file__)]
    for module in list(sys.modules.values()):
        filename = getattr(module, '__file__', None)
        if filename and Path(filename).resolve().parent == PROJECT / 'experiments':
            modules.append(Path(filename))
    for path in sorted(set(p.resolve() for p in modules)):
        digest.update(path.name.encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def run_once(root):
    started = time.perf_counter()
    features, references, audit = load_training(root)
    loaded = time.perf_counter()
    scores, predictions, actions, folds = leave_environment_out(features, references)
    forecast, reliability = forecast_tables(features, scores, predictions)
    accounting = accounting_table(features, scores, actions)
    tables = dict(support=support_table(features, scores, audit), folds=folds.drop(columns='fit_seconds'),
                  forecast=forecast, reliability=reliability, accounting=accounting,
                  contrasts=contrasts_table(forecast, accounting))
    for table in tables.values():
        table.insert(0, 'split', 'train')
    timing = dict(load_seconds=loaded - started, computation_seconds=time.perf_counter() - loaded,
                  fit_seconds=folds.fit_seconds.sum(), total_seconds=time.perf_counter() - started)
    return tables, timing


def csv_bytes(table):
    return table.to_csv(index=False, float_format='%.12g', lineterminator='\n').encode('utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT, help='官方附件根；固定只读 train')
    parser.add_argument('--self-test', action='store_true', help='只跑合成核对，不读附件、不写文件')
    args = parser.parse_args()
    self_test()
    if args.self_test:
        return
    base = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=PROJECT, text=True).strip()
    guard = TrainingAudit(args.root)
    sys.addaudithook(guard)
    started_cpu = time.process_time()
    first, first_time = run_once(args.root)
    second, second_time = run_once(args.root)
    for name in first:
        assert csv_bytes(first[name]) == csv_bytes(second[name]), f'Non-reproducible aggregate: {name}'
    OUT.mkdir(parents=True, exist_ok=True)
    provenance = [dict(kind='runtime', name=f'run_{number}', **timing)
                  for number, timing in enumerate((first_time, second_time), 1)]
    for name, table in first.items():
        payload = csv_bytes(table)
        (OUT / f'{name}.csv').write_bytes(payload)
        provenance.append(dict(kind='aggregate', name=f'{name}.csv', rows=len(table),
                               sha256=hashlib.sha256(payload).hexdigest(), exact_repeat=1))
    metadata = dict(base_commit=base, code_sha256=code_digest(), input_sha256=guard.input_digest(),
                    observed_input_files=len(guard.reads), python=sys.version.split()[0],
                    numpy=np.__version__, pandas=pd.__version__, pyarrow=pyarrow.__version__,
                    cpu_threads=1, gpu_used=0, tuning_runs=0, official_validation_test_reads=0,
                    max_depth=MAX_DEPTH, min_leaf=MIN_LEAF, ridge=RIDGE,
                    cpu_seconds=time.process_time() - started_cpu,
                    peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    provenance += [dict(kind='metadata', name=k, value=v) for k, v in metadata.items()]
    pd.DataFrame(provenance).to_csv(OUT / 'provenance.csv', index=False, float_format='%.12g', lineterminator='\n')
    print(first['forecast'].query("scope == 'macro' and hand == 'both'")[
        ['method', 'windows', 'event_rate', 'mean_probability', 'brier', 'logloss_bit', 'ece_5bin']].to_string(index=False))
    print(first['accounting'].query("scope == 'macro' and hand == 'both'")[
        ['method', 'requests', 'cost', 'pending_s', 'total_s', 'mean_error_cm', 'selection_total_s']].to_string(index=False))
    print(first['contrasts'].query("hand == 'both'").to_string(index=False))
    print(f'Two independent deterministic passes matched all six result CSVs; {len(guard.reads)} '
          f'Python-observed train/index files. Peak RSS {metadata["peak_rss_mib"]:.1f} MiB; '
          f'wall {first_time["total_seconds"]:.3f}/{second_time["total_seconds"]:.3f}s; '
          f'fit {first_time["fit_seconds"]:.4f}/{second_time["fit_seconds"]:.4f}s.')


if __name__ == '__main__':
    main()
