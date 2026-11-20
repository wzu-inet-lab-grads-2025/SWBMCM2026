#!/usr/bin/env python3
"""Re-audit saved JSONL against external inputs. Rebuild compact evidence and the paired CSV."""
from collections import Counter, defaultdict
from functools import lru_cache
import csv
import hashlib
import itertools
import json
import math
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from experiments.b.q4_audit import audit
from experiments.b.q4_retention import retention
from experiments.b.q4_reschedule import geometry as g, reschedule as solver

OUT = ROOT / 'runs/b/q4_reschedule'
MANIFESTS = ROOT / 'experiments/b/q4_scenarios'
EXAMPLES = (
    'main_a-none_f-none_c-none', 'main_a-030-30_f-none_c-none', 'main_a-none_f-V3_c-none',
    'main_a-none_f-none_c-10', 'main_a-270-50_f-V3_c-none', 'main_a-150-50_f-none_c-30',
    'main_a-none_f-V1_c-10', 'main_a-150-50_f-V3_c-30', 'main_a-none_f-V5_c-none',
    'main_a-030-30_f-V3_c-none', 'main_a-030-30_f-V3_c-10')


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def rows(p):
    return [json.loads(line) for line in p.read_text().splitlines() if line]


def tags(s):
    return ''.join(c for c, k in [('A', 'new_alert'), ('F', 'failed_vehicle'), ('C', 'recovery_s')]
                   if s[k] is not None) or 'none'


def public_prefix(record, cut):
    """Ordered, left-open public trace, independent of any event end that lies in the future."""
    def vec(p):
        return tuple(round(x, 8) for x in p)
    past = []
    for e in record['events']:
        if e['t0'] >= cut:
            continue
        start, end = e['t0'], min(e['t1'], cut)
        if e['kind'] == 'fail':
            past.append((e['vehicle'], 'fail', start, vec(e['position_xy_m']), vec(e['gimbal_deg'])))
            continue
        position, angle = e['position1_xy_m'], e['gimbal1_deg']
        path = e.get('path', [])
        if end < e['t1']:
            if e['kind'] == 'drive':
                length = audit.path_length(path)
                distance = g.SPEED * (end - start)
                position = audit.path_at(path, distance / length if length else 0)
                travelled, prefix = 0., [path[0]]
                for a, b in zip(path, path[1:]):
                    travelled += math.dist(a, b)
                    if travelled >= distance:
                        break
                    prefix.append(b)
                path = prefix + [position]
            elif e['kind'] in ('slew', 'reset'):
                angle = retention.move_axes(e['gimbal0_deg'], e['gimbal_goal_deg'], end - start)
        past.append((e['vehicle'], e['kind'], e['target'], round(start, 8), round(end, 8),
                     vec(e['position0_xy_m']), vec(position), vec(e['gimbal0_deg']), vec(angle),
                     tuple(vec(p) for p in path), e.get('command_effective_s')))
    cmds = [(c['vehicle'], c['target'], c['effective_s'], c.get('station'), c.get('path'))
            for c in record['commands'] if c['effective_s'] < cut]
    return past, cmds


def check_areas(record):
    # Each whole segment stays in its convex private rectangle. Separation between
    # rectangles certifies simultaneous separation for drives, waits and resets alike.
    for e in record['events']:
        points = e.get('path') or [e.get('position0_xy_m', e.get('position_xy_m')), e.get('position1_xy_m', e.get('position_xy_m'))]
        assert all(g.in_area(e['vehicle'], p) for p in points), (record['scenario_id'], e)
    area_gap = min(math.hypot(max(a[0]-b[2], b[0]-a[2], 0), max(a[1]-b[3], b[1]-a[3], 0))
                   for a,b in itertools.combinations(g.AREAS.values(), 2))
    assert area_gap >= 2 * g.RADIUS
    return area_gap


def check_commands(record, scenario):
    usable = scenario['recovery_s'] if scenario['recovery_s'] is not None else 5
    reject = [c for c in record['commands'] if c['source'] == 'reschedule' and
              (c['effective_s'] != usable or c['issued_s'] != usable or c['vehicle'] == scenario['failed_vehicle'])]
    assert not reject, reject
    canceled = []
    for e in record['events']:
        if e.get('reason') == 'cancel_unstarted_task':
            expected = max(map(abs, e['gimbal0_deg'])) / 30
            assert abs(e['t1'] - e['t0'] - expected) <= 1e-8 and max(map(abs, e['gimbal1_deg'])) == 0
            canceled.append(e)
    return len(canceled)


def boundary_probes():
    # Actual two visible, already aimed, healthy stations. Earliest joint start is exactly 60.
    jobs = {v: solver.State(g.HOME[v], g.aim(g.HOME[v], g.TARGETS['T2']), 60., 'T2') for v in ['V3', 'V5']}
    plan = solver.search(jobs, ['T2'], g.TARGETS, 0)
    assert len(plan) == 1 and plan[0].start == 60.0
    too_late = {v: solver.State(s.p, s.angle, math.nextafter(60., math.inf), s.target) for v, s in jobs.items()}
    assert not solver.search(too_late, ['T2'], g.TARGETS, 0)
    assert solver.score({'T2': {'start_s': 60.}}) == (1, 60.)
    assert solver.score({'T2': {'start_s': 59.}, 'T4': {'start_s': None}}) == (1, 60.)


