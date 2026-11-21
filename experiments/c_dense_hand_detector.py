"""Independent per-image MediaPipe Hands and a joined audit of C head views.

Run as `python -B -m experiments.c_dense_hand_detector --help` from repo root.
AI-assisted preprocessing and analysis, OpenCode, GPT-6.2 Astra, 2026-11-21.
"""

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
from fractions import Fraction
from hashlib import sha256
from itertools import groupby, islice, product
import json
import multiprocessing as mp
import os
from pathlib import Path
import time
from zipfile import BadZipFile

for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[name] = '1'
os.environ['CUDA_VISIBLE_DEVICES'] = ''
os.environ['MEDIAPIPE_DISABLE_GPU'] = '1'
# The binary distribution probes EGL even when both TFLite delegates use CPU.
# Suppress vendor discovery so that no NVIDIA graphics context is allocated.
os.environ['__EGL_VENDOR_LIBRARY_FILENAMES'] = '/dev/null'
os.environ['LIBGL_ALWAYS_SOFTWARE'] = '1'

import numpy as np

BASE = Path(__file__).resolve().parents[1]
ROOT = BASE / 'data/raw/C'
RUN = BASE / 'runs/c_dense_detector'
OUT = RUN / 'aggregates'
SIDES = ('left', 'right')
PALM = [0, 5, 9, 13, 17]
POINTS = [0, *range(4, 21)]  # common anatomical indices, including thumb tip
MAX_HANDS = 4
GATE = .05                     # fractions of the native image diagonal
AMBIGUITY = .012
WEIGHTS = {
    'modules/palm_detection/palm_detection_lite.tflite': 'e9a4aaddf90dda56a87235303cf00e4c2d3fb28725f68fd88772997dac905c18',
    'modules/hand_landmark/hand_landmark_lite.tflite': '048edd3645c9bf7397d19a9f6e3a42957d6e414c9bea6598030a2e9b624156e6',
    'modules/hand_landmark/hand_landmark_tracking_cpu.binarypb': '942ad4e6b3ecd1903fd2743ad2255a341854de975ef9c90418a4c7b64f0b4425',
}
CONFIG = dict(schema=1, mediapipe='0.10.21', av='13.1.0', complexity=0, use_prev=False,
              max_hands=MAX_HANDS, palm_threshold=.5, presence_threshold=.5,
              resize='half_size_bilinear', executor_threads=1, inference_threads=1, weights=WEIGHTS)


def require(ok, message):
    if not ok:
        raise ValueError(message)


def read_rows(path):
    with path.open(newline='', encoding='utf-8-sig') as handle:
        return list(csv.DictReader(handle))


def inventory(root):
    """Only identifiers/split and paths are read before visual inference."""
    source = root / 'Restocking_Tasks_and_Constraints'
    index = {r['episode_uuid']: r for r in read_rows(source / 'record_split.csv')}
    folders = {}
    for r in read_rows(source / 'source_segment_index.csv'):
        key = (r['dataset'], r['sample'])
        require(folders.setdefault(r['episode_uuid'], key) == key, 'Conflicting source folder')
    require(set(folders) == set(index) and len(index) == 69, 'Record inventory changed')
    jobs = []
    for uuid, (ds, sample) in sorted(folders.items(), key=lambda item: item[1]):
        folder = root / 'Raw_Data' / ds / sample
        require(index[uuid]['dataset'] == ds, 'Dataset mismatch')
        views = {}
        for path in sorted((folder / 'videos').rglob('*.mp4')):
            view = path.stem if ds != 'EgoDemo' else path.parents[1].name.removeprefix('observation.images.')
            require(view not in views, 'Duplicate view')
            views[view] = path
            jobs.append(dict(path=path, folder=folder, view=view, dataset=ds, split=index[uuid]['split'],
                             key=f'{ds}_{sample}_{view}', uuid=uuid, environment=index[uuid]['environment_id'],
                             relative=str(path.relative_to(root))))
        require(set(views) in ({'head_left', 'head_right'}, {'head_left', 'head_right', 'left_wrist', 'right_wrist'}),
                'Unexpected views')
    require(len(jobs) == 184 and {j['path'] for j in jobs} == set((root / 'Raw_Data').rglob('*.mp4')),
            'Video inventory changed')
    return jobs


