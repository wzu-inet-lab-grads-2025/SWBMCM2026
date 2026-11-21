"""Compare fixed image-change measures at true C segment boundaries and paired interior controls."""

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter
from decimal import Decimal
from fractions import Fraction
from functools import lru_cache
import json
import os
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

if __package__:
    from .c_dense_semantic_clock import BASE, ROOT, RUN, NS, H, W, measure
    from .c_full_video_decode import HEAD, WRISTS, write_csv
else:
    from c_dense_semantic_clock import BASE, ROOT, RUN, NS, H, W, measure
    from c_full_video_decode import HEAD, WRISTS, write_csv


RADIUS = 0.20
OFFSET = 0.60
REPS = 3999
SEED = 20261121
COLS = ['local', 'raw', 'shift', 'head_omega', 'appearance', 'single_local']
VIEWS = HEAD + WRISTS


@lru_cache(maxsize=4)
def head_omega(folder, view, times_length):
    if view not in HEAD:
        return np.full(times_length - 1, np.nan)
    side = view.split('_')[1]
    if folder.parent.name == 'EgoDemo':
        k = f'observation.state.head_{side}_camera_rotation'
        q = np.asarray(pq.read_table(next((folder / 'data').rglob('*.parquet')),
                                     columns=[k], use_threads=False)[k].to_pylist()).reshape(-1, 4)
    else:
        stem = 'pose_headcam' if side == 'left' else 'pose_right_eye_cam'
        fields = ['transforms[0].quat.' + a for a in 'wxyz']
        q = pd.read_csv(folder / f'tables/{stem}.csv', usecols=fields)[fields].to_numpy()
    if len(q) != times_length or not np.isfinite(q).all():
        raise ValueError('Unexpected head orientation coverage')
    q = q / np.linalg.norm(q, axis=1)[:, None]
    return np.rad2deg(2 * np.arccos(np.clip(np.abs((q[:-1] * q[1:]).sum(axis=1)), 0, 1)))


class Windows:
    """Integrate piecewise-constant frame-pair evidence over exact elapsed seconds."""

    def __init__(self, t, features, appearance):
        self.t = t
        self.features = features
        self.y = np.column_stack([features, appearance[:-1].reshape(len(t)-1, -1)])
        increments = self.y * np.diff(t)[:, None]
        self.integral = np.vstack((np.zeros(self.y.shape[1]), np.cumsum(increments, axis=0)))

    def at(self, t):
        j = np.clip(self.t.searchsorted(t, side='right')-1, 0, len(self.t)-2)
        return self.integral[j] + self.y[j] * (t-self.t[j])[:, None]

    def score(self, centers, outside=None, right_frames=None):
        c = np.asarray(centers, dtype='float64')
        lo, mid, hi = self.at(c-RADIUS), self.at(c), self.at(c+RADIUS)
        means = (hi[:, :4]-lo[:, :4]) / (2*RADIUS)
        change = ((hi[:, 4:]-mid[:, 4:]) - (mid[:, 4:]-lo[:, 4:])) / RADIUS
        tiles = np.abs(change.reshape(-1, 3, 48)).mean(axis=1)
        appearance = np.quantile(tiles, .90, axis=1)
        j = np.clip(self.t.searchsorted(c, side='left') if right_frames is None else right_frames, 1, len(self.t)-1)
        out = np.column_stack([means, appearance, self.features[j-1, 0]])
        if outside is None:
            outside = (c-RADIUS < self.t[0]) | (c+RADIUS > self.t[-1])
        out[outside] = np.nan
        return out

    def verify_window(self, center, saved, indices):
        # Direct intersections, independent of prefix sums. Equality at a single point has zero duration.
        lo, hi = indices
        if lo < 0 or hi >= len(self.t):
            return
        first, last = self.t[lo:hi], self.t[lo+1:hi+1]
        left = np.maximum(0., np.minimum(last,center)-np.maximum(first,center-RADIUS))
        right = np.maximum(0., np.minimum(last,center+RADIUS)-np.maximum(first,center))
        v = self.y[lo:hi]
        means = np.average(v[:, :4], weights=left+right, axis=0)
        change = np.average(v[:, 4:], weights=right, axis=0) - np.average(v[:, 4:], weights=left, axis=0)
        app = np.quantile(np.abs(change.reshape(3,48)).mean(axis=0), .90)
        if not np.allclose(saved[:5], [*means, app], rtol=1e-8, atol=1e-8, equal_nan=True):
            raise ValueError('Time integration differs from direct overlapped paired frames')


