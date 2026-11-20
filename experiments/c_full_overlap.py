"""Descriptive cross-record overlap checks for C, independent of scores and models.

AI-assisted data preparation, OpenCode / OpenAI GPT family, GPT-6.2 Astra
routing group, 2026-11-21. Exact routed model release date unavailable.
"""

import argparse
from collections import defaultdict
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from .c_records import ROOT, official_records, rows


def wrist_tracks(folder):
    if folder.parent.name == 'EgoDemo':
        cols = [f'observation.state.hand_{side}_world' for side in ('left','right')]
        data = pq.read_table(sorted((folder/'data').rglob('*.parquet')), columns=['timestamp',*cols])
        times = np.asarray(data['timestamp'].to_pylist(), dtype=float)
        return times, np.stack([np.asarray(data[c].to_pylist())[:, 0] for c in cols], axis=1)
    coords = [f'transforms[0].pos.{a}' for a in 'xyz']
    clocks, hands = [], []
    for side in ('left','right'):
        frame = pd.read_csv(folder/f'tables/pose_{side}_hand.csv', usecols=['header.ts',*coords], dtype={'header.ts':str})
        clocks.append(frame['header.ts'].to_numpy(dtype=np.int64))
        hands.append(frame[coords].to_numpy(dtype=float))
    if not np.array_equal(*clocks):
        raise ValueError('Unaligned wrists')
    return (clocks[0]-clocks[0][0])/1e9, np.stack(hands,axis=1)


def signatures(t, xy, width=16):
    """Same frame count, same relative time bins, same translated motion bins."""
    if not np.isfinite(xy).all() or not (np.diff(t)>0).all():
        raise ValueError('Unusable input geometry or clock')
    for i in range(len(t)-width+1):
        motion = xy[i:i+width]-xy[i]
        if np.linalg.norm(motion,axis=2).max() < .02:
            continue
        # Distances in source meters, times in seconds. Degrees of freedom removed:
        # time origin and the starting positions of each wrist, with fixed world axes.
        ms = np.rint(1000*(t[i:i+width]-t[i])).astype('<i4')
        mm = np.rint(1000*motion).astype('<i4')
        yield hashlib.sha256(ms.tobytes()+mm.tobytes()).digest()


def audit(root):
    units = rows(root/'Restocking_Tasks_and_Constraints/source_segment_index.csv')
    loc = {r['episode_uuid']:root/'Raw_Data'/r['dataset']/r['sample'] for r in units}
    texts = defaultdict(list)
    for r in units:
        texts[' '.join(r['description'].casefold().split())].append(r)
    print('semantic_units',len(units),'normalized_texts',len(texts),'repeated_texts',sum(len(g)>1 for g in texts.values()),
          'units_with_repeated_text',sum(len(g) for g in texts.values() if len(g)>1),
          'texts_crossing_records',sum(len({r['episode_uuid'] for r in g})>1 for g in texts.values()),
          'texts_crossing_splits',sum(len({r['split'] for r in g})>1 for g in texts.values()))
    seen = defaultdict(set)
    sizes = defaultdict(list)
    membership = {}
    possible = used = 0
    for r in official_records(root):
        episode = r['episode_uuid']
        folder = loc[episode]
        membership[episode] = r['split']
        t, xy = wrist_tracks(folder)
        possible += max(0, len(t)-15)
        for key in signatures(t,xy):
            seen[key].add(episode)
            used += 1
        for p in (folder/'videos').rglob('*.mp4'):
            sizes[p.stat().st_size].append(p)
    same_size = [g for g in sizes.values() if len(g)>1]
    identical = []
    for g in same_size:
        checksums = defaultdict(int)
        for p in g:
            h = hashlib.sha256()
            with p.open('rb') as f:
                while block := f.read(1024**2):
                    h.update(block)
            checksums[h.digest()] += 1
        identical.extend(n for n in checksums.values() if n>1)
    print('mp4s',sum(map(len,sizes.values())),'distinct_byte_sizes',len(sizes),'byte_identical_groups',len(identical))
    print('wrist_windows_considered',possible,'wrist_windows_with_motion_2cm',used,
          'signatures_crossing_records',sum(len(g)>1 for g in seen.values()),
          'signatures_crossing_splits',sum(len({membership[s] for s in g})>1 for g in seen.values()))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=ROOT)
    audit(p.parse_args().root)
