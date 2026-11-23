"""第四问同初排交叉复核：接入分区搜索，并导出两类通信口径的逐场证据。

输入：已验收的毫米初排与 Q4 归档、explore/b-first-round 工作树；输出：新的 runs/q4 子目录。
复跑：nohup env OPENBLAS_NUM_THREADS=1 python3 -B -m checks.q4_paired_panel --output runs/q4/<新目录>。
AI 辅助：OpenCode / Magpie，路由标识 group/gpt-6.2-sol，2026-11-22（底层机构及版本发布日期未提供）。
"""

from argparse import ArgumentParser
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
import csv
from dataclasses import asdict, replace
from functools import lru_cache
import gzip
import hashlib
import json
import math
from pathlib import Path
from statistics import mean
import subprocess
import sys

from checks.q2_geometry import check_route
from checks.q4_replay import separation
from src.q4.candidates import initial_schedule_hash, prefix_hash
from src.q4.physics import EAST, OutageRule, Phase, build_prefix, load_archive


ROOT = Path(__file__).resolve().parents[1]
Q2 = ROOT / 'results/q2/毫米部署与初排_全程净空四十五厘米_20261121'
Q4 = ROOT / 'results/q4/毫米初排与联合断联限制_全程净空四十五厘米_20261121'
PEER = ROOT.with_name('SWBMCM2026-worktrees') / 'explore-b-first-round'
MODELS = ('strict_frozen', 'original_append', 'local_region', 'recommended', 'fixed_stations')
BASE_MODELS = set(MODELS) - {'local_region'}
LABEL = dict(zip(MODELS, ('strict_retention', 'retain_and_append', 'local_region', 'field_wide', 'fixed_station_control')))
TO_PEER = dict(zip(('TRAVELING', 'SLEWING', 'READY_WAITING', 'OBSERVING', 'RESETTING', 'CANCEL_RESET', 'IDLE', 'FAILED'),
                   ('drive', 'slew', 'wait', 'observe', 'reset', 'reset', 'idle', 'fail')))
FROM_PEER = {kind: phase for phase, kind in TO_PEER.items() if phase != 'CANCEL_RESET'}
WALL, ANGLE, END, TOL = 0.45, 5.0, 60.0, 1e-8  # m、度、s、s；最后一个仅用于同分判定。


def csv_rows(path):
    """以 UTF-8 流式读取普通或 gzip CSV。"""
    with (gzip.open if path.suffix == '.gz' else open)(path, 'rt', encoding='utf-8', newline='') as stream:
        yield from csv.DictReader(stream)


def json_rows(path):
    """流式读取归档的压缩 JSONL。"""
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        yield from map(json.loads, stream)


def encode(value):
    """生成可直接重放的紧凑 JSON，不舍入米制坐标或连续秒。"""
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def need(ok, message):
    """源文件或回放不一致时终止，避免写出已通过的假标志。"""
    if not ok:
        raise ValueError(message)


def same(a, b, tol=TOL):
    """仅比较连续秒的绝对差，避免相对容差影响正式排名。"""
    return abs(a - b) <= tol


def comparison(a, b):
    """先按启动数，再按计分时间；同场 N 对两种策略相同。"""
    if a['k'] != b['k']:
        return 'higher_k' if a['k'] > b['k'] else 'lower_k'
    if same(a['t_last_s'], b['t_last_s']):
        return 'tie'
    return 'earlier' if a['t_last_s'] < b['t_last_s'] else 'later'


