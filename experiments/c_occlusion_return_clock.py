"""Read training quality clocks to compare witnessed returns to normal with a 0.5 s receipt.

AI-assisted tabulation, OpenCode / OpenAI GPT family, GPT-6.2 Astra routing group,
2026-11-20. Exact routed release date unavailable.
"""

import argparse
from itertools import groupby
from pathlib import Path

import numpy as np
import pandas as pd

if __package__:
    from .c_records import ROOT, rows, split_records
else:
    from c_records import ROOT, rows, split_records


def events(samples, *, continuous_occlusion=False):
    clocks, flags, occ = [], [], []
    for r in samples:
        clocks.append(int(r['header.ts']))
        flag = r['is_bad'].strip().lower()
        if flag not in ('true', 'false', '0', '1'):
            raise ValueError('Unexpected explicit flag')
        occ.append(any(r[f'problem_type[{i}]'].strip() in ('left:occlusion', 'right:occlusion')
                       for i in range(int(r['problem_type.length']))))
        bad = flag in ('true', '1')
        if occ[-1] and not bad:
            raise ValueError('Occlusion reason conflicts with explicit normal flag')
        flags.append(occ[-1] if continuous_occlusion else bad)
    dt = np.diff(np.asarray(clocks, dtype=np.int64))
    if (dt <= 0).any():
        raise ValueError('Non-increasing clock')
    for segment in np.split(np.arange(len(clocks)), np.flatnonzero(dt > 50_000_000) + 1):
        for bad, indices in groupby(segment, flags.__getitem__):
            if bad:
                sequence = list(indices)
                first, last = sequence[0], sequence[-1]
                start_seen, end_seen = first > segment[0], last < segment[-1]
                both = start_seen and end_seen
                yield dict(both_ends=both, start_seen=start_seen, end_seen=end_seen, onset_occlusion=occ[first],
                           contains_occlusion=any(occ[i] for i in sequence),
                           duration_ns=clocks[last+1]-clocks[first] if both else None,
                           until_normal_ns=clocks[last+1]-clocks[first] if both and not continuous_occlusion else None)


def main(root):
    data = []
    for rec, folder, meta in split_records(root, 'train'):
        if folder.parent.name != 'EgoDemo':
            for e in events(rows(folder/'tables/annotation_bad_frame_pose_hand.csv')):
                data.append(dict(record=rec, environment=meta['environment_id'], **e))
    table = pd.DataFrame(data)
    for label, subset in [('any_joint_bad', table), ('occlusion_at_first', table[table.onset_occlusion]),
                          ('occlusion_anywhere', table[table.contains_occlusion])]:
        complete = subset[subset.both_ends]
        d = complete.until_normal_ns
        print(label, 'spells', len(subset), 'both boundaries', len(d), 'records', complete.record.nunique(),
              'envs', complete.environment.nunique(), 'median_s', d.median()/1e9, 'max_s', d.max()/1e9)
        for age_ns in (0, 500_000_000, 1_000_000_000):
            at_risk = d[d > age_ns]
            print('age_s', age_ns/1e9, 'still_bad', len(at_risk), 'receipt_before_normal',
                  int((at_risk > age_ns + 500_000_000).sum()), 'simultaneous',
                  int((at_risk == age_ns + 500_000_000).sum()))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    main(parser.parse_args().root)