def verify_design():
    rng = np.random.default_rng(SEED)
    img = rng.integers(0, 180, (3,H,W), dtype=np.uint8)
    prior, spec, _, _ = measure(img, None, None)
    shifted = np.roll(img, (2,-3), axis=(-2,-1))+10
    _, _, _, (res, dx, dy, raw, light) = measure(shifted, prior, spec)
    if (dx, dy, light) != (-3, 2, 10) or raw <= 0 or res.any():
        raise ValueError('Known camera translation/exposure failed')
    # One changed tile under stationary camera should survive compensation.
    changed = img.copy()
    changed[0, 8:24, 8:24] += 20
    _, _, _, (res, dx, dy, _, light) = measure(changed, prior, spec)
    if (dx, dy, light) != (0, 0, 0) or res[0] != 20 or res[1:].any():
        raise ValueError('Local tile residual failed')
    t = np.array([0., .14, .35, .50, .9, 1.0])
    motion = np.ones((5, 4))
    motion[2] = 2
    color = np.zeros((6, 3, 48))
    color[3:] = 12
    w = Windows(t, motion, color)
    exact = w.score([.5])[0]
    if not np.allclose(exact, [1.375]*4 + [12., 2.]):
        raise ValueError('Time weighting, appearance step or exact-boundary crossing failed')


def check_mapping(z, meta):
    # Direct per-span comparisons to exact Decimal timestamps, independent of bisection.
    if meta['source_origin_ns'] is None:
        t = np.array([Decimal.from_float(float(x)) for x in z['source_stamp']], dtype=object)
    else:
        origin = meta['source_origin_ns']
        t = np.array([Decimal(int(x)-origin)/NS for x in z['source_stamp']], dtype=object)
    unique = np.full(len(t), -1, dtype='int16')
    closed, start, end = np.zeros((3, len(t)), dtype='int16')
    for i, unit in enumerate(meta['segments']):
        a, b = Decimal(unit['start_s']), Decimal(unit['end_s'])
        keep = (a <= t) & (t < b)
        if np.any(unique[keep] != -1):
            raise ValueError('Overlapping half-open membership')
        unique[keep] = i
        closed += (a <= t) & (t <= b)
        start += t == a
        end += t == b
    for key, values in [('segment', unique), ('closed_count', closed), ('exact_start', start), ('exact_end', end)]:
        if not np.array_equal(z[key], values):
            raise ValueError('Independent per-frame membership disagrees on ' + key)