def model():
    import av
    import cv2
    import mediapipe
    from mediapipe.calculators.tensor.inference_calculator_pb2 import InferenceCalculatorOptions
    from mediapipe.framework.calculator_pb2 import CalculatorGraphConfig
    from mediapipe.framework.thread_pool_executor_pb2 import ThreadPoolExecutorOptions
    from mediapipe.python._framework_bindings import resource_util, validated_graph_config
    from mediapipe.python.solution_base import SolutionBase

    cv2.setNumThreads(1)
    require((mediapipe.__version__, av.__version__) == (CONFIG['mediapipe'], CONFIG['av']), 'Version mismatch')
    package = Path(mediapipe.__file__).parent
    for path, expected in WEIGHTS.items():
        require(sha256((package / path).read_bytes()).hexdigest() == expected, f'Weight/graph mismatch {path}')
    resource_util.set_resource_dir(str(package.parent))
    validated = validated_graph_config.ValidatedGraphConfig()
    validated.initialize(binary_graph_path=str(package / 'modules/hand_landmark/hand_landmark_tracking_cpu.binarypb'))
    graph = CalculatorGraphConfig.FromString(validated.binary_config)
    # Explicit thread budget for the official CPU graph, including XNNPACK.
    if not graph.executor:
        graph.executor.add(name='')
    for executor in graph.executor:
        executor.type = 'ThreadPoolExecutor'
        executor.options.Extensions[ThreadPoolExecutorOptions.ext].num_threads = CONFIG['executor_threads']
    inference = [node for node in graph.node if node.calculator.startswith('InferenceCalculator')]
    require(len(inference) == 2, 'Expected one palm and one landmark model')
    for node in inference:
        node.options.Extensions[InferenceCalculatorOptions.ext].delegate.xnnpack.num_threads = CONFIG['inference_threads']
    return SolutionBase(graph_config=graph, side_inputs={'model_complexity': CONFIG['complexity'], 'num_hands': MAX_HANDS,
                        'use_prev_landmarks': CONFIG['use_prev']}, calculator_params={
                        'palmdetectioncpu__TensorsToDetectionsCalculator.min_score_thresh': CONFIG['palm_threshold'],
                        'handlandmarkcpu__ThresholdingCalculator.threshold': CONFIG['presence_threshold']},
                        outputs=['multi_hand_landmarks', 'multi_handedness'])


def start_worker(queue):
    global DETECTOR
    cpu = queue.get()
    os.sched_setaffinity(0, {cpu})  # all subsequently created library threads inherit the mask
    DETECTOR = model()


def atomic_npz(path, **arrays):
    tmp = path.with_suffix(f'.{os.getpid()}.part')
    with tmp.open('wb') as f:
        os.fchmod(f.fileno(), 0o600)
        np.savez_compressed(f, **arrays)
    tmp.replace(path)


