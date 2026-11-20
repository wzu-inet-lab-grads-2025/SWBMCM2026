"""Run as python -m experiments.q3_replay.acceptance pair|smoke [--output PATH]."""
import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from experiments.c_q3_fixture import pair_test as baseline
from .data import validation_trial
from .engine import Source, Trial, fixed_actions, public_sample, run_trial


def packed(log, payloads):
    """Losslessly store repeated callback payloads once, after the trial ends."""
    log = deepcopy(log)
    for call in log['calls']:
        for observation in call['input']['observations'].values():
            rows = observation.pop('rows')
            raw = json.dumps(rows, sort_keys=True, separators=(',',':'), allow_nan=False).encode()
            key = hashlib.sha256(raw).hexdigest()
            payloads.setdefault(key, rows)
            observation['rows_sha256'] = key
    return log


def verify_export(path):
    document = json.loads(path.read_text())
    for run in document['runs'].values():
        for call in run['calls']:
            for observation in call['input']['observations'].values():
                key = observation.pop('rows_sha256')
                rows = document['payloads'][key]
                digest = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',',':'),
                                                   allow_nan=False).encode()).hexdigest()
                assert key == digest
                observation['rows'] = rows
        verify_ledger(run)


def witnessed(trial, actions):
    # This recorder runs inside the user-supplied callable, independently of engine logging.
    script, actual = fixed_actions(actions), []
    def policy(view):
        received = deepcopy(view)
        action = script(view)
        actual.append(dict(index=len(actual), time=view['time'], input=received, action=deepcopy(action)))
        return action
    log = run_trial(trial, policy)
    assert actual == log['calls'], 'callback trace differs from actual function entries'
    verify_ledger(log)
    return log


