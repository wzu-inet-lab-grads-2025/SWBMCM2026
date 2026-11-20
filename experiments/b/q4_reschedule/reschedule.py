#!/usr/bin/env python3
"""run(scenario) -> one common-contract result. CLI accepts a row or a manifest."""
from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
from functools import lru_cache
import itertools
import json
import math
from pathlib import Path
import sys

if __package__ in ('', None):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from experiments.b.q4_retention import retention as original
from experiments.b.q4_reschedule import geometry as g

CUT = g.SCENE['q4']['disturbance_s']
END = g.SCENE['q4']['window_s'][1]
WIDTH = 36


@lru_cache(maxsize=6)
def old_program(failed):
    """Forecast the preloaded commands from *known* information, without an outage clock."""
    return original.run(dict(id='original', failed_vehicle=failed, new_alert=None, recovery_s=None))


def event(v, t, kind, t0, t1, p, a, b, cmd, **fields):
    return dict(vehicle=v, target=t, kind=kind, t0=t0, t1=t1, position0_xy_m=p, position1_xy_m=p,
                gimbal0_deg=a, gimbal1_deg=b, command_effective_s=cmd, command_issued_s=cmd, **fields)


def clip(e, now):
    """Physical left prefix, using arc length on the road and axis speed limits at rest."""
    if e['t1'] <= now:
        return dict(e)
    x = dict(e, t1=now, interrupted=True)
    if e['kind'] == 'drive':
        p, path = original.interpolate_path(e['path'], g.SPEED * (now - e['t0']))
        x.update(path=path, position1_xy_m=p, distance_m=original.path_length(path))
    elif e['kind'] in ('slew', 'reset'):
        x['gimbal1_deg'] = g.axes(e['gimbal0_deg'], e['gimbal_goal_deg'], now - e['t0'])
    return x


@dataclass(frozen=True)
class State:
    p: tuple
    angle: tuple
    free: float
    target: str | None = None   # cancellable old task, eligible to continue aiming in place


@dataclass(frozen=True)
class Leg:
    v: str
    t: str
    state: State
    p: tuple
    goal: tuple
    keep_aim: bool
    depart: float
    arrival: float
    ready: float


@dataclass(frozen=True)
class Assignment:
    t: str
    start: float
    legs: tuple[Leg, Leg]


def physical_snapshot(old, now):
    # Every started job owns both physical vehicles until their individual resets end.
    states = original.state_at(old, now)
    occupied = {}
    origin = {}
    for v, s in states.items():
        if not s['active']:
            continue
        p = tuple(s['position_xy_m'])
        if s['phase'] in ('observe', 'reset'):
            occupied[v] = s['release_after_reset_s']
            origin[v] = State(p, g.ZERO, occupied[v])
        else:
            origin[v] = State(p, tuple(s['gimbal_deg']), now, s['target'])
    prefix = []
    for e in old['events']:
        until = occupied.get(e['vehicle'], now)
        if e['t0'] < until or (e['kind'] == 'fail' and e['t0'] == until):
            prefix.append(clip(e, until))
    locked = {t: dict(j) for t, j in old['jobs'].items() if j['start_s'] is not None and j['start_s'] <= now}
    return states, origin, prefix, locked


def search(origin, pending, targets, latest_locked):
    pools = {v: g.stations(v, s.p) for v, s in origin.items()}

    @lru_cache(None)
    def options(v, s, t):
        choices = []
        for p in pools[v]:
            if not g.visible(p, targets[t]) or not g.road(s.p, p):
                continue
            keep = s.target == t and p == s.p
            depart = s.free + (0 if keep else g.turn(s.angle))
            arrival = depart + math.dist(s.p, p) / g.SPEED
            goal = g.aim(p, targets[t])
            ready = arrival + g.turn(s.angle if keep else g.ZERO, goal)
            if ready <= END:
                choices.append(Leg(v, t, s, p, goal, keep, depart, arrival, ready))
        return sorted(choices, key=lambda l: (l.ready, l.depart, l.p))

    def best_pair(v, w, states, t):
        # Individual earliest choices also minimize the joint start if geometrically compatible.
        a, b = options(v, states[v], t), options(w, states[w], t)
        if not a or not b:
            return None
        if g.pair_ok(a[0].p, b[0].p, targets[t]):
            return a[0], b[0]
        return min(((x, y) for x in a for y in b if g.pair_ok(x.p, y.p, targets[t])),
                   key=lambda pair: (max(l.ready for l in pair), sum(l.ready for l in pair)), default=None)

    frontier = [((), origin, tuple(pending), latest_locked)]
    best = ()
    for _ in pending:
        branches = []
        for jobs, states, remaining, latest in frontier:
            for t in remaining:
                for v, w in itertools.combinations(states, 2):
                    legs = best_pair(v, w, states, t)
                    if legs is None:
                        continue
                    start = max(l.ready for l in legs)
                    after = states.copy()
                    for l in legs:
                        after[l.v] = State(l.p, g.ZERO, start + g.DURATION + g.turn(l.goal))
                    assignments = (*jobs, Assignment(t, start, legs))
                    left = tuple(target for target in remaining if target != t)
                    branches.append((assignments, after, left, max(latest, start)))
        if not branches:
            break
        branches.sort(key=lambda n: (n[3], sum(s.free for s in n[1].values())))
        frontier = []
        seen = set()
        for node in branches:
            key = (node[2], tuple(node[1].items()))
            if key not in seen:
                frontier.append(node)
                seen.add(key)
                if len(frontier) == WIDTH:
                    break
        best = frontier[0][0]
    return best


