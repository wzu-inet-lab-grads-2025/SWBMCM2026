"""Rebuild the private interval triptychs for the C descriptive image review.

AI-assisted preprocessing and visual audit in OpenCode, GPT-6.2 Astra routing
group, 2026-11-21. Exact model release date unavailable.
"""

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from PIL import Image, ImageDraw, ImageFont, ImageOps

if __package__:
    from .c_full_video_sheets import inventory, write_csv
    from .c_records import ROOT, rows
else:
    from c_full_video_sheets import inventory, write_csv
    from c_records import ROOT, rows


BASE = Path(__file__).resolve().parents[1]
LOCAL = BASE / 'runs/c_dense_segment_review'
GRADES = ('visible', 'partial', 'insufficient', 'contradiction')
PAGE_SIZE = 5


def clock(folder, video):
    if folder.parent.name == 'EgoDemo':
        data, = (folder / 'data').rglob('*.parquet')
        table = pq.read_table(data, columns=['frame_index', 'timestamp'], use_threads=False)
        if not np.array_equal(table['frame_index'].to_numpy(), np.arange(table.num_rows)):
            raise ValueError('Nonsequential Demo frame_index')
        times = table['timestamp'].to_numpy().astype(np.float64)
        labels = json.loads((folder / 'annotation.json').read_text())['subtask_annotations']
        raw = [(r['start_time'], r['end_time'], r['description']) for r in labels]
    else:
        labels = rows(folder / 'tables/annotation_semantic_segments.csv')
        raw = [(r['segment.start_time'], r['segment.end_time'], r['segment.subtask_description']) for r in labels]
        origin = Decimal(labels[0]['header.ts']) - Decimal(raw[0][0]) * 10**9
        source = pd.read_csv(folder / 'tables/pose_right_hand.csv', usecols=['header.ts'])['header.ts'].to_numpy()
        side = pd.read_csv(video.with_name(video.stem + '_frames.csv'), usecols=['video_timestamp_ns'])['video_timestamp_ns'].to_numpy()
        if origin != int(origin) or not np.array_equal(source, side):
            raise ValueError(f'Source/video sidecar alignment error in {folder}')
        times = (source - int(origin)) / 1e9
    if not np.isfinite(times).all() or not (np.diff(times) > 0).all():
        raise ValueError(f'Invalid trajectory clock in {folder}')
    return times, raw


def extract(job):
    rec, video, units, root = job
    folder = root / 'Raw_Data' / rec['source'] / rec['sample']
    times, labels = clock(folder, video)
    if sorted(int(u['source_segment_index']) for u in units) != list(range(len(labels))):
        raise ValueError(f'Missing raw annotation in {folder}')
    ids, result = set(), []
    directory = LOCAL / 'images' / rec['alias']
    directory.mkdir(parents=True, exist_ok=True)
    for unit in units:
        start, end = map(float, (unit['start_s'], unit['end_s']))
        a, b, desc = labels[int(unit['source_segment_index'])]
        if (start, end, unit['description']) != (float(a), float(b), desc) or unit['split'] != rec['split']:
            raise ValueError(f'Official row disagrees with raw annotation {unit["unit_id"]}')
        within = np.flatnonzero((times >= start) & (times < end))
        if len(within) < 3:
            raise ValueError('Fewer than three distinct frames')
        middle = within[1:-1][np.argmin(abs(times[within[1:-1]] - (start + end) / 2))]
        row = {**unit, 'alias': rec['alias'], 'view': 'head_left', 'video': str(video.relative_to(root)), 'available': len(within)}
        for tag, idx in zip(('early', 'mid', 'late'), (within[0], middle, within[-1])):
            ids.add(idx)
            row[tag + '_index'] = int(idx)
            row[tag + '_s'] = float(times[idx])
            row[tag + '_image'] = str((directory / f'{idx:06}.jpg').relative_to(LOCAL))
        result.append(row)
    capture(video, ids, directory)
    return result


