"""Paired training-window exceedance durations and empirical residual spell times.

AI-assisted descriptive tabulation in OpenCode, OpenAI GPT family, GPT-6.2 Astra
routing group, 2026-11-20. Exact release date unavailable.
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

if __package__:
    from .c_occlusion_return_clock import events
    from .c_packet_value import METHODS, SECOND, SIDES, paired_windows, replay
    from .c_records import ROOT, rows, split_records
else:
    from c_occlusion_return_clock import events
    from c_packet_value import METHODS, SECOND, SIDES, paired_windows, replay
    from c_records import ROOT, rows, split_records


def runs_ns(flags, edges):
    starts = np.flatnonzero(flags & ~np.r_[False, flags[:-1]])
    stops = np.flatnonzero(flags & ~np.r_[flags[1:], False]) + 1
    return edges[stops] - edges[starts]


def until_inside_s(flags, edges):
    inside = np.flatnonzero(~flags)
    end = edges[inside[0]] if len(inside) else edges[-1]
    return (end-edges[0])/SECOND


def window_comparison(w, delay_ms, wait_ns):
    request = int(w.clock[w.clock.searchsorted(w.anchor_ns + wait_ns)])
    arrival = request + delay_ms * 10**6
    if arrival >= w.mask_end_ns:
        raise ValueError('This comparison requires an in-window receipt')
    # Zero-order hold of the reference from the first masked sample to the nominal end.
    clock = np.union1d(w.clock, [arrival])
    edges = np.r_[clock, w.mask_end_ns]
    widths = np.diff(edges)
    if (widths <= 0).any():
        raise ValueError('Non-positive scoring interval')
    reference = w.truth[w.clock.searchsorted(clock, side='right')-1]
    predicted, bill = replay(clock, w.initial, delay_ms, w.receive, request_not_before_ns=w.anchor_ns+wait_ns)
    if bill != (1, 1, 2, delay_ms/1000):
        raise ValueError('Unexpected packet accounting')
    base = np.linalg.norm(reference-w.initial, axis=2) > w.scale
    actual = np.linalg.norm(reference-predicted, axis=2) > w.scale
    post = clock >= arrival
    if np.any(base[~post] != actual[~post]):
        raise ValueError('Pre-receipt predictions changed the scores')
    for j, side in enumerate(SIDES):
        b, a = base[:, j], actual[:, j]
        saved = widths[b & ~a].sum()
        added = widths[~b & a].sum()
        baseline, updated = widths[b].sum(), widths[a].sum()
        if baseline-updated != saved-added:
            raise ValueError('Paired exceedance durations do not balance')
        longest_before = runs_ns(b, edges).max(initial=0)
        longest_after = runs_ns(a, edges).max(initial=0)
        idx = int(clock.searchsorted(arrival))
        # In the subset where hold-last is outside at delivery, compare the time
        # until first return inside. An immediately corrected estimate has zero tail.
        ongoing = bool(b[idx])
        tail_before = tail_after = np.nan
        if ongoing:
            tail_before = until_inside_s(b[idx:], edges[idx:])
            tail_after = until_inside_s(a[idx:], edges[idx:])
        yield dict(side=side, windows=1, ticks=len(w.clock), shorter=int(updated < baseline),
                   unchanged=int(updated == baseline), longer=int(updated > baseline),
                   shorter_run=int(longest_after < longest_before), longer_run=int(longest_after > longest_before),
                   scored_s=widths.sum()/SECOND, post_s=widths[post].sum()/SECOND,
                   base_s=baseline/SECOND, updated_s=updated/SECOND, net_saved_s=(saved-added)/SECOND,
                   post_base_s=widths[post & b].sum()/SECOND, post_updated_s=widths[post & a].sum()/SECOND,
                   avoided_s=saved/SECOND, induced_s=added/SECOND, longest_base_s=longest_before/SECOND,
                   longest_updated_s=longest_after/SECOND, outside_at_return=int(ongoing),
                   inside_after_update=int(ongoing and not a[idx]),
                   ongoing_base_s=tail_before, ongoing_updated_s=tail_after)


def duration_summary(root):
    scores = []
    for w in paired_windows(root):
        for method, delay, wait in METHODS[1:]:
            for row in window_comparison(w, delay, wait):
                scores.append(dict(method=method, environment=w.environment, record=w.record, **row))
    frame = pd.DataFrame(scores)
    keys = ['method', 'side']
    counts = ['windows','ticks','shorter','unchanged','longer','shorter_run','longer_run',
              'outside_at_return','inside_after_update']
    rule = {k: 'sum' for k in counts} | {k: 'mean' for k in frame if k.endswith('_s')}
    records = frame.groupby(keys+['environment','record'], as_index=False).agg(rule)
    records['records'] = 1
    envs = records.groupby(keys+['environment'], as_index=False).agg(rule | {'records':'sum'})
    envs['environments'] = 1
    overall = envs.groupby(keys, as_index=False).agg(rule | {'records':'sum', 'environments':'sum'})
    records['scope'], envs['scope'], overall['scope'] = 'record','environment','macro'
    return pd.concat([overall, envs, records], ignore_index=True)


def survival_cells(group):
    complete = group[group.both_ends]
    for age_ms in (0, 100, 250, 500, 750, 1000):
        risk = complete[complete.duration_ns > age_ms*10**6]
        for delay_ms in (100, 250, 500, 1000):
            deadline = (age_ms + delay_ms) * 10**6
            n, later, tie = len(risk), int((risk.duration_ns > deadline).sum()), int((risk.duration_ns == deadline).sum())
            yield dict(h_ms=age_ms, delta_ms=delay_ms, spells=len(group), complete=len(complete),
                       left_censored=int((~group.start_seen).sum()), right_censored=int((~group.end_seen).sum()),
                       at_risk=n, risk_records=risk.record.nunique(), risk_envs=risk.environment.nunique(),
                       before_recovery=later, simultaneous=tie, recovery_first=n-later-tie,
                       fraction_strict=later/n if n else np.nan, fraction_including_ties=(later+tie)/n if n else np.nan)


def natural_summary(root):
    spells = []
    for record, folder, meta in split_records(root, 'train'):
        if folder.parent.name == 'EgoDemo':
            continue
        samples = rows(folder/'tables/annotation_bad_frame_pose_hand.csv')
        for strict, definition in [(False,'onset_occ_until_joint_normal'),(True,'continuous_occ_reason')]:
            for event in events(samples, continuous_occlusion=strict):
                if event['onset_occlusion']:
                    spells.append(dict(definition=definition, record=record, environment=meta['environment_id'], **event))
    results = []
    for definition, group in pd.DataFrame(spells).groupby('definition'):
        groups = [('event_pool', 'all', group)] + [('environment', e, g) for e,g in group.groupby('environment')]
        for scope, name, subset in groups:
            results.extend(dict(definition=definition, scope=scope, name=name, **row) for row in survival_cells(subset))
    return pd.DataFrame(results)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--out', type=Path, default=Path('runs/c_duration_clock'))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    durations = duration_summary(args.root)
    durations.to_csv(args.out/'duration.csv', index=False, float_format='%.12g')
    survival = natural_summary(args.root)
    survival.to_csv(args.out/'survival.csv', index=False, float_format='%.12g')
    print(durations.query("scope=='macro'").to_string(index=False))
    print(survival.query("scope=='event_pool' and delta_ms==500").to_string(index=False))
