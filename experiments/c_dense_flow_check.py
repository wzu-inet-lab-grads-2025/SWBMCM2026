"""Audit consecutive head images against the official projected joint trajectories.

Run from the repo root with python -B -m experiments.c_dense_flow_check.
AI-assisted audit, OpenCode / OpenAI GPT-6.2 Astra routing group, 2026-11-21.
"""

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from fractions import Fraction
import json
import multiprocessing
import os
from pathlib import Path
import subprocess

for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[name] = '1'

import cv2
import numpy as np
import pandas as pd
import pyarrow as pa

from .c_full_hand_projection import inputs
from .c_full_video_decode import probe
from .c_quality_video_examples import marked_clock
from .c_records import ROOT, official_records, raw_metadata, rows


BASE = Path(__file__).resolve().parents[1]
SIZE = (360, 273)
NATIVE = (1920, 1456)
SIDES = ('left', 'right')
VIEWS = ('head_left', 'head_right')
FLAGS = ('normal', 'occlusion', 'near edge', 'unlisted', 'other_hand_only')
STATUS = ('no_previous', 'seed_outside', 'seed_window_edge', 'low_texture', 'forward_failed',
          'tracked_window_edge', 'backward_failed', 'cycle_failed', 'tracked')
FIRST, OUTSIDE, EDGE, FLAT, FORWARD, END_EDGE, BACKWARD, CYCLE, TRACKED = range(len(STATUS))
RADIUS = 5
GRID = np.array([(x, y) for y in np.linspace(18, SIZE[1]-19, 6)
                 for x in np.linspace(18, SIZE[0]-19, 8)], dtype=np.float32)
LK = dict(winSize=(11, 11), maxLevel=2, criteria=(cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 15, .03),
          minEigThreshold=.0001)
cv2.setNumThreads(1)
cv2.ocl.setUseOpenCL(False)
pa.set_cpu_count(1)
pa.set_io_thread_count(1)


def records(root):
    folders = {}
    for row in rows(root / 'Restocking_Tasks_and_Constraints/source_segment_index.csv'):
        folder = root / 'Raw_Data' / row['dataset'] / row['sample']
        if folders.setdefault(row['episode_uuid'], folder) != folder:
            raise ValueError('Conflicting source location')
    result = []
    for row in official_records(root):
        folder = folders[row['episode_uuid']]
        meta = raw_metadata(folder)
        if (folder.parent.name != row['dataset'] or
                any(meta[k] != row[k] for k in ('episode_uuid', 'environment_id'))):
            raise ValueError('Raw record does not match official index')
        result.append((row, folder))
    if len(result) != 69 or len(result) != len(folders):
        raise ValueError('Unexpected official record inventory')
    return result


def videos(folder):
    if folder.parent.name == 'EgoDemo':
        return [next_single((folder / 'videos' / f'observation.images.{v}').rglob('*.mp4')) for v in VIEWS]
    return [folder / 'videos' / f'{v}.mp4' for v in VIEWS]


def next_single(paths):
    path, = paths
    return path


def prefix(local, row, view):
    return local / f"{row['episode_uuid']}_{view}"


def decode_record(job):
    row, folder, local = job
    clock, _, left_video = marked_clock(folder)
    if not (np.diff(clock) > 0).all():
        raise ValueError('Nonmonotonic annotation clock')
    paths = videos(folder)
    if paths[0] != left_video:
        raise ValueError('Wrong head-left video')
    for view, path in zip(VIEWS, paths):
        stream, pts, _ = probe(path)
        if ((int(stream['width']), int(stream['height'])) != NATIVE or stream['pix_fmt'] != 'yuv420p'
                or len(pts) != len(clock) or int(stream['nb_frames']) != len(clock)):
            raise ValueError('Native format or source length mismatch')
        delta_s = (pts - pts[0]) * float(Fraction(stream['time_base'])) - (clock - clock[0]) * 1e-9
        if (np.diff(pts) <= 0).any() or np.max(abs(delta_s)) >= .5 * np.min(np.diff(clock)) * 1e-9:
            raise ValueError('Presentation and trajectory clocks disagree by half a step')
        if row['dataset'] != 'EgoDemo':
            side = pd.read_csv(path.with_name(view + '_frames.csv'), usecols=['video_timestamp_ns', '_message_index', 'frame_id'])
            if not (np.array_equal(side.video_timestamp_ns, clock) and np.array_equal(side._message_index, np.arange(len(clock)))
                    and (side.frame_id == view + '_camera').all()):
                raise ValueError('Camera sidecar disagrees with source clock')
        target = prefix(local, row, view).with_suffix('.gray')
        part = target.with_suffix('.part')
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-err_detect', 'explode',
                        '-threads', '1', '-noautorotate', '-i', str(path), '-map', '0:v:0', '-filter_threads', '1',
                        '-vf', f'extractplanes=y,scale={SIZE[0]}:{SIZE[1]}:flags=area', '-vsync', '0',
                        '-threads', '1', '-pix_fmt', 'gray', '-f', 'rawvideo', '-y', str(part)], check=True)
        if part.stat().st_size != len(pts) * SIZE[0] * SIZE[1]:
            raise ValueError('Partial decoded stream')
        part.replace(target)
    return 2 * len(clock)