def capture(video, ids, directory):
    ordered = sorted(ids)
    expr = '+'.join(f'eq(n\\,{i})' for i in ordered)
    # No input seeking. FFmpeg's n is the zero-based presentation-order index.
    with tempfile.TemporaryDirectory(dir=directory) as tmp:
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-threads', '1', '-i', str(video),
                        '-map', '0:v:0', '-filter_threads', '1', '-vf', f'select={expr},scale=960:-2',
                        '-vsync', '0', '-threads', '1', '-q:v', '2', str(Path(tmp) / '%06d.jpg')], check=True)
        files = sorted(Path(tmp).glob('*.jpg'))
        if len(files) != len(ordered):
            raise ValueError(f'FFmpeg did not return every requested display index in {video}')
        for idx, file in zip(ordered, files):
            file.replace(directory / f'{idx:06}.jpg')


def extra(root):
    manifest = {r['ordinal']: r for r in rows(LOCAL / 'manifest.csv')}
    requests = defaultdict(list)
    for req in rows(LOCAL / 'extra_requests.csv'):
        r = manifest[req['ordinal']]
        requests[r['alias'], req['view']].append((r, float(req['fraction'])))
    records = {r['alias']: (r, videos) for r, videos in inventory(root)}
    result = []
    for (alias, view), wanted in requests.items():
        record, videos = records[alias]
        if view == 'head_left':
            raise ValueError('Use saved primary images for head-left enlargement')
        video = videos[view]
        times, _ = clock(root / 'Raw_Data' / record['source'] / record['sample'], video)
        directory = LOCAL / 'extra' / f'{alias}_{view}'
        directory.mkdir(parents=True, exist_ok=True)
        ids = set()
        for r, fraction in wanted:
            a, b = float(r['start_s']), float(r['end_s'])
            if not 0 <= fraction <= 1:
                raise ValueError('Invalid additional sample fraction')
            inside = np.flatnonzero((times >= a) & (times < b))
            idx = inside[np.argmin(abs(times[inside] - (a + fraction * (b - a))))]
            ids.add(idx)
            result.append(dict(ordinal=r['ordinal'], unit_id=r['unit_id'], view=view, fraction=fraction,
                               index=idx, time_s=times[idx], image=str((directory / f'{idx:06}.jpg').relative_to(LOCAL))))
        if all((directory / f'{idx:06}.jpg').is_file() for idx in ids):
            for idx in ids:
                with Image.open(directory / f'{idx:06}.jpg') as image:
                    image.verify()
        else:
            capture(video, ids, directory)
    write_csv(LOCAL / 'extra_manifest.csv', result)


