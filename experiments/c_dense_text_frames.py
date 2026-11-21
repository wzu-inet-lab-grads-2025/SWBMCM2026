"""Frozen CLIP audit of all C camera frames. Run fetch, encode, then score.

Only fetch contacts the public model repository. All source data and per-file
vectors stay local. AI-assisted dataset audit in OpenCode, 2026-11-21.
"""

import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
import csv
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import urllib.request

for variable in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[variable] = '2' if variable in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS') else '1'
os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false',
                  HF_HUB_DISABLE_TELEMETRY='1', DO_NOT_TRACK='1')

import numpy as np
from c_full_video_decode import BASE, FRAME, ROOT, TIME_BASE, inventory, probe
from c_records import raw_metadata, rows

RUN = BASE / 'runs/c_dense_vlm'
MODEL = RUN / 'model'
PUBLIC = BASE / 'research/c_dense_audit'
REV = '3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268'
WEIGHT = 'a63082132ba4f97a80bea76823f544493bffa8082296d62d71581a4feff1576f'
# Git object IDs for the auxiliary files in the same immutable model revision.
AUX = {'config.json': 'a2a88b96561196777ca173b15309ea859f4d2ce0',
       'merges.txt': 'bbfec752c9a675946c6dce106def6f35c882dcc2',
       'preprocessor_config.json': '5a12a1eb250987a4eee0e3e7d7338c4b22724be1',
       'special_tokens_map.json': '9bfb42aa97dcd61e89f279ccaee988bccb4fabae',
       'tokenizer.json': '564c0ebd5ce29c4ee4864004aee693deadd3128c',
       'tokenizer_config.json': '4fdaf6842dd5a725b940c92b6b692490ce59d548',
       'vocab.json': '182766ce89b439768edadda342519f33802f5364'}
PROTOCOL = dict(model='openai/clip-vit-base-patch32', revision=REV, weights_sha256=WEIGHT,
                frame='each display frame, ffmpeg bicubic shorter edge 224, centered RGB24 crop 224',
                visual='CLIP projected pooler, FP32 L2-normalized then stored FP16, all rows',
                text='original English, no prompt, CLIP tokenizer without truncation, one class per token sequence',
                span='raw source clock [start,end), matched by presentation-order row',
                candidates='within record only, chronological first occurrence of each token sequence',
                mismatch='next interval in cyclic temporal order with a different token sequence',
                paired_swap='compare diagonal with swapped text for both intervals in each directed pair',
                pooling='mean cosine over frames and equally over available cameras at each time',
                tie='fractional first place, half pair credit, exact score equality',
                splits='source x official split, no fitting, threshold selection or sampling', version=1)
TAG = hashlib.sha256(json.dumps(PROTOCOL, sort_keys=True).encode()).hexdigest()
PIXELS = 224 * 224 * 3


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(2**20), b''):
            h.update(chunk)
    return h.hexdigest()


def verify_model_file(path, name):
    if name == 'pytorch_model.bin':
        ok = digest(path) == WEIGHT
    else:
        data = path.read_bytes()
        ok = hashlib.sha1(f'blob {len(data)}\0'.encode() + data).hexdigest() == AUX[name]
    if not ok:
        raise ValueError('Unexpected model file integrity: ' + name)


def model_files(fetch=False):
    MODEL.mkdir(exist_ok=True, parents=True)
    for name in ('pytorch_model.bin', *AUX):
        path = MODEL / name
        if fetch and not path.exists():
            with tempfile.NamedTemporaryFile(dir=MODEL, delete=False) as out:
                tmp = Path(out.name)
                try:
                    url = f'https://huggingface.co/openai/clip-vit-base-patch32/resolve/{REV}/{name}?download=true'
                    with urllib.request.urlopen(url, timeout=90) as source:
                        for chunk in iter(lambda: source.read(2**20), b''):
                            out.write(chunk)
                    out.flush()
                    verify_model_file(tmp, name)
                    tmp.replace(path)
                finally:
                    tmp.unlink(missing_ok=True)
        verify_model_file(path, name)


def write_json(path, data):
    with tempfile.NamedTemporaryFile('w', dir=path.parent, delete=False) as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    Path(f.name).replace(path)