def predict(job, work, limit=0):
    import av

    dest = work / (job['key'] + '.npz')
    path = job['path']
    fingerprint = sha256(path.read_bytes()).hexdigest()
    config = json.dumps(CONFIG, sort_keys=True)
    if not limit and dest.exists():
        try:
            with np.load(dest, allow_pickle=False) as z:
                require(str(z['sha256']) == fingerprint and str(z['config']) == config and
                        str(z['relative']) == job['relative'], 'Source or model differs from cache')
                pts = z['pts']
                require(np.all(np.diff(pts) > 0) and len(pts) and Fraction(str(z['time_base'])) > 0 and
                        (z['size'] > 0).all(), 'Invalid cached header')
                validate_outputs(z, len(pts))
                return len(pts)
        except (ValueError, KeyError, OSError, EOFError, BadZipFile) as exc:
            print(f'Rebuilding {job["key"]}: {exc}', flush=True)
    with av.open(str(path)) as video:
        stream, = video.streams.video
        stream.codec_context.thread_count = 1
        stream.codec_context.options = {'err_detect': 'explode'}
        w, h = stream.width, stream.height
        require(w % 2 == h % 2 == 0, 'Exact half-size resize unavailable')
        n = min(limit, stream.frames) if limit else stream.frames
        pts = np.empty(n, np.int64)
        count = np.zeros(n, np.uint8)
        xyz = np.full((n, MAX_HANDS, 21, 3), np.nan, np.float32)
        handedness = np.full((n, MAX_HANDS), -1, np.int8)
        score = np.full((n, MAX_HANDS), np.nan, np.float32)
        frames = video.decode(stream)
        seen = 0
        for i, frame in enumerate(islice(frames, limit) if limit else frames):
            require(i < n and frame.pts is not None and frame.time_base == stream.time_base and
                    (frame.width, frame.height) == (w, h), 'Unexpected decode clock/shape')
            image = frame.reformat(width=w//2, height=h//2, format='rgb24', interpolation='BILINEAR').to_ndarray()
            image.flags.writeable = False
            out = DETECTOR.process(image)
            points = out.multi_hand_landmarks or []
            classes = out.multi_handedness or []
            require(len(points) == len(classes) <= MAX_HANDS, 'Inconsistent model outputs')
            pts[i], count[i] = frame.pts, len(points)
            for k, (hand, label) in enumerate(zip(points, classes)):
                xyz[i, k] = [(p.x, p.y, p.z) for p in hand.landmark]
                cat, = label.classification
                handedness[i, k] = ('Left', 'Right').index(cat.label)
                score[i, k] = cat.score
            seen += 1
        require(seen == n > 0 and (np.diff(pts) > 0).all(), 'Truncated or unordered decode')
        if not limit:
            atomic_npz(dest, relative=job['relative'], sha256=fingerprint, config=config, pts=pts,
                       time_base=str(stream.time_base), size=[w, h], count=count, xyz=xyz,
                       handedness=handedness, handedness_score=score)
    return seen


def detect(jobs, workers, benchmark):
    require(2 <= workers <= 8, 'Use 2–8 workers')
    work = RUN / 'predictions'
    work.mkdir(exist_ok=True, parents=True, mode=0o700)
    if benchmark:
        # The training slice covers all mounts. Benchmarks retain only runtime and count.
        train = [j for j in jobs if j['split'] == 'train']
        jobs = [j for view in ('head_left', 'head_right', 'left_wrist', 'right_wrist')
                for j in [v for v in train if v['view'] == view][:2]]
    jobs = sorted(jobs, key=lambda j: -j['path'].stat().st_size)
    ctx = mp.get_context('spawn')
    queue = ctx.Queue()
    cpus = sorted(os.sched_getaffinity(0))
    require(len(cpus) >= workers, 'Insufficient available CPUs')
    for cpu in cpus[:workers]:
        queue.put(cpu)
    start = time.monotonic()
    total = 0
    with ProcessPoolExecutor(workers, mp_context=ctx, initializer=start_worker, initargs=(queue,)) as pool:
        futures = {pool.submit(predict, j, work, benchmark): j for j in jobs}
        for i, done in enumerate(as_completed(futures), 1):
            total += done.result()
            if i % 10 == 0 or i == len(jobs):
                print(f'{i}/{len(jobs)} files, {total} frames, {time.monotonic()-start:.1f}s', flush=True)
    elapsed = time.monotonic() - start
    if not benchmark:
        require(total == 178512, 'Unexpected total frames')
    tag = f'benchmark_{workers}_{benchmark}' if benchmark else 'detection_complete'
    (RUN / f'{tag}.json').write_text(json.dumps(dict(files=len(jobs), frames=total, workers=workers,
                    elapsed_s=elapsed, fps=total/elapsed, cpus=cpus[:workers], config=CONFIG), indent=2))


def assignments(cost):
    """Max-cardinality one-to-one assignments, then bounded min-sum cost.

    A side is ambiguous if *its* slot changes among equally large assignments
    within AMBIGUITY of the best sum. This leaves independent sides usable.
    """
    n, sides, k = cost.shape
    require(sides == 2, 'Expected two reference sets')
    plans = np.asarray([(a, b) for a, b in product(range(-1, k), repeat=2) if a == -1 or a != b])
    distances = np.zeros((n, len(plans)))
    sizes = np.zeros((n, len(plans)), dtype=int)
    for i, plan in enumerate(plans):
        allowed = np.ones(n, bool)
        for side, slot in enumerate(plan):
            if slot >= 0:
                d = cost[:, side, slot]
                allowed &= d <= GATE
                distances[:, i] += d
        sizes[:, i] = np.where(allowed, np.count_nonzero(plan >= 0), -1)
    distances[sizes < sizes.max(axis=1, keepdims=True)] = np.inf
    best = distances.argmin(axis=1)
    plausible = distances <= distances[np.arange(n), best, None] + AMBIGUITY
    slots = plans[best].copy()
    for side in range(2):
        ambiguous = (plausible & (plans[:, side] != slots[:, side, None])).any(axis=1)
        slots[ambiguous, side] = -2
    return slots


def match_geometry(xyz, reference, inside, size):
    w, h = size
    predicted = xyz[..., :2].astype(np.float64) * [w, h]
    pred_in = ((predicted >= 0) & (predicted < size)).all(axis=-1)
    ref_palm = np.take(reference, PALM, axis=2)
    pred_palm = np.take(predicted, PALM, axis=2)
    common = np.take(inside, PALM, axis=2)[:, :, None, :] & np.take(pred_in, PALM, axis=2)[:, None, :, :]
    diffs = np.linalg.norm(ref_palm[:, :, None] - pred_palm[:, None, :], axis=-1) / np.hypot(w, h)
    cost = np.ma.median(np.ma.array(diffs, mask=~common), axis=-1).filled(np.inf)
    cost[common.sum(axis=-1) < 3] = np.inf
    slots = assignments(cost)
    return predicted, pred_in, cost, slots


def geometry(xyz, reference, inside, size, count):
    predicted, pred_in, cost, slots = match_geometry(xyz, reference, inside, size)
    eligible = np.take(inside, PALM, axis=2).sum(axis=2) >= 3
    state = np.full(slots.shape, 'matched', dtype='U24')
    state[slots == -2] = 'ambiguous'
    for side in range(2):
        unassigned = slots[:, side] == -1
        has = (cost[:, side] <= GATE).any(axis=1)
        state[unassigned & has, side] = 'lost_competition'
        state[unassigned & ~has, side] = 'outside_gate'
        state[unassigned & ~np.isfinite(cost[:, side]).any(axis=1), side] = 'limited_common_palm'
        state[unassigned & (count == 0), side] = 'zero_detections'
    state[~eligible] = 'limited_projection'
    state[~inside.any(axis=2)] = 'outside_image'
    paired_points = np.full((*slots.shape, len(POINTS)), np.nan, dtype=np.float32)
    for side in range(2):
        idx = np.flatnonzero(slots[:, side] >= 0)
        assigned = slots[idx, side]
        mask = (inside[idx, side] & pred_in[idx, assigned])[:, POINTS]
        d = np.linalg.norm(reference[idx, side] - predicted[idx, assigned], axis=-1)[:, POINTS]
        paired_points[idx, side] = np.where(mask, d, np.nan)
    return slots, state, cost, paired_points


def source_quality(folder, labels):
    result = []
    demo = folder.parent.name == 'EgoDemo'
    listed = set()
    if demo:
        entries = json.loads((folder / 'bad_frame_ratio.json').read_text())['hand_bad_frame_details']
        listed = {e['frame'] for e in entries}
        require(all(0 <= i < len(labels) for i in listed), 'Invalid Demo annotation frame index')
    for i, flags in enumerate(labels):
        row = []
        for side in SIDES:
            own = sorted(r.split(':', 1)[1] for r in flags if r.startswith(side + ':'))
            if own:
                row.append('+'.join(own))
            elif demo:
                row.append('listed_without_own_reason' if i in listed else 'unlisted_unknown')
            else:
                row.append('other_hand_only' if flags else 'explicit_normal')
        result.append(row)
    return np.array(result)


def validate_outputs(z, n):
    count = z['count']
    xyz, hand, score = z['xyz'], z['handedness'], z['handedness_score']
    alive = np.arange(MAX_HANDS) < count[:, None]
    require(xyz.shape == (n, MAX_HANDS, 21, 3) and count.shape == (n,) and np.all(count <= MAX_HANDS) and
            np.isfinite(xyz[alive]).all() and np.isnan(xyz[~alive]).all() and
            np.isin(hand[alive], [0, 1]).all() and (hand[~alive] == -1).all() and
            np.all((.5 <= score[alive]) & (score[alive] <= 1)) and np.isnan(score[~alive]).all(),
            'Invalid model result or missing per-frame placeholders')
    return count, xyz, hand


def check_prediction(job, clock, z):
    from .c_full_video_decode import probe

    stream, packets, _ = probe(job['path'])
    n = len(clock)
    pts = z['pts']
    require(str(z['relative']) == job['relative'] and str(z['config']) == json.dumps(CONFIG, sort_keys=True)
            and str(z['sha256']) == sha256(job['path'].read_bytes()).hexdigest(), 'Prediction provenance')
    require(n == len(pts) == int(stream['nb_frames']) and np.array_equal(pts, packets) and
            str(z['time_base']) == stream['time_base'] and list(z['size']) == [stream['width'], stream['height']],
            'Decoded frames, packet PTS, or source clock mismatch')
    seconds = (pts - pts[0]) * float(Fraction(stream['time_base']))
    delta = seconds - (clock-clock[0]) * 1e-9
    require(np.all(np.diff(clock) > 0) and np.abs(delta).max() < .5 * np.min(np.diff(seconds)), 'Source/PTS drift')
    if job['dataset'] != 'EgoDemo':
        side = read_rows(job['path'].with_name(job['view'] + '_frames.csv'))
        require(np.array_equal(clock, [int(r['video_timestamp_ns']) for r in side]) and
                all(int(r['_message_index']) == i and r['frame_id'] == job['view'] + '_camera'
                    for i, r in enumerate(side)), 'Camera sidecar alignment')
    count, xyz, hand = validate_outputs(z, n)
    return count, xyz, hand, np.rint(delta * 1e9).astype(np.int64)


STATES = ('matched', 'ambiguous', 'lost_competition', 'outside_gate', 'limited_common_palm',
          'zero_detections', 'limited_projection', 'outside_image')


def compare(jobs):
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq
    from .c_full_hand_projection import inputs
    from .c_quality_video_examples import marked_clock
    from .c_records import raw_metadata

    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)
    private = RUN / 'comparison'
    private.mkdir(exist_ok=True, parents=True, mode=0o700)
    predicted = RUN / 'predictions'
    require({p.stem for p in predicted.glob('*.npz')} == {j['key'] for j in jobs}, 'Incomplete predictions')
    camera_rows, hand_rows, point_rows, paired_views = [], [], [], []
    for folder, group in groupby(jobs, key=lambda j: j['folder']):
        record = raw_metadata(folder)
        clock, labels, _ = marked_clock(folder)
        native_clock = {}
        if folder.parent.name == 'EgoDemo':
            source = pq.read_table(sorted((folder / 'data').rglob('*.parquet')), columns=['timestamp'], use_threads=False)
            seconds = source['timestamp'].to_numpy()
            require(np.array_equal(clock, np.rint(seconds.astype(np.float64)*1e9).astype(np.int64)),
                    'Native Demo timestamp and nanosecond conversion differ')
            native_clock['source_timestamp_s'] = seconds
        quality = source_quality(folder, labels)
        official = {(view, side): (uv, inside, [int(w), int(h)])
                    for view, side, uv, inside, w, h in inputs(folder, clock)}
        head_matches = {}
        for j in group:
            require(j['uuid'] == record['episode_uuid'] and j['environment'] == record['environment_id'],
                    'Source record and split identifiers differ')
            n = len(clock)
            view = j['view']
            meta = {k: j[k] for k in ('uuid', 'dataset', 'split', 'view')}
            with np.load(predicted / (j['key'] + '.npz'), allow_pickle=False) as z:
                count, xyz, hand, delta = check_prediction(j, clock, z)
                size = z['size']
                joined = dict(frame_index=np.arange(n), source_ns=clock, pts=z['pts'], time_base=z['time_base'],
                              delta_ns=delta, relative=j['relative'], source_video_sha256=z['sha256'], **native_clock)
            row = dict(**meta, files=1, frames=n, max_abs_source_pts_us=np.abs(delta).max()/1e3,
                       predicted_hands=int(count.sum()))
            row.update({f'frames_{k}_hands': int((count == k).sum()) for k in range(MAX_HANDS+1)})
            camera_rows.append(row)
            if view.startswith('head_'):
                sides = [official[view, side] for side in SIDES]
                require(all(np.array_equal(s[2], size) for s in sides), 'Projection and decoded dimensions differ')
                uv = np.stack([s[0] for s in sides], axis=1)
                inside = np.stack([s[1] for s in sides], axis=1)
                slots, state, cost, errors = geometry(xyz, uv, inside, size, count)
                head_matches[view] = (state == 'matched').ravel()
                reflected = xyz.copy()
                reflected[..., 0] = 1 - reflected[..., 0]
                _, _, _, mirrored_slots = match_geometry(reflected, uv, inside, size)
                joined.update(quality=quality, projected_xy=uv, projected_inside=inside, slots=slots, state=state,
                              pair_cost_diag=cost, mirrored_slots=mirrored_slots, point_indices=POINTS, point_errors_px=errors)
                raw_same = (hand[np.arange(n)[:, None], slots.clip(min=0)] == np.arange(2)) & (slots >= 0)
                hand_rows.append(pd.DataFrame(dict(**meta, hand=np.tile(SIDES, n), quality=quality.ravel(),
                                 state=state.ravel(), mirror_matched=(mirrored_slots >= 0).ravel(), raw_same=raw_same.ravel(),
                                 any_prediction=np.repeat(count > 0, 2), all21_inside=inside.all(axis=2).ravel())))
                point_rows.append(errors.reshape(-1, len(POINTS)))
            atomic_npz(private / (j['key'] + '.npz'), **joined)
        paired_views.append(pd.DataFrame(dict(uuid=j['uuid'], dataset=j['dataset'], split=j['split'],
                            hand=np.tile(SIDES, n), quality=quality.ravel(), **head_matches)))
    cameras = pd.DataFrame(camera_rows)
    hands = pd.concat(hand_rows, ignore_index=True)
    points = np.concatenate(point_rows)
    require(len(cameras) == 184 and cameras.frames.sum() == 178512 and cameras.uuid.nunique() == 69 and
            len(hands) == 2*cameras[cameras.view.str.startswith('head_')].frames.sum() and
            hands.state.isin(STATES).all() and np.array_equal(hands.state == 'matched', np.isfinite(points).any(axis=1)),
            'Aggregate conservation failure')
    publish(cameras, hands, points, pd.concat(paired_views, ignore_index=True))