def scaled_xy(pixels, native=NATIVE):
    return ((pixels + .5) * (np.array(SIZE) / native) - .5).astype(np.float32)


def full_window(points):
    return (np.isfinite(points).all(axis=1) & (points >= RADIUS).all(axis=1)
            & (points <= np.array(SIZE) - 1 - RADIUS).all(axis=1))


def track(prev, curr, points, in_view):
    """Uses only the previous seed positions. The current projection is unavailable here."""
    status = np.full(len(points), OUTSIDE, dtype=np.uint8)
    disp = np.full(points.shape, np.nan, dtype=np.float32)
    status[in_view] = EDGE
    ids = np.flatnonzero(in_view & full_window(points))
    if not len(ids):
        return status, disp
    start = points[ids]
    # Always solve on all supported seeds, including the fixed image grid. Weak
    # texture is classified separately with the previous-frame structure tensor.
    end, found, _ = cv2.calcOpticalFlowPyrLK(prev, curr, start[:, None], None, **LK)
    end = end.reshape(-1, 2)
    tensor = cv2.cornerMinEigenVal(prev, blockSize=11, ksize=3)
    x, y = np.rint(start).astype(int).T
    textured = tensor[y, x] >= .00012
    status[ids] = FLAT
    status[ids[textured]] = FORWARD
    forward = textured & found.ravel().astype(bool) & np.isfinite(end).all(axis=1)
    status[ids[forward]] = END_EDGE
    usable = forward & full_window(end)
    ids = ids[usable]
    if len(ids):
        start, end = start[usable], end[usable]
        back, ok, _ = cv2.calcOpticalFlowPyrLK(curr, prev, end[:, None], None, **LK)
        back = back.reshape(-1, 2)
        status[ids] = BACKWARD
        valid = ok.ravel().astype(bool) & np.isfinite(back).all(axis=1)
        status[ids[valid]] = CYCLE
        passed = valid & (np.linalg.norm(back - start, axis=1) <= 1.)
        status[ids[passed]] = TRACKED
        disp[ids[passed]] = end[passed] - start[passed]
    return status, disp


def grid_off_hand(points, inside):
    outside = np.ones(len(GRID), bool)
    for xy, valid in zip(points, inside):
        if valid.any():
            visible = xy[valid]
            outside &= ~((GRID >= visible.min(axis=0) - 12) & (GRID <= visible.max(axis=0) + 12)).all(axis=1)
    return outside


def grid_median(status, flow, off_hand):
    keep = (status == TRACKED) & off_hand
    quadrants = (GRID[:, 0] >= SIZE[0]/2).astype(int) + 2 * (GRID[:, 1] >= SIZE[1]/2)
    if keep.sum() < 6 or len(np.unique(quadrants[keep])) < 3:
        return np.full(2, np.nan, dtype=np.float32)
    return np.median(flow[keep], axis=0)


def state_codes(labels, demo):
    codes = []
    for reasons in labels:
        pair = []
        for side in SIDES:
            own = [r.split(':', 1)[1] for r in reasons if r.startswith(side + ':')]
            if len(own) > 1:
                raise ValueError('Multiple official reasons on the same hand and frame')
            state = own[0] if own else ('other_hand_only' if reasons else ('unlisted' if demo else 'normal'))
            pair.append(FLAGS.index(state))
        codes.append(pair)
    return np.asarray(codes, dtype=np.uint8)