def load_clip():
    import torch
    from transformers import CLIPModel
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    model_files()
    return CLIPModel.from_pretrained(MODEL, local_files_only=True, attn_implementation='sdpa').eval().requires_grad_(False)


class Vision:
    def __init__(self, model, gpu):
        import torch
        self.torch = torch
        self.gpu = gpu
        self.device = 'cpu'
        self.peak = 0
        self.batch = 8
        self.last_guard = 0
        self.model = model.vision_model
        self.projection = model.visual_projection
        config = json.loads((MODEL / 'preprocessor_config.json').read_text())
        self.mean = torch.tensor(config['image_mean']).view(1, 3, 1, 1)
        self.std = torch.tensor(config['image_std']).view(1, 3, 1, 1)
        if gpu and self.free_mib() >= 3072 and torch.cuda.is_available():
            props = torch.cuda.get_device_properties(0)
            torch.cuda.set_per_process_memory_fraction((896 * 2**20) / props.total_memory)
            self.stream = torch.cuda.Stream(priority=0)
            self.device = 'cuda:0'
            try:
                self.model.to(self.device, dtype=torch.float16)
                self.projection.to(self.device, dtype=torch.float16)
            except torch.cuda.OutOfMemoryError:
                self.cpu()
        print('vision device:', self.device, flush=True)

    def free_mib(self):
        try:
            return int(subprocess.check_output(['nvidia-smi', '-i', self.gpu, '--query-gpu=memory.free',
                       '--format=csv,noheader,nounits'], timeout=5, text=True).strip())
        except (OSError, ValueError, subprocess.SubprocessError):
            return 0

    def cpu(self):
        self.peak = max(self.peak, self.torch.cuda.max_memory_reserved())
        self.device = 'cpu'
        self.model.to('cpu', dtype=self.torch.float32)
        self.projection.to('cpu', dtype=self.torch.float32)
        self.torch.cuda.empty_cache()
        print('using two CPU inference threads', flush=True)

    def project(self, chunk):
        torch = self.torch
        inputs = torch.from_numpy(chunk.copy()).permute(0, 3, 1, 2).to(self.device, dtype=torch.float32)
        inputs = (inputs.div_(255) - self.mean.to(self.device)) / self.std.to(self.device)
        pooled = self.model(pixel_values=inputs.to(self.projection.weight.dtype)).pooler_output
        vec = torch.nn.functional.normalize(self.projection(pooled).float(), dim=-1)
        return vec.to(dtype=torch.float16, device='cpu').numpy()

    def __call__(self, images):
        torch = self.torch
        offset, chunks = 0, []
        with torch.inference_mode():
            while offset < len(images):
                if self.device != 'cpu' and time.monotonic() - self.last_guard >= 3:
                    self.last_guard = time.monotonic()
                    if self.free_mib() < 3072:
                        self.cpu()
                chunk = images[offset:offset+self.batch]
                if self.device == 'cpu':
                    out = self.project(chunk)
                else:
                    try:
                        with torch.cuda.stream(self.stream):
                            out = self.project(chunk)
                        self.peak = max(self.peak, torch.cuda.max_memory_reserved())
                    except torch.cuda.OutOfMemoryError:
                        out = None
                    if out is None:
                        torch.cuda.empty_cache()
                        if self.batch == 1:
                            self.cpu()
                        else:
                            self.batch //= 2
                        continue
                    time.sleep(.005)  # yield shared GPU between short batches
                chunks.append(out)
                offset += len(chunk)
        return np.concatenate(chunks)


def cache_path(j):
    return RUN / 'vectors' / f"file_{j['file_id']:03d}.npz"


def validate(a, job, stream, packets, video_hash):
    x, pts = a['vectors'], a['pts']
    if (str(a['protocol']) != TAG or str(a['video_sha256']) != video_hash or
            str(a['time_base']) != stream['time_base'] or not np.array_equal(pts, packets) or
            (np.diff(pts) <= 0).any() or len(pts) != int(stream['nb_frames']) or
            x.shape != (len(job['stamps']), 512) or len(pts) != len(x) or x.dtype != np.float16 or
            not np.allclose(np.linalg.norm(x.astype('float32'), axis=1), 1., atol=.001, rtol=0)):
        raise ValueError('Vector, frame count, source clock or PTS check failed')


