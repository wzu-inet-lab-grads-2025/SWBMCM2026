"""Decode every official C camera stream and audit relative presentation clocks.

Private per-frame arrays contain timing and scalar measurements only. Run from
the repository root with the local C attachment, FFmpeg and GNU timeout.
AI-assisted audit in OpenCode, general-pro, 2026-11-21. Exact routed model
version and release date unavailable.
"""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from fractions import Fraction
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

if __package__:
    from .c_audit_fields import relative_ns
    from .c_audit_segments import DATASETS, POSES
    from .c_records import ROOT, official_records, raw_metadata
else:
    from c_audit_fields import relative_ns
    from c_audit_segments import DATASETS, POSES
    from c_records import ROOT, official_records, raw_metadata


HEAD = ('head_left', 'head_right')
WRISTS = ('left_wrist', 'right_wrist')
PIXELS = 64 * 48
BATCH = 32
BASE = Path(__file__).resolve().parents[1]
TIME_BASE = re.compile(rb'config in time_base: (\d+/\d+)')
FRAME = re.compile(rb'\bn:\s*(\d+) pts:\s*(-?\d+) pts_time:.*? fmt:(\S+) .*?\bs:(\d+)x(\d+) ')


def inventory(root):
    index = {r['episode_uuid']: r for r in official_records(root)}
    seen, jobs = set(), []
    for dataset in DATASETS:
        for folder in sorted(p for p in (root / 'Raw_Data' / dataset).iterdir() if p.is_dir()):
            meta = raw_metadata(folder)
            uuid = meta['episode_uuid']
            record = index[uuid]
            if (uuid in seen or dataset != record['dataset'] or
                    meta['environment_id'] != record['environment_id']):
                raise ValueError('Raw record and official split disagree')
            seen.add(uuid)
            paths = sorted(folder.rglob('*.mp4'))
            if dataset == 'EgoDemo':
                info = json.loads((folder / 'meta/info.json').read_text())
                declared = {k.removeprefix('observation.images.'): v['shape']
                            for k, v in info['features'].items() if v['dtype'] == 'video'}
                views = [p.parents[1].name.removeprefix('observation.images.') for p in paths]
                if set(declared) not in (set(HEAD), set(HEAD + WRISTS)):
                    raise ValueError('Unexpected Demo camera declaration')
                timeline = pq.read_table(sorted((folder / 'data').rglob('*.parquet')),
                                         columns=['frame_index', 'timestamp'], use_threads=False)
                frame_index = timeline['frame_index'].to_numpy()
                if not np.array_equal(frame_index, np.arange(len(frame_index))):
                    raise ValueError('Demo frame_index differs from physical row')
                stamps = timeline['timestamp'].to_numpy().astype(np.float64)
                trajectory = stamps - stamps[0]
                if len(stamps) != info['total_frames'] or len(stamps) != meta['length']:
                    raise ValueError('Demo declared trajectory length differs')
            else:
                views = [p.stem for p in paths]
                declared = dict.fromkeys(HEAD + (WRISTS if dataset == 'EgoPro' else ()))
                clocks = [pd.read_csv(folder / 'tables' / f'{name}.csv', usecols=['header.ts'],
                                      dtype='int64')['header.ts'].to_numpy()
                          for name in (*POSES, 'annotation_bad_frame_pose_hand')]
                stamps = clocks[POSES.index('pose_right_hand')]
                if any(not np.array_equal(c, stamps) for c in clocks):
                    raise ValueError('CSV pose/quality timeline mismatch')
                trajectory = relative_ns(stamps)
            if sorted(views) != sorted(declared) or not np.isfinite(trajectory).all():
                raise ValueError('Unexpected camera coverage or invalid trajectory timestamp')
            sidecars = set(folder.rglob('*_frames.csv'))
            expected_sidecars = ({p.with_name(f'{p.stem}_frames.csv') for p in paths}
                                 if dataset != 'EgoDemo' else set())
            if sidecars != expected_sidecars:
                raise ValueError('Sidecar inventory differs from video inventory')
            for path, view in zip(paths, views):
                jobs.append(dict(file_id=len(jobs), record_id=len(seen), dataset=dataset,
                                 split=record['split'], view=view, path=path, stamps=stamps,
                                 trajectory=trajectory, shape=declared[view]))
    if seen != set(index) or {j['path'] for j in jobs} != set((root / 'Raw_Data').rglob('*.mp4')):
        raise ValueError('Official records or extracted MP4 inventory incomplete')
    return jobs