def draw_page(group, number):
    page = Image.new('RGB', (1020, 271 * len(group)), '#101010')
    pen = ImageDraw.Draw(page)
    font = ImageFont.truetype('DejaVuSans.ttf', 14)
    for n, r in enumerate(group):
        top = n * 271
        description = f"{int(r['ordinal']):03}  {r['alias']} / {r['source_segment_index']}  {r['description']}"
        pen.multiline_text((3, top + 1), '\n'.join(textwrap.wrap(description, 125)), fill='white', font=font, spacing=0)
        for j, tag in enumerate(('early', 'mid', 'late')):
            with Image.open(LOCAL / r[tag + '_image']) as image:
                thumb = ImageOps.contain(image, (338, 232), Image.Resampling.LANCZOS)
                page.paste(thumb, (j * 340 + (340 - thumb.width) // 2, top + 37))
        r['page'] = f'pages/{number:02}.jpg'
    page.save(LOCAL / group[0]['page'], quality=93)


def build(root):
    (LOCAL / 'pages').mkdir(parents=True, exist_ok=True)
    index = rows(root / 'Restocking_Tasks_and_Constraints/source_segment_index.csv')
    by_record = defaultdict(list)
    for ordinal, unit in enumerate(index, 1):
        unit['ordinal'] = ordinal
        by_record[unit['episode_uuid']].append(unit)
    jobs = [(rec, videos['head_left'], by_record.pop(rec['episode_uuid']), root) for rec, videos in inventory(root)]
    if by_record or len({u['unit_id'] for u in index}) != len(index):
        raise ValueError('Unexpected official unit inventory')
    with ThreadPoolExecutor(2) as pool:
        manifest = sorted((r for group in pool.map(extract, jobs) for r in group), key=lambda r: r['ordinal'])
    for offset in range(0, len(manifest), PAGE_SIZE):
        draw_page(manifest[offset:offset + PAGE_SIZE], 1 + offset // PAGE_SIZE)
    write_csv(LOCAL / 'manifest.csv', manifest)
    print(f'{len(jobs)} videos, {len(manifest)} interval triptychs')


def aggregate(root):
    manifest = rows(LOCAL / 'manifest.csv')
    official = rows(root / 'Restocking_Tasks_and_Constraints/source_segment_index.csv')
    notes = rows(LOCAL / 'notes.csv')
    mapped = {r['ordinal']: r for r in notes}
    if len(notes) != len(mapped) or len(notes) != len(manifest) or len(manifest) != len(official):
        raise ValueError('Incomplete or duplicated review')
    extras = defaultdict(list)
    extra_manifest = LOCAL / 'extra_manifest.csv'
    if extra_manifest.exists():
        requested = rows(LOCAL / 'extra_requests.csv')
        completed = rows(extra_manifest)
        def request_key(item):
            return item['ordinal'], item['view'], float(item['fraction'])
        if Counter(map(request_key, requested)) != Counter(map(request_key, completed)):
            raise ValueError('Extra views differ from the private request list')
        for e in completed:
            extras[e['ordinal']].append(e)
    elif (LOCAL / 'extra_requests.csv').exists():
        raise ValueError('Run --extra before summarizing requested alternate views')
    if extras.keys() - mapped.keys():
        raise ValueError('Extra views contain unknown units')
    for r, u in zip(manifest, official):
        n = mapped[r['ordinal']]
        if (any(r[k] != v for k, v in u.items()) or any(n[k] not in GRADES for k in ('object', 'action'))
                or n['unit_id'] != u['unit_id'] or not n['observation'].strip()):
            raise ValueError(f'Invalid review for {r["ordinal"]}')
        a, b, c = [float(r[k + '_s']) for k in ('early', 'mid', 'late')]
        ids = [int(r[k + '_index']) for k in ('early', 'mid', 'late')]
        if not float(u['start_s']) <= a < b < c < float(u['end_s']) or not ids[0] < ids[1] < ids[2]:
            raise ValueError('Invalid sample clock')
        for tag in ('early', 'mid', 'late'):
            with Image.open(LOCAL / r[tag + '_image']) as image:
                image.verify()
        for e in extras[r['ordinal']]:
            if (e['unit_id'] != u['unit_id'] or not a <= float(e['time_s']) <= c
                    or not ids[0] <= int(e['index']) <= ids[-1]):
                raise ValueError('Extra view indexed outside its interval')
            with Image.open(LOCAL / e['image']) as image:
                image.verify()
        r.update(object=n['object'], action=n['action'], overall=max((n['object'], n['action']), key=GRADES.index),
                 observation=n['observation'], extra_samples=len(extras[r['ordinal']]))
    write_csv(LOCAL / 'reviewed.csv', manifest)
    summary = []
    for field, values in [('all', ['all']), ('dataset', sorted({r['dataset'] for r in manifest})),
                          ('split', ['train', 'validation', 'test']), ('eligible', ['1', '0'])]:
        for value in values:
            group = [r for r in manifest if field == 'all' or r[field] == value]
            for dim in ('object', 'action', 'overall'):
                counts = Counter(r[dim] for r in group)
                summary.append(dict(group_by=field, group=value, dimension=dim, intervals=len(group),
                                    records=len({r['alias'] for r in group}), head_left_samples=3*len(group),
                                    extra_intervals=sum(r['extra_samples'] > 0 for r in group),
                                    extra_samples=sum(r['extra_samples'] for r in group),
                                    **{k: counts[k] for k in GRADES}))
    write_csv(LOCAL / 'review_summary.csv', summary)
    print(summary[2])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--aggregate', action='store_true')
    mode.add_argument('--extra', action='store_true', help='Read private extra_requests.csv for alternate views')
    args = parser.parse_args()
    os.umask(0o077)
    if args.aggregate:
        aggregate(args.root)
    elif args.extra:
        extra(args.root)
    else:
        build(args.root)