def event_rows(meta, z, w, exact):
    owner, t, units = z['segment'], w.t, meta['segments']
    offset, radius = Fraction(str(OFFSET)), Fraction(str(RADIUS))
    def indices(epochs):
        return [(bisect_right(exact, e-radius)-1, bisect_left(exact, e+radius)) for e in epochs]
    def score_at(epochs, ids):
        outside = np.array([lo < 0 or hi >= len(t) for lo, hi in ids])
        return w.score(epochs, outside, [bisect_left(exact, e) for e in epochs])
    base = {k: meta[k] for k in ('file_id', 'record_id', 'view', 'dataset', 'split', 'environment_id')}
    out = []
    for i in range(1, len(units)):
        a, b = units[i-1], units[i]
        c = Fraction(b['start_s'])
        cross = int(np.flatnonzero(owner == i)[0])
        if cross != bisect_left(exact, c) or owner[cross-1] != i-1:
            raise ValueError('Crossing pair disagrees with exact semantic ownership')
        left_length = Fraction(a['end_s'])-Fraction(a['start_s'])
        right_length = Fraction(b['end_s'])-Fraction(b['start_s'])
        pseudo_segment = i-1 if left_length >= right_length else i
        unit = units[pseudo_segment]
        midpoint = (Fraction(unit['start_s'])+Fraction(unit['end_s']))/2
        for protocol, center in [('true', c), ('placebo', midpoint)]:
            epochs = [center+lag for lag in (-offset, -offset/2, 0, offset/2, offset)]
            ids = indices(epochs)
            valid = all(lo >= 0 and hi < len(t) for lo, hi in ids)
            if valid:
                expected = [(i-1, i-1), (i, i)] if protocol == 'true' else [(pseudo_segment, pseudo_segment)]*2
                for (lo, hi), (first, last) in zip((ids[0], ids[-1]), expected):
                    valid &= owner[lo] == first and owner[hi] == last
            scores = score_at(epochs, ids)
            cj = bisect_left(exact, center)
            local_dt = exact[cj] - exact[cj-1]
            jitter_epochs = [center-local_dt, center+local_dt]
            jitter_ids = indices(jitter_epochs)
            jitter = score_at(jitter_epochs, jitter_ids)
            if valid:
                for epoch, score, support in zip(epochs+jitter_epochs, np.vstack((scores, jitter)), ids+jitter_ids):
                    w.verify_window(float(epoch), score, support)
            # Return the status and locations even when a control overlaps an endpoint.
            row = dict(**base, source_segment_index=i, left_unit_id=a['unit_id'], right_unit_id=b['unit_id'],
                       pseudo_unit_id=unit['unit_id'], boundary_s=float(c), crossing_frame=cross,
                       first_right_delay_ms=float((exact[cross]-c)*1000), bracket_ms=float((exact[cross]-exact[cross-1])*1000),
                       protocol=protocol, center_s=float(center), jitter_step_ms=float(local_dt*1000), valid=bool(valid))
            for epoch, (lo, hi), values in zip(('before', 'lag_m030', 'center', 'lag_p030', 'after'), ids, scores):
                row[epoch + '_first_frame'], row[epoch + '_last_frame'] = lo, hi
                row.update({epoch + '_' + k: v for k, v in zip(COLS, values)})
            for label, support, values in zip(('jitter_minus','jitter_plus'), jitter_ids, jitter):
                row[label+'_first_frame'], row[label+'_last_frame'] = support
                row.update({label + '_' + k: v for k, v in zip(COLS, values)})
            out.append(row)
    return out