def measure_record(job):
    row, folder, local = job
    clock, labels, _ = marked_clock(folder)
    n = len(clock)
    demo = row['dataset'] == 'EgoDemo'
    if demo and labels[0]:
        raise ValueError('One-based sensitivity would move a label outside this file')
    quality = state_codes(labels, demo)
    alternate = state_codes(labels[1:] + [set()], True) if demo else quality
    projections = {v: {} for v in VIEWS}
    for v, side, pixels, inside, w, h in inputs(folder, clock):
        if (w, h) != NATIVE:
            raise ValueError('Official intrinsics and decoded native size disagree')
        projections[v][side] = scaled_xy(pixels), inside
    for view in VIEWS:
        stem = prefix(local, row, view)
        xy, inside = (np.stack(a, axis=1) for a in zip(*(projections[view][s] for s in SIDES)))
        if stem.with_suffix('.gray').stat().st_size != n * SIZE[0] * SIZE[1]:
            raise ValueError('Cannot match gray stream to trajectory')
        gray = np.memmap(stem.with_suffix('.gray'), np.uint8, 'r', shape=(n, SIZE[1], SIZE[0]))
        count = 42 + len(GRID)
        status = np.zeros((n, count), dtype=np.uint8)
        motion = np.full((n, count, 2), np.nan, dtype=np.float32)
        background = np.full((n, 2), np.nan, dtype=np.float32)
        off_hand = np.zeros((n, len(GRID)), bool)
        for t in range(1, n):
            points = np.concatenate((xy[t-1].reshape(42, 2), GRID))
            eligible = np.r_[inside[t-1].ravel(), np.ones(len(GRID), bool)]
            status[t], motion[t] = track(gray[t-1], gray[t], points, eligible)
            off_hand[t] = grid_off_hand(xy[t-1], inside[t-1])
            background[t] = grid_median(status[t, 42:], motion[t, 42:], off_hand[t])
        # Keep all per-frame evidence and alternate indexing private. Atomic unit is one view.
        with stem.with_suffix('.npz.part').open('wb') as handle:
            np.savez_compressed(handle, clock=clock, quality=quality, alternate=alternate,
                                xy=xy, inside=inside, status=status[:, :42].reshape(n, 2, 21),
                                motion=motion[:, :42].reshape(n, 2, 21, 2), grid_status=status[:, 42:],
                                grid_motion=motion[:, 42:], off_hand=off_hand, background=background)
        stem.with_suffix('.npz.part').replace(stem.with_suffix('.npz'))
    return 2 * n


def synthetic():
    # A native image patch whose center and extent are exactly measurable after the production scale filter.
    marker = np.zeros(NATIVE[::-1], np.uint8)
    marker[320:336, 480:496] = 255
    command = ['ffmpeg', '-v', 'error', '-threads', '1', '-f', 'rawvideo', '-pix_fmt', 'gray',
               '-s', '1920x1456', '-i', 'pipe:0', '-filter_threads', '1', '-vf', 'scale=360:273:flags=area',
               '-threads', '1', '-pix_fmt', 'gray', '-f', 'rawvideo', 'pipe:1']
    marker_small = np.frombuffer(subprocess.run(command, input=marker.tobytes(), capture_output=True,
                                                check=True).stdout, np.uint8).reshape(SIZE[::-1])
    ys, xs = np.nonzero(marker_small)
    observed = np.average(np.column_stack((xs, ys)), axis=0, weights=marker_small[ys, xs])
    center_error = np.linalg.norm(observed - scaled_xy(np.array([487.5, 327.5])))
    rng = np.random.default_rng(20261121)
    prev = rng.integers(24, 230, SIZE[::-1], dtype=np.uint8)
    delta = np.array([3., -2.], dtype=np.float32)
    curr = np.roll(prev, (-2, 3), axis=(0, 1))
    # An independently moving texture travels left and down, against the background.
    curr[85:165, 116:196] = prev[80:160, 120:200]
    points = np.array([[147., 107.], [169., 133.], [80., 70.], [2., 50.], [-2., 50.]], np.float32)
    seeds = np.vstack((points, GRID))
    status, flow = track(prev, curr, seeds, np.arange(len(seeds)) != 4)
    known = np.array([[-4, 5], [-4, 5], delta])
    error = np.linalg.norm(flow[:3] - known, axis=1)
    off = ~((GRID >= [108, 68]) & (GRID <= [212, 172])).all(axis=1)
    bg = grid_median(status[len(points):], flow[len(points):], off)
    flat = np.full_like(prev, 37)
    flat_status, _ = track(flat, flat, points[:1], np.ones(1, bool))
    if (center_error > .015 or not np.array_equal(status[:5], [TRACKED, TRACKED, TRACKED, EDGE, OUTSIDE])
            or not (error < .15).all() or not np.linalg.norm(bg - delta) < .15 or flat_status[0] != FLAT):
        raise AssertionError((center_error, status[:5], error, bg, flat_status))
    return dict(marker_center_error_px=float(center_error), max_translation_error_px=float(error.max()),
                background_translation_error_px=float(np.linalg.norm(bg - delta)), flat_and_edge_checks='passed')


