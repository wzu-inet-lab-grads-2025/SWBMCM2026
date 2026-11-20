"""Training-only descriptive wrist speed by thirds of an annotated interval.

AI-assisted descriptive tabulation in OpenCode, OpenAI GPT family, GPT-6.2
Astra routing group, 2026-11-20. Exact model release date unavailable.
"""

import argparse
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

if __package__:
    from .c_q2_windows import SIDES, wrist_inputs
    from .c_records import ROOT, rows, split_records
else:
    from c_q2_windows import SIDES, wrist_inputs
    from c_records import ROOT, rows, split_records


def segments(root):
    data = []
    for record, folder, meta in split_records(root, 'train'):
        if folder.parent.name == 'EgoDemo':
            continue
        t, _, xyz, masks = wrist_inputs(folder)
        dt = np.diff(t)
        good = masks['both'][:-1] & masks['both'][1:] & (dt > 0) & (dt <= 50_000_000)
        distances = {s: np.linalg.norm(np.diff(xyz[s], axis=0), axis=1) for s in SIDES}
        for seg in rows(folder/'tables/annotation_semantic_segments.csv'):
            start = int(seg['header.ts'])
            span = Decimal(seg['segment.end_time']) - Decimal(seg['segment.start_time'])
            length = int(span * 10**9)
            if length <= 0:
                raise ValueError('Nonpositive annotated interval')
            # Both samples must be inside the same half-open third, on the unrounded clock.
            thirds = ((t-start)*3) // length
            valid = good & (thirds[:-1] == thirds[1:])
            groups = [valid & (thirds[:-1] == i) for i in range(3)]
            seconds = [dt[g].sum()/1e9 for g in groups]
            for s in SIDES:
                rates = [100*distances[s][g].sum()/sec if sec > 0 else np.nan for g,sec in zip(groups, seconds)]
                data.append(dict(record=record, environment=meta['environment_id'], dataset=folder.parent.name,
                                 side=s, early=rates[0], middle=rates[1], late=rates[2]))
    return pd.DataFrame(data)


def summarize(data):
    values = ['early', 'middle', 'late']
    for s, all_side in data.groupby('side'):
        # Compare stages on exactly the same intervals. Counts retain every interval in the denominator.
        valid = all_side.dropna(subset=values).copy()
        valid['middle_fastest'] = (valid.middle > valid.early) & (valid.middle > valid.late)
        for scope,name,denom in [('all','all',all_side), *(('environment', e, g) for e,g in all_side.groupby('environment'))]:
            g = valid if scope=='all' else valid[valid.environment==name]
            rec_means = g.groupby(['environment','record'])[values].mean()
            mean_speed = rec_means.groupby('environment').mean().mean()
            yield dict(scope=scope,name=name,hand=s,intervals=len(denom),paired=len(g),records=g.record.nunique(),
                       environments=g.environment.nunique(),middle_fastest=int(g.middle_fastest.sum()),
                       **{f'{k}_cm_s':mean_speed[k] for k in values})


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=ROOT)
    p.add_argument('--out', type=Path, default=Path('runs/c_design_support/thirds.csv'))
    args = p.parse_args()
    table = pd.DataFrame(summarize(segments(args.root)))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.out, index=False, float_format='%.10g')
    print(table.to_string(index=False))