def probe(path):
    entries = ('stream=codec_type,codec_name,width,height,pix_fmt,r_frame_rate,avg_frame_rate,time_base,'
               'start_pts,nb_frames:packet=pts,duration,flags')
    data = subprocess.run(['ffprobe', '-v', 'error', '-threads', '1',
                           '-show_entries', entries, '-of', 'json', str(path)],
                          capture_output=True, timeout=90, check=True)
    if data.stderr.strip():
        raise ValueError(f'ffprobe error: {data.stderr[-1000:]!r}')
    output = json.loads(data.stdout)
    stream, = output['streams']
    if stream['codec_type'] != 'video':
        raise ValueError('Expected one video stream in MP4')
    packets = output['packets']
    ticks = np.fromiter((int(p['pts']) for p in packets), np.int64)
    if any(int(p['duration']) <= 0 or 'C' in p['flags'] for p in packets):
        raise ValueError('Zero packet duration or corrupt packet flag')
    order = np.argsort(ticks)
    sorted_ticks = ticks[order]
    steps = np.diff(sorted_ticks)
    durations = np.array([p['duration'] for p in packets], dtype=np.int64)[order]
    packet_clock = dict(distinct_steps=len(np.unique(steps)),
                        internal_duration_mismatch=int(np.count_nonzero(steps != durations[:-1])),
                        last_minus_prior_ticks=int(durations[-1] - steps[-1]))
    return stream, sorted_ticks, packet_clock


def decode(path, stream, run_dir):
    # Keep original display order, dimensions and tick clock up to showinfo.
    # stderr goes to a temporary file, avoiding backpressure on the image pipe.
    args = ['timeout', '--kill-after=5s', '300s', 'ffmpeg', '-nostdin', '-hide_banner',
            '-nostats', '-loglevel', 'level+info', '-xerror', '-err_detect', 'explode',
            '-copyts', '-threads', '1', '-noautorotate', '-i', str(path), '-map', '0:v:0',
            '-filter_threads', '1', '-vf', 'showinfo=checksum=0,'
            'scale=64:48:flags=area:out_range=full,format=gray', '-vsync', '0',
            '-threads', '1', '-f', 'rawvideo', 'pipe:1']
    sums, maxima, changes = [], [], []
    prior = None
    with tempfile.TemporaryFile(dir=run_dir) as log:
        with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=log) as proc:
            try:
                while block := proc.stdout.read(BATCH * PIXELS):
                    image = np.frombuffer(block, np.uint8).reshape(-1, PIXELS)
                    sums.append(image.sum(axis=1, dtype=np.int64))
                    maxima.append(image.max(axis=1))
                    current = image.astype(np.int16)
                    previous = current[:1] if prior is None else prior
                    diff = np.abs(current - np.concatenate((previous, current[:-1])))
                    changes.append(diff.sum(axis=1, dtype=np.int64))
                    prior = current[-1:].copy()
            finally:
                proc.stdout.close()
        log.seek(0)
        ticks, time_bases, warnings = [], set(), set()
        for line in log:
            if b'[warning]' in line or b'[error]' in line or b'[fatal]' in line:
                warnings.add(line.decode(errors='replace').strip())
            if b'Parsed_showinfo' not in line:
                continue
            if config := TIME_BASE.search(line):
                time_bases.add(config[1].decode())
            if b' pts_time:' in line:
                frame = FRAME.search(line)
                if (not frame or int(frame[1]) != len(ticks) or
                        (frame[3].decode(), int(frame[4]), int(frame[5])) !=
                        (stream['pix_fmt'], stream['width'], stream['height'])):
                    raise ValueError('Decoded frame number or native format/size changed')
                ticks.append(int(frame[2]))
        if proc.returncode or time_bases != {stream['time_base']} or not ticks:
            raise RuntimeError(f'ffmpeg returned {proc.returncode}; time base {time_bases}; {warnings}')
    sums, maxima, changes = map(np.concatenate, (sums, maxima, changes))
    if len(sums) != len(ticks):
        raise ValueError('Raw pixel frame count differs from showinfo frame count')
    changes[0] = -1  # no pair crosses a file boundary
    return np.asarray(ticks, dtype=np.int64), sums, maxima, changes, sorted(warnings)


