"""Run as python -m research.q3_replay.acceptance pair|smoke [--output PATH]."""
import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from research.c_q3_fixture import pair_test as baseline
from .data import validation_trial
from .engine import Source, Trial, fixed_actions, run_trial


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
    """Recompute accepted actions and all scores from public conditions + observed feedback."""
    initial = log['calls'][0]['input']
    jobs, case, zones = initial['jobs'], initial['case'], initial['zones']
    horizon = case['horizon_s']
    t, cost, reviews, idle, overrun, censored = 0, 0, 0, 0, 0, 0
    zone, finishes = initial['zone'], {}
    assert len(log['calls']) == len(log['ledger'])
    for call,e in zip(log['calls'],log['ledger']):
        a, v = call['action'], call['input']
        assert e['action'] == a['action'] and t == call['time'] == v['time'] == e['time']
        assert zone == e['zone_before'] == v['zone'] and v['completed'] == finishes
        assert math.isclose(case['cost_budget']-cost, v['budget_left'])
        assert case['observation_limit']-reviews == v['reviews_left']
        if a['action'] == 'REVIEW':
            assert a['job_id']==e['job_id'] and e['fee']==1
            reviews += 1
            cost += 1
            t += 1
        elif a['action'] == 'WAIT':
            t += a['seconds']
            assert e['fee']==0
        elif a['action'] == 'EXECUTE':
            j, b = a['job_id'], a['b_s']
            assert (j,b) == (e['job_id'],e['b'])
            dest = jobs[j]['zone_id']
            distance = math.dist(zones[zone], zones[dest])
            start = t+distance+.5
            fee = 2+.1*b+.2*distance
            assert math.isclose(e['fee'], fee) and e['start'] == start
            cost += fee
            c = e['completion']
            assert e['censored'] == (c is None)
            if c is None:
                t = horizon
                censored += 1
            else:
                finishes[j] = c
                t = max(c,start+b)
            observed = (horizon if c is None else c)-start
            this_idle = 0 if c is None else max(0,b-observed)
            this_overrun = max(0,observed-b)
            assert math.isclose(this_idle,e['idle'],abs_tol=1e-10)
            assert math.isclose(this_overrun,e['overrun'],abs_tol=1e-10)
            idle += this_idle
            overrun += this_overrun
            zone = dest
        else:
            assert a['action']=='STOP' and e['fee']==0
        assert math.isclose(t,e['end']) and math.isclose(cost,e['cost_after']) and zone == e['zone_after']
    W = sum(j['priority_weight'] for j in jobs.values())
    expect = dict(elapsed_s=t, cost=cost, observations=reviews, reserved_idle_s=idle,
                  observed_overrun_s=overrun, censored_units=censored, valid_protocol=True, invalid_actions=0,
                  weighted_completion_rate=sum(jobs[j]['priority_weight'] for j in finishes)/W,
                  weighted_on_time_rate=sum(jobs[j]['priority_weight'] for j,c in finishes.items() if c<=jobs[j]['due_s'])/W,
                  weighted_tardiness_s=sum(v['priority_weight']*max(0,finishes.get(j,horizon)-v['due_s']) for j,v in jobs.items()))
    assert set(expect)==set(log['metrics'])
    for k,v in expect.items():
        assert math.isclose(v,log['metrics'][k],abs_tol=1e-10), (k,v,log['metrics'][k])


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