def groups(table, definitions):
    for fields in definitions:
        if not fields:
            yield {'scope': 'overall'}, table.index.to_numpy()
        else:
            for key, ids in table.groupby(list(fields), sort=True).indices.items():
                key = key if isinstance(key, tuple) else (key,)
                yield {'scope': '_'.join(fields), **dict(zip(fields, key))}, ids


def residuals(values):
    finite = values[np.isfinite(values)].astype(np.float64)
    if len(finite):
        return dict(points=len(finite), mean_px=finite.mean(), p50_px=np.median(finite), p90_px=np.percentile(finite, 90))
    return dict(points=0, mean_px=None, p50_px=None, p90_px=None)


def within_record(hands):
    """Standardize explicit-normal match rates using marked record/side/view weights."""
    import pandas as pd

    keys = ['dataset', 'uuid', 'view', 'hand']
    g = (hands.assign(matched=hands.state == 'matched').groupby([*keys, 'quality']).matched
         .agg(['sum', 'size']).reset_index())
    normal = g[g.quality == 'explicit_normal'].drop(columns='quality').rename(
        columns={'sum': 'normal_hits', 'size': 'normal_n'})
    marks = g[g.quality.isin(['occlusion', 'near edge']) & (g.dataset != 'EgoDemo')]
    paired = marks.merge(normal, on=keys, how='left', validate='many_to_one')
    require(paired.normal_n.notna().all(), 'Marked CSV hand has no explicit-normal reference in same record/view')
    paired['expected'] = paired['size'] * paired.normal_hits / paired.normal_n
    rows = []
    for fields in [('quality',), ('dataset', 'quality')]:
        for key, ids in groups(paired.reset_index(drop=True), [fields]):
            p = paired.iloc[ids]
            marked, normal_rate = p['sum'].sum()/p['size'].sum(), p.expected.sum()/p['size'].sum()
            rows.append(dict(**key, records=p.uuid.nunique(), record_hand_view_strata=len(p), marked_hand_views=p['size'].sum(),
                             marked_matched=p['sum'].sum(), normal_comparison_samples=p.normal_n.sum(),
                             marked_rate=marked, normal_rate_at_marked_weights=normal_rate, difference_pp=100*(marked-normal_rate)))
    return rows