def audit(job, run_dir, root):
    path, trajectory = job['path'], job['trajectory']
    stream, packets, _ = probe(path)
    ticks, sums, maxima, changes, warnings = decode(path, stream, run_dir)
    if not np.array_equal(packets, ticks) or len(ticks) != int(stream['nb_frames']):
        raise ValueError('Decoded PTS differ from sorted packet PTS or header frame count')
    if job['shape'] and job['shape'][:2] != [stream['height'], stream['width']]:
        raise ValueError('Demo declaration and decoded native resolution differ')
    seconds = (ticks - ticks[0]) * float(Fraction(stream['time_base']))
    paired = min(len(seconds), len(trajectory))
    arrays = dict(pts=ticks, relative_pts_s=seconds, trajectory_s=trajectory,
                  pts_minus_trajectory_s=seconds[:paired] - trajectory[:paired],
                  gray_sum=sums, gray_max=maxima, adjacent_absdiff_sum=changes)
    if job['dataset'] != 'EgoDemo':
        columns = ['video_timestamp_ns', '_message_index', 'frame_id']
        side = pd.read_csv(path.with_name(path.stem + '_frames.csv'), usecols=columns,
                           dtype={'video_timestamp_ns': 'int64', '_message_index': 'int64'})
        stamps = side['video_timestamp_ns'].to_numpy()
        paired = min(len(seconds), len(stamps))
        source = job['stamps']
        common = min(len(stamps), len(source))
        arrays.update(sidecar_ns=stamps, trajectory_ns=source,
                      pts_minus_sidecar_s=seconds[:paired] - relative_ns(stamps)[:paired],
                      sidecar_minus_trajectory_ns=stamps[:common] - source[:common],
                      message_index=side['_message_index'].to_numpy())
        if not (side['frame_id'] == job['view'] + '_camera').all():
            raise ValueError('Sidecar frame_id disagrees with camera name')
    prefix = run_dir / f"file_{job['file_id']:03d}"
    np.savez_compressed(prefix.with_suffix('.npz'), **arrays)
    row = {k: job[k] for k in ('file_id', 'record_id', 'dataset', 'split', 'view')}
    row.update(path=str(path.relative_to(root)), **stream, warnings=warnings)
    prefix.with_suffix('.json').write_text(json.dumps(row, indent=2) + '\n')
    return len(ticks)


def run(root, run_dir, workers):
    if not 1 <= workers <= 6:
        raise ValueError('Choose between 1 and 6 simultaneous video workers')
    jobs = inventory(root)
    run_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    print(f'{len(jobs)} camera files, {workers} workers, one decoder thread each', flush=True)
    total = 0
    # Start the largest inputs early, reducing the final tail of the run.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(audit, job, run_dir, root): job for job in
                   sorted(jobs, key=lambda j: -j['path'].stat().st_size)}
        for done, future in enumerate(as_completed(pending), 1):
            total += future.result()
            if done % 10 == 0 or done == len(jobs):
                print(f'{done}/{len(jobs)} files, {total} decoded frames', flush=True)
    (run_dir / 'complete.json').write_text(json.dumps(dict(files=len(jobs), frames=total)) + '\n')


def read_measurements(run_dir):
    manifest = json.loads((run_dir / 'complete.json').read_text())
    files = []
    for path in sorted(run_dir.glob('file_*.json')):
        row = json.loads(path.read_text())
        with np.load(path.with_suffix('.npz'), allow_pickle=False) as arrays:
            row.update({k: arrays[k] for k in arrays})
        pts, trajectory = row['relative_pts_s'], row['trajectory_s']
        n = len(pts)
        if (n != int(row['nb_frames']) or len(row['pts']) != n or
                not all(len(row[k]) == n for k in ('gray_sum', 'gray_max', 'adjacent_absdiff_sum'))
                or row['adjacent_absdiff_sum'][0] != -1 or (row['adjacent_absdiff_sum'][1:] < 0).any()):
            raise ValueError('Inconsistent private per-frame evidence')
        row['paired_ns'] = np.rint(row['pts_minus_trajectory_s'] * 1e9).astype('int64')
        row['pts_step_ms'] = np.diff(pts) * 1e3
        row['source_step_ms'] = np.diff(trajectory) * 1e3
        row['brightness'] = row['gray_sum'] / PIXELS
        row['adjacent_mad'] = row['adjacent_absdiff_sum'][1:] / PIXELS
        if 'sidecar_ns' in row:
            side, source = row['sidecar_ns'], row['trajectory_ns']
            row['side_missing_keys'] = len(np.setdiff1d(source, side))
            row['side_extra_keys'] = len(np.setdiff1d(side, source))
            row['side_step_ms'] = np.diff(side) * 1e-6
            row['side_index_errors'] = int(np.count_nonzero(row['message_index'] != np.arange(len(side))))
        files.append(row)
    if (len(files) != manifest['files'] or len(list(run_dir.glob('file_*.npz'))) != len(files) or
            len({f['path'] for f in files}) != len(files) or sum(len(f['pts']) for f in files) != manifest['frames']):
        raise ValueError('Incomplete private file inventory')
    anchors = {f['record_id']: f for f in files if f['view'] == 'head_left'}
    for row in files:
        if row['view'] == 'head_left':
            continue
        anchor = anchors[row['record_id']]
        p, q = row['pts'], anchor['pts']
        n = min(len(p), len(q))
        ratio = Fraction(row['time_base']) / Fraction(anchor['time_base'])
        # Exact comparison in a common rational time base (int64 safe for these clips).
        row['cross_pts_unequal'] = int(np.count_nonzero((p[:n] - p[0]) * ratio.numerator !=
                                                       (q[:n] - q[0]) * ratio.denominator))
        row['cross_pts_pairs'] = n
        row['cross_unpaired'] = abs(len(p) - len(q))
    return files