@lru_cache(maxsize=6)
def keep(failed):
    return retention.run({'id': 'keep', 'failed_vehicle': failed, 'new_alert': None, 'recovery_s': None})


def main():
    boundary_probes()
    ctx = audit.Context.default()
    panels = [('main', 'manifest.json'), ('sensitivity', 'sensitivity.json')]
    cohorts = defaultdict(list)
    counts, summaries, detailed = {}, [], []
    extracts = {}
    inputs = [g.SCENE_PATH, g.SNAPSHOT, Path(retention.__file__), Path(audit.__file__), HERE/'geometry.py',
              HERE/'reschedule.py', HERE/'verify.py']
    for panel, manifest in panels:
        source = MANIFESTS / manifest
        inputs.append(source)
        cases = json.loads(source.read_text())['scenarios']
        logs = rows(OUT / (panel + '.jsonl'))
        by_id = {q['scenario_id']: q for q in logs}
        assert len(logs) == len(by_id) == len(cases) and set(by_id) == {s['id'] for s in cases}
        stats = Counter()
        for s in cases:
            q = by_id[s['id']]
            ext = dict(s, recovery_scope='sensitivity') if panel == 'sensitivity' else s
            report = audit.audit_record(q, ext, context=ctx)
            if not report['certified']:
                raise ValueError((s['id'], report['errors'], report['missing_evidence']))
            stats['independent_audits_pass'] += 1
            assert public_prefix(q, 5) == public_prefix(keep(None), 5), s
            stats['nominal_prefix_pass'] += 1
            baseline = keep(s['failed_vehicle'])
            if s['recovery_s'] is not None:
                assert public_prefix(q, s['recovery_s']) == public_prefix(baseline, s['recovery_s']), s
                cohorts[(tuple(s['new_alert'] or []), s['failed_vehicle'])].append((s['recovery_s'], q))
                stats['outage_prefix_pass'] += 1
            area_gap = check_areas(q)
            stats['cancellation_resets'] += check_commands(q, s)
            # Recount fixed-policy joint starts from raw events, including the lost seat for T4.
            started = defaultdict(list)
            for e in baseline['events']:
                if e['kind'] == 'observe':
                    started[e['target']].append((e['t0'], e['vehicle']))
            assert all(len(p) == 2 and p[0][0] == p[1][0] and p[0][1] != p[1][1] for p in started.values())
            k0 = len(started)
            last0 = max(p[0][0] for p in started.values()) if k0 == q['N'] else 60.
            first = (k0, -last0)
            second = (q['k'], -q['t_last_s'])
            comparison = 'win' if second > first else 'tie' if second == first else 'loss'
            stats[comparison] += 1
            stats['reschedule_full'] += q['k'] == q['N']
            r = dict(panel=panel, scenario_id=s['id'], factors=tags(s), N=q['N'], keep_k=k0,
                     keep_eta=k0/q['N'], keep_t_last_s=last0, reschedule_k=q['k'], reschedule_eta=q['eta'],
                     reschedule_t_last_s=q['t_last_s'], decision=q['decision']['mode'], comparison=comparison)
            summaries.append(r)
            if s['id'] in EXAMPLES:
                extracts[s['id']] = dict(r, jobs={t: [j['start_s'], j['vehicles']] for t,j in sorted(q['jobs'].items())})
                detailed.append(q)
        counts[panel] = dict(stats, latest_start_max_s=max(q['t_last_s'] for q in logs),
                             min_work_area_center_separation_m=area_gap,
                             trace_sha256=sha(OUT / (panel+'.jsonl')))
    pairs = 0
    for group in cohorts.values():
        group.sort(key=lambda item: item[0])
        for (now, a), (_, b) in zip(group, group[1:]):
            assert public_prefix(a, now) == public_prefix(b, now), (a['scenario_id'], b['scenario_id'])
            pairs += 1
    with (OUT/'comparison.csv').open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(summaries[0]))
        w.writeheader()
        w.writerows(summaries)
    with (OUT/'examples.jsonl').open('w') as f:
        for q in detailed:
            print(json.dumps(q, separators=(',', ':')), file=f)
    evidence = dict(panels=counts, same_factors_different_recovery_prefixes=pairs,
                    endpoint_checks=['joint start at 60 accepted', 'one ULP beyond 60 rejected', 'incomplete => 60 s'],
                    cases=[extracts[key] for key in EXAMPLES],
                    source_sha256={str(p.relative_to(ROOT)): sha(p) for p in inputs})
    (HERE/'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(dict(panels=counts, recovery_prefix_pairs=pairs), indent=2))


if __name__ == '__main__':
    main()