def score(jobs):
    starts = [j['start_s'] for j in jobs.values() if j['start_s'] is not None and 0 <= j['start_s'] <= END]
    return len(starts), (max(starts) if len(starts) == len(jobs) else END)


def reset_cancel(v, state, cmd):
    return event(v, state.target, 'reset', state.free, state.free + g.turn(state.angle), state.p,
                 state.angle, g.ZERO, cmd, station=state.p, gimbal_goal_deg=g.ZERO,
                 reason='cancel_unstarted_task', planned_end_s=state.free + g.turn(state.angle))


def append_leg(events, leg, start, cmd):
    v, t, s, p, goal = leg.v, leg.t, leg.state, leg.p, leg.goal
    if leg.depart > s.free:
        events.append(reset_cancel(v, s, cmd))
    path = [s.p, p]
    if not leg.keep_aim:
        d = event(v, t, 'drive', leg.depart, leg.arrival, s.p, g.ZERO, g.ZERO, cmd,
                  path=path, station=p, speed_m_s=g.SPEED, distance_m=math.dist(*path), planned_end_s=leg.arrival)
        d['position1_xy_m'] = p
        events.append(d)
    if leg.ready > leg.arrival:
        events.append(event(v, t, 'slew', leg.arrival, leg.ready, p, s.angle if leg.keep_aim else g.ZERO, goal, cmd,
                            station=p, gimbal_goal_deg=goal, planned_end_s=leg.ready))
    if start > leg.ready:
        events.append(event(v, t, 'wait', leg.ready, start, p, goal, goal, cmd, station=p))
    end = start + g.DURATION
    events.append(event(v, t, 'observe', start, end, p, goal, goal, cmd, station=p, joint_start_s=start, joint_end_s=end))
    free = end + g.turn(goal)
    events.append(event(v, t, 'reset', end, free, p, goal, g.ZERO, cmd, station=p, gimbal_goal_deg=g.ZERO, planned_end_s=free))
    return free


def materialize(assignments, origin, events, locked, now, original_commands, jobs):
    commands = list(original_commands)
    last = dict(origin)
    used = set()
    for task in assignments:
        pair = [l.v for l in task.legs]
        jobs[task.t] = dict(start_s=task.start, vehicles=pair, stations={l.v: l.p for l in task.legs},
                            observation_end_s=task.start + g.DURATION, locked_at_5s=False)
        for leg in task.legs:
            free = append_leg(events, leg, task.start, now)
            last[leg.v] = State(leg.p, g.ZERO, free)
            used.add(leg.v)
            commands.append(dict(vehicle=leg.v, target=task.t, source='reschedule', issued_s=now, effective_s=now,
                                 station=leg.p, path=[leg.state.p, leg.p], continue_aim=leg.keep_aim,
                                 planned_departure_s=leg.depart, joint_start_s=task.start))
    for v, s in last.items():
        if v not in used:
            commands.append(dict(vehicle=v, target=s.target, source='reschedule', action='clear_pending_queue',
                                 issued_s=now, effective_s=now))
            if g.turn(s.angle) > 0:
                canceled = reset_cancel(v, s, now)
                events.append(canceled)
                last[v] = State(s.p, g.ZERO, canceled['t1'])
    horizon = max(END, *(s.free for s in last.values()))
    for v, s in last.items():
        if s.free < horizon:
            events.append(event(v, None, 'idle', s.free, horizon, s.p, g.ZERO, g.ZERO, now))
    jobs.update(locked)
    return jobs, sorted(events, key=lambda e: (e['t0'], e['vehicle'])), commands