def scan(meta, z, frame_dir, root):
    check_mapping(z, meta)
    t, owner, closed = z['source_s'], z['segment'], z['closed_count']
    n, dt = len(t), np.diff(t)
    if (np.any(dt <= 0) or not np.isfinite(z['appearance']).all() or
            not np.isfinite(z['residual'][1:]).all() or not np.isnan(z['residual'][0]).all()):
        raise ValueError('Invalid feature or clock coverage')
    q90 = np.quantile(z['residual'][1:], .90, axis=1)
    shift = np.hypot(z['dx'][1:], z['dy'][1:])
    angle = head_omega(root / 'Raw_Data' / meta['dataset'] / meta['sample'], meta['view'], n)
    features = np.column_stack([q90 / (30*dt), z['raw_mad'][1:] / (30*dt), shift / dt, angle / dt])
    w = Windows(t, features, z['appearance'])
    if meta['source_origin_ns'] is None:
        exact = [Fraction(float(x)) for x in z['source_stamp']]
    else:
        exact = [Fraction(int(x)-meta['source_origin_ns'], NS) for x in z['source_stamp']]
    radius = Fraction(str(RADIUS))
    outside = np.ones(n, dtype=bool)
    outside[bisect_left(exact, exact[0]+radius):bisect_right(exact,exact[-1]-radius)] = False
    middle = w.score(t, outside, np.arange(n))
    base = {k: meta[k] for k in ('file_id', 'record_id', 'view', 'dataset', 'split', 'environment_id')}
    units = meta['segments']
    unit_ids = np.array([s['unit_id'] for s in units]+[None], dtype=object)[owner]
    relation = np.select([owner[1:] < 0, owner[1:] == owner[:-1]], ['outside', 'within'], default='cross_segment')
    relation[(owner[:-1] >= 0) & (owner[1:] < 0)] = 'leave'
    boundaries = np.array([float(u['start_s']) for u in units[1:]])
    mid = (t[:-1]+t[1:])/2
    if len(boundaries):
        right = np.searchsorted(boundaries, mid)
        before = boundaries[np.maximum(right-1, 0)]
        after = boundaries[np.minimum(right, len(boundaries)-1)]
        nearest = np.where(abs(mid-before) <= abs(mid-after), before, after)
        signed_distance = mid - nearest
        bins = np.select([abs(signed_distance)<=RADIUS, abs(signed_distance)<=OFFSET],
                         ['within_le_0p2s','within_0p2_to_0p6s'], default='within_gt_0p6s')
    else:
        signed_distance = np.full(n-1, np.nan)
        bins = np.repeat('within_single_segment_record', n-1)
    bins = np.where(relation=='within', bins, relation)
    frames = pd.DataFrame(dict(frame_index=np.arange(n), unit_id=unit_ids, source_segment_index=owner,
        source_s=t, pts=z['pts'], closed_count=closed, exact_start=z['exact_start'], exact_end=z['exact_end'],
        pts_segment=z['pts_segment'], pair_type=np.concatenate((['initial'], relation)),
        pair_mid_minus_nearest_internal_s=np.r_[np.nan, signed_distance],
        pair_band=np.concatenate((['initial'], bins)),
        pair_q90=np.r_[np.nan, q90], pair_raw_mad=z['raw_mad'], dx=z['dx'], dy=z['dy'],
        median_luma_shift=z['exposure'], pair_head_omega=np.r_[np.nan, features[:, 3]],
        **{f'window_{key}': middle[:, j] for j, key in enumerate(COLS)}))
    frame_path = frame_dir / f"view_{meta['file_id']:03d}.parquet"
    frames.to_parquet(frame_path, index=False)
    os.chmod(frame_path, 0o600)
    segment_views = []
    for i, unit in enumerate(units):
        idx = np.flatnonzero(owner == i)
        within = idx[1:]
        segment_views.append(dict(**base, episode_uuid=meta['episode_uuid'], **unit, first_frame=int(idx[0]),
               last_frame=int(idx[-1]), frames=len(idx), within_pairs=len(within),
               mean_within_q90=np.mean(q90[within-1]), mean_within_raw_mad=np.mean(z['raw_mad'][within])))
    counts = Counter(frames.pair_type)
    pt = z['pts_segment']
    covered = owner >= 0
    tail = np.flatnonzero(~covered)
    if len(tail) and not np.array_equal(tail, np.arange(tail[0], n)):
        raise ValueError('Unexpected internal uncovered sample')
    row = dict(**base, segments=len(units), ticks=n, covered=int(covered.sum()), uncovered=int((~covered).sum()),
               closed_unique=int(np.count_nonzero(closed)), closed_memberships=int(closed.sum()),
               shared_exact=int(np.count_nonzero(closed==2)), final_exact=int(np.count_nonzero((~covered)&z['exact_end'])),
               initial=counts['initial'], within=counts['within'], cross_segment=counts['cross_segment'], leave=counts['leave'],
               outside_pairs=counts['outside'], pts_changed=int(np.count_nonzero(owner!=pt)),
               pts_in_out_switched=int(np.count_nonzero(covered!=(pt>=0))),
               every_window_full=int(np.isfinite(middle[:, 0]).sum()),
               nonzero_shift_pairs=int(np.count_nonzero(shift)),
               search_limit_pairs=int(np.count_nonzero((abs(z['dx'][1:])==8)|(abs(z['dy'][1:])==8))))
    if n != sum(counts.values()) or len(units)-1 != row['cross_segment'] or row['initial'] != 1:
        raise ValueError('Adjacent-pair accounting error')
    return row, segment_views, event_rows(meta, z, w, exact)