def verify_ledger(log):
    """Verify public admission, disclosure, settlement and scores independently.

    A correctly recorded terminal rejection is valid *audit evidence*, not a
    valid protocol run. Hidden durations and source/sample identity cannot be
    authenticated from this log alone; those still need external truth/witnesses.
    """
    if not __debug__:
        raise RuntimeError('verify_ledger requires assertions; run without -O')

    def numeric(value):
        return type(value) in (int, float) and math.isfinite(value)

    def same(actual, expected):
        assert numeric(actual) and math.isclose(actual, expected, rel_tol=0, abs_tol=1e-9), (actual, expected)

    calls, ledger = log['calls'], log['ledger']
    assert calls, 'an initial public snapshot is required'
    initial = calls[0]['input']
    jobs, case, zones, rules = (initial[k] for k in ('jobs', 'case', 'zones', 'rules'))
    horizon, budget, quota = (case[k] for k in ('horizon_s', 'cost_budget', 'observation_limit'))
    obs, fees, movement = (rules[k] for k in ('observation', 'fees', 'movement'))
    widths = obs['prefix_seconds']
    assert numeric(horizon) and horizon > 0 and numeric(budget) and budget >= 0
    assert type(quota) is int and quota >= 0
    assert numeric(movement['speed']) and movement['speed'] > 0
    assert numeric(movement['changeover_s']) and movement['changeover_s'] >= 0
    assert numeric(obs['delay_s']) and obs['delay_s'] > 0
    assert all(numeric(fees[k]) and fees[k] >= 0 for k in ('service_fixed', 'reserve_per_second', 'distance_per_unit'))
    assert numeric(obs['cost']) and obs['cost'] >= 0
    assert numeric(rules['reserve_min_s']) and rules['reserve_min_s'] > 0
    assert type(rules['max_actions']) is int and rules['max_actions'] > 0
    assert type(obs['max_per_job']) is int and 0 <= obs['max_per_job'] < len(widths)
    assert all(numeric(w) and w >= 0 for w in widths) and all(a < b for a,b in zip(widths, widths[1:]))
    assert numeric(initial['alpha']) and initial['alpha'] >= 0
    assert numeric(initial['beta']) and initial['beta'] >= 0
    assert jobs and initial['zone'] in zones
    for name, spec in jobs.items():
        assert spec['job_id'] == name and spec['zone_id'] in zones
        assert numeric(spec['release_s']) and spec['release_s'] >= 0
        assert numeric(spec['due_s']) and numeric(spec['priority_weight']) and spec['priority_weight'] > 0
        assert all(p in jobs for p in spec['predecessors'])
    public_keys = {'time','zone','budget_left','reviews_left','case','rules','zones',
                   'jobs','alpha','beta','completed','observations'}
    t, cost, reviews, idle, overrun, censored, invalid = 0, 0, 0, 0, 0, 0, 0
    zone, finishes, levels, disclosed = initial['zone'], {}, {j: 0 for j in jobs}, {}
    stopped = False
    assert len(ledger) <= len(calls) <= len(ledger)+1
    for index, call in enumerate(calls):
        a, v = call['action'], call['input']
        assert not stopped and 0 <= t < horizon, 'callback after termination/horizon'
        assert call['index'] == index and set(v) == public_keys
        assert t == call['time'] == v['time'] and zone == v['zone'] and v['completed'] == finishes
        for key in ('case', 'jobs', 'zones', 'rules', 'alpha', 'beta'):
            assert v[key] == initial[key], ('public conditions changed', key)
        same(v['budget_left'], budget-cost)
        assert 0 <= v['budget_left'] <= budget and v['reviews_left'] == quota-reviews
        assert 0 <= v['reviews_left'] <= quota
        released = {j for j,spec in jobs.items() if spec['release_s'] <= t}
        assert set(v['observations']) == released, 'unreleased/missing observation'
        for j, observation in v['observations'].items():
            assert set(observation) == {'prefix_s', 'rows'}
            width, rows = widths[levels[j]], observation['rows']
            assert observation['prefix_s'] == width and isinstance(rows, list), 'prefix progress'
            for row in rows:
                try:
                    projected = public_sample(row)
                except (ValueError, TypeError, KeyError) as exc:
                    raise AssertionError('malformed public sample') from exc
                assert projected == row, 'unallowlisted public sample field'
                assert 0 <= row['timestamp_s'] <= width, 'future/out-of-window sample'
            if j in disclosed:
                old_width, old_rows = disclosed[j]
                assert [r for r in rows if r['timestamp_s'] <= old_width] == old_rows, 'rewritten/missing disclosed prefix'
            disclosed[j] = (width, deepcopy(rows))

        # Re-derive admission from public conditions, NOT engine number/quote/require.
        reason, start, fee = None, None, 0
        if index+1 > rules['max_actions']:
            reason = 'action limit'
        elif not isinstance(a, dict):
            reason = 'action object'
        elif a.get('action') not in ('STOP', 'WAIT', 'REVIEW', 'EXECUTE'):
            reason = 'unknown action'
        elif a['action'] == 'WAIT':
            duration = a.get('seconds')
            if not numeric(duration):
                reason = 'seconds'
            elif not (duration > 0 and t+duration <= horizon):
                reason = 'wait duration'
        elif a['action'] in ('REVIEW', 'EXECUTE'):
            j = a.get('job_id')
            if not (isinstance(j, str) and j in released and j not in finishes):
                reason = 'job unavailable'
            elif a['action'] == 'REVIEW':
                fee = obs['cost']
                if not (levels[j] < obs['max_per_job'] and reviews < quota
                        and t+obs['delay_s'] <= horizon and cost+fee <= budget):
                    reason = 'review limit, budget or horizon'
            elif not all(p in finishes for p in jobs[j]['predecessors']):
                reason = 'predecessors'
            elif not numeric(a.get('b_s')):
                reason = 'b_s'
            else:
                b, dest = a['b_s'], jobs[j]['zone_id']
                distance = math.dist(zones[zone], zones[dest])
                start = t+distance/movement['speed']+movement['changeover_s']
                fee = fees['service_fixed']+fees['reserve_per_second']*b+fees['distance_per_unit']*distance
                if not (b >= rules['reserve_min_s'] and start+b <= horizon and cost+fee <= budget):
                    reason = 'reservation, horizon or budget'
        if reason is not None:
            assert index == len(calls)-1 == len(ledger), 'illegal action accepted/nonterminal rejection'
            assert call.get('rejected') == reason, 'incorrect rejection reason'
            invalid = 1
            break
        assert 'rejected' not in call and index < len(ledger), 'legal action wrongly rejected/missing row'
        e = ledger[index]
        assert e['action'] == a['action'] and e['time'] == t and e['zone_before'] == zone
        same(e['fee'], fee)
        if a['action'] == 'REVIEW':
            assert e['job_id'] == j
            levels[j] += 1
            reviews += 1
            t += obs['delay_s']
            assert e['prefix_returned_s'] == widths[levels[j]], 'noncumulative review prefix'
        elif a['action'] == 'WAIT':
            t += duration
        elif a['action'] == 'EXECUTE':
            assert (e['job_id'], e['b']) == (j, b)
            same(e['start'], start)
            c = e['completion']
            assert type(e['censored']) is bool and e['censored'] == (c is None)
            assert c is None or (numeric(c) and start <= c <= horizon), 'completion outside service/horizon'
            if c is None:
                t = horizon
                censored += 1
            else:
                finishes[j] = c
                t = max(c, start+b)
            observed = (horizon if c is None else c)-start
            this_idle = 0 if c is None else max(0, b-observed)
            this_overrun = max(0, observed-b)
            same(e['idle'], this_idle)
            same(e['overrun'], this_overrun)
            assert isinstance(e['source_ref_offline'], str) and e['source_ref_offline']
            same(e['source_ref_logged_at'], horizon if c is None else c)
            idle += this_idle
            overrun += this_overrun
            zone = dest
        else:
            stopped = True
        cost += fee
        assert 0 <= t <= horizon and 0 <= cost <= budget, 'time/budget bounds'
        same(e['end'], t)
        same(e['cost_after'], cost)
        assert e['time'] <= e['end'] <= horizon and 0 <= e['cost_after'] <= budget
        assert e['zone_after'] == zone
    assert invalid or stopped or t == horizon, 'truncated trial without terminal event'
    releases = [dict(job_id=j, time=spec['release_s'], prefix_s=widths[0])
                for j,spec in sorted(jobs.items(), key=lambda item: (item[1]['release_s'], item[0]))
                if spec['release_s'] <= t]
    assert log['releases'] == releases, 'release ledger mismatch'
    W = sum(j['priority_weight'] for j in jobs.values())
    expect = dict(elapsed_s=t, cost=cost, observations=reviews, reserved_idle_s=idle,
                  observed_overrun_s=overrun, censored_units=censored, valid_protocol=not invalid, invalid_actions=invalid,
                  weighted_completion_rate=sum(jobs[j]['priority_weight'] for j in finishes)/W,
                  weighted_on_time_rate=sum(jobs[j]['priority_weight'] for j,c in finishes.items() if c<=jobs[j]['due_s'])/W,
                  weighted_tardiness_s=sum(v['priority_weight']*max(0,finishes.get(j,horizon)-v['due_s']) for j,v in jobs.items()))
    assert set(expect) == set(log['metrics'])
    for k,v in expect.items():
        if isinstance(v, bool):
            assert type(log['metrics'][k]) is bool and log['metrics'][k] == v
        else:
            same(log['metrics'][k], v)