def scope_groups(files):
    for fields in [(), ('dataset',), ('split',), ('view',), ('dataset', 'view')]:
        groups = {}
        for row in files:
            key = tuple(row[f] for f in fields)
            groups.setdefault(key, []).append(row)
        for key, group in sorted(groups.items()):
            yield {'scope': '_'.join(fields) or 'overall', 'dataset': '', 'split': '',
                   'view': '', **dict(zip(fields, key))}, group


def joined(group, key):
    return np.concatenate([f[key] for f in group])


def clock_summary(group):
    delta = joined(group, 'paired_ns')
    abs_delta = np.abs(delta)
    result = dict(records=len({f['record_id'] for f in group}), files=len(group),
                  decoded_frames=sum(len(f['pts']) for f in group), paired_source_rows=len(delta),
                  frames_without_source=sum(max(0, len(f['pts']) - len(f['trajectory_s'])) for f in group),
                  source_without_frame=sum(max(0, len(f['trajectory_s']) - len(f['pts'])) for f in group),
                  delta_min_us=delta.min() / 1e3, delta_max_us=delta.max() / 1e3,
                  abs_delta_p50_us=np.median(abs_delta) / 1e3,
                  abs_delta_p95_us=np.percentile(abs_delta, 95) / 1e3,
                  abs_delta_max_us=abs_delta.max() / 1e3,
                  abs_delta_gt_10us_rows=np.count_nonzero(abs_delta > 10_000),
                  abs_delta_gt_1ms_rows=np.count_nonzero(abs_delta > 1_000_000),
                  abs_delta_gt_1ms_files=sum((np.abs(f['paired_ns']) > 1_000_000).any() for f in group),
                  abs_delta_gt_half_frame_rows=sum(np.count_nonzero(np.abs(f['paired_ns']) >
                      .5e9 / float(Fraction(f['r_frame_rate']))) for f in group),
                  cross_head_left_pairs=sum(f.get('cross_pts_pairs', 0) for f in group),
                  cross_head_left_unequal=sum(f.get('cross_pts_unequal', 0) for f in group),
                  cross_head_left_unpaired=sum(f.get('cross_unpaired', 0) for f in group))
    residuals = [f['pts_minus_trajectory_s'] for f in group]
    result['max_dev_from_endpoint_line_us'] = max(
        np.max(np.abs(d - np.linspace(d[0], d[-1], len(d)))) * 1e6 for d in residuals)
    demo = [f for f in group if f['dataset'] == 'EgoDemo']
    result['demo_f32_formula_rows'] = sum(len(f['trajectory_s']) for f in demo)
    result['demo_f32_formula_unequal'] = sum(np.count_nonzero(
        np.arange(len(f['trajectory_s']), dtype='float32') / np.float32(float(Fraction(f['r_frame_rate'])))
        != f['trajectory_s']) for f in demo)
    sides = [f for f in group if 'sidecar_ns' in f]
    result['side_files'] = len(sides)
    for name in ('pts', 'source', 'side'):
        files = sides if name == 'side' else group
        steps = joined(files, name + '_step_ms') if files else np.array([])
        result.update({name + '_pairs': len(steps),
                       name + '_dt_min_ms': steps.min() if len(steps) else None,
                       name + '_dt_max_ms': steps.max() if len(steps) else None,
                       name + '_nonpositive_pairs': np.count_nonzero(steps <= 0),
                       name + '_gt_50ms_pairs': np.count_nonzero(steps > 50)})
    result['side_rows'] = sum(len(f['sidecar_ns']) for f in sides)
    result['side_paired_src_rows'] = sum(len(f['sidecar_minus_trajectory_ns']) for f in sides)
    result['side_paired_pts_rows'] = sum(len(f['pts_minus_sidecar_s']) for f in sides)
    result['side_vs_pts_unpaired'] = sum(abs(len(f['sidecar_ns']) - len(f['pts'])) for f in sides)
    for key in ('side_missing_keys', 'side_extra_keys', 'side_index_errors'):
        result[key] = sum(f[key] for f in sides)
    if sides:
        d = joined(sides, 'sidecar_minus_trajectory_ns')
        result['side_src_unequal_rows'] = np.count_nonzero(d)
        result['side_src_max_abs_ns'] = np.abs(d).max()
        result['side_pts_max_abs_us'] = np.rint(np.max(np.abs(joined(sides, 'pts_minus_sidecar_s'))) * 1e9) / 1e3
    return result