def all_pairs(files, frame_dir):
    frames = []
    for f in files.itertuples():
        cols = ['source_s', 'pair_band', 'pair_q90', 'pair_raw_mad', 'dx', 'dy', 'pair_head_omega']
        x = pd.read_parquet(frame_dir / f'view_{f.file_id:03d}.parquet', columns=cols)
        dt = x.source_s.diff()
        x['local'], x['raw'], x['shift'] = x.pair_q90 / (30*dt), x.pair_raw_mad / (30*dt), np.hypot(x.dx, x.dy)/dt
        x['view'] = f.view
        frames.append(x.iloc[1:][['view', 'pair_band', 'local', 'raw', 'shift', 'pair_head_omega']])
    data = pd.concat(frames, ignore_index=True)
    if len(data) != files.ticks.sum()-len(files):
        raise ValueError('Per-frame context ledger lost an adjacent pair')
    out = []
    for v, g in data.groupby('view'):
        for band, f in [('all', g), *g.groupby('pair_band')]:
            row = dict(view=v, pair_band=band, frame_pairs=len(f))
            for k in ('local', 'raw', 'shift', 'pair_head_omega'):
                if f[k].notna().any():
                    row.update({k+'_mean': f[k].mean(), k+'_p50': f[k].median(), k+'_p90': f[k].quantile(.90)})
            if band == 'all':
                rank = f[['local','raw','shift','pair_head_omega']].rank()
                corr = rank.corr()
                for field in ('raw', 'local'):
                    for control in ('shift', 'pair_head_omega'):
                        row[f'rho_{field}_{control}'] = corr.loc[field, control]
            out.append(row)
    return out


def coverage_table(files):
    out = []
    fields = ['ticks', 'covered', 'uncovered', 'closed_unique', 'closed_memberships', 'shared_exact', 'final_exact',
              'initial', 'within', 'cross_segment', 'leave', 'outside_pairs', 'pts_changed', 'pts_in_out_switched',
              'every_window_full', 'nonzero_shift_pairs', 'search_limit_pairs']
    for by in [None, 'dataset', 'split', 'view']:
        groups = [('all', files)] if by is None else files.groupby(by, sort=True)
        for group, g in groups:
            unique = g.drop_duplicates('record_id')
            row = dict(scope=by or 'overall', group=group, streams=len(g), records=len(unique),
                       unique_segments=int(unique.segments.sum()), segment_views=int(g.segments.sum()))
            for prefix, f in [('frame', g), ('tick', unique)]:
                for k in fields:
                    if prefix == 'tick' and k in ('nonzero_shift_pairs', 'search_limit_pairs'):
                        continue
                    row[prefix+'_'+k] = int(f[k].sum())
            out.append(row)
    return out


def cluster_interval(frame, effect):
    data = frame.assign(_effect=effect).dropna(subset=['_effect'])
    if len(data) == 0:
        return np.nan, np.nan
    env = data.groupby('environment_id')._effect.agg(['sum', 'count']).sort_index()
    if len(env) < 2:
        return np.nan, np.nan
    idx = np.random.default_rng(SEED).integers(len(env), size=(REPS, len(env)))
    draws = env['sum'].to_numpy()[idx].sum(axis=1) / env['count'].to_numpy()[idx].sum(axis=1)
    return tuple(np.quantile(draws, [.025, .975]))


def paired_views(events, views):
    g = events[events.view.isin(views)].copy()
    keys = ['environment_id', 'record_id', 'source_segment_index', 'protocol', 'dataset', 'split']
    gb = g.groupby(keys, sort=True)
    if not (gb.size() == len(views)).all():
        raise ValueError('Unpaired simultaneous views')
    positions = ('before', 'lag_m030', 'center', 'lag_p030', 'after', 'jitter_minus', 'jitter_plus')
    cols = ['center_s','jitter_step_ms'] + [f'{pos}_{k}' for pos in positions for k in COLS]
    paired = gb[cols].mean().reset_index()
    paired['valid'] = gb.valid.all().to_numpy()
    return paired