def pair_trials():
    _, rules, zones, _, _, _, sources = baseline.load_inputs()
    limits = dict(rules['cases'][0], jobs=2)
    jobs = {j['job_id']:j for j in baseline.JOBS}
    for version in sources:
        inputs = {}
        for j,s in version.items():
            # Same fixture data as 850380e, expressed in segment-relative seconds.
            rows = [dict(row,timestamp_s=round(row['timestamp_s']-s['annotation_start_s'],9))
                    for row in s['numeric_rows']]
            inputs[j] = Source(s['source_ref'],s['d_s'],rows)
        yield Trial(jobs,inputs,zones,limits,rules)


def check_pair():
    short, long = (witnessed(t,baseline.ACTIONS) for t in pair_trials())
    for log, (a_end,available,b_start,b_end,tardy) in zip((short,long),[(4.5,5.5,6,8,0),(7.5,7.5,8,10,6)]):
        a,b,stop = log['ledger']
        assert (a['start'],a['completion'],a['end'],b['time'],b['start'],b['completion'],stop['time']) == (
            1.5,a_end,available,available,b_start,b_end,b_end)
        assert (a['fee'],b['fee']) == (2.6,2.2)
        assert math.isclose(log['metrics']['cost'],4.8) and log['metrics']['weighted_tardiness_s']==tardy
        assert [c['time'] for c in log['calls']]==[0,available,b_end]
        for call in log['calls']:
            v = call['input']
            assert set(v)=={'time','zone','budget_left','reviews_left','case','rules','zones','jobs','alpha','beta','completed','observations'}
            assert not (baseline.keys(v) & (baseline.FORBIDDEN | {'actual_end','available_at','seed','p','d'}))
            assert all(o['prefix_s']==.2 and len(o['rows'])==3 for o in v['observations'].values())
    # First returned differing feedback under the blocking convention is at 5.5.
    before = lambda r: [c for c in r['calls'] if c['time'] < 5.5]
    assert len(before(short)) == 1 and before(short)==before(long)
    return {'short':short, 'long':long}