def encode_file(job, vision):
    path = job['path']
    stream, packets, _ = probe(path)
    video_hash = digest(path)
    dest = cache_path(job)
    if dest.exists():
        with np.load(dest, allow_pickle=False) as a:
            validate(a, job, stream, packets, video_hash)
        return 'verified'
    height, width = stream['height'], stream['width']
    w, h = 224 * width // min(width, height), 224 * height // min(width, height)
    vf = f'showinfo=checksum=0,scale={w}:{h}:flags=bicubic,format=rgb24,crop=224:224:{(w-224)//2}:{(h-224)//2}'
    cmd = ['ffmpeg', '-nostdin', '-hide_banner', '-nostats', '-loglevel', 'level+info', '-xerror',
           '-err_detect', 'explode', '-copyts', '-threads', '2', '-noautorotate', '-i', str(path),
           '-map', '0:v:0', '-filter_threads', '1', '-vf', vf, '-vsync', '0', '-threads', '1',
           '-f', 'rawvideo', 'pipe:1']
    blocks, pts, bases = [], [], set()
    with tempfile.TemporaryFile(dir=RUN) as log, subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=log) as proc:
        try:
            while block := proc.stdout.read(8 * PIXELS):
                image = np.frombuffer(block, dtype=np.uint8).reshape(-1, 224, 224, 3)
                blocks.append(vision(image))
        finally:
            proc.stdout.close()
        if proc.wait() != 0:
            raise RuntimeError(f'FFmpeg decode failed for file {job["file_id"]}')
        log.seek(0)
        for line in log:
            if b'Parsed_showinfo' not in line:
                continue
            if m := TIME_BASE.search(line):
                bases.add(m[1].decode())
            if b' pts_time:' in line:
                m = FRAME.search(line)
                if (not m or int(m[1]) != len(pts) or
                        (m[3].decode(), int(m[4]), int(m[5])) != (stream['pix_fmt'], width, height)):
                    raise ValueError('Unexpected display frame')
                pts.append(int(m[2]))
    if bases != {stream['time_base']}:
        raise ValueError('Stream and display time bases disagree')
    a = dict(vectors=np.concatenate(blocks), pts=np.array(pts, np.int64), time_base=stream['time_base'],
             protocol=TAG, video_sha256=video_hash, relative_path=str(path.relative_to(ROOT)),
             end_device=vision.device, peak_reserved_bytes=vision.peak)
    validate(a, job, stream, packets, video_hash)
    with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as f:
        np.savez(f, **a)
    Path(f.name).replace(dest)
    return 'encoded'


def encode():
    import transformers
    import torch
    (RUN / 'vectors').mkdir(exist_ok=True)
    jobs = inventory(ROOT)
    if (len(jobs), sum(len(j['stamps']) for j in jobs)) != (184, 178512):
        raise ValueError('Incomplete attachment')
    model = load_clip()
    vision = Vision(model, os.environ.get('CUDA_VISIBLE_DEVICES', ''))
    write_json(RUN / 'protocol.json', dict(**PROTOCOL, protocol_sha256=TAG, torch=torch.__version__,
               transformers=transformers.__version__, ffmpeg=subprocess.check_output(['ffmpeg','-version'], text=True).splitlines()[0]))
    for i, job in enumerate(jobs, 1):
        status = encode_file(job, vision)
        print(f'{i}/{len(jobs)} {status} {len(job["stamps"])} rows, peak reserved {vision.peak / 2**20:.0f} MiB', flush=True)


# Supplementary, text-only rule specified before scoring. Require the opening
# action verb and discard text mentioning any other family in the dictionary.
VERBS = {
    'take': r'pick(?:s|ed|ing)?|grab(?:s|bed|bing)?|grasp(?:s|ed|ing)?|lift(?:s|ed|ing)?',
    'put': r'plac(?:e|es|ed|ing)|put(?:s|ting)?|releas(?:e|es|ed|ing)',
    'open': r'open(?:s|ed|ing)?', 'close': r'clos(?:e|es|ed|ing)|shut(?:s|ting)?',
}
OPPOSITE = {'take': 'put', 'put': 'take', 'open': 'close', 'close': 'open'}


