"""Fixed train-only Q1 quality-gating feasibility; aggregate output only.

Specification: research/C题质量门控可行性探索.md (written before results).
AI-assisted implementation/checking, OpenCode, 2026-11-21,
Sonus / Qoder / Remote magpie (magpie / remote-magpie/qoder/smodel).
No parameter fitting, held-out data, video, Q3, or row-level output.
"""

import argparse
from collections import Counter
import hashlib
from pathlib import Path
import sys

sys.dont_write_bytecode = True
import numpy as np
import pandas as pd

if __package__:
    from .c_palm_scale import SECOND, SIDES, history_scale, lengths
    from .c_q1_onset_context import install_read_guard, reason_arrays, training_records
    from .c_q2_windows import qualified_runs, wrist_inputs
    from .c_records import ROOT, official_records, rows
else:
    from c_palm_scale import SECOND, SIDES, history_scale, lengths
    from c_q1_onset_context import install_read_guard, reason_arrays, training_records
    from c_q2_windows import qualified_runs, wrist_inputs
    from c_records import ROOT, official_records, rows


GAP_NS, HISTORY_NS = 50_000_000, 200_000_000
METHODS = ('trust', 'joint_skip', 'joint_half', 'local_skip', 'local_half')
SUBGROUPS = ('all', 'own_occlusion', 'own_other_only', 'other_hand_only')
HISTORY_EXCLUSIONS = ('short', 'gap', 'not_normal', 'wrist', 'scale')
COUNTS = (
    'hand_ticks', 'joint_bad_ticks', 'own_bad_ticks', 'unresolved_ticks',
    'bad_spells', 'missing_start', 'missing_return', 'exclude_boundary',
    'exclude_anchor', 'exclude_first_reference', 'anchor_eligible',
    *('exclude_history_' + k for k in HISTORY_EXCLUSIONS), 'normal200_eligible',
    'anchor_bad_ticks', 'anchor_invalid_bad_ticks', 'anchor_own_bad_ticks',
    'anchor_unresolved_ticks', 'anchor_own_occlusion', 'anchor_own_other_only',
    'anchor_other_hand_only', 'anchor_mixed_own_unresolved', 'anchor_own_other_reason',
    'anchor_own_then_ends_unresolved',
)
OUT = Path(__file__).resolve().parents[1] / 'research/feas_quality'


def numeric_valid(xyz):
    return np.isfinite(xyz).all(axis=-1) & (xyz != 0).any(axis=-1)


def natural_spells(times, normal):
    """Recovery is exactly the first adjacent normal point, never a later substitute."""
    for run in qualified_runs(times, ~normal):
        start, end = int(run[0]), int(run[-1]) + 1
        start_seen = start > 0 and normal[start-1] and times[start] - times[start-1] <= GAP_NS
        end_seen = end < len(times) and normal[end] and times[end] - times[end-1] <= GAP_NS
        yield start, end, bool(start_seen), bool(end_seen)


def anchor_qualification(points, start, end, start_seen, end_seen):
    if not (start_seen and end_seen):
        return 'boundary'
    if not numeric_valid(points[start-1]):
        return 'anchor'
    if not numeric_valid(points[end]):
        return 'first_reference'
    return None


def normal_history(times, normal, points, scales, start):
    """Only indices <start; the primary anchor and target have already been checked."""
    anchor = start - 1
    first = int(times.searchsorted(times[anchor] - HISTORY_NS, side='right')) - 1
    if first < 0:
        return None, 'short'
    if np.any(np.diff(times[first:start]) > GAP_NS):
        return None, 'gap'
    if not normal[first:start].all():
        return None, 'not_normal'
    if not numeric_valid(points[first:start]).all():
        return None, 'wrist'
    scale = history_scale(scales[first:start])
    if scale is None:
        return None, 'scale'
    return dict(scale=scale, history_points=start-first,
                history_span_s=(times[anchor] - times[first]) / SECOND), None


