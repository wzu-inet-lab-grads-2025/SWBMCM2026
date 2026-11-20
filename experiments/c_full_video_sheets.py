"""Local fixed-time, all-camera contact sheets for the C descriptive audit.

AI-assisted preprocessing/visual audit, OpenCode, OpenAI GPT family,
GPT-6.2 Astra routing group, 2026-11-21. Exact release date unavailable.
"""

import argparse
from collections import Counter
import csv
import io
import json
import os
from pathlib import Path
import subprocess

from PIL import Image, ImageDraw, ImageFont, ImageOps

if __package__:
    from .c_records import ROOT, official_records, raw_metadata
else:
    from c_records import ROOT, official_records, raw_metadata


LOCAL = Path(__file__).resolve().parents[1] / 'runs/c_full_visual'
VIEWS = ('head_left', 'head_right', 'left_wrist', 'right_wrist')
FRACTIONS = (0.1, 0.5, 0.9)
SOURCES = {'EgoDemo': 'D', 'EgoPro': 'P', 'EgoStandard': 'S'}
SIGNS = ('both_hands', 'held_object', 'partial_hand', 'wrist_detail')


def write_csv(path, data):
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=data[0], lineterminator='\n')
        writer.writeheader()
        writer.writerows(data)


def read_csv(path):
    with path.open(newline='', encoding='utf-8') as handle:
        return list(csv.DictReader(handle))


def inventory(root):
    by_uuid = {r['episode_uuid']: r for r in official_records(root)}
    seen = set()
    for source, prefix in SOURCES.items():
        folders = sorted(d for d in (root / 'Raw_Data' / source).iterdir() if d.is_dir())
        for ordinal, folder in enumerate(folders, 1):
            meta = raw_metadata(folder)
            uuid = meta['episode_uuid']
            record = by_uuid[uuid]
            if (uuid in seen or record['dataset'] != source or
                    record['environment_id'] != meta['environment_id']):
                raise ValueError(f'Split mismatch in {folder}')
            seen.add(uuid)
            videos = {}
            for path in (folder / 'videos').rglob('*.mp4'):
                view = (path.relative_to(folder / 'videos').parts[0].split('.')[-1]
                        if source == 'EgoDemo' else path.stem)
                if view not in VIEWS or view in videos:
                    raise ValueError(f'Unexpected camera/chunks: {path}')
                videos[view] = path
            if not videos:
                raise ValueError(f'No cameras in {folder}')
            yield {'source': source, 'alias': f'{prefix}{ordinal:02d}', 'sample': folder.name,
                   'episode_uuid': uuid, 'split': record['split']}, videos
    if seen != set(by_uuid):
        raise ValueError('Raw folders and official records differ')


def duration(path):
    result = subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                                      '-show_entries', 'stream=duration', '-of', 'json', str(path)])
    seconds = float(json.loads(result)['streams'][0]['duration'])
    if seconds <= 0:
        raise ValueError(f'Unexpected duration: {path}')
    return seconds


def extract(path, second, target):
    # Input seeking decodes only the needed GOP. One process, one decoder thread.
    data = subprocess.check_output([
        'ffmpeg', '-nostdin', '-loglevel', 'error', '-threads', '1', '-ss', f'{second:.6f}',
        '-i', str(path), '-map', '0:v:0', '-frames:v', '1', '-filter_threads', '1',
        '-vf', 'scale=512:384:force_original_aspect_ratio=decrease', '-threads', '1',
        '-f', 'image2pipe', '-vcodec', 'mjpeg', '-q:v', '3', '-'])
    with Image.open(io.BytesIO(data)) as image:
        image.verify()
    target.write_bytes(data)