def parallel(fn, records_, local, workers):
    with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context('spawn')) as pool:
        # Start large clips first to keep the four bounded workers productive.
        jobs = sorted(records_, key=lambda item: -sum(p.stat().st_size for p in videos(item[1])))
        pending = [pool.submit(fn, (*job, local)) for job in jobs]
        total = 0
        for i, future in enumerate(as_completed(pending), 1):
            total += future.result()
            if i % 10 == 0 or i == len(jobs):
                print(f'{fn.__name__}: {i}/{len(jobs)} records, {total} view frames', flush=True)
    if total != 132140:
        raise ValueError('Unexpected decoded or measured population')


def lengths(vectors):
    return np.linalg.norm(vectors, axis=-1)


def read_view(path, rec_id, env_id, row, view):
    with np.load(path, allow_pickle=False) as data:
        xy, inside, code, flow = (data[k] for k in ('xy', 'inside', 'status', 'motion'))
        bg, grid = data['background'], data['grid_status']
        n = len(data['clock'])
        shape = (n, 2, 21)
        if not (code.shape == shape and np.isfinite(xy).all() and (code[0] == FIRST).all()
                and (grid[0] == FIRST).all() and (code[1:] > FIRST).all() and (grid[1:] >= FLAT).all()
                and (grid <= TRACKED).all() and (code <= TRACKED).all()
                and np.array_equal(code[1:] == OUTSIDE, ~inside[:-1])
                and np.array_equal(code == TRACKED, np.isfinite(flow).all(axis=-1))
                and np.array_equal(grid == TRACKED, np.isfinite(data['grid_motion']).all(axis=-1))
                and not np.isfinite(bg[0]).any()):
            raise ValueError('Incomplete or inconsistent per-frame evidence')
        delta = np.full_like(xy, np.nan)
        delta[1:] = xy[1:] - xy[:-1]
        delta[~inside] = np.nan
        delta[1:] = np.where(inside[:-1, :, :, None], delta[1:], np.nan)
        comparable = (code == TRACKED) & inside
        error = lengths(flow - delta)
        if not np.array_equal(np.isfinite(error), comparable):
            raise ValueError('Inconsistent endpoint-error denominator')
        norm = lengths(delta)
        norm[~comparable] = np.nan
        bg_error = lengths(bg[:, None, None, :] - delta)
        bg_error[~comparable] = np.nan
        shifted = np.full_like(error, np.nan)
        shifted[:-10] = lengths(flow[:-10] - delta[10:])
        shifted[~comparable] = np.nan
        direction = comparable & (norm >= 1) & (lengths(flow) >= 1)
        cosine = np.full_like(error, np.nan)
        cosine[direction] = np.sum(flow[direction] * delta[direction], axis=-1) / (norm[direction] * lengths(flow[direction]))
        quality, alt = data['quality'], data['alternate']

    def broadcast(arr):
        return np.broadcast_to(arr, shape).ravel()

    table = pd.DataFrame(dict(record=np.int16(rec_id), environment=np.int16(env_id),
        dataset=pd.Categorical.from_codes(np.zeros(np.prod(shape), dtype=np.int8), [row['dataset']]),
        view=np.uint8(VIEWS.index(view)), hand=broadcast(np.arange(2, dtype=np.uint8)[None, :, None]),
        joint=broadcast(np.arange(21, dtype=np.uint8)), quality=broadcast(quality[:, :, None]),
        alternate=broadcast(alt[:, :, None]), inside=inside.ravel(), status=code.ravel(),
        epe=error.ravel(), official=norm.ravel(), bg=bg_error.ravel(), shifted=shifted.ravel(), cosine=cosine.ravel()))
    coverage = dict(record=rec_id, dataset=row['dataset'], view=view, streams=1, frames=n, first_no_previous=1,
        pairs=n-1, pairs_with_grid_attempt=int((grid[1:] >= FLAT).any(axis=1).sum()),
        pairs_with_grid_track=int((grid[1:] == TRACKED).any(axis=1).sum()),
        pairs_with_background=int(np.isfinite(bg[:, 0]).sum()), pairs_with_hand_comparison=int(comparable.any(axis=(1, 2)).sum()),
        pairs_with_both_hands=int(comparable.any(axis=2).all(axis=1).sum()),
        hand_pairs_any_comparable=int(comparable.any(axis=2).sum()))
    return table, coverage