# Fixed I/O checks specified in public job IDs, independently of sampled durations.
SMOKE_ACTIONS = [{'action':'REVIEW','job_id':'S1_J01'}, {'action':'REVIEW','job_id':'S1_J01'},
                 {'action':'EXECUTE','job_id':'S1_J01','b_s':4}, {'action':'WAIT','seconds':40},
                 {'action':'EXECUTE','job_id':'S1_J07','b_s':3}, {'action':'STOP'}]


def smoke():
    trial = validation_trial()
    log = witnessed(trial,SMOKE_ACTIONS)
    calls = log['calls']
    for c in calls:
        view = c['input']
        obs = view['observations']
        assert set(obs)=={j for j,v in trial.jobs.items() if v['release_s']<=c['time']}
        for j,o in obs.items():
            assert o['rows'] and o['rows']==[r for r in trial.sources[j].rows if r['timestamp_s']<=o['prefix_s']]
            for r in o['rows']:
                assert set(r)=={'timestamp_s','right_hand','left_hand','head_pose','left_head_camera_pose',
                                'right_head_camera_pose','hand_quality_flags'}
                for hand in ('left_hand','right_hand'):
                    assert len(r[hand]['positions'])==len(r[hand]['orientations_xyzw'])==21
    assert [c['time'] for c in calls[:3]]==[0,1,2]
    windows = [c['input']['observations']['S1_J01'] for c in calls[:3]]
    assert [w['prefix_s'] for w in windows]==[.2,.6,1.]
    assert [len(w['rows']) for w in windows]==[6,18,30]  # previously audited witness
    assert len(log['releases'])==len(trial.jobs)==8
    log['offline_trial'] = dict(case='S1',split='validation',seed=2026111700,alpha=1,beta=.5,
                                assignments={j:s.ref for j,s in trial.sources.items()})
    return {'S1': log}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['pair','smoke','verify-log'])
    parser.add_argument('--output',type=Path,help='Full audit. Smoke contains real data: use a private local file.')
    parser.add_argument('--input',type=Path,help='Previously exported log for offline verification')
    args=parser.parse_args()
    if args.mode=='verify-log':
        if not args.input:
            parser.error('verify-log requires --input')
        verify_export(args.input)
        print('Verified saved callback payloads, equipment ledger, fees and all 11 metrics.')
        return
    results=check_pair() if args.mode=='pair' else smoke()
    if args.output:
        payloads={}
        report={'runs':{k:packed(v,payloads) for k,v in results.items()},'payloads':payloads}
        args.output.write_text(json.dumps(report,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n')
    for name,log in results.items():
        print(name, 'callback times', [c['time'] for c in log['calls']], 'metrics', json.dumps(log['metrics']))


if __name__=='__main__':
    main()
