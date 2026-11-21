"""Map all C video frames to the exact raw semantic clock and extract independent image evidence.

Run from the repository root. Per-view arrays and the frame/segment lookup are private.
AI-assisted attachment audit, 2026-11-21.
"""

import argparse
from bisect import bisect_left, bisect_right
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from fractions import Fraction
import json
import os
from pathlib import Path
import subprocess
import tempfile

import numpy as np
import pandas as pd
from scipy.fft import rfft2, irfft2

if __package__:
    from .c_full_video_decode import BASE, ROOT, FRAME, TIME_BASE, inventory, probe
    from .c_records import raw_metadata, rows
else:
    from c_full_video_decode import BASE, ROOT, FRAME, TIME_BASE, inventory, probe
    from c_records import raw_metadata, rows


RUN = BASE / 'runs/c_dense_semantic'
H, W, PAD, BLOCK = 112, 144, 8, 16
HH, WW = H - 2 * PAD, W - 2 * PAD
WINDOW = np.outer(np.hanning(H), np.hanning(W)).astype('float32')
SHIFTS = np.arange(-PAD, PAD + 1)
LAGS = np.ix_(SHIFTS % H, SHIFTS % W)
NS = 10**9


def slices(times, starts, ends):
    owner = np.full(len(times), -1, dtype='int16')
    closed = np.zeros(len(times), dtype='int16')
    exact_start = np.zeros(len(times), dtype=bool)
    exact_end = np.zeros(len(times), dtype=bool)
    for i, (a, b) in enumerate(zip(starts, ends)):
        lo, hi = bisect_left(times, a), bisect_left(times, b)
        if hi <= lo or np.any(owner[lo:hi] >= 0):
            raise ValueError('Empty or overlapping source interval')
        owner[lo:hi] = i
        closed[lo:bisect_right(times, b)] += 1
        if lo < len(times) and times[lo] == a:
            exact_start[lo] = True
        if hi < len(times) and times[hi] == b:
            exact_end[hi] = True
    return owner, closed, exact_start, exact_end


def collect(root):
    units = defaultdict(list)
    for row in rows(root / 'Restocking_Tasks_and_Constraints/source_segment_index.csv'):
        units[row['episode_uuid']].append(row)
    jobs = inventory(root)
    records = {}
    for job in jobs:
        folder = root.joinpath(*job['path'].relative_to(root).parts[:3])
        key = job['record_id']
        if key not in records:
            meta = raw_metadata(folder)
            group = sorted(units[meta['episode_uuid']], key=lambda u: int(u['source_segment_index']))
            if job['dataset'] == 'EgoDemo':
                raw = json.loads((folder / 'annotation.json').read_text(), parse_float=Fraction)
                spans = [(Fraction(s['start_time']), Fraction(s['end_time']), s['description'])
                         for s in raw['subtask_annotations']]
                origin = None
                times = [Fraction(float(t)) for t in job['stamps']]
            else:
                raw = rows(folder / 'tables/annotation_semantic_segments.csv')
                spans = [(Fraction(s['segment.start_time']), Fraction(s['segment.end_time']),
                          s['segment.subtask_description']) for s in raw]
                origin = Fraction(int(raw[0]['header.ts'])) - spans[0][0] * NS
                if origin.denominator != 1 or origin != int(job['stamps'][0]):
                    raise ValueError('Unexpected CSV annotation origin')
                origin = int(origin)
                times = [Fraction(int(t) - origin, NS) for t in job['stamps']]
            if len(spans) != len(group) or not group or times[0] != 0:
                raise ValueError('Source record does not align to index')
            for i, (u, span) in enumerate(zip(group, spans)):
                if (int(u['source_segment_index']) != i or u['sample'] != folder.name or
                        u['dataset'] != job['dataset'] or u['split'] != job['split'] or
                        u['environment_id'] != meta['environment_id'] or
                        (Fraction(u['start_s']), Fraction(u['end_s']), u['description']) != span or
                        (i and spans[i-1][1] != span[0])):
                    raise ValueError('Raw annotation or index disagrees')
            starts, ends = zip(*[(a, b) for a, b, _ in spans])
            records[key] = dict(segments=[{k: u[k] for k in ('source_segment_index', 'unit_id',
                          'start_s', 'end_s', 'eligible')} for u in group],
                          episode_uuid=meta['episode_uuid'], environment_id=meta['environment_id'],
                          sample=folder.name, source_origin_ns=origin,
                          source=slices(times, starts, ends), times=times)
        r = records[key]
        job.update({k: v for k, v in r.items() if k != 'times'})
        job['source_s'] = np.asarray(r['times'], dtype='float64')
        if job['dataset'] != 'EgoDemo':
            side = pd.read_csv(job['path'].with_name(job['view'] + '_frames.csv'),
                               usecols=['video_timestamp_ns', '_message_index', 'frame_id'])
            if (not np.array_equal(side.video_timestamp_ns, job['stamps']) or
                    not np.array_equal(side._message_index, np.arange(len(side))) or
                    not (side.frame_id == job['view'] + '_camera').all()):
                raise ValueError('Source sidecar, index or camera mismatch')
    if (len(jobs), sum(len(j['source_s']) for j in jobs), len(records),
            sum(len(r['segments']) for r in records.values())) != (184, 178512, 69, 349):
        raise ValueError('Unexpected full attachment inventory')
    return jobs


