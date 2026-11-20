"""Training-only support for a future comparison of reservation rules.

AI-assisted descriptive tabulation, OpenCode / OpenAI GPT family (GPT-6.2
Astra routing group), 2026-11-20. Exact model release date unavailable.
"""

import argparse
import csv
from itertools import groupby
from operator import itemgetter
from pathlib import Path
import sys

import numpy as np


ROOT = Path(__file__).resolve().parents[1] / 'data/raw/C'


def summarize(group):
    duration = np.array([float(r['duration_s']) for r in group])
    environments = np.array([r['environment_id'] for r in group])
    ids = np.unique(environments)
    q25, median, q75 = np.quantile(duration, [.25, .5, .75], method='linear')
    # An environment is removed as a block while all other training units retain weight one.
    omitted = []
    if len(ids) > 1:
        for environment in ids:
            retained = duration[environments != environment]
            omitted.append(np.quantile(retained, .75, method='linear'))
    return dict(segments=len(group), environments=len(ids), records=len({r['episode_uuid'] for r in group}),
                q25_s=q25, median_s=median, q75_s=q75,
                omit_env_q75_min_s=min(omitted, default=None), omit_env_q75_max_s=max(omitted, default=None))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    with (args.root/'Restocking_Tasks_and_Constraints/source_segment_index.csv').open(encoding='utf-8-sig') as f:
        samples = [r for r in csv.DictReader(f) if r['split'] == 'train' and r['eligible'] == '1']
    by_family = itemgetter('family_id')
    results = [dict(family=family, **summarize(list(group)))
               for family, group in groupby(sorted(samples, key=by_family), key=by_family)]
    writer = csv.DictWriter(sys.stdout, fieldnames=results[0], lineterminator='\n')
    writer.writeheader()
    writer.writerows(results)