def summary(group):
    status = group.status.to_numpy()
    count = np.bincount(status, minlength=len(STATUS))
    ok = group.epe.notna()
    epe = group.epe[ok]
    direction = group.cosine.dropna().clip(-1, 1)
    medians = group.loc[ok].groupby('record').epe.median()
    return dict(records=group.record.nunique(), comparable_records=len(medians), comparable_envs=group.environment[ok].nunique(),
                adjacent_joint_slots=len(group)-count[FIRST], first_no_previous=count[FIRST],
                **{name: count[i] for i, name in enumerate(STATUS[1:-1], 1)},
                curr_outside_any=int(((status != FIRST) & ~group.inside).sum()), tracked=count[TRACKED],
                tracked_curr_outside=count[TRACKED]-len(epe), comparable=len(epe),
                epe_p50_px=epe.median(), epe_p95_px=epe.quantile(.95), epe_le1=int((epe <= 1).sum()),
                epe_le2=int((epe <= 2).sum()), official_step_p50_px=group.official[ok].median(),
                both_steps_ge1=len(direction), direction_le45deg=int((direction >= np.sqrt(.5)).sum()),
                record_p50_q25_px=medians.quantile(.25), record_p50_q75_px=medians.quantile(.75))


def control_summary(group, group_name, name, motion_class):
    # Paired support for the observed alignment and the particular comparator.
    paired = group[group[name].notna()]
    if motion_class == 'official_ge1px':
        paired = paired[paired.official >= 1]
    aligned, control = paired.epe, paired[name]
    gain = control.astype(np.float64) - aligned
    environment = gain.groupby(paired.environment).agg(['sum', 'size'])
    # Descriptive sensitivity to the observed environment composition, resampling
    # complete clusters and retaining the paired joint-slot weighting within each.
    low = high = np.nan
    if len(environment) >= 2:
        rng = np.random.default_rng(20261121)
        ids = rng.integers(0, len(environment), (2000, len(environment)))
        estimates = environment['sum'].to_numpy()[ids].sum(axis=1) / environment['size'].to_numpy()[ids].sum(axis=1)
        low, high = np.quantile(estimates, [.025, .975])
    label = {'official': 'zero_motion', 'bg': 'off_hand_grid', 'shifted': 'clock_plus10'}[name]
    return dict(quality=group_name, comparator=label, subset=motion_class, paired=len(paired), records=paired.record.nunique(),
                environments=len(environment), local_p50_px=aligned.median(), control_p50_px=control.median(),
                local_p95_px=aligned.quantile(.95), control_p95_px=control.quantile(.95), local_strictly_closer=int((gain > 0).sum()),
                ties=int((gain == 0).sum()), mean_control_minus_local_px=gain.mean(), env_boot_p025_px=low, env_boot_p975_px=high)


def dimension(value, field):
    if field in ('quality', 'alternate'):
        return FLAGS[value]
    if field == 'hand':
        return SIDES[value]
    if field == 'view':
        return VIEWS[value]
    return value


def save(out, stem, result):
    out.mkdir(mode=0o755, parents=True, exist_ok=True)
    path = out / f'flow_{stem}.csv'
    part = path.with_suffix('.csv.part')
    pd.DataFrame(result).to_csv(part, index=False, float_format='%.8g')
    part.chmod(0o644)
    part.replace(path)