def recovery_predictions(anchor, bad_observations, own_bad):
    """Predict THEN reveal each row; recovery coordinates/labels cannot enter here.

    The state transition is identity (hold). All observations are joint-bad;
    own_bad distinguishes localized reasons from unresolved other-hand-only rows.
    All five states receive the same input stream; invalid numbers are skipped
    by every method. Return the predictions BEFORE revealing recovery.
    """
    if not numeric_valid(anchor) or len(bad_observations) != len(own_bad):
        raise ValueError('Invalid initial state or mismatched localized labels')
    state = np.tile(anchor, (len(METHODS), 1)).astype(float)
    for observed, own in zip(bad_observations, own_bad):
        # At this tick the submitted prediction is state.copy(); no scoring
        # reference exists inside a bad segment. Reveal/update only afterwards.
        if not numeric_valid(observed):
            continue
        gains = (1., 0., .5, 0. if own else 1., .5 if own else 1.)
        for i, gain in enumerate(gains):
            if gain == 1:
                state[i] = observed
            elif gain:
                state[i] = (1-gain) * state[i] + gain * observed
    return state


def middle_scales(folder, side, times, wrist):
    columns = [f'transforms[9].pos.{axis}' for axis in 'xyz']
    frame = pd.read_csv(folder / f'tables/pose_{side}_hand.csv',
                        usecols=['header.ts', *columns], dtype={'header.ts': 'string'})
    if not np.array_equal(times, frame['header.ts'].to_numpy(dtype=np.int64)):
        raise ValueError('Middle MCP clock differs from wrist clock')
    return lengths(wrist, frame[columns].to_numpy(dtype=float))


