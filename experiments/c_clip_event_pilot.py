"""Prepare private, blinded views for a six-clip train-only event reading.

OpenCode assisted data preparation, OpenAI GPT family, GPT-6.2 Astra routing
group, 2026-11-21. Exact model release date unavailable.
"""

import argparse
import csv
import hashlib
import json
import os
from collections import Counter
from decimal import Decimal
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from .c_all_segment_review import capture, clock
from .c_records import ROOT, official_records, raw_metadata, rows


OUT = Path('runs/c_clip_event_pilot')
EVENTS = ('moves_with_hand', 'supported_separated')
STATUS = ('seen', 'unclear', 'not_seen')


def select(root):
    records = {r['episode_uuid']: r for r in official_records(root)}
    candidates = []
    for u in rows(root/'Restocking_Tasks_and_Constraints/source_segment_index.csv'):
        if u['split'] == 'train' and u['eligible'] == '1':
            assert all(u[k] == records[u['episode_uuid']][k] for k in ('split','dataset','environment_id'))
            length = Decimal(u['end_s']) - Decimal(u['start_s'])
            if Decimal('1.5') <= length <= Decimal('8.5'):
                candidates.append(u)
    used, sources, environments = set(), Counter(), Counter()
    def rank(u):
        digest = hashlib.sha256(('clip-event-v1/' + u['unit_id']).encode()).hexdigest()
        return environments[u['environment_id']], sources[u['dataset']], digest
    for family in ('L','L','P','P','A','A'):
        u = min((r for r in candidates if r['family_id'] == family and r['episode_uuid'] not in used), key=rank)
        used.add(u['episode_uuid'])
        sources[u['dataset']] += 1
        environments[u['environment_id']] += 1
        yield u