def verb_family(text):
    text = text.casefold().strip()
    groups = [name for name, words in VERBS.items() if re.search(r'\b(?:' + words + r')\b', text)]
    if len(groups) != 1:
        return None
    words = VERBS[groups[0]]
    if re.match(r'^(?:(?:the (?:person|operator|worker)|he|she|they) )?(?:' + words + r')\b', text):
        return groups[0]
    return None


def span_index(job, official):
    folder = ROOT.joinpath(*job['path'].relative_to(ROOT).parts[:3])
    meta = raw_metadata(folder)
    units = sorted(official.pop(meta['episode_uuid']), key=lambda u: int(u['source_segment_index']))
    if job['dataset'] == 'EgoDemo':
        annotations = json.loads((folder / 'annotation.json').read_text(), parse_float=Fraction)
        spans = [(Fraction(r['start_time']), Fraction(r['end_time']), r['description'])
                 for r in annotations['subtask_annotations']]
        times = [Fraction(float(t)) for t in job['stamps']]
    else:
        annotations = rows(folder / 'tables/annotation_semantic_segments.csv')
        spans = [(Fraction(r['segment.start_time']), Fraction(r['segment.end_time']),
                  r['segment.subtask_description']) for r in annotations]
        origin = Fraction(int(annotations[0]['header.ts'])) - spans[0][0] * 10**9
        if origin != int(job['stamps'][0]):
            raise ValueError('Unexpected semantic clock origin')
        times = [Fraction(int(t) - int(origin), 10**9) for t in job['stamps']]
    if (len(spans) != len(units) or not units or any(b <= a for a, b in zip(times, times[1:]))):
        raise ValueError('Invalid annotation count or source timestamps')
    result = []
    for i, (u, (a,b,text)) in enumerate(zip(units, spans)):
        if ((Fraction(u['start_s']), Fraction(u['end_s']), u['description']) != (a,b,text) or
                int(u['source_segment_index']) != i or u['dataset'] != job['dataset'] or
                u['sample'] != folder.name or u['split'] != job['split'] or
                u['environment_id'] != meta['environment_id'] or (i and a < spans[i-1][1])):
            raise ValueError('Original annotation disagrees with official index or overlaps')
        lo, hi = bisect_left(times, a), bisect_left(times, b)
        if lo == hi:
            raise ValueError('Interval has no available source frames')
        result.append((lo, hi, text))
    return result


def text_candidates(model, tokenizer, spans):
    import torch
    original = [text for _,_,text in spans]
    tokens = tokenizer(original, padding=False, truncation=False)['input_ids']
    if max(map(len, tokens)) > model.config.text_config.max_position_embeddings:
        raise ValueError('Raw text exceeds the model context window')
    classes, index, first = [], {}, []
    for text, ids in zip(original, tokens):
        key = tuple(ids)
        if key not in index:
            index[key] = len(first)
            first.append(text)
        classes.append(index[key])
    with torch.inference_mode():
        inputs = tokenizer(first, padding=True, truncation=False, return_tensors='pt')
        vec = model.get_text_features(**inputs)
        matrix = torch.nn.functional.normalize(vec.float(), dim=-1).numpy()
    if not np.allclose(np.linalg.norm(matrix, axis=1), 1., rtol=0, atol=1e-5):
        raise ValueError('Invalid normalized text vectors')
    return classes, matrix, max(map(len, tokens))


def frame_scores(job, text):
    import pandas as pd
    stream, packets, _ = probe(job['path'])
    with np.load(cache_path(job), allow_pickle=False) as a:
        validate(a, job, stream, packets, digest(job['path']))
        if str(a['relative_path']) != str(job['path'].relative_to(ROOT)):
            raise ValueError('File inventory changed')
        x = a['vectors'].astype(np.float32)
        tb = Fraction(str(a['time_base']))
        pts = a['pts'] - int(a['pts'][0])
    if job['dataset'] != 'EgoDemo':
        side = pd.read_csv(job['path'].with_name(job['view'] + '_frames.csv'),
                           usecols=['video_timestamp_ns', '_message_index', 'frame_id'])
        if (not np.array_equal(side.video_timestamp_ns, job['stamps']) or
                not np.array_equal(side._message_index, np.arange(len(side))) or
                not (side.frame_id == job['view'] + '_camera').all()):
            raise ValueError('Source and video sidecar differ')
    x /= np.linalg.norm(x, axis=1, keepdims=True)
    return x @ text.T, pts, tb


