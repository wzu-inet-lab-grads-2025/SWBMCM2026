"""Check published hand layouts against local schemas and audit a training-only scale.

AI-assisted tabulation, OpenCode / OpenAI GPT family, GPT-6.2 Astra routing
group, 2026-11-20. Exact routed release date unavailable.
"""

import argparse
import csv
import json
from pathlib import Path
import re

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

if __package__:
    from .c_q2_windows import qualified_runs, windows, wrist_inputs
    from .c_records import ROOT, official_records, rows, split_records
else:
    from c_q2_windows import qualified_runs, windows, wrist_inputs
    from c_records import ROOT, official_records, rows, split_records


SIDES = ('left', 'right')
DATASETS = ('EgoDemo', 'EgoPro', 'EgoStandard')
SECOND = 10**9


def check_schemas(root):
    """All splits, reading only headers, feature names and official indices."""
    units = rows(root/'Restocking_Tasks_and_Constraints/source_segment_index.csv')
    locations = {}
    for unit in units:
        location = (unit['dataset'], unit['sample'])
        if locations.setdefault(unit['episode_uuid'], location) != location:
            raise ValueError('Conflicting folders for one episode')
    expected = {(j, axis) for j in range(21) for axis in 'xyz'}
    demo_names = ['Wrist', *[f+j for f in ('Thumb', 'Index', 'Middle', 'Ring', 'Little')
                             for j in ('MCP', 'PIP', 'DIP', 'Tip')]]
    counts = {dataset: 0 for dataset in DATASETS}
    for record in official_records(root):
        dataset, sample = locations[record['episode_uuid']]
        if dataset != record['dataset']:
            raise ValueError('Dataset disagrees with official record index')
        folder = root/'Raw_Data'/dataset/sample
        if dataset == 'EgoDemo':
            features = json.loads((folder/'meta/info.json').read_text())['features']
        for side in SIDES:
            if dataset == 'EgoDemo':
                field = features[f'observation.state.hand_{side}_world']
                if field['shape'] != [21, 3] or field['names'] != [[side+n for n in demo_names], list('xyz')]:
                    raise ValueError(f'Unexpected LeRobot joint order in {folder}')
            else:
                with (folder/f'tables/pose_{side}_hand.csv').open(encoding='utf-8-sig', newline='') as f:
                    header = next(csv.reader(f))
                indexed = [re.fullmatch(r'transforms\[(\d+)\]\.pos\.([xyz])', c) for c in header]
                indices = [(int(m[1]), m[2]) for m in indexed if m]
                if set(indices) != expected or len(indices) != len(expected) or 'transforms.length' not in header:
                    raise ValueError(f'Unexpected MCAP-export hand columns in {folder}')
        counts[dataset] += 1
    return counts


def lengths(wrist, middle):
    valid = np.isfinite(wrist).all(axis=1) & np.isfinite(middle).all(axis=1)
    valid &= (wrist != 0).any(axis=1) & (middle != 0).any(axis=1)
    dist = np.linalg.norm(middle-wrist, axis=1)
    return np.where(valid & (dist > 0), dist, np.nan)


def demo_inputs(folder):
    cols = [f'observation.state.hand_{s}_world' for s in SIDES]
    table = pq.read_table(sorted((folder/'data').rglob('*.parquet')), columns=['frame_index', *cols])
    flags = json.loads((folder/'bad_frame_ratio.json').read_text())['hand_bad_frame_details']
    unlisted = ~np.isin(table['frame_index'].to_pylist(), [r['frame'] for r in flags])
    dist = {}
    for side, col in zip(SIDES, cols):
        values = np.asarray(table[col].to_pylist(), dtype=float)
        dist[side] = lengths(values[:, 0], values[:, 9])
    return unlisted, dist


def history_scale(history):
    """Require both endpoints usable at every stored historical sample for this probe."""
    if len(history) < 2 or not np.isfinite(history).all() or np.any(history <= 0):
        return None
    return float(np.median(history))