def public_events(events):
    mounts = {view: paired_views(events, (view,)) for view in VIEWS}
    mounts.update(head=paired_views(events, HEAD), wrist=paired_views(events, WRISTS))
    keys = ['record_id', 'source_segment_index']
    four = mounts['wrist'].record_id.unique()
    mounts['head_four_view'] = mounts['head'][mounts['head'].record_id.isin(four)]
    results, lag, contrasts = [], [], []
    for mount, data in mounts.items():
        eligible = data[data.valid]
        real = eligible[eligible.protocol == 'true'].set_index(keys)
        placebo = eligible[eligible.protocol == 'placebo'].set_index(keys)
        same = real.index.intersection(placebo.index)
        motion_balanced = (real[['before_shift','after_shift']].sub(real.center_shift, axis=0).abs().max(axis=1) <= 15)
        groups = [('true', 'all_matched', real), ('true', 'common_placebo', real.loc[same]),
                  ('placebo', 'common_placebo', placebo.loc[same]), ('true', 'motion_balanced', real[motion_balanced])]
        if mount in ('head', 'wrist'):
            groups += [('true', col + '_' + str(s), f) for col in ('dataset', 'split') for s, f in real.groupby(col)]
        for protocol, cohort, frame in groups:
            if len(frame) < 2:
                continue
            for k in COLS:
                values = frame['center_'+k]
                if values.isna().all():
                    continue
                ctrl = (frame['before_'+k] + frame['after_'+k])/2
                d = values - ctrl
                p0, p1 = cluster_interval(frame, d)
                results.append(dict(mount=mount, protocol=protocol, cohort=cohort, metric=k,
                       boundaries=len(frame), records=frame.index.get_level_values(0).nunique(),
                       unique_centers=len(frame.reset_index().drop_duplicates(['record_id','center_s'])),
                       environments=frame.environment_id.nunique(), center_mean=values.mean(), control_mean=ctrl.mean(),
                       paired_mean=d.mean(), ci_low=p0, ci_high=p1, median_difference=d.median(),
                       greater=int((d>0).sum()), equal=int((d==0).sum()), less=int((d<0).sum())))
                if protocol == 'true' and cohort == 'all_matched' and mount in ('head', 'wrist'):
                    for offset, col in zip((-.6, -.3, 0, .3, .6), ('before','lag_m030','center','lag_p030','after')):
                        lag.append(dict(mount=mount, offset_s=offset, metric=k, boundaries=len(frame),
                                        mean=frame[col+'_'+k].mean()))
        for k in COLS:
            if real['center_'+k].isna().all():
                continue
            effects = [(f['center_'+k] - (f['before_'+k]+f['after_'+k])/2).loc[same] for f in (real, placebo)]
            delta = effects[0]-effects[1]
            low, high = cluster_interval(real.loc[same], delta)
            contrasts.append(dict(comparison='true_minus_placebo', mount=mount, metric=k, boundaries=len(same),
                   records=same.get_level_values(0).nunique(), environments=real.loc[same].environment_id.nunique(),
                   paired_difference=delta.mean(), ci_low=low, ci_high=high))
    def effect(f, k):
        return f['center_'+k] - (f['before_'+k] + f['after_'+k])/2
    h, w = (mounts[m].query("valid and protocol == 'true'").set_index(keys).sort_index() for m in ('head_four_view','wrist'))
    if not h.index.equals(w.index):
        raise ValueError('Four-view head/wrist comparison lost synchronization')
    for k in (c for c in COLS if c != 'head_omega'):
        delta = effect(w, k) - effect(h, k)
        low, high = cluster_interval(w, delta)
        contrasts.append(dict(comparison='wrist_minus_head', mount='four_view', metric=k, boundaries=len(w),
              records=w.index.get_level_values(0).nunique(), environments=w.environment_id.nunique(),
              paired_difference=delta.mean(), ci_low=low, ci_high=high))
    return results, lag, contrasts


def event_support(events):
    out = []
    for view, data in events.groupby('view'):
        for proto, g in data.groupby('protocol'):
            v = g[g.valid]
            out.append(dict(view=view, protocol=proto, internal_boundaries=len(g),
                            distinct_centers=len(g.drop_duplicates(['record_id','center_s'])),
                            matched_boundaries=len(v), matched_distinct_centers=len(v.drop_duplicates(['record_id','center_s'])),
                            excluded_window_crosses_endpoint=len(g)-len(v), records=g.record_id.nunique(),
                            matched_records=v.record_id.nunique(), environments=g.environment_id.nunique(),
                            matched_environments=v.environment_id.nunique(),
                            crossing_bracket_ms_median=g.bracket_ms.median() if proto=='true' else None,
                            crossing_bracket_ms_max=g.bracket_ms.max() if proto=='true' else None,
                            first_right_delay_ms_median=g.first_right_delay_ms.median() if proto=='true' else None,
                            first_right_delay_ms_max=g.first_right_delay_ms.max() if proto=='true' else None))
    return out