def decide(now, announced_alert, known_failure):
    """At a connected clock tick only. No future recovery value / scenario id in this API."""
    old = old_program(known_failure)
    states, origin, prefix, locked = physical_snapshot(old, now)
    targets = dict(g.TARGETS)
    if announced_alert is not None:
        targets['T4'] = g.project(announced_alert)
    old_jobs = dict(old['jobs'])
    if 'T4' in targets:
        old_jobs['T4'] = dict(start_s=None, vehicles=[], stations={})
    pending = [t for t in targets if t not in locked]
    assignments = search(origin, pending, targets, max((j['start_s'] for j in locked.values()), default=0))
    candidate = {t: dict(start_s=None, vehicles=[], stations={}) for t in pending}
    partial = dict(candidate, **locked)
    partial.update({job.t: dict(start_s=job.start) for job in assignments})
    k, last = score(partial)
    old_k, old_last = score(old_jobs)
    diagnostic = dict(at_s=now, states=states, started_targets=list(locked), projected_keep=[old_k, old_last],
                      projected_replan=[k, last], mode='keep_preloaded')
    # Ties keep the existing queue. Small numerical noise cannot trigger spurious new commands.
    if k < old_k or (k == old_k and last >= old_last - 1e-9):
        return old_jobs, old['events'], old['commands'], diagnostic
    diagnostic['mode'] = 'replace_pending_queues'
    jobs, events, commands = materialize(assignments, origin, prefix, locked, now, old['commands'], candidate)
    return jobs, events, commands, diagnostic


def run(scenario):
    """The experiment clock delivers disturbances and restoration; the planner only sees current facts."""
    row = original.normalize_scenario(scenario)
    initial = old_program(None)
    jobs, events, commands = initial['jobs'], initial['events'], initial['commands']
    alert, failure, recovery = row['new_alert'], row['failed_vehicle'], row['recovery_s']
    diagnostic = dict(at_s=None, mode='no_disturbance')
    system = []
    if alert is not None:
        system.append(dict(t=CUT, kind='new_alert', target='T4', polar=alert))
    if failure is not None:
        system.append(dict(t=CUT, kind='vehicle_failure', vehicle=failure))
    if recovery is not None:
        system.append(dict(t=CUT, kind='communication_lost'))
        if recovery <= END:
            system.append(dict(t=recovery, kind='communication_recovered'))
    if alert is not None or failure is not None or recovery is not None:
        if recovery is None or recovery <= END:
            jobs, events, commands, diagnostic = decide(CUT if recovery is None else recovery, alert, failure)
        else:
            before = old_program(failure)
            jobs, events, commands = dict(before['jobs']), before['events'], before['commands']
            if alert is not None:
                jobs['T4'] = dict(start_s=None, vehicles=[], stations={})
            diagnostic = dict(at_s=None, mode='outage_through_window')
    k, last = score(jobs)
    # Return independent JSON-ready records even though the neutral propagation is cached.
    events = copy.deepcopy(events)
    for e in events:
        if e['t1'] < e.get('planned_end_s', e['t1']) - 1e-10:
            e['interrupted'] = True
    horizon = max(e['t1'] for e in events)
    return dict(scenario_id=row['id'], policy='reschedule', scenario=row, N=len(jobs), k=k, eta=k / len(jobs),
                t_last_s=last, jobs=copy.deepcopy(jobs), events=events, commands=copy.deepcopy(commands),
                 decision=diagnostic, system_events=system, dispatch_mode='preloaded', timeline_end_s=horizon,
                 rotation_model='independent_rate_limited_axes',
                trace_complete_until_s=horizon, sources=dict(initial['sources']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', nargs='?', help='JSON row, list, manifest or JSONL file (- for stdin)')
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--output', '-o', type=Path, help='JSON Lines output, default stdout')
    args = parser.parse_args()
    if bool(args.input) == bool(args.manifest):
        parser.error('provide either an input file or --manifest')
    source = args.input or args.manifest
    rows = original.read_scenarios(sys.stdin.read() if source == '-' else Path(source).read_text())
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
    stream = args.output.open('w') if args.output else sys.stdout
    try:
        for row in rows:
            print(json.dumps(run(row), ensure_ascii=False, allow_nan=False, separators=(',', ':')), file=stream)
    finally:
        if args.output:
            stream.close()


if __name__ == '__main__':
    main()