def image_summary(group):
    gray = joined(group, 'brightness')
    mad = joined(group, 'adjacent_mad')
    return dict(files=len(group), frames=len(gray), pairs=len(mad),
                gray_mean=gray.mean(), gray_p05=np.percentile(gray, 5), gray_p95=np.percentile(gray, 95),
                gray_max_le_5_frames=np.count_nonzero(joined(group, 'gray_max') <= 5),
                adjacent_mad_mean=mad.mean(), adjacent_mad_p50=np.median(mad),
                adjacent_mad_p95=np.percentile(mad, 95), equal_gray_pairs=np.count_nonzero(mad == 0),
                files_with_equal_pairs=sum((f['adjacent_mad'] == 0).any() for f in group))


def format_summary(group):
    fps = [Fraction(f['avg_frame_rate']) for f in group]
    nominal = [Fraction(f['r_frame_rate']) for f in group]
    return dict(files=len(group), codecs='|'.join(sorted({f['codec_name'] for f in group})),
                native_sizes='|'.join(sorted({f"{f['width']}x{f['height']}" for f in group})),
                native_formats='|'.join(sorted({f['pix_fmt'] for f in group})),
                avg_fps_min=float(min(fps)), avg_fps_max=float(max(fps)),
                r_fps_min=float(min(nominal)), r_fps_max=float(max(nominal)),
                avg_fps_distinct=len(set(fps)), avg_ne_r_fps_files=sum(a != b for a, b in zip(fps, nominal)),
                variable_pts_step_files=sum(f['distinct_steps'] != 1 for f in group),
                r_fps_ne_measured_step_files=sum(
                    Fraction(f['r_frame_rate']) * Fraction(f['time_base']) * int(f['pts'][1] - f['pts'][0]) != 1
                    for f in group),
                internal_packet_duration_mismatch=sum(f['internal_duration_mismatch'] for f in group),
                last_packet_ne_prior_files=sum(f['last_minus_prior_ticks'] != 0 for f in group),
                nonzero_first_pts_files=sum(int(f['pts'][0]) != 0 for f in group),
                files_with_log_warning=sum(bool(f['warnings']) for f in group))


def write_csv(destination, rows):
    # Replace each complete aggregate atomically; all per-file details stay private.
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf8', newline='', dir=destination.parent,
                                     prefix='video_', suffix='.csv', delete=False) as tmp:
        pd.DataFrame(rows).to_csv(tmp, index=False, float_format='%.9g')
    os.chmod(tmp.name, 0o644)
    os.replace(tmp.name, destination)


