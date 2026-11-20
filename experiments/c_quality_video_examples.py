"""Fixed event-centred video examples for each C record with marked hand samples.

AI-assisted data preparation using OpenCode, OpenAI GPT family, GPT-6.2 Astra
routing group, 2026-11-21. Exact routed model release date unavailable.
"""

import argparse
from collections import Counter, defaultdict
import csv
from itertools import groupby
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

import numpy as np
from PIL import Image, ImageDraw
import pyarrow.parquet as pq

if __package__:
    from .c_records import ROOT, official_records, raw_metadata, rows
else:
    from c_records import ROOT, official_records, raw_metadata, rows


def marked_clock(folder):
    """Demonstration omissions retain unknown quality. Event timing uses integer ns."""
    if folder.parent.name == 'EgoDemo':
        meta = json.loads((folder/'bad_frame_ratio.json').read_text())
        table = pq.read_table(sorted((folder/'data').rglob('*.parquet')), columns=['timestamp', 'frame_index'])
        clock = np.rint(np.array(table['timestamp'].to_pylist()) * 1e9).astype(np.int64)
        indices = table['frame_index'].to_pylist()
        assert indices == list(range(len(clock))) and len(clock) == meta['total_frames']
        labels = [set() for _ in indices]
        for item in meta['hand_bad_frame_details']:
            for side in ('left', 'right'):
                if item.get(f'{side}_bad_reason'):
                    labels[item['frame']].add(f"{side}:{item[side + '_bad_reason']}")
        files = list((folder/'videos/observation.images.head_left').rglob('*.mp4'))
        if len(files) != 1:
            raise ValueError('Expected one head-left MP4')
        return clock, labels, files[0]
    quality = rows(folder/'tables/annotation_bad_frame_pose_hand.csv')
    head = rows(folder/'videos/head_left_frames.csv')
    clock = np.array([int(r['header.ts']) for r in quality], dtype=np.int64)
    if not np.array_equal(clock, [int(r['video_timestamp_ns']) for r in head]):
        raise ValueError('Quality and camera clocks differ')
    labels = []
    for r in quality:
        reasons = {r[f'problem_type[{i}]'].strip() for i in range(int(r['problem_type.length']))}
        assert bool(reasons) == (r['is_bad'].lower() in ('true', '1'))
        labels.append(reasons)
    return clock, labels, folder/'videos/head_left.mp4'


def spells(clock, labels):
    starts = np.r_[True, np.diff(clock)>50_000_000]
    if (np.diff(clock) <= 0).any():
        raise ValueError('Non-increasing source clock')
    for part in np.split(np.arange(len(clock)), np.flatnonzero(starts)[1:]):
        for bad, ticks in groupby(part, lambda i: bool(labels[i])):
            if bad:
                yield list(ticks)


def read_tiles(video, ticks):
    unique = sorted(set(ticks))
    expression = '+'.join(f'eq(n\\,{i})' for i in unique)
    with TemporaryDirectory() as directory:
        output = Path(directory)/'frame%02d.png'
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-threads', '2', '-i', str(video),
                        '-filter_threads', '1', '-vf', f'select={expression},scale=288:-2',
                        '-vsync', '0', '-threads', '1', '-frames:v', str(len(unique)), str(output)], check=True)
        images = {}
        for i,t in enumerate(unique, 1):
            with Image.open(Path(directory)/f'frame{i:02d}.png') as img:
                images[t] = img.convert('RGB')
        return [images[t] for t in ticks]


def run(root, out, counts_only=False):
    out.mkdir(parents=True, exist_ok=True)
    index = official_records(root)
    loc = {r['episode_uuid']:root/'Raw_Data'/r['dataset']/r['sample']
           for r in rows(root/'Restocking_Tasks_and_Constraints/source_segment_index.csv')}
    stats, pages = defaultdict(Counter), []
    for ordinal, record in enumerate(index, 1):
        folder = loc[record['episode_uuid']]
        meta = raw_metadata(folder)
        assert meta['episode_uuid'] == record['episode_uuid'] and meta['environment_id'] == record['environment_id']
        clock, reasons, video = marked_clock(folder)
        runs = list(spells(clock, reasons))
        row = stats[record['dataset'], record['split']]
        row.update(records=1, ticks=len(clock), marked=sum(map(len, runs)), spells=len(runs))
        if not runs:
            continue
        # Longest by observed first-to-last time, earliest on ties. Pick contextual
        # frames near +/-0.25 seconds, not necessarily known-normal in either format.
        run = max(runs, key=lambda seq: clock[seq[-1]]-clock[seq[0]])
        mid = run[(len(run)-1)//2]
        ids = [max(0, int(clock.searchsorted(clock[run[0]]-250_000_000, side='right'))-1), mid,
               min(len(clock)-1, int(clock.searchsorted(clock[run[-1]]+250_000_000)))]
        row.update(records_with_marks=1, displayed_panels=3, distinct_displayed_frames=len(set(ids)))
        name = f"R{ordinal:02} {record['dataset']} {record['split']} | {len(run)} marked ticks | {','.join(sorted(reasons[mid]))}"
        if not counts_only:
            pages.append((name, read_tiles(video, ids)))
    for offset in range(0,len(pages),4):
        subset = pages[offset:offset+4]
        sheet = Image.new('RGB', (3*288, len(subset)*259), 'white')
        draw = ImageDraw.Draw(sheet)
        for i,(name, images) in enumerate(subset):
            draw.text((4,i*259+2), name, fill='black')
            for j,img in enumerate(images):
                img.thumbnail((286,230))
                sheet.paste(img, (j*288,i*259+20))
        sheet.save(out/f'page{offset//4+1:02}.jpg',quality=88)
    for key, row in sorted(stats.items()):
        print(*key, dict(row))
    print('Unique population:', dict(sum(stats.values(), Counter())))
    fields = ('records','ticks','marked','spells','records_with_marks','displayed_panels','distinct_displayed_frames')
    with (out/'summary.csv').open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=['dataset','split',*fields], lineterminator='\n')
        writer.writeheader()
        for (source,split), row in sorted(stats.items()):
            writer.writerow(dict(dataset=source, split=split, **{k:row[k] for k in fields}))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=ROOT)
    p.add_argument('--out', type=Path, default=Path('runs/c_full_overview/event_sheets'))
    p.add_argument('--counts-only', action='store_true', help='Recalculate the selection counts without redrawing the local pages')
    args = p.parse_args()
    run(args.root, args.out, args.counts_only)
