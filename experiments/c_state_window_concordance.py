"""Joint directions of per-window post-receipt errors in the existing train probe.

OpenCode assisted tabulation, OpenAI GPT family, GPT-6.2 Astra routing group,
2026-11-21. Exact model release date unavailable. Only aggregate counts leave memory.
"""

import argparse
from collections import Counter
import csv
from pathlib import Path

import numpy as np

from .c_feas_hand_channels import ROOT, SECOND, SIDES, TOL, error_arrays, packed_states, request_clock
from .c_q2_windows import qualified_runs, windows, wrist_inputs
from .c_records import split_records


def direction(before, after):
    change = float((after - before).mean())
    if abs(change) <= TOL:
        return 'tie'
    return 'better' if change < 0 else 'worse'


def summarize(root):
    counts = Counter()
    for _, folder, meta in split_records(root, 'train'):
        if folder.parent.name == 'EgoDemo':
            continue  # The existing pool requires explicit-normal records.
        time, _, wrists, masks = wrist_inputs(folder)
        data = [packed_states(folder, side, time, wrists[side]) for side in SIDES]
        state = np.stack([packed for packed, _ in data], axis=1)
        angles = np.stack([bend for _, bend in data], axis=1)
        for run in qualified_runs(time, masks['both']):
            for start, first, end in windows(time, run, SECOND, 2 * SECOND):
                if not (np.isfinite(state[start:end]).all() and np.isfinite(angles[start:end]).all()):
                    continue
                clock = time[first:end]
                source, arrival = request_clock(clock, int(time[first - 1]))
                source_index = first + int(clock.searchsorted(source))
                assert time[source_index] == source
                truth = state[first:end][clock >= arrival]
                assert len(truth) > 0
                # Only evaluate already-returned information. Each prediction holds one source state.
                unchanged = error_arrays(np.broadcast_to(state[first - 1], truth.shape), truth)
                updated = error_arrays(np.broadcast_to(state[source_index], truth.shape), truth)
                relative = ('pair', 'relative_vector', 'cm')
                rel = direction(unchanged[relative], updated[relative])
                for hand in SIDES:
                    keys = [(hand, 'wrist_position', 'cm'), (hand, 'rotation', 'deg'), (hand, 'bend_mean4', 'deg')]
                    directions = tuple(direction(unchanged[k], updated[k]) for k in keys)
                    counts[meta['environment_id'], hand, *directions, rel] += 1
    return counts


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--out', type=Path, default=Path('runs/c_state_concordance/counts.csv'))
    args = parser.parse_args()
    counts = summarize(args.root)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('w', newline='') as stream:
        writer = csv.writer(stream, lineterminator='\n')
        writer.writerow(['environment', 'hand', 'position', 'rotation', 'bend_mean4', 'relative_vector', 'windows'])
        writer.writerows((*key, n) for key, n in sorted(counts.items()))