def jitter_table(events):
    out = []
    for mount, views in [('head', HEAD), ('wrist', WRISTS)]:
        real = paired_views(events, views).query("valid and protocol == 'true'")
        for sign, name in [(-1, 'jitter_minus'), (1, 'jitter_plus')]:
            for feature in COLS:
                v = real[f'{name}_{feature}']
                if v.isna().all():
                    continue
                d = v - real['center_'+feature]
                lo, hi = cluster_interval(real, d)
                out.append(dict(mount=mount, jitter_frames=sign, mean_offset_ms=sign*real.jitter_step_ms.mean(),
                                metric=feature, boundaries=len(real), mean_change=d.mean(), ci_low=lo, ci_high=hi,
                                median_abs_change=d.abs().median(), p95_abs_change=d.abs().quantile(.95)))
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=ROOT)
    p.add_argument('--run-dir', type=Path, default=RUN)
    p.add_argument('--out', type=Path, help='Aggregate CSV directory, defaults to aggregates/ under --run-dir')
    args = p.parse_args()
    args.out = args.out or args.run_dir / 'aggregates'
    os.umask(0o077)
    verify_design()
    run = args.run_dir
    manifest = json.loads((run/'complete.json').read_text())
    (run/'frames').mkdir(exist_ok=True)
    files, segments, events = [], [], []
    cross_view = {}
    for path in sorted((run/'views').glob('view_*.json')):
        meta = json.loads(path.read_text())
        with np.load(path.with_suffix('.npz'), allow_pickle=False) as archive:
            z = {k: archive[k] for k in archive.files}
        clock = tuple(z[k].tobytes() for k in ('source_stamp', 'segment', 'closed_count', 'pts_segment'))
        key = meta['record_id']
        if cross_view.setdefault(key, clock) != clock:
            raise ValueError('Cross-view membership or source clock mismatch')
        f, s, e = scan(meta, z, run/'frames', args.root)
        files.append(f)
        segments.extend(s)
        events.extend(e)
    files, segments, events = map(pd.DataFrame, (files, segments, events))
    cov = coverage_table(files)
    a = cov[0]
    if (manifest != dict(streams=184, frames=178512) or
            (a['streams'], a['records'], a['frame_ticks'], a['tick_ticks'], a['tick_covered'],
             a['tick_shared_exact'], a['unique_segments'], a['tick_cross_segment']) !=
            (184, 69, 178512, 66070, 65765, 213, 349, 280) or segments.unit_id.nunique() != 349 or
            len(segments) != a['segment_views'] or segments.frames.sum() != a['frame_covered'] or
            len(events) != a['frame_cross_segment']*2):
        raise ValueError('Coverage totals differ from full independent audits')
    for name, f in [('streams',files), ('segment_views',segments), ('boundary_views',events)]:
        f.to_parquet(run/(name+'.parquet'), index=False)
    results, lag, contrasts = public_events(events)
    if not args.out.is_dir():
        args.out.mkdir(parents=True, exist_ok=True)
        if not args.out.resolve().is_relative_to(run.resolve()):
            os.chmod(args.out, 0o755)
    aggregates = run/'aggregates'
    aggregates.mkdir(exist_ok=True)
    for name, data in [('coverage', cov), ('support', event_support(events)),
                       ('pairs', all_pairs(files, run/'frames')), ('boundary', results),
                       ('offset', lag), ('jitter', jitter_table(events)), ('contrasts', contrasts)]:
        snapshot = aggregates / f'semantic_{name}.csv'
        write_csv(snapshot, data)
        if args.out.resolve() != aggregates.resolve():
            with tempfile.NamedTemporaryFile(dir=args.out, prefix='semantic_', suffix='.csv', delete=False) as tmp:
                tmp.write(snapshot.read_bytes())
            os.chmod(tmp.name, 0o644)
            os.replace(tmp.name, args.out / snapshot.name)
    print(f"Verified {a['frame_ticks']} frames, {a['tick_ticks']} distinct record times, "
          f"{a['segment_views']} view-segments, {len(events)//2} internal boundary-views")


if __name__ == '__main__':
    main()