def publish(cameras, hands, points, paired_views):
    import pandas as pd
    from .c_full_video_decode import write_csv

    OUT.mkdir(parents=True, exist_ok=True)
    views, associations, joints, stereo = [], [], [], []
    for key, ids in groups(cameras, [(), ('view',), ('dataset',), ('split',), ('dataset', 'view'),
                                      ('dataset', 'split', 'view')]):
        g = cameras.iloc[ids]
        row = {**key, 'records': g.uuid.nunique(), **g[['files', 'frames', 'predicted_hands',
               *[f'frames_{k}_hands' for k in range(MAX_HANDS+1)]]].sum().to_dict()}
        row['max_abs_source_pts_us'] = g.max_abs_source_pts_us.max()
        views.append(row)
    definitions = [(), ('dataset',), ('split',), ('view',), ('hand',), ('quality',), ('dataset', 'quality'),
                   ('dataset', 'split', 'view', 'hand', 'quality')]
    for key, ids in groups(hands, definitions):
        g = hands.iloc[ids]
        counts = g.state.value_counts()
        row = {**key, 'records': g.uuid.nunique(), 'hand_view_samples': len(g), 'any_prediction': int(g.any_prediction.sum()),
               'all21_inside': int(g.all21_inside.sum()), **{s: int(counts.get(s, 0)) for s in STATES},
               'raw_handedness_same': int(g.raw_same.sum()), 'mirror_control_matched': int(g.mirror_matched.sum()),
               'matched_in_both_geometries': int((g.mirror_matched & (g.state == 'matched')).sum()), **residuals(points[ids])}
        row['eligible'] = len(g) - row['outside_image'] - row['limited_projection']
        associations.append(row)
        if key['scope'] in ('overall', 'dataset', 'quality'):
            for i, joint in enumerate(POINTS):
                joints.append({**key, 'joint_index': joint, 'matched_hand_views': row['matched'],
                               **residuals(points[ids, i])})
    for key, ids in groups(paired_views, [(), ('dataset',), ('split',), ('hand',), ('quality',),
                                          ('dataset', 'split', 'hand', 'quality')]):
        g = paired_views.iloc[ids]
        left, right = g.head_left, g.head_right
        stereo.append(dict(**key, records=g.uuid.nunique(), hand_times=len(g), both=int((left & right).sum()),
                           head_left_only=int((left & ~right).sum()), head_right_only=int((~left & right).sum()),
                           neither=int((~left & ~right).sum())))
    require(stereo[0]['hand_times'] * 2 == len(hands) and
            2*stereo[0]['both'] + stereo[0]['head_left_only'] + stereo[0]['head_right_only'] == associations[0]['matched'],
            'Stereo/monocular association counts disagree')
    for kind, rows in [('views', views), ('association', associations), ('joints', joints), ('stereo', stereo),
                       ('within_record', within_record(hands))]:
        write_csv(OUT / f'detector_{kind}.csv', rows)
    print(pd.DataFrame([r for r in associations if r['scope'] == 'quality']).to_string(index=False))