def verify(root, run_dir):
    files = read_measurements(run_dir)
    source_files = set((root / 'Raw_Data').rglob('*.mp4'))
    if source_files != {root / f['path'] for f in files}:
        raise ValueError('Read-back manifest differs from extracted videos')
    # A second decode implementation path (ffprobe -show_frames) covers each
    # available source × camera mount, the largest clock residual, and the
    # avg/r rate exception. All packet clocks are independently re-read as well.
    sample = set()
    for dataset in DATASETS:
        for mount in (HEAD, WRISTS):
            subset = [f for f in files if f['dataset'] == dataset and f['view'] in mount]
            if subset:
                sample.add(min(subset, key=lambda f: len(f['pts']))['file_id'])
    sample.add(max(files, key=lambda f: np.abs(f['paired_ns']).max())['file_id'])
    unusual = [f for f in files if Fraction(f['avg_frame_rate']) != Fraction(f['r_frame_rate'])]
    if unusual:
        sample.add(unusual[0]['file_id'])

    packet_clocks, second_frames = [], 0
    for f in files:
        path = root / f['path']
        stream, packet_pts, packet_clock = probe(path)
        if not np.array_equal(packet_pts, f['pts']) or any(
                stream[k] != f[k] for k in ('nb_frames', 'width', 'height', 'pix_fmt', 'time_base',
                                            'codec_name', 'avg_frame_rate', 'r_frame_rate')):
            raise ValueError('Packet read-back disagrees with private decoded-frame evidence')
        packet_clocks.append({'file_id': f['file_id'], **packet_clock})
        if f['file_id'] not in sample:
            continue
        data = subprocess.run(['ffprobe', '-v', 'error', '-threads', '1', '-err_detect', 'explode',
                               '-show_frames', '-show_entries', 'frame=pts,pkt_pts,best_effort_timestamp,'
                               'width,height,pix_fmt', '-of', 'json', str(path)],
                              check=True, capture_output=True, timeout=300)
        if data.stderr.strip():
            raise ValueError('ffprobe frame decode emitted errors')
        decoded = json.loads(data.stdout)['frames']
        if len(decoded) != len(f['pts']) or any(
                int(d.get('pts', d.get('pkt_pts'))) != pts or int(d['best_effort_timestamp']) != pts or
                (d['width'], d['height'], d['pix_fmt']) != (f['width'], f['height'], f['pix_fmt'])
                for d, pts in zip(decoded, f['pts'])):
            raise ValueError('ffprobe frame decode disagrees with ffmpeg showinfo')
        second_frames += len(decoded)
    (run_dir / 'verification.json').write_text(json.dumps(dict(packet_clocks=packet_clocks,
            second_decode_files=sorted(sample), second_decode_frames=second_frames), indent=2) + '\n')
    print(f'Verified {len(files)} packet clocks. Second decode: {len(sample)} streams, {second_frames} frames.')


def summarize(run_dir, public_dir):
    files = read_measurements(run_dir)
    ledger = json.loads((run_dir / 'verification.json').read_text())
    packet_clocks = {row['file_id']: row for row in ledger['packet_clocks']}
    for f in files:
        f.update(packet_clocks[f['file_id']])
    public_dir.mkdir(parents=True, exist_ok=True)
    coverage, clocks, images, formats = [], [], [], []
    for dataset, split in sorted({(f['dataset'], f['split']) for f in files}):
        group = [f for f in files if (f['dataset'], f['split']) == (dataset, split)]
        counts = Counter(f['view'] for f in group)
        coverage.append(dict(dataset=dataset, split=split, records=len({f['record_id'] for f in group}),
                             files=len(group), **{v: counts[v] for v in HEAD + WRISTS}))
    for key, group in scope_groups(files):
        clocks.append({**key, **clock_summary(group)})
        images.append({**key, **image_summary(group)})
        formats.append({**key, **format_summary(group)})
    for name, rows in [('coverage', coverage), ('clock', clocks), ('image', images), ('format', formats)]:
        write_csv(public_dir / f'video_{name}.csv', rows)
    print(f"Published four aggregate tables for {clocks[0]['files']} streams, "
          f"{clocks[0]['decoded_frames']} frames and {clocks[0]['pts_pairs']} consecutive pairs")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--run-dir', type=Path, default=BASE / 'runs/c_full_video/full')
    parser.add_argument('--workers', type=int, default=5)
    parser.add_argument('--out', type=Path, help='Aggregate CSV directory (default: aggregates/ in the run directory)')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--summarize-only', action='store_true', help='rebuild aggregate tables from completed decode')
    mode.add_argument('--verify-only', action='store_true', help='verify completed decode and rebuild aggregates')
    args = parser.parse_args()
    if not args.verify_only and not args.summarize_only:
        previous_mask = os.umask(0o077)
        try:
            run(args.root, args.run_dir, args.workers)
        finally:
            os.umask(previous_mask)
    if not args.summarize_only:
        verify(args.root, args.run_dir)
    summarize(args.run_dir, args.out or args.run_dir/'aggregates')
