"""Cross observed training spell lengths with reference-qualified movements.

AI-assisted data preparation and fixed-action accounting, OpenCode / OpenAI
GPT family, GPT-6.2 Astra routing group, 2026-11-20. Exact release date unavailable.
"""

import argparse
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

if __package__:
    from .c_occlusion_return_clock import events
    from .c_packet_value import SECOND, SIDES, paired_windows
    from .c_q2_diagnostic import FEE
    from .c_records import ROOT, rows, split_records
else:
    from c_occlusion_return_clock import events
    from c_packet_value import SECOND, SIDES, paired_windows
    from c_q2_diagnostic import FEE
    from c_records import ROOT, rows, split_records


def spell_lengths(root):
    durations = defaultdict(list)
    for _, folder, meta in split_records(root, 'train'):
        if folder.parent.name != 'EgoDemo':
            labels = rows(folder/'tables/annotation_bad_frame_pose_hand.csv')
            for e in events(labels):
                if e['both_ends'] and e['onset_occlusion']:
                    durations[meta['environment_id']].append(e['duration_ns'])
    return {env: np.asarray(d, dtype=np.int64) for env, d in durations.items()}


def compare(window, ends, request_index, delay_ms):
    """Evaluate nested masks without using their ends to choose the request time."""
    clock = window.clock
    if (ends <= clock[0]).any() or (ends > window.mask_end_ns).any():
        raise ValueError('Truncated mask outside reference window')
    edges = np.r_[clock, window.mask_end_ns]
    weights = np.maximum(0, np.minimum(ends[:, None], edges[1:]) - clock)
    base = np.linalg.norm(window.truth-window.initial, axis=2) > window.scale
    request = int(clock[request_index])
    arrival = request + delay_ms*10**6
    asked = request < ends
    timely = arrival < ends
    # The policy only sees the source point on receipt. Offline evaluate the
    # difference between its two possible held points only after that time.
    post = np.maximum(0, np.minimum(ends[:, None], edges[1:]) - np.maximum(clock, arrival))
    updated = base
    if timely.any():
        updated = np.linalg.norm(window.truth-window.receive(request), axis=2) > window.scale
    avoided = post @ (base & ~updated)
    induced = post @ (~base & updated)
    base_ns = weights @ base
    saved = avoided-induced
    if not np.array_equal(post.sum(axis=1), np.maximum(0, ends-arrival)):
        raise ValueError('Receipt opportunity disagrees with scoring intervals')
    for j, side in enumerate(SIDES):
        yield dict(side=side, combinations=len(ends), requested=int(asked.sum()), timely=int(timely.sum()),
                   at_end=int((arrival==ends).sum()), late=int((asked & (arrival>ends)).sum()),
                   cost=int(FEE*asked.sum()), helped=int((saved[:, j]>0).sum()), harmed=int((saved[:, j]<0).sum()),
                   timely_tied=int((timely & (saved[:, j]==0)).sum()),
                   pending_in_mask_s=np.clip(ends-request, 0, arrival-request).mean()/SECOND,
                   pending_to_receipt_s=asked.mean()*delay_ms/1000,
                   scored_s=(ends-clock[0]).mean()/SECOND, usable_after_arrival_s=post.sum(axis=1).mean()/SECOND,
                   baseline_s=base_ns[:, j].mean()/SECOND, updated_s=(base_ns[:, j]-saved[:, j]).mean()/SECOND,
                   avoided_s=avoided[:, j].mean()/SECOND, induced_s=induced[:, j].mean()/SECOND,
                   saved_s=saved[:, j].mean()/SECOND)


def tables(root):
    duration_by_env = spell_lengths(root)
    durations = np.concatenate(list(duration_by_env.values()))
    data = []
    for w in paired_windows(root):
        pools = [('empirical', w.clock[0]+durations), ('long', np.array([w.mask_end_ns]))]
        if w.environment in duration_by_env:
            pools.append(('same_environment', w.clock[0]+duration_by_env[w.environment]))
        for pool, ends in pools:
            for timing, index in [('first', 0), ('at_1s', w.clock.searchsorted(w.anchor_ns+SECOND))]:
                for delay in (100, 500):
                    for row in compare(w, ends, index, delay):
                        data.append(dict(pool=pool, timing=timing, delay_ms=delay, environment=w.environment,
                                         record=w.record, reference_windows=1, **row))
    frame = pd.DataFrame(data)
    keys = ['pool','timing','delay_ms','side']
    counts = ['reference_windows','combinations','requested','timely','at_end','late','cost','helped','harmed','timely_tied']
    rules = {k:'mean' for k in frame if k.endswith('_s')} | {k:'sum' for k in counts}
    records = frame.groupby(keys+['environment','record'], as_index=False).agg(rules)
    records['records'] = 1
    environments = records.groupby(keys+['environment'], as_index=False).agg(rules | {'records':'sum'})
    environments['environments'] = 1
    matched = environments[(environments.pool == 'empirical') & environments.environment.isin(duration_by_env)].copy()
    matched['pool'] = 'empirical_on_matched_envs'
    environments = pd.concat([environments, matched], ignore_index=True)
    macro = environments.groupby(keys, as_index=False).agg(rules | {'records':'sum','environments':'sum'})
    macro['scope'], environments['scope'], records['scope'] = 'macro','environment','record'
    return pd.concat([macro, environments, records], ignore_index=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=ROOT)
    p.add_argument('--out', type=Path, default=Path('runs/c_duration_clock/empirical_masks.csv'))
    args = p.parse_args()
    result = tables(args.root)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.out, index=False, float_format='%.12g')
    print(result.query("scope=='macro'").to_string(index=False))
