"""Describe wrist motion in the first indexed training CSV record per source.

AI-assisted data preparation, 2026-11-20: OpenCode, GPT-6.2 Astra routing group
(OpenAI GPT family; exact routed model release date unavailable).
"""

import argparse
from decimal import Decimal
from pathlib import Path

import numpy as np

if __package__:
    from .c_q2_windows import ROOT, SIDES, qualified_runs, raw_metadata, rows, wrist_inputs
else:
    from c_q2_windows import ROOT, SIDES, qualified_runs, raw_metadata, rows, wrist_inputs


def sample(root):
    rules = root / 'Restocking_Tasks_and_Constraints'
    units = rows(rules / 'source_segment_index.csv')
    training = {r['episode_uuid'] for r in rows(rules / 'record_split.csv') if r['split'] == 'train'}
    for dataset in ('EgoPro', 'EgoStandard'):
        unit = next(r for r in units if r['split'] == 'train' and r['dataset'] == dataset)
        folder = root / 'Raw_Data' / dataset / unit['sample']
        assert raw_metadata(folder)['episode_uuid'] == unit['episode_uuid'] in training
        times, _, points, masks = wrist_inputs(folder)
        counts = dict(left=0, right=0, tie=0, no_pair=0)
        for seg in rows(folder / 'tables/annotation_semantic_segments.csv'):
            start = int(seg['header.ts'])
            duration = Decimal(seg['segment.end_time']) - Decimal(seg['segment.start_time'])
            end = start + int(duration * 10**9)
            usable = masks['both'] & (start <= times) & (times < end)
            runs = [r for r in qualified_runs(times, usable) if len(r) > 1]
            if not runs:
                counts['no_pair'] += 1
                continue
            left, right = (sum(np.linalg.norm(np.diff(points[s][r], axis=0), axis=1).sum() for r in runs)
                           for s in SIDES)
            key = 'left' if left > right else 'right' if right > left else 'tie'
            counts[key] += 1
        print(dataset, 'segments', sum(counts.values()), counts)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    sample(parser.parse_args().root)