def tiles(image):
    return image.reshape(*image.shape[:-2], HH // BLOCK, BLOCK, WW // BLOCK, BLOCK).mean(
        axis=(-1, -3), dtype=np.float32).reshape(*image.shape[:-2], -1)


def compensated_difference(current, prior, dy, dx):
    # Compare the same interior target support for both the proposed shift and zero.
    diff = current[PAD:-PAD, PAD:-PAD] - prior[PAD-dy:PAD-dy+HH, PAD-dx:PAD-dx+WW]
    brightness_shift = np.median(diff)
    return tiles(np.abs(diff - brightness_shift)), brightness_shift


def measure(image, prior, prior_fft):
    y = image[0].astype(np.float32)
    appearance = tiles(image[:, PAD:-PAD, PAD:-PAD])
    current_fft = rfft2((y - y.mean()) * WINDOW, workers=1)
    if prior is None:
        return y, current_fft, appearance, None
    product = current_fft * prior_fft.conj()
    corr = irfft2(product / np.maximum(np.abs(product), 1e-6), s=(H, W), workers=1)[LAGS]
    iy, ix = np.unravel_index(np.argmax(corr), corr.shape)
    dy, dx = int(SHIFTS[iy]), int(SHIFTS[ix])
    zero, exposure = compensated_difference(y, prior, 0, 0)
    residual = zero
    if dy or dx:
        shifted, shifted_exposure = compensated_difference(y, prior, dy, dx)
        if np.median(shifted) < np.median(zero):
            residual, exposure = shifted, shifted_exposure
        else:
            dx = dy = 0
    raw = np.abs(y[PAD:-PAD, PAD:-PAD] - prior[PAD:-PAD, PAD:-PAD]).mean()
    return y, current_fft, appearance, (residual, dx, dy, raw, exposure)