class ReplayAdapter:
    """只移植分区求解调用；前缀和额外约束统一，局部搜索逻辑保留。"""

    def __init__(self, peer):
        """检查毫米名义时序，并加载另一工作树的分区搜索和独立事件审核器。"""
        sys.path.insert(0, str(peer))
        from src.q4 import reschedule
        from src.q4.replay import ScenarioReplay, outcome
        from src.shared import geometry, timeline
        from src.shared.plan import Plan
        from src.shared.scene import Scene
        from experiments.b.q4_audit.audit import Context, audit_record

        self.geometry, self.timeline, self.outcome, self.audit = geometry, timeline, outcome, audit_record
        self.archive = load_archive(Q2, wall_clearance_m=WALL, plan='practical', waypoint_decimals=3)
        self.names = (*self.archive.scene.targets, 'T4')
        self.vehicles = self.archive.scene.vehicles
        raw = (peer / 'experiments/b/scene.json').read_bytes()
        data, sha = json.loads(raw), hashlib.sha256(raw).hexdigest()
        self.scene = Scene.from_dict(data)
        self.nominal = []
        raw_routes = json.loads((Q2 / 'q22_routes.json').read_bytes())
        rows = {(r['target'], r['vehicle']): r for r in csv_rows(Q2 / 'q22_timeline.csv') if r['plan'] == 'practical'}
        for t in self.scene.targets:
            legs = []
            for leg in raw_routes:
                if (leg['plan'], leg['target']) != ('practical', t):
                    continue
                r = rows[t, leg['vehicle']]
                legs.append(dict(vehicle=leg['vehicle'], station=leg['destination'], path=leg['waypoints'],
                                 depart_s=float(r['depart_s']), arrival_s=float(r['arrive_s']),
                                 ready_s=float(r['turn_end_s']), observation_end_s=float(r['observation_end_s']),
                                 reset_end_s=float(r['reset_end_s'])))
            self.nominal.append(dict(target=t, start_s=float(r['observation_start_s']), legs=legs))
        plan = Plan.load(dict(schema='b.plan.v1', method='practical', scene_sha256=sha, jobs=self.nominal), 'practical', sha)
        starts = timeline.joint_observations(timeline.replay(self.scene, plan))
        need(all(same(starts[j['target']][0], j['start_s']) for j in self.nominal), 'Q2 nominal clock mismatch')
        self.nominal_plan = plan.to_dict()
        self.context = Context(data, self.nominal)
        self.engine = ScenarioReplay(self.scene, plan, data['q4'])

        # 护栏外包装不改队友工作树，不把墙距外扩误用到车间距或光学视线上。
        wall_scene = replace(self.scene, radius=self.scene.radius + WALL)
        def leg_with_wall(scene, target, leg):
            geometry.check_route(wall_scene, leg.path)
            return timeline.measure_leg(scene, target, leg)

        def pair_with_angle(scene, p, q, a, b, min_sine):
            return geometry.compatible_pair(scene, p, q, a, b, max(min_sine, math.sin(math.radians(ANGLE))))

        reschedule.measure_leg = leg_with_wall
        reschedule.compatible_pair = pair_with_angle
        cfg = json.loads((peer / 'configs/b/q4-reschedule.json').read_bytes())
        self.planner = reschedule.BeamPlanner(self.scene, plan, cfg)
        # 使用与全场方法相同的场界内移，避免恰好位于浮点临界边界。
        lo, hi = wall_scene.radius + 1e-7, wall_scene.field_side - wall_scene.radius - 1e-7
        for v, sites in self.planner.stations.items():
            self.planner.stations[v] = {tuple(min(hi, max(lo, x)) for x in p) for p in sites}
        self.prefix = lru_cache(maxsize=80)(
            lambda f, t, rule: build_prefix(self.archive, f, t, outage_rule=rule))
        self.clear_route = lru_cache(maxsize=4096)(
            lambda pts: check_route(self.archive.check, pts, pts[0], wall_clearance_m=WALL))

    def original_events(self, tracks):
        """把同一已执行前缀/保留轨迹换成队友的事件格式；不重新安排任何旧车次。"""
        events = []
        for v, track in zip(self.vehicles, tracks, strict=True):
            for phase in track:
                kind = TO_PEER[phase.kind]
                t = self.names[phase.alert] if phase.alert is not None else None
                row = dict(vehicle=v, target=t, kind=kind, t0=phase.start, t1=phase.end,
                           command_issued_s=0., command_effective_s=0., station=phase.points[-1],
                           gimbal0_deg=phase.pose0, gimbal1_deg=phase.pose1)
                if kind == 'fail':
                    row.update(t1=phase.start, position_xy_m=phase.points[0], gimbal_deg=phase.pose0)
                elif kind == 'drive':
                    row['path'] = phase.points if len(phase.points) > 1 else phase.points * 2
                elif kind in {'slew', 'reset'}:
                    goal = self.geometry.pointing(self.scene, t, phase.points[0]) if kind == 'slew' else EAST
                    row.update(gimbal_goal_deg=goal, interrupted=any(abs(a-b) > 1e-7 for a, b in zip(goal, phase.pose1)))
                events.append(row)
        return sorted(events, key=lambda e: (e['t0'], e['vehicle']))

    def tracks(self, events):
        """统一两套命令方言；将单点的故障退出补成不可用的窗口尾段。"""
        horizon = max(END, *(e['t1'] for e in events))
        tracks = {v: [] for v in self.vehicles}
        for e in events:
            kind = FROM_PEER[e['kind']]
            a = self.names.index(e['target']) if e['target'] is not None else None
            if kind == 'FAILED':
                phase = Phase(kind, e['t0'], horizon, a, (tuple(e['position_xy_m']),),
                              tuple(e['gimbal_deg']), tuple(e['gimbal_deg']))
            else:
                if e.get('reason') == 'cancel_unstarted_task':
                    kind = 'CANCEL_RESET'
                pts = tuple(map(tuple, e['path'])) if e['kind'] == 'drive' else (tuple(e['station']),)
                if len(pts) == 2 and pts[0] == pts[1]:
                    pts = pts[:1]
                phase = Phase(kind, e['t0'], e['t1'], a, pts, tuple(e['gimbal0_deg']), tuple(e['gimbal1_deg']))
            tracks[e['vehicle']].append(phase)
        return tuple(tuple(track) for track in tracks.values())

    def solve(self, meta, frozen):
        """按可见快照调用分区搜索，从输出事件复核分数、实采和几何。"""
        sc = meta['scenario_id']
        t4 = [meta[x] for x in ('theta_deg', 'R_m', 'H_m')] if meta['theta_deg'] is not None else None
        f, t, rule = meta['failed_vehicle'], meta['dispatch_s'], meta['variant']
        case = dict(id=sc, new_alert=t4, failed_vehicle=f, recovery_s=meta['t_rec_s'])
        prefix = self.prefix(self.vehicles.index(f) if f else None, t,
                             OutageRule.FULL_QUEUE if rule == 'main' else OutageRule.STRICT_OUTAGE)
        need(meta['vehicle_state_snapshot_hash'] == prefix_hash(prefix), (sc, rule, 'snapshot hash'))
        scene = replace(self.scene, targets={**self.scene.targets, 'T4': self.scene.project(self.scene.centre, t4)}) if t4 else self.scene
        origins = {}
        for v, state in zip(self.vehicles, prefix.states, strict=True):
            if v != f:
                pose, target = (EAST, None) if state.release > t else (state.pose, self.names[state.alert] if state.alert is not None else None)
                origins[v] = self.timeline.Available(state.point, state.release, pose, target)
        pending = [name for i, name in enumerate(scene.targets) if i not in prefix.locked]
        jobs, reason = self.planner.choose(scene, origins, pending, max(prefix.locked.values(), default=0.0), math.nextafter(END, -math.inf))
        fixed = list(prefix.fixed)
        if prefix.failed is not None:
            # 5 s 无断联：严格前缀不含同刻事件，故障退出仍需在时间轴打点。
            v = prefix.failed
            if not any(p.kind == 'FAILED' for p in fixed[v]):
                fixed[v] = (*fixed[v], prefix.original[v][-1])
        events = self.original_events(fixed)
        events.extend(self.timeline.execute_jobs(scene, jobs, origins, END, command_s=t))
        events.sort(key=lambda e: (e['t0'], e['vehicle']))
        new_score = self.outcome(events, scene.targets, 5, [0, END])
        kept_tracks = tuple(tuple(Phase.from_record(p) for p in track) for track in frozen['tracks'])
        kept_events = self.original_events(kept_tracks)
        old_score = self.outcome(kept_events, scene.targets, 5, [0, END])
        replace_queue = new_score['k'] > old_score['k'] or (
            new_score['k'] == old_score['k'] and new_score['t_last_s'] < old_score['t_last_s'] - 1e-9)
        if not replace_queue:
            events = kept_events
        outcome = new_score if replace_queue else old_score
        record = dict(scenario_id=sc, policy='local_region', events=events, rotation_model='independent_rate_limited_axes',
                      trace_complete_until_s=max(e['t1'] for e in events), **outcome)
        audit = self.audit(json.loads(encode(record)), case, context=self.context)
        need(audit['certified'], (sc, rule, audit['errors'], audit['missing_evidence']))
        if rule == 'main':
            # 原分支的独立时钟与公共前缀应给出相同分数；这也检查适配没有改变搜索行为。
            native = self.engine.run(case, self.planner)
            need(native['k'] == outcome['k'] and same(native['t_last_s'], outcome['t_last_s']), (sc, 'native replay parity'))
            for label, job in native['jobs'].items():
                expected = outcome['jobs'][label]
                need(set(job['vehicles']) == set(expected['vehicles']) and
                     (job['start_s'] is expected['start_s'] or same(job['start_s'], expected['start_s'])), (sc, label, 'native assignment parity'))
        tracks = self.tracks(events) if replace_queue else kept_tracks
        min_dist, _ = separation(tracks, 3.)
        need(min_dist >= 3. - TOL, (sc, rule, min_dist, 'vehicle overlap'))
        for track in tracks:
            for p in track:
                if p.kind != 'FAILED':
                    self.clear_route(p.points)
        selected = [dict(alert=j.target, legs=[asdict(leg) for leg in j.legs]) for j in jobs] if replace_queue else []
        for job in selected:
            a, b = (self.geometry.pointing(scene, job['alert'], leg['station'])[0] for leg in job['legs'])
            need(abs(math.sin(math.radians(a-b))) >= math.sin(math.radians(ANGLE)) - 1e-12,
                 (sc, rule, job['alert'], 'intersection angle guard'))
        return dict(tracks=tracks, selected=selected, native_score=outcome,
                    decision='replace_pending_queues' if replace_queue else 'retain_original', search_reason=reason,
                    checked_counts=audit['checked_counts'], min_vehicle_distance_m=min_dist,
                    candidate_k=new_score['k'], candidate_t_last_s=new_score['t_last_s'])


