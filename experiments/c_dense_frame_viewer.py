"""Build a local-only all-frame, synchronized source-label viewer for the C audit.

AI-assisted data preparation, OpenCode, OpenAI GPT family, GPT-6.2 Astra routing
group, 2026-11-21. Exact routed model release date unavailable. Requires prior
decoded PTS evidence in runs/c_full_video/full; no network or media copying.
"""

import argparse
import base64
from fractions import Fraction
import html
import json
import os
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pyarrow.parquet as pq

from .c_full_record_audit import memberships
from .c_full_hand_projection import inputs
from .c_quality_video_examples import marked_clock
from .c_records import ROOT, official_records, rows


PAGE = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>C题逐帧复核</title>
<style>*{box-sizing:border-box}body{font:1rem/1.5 system-ui,sans-serif;background:#fafbfc;color:#202932;max-width:76rem;margin:auto;padding:1rem}header,nav{display:flex;gap:.65rem;align-items:center;flex-wrap:wrap;margin-bottom:.6rem}h1{font-size:1.4rem;margin:.2rem 0}select,button,input{font:inherit}button,select{padding:.27rem .55rem}input[type=range]{width:min(26rem,96%)}input[type=number]{width:6rem}#views{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,28rem),1fr));gap:.65rem}figure{margin:0}video{width:100%;display:block;background:#1b1e21}figcaption{font-size:.9rem}#labels{padding:.7rem;background:#e9edf2}#status{min-height:1.5em}</style>
<header><h1>C题逐帧复核</h1><label>记录 <select id="record"></select></label><span id="meta"></span></header>
<nav><button id="prev">上一帧</button><button id="next">下一帧</button><label>帧号（从 0 起） <input id="number" type="number" min="0" step="1"></label><input aria-label="选择源帧" id="slider" type="range" min="0" step="1"><span id="time"></span></nav>
<nav><label><input id="reveal" type="checkbox"> 显示官方标注</label><label><input id="pose" type="checkbox"> 头摄官方投影（左绿、右紫）</label><label><input id="detect" type="checkbox"> 图像独立检测（橙圈）</label><label><input id="measure" type="checkbox"> 图像核对读数</label><button id="mark" disabled>下个手部坏标记</button><button id="action" disabled>下个语义区间</button></nav>
<p id="labels" hidden></p><p id="readout" hidden></p><p id="status" role="status"></p><main id="views"></main>
<script src="POSE_FILE_HERE"></script><script id="payload" type="application/json">PAYLOAD_HERE</script><script>
const data = JSON.parse(document.querySelector('#payload').textContent);
const el = id => document.getElementById(id);
const cameras = [];
const positions = new Uint16Array(Uint8Array.from(atob(window.pose64), c => c.charCodeAt(0)).buffer);
const evidence = window.evidence64 ? new Int16Array(Uint8Array.from(atob(window.evidence64), c => c.charCodeAt(0)).buffer) : null;
el('measure').disabled = !evidence;
el('detect').disabled = !window.detectorData;
let frame = 0;
for (const record of data.menu) {
  const option = new Option(record.label, record.page);
  option.selected = record.page === data.page;
  el('record').add(option);
}
el('record').onchange = () => { location.href = el('record').value; };
el('meta').textContent = `${data.n} 个源时刻 · ${data.cameras.length} 个视角`;
el('slider').max = el('number').max = data.n - 1;

function status() {
  const ready = cameras.filter(({video, period, target}) => video.readyState >= 2 &&
    !video.seeking && Math.abs(video.currentTime - target) < period / 2).length;
  el('status').textContent = `${ready}/${cameras.length} 个视频已定位至所选展示时段`;
}
function labels() {
  const reveal = el('reveal').checked;
  el('labels').hidden = el('mark').disabled = el('action').disabled = !reveal;
  if (!reveal) return;
  const segment = data.spans.find(s => s.lo <= frame && frame < s.hi);
  const text = segment ? `区间 ${segment.index+1} · ${segment.description}` : '语义标注范围外';
  el('labels').textContent = (data.flags[frame] || data.defaultLabel) + ' ｜ ' + text;
}
function setTime(camera) {
  if (camera.video.readyState < 1) return;
  camera.target = Math.min((frame + .5) * camera.period, camera.video.duration - .000001);
  camera.video.currentTime = camera.target;
}
function overlay(c) {
  const official = el('pose').checked && c.eye !== null;
  const predicted = el('detect').checked && c.candidates;
  c.cap.textContent = predicted ? `${c.label} · 图像候选 ${c.offset[frame+1]-c.offset[frame]} 只手` : c.label;
  c.canvas.hidden = !official && !predicted;
  if (c.canvas.hidden) return;
  const ctx = c.canvas.getContext('2d');
  ctx.clearRect(0, 0, c.canvas.width, c.canvas.height);
  if (official) for (let hand=0; hand<2; hand++) {
    ctx.fillStyle = hand === 0 ? '#48ff70' : '#fa4bce';
    for (let joint=0; joint<21; joint++) {
      const index = (((frame*2+c.eye)*2+hand)*21+joint)*2;
      const x=positions[index], y=positions[index+1];
      if (x === 65535) continue;
      ctx.beginPath(); ctx.arc(x, y, joint === 0 ? 9 : 5, 0, Math.PI*2); ctx.fill();
    }
  }
  if (predicted) {
    ctx.strokeStyle = '#ffc035'; ctx.lineWidth = 3;
    for (let i=c.offset[frame]*21; i<c.offset[frame+1]*21; i++) {
      const x=c.candidates[2*i], y=c.candidates[2*i+1];
      if (x === 65535) continue;
      ctx.beginPath(); ctx.arc(x,y,7,0,Math.PI*2); ctx.stroke();
    }
  }
}
function seek(value) {
  frame = Math.min(data.n - 1, Math.max(0, Math.trunc(Number(value) || 0)));
  el('slider').value = el('number').value = frame;
  el('time').textContent = (data.ns[frame] / 1e9).toFixed(6) + ' 源秒';
  cameras.forEach(setTime);
  cameras.forEach(overlay);
  labels();
  readout();
  status();
}
function readout() {
  el('readout').hidden = !evidence || !el('measure').checked;
  if (el('readout').hidden) return;
  const parts = [];
  for (let hand=0; hand<2; hand++) {
    const j = (frame*2+hand)*4;
    const stereo = evidence[j] ? (evidence[j+1]/1000).toFixed(3) : '无可比点';
    const flow = evidence[j+2] ? (evidence[j+3]/100).toFixed(2)+' px' : '无可比点';
    parts.push(`${hand?'右':'左'}手：双目相关优势 ${stereo}（${evidence[j]}/21 点），两眼运动残差 ${flow}（${evidence[j+2]}/21 点，360×273）`);
  }
  el('readout').textContent = parts.join(' ｜ ');
}
for (const view of data.cameras) {
  const fig = document.createElement('figure');
  const cap = document.createElement('figcaption');
  const video = document.createElement('video');
  cap.textContent = view.label;
  video.preload = 'auto'; video.playsInline = true; video.muted = true;
  const media = document.createElement('div');
  media.style.position = 'relative'; media.append(video); fig.append(cap, media);
  el('views').append(fig);
  const camera = {video, period:view.period, target:0, label:view.label, cap, eye:null};
  if (view.label.startsWith('head_')) camera.eye = view.label === 'head_left' ? 0 : 1;
  camera.canvas = document.createElement('canvas');
  camera.canvas.width=view.width; camera.canvas.height=view.height;
  camera.canvas.style.cssText='position:absolute;inset:0;width:100%;height:100%;pointer-events:none';
  camera.canvas.hidden=true; media.append(camera.canvas);
  if (window.detectorData) {
    const packed = window.detectorData[view.label];
    const count = Uint8Array.from(atob(packed.count), c => c.charCodeAt(0));
    camera.offset = new Uint32Array(count.length+1);
    for (let i=0;i<count.length;i++) camera.offset[i+1] = camera.offset[i]+count[i];
    camera.candidates = new Uint16Array(Uint8Array.from(atob(packed.xy), c=>c.charCodeAt(0)).buffer);
  }
  cameras.push(camera);
  video.onloadedmetadata = () => setTime(camera);
  video.onloadeddata = video.onseeked = status;
  video.onerror = () => { cap.textContent = view.label + ' · 无法加载本地 MP4'; };
  video.src = view.url;
}
function jump(candidates) {
  const next = candidates.find(index => index > frame);
  if (next !== undefined) seek(next);
  else el('status').textContent = '本记录后续没有该类标记';
}
el('prev').onclick = () => seek(frame-1);
el('next').onclick = () => seek(frame+1);
el('slider').oninput = event => seek(event.target.value);
el('number').onchange = event => seek(event.target.value);
el('reveal').onchange = labels;
el('pose').onchange = () => cameras.forEach(overlay);
el('detect').onchange = () => cameras.forEach(overlay);
el('measure').onchange = readout;
el('mark').onclick = () => jump(Object.keys(data.flags).map(Number).sort((a,b) => a-b));
el('action').onclick = () => jump(data.spans.map(s => s.lo));
document.addEventListener('keydown', event => {
  if (['INPUT','SELECT'].includes(event.target.tagName)) return;
  if (['ArrowLeft','ArrowRight'].includes(event.key)) {
    event.preventDefault();
    seek(frame + (event.key === 'ArrowLeft' ? -1 : 1));
  }
});
seek(new URLSearchParams(location.search).get('frame') || 0);
</script></html>'''


def encode_candidates(path, pts, size):
    with np.load(path,allow_pickle=False) as pred:
        counts, xy = pred['count'],pred['xyz'][...,:2]
        assert np.array_equal(pred['pts'],pts) and np.array_equal(pred['size'],size)
        used = np.arange(xy.shape[1])[None,:]<counts[:,None]
        points = xy[used]
        inside = np.all((points>=0) & (points<1),axis=2)
        packed = np.full(points.shape,65535,dtype='<u2')
        packed[inside] = np.clip(np.rint(points[inside]*size),[0,0],np.asarray(size)-1).astype(np.uint16)
        return dict(count=base64.b64encode(counts.astype('u1').tobytes()).decode('ascii'),
                    xy=base64.b64encode(packed.tobytes()).decode('ascii'))


def pages(root, decode, out, evidence, detectors):
    index = official_records(root)
    units = rows(root/'Restocking_Tasks_and_Constraints/source_segment_index.csv')
    ledger = {}
    for path in decode.glob('file_*.json'):
        entry = json.loads(path.read_text())
        ledger[(root/entry['path']).resolve()] = (entry,path.with_suffix('.npz'))
    out.mkdir(parents=True,exist_ok=True)
    menu = [dict(page=f'R{i:02d}.html',label=f"R{i:02d} · {r['dataset']} · {r['split']}") for i,r in enumerate(index,1)]
    seen, samples, view_frames, covered = set(), 0, 0, 0
    for ordinal,(record, item) in enumerate(zip(index,menu)):
        group = sorted([u for u in units if u['episode_uuid']==record['episode_uuid']],key=lambda u:int(u['source_segment_index']))
        folder = root/'Raw_Data'/record['dataset']/group[0]['sample']
        clock, flags, _ = marked_clock(folder)
        zero = clock-clock[0]
        if record['dataset']=='EgoDemo':
            table=pq.read_table(sorted((folder/'data').rglob('*.parquet')),columns=['timestamp'])
            times=[Fraction(x) for x in table['timestamp'].to_pylist()]
        else:
            times=[Fraction(int(t),10**9) for t in zero]
        bounds=[(Fraction(u['start_s']),Fraction(u['end_s']),u['description']) for u in group]
        spans=[dict(index=i,lo=lo,hi=hi,description=text) for i,((lo,hi),(_,_,text)) in enumerate(zip(memberships(times,bounds),bounds))]
        cameras, candidates = [], {}
        for video in sorted((folder/'videos').rglob('*.mp4')):
            entry,arrays=ledger[video.resolve()]
            assert video not in seen and (entry['dataset'],entry['split'])==(record['dataset'],record['split'])
            seen.add(video)
            with np.load(arrays,allow_pickle=False) as values:
                pts = values['pts']; delta=np.diff(pts)
                assert len(pts)==len(clock) and pts[0]==0 and (delta==delta[0]).all()
                period=float(int(delta[0])*Fraction(entry['time_base']))
            cameras.append(dict(label=entry['view'],url=quote(os.path.relpath(video.resolve(),out.resolve()),safe='/'),
                                period=period,width=entry['width'],height=entry['height']))
            if detectors is not None:
                pred_file = detectors/f"{record['dataset']}_{folder.name}_{entry['view']}.npz"
                candidates[entry['view']] = encode_candidates(pred_file,pts,[entry['width'],entry['height']])
        positions = np.full((len(clock),2,2,21,2),65535,dtype='<u2')
        for view,side,xy,inside,w,h in inputs(folder,clock):
            eye = ('head_left','head_right').index(view)
            hand = ('left','right').index(side)
            positions[:,eye,hand][inside] = np.clip(np.rint(xy[inside]),[0,0],[w-1,h-1]).astype(np.uint16)
        pose_page = item['page'].replace('.html','_pose.js')
        packed = base64.b64encode(positions.tobytes()).decode('ascii')
        code = 'window.pose64="'+packed+'";'
        if evidence is not None:
            measures = np.load(evidence/f'{ordinal:03d}.npy', allow_pickle=False)
            assert measures.shape == (len(clock),2,4) and measures.dtype==np.dtype('<i2')
            code += 'window.evidence64="'+base64.b64encode(measures.tobytes()).decode('ascii')+'";'
        if candidates:
            code += 'window.detectorData='+json.dumps(candidates,separators=(',',':'))+';'
        (out/pose_page).write_text(code,encoding='ascii')
        payload=dict(menu=menu,page=item['page'],n=len(clock),ns=zero.tolist(),cameras=cameras,spans=spans,
                     flags={i:', '.join(sorted(v)) for i,v in enumerate(flags) if v},
                     defaultLabel='质量未列（未知）' if record['dataset']=='EgoDemo' else '显式正常')
        content=json.dumps(payload,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c')
        (out/item['page']).write_text(PAGE.replace('POSE_FILE_HERE',pose_page).replace('PAYLOAD_HERE',content),encoding='utf-8')
        samples+=len(clock); view_frames+=len(clock)*len(cameras); covered+=sum(v['hi']-v['lo'] for v in spans)
    links=''.join(f'<li><a href="{r["page"]}">{html.escape(r["label"])}</a></li>' for r in menu)
    (out/'index.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>C题全帧复核索引</title><style>body{font:1rem/1.55 system-ui,sans-serif;max-width:58rem;margin:auto;padding:1rem}ul{columns:3 14rem;padding-left:1.3rem}li{break-inside:avoid}</style><h1>C题全帧复核索引</h1><p>选择记录后，可逐帧对照各视角。官方质量标记与区间文字默认折叠。</p><ul>'+links+'</ul></html>',encoding='utf-8')
    if len(seen)!=len(ledger):
        raise ValueError('Viewer and decoded inventory disagree')
    print(f'{len(menu)} records, {len(seen)} videos, {samples} source ticks, {view_frames} view frames, {covered} labeled source ticks')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=ROOT)
    p.add_argument('--decode',type=Path,default=Path('runs/c_full_video/full'))
    p.add_argument('--out',type=Path,default=Path('runs/c_dense_frame_viewer'))
    p.add_argument('--evidence',type=Path,help='Add per-hand readouts from c_dense_evidence_join')
    p.add_argument('--detectors',type=Path,help='Add optional local independent candidate points for all cameras')
    args=p.parse_args(); os.umask(0o077); pages(args.root,args.decode,args.out,args.evidence,args.detectors)