def hand_support(data):
    # Input stays in stream/frame/hand/joint order from read_view.
    hands = data.iloc[::21][['dataset', 'record', 'quality', 'status']].copy()
    error = data.epe.to_numpy().reshape(-1, 21)
    hands['joints'] = np.isfinite(error).sum(axis=1)
    usable = hands.joints > 0
    hands['median'] = np.nan
    hands.loc[usable, 'median'] = np.nanmedian(error[usable], axis=1)
    result = []
    for q, group in [('all', hands), *[(FLAGS[i], g) for i, g in hands.groupby('quality')]]:
        pairs = group[group.status != FIRST]
        result.append(dict(quality=q, records=group.record.nunique(), hand_view_frames=len(group),
                           first_no_previous=len(group)-len(pairs), hand_view_pairs=len(pairs),
                           no_comparable=int((pairs.joints == 0).sum()), any_comparable=int((pairs.joints > 0).sum()),
                           all21_comparable=int((pairs.joints == 21).sum()), comparable_joints=int(pairs.joints.sum()),
                           median_of_hand_medians_px=pairs['median'].median()))
    return result


def block_check(items, local, out):
    """Fixed sampled pairs, integer normalized-correlation estimator independent of LK."""
    all_rows = []
    radius, search = 5, 6
    margin = radius + search
    for row, _ in items:
        for view in VIEWS:
            stem = prefix(local, row, view)
            with np.load(stem.with_suffix('.npz'), allow_pickle=False) as data:
                xy, inside, primary = data['xy'], data['inside'], data['motion']
            n = len(xy)
            gray = np.memmap(stem.with_suffix('.gray'), np.uint8, 'r', shape=(n, SIZE[1], SIZE[0]))
            for t in np.rint((n-1) * np.array([.25, .5, .75])).astype(int):
                prev, curr = gray[t-1], gray[t]
                for hand, joint in np.ndindex(2, 21):
                    p = xy[t-1, hand, joint]
                    x, y = np.rint(p).astype(int)
                    r = dict(dataset=row['dataset'], eligible=False, comparable=False, boundary=False,
                             block_error=np.nan, lk_error=np.nan, pixel_disagreement=np.nan)
                    all_rows.append(r)
                    if not (inside[t-1, hand, joint] and margin <= x < SIZE[0]-margin and margin <= y < SIZE[1]-margin):
                        continue
                    patch = prev[y-radius:y+radius+1, x-radius:x+radius+1]
                    if patch.std() < 3:
                        continue
                    window = curr[y-margin:y+margin+1, x-margin:x+margin+1]
                    scores = cv2.matchTemplate(window, patch, cv2.TM_CCOEFF_NORMED)
                    _, _, _, peak = cv2.minMaxLoc(scores)
                    vector = np.array(peak) - search
                    r.update(eligible=True, boundary=bool((abs(vector) == search).any()))
                    lk = primary[t, hand, joint]
                    if inside[t, hand, joint] and np.isfinite(lk).all():
                        d = xy[t, hand, joint] - p
                        r.update(comparable=True, block_error=lengths(vector-d), lk_error=lengths(lk-d),
                                 pixel_disagreement=lengths(vector-lk))
    data = pd.DataFrame(all_rows)
    if len(data) != 138*3*42:
        raise ValueError('Unexpected sampled block-check denominator')
    results = []
    for key, g in [('all', data), *data.groupby('dataset')]:
        results.append(dict(dataset=key, pairs=len(g)//42, slots=len(g), block_eligible=g.eligible.sum(),
                            block_peak_on_search_boundary=g.boundary.sum(), common=g.comparable.sum(),
                            block_epe_p50_px=g.block_error.median(), lk_epe_p50_px=g.lk_error.median(),
                            block_lk_diff_p50_px=g.pixel_disagreement.median(), block_lk_diff_p95_px=g.pixel_disagreement.quantile(.95)))
    save(out, 'block_check', results)
    print(f'Second estimator: {len(data)//42} sampled pairs, {data.comparable.sum()} common joint comparisons', flush=True)