def start_worker(peer):
    """各进程只加载一次名义路线和两份几何代码。"""
    global ADAPTER
    ADAPTER = ReplayAdapter(peer)


def run_case(case):
    """工作进程接收一个场景的公共数据与严格保持轨迹。"""
    return ADAPTER.solve(*case)


def evaluate(names, vehicles, tracks):
    """不读求解分数：据两车同步观测区间重算 k、最终评分与双车采满。"""
    by_alert = defaultdict(list)
    for v, parts in zip(vehicles, tracks, strict=True):
        for phase in parts:
            if phase.kind == 'OBSERVING':
                by_alert[phase.alert].append((v, phase))
    alerts = {}
    for i, name in enumerate(names):
        seen = by_alert[i]
        if not seen:
            continue
        need(len(seen) == 2 and seen[0][0] != seen[1][0], (name, seen, 'unpaired'))
        (v, p), (w, q) = seen
        need(same(p.start, q.start) and 0 <= p.start < END, (name, seen, 'invalid start'))
        end = min(p.end, q.end, END)
        alerts[name] = (max(p.start, q.start), end, (v, w), (p.points[0], q.points[0]))
    n, k = len(names), len(alerts)
    k_full = sum(end-start >= 5 - TOL for start, end, *_ in alerts.values())
    return dict(N=n, k=k, eta=k/n, t_last_s=max(a[0] for a in alerts.values()) if k == n else END,
                k_full_5s=k_full), alerts