def sheets(path, name, times, ids, start):
    for offset in range(0, len(ids), 12):
        batch = ids[offset:offset+12]
        canvas = Image.new('RGB', (4*300, ((len(batch)+3)//4)*250), '#202020')
        draw = ImageDraw.Draw(canvas)
        for i, idx in enumerate(batch):
            x, y = i%4*300, i//4*250
            with Image.open(path/f'{idx:06}.jpg') as image:
                thumb = ImageOps.contain(image,(298,225))
                canvas.paste(thumb,(x,y+22))
            draw.text((x+3,y+4), f'{name}  K{offset+i+1:02}  +{times[idx]-start:.3f}s',fill='white')
        canvas.save(path.parent/f'{name}_{path.name}_{offset//12+1}.jpg',quality=91)


def review_index(manifest):
    pages = {r['episode_uuid']: f'R{i:02}.html' for i,r in enumerate(official_records(ROOT),1)}
    text = ['# 训练视频复核入口', '',
            '在记录查看器输入相应的源帧号，用上一帧、下一帧检查事件前后。初次复核保持官方标注隐藏。',
            '试读图 K 序号从 1 起，源帧从 0 起。每名队员可复制 [空表](blank.csv) 独立填写。', '']
    for m in manifest:
        frames = m['source_frames']
        page = pages[m['record']]
        text.extend([f"## {m['clip']}", '',
                     f"[同步原帧查看器](../c_dense_frame_viewer/{page})，本段源帧 {frames[0]}–{frames[-1]}。", '',
                     ' · '.join(f'K{i:02}={n}' for i,n in enumerate(frames,1)), '',
                     ' · '.join(f'[{s.stem}](blind/{m["clip"]}/{s.name})' for s in sorted((OUT/'blind'/m['clip']).glob('*.jpg'))), ''])
    (OUT/'复核入口.md').write_text('\n'.join(text),encoding='utf-8')


def compare(manifest):
    sizes = {clip['clip']: len(clip['source_frames']) for clip in manifest}
    keys = {(clip, hand, event) for clip in sizes for hand in ('left', 'right') for event in EVENTS}

    def annotations(reader):
        result = {}
        with (OUT/f'reader_{reader}.csv').open(encoding='utf-8-sig') as f:
            for r in csv.DictReader(f):
                k = (r['clip'],r['hand'],r['event'])
                if k not in keys or k in result or r['status'] not in STATUS:
                    raise ValueError(f'{reader}: invalid row or repeated key')
                if r['status'] == 'seen':
                    lo,hi = int(r['before_k']),int(r['after_k'])
                    if not 1 <= lo < hi <= sizes[k[0]] or not r['object'].strip():
                        raise ValueError(f'{reader}: invalid event bracket or missing object')
                elif r['before_k'] or r['after_k']:
                    raise ValueError(f'{reader}: bracket for an unresolved event')
                result[k] = r
        if set(result) != keys:
            raise ValueError(f'{reader}: missing ratings')
        return result
    first_reader, second_reader = annotations('A'), annotations('B')
    for event in EVENTS:
        cells = Counter()
        overlap = 0
        for key in sorted(keys):
            if key[2] != event:
                continue
            a, b = first_reader[key], second_reader[key]
            cells[a['status'], b['status']] += 1
            if a['status'] == b['status'] == 'seen':
                start = max(int(a['before_k']), int(b['before_k']))
                end = min(int(a['after_k']), int(b['after_k']))
                overlap += start < end
        print(event, 'rows=A, cols=B, status order', STATUS)
        print([[cells[i,j] for j in STATUS] for i in STATUS], 'both-seen overlapping brackets', overlap)


def prepare():
    os.umask(0o077)
    OUT.mkdir(parents=True, exist_ok=True)
    selected = sorted(select(ROOT), key=lambda r: hashlib.sha256(r['unit_id'].encode()).digest())
    manifest, form = [], []
    for number,u in enumerate(selected,1):
        name = f'C{number:02}'
        clip = OUT/'blind'/name
        folder = ROOT/'Raw_Data'/u['dataset']/u['sample']
        assert raw_metadata(folder)['episode_uuid'] == u['episode_uuid']
        videos = {v.relative_to(folder/'videos').parts[0].split('.')[-1] if u['dataset']=='EgoDemo' else v.stem: v
                  for v in (folder/'videos').rglob('*.mp4')}
        times, labels = clock(folder,videos['head_left'])
        a,b,_ = labels[int(u['source_segment_index'])]
        start,end = float(u['start_s']),float(u['end_s'])
        assert (float(a),float(b)) == (start,end)
        # Same stored timestamps as the existing frame viewer. Enforce exact half-open endpoints.
        source_times = [Decimal.from_float(float(t)) if u['dataset'] == 'EgoDemo' else Decimal(str(t)) for t in times]
        lower, upper = Decimal(u['start_s']), Decimal(u['end_s'])
        within = np.array([i for i,t in enumerate(source_times) if lower <= t < upper])
        assert len(within) > 1
        targets = np.arange(start, end, .25)
        nearest = {int(within[np.argmin(abs(times[within] - t))]) for t in targets}
        ids = sorted(nearest | {int(within[-1])})
        for view, video in sorted(videos.items()):
            directory = clip/view
            directory.mkdir(parents=True,exist_ok=True)
            other,_=clock(folder,video)
            assert np.array_equal(times,other)
            capture(video,ids,directory)
            sheets(directory,name,times,ids,start)
        for hand in ('left','right'):
            for event in EVENTS:
                form.append(dict(clip=name,hand=hand,event=event,status='',before_k='',after_k='',object='',note=''))
        manifest.append(dict(clip=name,unit=u['unit_id'],record=u['episode_uuid'],family=u['family_id'],
                             environment=u['environment_id'],dataset=u['dataset'],source_start=start,source_end=end,
                             views=sorted(videos),source_frames=ids,source_times_s=times[ids].tolist()))
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    with (OUT/'blank.csv').open('w',newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(form[0]))
        writer.writeheader()
        writer.writerows(form)
    review_index(manifest)
    print('clips',len(manifest),'records',len({m['record'] for m in manifest}),
          'environments',len({m['environment'] for m in manifest}),'sources',dict(Counter(m['dataset'] for m in manifest)),
          'source ticks',sum(len(m['source_frames']) for m in manifest),'sheets',len(list((OUT/'blind').glob('*/*.jpg'))))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',nargs='?',default='prepare',choices=('prepare','index','compare'))
    action=parser.parse_args().action
    if action=='prepare':
        prepare()
    else:
        manifest=json.loads((OUT/'manifest.json').read_text())
        if action=='index':
            review_index(manifest)
        else:
            compare(manifest)