def scored_intervals(scores, spans, classes, base, view):
    k = scores.shape[1]
    counts = Counter(classes)
    frequencies = np.bincount(classes, minlength=k) / len(classes)
    means = np.stack([scores[lo:hi].mean(axis=0, dtype=np.float64) for lo,hi,_ in spans])
    result = []
    for i, ((lo,hi,text), c) in enumerate(zip(spans, classes)):
        own, window = means[i,c], scores[lo:hi]
        frame_max = window.max(axis=1)
        top = means[i].max()
        top_credit = (means[i] == top) / np.sum(means[i] == top)
        r = dict(**base, ordinal=i, view=view, k=k, frames=hi-lo, repeat=counts[c]>1,
                 own=own, chance=1/k, top=top_credit[c], permutation_null=top_credit @ frequencies,
                 frame_top=float(np.sum((window[:,c] == frame_max) / (window == frame_max[:,None]).sum(axis=1))))
        if k > 1:
            j = next((i+d)%len(spans) for d in range(1,len(spans)) if classes[(i+d)%len(spans)] != c)
            other = classes[j]
            delta = own - means[i,other]
            swap = .5 * (delta + means[j,other] - means[j,c])
            df = window[:,c] - window[:,other]
            family, negative_family = verb_family(text), verb_family(spans[j][2])
            r.update(neg=means[i,other], gap=delta, swap=swap, opp=family is not None and OPPOSITE[family]==negative_family,
                     wins=int(delta>0), ties=int(delta==0), swap_wins=int(swap>0), swap_ties=int(swap==0),
                     frame_wins=int(np.sum(df>0)), frame_ties=int(np.sum(df==0)), alternative_ordinal=j)
        result.append(r)
    return result


def strata(entries):
    for source in ['all', 'EgoDemo', 'EgoPro', 'EgoStandard']:
        for split in ['all', 'train', 'validation', 'test']:
            yield {'source':source,'split':split}, [r for r in entries if source in ('all', r['source']) and
                                                   split in ('all', r['split'])]


def summary(data):
    # Frame-wise fractions weight aligned instants. Interval statistics give
    # each interval one vote. Records contribute evenly to record_macro_top1.
    if not data:
        return {}
    n = len(data)
    frames = sum(r['frames'] for r in data)
    def avg(name):
        return sum(r[name] for r in data) / n
    def total(name):
        return sum(r[name] for r in data)
    by_rec = defaultdict(list)
    for r in data:
        by_rec[r['record']].append(r)
    out = dict(records=len(by_rec), intervals=n, aligned_instants=frames, mean_candidates=avg('k'),
               own_cosine=avg('own'))
    if all(r['k'] == 1 for r in data):
        return out
    out.update(top1_credit=total('top'), interval_top1=avg('top'),
               interval_uniform_chance=avg('chance'), frame_top1=total('frame_top')/frames,
               frame_uniform_chance=sum(r['frames']*r['chance'] for r in data)/frames,
               record_macro_top1=np.mean([np.mean([r['top'] for r in group]) for group in by_rec.values()]))
    if all('gap' in r for r in data):
        wins, ties = total('wins'), total('ties')
        out.update(mismatch_cosine=avg('neg'), mean_gap=avg('gap'), wins=wins, ties=ties, losses=n-wins-ties,
                   pair_credit=(wins+.5*ties)/n, frame_pair_credit=(total('frame_wins')+.5*total('frame_ties'))/frames,
                   swap_mean_gap=avg('swap'), swap_wins=total('swap_wins'), swap_ties=total('swap_ties'),
                   swap_credit=(total('swap_wins')+.5*total('swap_ties'))/n)
    return out


def write_table(path, data, private=False):
    fields = list(dict.fromkeys(k for row in data for k in row))
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', newline='', dir=path.parent, delete=False) as f:
        writer = csv.DictWriter(f, fields, lineterminator='\n')
        writer.writeheader()
        for r in data:
            writer.writerow({k:format(v,'.9g') if isinstance(v,(float,np.floating)) else v for k,v in r.items()})
    tmp = Path(f.name)
    if not private:
        tmp.chmod(0o644)
    tmp.replace(path)