def csv_writer(stream, names):
    """输出字段顺序固定的 CSV，便于独立配对。"""
    writer = csv.DictWriter(stream, fieldnames=names.split(), lineterminator='\n')
    writer.writeheader()
    return writer


def sha256(path):
    """保存每份直接输入的 SHA-256，以辨别换初排或换源文件。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(path, peer, workers, field_run=Q4):
    """导出五种策略在共同主样本及保守断联对照中的完整记录。"""
    score_fields = 'N k eta t_last_s k_full_5s'
    key_fields = 'scenario_id protocol policy'
    score_path = field_run / 'model_comparison.csv.gz'
    if not score_path.is_file():
        # 原始复跑目录写普通 CSV；长期归档将同一份表 gzip，不改变评分口径。
        score_path = field_run / 'model_comparison.csv'
    prefixes = {(r['scenario_id'], r['variant']): r for r in json_rows(field_run / 'scenario_inputs.jsonl.gz')
                if r['variant'] in {'main', 'strict_outage'}}
    saved = {(r['scenario_id'], r['variant'], r['model']): r for r in csv_rows(score_path)
             if r['variant'] in {'main', 'strict_outage'} and r['model'] in BASE_MODELS}
    records = {(r['scenario_id'], r['variant'], r['model']): r for r in json_rows(field_run / 'plans_and_tracks.jsonl.gz')
               if r['variant'] in {'main', 'strict_outage'} and r['model'] in BASE_MODELS}
    counts = Counter(v for _, v in prefixes)
    need(counts == {'main': 1751, 'strict_outage': 1314} and records.keys() == saved.keys() and
         len(saved) == len(prefixes) * len(BASE_MODELS), 'archive coverage mismatch')
    adr = ReplayAdapter(peer)
    initial_hash = initial_schedule_hash(adr.archive)
    path.mkdir(parents=True, exist_ok=False)
    (path / 'nominal_q2.json').write_text(encode(dict(initial_schedule_hash=initial_hash, plan=adr.nominal_plan,
                                                     jobs=adr.nominal)), encoding='utf-8')
    summary, pairs = defaultdict(list), defaultdict(Counter)
    all_min = defaultdict(lambda: math.inf)
    with (path / 'scenario_manifest.csv').open('w', encoding='utf-8', newline='') as manifest_file, \
         (path / 'scenario_scores.csv').open('w', encoding='utf-8', newline='') as score_file, \
         gzip.open(path / 'alert_assignments.csv.gz', 'wt', encoding='utf-8', newline='') as alert_file, \
         gzip.open(path / 'vehicle_timelines.csv.gz', 'wt', encoding='utf-8', newline='') as track_file, \
         gzip.open(path / 'decisions.jsonl.gz', 'wt', encoding='utf-8') as decision_file, \
         ProcessPoolExecutor(max_workers=workers, initializer=start_worker, initargs=(peer,)) as pool:
        manifest_fields = 'scenario_id protocol category theta_deg R_m H_m failed_vehicle failure_probability t_rec_s outage_duration_s dispatch_s N initial_schedule_hash vehicle_state_snapshot_hash locked_before_dispatch'
        manifests = csv_writer(manifest_file, manifest_fields)
        scores = csv_writer(score_file, f'{key_fields} category {score_fields} action min_center_separation_m')
        alerts_csv = csv_writer(alert_file, f'{key_fields} alert status vehicle_1 station_1_x_m station_1_y_m '
                                'vehicle_2 station_2_x_m station_2_y_m start_s joint_end_in_window_s joint_duration_in_window_s full_5s')
        tracks_csv = csv_writer(track_file, f'{key_fields} vehicle phase_index phase alert start_s end_s '
                                'start_x_m start_y_m end_x_m end_y_m pan_start_deg tilt_start_deg pan_end_deg tilt_end_deg waypoints_xy_m')
        keys = list(prefixes)
        cases = ((prefixes[key], records[*key, 'strict_frozen']) for key in keys)
        for i, (key, local) in enumerate(zip(keys, pool.map(run_case, cases, chunksize=8), strict=True), 1):
            meta = prefixes[key]
            sc, protocol, cat, names = meta['scenario_id'], meta['variant'], meta['category'], meta['names']
            need(meta['initial_schedule_hash'] == initial_hash, (key, 'initial schedule hash'))
            common = dict(scenario_id=sc, protocol=protocol)
            manifests.writerow({**common, **{k: meta[k] for k in manifest_fields.split()[2:-1]},
                                'locked_before_dispatch': encode(meta['locked_at_dispatch'])})
            class_scores = {}
            for model in MODELS:
                entry = local if model == 'local_region' else records[*key, model]
                named = {**common, 'policy': LABEL[model]}
                tracks = entry['tracks'] if model == 'local_region' else tuple(tuple(Phase.from_record(p) for p in row)
                                                                              for row in entry['tracks'])
                metric, starts = evaluate(names, adr.vehicles, tracks)
                if model == 'local_region':
                    ref = entry['native_score']
                    action, distance, selected = entry['decision'], entry['min_vehicle_distance_m'], entry['selected']
                    check_data = {k: entry[k] for k in ('search_reason', 'candidate_k', 'candidate_t_last_s', 'checked_counts')}
                else:
                    ref = saved[*key, model]
                    need(ref['valid'] == 'True' and metric['k_full_5s'] == int(ref['k_full_observation']), (key, model, 'archive validity / data duration'))
                    action = ('replace_pending_queues' if entry['selected'] else 'retain_original') if entry['strategy'] == 'dynamic' else (
                        'append_new_alert' if entry['selected'] else 'retain_original')
                    distance = float(ref['continuous_min_vehicle_distance_m'])
                    selected, check_data = [], {}
                    for p in entry['selected']:
                        a = str(p['alert'])
                        selected.append(dict(alert=names[p['alert']], legs=[dict(vehicle=adr.vehicles[p[v]], station=site)
                                             for v, site in zip(('first', 'second'), meta['pairs'][a][p['pair']], strict=True)]))
                need(metric['k'] == int(ref['k']) and same(metric['t_last_s'], float(ref['t_last_s'])), (key, model, ref, metric))
                scores.writerow({**named, 'category': cat, **metric, 'action': action, 'min_center_separation_m': distance})
                class_scores[model] = metric
                summary[protocol, cat, LABEL[model]].append(metric)
                summary[protocol, 'All', LABEL[model]].append(metric)
                all_min[LABEL[model]] = min(all_min[LABEL[model]], distance)
                decision_file.write(encode({**named, **metric, 'dispatch_s': meta['dispatch_s'], 'action': action,
                                            'locked_alerts': meta['locked_at_dispatch'], 'selected_in_processing_order': selected,
                                            **check_data}) + '\n')
                for a, name in enumerate(names):
                    row = dict(**named, alert=name, status='unstarted')
                    if name in starts:
                        s, e, (v, w), ((x, y), (z, h)) = starts[name]
                        row.update(status='locked' if name in meta['locked_at_dispatch'] else 'started',
                                   vehicle_1=v, station_1_x_m=x, station_1_y_m=y,
                                   vehicle_2=w, station_2_x_m=z, station_2_y_m=h, start_s=s,
                                   joint_end_in_window_s=e, joint_duration_in_window_s=e-s, full_5s=int(e-s >= 5-TOL))
                    elif name != 'T4' and action in {'retain_original', 'append_new_alert'}:
                        legs = adr.nominal[a]['legs']
                        for j, leg in enumerate(legs, 1):
                            row.update({f'vehicle_{j}': leg['vehicle'], f'station_{j}_x_m': leg['station'][0],
                                        f'station_{j}_y_m': leg['station'][1]})
                        row['status'] = 'unstarted_original_assignment'
                    alerts_csv.writerow(row)
                for v, parts in zip(adr.vehicles, tracks, strict=True):
                    need(parts and same(parts[0].start, 0) and parts[-1].end >= END, (key, model, v, 'incomplete trace'))
                    need(all(same(a.end, b.start) for a, b in zip(parts, parts[1:])), (key, model, v, 'timeline gap'))
                    for j, p in enumerate(parts):
                        tracks_csv.writerow(dict(**named, vehicle=v, phase_index=j, phase=p.kind,
                                                 alert=names[p.alert] if p.alert is not None else None,
                                                 start_s=p.start, end_s=p.end, start_x_m=p.points[0][0], start_y_m=p.points[0][1],
                                                 end_x_m=p.points[-1][0], end_y_m=p.points[-1][1],
                                                 pan_start_deg=p.pose0[0], tilt_start_deg=p.pose0[1],
                                                 pan_end_deg=p.pose1[0], tilt_end_deg=p.pose1[1], waypoints_xy_m=encode(p.points)))
            for a, b in (('local_region','strict_frozen'), ('recommended','strict_frozen'), ('local_region','original_append'),
                         ('recommended','original_append'), ('recommended','local_region'), ('recommended','fixed_stations')):
                what = comparison(class_scores[a], class_scores[b])
                pairs[protocol, cat, LABEL[a], LABEL[b]][what] += 1
                pairs[protocol, 'All', LABEL[a], LABEL[b]][what] += 1
            if i % 400 == 0 or i == len(keys):
                print(f'Q4 paired evidence: {i}/{len(keys)}', flush=True)

    with (path / 'category_summary.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv_writer(stream, 'protocol category policy cases all_started all_full_5s mean_eta mean_t_last_s max_t_last_s')
        for (p, c, policy), rows in sorted(summary.items()):
            writer.writerow(dict(protocol=p, category=c, policy=policy, cases=len(rows),
                                 all_started=sum(r['k'] == r['N'] for r in rows), all_full_5s=sum(r['k_full_5s'] == r['N'] for r in rows),
                                 mean_eta=mean(r['eta'] for r in rows), mean_t_last_s=mean(r['t_last_s'] for r in rows),
                                 max_t_last_s=max(r['t_last_s'] for r in rows)))
    with (path / 'paired_summary.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv_writer(stream, 'protocol category policy reference higher_k earlier tie later lower_k')
        for (p, c, a, b), counts_row in sorted(pairs.items()):
            writer.writerow(dict(protocol=p, category=c, policy=a, reference=b, **{k: counts_row[k] for k in ('higher_k','earlier','tie','later','lower_k')}))
    def revision(p):
        """支持去掉 Git 元数据的支撑材料；不把解压到的外层仓库误记成源码版本。"""
        if not (p / '.git').exists():
            return None
        r = subprocess.run(['git', '-C', str(p), 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True)
        return r.stdout.strip()

    (path / 'verification.json').write_text(json.dumps(dict(
        scenario_protocol_counts=counts, policy_records=len(prefixes)*len(MODELS), local_independent_audits=len(prefixes),
        local_native_main_parity=counts['main'], five_vehicle_timelines=len(prefixes)*len(MODELS)*5,
        field_archive_score_and_5s_parity=len(saved), policies=LABEL, min_center_separation_m=dict(all_min),
        q2_initial_latest_s=max(adr.archive.starts), initial_schedule_hash=initial_hash, strict_before_s=END,
        required_wall_clearance_m=WALL, minimum_intersection_gap_deg=ANGLE, score_tie_tolerance_s=TOL,
        project_head=revision(ROOT), peer_head=revision(peer), python=sys.version, command=sys.argv,
        input_sha256={p.name: sha256(p) for p in (field_run/'scenario_inputs.jsonl.gz', field_run/'plans_and_tracks.jsonl.gz', score_path,
                                                 Q2/'q22_routes.json', Q2/'q22_timeline.csv', peer/'configs/b/q4-reschedule.json')},
        adapter_sha256=sha256(Path(__file__))), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(path, flush=True)


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--peer', type=Path, default=PEER)
    parser.add_argument('--field-run', type=Path, default=Q4, help='毫米初排的全场搜索输出或归档目录')
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args()
    main(args.output, args.peer.resolve(), args.workers, field_run=args.field_run.resolve())