def summarize(items, local, out):
    tables, cover = [], []
    envs = sorted({r['environment_id'] for r, _ in items})
    paths = {prefix(local, row, v).with_suffix('.npz') for row, _ in items for v in VIEWS}
    if set(local.glob('*.npz')) != paths:
        raise ValueError('Private evidence inventory differs from 138 official streams')
    for i, (row, _) in enumerate(items):
        for view in VIEWS:
            t, c = read_view(prefix(local, row, view).with_suffix('.npz'), i, envs.index(row['environment_id']), row, view)
            tables.append(t)
            cover.append(c)
    data = pd.concat(tables, ignore_index=True)
    del tables
    data['dataset'] = data.dataset.astype('category')
    coverage = pd.DataFrame(cover)
    if (len(data) != 132140 * 42 or coverage.pairs.sum() != 132002 or coverage.pairs_with_grid_attempt.sum() != 132002
            or coverage.first_no_previous.sum() != 138):
        raise ValueError('First-frame or adjacent-pair coverage mismatch')
    coverage_rows = []
    for field in ('all', 'dataset', 'view'):
        groups = [('all', coverage)] if field == 'all' else coverage.groupby(field)
        for key, group in groups:
            coverage_rows.append(dict(scope=field, value=key, records=group.record.nunique(),
                **group.drop(columns=['record', 'dataset', 'view']).sum().to_dict()))
    save(out, 'coverage', coverage_rows)

    # Independent native-field-of-view totals from the existing full trajectory audit.
    ref = pd.read_csv(BASE / 'research/c_full_audit/hand_projection.csv')
    observed = data.groupby(['dataset', 'view', 'hand', 'quality'], observed=True).inside.agg(['size', 'sum'])
    if len(ref) != len(observed):
        raise ValueError('Independent projection groups disagree')
    for r in ref.itertuples(index=False):
        totals = observed.loc[(r.dataset, VIEWS.index(r.view), SIDES.index(r.hand), FLAGS.index(r.quality))]
        if tuple(totals) != (r.hand_ticks * 21, r.joints_inside):
            raise ValueError('Independent native field-of-view counts disagree')

    save(out, 'hand_support', hand_support(data))
    rows_ = []
    for fields in [(), ('quality',), ('dataset',), ('view',), ('hand',), ('dataset', 'quality')]:
        groups = data.groupby(list(fields), observed=True) if fields else [((), data)]
        for keys, g in groups:
            if not isinstance(keys, tuple):
                keys = (keys,)
            rows_.append(dict(scope='+'.join(fields) or 'all', **{f: dimension(k, f) for f, k in zip(fields, keys)}, **summary(g)))
    save(out, 'agreement', rows_)
    joint_rows = [dict(hand=SIDES[s], joint=int(j), quality=FLAGS[q], **summary(g)) for (s, j, q), g in
                  data.groupby(['hand', 'joint', 'quality'], observed=True)]
    save(out, 'joint_quality', joint_rows)

    demo = data[data.dataset == 'EgoDemo']
    index_rows = []
    for field in ('quality', 'alternate'):
        for q, g in demo.groupby(field):
            index_rows.append(dict(demo_index='zero_based' if field == 'quality' else 'one_based', quality=FLAGS[q],
                                   reassigned_slots=int((g.quality != g.alternate).sum()), **summary(g)))
    save(out, 'demo_index', index_rows)

    controls = []
    for q, group in [('all', data), *[(FLAGS[q], g) for q, g in data.groupby('quality')]]:
        for name in ('official', 'bg', 'shifted'):
            for subset in ('all', 'official_ge1px'):
                controls.append(control_summary(group, q, name, subset))
    save(out, 'controls', controls)
    print(f'{len(paths)} streams, {coverage.frames.sum()} frames, {coverage.pairs.sum()} pairs, '
          f'{data.epe.notna().sum()} comparable joint observations', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--local', type=Path, default=BASE / 'runs/c_dense_motion')
    parser.add_argument('--out', type=Path, help='Aggregate CSV directory, defaults to aggregates/ under --local')
    parser.add_argument('--stage', choices=('decode', 'measure', 'summarize', 'synthetic', 'block-check', 'all'), default='all')
    parser.add_argument('--workers', type=int, choices=range(1, 5), default=4)
    args = parser.parse_args()
    args.out = args.out or args.local / 'aggregates'
    prior_umask = os.umask(0o077)
    args.local.mkdir(mode=0o700, parents=True, exist_ok=True)
    if args.stage in ('synthetic', 'measure', 'all'):
        result = synthetic()
        print(result, flush=True)
        (args.local / 'synthetic.json').write_text(json.dumps(result, indent=2) + '\n')
        if args.stage == 'synthetic':
            return
    items = records(args.root)
    if args.stage in ('decode', 'all'):
        parallel(decode_record, items, args.local, args.workers)
    if args.stage in ('measure', 'all'):
        parallel(measure_record, items, args.local, args.workers)
    if args.stage in ('summarize', 'all'):
        os.umask(prior_umask)
        summarize(items, args.local, args.out)
    if args.stage in ('block-check', 'all'):
        os.umask(prior_umask)
        block_check(items, args.local, args.out)


if __name__ == '__main__':
    main()