def inspect_record(record, folder, meta):
    times, normal, points, _ = wrist_inputs(folder)
    own, occ, other = reason_arrays(rows(folder / 'tables/annotation_bad_frame_pose_hand.csv'), times, normal)
    spells = list(natural_spells(times, normal))
    units, events = [], []
    for side in SIDES:
        wrist = points[side]
        scales = middle_scales(folder, side, times, wrist)
        c = Counter(hand_ticks=len(times), joint_bad_ticks=int((~normal).sum()),
                    own_bad_ticks=int(own[side].sum()),
                    unresolved_ticks=int((~normal & ~own[side]).sum()), bad_spells=len(spells))
        base = dict(record=record, environment=meta['environment_id'], dataset=folder.parent.name, side=side)
        for start, end, seen_start, seen_end in spells:
            c['missing_start'] += int(not seen_start)
            c['missing_return'] += int(not seen_end)
            excluded = anchor_qualification(wrist, start, end, seen_start, seen_end)
            if excluded:
                c['exclude_' + excluded] += 1
                continue
            c['anchor_eligible'] += 1
            own_flags = own[side][start:end]
            valid = numeric_valid(wrist[start:end])
            subgroup = ('own_occlusion' if occ[side][start:end].any() else
                        'own_other_only' if own_flags.any() else 'other_hand_only')
            c['anchor_' + subgroup] += 1
            c['anchor_bad_ticks'] += end-start
            c['anchor_invalid_bad_ticks'] += int((~valid).sum())
            c['anchor_own_bad_ticks'] += int(own_flags.sum())
            c['anchor_unresolved_ticks'] += int((~own_flags).sum())
            c['anchor_mixed_own_unresolved'] += int(own_flags.any() and not own_flags.all())
            c['anchor_own_other_reason'] += int(other[side][start:end].any())
            c['anchor_own_then_ends_unresolved'] += int(own_flags.any() and not own_flags[-1])
            history, excluded_history = normal_history(times, normal, wrist, scales, start)
            if excluded_history:
                c['exclude_history_' + excluded_history] += 1
            else:
                c['normal200_eligible'] += 1
            # Only the prefix below enters predictions. Reference read follows.
            predictions = recovery_predictions(wrist[start-1].copy(), wrist[start:end].copy(), own_flags.copy())
            errors = np.linalg.norm(predictions - wrist[end], axis=1)
            assert np.isfinite(errors).all()
            # Exact closed-form check for the shared numerical-skip conventions.
            np.testing.assert_array_equal(predictions[0], wrist[start:end][valid][-1] if valid.any() else wrist[start-1])
            np.testing.assert_array_equal(predictions[1], wrist[start-1])
            if not own_flags.any():
                np.testing.assert_array_equal(predictions[3:], np.tile(predictions[0], (2, 1)))
            if own_flags.all():
                np.testing.assert_array_equal(predictions[3:], predictions[[1, 2]])
            # Independent nonrecursive weighted-sum expansion of every update.
            observed = wrist[start:end][valid]
            own_valid = own_flags[valid]
            for k in range(len(METHODS)):
                gains = np.array([(1., 0., .5, 0. if b else 1., .5 if b else 1.)[k]
                                  for b in own_valid])
                weights = np.array([gains[j] * np.prod(1-gains[j+1:]) for j in range(len(gains))])
                expected = wrist[start-1] * np.prod(1-gains) + (observed * weights[:, None]).sum(axis=0)
                np.testing.assert_allclose(predictions[k], expected, atol=1e-12, rtol=1e-12)
            events.append(dict(**base, event=start, subgroup=subgroup,
                               duration_s=(times[end]-times[start]) / SECOND,
                               anchor_to_return_s=(times[end]-times[start-1]) / SECOND,
                               bad_points=end-start, invalid_bad_points=int((~valid).sum()),
                               own_bad_points=int(own_flags.sum()),
                               scale=history['scale'] if history else np.nan,
                               history_points=history['history_points'] if history else np.nan,
                               history_span_s=history['history_span_s'] if history else np.nan,
                               **dict(zip(METHODS, errors))))
        assert c['bad_spells'] == sum(c['exclude_' + k] for k in ('boundary', 'anchor', 'first_reference')) + c['anchor_eligible']
        assert c['anchor_eligible'] == c['normal200_eligible'] + sum(c['exclude_history_' + k] for k in HISTORY_EXCLUSIONS)
        assert c['anchor_eligible'] == sum(c['anchor_' + k] for k in SUBGROUPS[1:])
        assert c['anchor_bad_ticks'] == c['anchor_own_bad_ticks'] + c['anchor_unresolved_ticks']
        units.append(dict(**base, explicit=True, counts=c))
    return units, events


def grouping(units):
    for side in ('both', *SIDES):
        hands = [u for u in units if side == 'both' or u['side'] == side]
        yield 'all', 'train', side, hands
        for field in ('dataset', 'environment', 'record'):
            for name in sorted({u[field] for u in hands}):
                yield field, name, side, [u for u in hands if u[field] == name]


def coverage(units):
    for scope, name, side, members in grouping(units):
        counts = sum((u['counts'] for u in members), Counter())
        row = dict(scope=scope, name=name, side=side,
                   indexed_records=len({u['record'] for u in members}),
                   indexed_environments=len({u['environment'] for u in members}),
                   explicit_records=len({u['record'] for u in members if u['explicit']}),
                   explicit_environments=len({u['environment'] for u in members if u['explicit']}),
                   **{k: int(counts[k]) for k in COUNTS})
        for key in ('anchor', 'normal200'):
            scored = [u for u in members if u['counts'][key + '_eligible']]
            row[key + '_records'] = len({u['record'] for u in scored})
            row[key + '_environments'] = len({u['environment'] for u in scored})
        if scope == 'record':
            row.update(environment=members[0]['environment'], dataset=members[0]['dataset'])
        yield row


def select_events(events, members):
    identities = {(u['record'], u['side']) for u in members}
    return events[[key in identities for key in zip(events.record, events.side)]]


def sign_counts(values):
    return dict(improved=int((values < 0).sum()), worsened=int((values > 0).sum()), tied=int((values == 0).sum()))