def lock_scoring_protocol():
    index_dir = ROOT / 'Restocking_Tasks_and_Constraints'
    index_hashes = {name:digest(index_dir/name) for name in ('record_split.csv', 'source_segment_index.csv')}
    lock = dict(**PROTOCOL, protocol_sha256=TAG, verb_families=VERBS, opposing=OPPOSITE,
               permutation_null='primary alternate set only: exact expected segment top1 when permuting interval texts within each record, preserving class counts',
               script_sha256=digest(Path(__file__)), official_index_sha256=index_hashes,
               subsets=['all alternate', 'single occurrence with alternate', 'repeated with alternate',
                        'opposing opening verbs with no other listed family in either text'])
    path = RUN / 'score_protocol.json'
    if path.exists() and json.loads(path.read_text()) != lock:
        raise ValueError('Scoring script, input index or protocol differs from the frozen run')
    if not path.exists():
        write_json(path, lock)
    return index_dir


def score():
    from transformers import CLIPTokenizerFast
    index_dir = lock_scoring_protocol()
    model = load_clip()
    tokenizer = CLIPTokenizerFast.from_pretrained(MODEL, local_files_only=True)
    official = defaultdict(list)
    for u in rows(index_dir / 'source_segment_index.csv'):
        official[u['episode_uuid']].append(u)
    records = defaultdict(list)
    for job in inventory(ROOT):
        records[job['record_id']].append(job)
    details, ledger = [], []
    for rid, jobs in records.items():
        first = jobs[0]
        base = dict(record=rid, source=first['dataset'], split=first['split'])
        spans = span_index(first, official)
        classes, text, max_tokens = text_candidates(model, tokenizer, spans)
        cameras = {}
        for job in jobs:
            scores, pts, tb = frame_scores(job, text)
            if not cameras:
                ref_pts, ref_tb = pts, tb
            if (not np.array_equal(pts * tb.numerator * ref_tb.denominator, ref_pts * ref_tb.numerator * tb.denominator) or
                    not np.array_equal(job['stamps'], first['stamps']) or job['view'] in cameras):
                raise ValueError('Cross-view time or view index mismatch')
            cameras[job['view']] = scores
            details.extend(scored_intervals(scores, spans, classes, base, job['view']))
        fused = np.mean(list(cameras.values()), axis=0, dtype=np.float64)
        details.extend(scored_intervals(fused, spans, classes, base, 'equal_views'))
        sizes = Counter(classes)
        n, k, views = len(spans), len(sizes), len(cameras)
        assigned = sum(hi-lo for lo,hi,_ in spans)
        instants = len(ref_pts)
        single = sum(s == 1 for s in sizes.values())
        ledger.append(dict(**base, records=1, files=views, view_frames=views*instants, instants=instants,
                           mapped_instants=assigned, mapped_view_frames=assigned*views, outside_instants=instants-assigned,
                           outside_view_frames=(instants-assigned)*views, intervals=n, raw_text_slots=len({s[2] for s in spans}),
                           token_class_slots=k, single_interval_records=int(n==1), only_one_class_records=int(k==1),
                           only_one_class_intervals=n if k==1 else 0, alternate_records=int(k>1),
                           alternate_intervals=n if k>1 else 0, unique_with_alt=single if k>1 else 0,
                           repeated_with_alt=n-single if k>1 else 0, repeated_intervals=n-single, max_tokens=max_tokens))
    if (official or len(ledger)!=69 or sum(r['files'] for r in ledger)!=184 or sum(r['view_frames'] for r in ledger)!=178512 or
            sum(r['intervals'] for r in ledger)!=349):
        raise ValueError('Incomplete aligned ledger')
    write_table(RUN/'interval_scores.csv', details, private=True)
    write_table(RUN/'record_counts.csv', ledger, private=True)
    coverage, retrieval = [], []
    for group, data in strata(ledger):
        counts = {k:sum(r[k] for r in data) for k in ledger[0] if k not in ('source','split','record','max_tokens')}
        coverage.append(dict(**group, **counts, longest_token_sequence=max(r['max_tokens'] for r in data)))
    for group, data in strata(details):
        for view in ('equal_views','head_left','head_right','left_wrist','right_wrist'):
            selected = [r for r in data if r['view']==view]
            alt = [r for r in selected if r['k']>1]
            groups = {'alternate': alt}
            if view == 'equal_views':
                groups.update(unique_with_alt=[r for r in alt if not r['repeat']], repeated_with_alt=[r for r in alt if r['repeat']],
                              opposing_next=[r for r in alt if r['opp']], one_class=[r for r in selected if r['k']==1])
            for name, subset in groups.items():
                result = summary(subset) if subset else dict(records=0,intervals=0,aligned_instants=0)
                if name == 'alternate' and subset:
                    result['within_record_permutation_top1'] = np.mean([r['permutation_null'] for r in subset])
                retrieval.append(dict(**group, view=view, subset=name, **result))
    if not PUBLIC.exists():
        PUBLIC.mkdir(exist_ok=True, mode=0o755)
        PUBLIC.chmod(0o755)
    write_table(PUBLIC/'text_coverage.csv', coverage)
    write_table(PUBLIC/'text_retrieval.csv', retrieval)
    print('Published two aggregate tables:', coverage[0], flush=True)