def training(root):
    geometry, blocks, pool = [], [], []
    for rec, folder, meta in split_records(root, 'train'):
        dataset, env = folder.parent.name, meta['environment_id']
        eligible = []
        if dataset == 'EgoDemo':
            eligible_ticks, dist = demo_inputs(folder)
        else:
            times, eligible_ticks, wrists, masks = wrist_inputs(folder)
            dist = {}
            for side in SIDES:
                columns = [f'transforms[9].pos.{axis}' for axis in 'xyz']
                frame = pd.read_csv(folder/f'tables/pose_{side}_hand.csv',
                                    usecols=['header.ts', *columns], dtype={'header.ts': 'string'})
                if not np.array_equal(times, frame['header.ts'].to_numpy(dtype=np.int64)):
                    raise ValueError('Middle MCP clock differs from wrist clock')
                dist[side] = lengths(wrists[side], frame[columns].to_numpy(dtype=float))
            eligible = [block for run in qualified_runs(times, masks['both'])
                        for block in windows(times, run, SECOND, 2*SECOND)]
        pool.append(dict(record=rec, dataset=dataset, environment=env, windows=len(eligible)))
        for side in SIDES:
            observed = dist[side][eligible_ticks]
            finite = observed[np.isfinite(observed)]
            median = np.median(finite) if len(finite) else np.nan
            relative_spread = np.ptp(np.quantile(finite, [.1, .9])) / median if len(finite) else np.nan
            geometry.append(dict(dataset=dataset, side=side, eligible=len(observed), usable=len(finite),
                                 record_median=median, record_spread=relative_spread))
            for start, first, end in eligible:
                h = dist[side][start:first]
                scale = history_scale(h)
                if scale is None:
                    continue
                mid = np.searchsorted(times[start:first] - times[start], SECOND//2)
                if not 0 < mid < len(h):
                    raise ValueError('Empty half-history in a qualified window')
                split_change = abs(np.median(h[mid:])-np.median(h[:mid])) / scale
                p10, p90 = np.quantile(h, [0.1, 0.9])
                err = np.linalg.norm(wrists[side][first:end] - wrists[side][first-1], axis=1)
                blocks.append(dict(dataset=dataset, record=rec, environment=env, side=side,
                                   scale=scale, spread=(p90-p10)/scale, half_change=split_change,
                                   points=len(err), error_sum=err.sum(), ratio_sum=(err/scale).sum()))
    return pd.DataFrame(geometry), pd.DataFrame(blocks), pd.DataFrame(pool)


def summarize(root):
    schemas = check_schemas(root)
    geo, blocks, pool = training(root)
    for dataset in (*DATASETS, 'all'):
        group = pool if dataset == 'all' else pool[pool.dataset == dataset]
        for side in SIDES:
            g = geo[geo.side == side]
            b = blocks[blocks.side == side]
            if dataset != 'all':
                g, b = g[g.dataset == dataset], b[b.dataset == dataset]
            row = dict(dataset=dataset, side=side, schema_records=schemas.get(dataset, sum(schemas.values())),
                       train_records=len(group), inspected_ticks=int(g.eligible.sum()), usable_ticks=int(g.usable.sum()),
                       rec_median_min=g.record_median.min(), rec_median_max=g.record_median.max(),
                       rec_spread_max=g.record_spread.max(),
                       eligible_windows=int(group.windows.sum()), scored_windows=len(b), scored_records=b.record.nunique(),
                       scored_envs=b.environment.nunique(), timestamps=int(b.points.sum()))
            stats = dict(scale_min=b.scale.min(), scale_median=b.scale.median(), scale_max=b.scale.max(),
                         spread_p95=b.spread.quantile(.95), spread_max=b.spread.max(),
                         half_change_p95=b.half_change.quantile(.95), half_change_max=b.half_change.max())
            if len(b):
                records = b.groupby(['environment', 'record'])[['points', 'error_sum', 'ratio_sum']].sum()
                macro = records[['error_sum', 'ratio_sum']].div(records.points, axis=0).groupby('environment').mean().mean()
                stats.update(macro_distance=macro.error_sum, macro_relative=macro.ratio_sum)
            yield {**row, **stats}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--out', type=Path, default=Path('runs/c_palm_scale/summary.csv'))
    args = parser.parse_args()
    results = pd.DataFrame(summarize(args.root))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(args.out, index=False, float_format='%.12g')
    print(results.to_string(index=False))