def score_group(group, scope, name, side, pool, subgroup, unit, members):
    factor = 100. if unit == 'cm' else 1. / group['scale']
    errors = group[list(METHODS)].mul(factor, axis=0)
    errors['record'], errors['environment'] = group.record, group.environment
    records = errors.groupby(['environment', 'record'])[list(METHODS)].mean()
    environments = records.groupby(level='environment').mean()
    base = dict(scope=scope, name=name, side=side, pool=pool, subgroup=subgroup, unit=unit,
                indexed_records=len({u['record'] for u in members}),
                indexed_environments=len({u['environment'] for u in members}),
                records=group.record.nunique(), environments=group.environment.nunique(),
                hand_events=len(group), unique_spells=len(group[['record', 'event']].drop_duplicates()),
                bad_points=int(group.bad_points.sum()), invalid_bad_points=int(group.invalid_bad_points.sum()),
                own_bad_points=int(group.own_bad_points.sum()),
                duration_median_s=group.duration_s.median(), duration_max_s=group.duration_s.max(),
                anchor_to_return_min_s=group.anchor_to_return_s.min(),
                anchor_to_return_max_s=group.anchor_to_return_s.max(),
                scale_cm_min=100*group.scale.min(), scale_cm_max=100*group.scale.max(),
                history_points_min=group.history_points.min(), history_points_max=group.history_points.max(),
                history_span_min_s=group.history_span_s.min(), history_span_max_s=group.history_span_s.max(),
                joint_half_worse_than_both=int((errors.joint_half > errors[['trust', 'joint_skip']].max(axis=1)).sum()),
                joint_half_better_than_both=int((errors.joint_half < errors[['trust', 'joint_skip']].min(axis=1)).sum()),
                local_half_worse_than_both=int((errors.local_half > errors[['trust', 'local_skip']].max(axis=1)).sum()),
                local_half_better_than_both=int((errors.local_half < errors[['trust', 'local_skip']].min(axis=1)).sum()))
    if scope == 'record':
        base.update(environment=members[0]['environment'], dataset=members[0]['dataset'])
    for method in METHODS:
        diff = errors[method] - errors.trust
        rd = records[method] - records.trust
        ed = environments[method] - environments.trust
        loo = [(ed.sum()-value)/(len(ed)-1) for value in ed] if len(ed) > 1 else []
        row = dict(**base, method=method, event_mean=errors[method].mean(),
                   macro_mean=environments[method].mean(), delta_event_mean=diff.mean(),
                   delta_event_median=diff.median(), delta_macro=ed.mean(),
                   loo_macro_min=min(loo) if loo else np.nan, loo_macro_max=max(loo) if loo else np.nan)
        for level, values in (('events', diff), ('records', rd), ('environments', ed)):
            row.update({key + '_' + level: value for key, value in sign_counts(values).items()})
        yield row


def scores(events, units):
    # All zero-event records/environments remain in the all subgroup. Demo is
    # retained in coverage only: it never supplies explicit-normal references.
    explicit = [u for u in units if u['explicit']]
    for pool, unit in (('anchor', 'cm'), ('normal200', 'cm'), ('normal200', 'L')):
        eligible = events if pool == 'anchor' else events[events.scale.notna()]
        for scope, name, side, members in grouping(explicit):
            if scope == 'dataset':
                continue
            selected = select_events(eligible, members)
            for subgroup in SUBGROUPS:
                group = selected if subgroup == 'all' else selected[selected.subgroup.eq(subgroup)]
                if subgroup != 'all' and not len(group) and scope != 'all':
                    continue
                yield from score_group(group, scope, name, side, pool, subgroup, unit, members)


