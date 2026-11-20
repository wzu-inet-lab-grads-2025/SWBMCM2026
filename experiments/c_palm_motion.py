"""Training-only wrist translation and palm orientation description.

AI-assisted data preparation and accounting in OpenCode, 2026-11-20, OpenAI
GPT family, GPT-6.2 Astra routing group. Exact release date unavailable.
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

if __package__:
    from .c_palm_scale import SECOND, SIDES, history_scale, lengths
    from .c_q2_windows import qualified_runs, windows, wrist_inputs
    from .c_records import ROOT, split_records
    from .c_state_examples import JOINTS
else:
    from c_palm_scale import SECOND, SIDES, history_scale, lengths
    from c_q2_windows import qualified_runs, windows, wrist_inputs
    from c_records import ROOT, split_records
    from c_state_examples import JOINTS


def palm_points(folder, side, times):
    fields = [f'transforms[{j}].pos.{a}' for j in JOINTS for a in 'xyz']
    frame = pd.read_csv(folder/f'tables/pose_{side}_hand.csv', usecols=['header.ts', *fields],
                        dtype={'header.ts': 'string'})
    if not np.array_equal(times, frame['header.ts'].to_numpy(dtype=np.int64)):
        raise ValueError('Palm and wrist clocks differ')
    return frame[fields].to_numpy(dtype=float).reshape(-1, 5, 3)


def describe(points, scale):
    """First row is the pre-mask anchor, followed by the actual scored ticks."""
    if scale is None or len(points) < 4 or not np.isfinite(points).all() or (points == 0).all(axis=2).any():
        return None
    wrist = points[:, 0]
    index, little = points[:, 1] - wrist, points[:, 4] - wrist
    cross = np.cross(index, little)
    area = np.linalg.norm(cross, axis=1)
    product = np.linalg.norm(index, axis=1) * np.linalg.norm(little, axis=1)
    if np.any(product <= 0) or np.any(area < .1 * product):
        return None
    normal = cross / area[:, None]
    angle = np.rad2deg(np.arctan2(np.linalg.norm(np.cross(normal[0], normal[1:]), axis=1), normal[1:] @ normal[0]))
    peak = np.argmax(angle)
    turn = angle >= 30
    sustained = bool(np.any(turn[:-2] & turn[1:-1] & turn[2:]))
    palm = points.mean(axis=1)
    relative = palm - wrist
    excursion = np.linalg.norm(wrist[1:] - wrist[0], axis=1).max()
    return dict(quiet=bool(excursion <= .5 * scale), turned=sustained, scale_cm=100*scale,
                wrist_max_cm=100*excursion, angle_max_deg=angle[peak], peak_offset=int(peak+1),
                palm_max_cm=100*np.linalg.norm(palm[1:] - palm[0], axis=1).max(),
                palm_relative_max_cm=100*np.linalg.norm(relative[1:] - relative[0], axis=1).max())


def training(root):
    details = []
    for record, folder, meta in split_records(root, 'train'):
        dataset = folder.parent.name
        if dataset == 'EgoDemo':  # No explicit-normal qualification for the existing paired pool.
            continue
        times, _, _, masks = wrist_inputs(folder)
        hands = {side: palm_points(folder, side, times) for side in SIDES}
        for run in qualified_runs(times, masks['both']):
            for start, first, end in windows(times, run, SECOND, 2*SECOND):
                anchor = first-1
                stop = min(end, times.searchsorted(times[anchor] + SECOND//2, side='right'))
                for side, points in hands.items():
                    history = points[start:first]
                    scale = history_scale(lengths(history[:, 0], history[:, 2]))
                    result = describe(points[anchor:stop], scale)
                    row = dict(dataset=dataset, environment=meta['environment_id'], record=record, side=side,
                               folder=str(folder), anchor=int(anchor), anchor_ns=int(times[anchor]), valid=result is not None)
                    if result is not None:
                        peak = anchor + result.pop('peak_offset')
                        row.update(result, peak_ns=int(times[peak]), peak=int(peak))
                    details.append(row)
    return pd.DataFrame(details)


def summarize(details):
    for side, hand in details.groupby('side'):
        groups = [('all', 'all', hand)]
        for field in ('dataset', 'environment', 'record'):
            groups.extend((field, key, group) for key, group in hand.groupby(field))
        for scope, name, group in groups:
            usable = group[group.valid]
            quiet = usable[usable.quiet.eq(True)]
            cases = quiet[quiet.turned.eq(True)]
            record_rates = quiet.groupby(['environment', 'record']).turned.mean()
            rate = record_rates.groupby(level='environment').mean().mean()
            row = dict(scope=scope, name=name, side=side, pool_windows=len(group), usable=len(usable),
                       quiet=len(quiet), quiet_and_turned=len(cases), records=usable.record.nunique(),
                       quiet_records=quiet.record.nunique(), case_records=cases.record.nunique(),
                       environments=usable.environment.nunique(), quiet_environments=quiet.environment.nunique(),
                       case_environments=cases.environment.nunique(), macro_fraction_given_quiet=rate,
                       cases_with_quiet_palm_center=int((cases.palm_max_cm <= .5*cases.scale_cm).sum()))
            if scope == 'all':
                for column in ('wrist_max_cm', 'palm_max_cm', 'palm_relative_max_cm', 'angle_max_deg'):
                    row[f'case_median_{column}'] = cases[column].median()
            yield row


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--out', type=Path, default=Path('runs/c_palm_motion'))
    args = parser.parse_args()
    details = training(args.root)
    output = pd.DataFrame(summarize(details))
    args.out.mkdir(parents=True, exist_ok=True)
    output.to_csv(args.out/'summary.csv', index=False, float_format='%.9g')
    cases = details[details.valid & details.quiet.eq(True) & details.turned.eq(True)]
    # Retain only the first matching sample per source for local visual inspection.
    cases.groupby('dataset', sort=False).head(1).to_csv(args.out/'examples_private.csv', index=False)
    print(output.query("scope == 'all' or scope == 'dataset'").to_string(index=False))