def verify():
    """Independent CPU forward on fixed training images, including a batch boundary."""
    from PIL import Image
    import torch
    from transformers import CLIPImageProcessor
    jobs = inventory(ROOT)
    first = next(j for j in jobs if j['split']=='train' and j['dataset']=='EgoDemo')
    model = load_clip()
    processor = CLIPImageProcessor.from_pretrained(MODEL, local_files_only=True)
    collected = defaultdict(list)
    for job in (j for j in jobs if j['record_id']==first['record_id']):
        stream, _, _ = probe(job['path'])
        width, height = stream['width'], stream['height']
        w, h = (224*n//min(width,height) for n in (width,height))
        select = r'select=eq(n\,0)+eq(n\,7)+eq(n\,8)'
        filters = {'production_resize':f'{select},scale={w}:{h}:flags=bicubic,format=rgb24,crop=224:224:{(w-224)//2}:{(h-224)//2}',
                   'official_pil_resize':f'{select},format=rgb24'}
        with np.load(cache_path(job), allow_pickle=False) as archive:
            ref = torch.tensor(archive['vectors'][[0,7,8]].astype(np.float32))
        for mode, vf in filters.items():
            cmd = ['ffmpeg','-nostdin','-v','error','-threads','1','-i',str(job['path']),'-map','0:v:0',
                   '-filter_threads','1','-vf',vf,'-vsync','0','-frames:v','3','-threads','1','-f','rawvideo','pipe:1']
            data = np.frombuffer(subprocess.check_output(cmd), dtype=np.uint8)
            native = mode=='official_pil_resize'
            rgb = data.reshape(3, height if native else 224, width if native else 224, 3)
            pixels = processor([Image.fromarray(a) for a in rgb], do_resize=native, do_center_crop=native, return_tensors='pt')
            with torch.inference_mode():
                x = model.get_image_features(**pixels)
                collected[mode].extend(torch.nn.functional.cosine_similarity(x,ref).tolist())
    out = {mode:dict(images=len(data), minimum=min(data), median=float(np.median(data)), maximum=max(data))
           for mode,data in collected.items()}
    write_json(RUN/'numerical_check.json', out)
    print(json.dumps(out, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['fetch', 'encode', 'lock', 'score', 'verify'])
    p.add_argument('--gpu-uuid', default='', help='physical GPU UUID explicitly made available to this job')
    args = p.parse_args()
    os.umask(0o077)
    RUN.mkdir(exist_ok=True, parents=True)
    if args.gpu_uuid and not args.gpu_uuid.startswith('GPU-'):
        p.error('Use a physical GPU UUID')
    os.environ['CUDA_VISIBLE_DEVICES'] = args.gpu_uuid
    if args.command == 'fetch':
        model_files(fetch=True)
    elif args.command == 'lock':
        lock_scoring_protocol()
    elif args.command == 'encode':
        encode()
    elif args.command == 'verify':
        verify()
    else:
        score()