def draw_page(source, number, groups):
    name = f'{source}-{number:02d}'
    cameras = [row for group in groups for row in group]
    canvas = Image.new('RGB', (996, 30 + len(cameras) * 247), '#181818')
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.truetype('DejaVuSans.ttf', 16)
    draw.text((8, 5), f'{name}     |     from left: 10%, 50%, 90% of common duration', font=font)
    for i, row in enumerate(cameras):
        y = 30 + i * 247
        for col, shot in enumerate(row):
            x = col * 332
            with Image.open(LOCAL / shot['image']) as image:
                thumb = ImageOps.contain(image, (330, 222), Image.Resampling.LANCZOS)
                canvas.paste(thumb, (x + (332 - thumb.width) // 2, y + 23))
            draw.text((x + 5, y + 1), f"{shot['alias']}   {shot['view']}   {shot['seek_s']} s", font=font)
            shot['page'] = name
    canvas.save(LOCAL / 'pages' / f'{name}.jpg', quality=92)
    return name


def index_pages(names):
    links = []
    for i, name in enumerate(names):
        href = f'pages/{name}.html'
        links.append(f'<li><a href="{href}">{name}</a></li>')
        nav = ['<a href="../index.html">All pages</a>']
        if i:
            nav.append(f'<a href="{names[i-1]}.html">Previous</a>')
        if i + 1 < len(names):
            nav.append(f'<a href="{names[i+1]}.html">Next</a>')
        (LOCAL / href).write_text(f'<!doctype html><meta charset="utf-8"><title>{name}</title>'
                                  + ' | '.join(nav) + f'<p><img style="max-width:100%" '
                                  f'src="{name}.jpg" alt="{name} contact sheet">')
    (LOCAL / 'index.html').write_text('<!doctype html><meta charset="utf-8"><title>Camera sheets</title>'
                                     '<h1>Camera sheets</h1><ol>' + ''.join(links) + '</ol>')


def build(root):
    (LOCAL / 'pages').mkdir(parents=True, exist_ok=True)
    (LOCAL / 'images').mkdir(exist_ok=True)
    shots, names, source, pending, number = [], [], None, [], 1
    for record, videos in inventory(root):
        if pending and (source != record['source'] or sum(map(len, pending)) + len(videos) > 4):
            names.append(draw_page(source, number, pending))
            number += 1
            pending = []
        if source != record['source']:
            number = 1
        source = record['source']
        common_seconds = min(duration(video) for video in videos.values())
        group = []
        for view in VIEWS:
            if view not in videos:
                continue
            row = []
            for fraction in FRACTIONS:
                second = common_seconds * fraction
                image_path = Path('images') / f"{record['alias']}_{view}_{int(fraction*100)}.jpg"
                extract(videos[view], second, LOCAL / image_path)
                shot = {**record, 'view': view, 'fraction': fraction, 'seek_s': f'{second:.6f}',
                        'video': str(videos[view].relative_to(root)), 'image': str(image_path)}
                row.append(shot)
                shots.append(shot)
            group.append(row)
        pending.append(group)
    if pending:
        names.append(draw_page(source, number, pending))
    index_pages(names)
    write_csv(LOCAL / 'samples.csv', shots)
    print(f'{len(names)} pages, {len(shots)} images; local index {LOCAL / "index.html"}')


def aggregate():
    shots = read_csv(LOCAL / 'samples.csv')
    notes = read_csv(LOCAL / 'review.csv')
    by_alias = {r['alias']: r for r in notes}
    if len(notes) != len(by_alias) or set(by_alias) != {s['alias'] for s in shots}:
        raise ValueError('Local review must cover each record exactly once')
    output = []
    for source in [*SOURCES, 'ALL']:
        group = [s for s in shots if source == 'ALL' or s['source'] == source]
        records = {s['alias']: s['split'] for s in group}
        counts = Counter(records.values())
        cameras = {(s['alias'], s['view']) for s in group}
        observed = {(s['alias'], s['view'], float(s['fraction'])) for s in group}
        expected = {(alias, view, f) for alias, view in cameras for f in FRACTIONS}
        if len(observed) != len(group) or observed != expected:
            raise ValueError('Unexpected sampling density')
        wrists = {alias for alias, v in cameras if v.endswith('wrist')}
        result = dict(source=source, records=len(records), train=counts['train'],
                      validation=counts['validation'], test=counts['test'],
                      pages=len({s['page'] for s in group}), cameras=len(cameras), images=len(group),
                      head_pairs=sum(v == 'head_left' for _, v in cameras), wrist_pairs=len(wrists))
        for sign in SIGNS:
            eligible = wrists if sign == 'wrist_detail' else records
            for alias in records:
                valid = ('1', '?') if alias in eligible else ('na',)
                if by_alias[alias][sign] not in valid or not by_alias[alias]['observation'].strip():
                    raise ValueError(f'Invalid local review: {alias} {sign}')
            result[sign + '_confirmed'] = sum(by_alias[a][sign] == '1' for a in eligible)
        output.append(result)
    target = LOCAL / 'visual_summary.csv'
    target.parent.mkdir(parents=True, exist_ok=True)
    write_csv(target, output)
    print(target)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--aggregate', action='store_true', help='Summarize existing local review notes')
    args = parser.parse_args()
    os.umask(0o077)
    if args.aggregate:
        aggregate()
    else:
        build(args.root)