def extract(job, output):
    path = job['path']
    stream, packet_pts, _ = probe(path)
    n = len(job['stamps'])
    if len(packet_pts) != n or int(stream['nb_frames']) != n:
        raise ValueError('Packet/source count mismatch')
    cmd = ['timeout', '--kill-after=5s', '900s', 'ffmpeg', '-nostdin', '-hide_banner', '-nostats',
           '-loglevel', 'info', '-xerror', '-err_detect', 'explode', '-copyts', '-threads', '1',
           '-noautorotate', '-i', str(path), '-map', '0:v:0', '-filter_threads', '1', '-vf',
           f'showinfo=checksum=0,scale={W}:{H}:flags=area:out_range=full,format=yuv444p',
           '-vsync', '0', '-threads', '1', '-f', 'rawvideo', 'pipe:1']
    appearance = np.empty((n, 3, 48), dtype='float32')
    residual = np.full((n, 48), np.nan, dtype='float32')
    motion = np.full((n, 4), np.nan, dtype='float32')
    i, prior, prior_fft = 0, None, None
    with tempfile.TemporaryFile(dir=output) as log:
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=log) as proc:
            try:
                while block := proc.stdout.read(12 * 3 * H * W):
                    for img in np.frombuffer(block, dtype='uint8').reshape(-1, 3, H, W):
                        prior, prior_fft, appearance[i], diff = measure(img, prior, prior_fft)
                        if diff is not None:
                            residual[i] = diff[0]
                            motion[i] = diff[1:]
                        i += 1
            finally:
                proc.stdout.close()
        if proc.returncode or i != n:
            raise ValueError(f'Decoder exited {proc.returncode}, {i}/{n} frames')
        log.seek(0)
        count, bases = 0, set()
        for line in log:
            if b'Parsed_showinfo' not in line:
                continue
            if found := TIME_BASE.search(line):
                bases.add(found[1].decode())
            if b' pts_time:' in line:
                found = FRAME.search(line)
                if (not found or int(found[1]) != count or int(found[2]) != packet_pts[count] or
                        found[3].decode() != stream['pix_fmt'] or int(found[4]) != stream['width'] or
                        int(found[5]) != stream['height']):
                    raise ValueError('Presentation order or native frame format disagrees')
                count += 1
        if count != n or bases != {stream['time_base']}:
            raise ValueError('Incomplete presentation timestamp log')
    owner, closed, exact_start, exact_end = job['source']
    tick = Fraction(stream['time_base'])
    pts_times = [int(p - packet_pts[0]) * tick for p in packet_pts]
    segments = job['segments']
    pts_owner = slices(pts_times, [Fraction(s['start_s']) for s in segments],
                       [Fraction(s['end_s']) for s in segments])[0]
    stem = output / f"view_{job['file_id']:03d}"
    with tempfile.NamedTemporaryFile(dir=output, delete=False) as tmp:
        np.savez_compressed(tmp, pts=packet_pts, source_stamp=job['stamps'], source_s=job['source_s'],
                            segment=owner, pts_segment=pts_owner, closed_count=closed,
                            exact_start=exact_start, exact_end=exact_end, appearance=appearance,
                            residual=residual, dx=motion[:, 0], dy=motion[:, 1],
                            raw_mad=motion[:, 2], exposure=motion[:, 3])
    os.replace(tmp.name, stem.with_suffix('.npz'))
    meta = {k: job[k] for k in ('file_id', 'record_id', 'dataset', 'split', 'view', 'environment_id',
            'episode_uuid', 'sample', 'source_origin_ns', 'segments')}
    meta.update(path='/'.join(path.parts[path.parts.index('Raw_Data'):]), time_base=stream['time_base'])
    stem.with_suffix('.json').write_text(json.dumps(meta, indent=2) + '\n')
    return n


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=ROOT)
    p.add_argument('--run-dir', type=Path, default=RUN)
    p.add_argument('--workers', type=int, choices=range(1, 4), default=3)
    args = p.parse_args()
    os.umask(0o077)
    jobs = collect(args.root.resolve())
    output = args.run_dir / 'views'
    output.mkdir(parents=True, exist_ok=False)
    print(f'{len(jobs)} streams; {args.workers} workers', flush=True)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(extract, job, output) for job in sorted(jobs, key=lambda j: -j['path'].stat().st_size)]
        total = 0
        for i, f in enumerate(as_completed(futures), 1):
            total += f.result()
            if i % 10 == 0 or i == len(jobs):
                print(f'{i}/{len(jobs)} streams, {total} frames', flush=True)
    if total != 178512:
        raise ValueError('Incomplete decode')
    (args.run_dir / 'complete.json').write_text(json.dumps(dict(streams=len(jobs), frames=total))+'\n')


if __name__ == '__main__':
    main()
