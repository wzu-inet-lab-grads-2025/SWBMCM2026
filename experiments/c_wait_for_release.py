"""Compare two fixed synthetic S1 action sequences with identical jobs and fees.

AI-assisted hand-accounting check, OpenCode / OpenAI GPT family, GPT-6.2 Astra
routing group, 2026-11-20. Exact release date unavailable.
"""

from math import isclose

from .c_q3_review_slack_check import execute, public_inputs
from .q3_replay.engine import Source, Trial, fixed_actions, run_trial


def main():
    rules, jobs, zones, case = public_inputs()
    a, b = 'S1_J01', 'S1_J05'
    for j, release, due in [(a, 0, 35), (b, 20, 55)]:
        assert (jobs[j]['release_s'], jobs[j]['due_s'], jobs[j]['zone_id']) == (release, due, 'Z4')
        assert jobs[j]['priority_weight'] == 3 and not jobs[j]['predecessors']
    other_tardy = sum(v['priority_weight']*(case['horizon_s']-v['due_s']) for k,v in jobs.items() if k not in (a,b))
    for p in (24, 54):
        sources = {j: Source('synthetic', (p if j == a else 2) - .5, [{'timestamp_s': 0}]) for j in jobs}
        for wait in (False, True):
            commands = ([{'action': 'WAIT', 'seconds': 20}, execute(b, 2), execute(a, 2)]
                        if wait else [execute(a, 2), execute(b, 2)])
            log = run_trial(Trial(jobs, sources, zones, case, rules), fixed_actions(commands+[{'action':'STOP'}]))
            ca, cb = (25+p, 24.5) if wait else (2.5+p, 5+p)
            m = log['metrics']
            assert m['valid_protocol'] and m['observations'] == 0 and isclose(m['cost'], 4.8)
            assert m['weighted_completion_rate'] == 6/16 and m['elapsed_s'] == max(ca, cb)
            assert m['weighted_on_time_rate'] == 3*((ca <= 35)+(cb <= 55))/16
            assert m['weighted_tardiness_s'] == other_tardy + 3*(max(0,ca-35) + max(0,cb-55))
            print(p, 'wait_then_BA' if wait else 'direct_AB', ca, cb, m['weighted_on_time_rate'], m['weighted_tardiness_s'])


if __name__ == '__main__':
    main()
