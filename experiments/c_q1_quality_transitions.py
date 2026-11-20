"""Training-only next-sample baseline for the explicit joint hand-quality flag.

AI-assisted preprocessing and tabulation in OpenCode on 2026-11-20, OpenAI GPT
family, GPT-6.2 Astra routing group. Exact model release date unavailable.
"""

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path
import sys

if __package__:
    from .c_q2_windows import ROOT, rows, split_records
else:
    from c_q2_windows import ROOT, rows, split_records


def transitions(samples):
    counts = Counter()
    previous = None
    for row in samples:
        time = int(row['header.ts'])
        token = row['is_bad'].strip().lower()
        if token not in {'true', 'false', '0', '1'}:
            raise ValueError('Unexpected joint quality flag')
        bad = token in {'true', '1'}
        counts['ticks'] += 1
        if previous is not None:
            dt = time - previous[0]
            if dt <= 0:
                raise ValueError('Non-increasing quality clock')
            if dt <= 50_000_000:
                old_label = 'bad' if previous[1] else 'good'
                new_label = 'bad' if bad else 'good'
                counts[f'{old_label}_{new_label}'] += 1
            else:
                counts['gaps'] += 1
        previous = time, bad
    counts['records'] = 1
    return counts


def run(root):
    groups = defaultdict(Counter)
    for _, folder, meta in split_records(root, 'train'):
        dataset = folder.parent.name
        if dataset == 'EgoDemo':  # Sparse reasons cannot label an adjacent good -> bad pair.
            continue
        c = transitions(rows(folder / 'tables/annotation_bad_frame_pose_hand.csv'))
        for key in [('source', dataset), ('environment', meta['environment_id']), ('all', 'explicit_csv')]:
            groups[key].update(c)
    fields = ('records', 'ticks', 'gaps', 'good_good', 'good_bad', 'bad_good', 'bad_bad')
    for (scope, name), c in sorted(groups.items()):
        assert sum(c[k] for k in fields[3:]) + c['gaps'] == c['ticks'] - c['records']
        yield dict(scope=scope, name=name, **{k: c[k] for k in fields})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    output = list(run(args.root))
    writer = csv.DictWriter(sys.stdout, fieldnames=output[0], lineterminator='\n')
    writer.writeheader()
    writer.writerows(output)