def leave_environment_out(table):
    selected = table[table.scope.eq('environment') & table.subgroup.eq('all') & table.hand_events.gt(0)]
    for (pool, side, unit, method), group in selected.groupby(['pool', 'side', 'unit', 'method']):
        if len(group) < 2:
            continue
        for row in group.itertuples():
            rest = group[~group.name.eq(row.name)]
            yield dict(pool=pool, side=side, unit=unit, method=method, omitted_environment=row.name,
                       omitted_hand_events=row.hand_events, retained_environments=len(rest),
                       retained_hand_events=int(rest.hand_events.sum()),
                       retained_delta_macro=rest.delta_macro.mean())


def verify_aggregates(cov, table, loo):
    """Independent reconstruction from emitted aggregate levels, including zeros."""
    for side in ('both', *SIDES):
        c = cov[cov.side.eq(side)]
        total = c[c.scope.eq('all')].iloc[0]
        for level in ('dataset', 'record', 'environment'):
            np.testing.assert_array_equal(c[c.scope.eq(level)][list(COUNTS)].sum().to_numpy(),
                                          total[list(COUNTS)].to_numpy())
    for (pool, side, subgroup, unit, method), group in table.groupby(['pool', 'side', 'subgroup', 'unit', 'method']):
        total = group[group.scope.eq('all')].iloc[0]
        rec = group[group.scope.eq('record')]
        env = group[group.scope.eq('environment')]
        assert int(rec.hand_events.sum()) == int(env.hand_events.sum()) == int(total.hand_events)
        if not total.hand_events:
            assert pd.isna(total.macro_mean)
            continue
        active = rec[rec.hand_events.gt(0)]
        expected = active.groupby('environment')[['macro_mean', 'delta_macro']].mean().sort_index()
        actual = env[env.hand_events.gt(0)].set_index('name')[['macro_mean', 'delta_macro']].sort_index()
        np.testing.assert_allclose(actual.values, expected.values, atol=1e-10, rtol=1e-10)
        np.testing.assert_allclose(expected.mean().values, total[['macro_mean', 'delta_macro']].to_numpy(dtype=float),
                                   atol=1e-10, rtol=1e-10)
        micro = (active.event_mean * active.hand_events).sum() / total.hand_events
        assert np.isclose(micro, total.event_mean)
        for label in ('improved', 'worsened', 'tied'):
            assert rec[label + '_events'].sum() == total[label + '_events']
        for key in ('joint_half_worse_than_both', 'joint_half_better_than_both',
                    'local_half_worse_than_both', 'local_half_better_than_both'):
            assert rec[key].sum() == env[key].sum() == total[key]
        for level, denominator in (('events', 'hand_events'), ('records', 'records'), ('environments', 'environments')):
            assert sum(total[label + '_' + level] for label in ('improved', 'worsened', 'tied')) == total[denominator]
        if len(expected) > 1:
            deltas = expected.delta_macro
            deletion = [(deltas.sum()-v)/(len(deltas)-1) for v in deltas]
            np.testing.assert_allclose([min(deletion), max(deletion)], [total.loo_macro_min, total.loo_macro_max],
                                       atol=1e-10, rtol=1e-10)
    for row in loo.itertuples():
        g = table[(table.scope.eq('environment')) & table.pool.eq(row.pool) & table.side.eq(row.side)
                  & table.unit.eq(row.unit) & table.method.eq(row.method) & table.subgroup.eq('all')
                  & table.hand_events.gt(0) & ~table.name.eq(row.omitted_environment)]
        assert np.isclose(g.delta_macro.mean(), row.retained_delta_macro)
    # Recover reference means and pair differences without assuming a winner.
    for _, g in table.groupby(['scope', 'name', 'pool', 'side', 'subgroup', 'unit']):
        trust = g[g.method.eq('trust')].iloc[0]
        np.testing.assert_allclose(g.macro_mean-trust.macro_mean, g.delta_macro,
                                   atol=1e-10, rtol=1e-10, equal_nan=True)
    print('aggregate verification passed: counts, hierarchical means, pairing, signs, environment deletions')