def verify(jobs):
    """Read back all matched counts, cross-check prior geometry and replay in reverse."""
    import av
    import pandas as pd

    # Map the earlier labels onto the current source-specific missingness names.
    old = pd.read_csv(BASE / 'research/c_full_audit/hand_projection.csv')
    old.quality = old.quality.replace({'unlisted': 'unlisted_unknown', 'normal': 'explicit_normal'})
    old.loc[(old.dataset == 'EgoDemo') & (old.quality == 'other_hand_only'), 'quality'] = 'listed_without_own_reason'
    keys = ['dataset', 'view', 'hand', 'quality']
    curr = pd.read_csv(OUT / 'detector_association.csv')
    curr = curr[curr.scope == 'dataset_split_view_hand_quality'].groupby(keys)[
        ['hand_view_samples', 'all21_inside', 'outside_image']].sum().sort_index()
    prev = old.set_index(keys)[['hand_ticks', 'all21_inside', 'none_inside']].sort_index()
    require(prev.index.equals(curr.index) and np.array_equal(prev, curr), 'Earlier projection audit differs')
    n, hands = 0, 0
    selected = {}
    for j in jobs:
        with np.load(RUN / 'comparison' / (j['key'] + '.npz'), allow_pickle=False) as z:
            count = len(z['source_ns'])
            require(np.array_equal(z['frame_index'], np.arange(count)), 'Missing joined frame')
            n += count
            if j['view'].startswith('head_'):
                slots = z['slots']
                require(np.array_equal(slots >= 0, z['state'] == 'matched') and
                        np.all((slots[:, 0] < 0) | (slots[:, 0] != slots[:, 1])), 'Invalid joined identity')
                hands += np.count_nonzero(slots >= 0)
        if j['split'] == 'train':
            selected.setdefault((j['dataset'], j['view']), j)
    overall = pd.read_csv(OUT / 'detector_association.csv').iloc[0]
    require(n == 178512 and hands == overall.matched, 'Readback totals')

    # A new graph replays four source positions per source×view, in reverse of
    # the original display order. This checks for unintended tracking/state reuse.
    os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
    frames, positives = 0, 0
    with model() as graph:
        for j in selected.values():
            with np.load(RUN / 'predictions' / (j['key'] + '.npz'), allow_pickle=False) as pred, av.open(str(j['path'])) as video:
                stream, = video.streams.video
                stream.codec_context.thread_count = 1
                indices = np.linspace(0, stream.frames-1, 4, dtype=int)
                images = []
                for i, frame in enumerate(video.decode(stream)):
                    if i in indices:
                        require(frame.pts == pred['pts'][i], 'Repeated PTS mismatch')
                        images.append((i, frame.reformat(width=stream.width//2, height=stream.height//2,
                                      format='rgb24', interpolation='BILINEAR').to_ndarray()))
                for i, image in reversed(images):
                    out = graph.process(image)
                    detections = out.multi_hand_landmarks or []
                    require(len(detections) == pred['count'][i], 'Repeated detections differ')
                    for k, (hand, cls) in enumerate(zip(detections, out.multi_handedness or [])):
                        xyz = np.array([(p.x, p.y, p.z) for p in hand.landmark], dtype=np.float32)
                        cat, = cls.classification
                        require(np.array_equal(xyz, pred['xyz'][i,k]) and
                                cat.label.lower() == SIDES[pred['handedness'][i,k]] and
                                cat.score == pred['handedness_score'][i,k], 'Repeated model output differs')
                    frames += 1
                    positives += bool(detections)
    require(frames == 4*len(selected) and 0 < positives < frames, 'Replay coverage')
    result = dict(files=len(jobs), frames=n, matched_hand_views=int(hands), projection_groups=len(prev),
                  replay_files=len(selected), replay_frames=frames, replay_positive=positives)
    (RUN / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(result)


def self_check():
    # Distinct geometry, duplicate person/hand, a rejected third party, collision,
    # empty detections, in-front but offscreen, a partial palm and cutoff ties.
    inf = np.inf
    costs = np.array([[[.02,.005],[.002,.03]], [[.001,.004],[inf,inf]], [[.08,inf],[inf,inf]],
                      [[.01,inf],[.04,inf]], [[.05,inf],[inf,inf]], [[.05+.000001,inf],[inf,inf]],
                      [[.01,inf],[.012,inf]]])
    expected = [[1,0],[-2,-1],[-1,-1],[0,-1],[0,-1],[-1,-1],[-2,-2]]
    require(np.array_equal(assignments(costs), expected), 'Assignment invariants')
    four = np.array([[[.10,.08,.075,.01],[.01,.12,.055,.12]], [[.01,.08,.012,.10],[.11,.004,.1,.09]]])
    require(np.array_equal(assignments(four), [[3,0],[-2,1]]), 'Surplus candidates and side-specific ambiguity')
    order = [3,2,0,1]
    require(np.array_equal(assignments(four[:, :, order]), [[0,2],[-2,3]]), 'Candidate order invariance')
    size = [400, 300]
    xyz = np.full((4, MAX_HANDS, 21, 3), np.nan, np.float32)
    xyz[:3, 0] = [.25,.5,0]
    xy = np.empty((4,2,21,2))
    xy[:,0] = [103,154]
    xy[:,1] = [350,80]
    valid = np.ones((4,2,21), bool)
    valid[1,0] = False
    valid[2,0] = False
    valid[2,0,[0,5]] = True
    _, state, _, errors = geometry(xyz, xy, valid, size, np.array([1,1,1,0]))
    require(state[:,0].tolist() == ['matched','outside_image','limited_projection','zero_detections'] and
            np.all(errors[0,0] == 5) and np.isnan(errors[1:]).all(), 'Visibility and point denominator')
    print('Synthetic assignment, missingness, visibility and native-pixel distance checks passed.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['detect', 'compare', 'verify', 'self-check'])
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--benchmark', type=int, default=0, metavar='FRAMES_PER_FILE')
    args = parser.parse_args()
    if args.action == 'detect':
        os.umask(0o077)
        require(args.benchmark >= 0, 'Benchmark frame count must be nonnegative')
        detect(inventory(args.root), args.workers, args.benchmark)
    elif args.action == 'compare':
        compare(inventory(args.root))
    elif args.action == 'verify':
        verify(inventory(args.root))
    else:
        self_check()


if __name__ == '__main__':
    main()