def self_test():
    origin = 1_700_000_000_000_000_000
    t = origin + np.arange(12, dtype=np.int64)*40_000_000
    normal = np.ones(12, dtype=bool)
    normal[[0, 3, 4, 8, 11]] = False
    assert list(natural_spells(t, normal)) == [(0, 1, False, True), (3, 5, True, True),
                                             (8, 9, True, True), (11, 12, True, False)]
    gap = t.copy()
    gap[5:] += 10_000_000
    assert list(natural_spells(gap, normal))[1][-1] is True
    gap[5:] += 1
    assert list(natural_spells(gap, normal))[1][-1] is False
    a = np.array([1., 2., 3.])
    observed = np.array([[5., 2., 3.], [9., 2., 3.]])
    p = recovery_predictions(a, observed, [True, False])
    np.testing.assert_array_equal(p[:, 0], [9., 1., 6., 9., 9.])
    q = recovery_predictions(a, observed, [True, True])
    np.testing.assert_array_equal(q[:, 0], [9., 1., 6., 1., 6.])
    # Same numerical missing-value rule for all policies; single zero axis valid.
    invalid = np.array([[np.nan, 2, 3], [0, 0, 0]])
    np.testing.assert_array_equal(recovery_predictions(a, invalid, [True, True]), np.tile(a, (5, 1)))
    assert numeric_valid(np.array([0., 2., 3.]))
    # Counterexample: partially retaining an early corruption is worse than BOTH
    # endpoints when the last flagged observation has already recovered.
    c = recovery_predictions(a, np.array([[101., 2, 3], a]), [True, True])
    np.testing.assert_array_equal(c[:, 0], [1, 1, 26, 1, 26])
    assert np.linalg.norm(c[2]-a) > np.linalg.norm(c[0]-a) == np.linalg.norm(c[1]-a)
    # Genuine motion in labeled-bad coordinates makes skip worse than trust.
    target = np.array([10., 2, 3])
    assert np.linalg.norm(p[0]-target) < np.linalg.norm(p[1]-target)
    # Future/reference coordinates are sliced OUT of the predictor input.
    full = np.vstack((a, observed, target, target+100))
    baseline = recovery_predictions(full[0], full[1:3], [True, False])
    full[3:] = np.nan
    np.testing.assert_array_equal(recovery_predictions(full[0], full[1:3], [True, False]), baseline)
    # This score is taken BEFORE the recovery value resets all five hold states.
    before_reveal_errors = np.linalg.norm(baseline-target, axis=1)
    reset = np.tile(target, (len(METHODS), 1))
    assert before_reveal_errors[0] > 0 and np.all(np.linalg.norm(reset-target, axis=1) == 0)
    altered = observed + 100
    np.testing.assert_array_equal(recovery_predictions(a, altered, [True, True])[1], a)
    # Do not skip an unusable first normal reference to choose a later good one.
    xyz = np.tile(a, (12, 1))
    xyz[5] = 0
    assert anchor_qualification(xyz, 3, 5, True, True) == 'first_reference'
    assert numeric_valid(xyz[6])
    xyz[5] = a
    normal[:] = True
    scales = np.full(12, .08)
    h, why = normal_history(t, normal, xyz, scales, 8)
    assert why is None and h == dict(scale=.08, history_points=6, history_span_s=.2)
    xyz[8:] = np.nan
    normal[8:] = False
    assert normal_history(t, normal, xyz, scales, 8)[0] == h
    normal[6] = False
    assert normal_history(t, normal, xyz, scales, 8)[1] == 'not_normal'
    normal[:] = True
    assert normal_history(t, normal, xyz, scales, 2)[1] == 'short'
    assert normal_history(gap, normal, xyz, scales, 8)[1] == 'gap'
    xyz[6] = 0
    assert normal_history(t, normal, xyz, scales, 8)[1] == 'wrist'
    xyz[6] = a
    scales[6] = np.nan
    assert normal_history(t, normal, xyz, scales, 8)[1] == 'scale'
    assert anchor_qualification(xyz, 0, 1, False, True) == 'boundary'
    xyz[2] = 0
    assert anchor_qualification(xyz, 3, 5, True, True) == 'anchor'
    # Unequal event/record/environment counts expose accidental pooling.
    fake = []
    for rec, env, n, trust, gate in [('a', 'A', 1, 1., 0.), ('b', 'A', 3, 3., 2.), ('c', 'B', 2, 10., 12.)]:
        for i in range(n):
            fake.append(dict(record=rec, environment=env, side='left', event=i, bad_points=1,
                             invalid_bad_points=0, own_bad_points=1, duration_s=.04, anchor_to_return_s=.08,
                             scale=.1, history_points=6, history_span_s=.2, trust=trust,
                             **{m: gate for m in METHODS[1:]}))
    members = [dict(record='a', environment='A'), dict(record='b', environment='A'), dict(record='c', environment='B')]
    result = list(score_group(pd.DataFrame(fake), 'all', 'train', 'left', 'anchor', 'all', 'cm', members))[1]
    assert result['macro_mean'] == 650 and result['delta_macro'] == 50
    assert result['loo_macro_min'] == -100 and result['loo_macro_max'] == 200
    print('self-test passed: ns boundaries, shared update/missingness, future isolation, first recovery, history, counterexamples, weights')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--check-csv', action='store_true', help='Verify saved aggregates only; no raw data access')
    parser.add_argument('--verify-existing', action='store_true', help='Rerun train and compare bytes without writing')
    args = parser.parse_args()
    self_test()
    if args.self_test:
        return
    if args.check_csv:
        verify_aggregates(*(pd.read_csv(OUT / name) for name in ('coverage.csv', 'scores.csv', 'leave_environment_out.csv')))
        return
    opened, touched = install_read_guard(args.root)
    units, events = [], []
    for record, folder, meta in training_records(args.root):
        u, e = inspect_record(record, folder, meta)
        units.extend(u)
        events.extend(e)
    # Retain official Demo denominators without opening any Demo raw metadata.
    for i, item in enumerate((r for r in official_records(args.root) if r['split'] == 'train'), 1):
        if item['dataset'] == 'EgoDemo':
            for side in SIDES:
                units.append(dict(record=f'T{i:02}', environment=item['environment_id'],
                                  dataset=item['dataset'], side=side, explicit=False, counts=Counter()))
    frame = pd.DataFrame(events)
    if not len(frame):
        raise ValueError('No scored natural events; do not overwrite aggregates')
    cov, table = pd.DataFrame(coverage(units)), pd.DataFrame(scores(frame, units))
    loo = pd.DataFrame(leave_environment_out(table))
    verify_aggregates(cov, table, loo)
    for name, data in (('coverage.csv', cov), ('scores.csv', table), ('leave_environment_out.csv', loo)):
        text = data.to_csv(index=False, float_format='%.12g', lineterminator='\n')
        if args.verify_existing:
            assert (OUT / name).read_bytes() == text.encode('utf-8'), name
        else:
            OUT.mkdir(parents=True, exist_ok=True)
            (OUT / name).write_text(text, encoding='utf-8')
        print(name, 'rows:', len(data), 'SHA256:', hashlib.sha256(text.encode('utf-8')).hexdigest())
    chosen = table[table.scope.eq('all') & table.subgroup.eq('all') & table.unit.eq('cm')]
    print(chosen[['pool', 'side', 'method', 'hand_events', 'records', 'environments', 'macro_mean',
                  'delta_macro', 'improved_environments', 'worsened_environments', 'loo_macro_min', 'loo_macro_max']].to_string(index=False))
    print('read_guard:', dict(opened), 'unique_raw_files:', len(touched))
    print('Python', sys.version.split()[0], 'NumPy', np.__version__, 'pandas', pd.__version__)


if __name__ == '__main__':
    main()
