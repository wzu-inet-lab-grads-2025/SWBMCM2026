#!/usr/bin/env python3
"""Independent, standard-library replay of the fixed q22 schedule.

Public interfaces: run(scenario, *, dispatch_mode='preloaded') -> dict;
state_at(result, t, side='right') -> dict of vehicle states;
interpolate_path(path, distance_m) -> (position, travelled_prefix).
No search/rescheduling code or other agents' implementations are imported.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import heapq
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT = ROOT / 'experiments/b/snapshots/q2_numerical/summary.json'
SCENE = ROOT / 'experiments/b/scene.json'
DISTURBANCE = 5.0
DEADLINE = 60.0


def path_length(path):
    return sum(math.dist(a, b) for a, b in zip(path, path[1:]))


def interpolate_path(path, distance_m):
    """Arc-length interpolation, including zero-length segments and vertices.

    The returned prefix ends at the actual current position, never at a future
    station. Outside the path's length clamp to its endpoints.
    """
    if not path:
        raise ValueError('path must contain at least one point')
    left = max(0.0, float(distance_m))
    prefix = [list(path[0])]
    for a, b in zip(path, path[1:]):
        length = math.dist(a, b)
        if length == 0:
            continue
        if left < length:
            p = [a[d] + (b[d] - a[d]) * left / length for d in range(2)]
            if p != prefix[-1]:
                prefix.append(p)
            return p, prefix
        prefix.append(list(b))
        left -= length
    return list(path[-1]), prefix


def move_axes(start, goal, dt, rate=30.0):
    return [a + math.copysign(min(abs(b - a), max(0.0, dt) * rate), b - a)
            for a, b in zip(start, goal)]


def pointing(station, polar, scene):
    theta, radius, altitude = polar
    cx, cy = scene['centre_xy_m']
    dx = cx + radius * math.cos(math.radians(theta)) - station[0]
    dy = cy + radius * math.sin(math.radians(theta)) - station[1]
    return [math.degrees(math.atan2(dy, dx)),
            math.degrees(math.atan2(altitude - scene['camera_height_m'], math.hypot(dx, dy)))]


def normalize_scenario(scenario):
    if not isinstance(scenario, dict):
        raise ValueError('scenario must be a JSON object')
    failed = scenario.get('failed_vehicle')
    if failed is not None and (not isinstance(failed, str) or failed not in {f'V{i}' for i in range(1, 6)}):
        raise ValueError('failed_vehicle must be null or V1..V5')
    recovery = scenario.get('recovery_s')
    if recovery is not None:
        if isinstance(recovery, bool) or not isinstance(recovery, (int, float)):
            raise ValueError('recovery_s must be a number or null')
        recovery = float(recovery)
        if not math.isfinite(recovery) or recovery <= DISTURBANCE:
            raise ValueError('recovery_s must be finite and strictly greater than 5')
    new = scenario.get('new_alert')
    if new is not None:
        if not isinstance(new, (list, tuple)) or len(new) != 3:
            raise ValueError('new_alert must be null or [theta_deg, range_m, altitude_m]')
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
               for x in new):
            raise ValueError('new_alert coordinates must be finite numbers')
        theta, radius, altitude = map(float, new)
        if not (0 <= theta < 360 and 30 <= radius <= 50 and altitude == 50):
            raise ValueError('new_alert requires theta in [0,360), R in [30,50], H=50')
        new = [theta, radius, altitude]
    return {'id': str(scenario.get('id', 'scenario')), 'new_alert': new,
            'failed_vehicle': failed, 'recovery_s': recovery}


class _Replay:
    def __init__(self, scenario, plan, scene, dispatch_mode):
        self.scenario = normalize_scenario(scenario)
        if dispatch_mode not in ('preloaded', 'current_only'):
            raise ValueError('dispatch_mode must be preloaded or current_only')
        self.mode, self.scene = dispatch_mode, scene
        self.speed, self.rate = scene['vehicle_speed_m_s'], scene['axis_speed_deg_s']
        self.duration = scene['joint_observation_s']
        self.events, self.commands, self.system_events = [], [], []
        self.heap, self.serial, self.now = [], 0, 0.0
        self.online = True
        self.tasks = {v: [] for v in scene['vehicles_xy_m']}
        self.jobs = {}
        for job in plan:
            target = job['target']
            self.jobs[target] = {'start_s': None, 'vehicles': [],
                                 'planned_vehicles': [leg['vehicle'] for leg in job['legs']],
                                 'stations': {leg['vehicle']: list(leg['station']) for leg in job['legs']}}
            for leg in job['legs']:
                item = copy.deepcopy(leg)
                item['target'] = target
                item['angles'] = pointing(leg['station'], scene['alerts_azimuth_deg_range_m_altitude_m'][target], scene)
                item['slew_duration'] = max(map(abs, item['angles'])) / self.rate
                self.tasks[leg['vehicle']].append(item)
        for tasks in self.tasks.values():
            tasks.sort(key=lambda task: task['depart_s'])
        self.vehicles = {}
        for v, p in scene['vehicles_xy_m'].items():
            self.vehicles[v] = {'alive': True, 'cursor': 0, 'token': 0,
                                'position': list(p), 'gimbal': [0.0, 0.0],
                                'phase': None, 'command': None}
            self.enter(v, 'idle', 0.0)
        self.issued = {}
        # Only the original t=0 schedule is transmitted here. No scenario data
        # informs assignments, stations, ordering, or the first commands.
        for v in self.vehicles:
            count = len(self.tasks[v]) if self.mode == 'preloaded' else min(1, len(self.tasks[v]))
            for index in range(count):
                self.issue(v, index, 0.0)
            if self.tasks[v]:
                self.push(0.0, 2, 'dispatch', v)
        self.push(DISTURBANCE, 0, 'disturbance')
        recovery = self.scenario['recovery_s']
        if recovery is not None and recovery <= DEADLINE:
            self.push(recovery, 1, 'recovery')

    def push(self, time, priority, action, vehicle=None, token=None):
        self.serial += 1
        heapq.heappush(self.heap, (time, priority, self.serial, action, vehicle, token))

    def issue(self, vehicle, index, time):
        # This method never reads the future restoration time.
        if not self.online:
            raise AssertionError('new command while offline')
        task = self.tasks[vehicle][index]
        command = {'vehicle': vehicle, 'target': task['target'], 'sequence': index,
                   'issued_s': time, 'effective_s': time, 'station': task['station'],
                   'path': task['path'], 'source': 'original_schedule'}
        self.commands.append(command)
        self.issued[(vehicle, index)] = command

    def enter(self, vehicle, kind, time, target=None, duration=None, **extra):
        v = self.vehicles[vehicle]
        v['token'] += 1
        phase = {'vehicle': vehicle, 'target': target, 'kind': kind, 't0': time,
                 'position0_xy_m': list(v['position']), 'gimbal0_deg': list(v['gimbal'])}
        if v['command'] is not None and target is not None:
            phase['command_effective_s'] = v['command']['effective_s']
            phase['command_issued_s'] = v['command']['issued_s']
        phase.update(extra)
        if duration is not None:
            phase['planned_end_s'] = time + duration
            self.push(time + duration, 2, 'phase_end', vehicle, v['token'])
        v['phase'] = phase

    def leave(self, vehicle, time):
        v = self.vehicles[vehicle]
        e = copy.deepcopy(v['phase'])
        e['t1'] = time
        dt = time - e['t0']
        if e['kind'] == 'drive':
            v['position'], prefix = interpolate_path(e['path'], self.speed * dt)
            # Remove arithmetic endpoint drift when the complete leg is done.
            if time == e['planned_end_s']:
                v['position'], prefix = list(e['path'][-1]), copy.deepcopy(e['path'])
            e['path'] = prefix
            e['distance_m'] = path_length(prefix)
        elif e['kind'] in ('slew', 'reset'):
            v['gimbal'] = move_axes(e['gimbal0_deg'], e['gimbal_goal_deg'], dt, self.rate)
            if time == e['planned_end_s']:
                v['gimbal'] = list(e['gimbal_goal_deg'])
        e['position1_xy_m'] = list(v['position'])
        e['gimbal1_deg'] = list(v['gimbal'])
        # Keep meaningful instantaneous drive/slew/reset/fail records, not idle noise.
        if e['kind'] not in ('idle', 'wait') or time > e['t0']:
            self.events.append(e)
        return e

    def task(self, vehicle):
        v = self.vehicles[vehicle]
        return self.tasks[vehicle][v['cursor']]

    def dispatch(self, vehicle, time):
        v = self.vehicles[vehicle]
        if not v['alive'] or v['cursor'] == len(self.tasks[vehicle]) or time > DEADLINE:
            return
        key = (vehicle, v['cursor'])
        if key not in self.issued:
            if not self.online:
                return
            self.issue(vehicle, v['cursor'], time)
        task = self.task(vehicle)
        if math.dist(v['position'], task['path'][0]) > 1e-8:
            raise AssertionError('fixed next path does not start at actual current position')
        if max(map(abs, v['gimbal'])) > 1e-8:
            raise AssertionError('driving before east-horizontal reset')
        self.leave(vehicle, time)
        v['command'] = self.issued[key]
        self.enter(vehicle, 'drive', time, task['target'],
                   duration=path_length(task['path']) / self.speed,
                   path=copy.deepcopy(task['path']), planned_path=copy.deepcopy(task['path']),
                   speed_m_s=self.speed, station=task['station'])

    def end_phase(self, vehicle, time, token):
        v = self.vehicles[vehicle]
        if not v['alive'] or token != v['token']:
            return
        e, task = self.leave(vehicle, time), self.task(vehicle)
        target = task['target']
        if e['kind'] == 'drive':
            self.enter(vehicle, 'slew', time, target, duration=task['slew_duration'],
                       station=task['station'], gimbal_goal_deg=task['angles'], axis_speed_deg_s=self.rate)
        elif e['kind'] == 'slew':
            self.enter(vehicle, 'wait', time, target, station=task['station'],
                       reason='awaiting_original_partner')
            self.push(time, 3, 'try_start', vehicle)
        elif e['kind'] == 'observe':
            self.enter(vehicle, 'reset', time, target, duration=task['slew_duration'],
                       station=task['station'], gimbal_goal_deg=[0.0, 0.0], axis_speed_deg_s=self.rate)
        elif e['kind'] == 'reset':
            v['cursor'] += 1
            v['command'] = None
            remaining = v['cursor'] < len(self.tasks[vehicle])
            self.enter(vehicle, 'idle', time, reason='awaiting_original_command' if remaining else 'original_queue_finished')
            if remaining and time <= DEADLINE:
                self.push(time, 2, 'dispatch', vehicle)

    def try_start(self, vehicle, time):
        if time > DEADLINE or not self.vehicles[vehicle]['alive']:
            return
        target = self.vehicles[vehicle]['phase']['target']
        if target is None or self.jobs[target]['start_s'] is not None:
            return
        pair = self.jobs[target]['planned_vehicles']
        if not all(self.vehicles[v]['alive'] and self.vehicles[v]['phase']['kind'] == 'wait'
                   and self.vehicles[v]['phase']['target'] == target for v in pair):
            return
        self.jobs[target]['start_s'] = time
        self.jobs[target]['vehicles'] = list(pair)
        for v in pair:
            self.leave(v, time)
            self.enter(v, 'observe', time, target, duration=self.duration,
                       station=self.task(v)['station'], joint_start_s=time, joint_end_s=time + self.duration)

    def disturb(self, time):
        scenario = self.scenario
        if scenario['recovery_s'] is not None:
            self.online = False
            self.system_events.append({'kind': 'communication_lost', 't': time})
        if scenario['new_alert'] is not None:
            self.jobs['T4'] = {'start_s': None, 'vehicles': [], 'planned_vehicles': [], 'stations': {}}
            self.system_events.append({'kind': 'new_alert', 'target': 'T4', 't': time,
                                       'polar': scenario['new_alert']})
        failed = scenario['failed_vehicle']
        if failed is not None:
            v = self.vehicles[failed]
            before = self.leave(failed, time)
            self.events.append({'vehicle': failed, 'target': before['target'], 'kind': 'fail',
                                't0': time, 't1': time, 'position_xy_m': list(v['position']),
                                'gimbal_deg': list(v['gimbal']), 'interrupted_kind': before['kind'],
                                'withdrawn': True})
            v['alive'] = False
            v['token'] += 1

    def finish(self):
        while self.heap:
            time, _, _, action, vehicle, token = heapq.heappop(self.heap)
            self.now = time
            if action == 'disturbance':
                self.disturb(time)
            elif action == 'recovery':
                self.online = True
                self.system_events.append({'kind': 'communication_recovered', 't': time})
                for v, state in self.vehicles.items():
                    if state['alive'] and state['phase']['kind'] == 'idle':
                        self.push(time, 2, 'dispatch', v)
            elif action == 'dispatch':
                self.dispatch(vehicle, time)
            elif action == 'phase_end':
                self.end_phase(vehicle, time, token)
            elif action == 'try_start':
                self.try_start(vehicle, time)
        horizon = max(DEADLINE, self.now)
        for vehicle, v in self.vehicles.items():
            if v['alive']:
                self.leave(vehicle, horizon)
        self.events.sort(key=lambda e: (e['t0'], e['vehicle']))
        starts = [j['start_s'] for j in self.jobs.values() if j['start_s'] is not None and j['start_s'] <= DEADLINE]
        for target, job in self.jobs.items():
            job['locked_at_5s'] = job['start_s'] is not None and job['start_s'] < DISTURBANCE
            job['observation_end_s'] = None if job['start_s'] is None else job['start_s'] + self.duration
            if job['start_s'] is None:
                if target == 'T4':
                    job['reason'] = 'no_original_seat_for_new_alert'
                elif self.scenario['failed_vehicle'] in job['planned_vehicles']:
                    job['reason'] = 'original_partner_failed_before_joint_start'
                else:
                    job['reason'] = 'original_dependency_or_command_not_ready_by_deadline'
        n, k = len(self.jobs), len(starts)
        recovery = self.scenario['recovery_s']
        result = {'scenario_id': self.scenario['id'], 'policy': 'retain_original' if self.mode == 'preloaded' else 'retain_original_current_only',
                  'scenario': self.scenario, 'dispatch_mode': self.mode,
                  'rotation_model': 'independent_rate_limited_axes',
                  'recovery_scope': 'main_intersection' if recovery is None or 10 <= recovery <= 30 else 'range_sensitivity',
                  'N': n, 'k': k, 'eta': k / n, 't_last_s': max(starts, default=0.0) if k == n else DEADLINE,
                  'jobs': self.jobs, 'events': self.events, 'commands': self.commands,
                  'system_events': self.system_events, 'timeline_end_s': horizon,
                  'trace_complete_until_s': horizon,
                  'original_queues': {v: [t['target'] for t in tasks] for v, tasks in self.tasks.items()},
                  'initial_positions_xy_m': self.scene['vehicles_xy_m'],
                  'axis_speed_deg_s': self.rate, 'observation_duration_s': self.duration,
                  'score_rule': 'joint start in [0,60]; starts before 5 locked even if a vehicle then fails',
                  'time_rule': 'exact floating-point ordering; disturbance before start at equal time; no deadline grace'}
        result['locked_targets_at_5s'] = [t for t, j in self.jobs.items() if j['locked_at_5s']]
        result['state_5s_before'] = state_at(result, DISTURBANCE, side='left')
        result['state_5s_after'] = state_at(result, DISTURBANCE, side='right')
        return result


def _run_plan(scenario, plan, scene, dispatch_mode='preloaded'):
    """Internal hook for synthetic timing/dependency boundary tests only."""
    return _Replay(scenario, copy.deepcopy(plan), copy.deepcopy(scene), dispatch_mode).finish()


def run(scenario, *, dispatch_mode='preloaded'):
    """Replay any shared-contract scenario; all fixed-plan commands start at t=0.

    recovery_s outside [10,30] is accepted only as labelled range sensitivity.
    `current_only` is an explicitly separate command-protocol sensitivity.
    """
    plan = json.loads(SNAPSHOT.read_text())['q22']['jobs']
    scene = json.loads(SCENE.read_text())
    result = _run_plan(scenario, plan, scene, dispatch_mode)
    result['sources'] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in (SNAPSHOT, SCENE)}
    return result


def state_at(result, t, side='right'):
    """Physical states from event replay, with continuous road/gimbal interpolation.

    Right limit includes failure at t=5; left limit excludes equal-time starts.
    Locked score does NOT free an observer. Reassignable means a new command
    may change its task, not that a nonzero gimbal can immediately start driving.
    """
    t = float(t)
    if side not in ('left', 'right') or not 0 <= t <= result['timeline_end_s']:
        raise ValueError('invalid side or time outside recorded timeline')
    states = {}
    for vehicle, initial in result['initial_positions_xy_m'].items():
        events = [e for e in result['events'] if e['vehicle'] == vehicle]
        failures = [e for e in events if e['kind'] == 'fail' and (e['t0'] < t or (e['t0'] == t and side == 'right'))]
        connected = not any(e['kind'] == 'communication_lost' and (e['t'] < t or (e['t'] == t and side == 'right'))
                            for e in result['system_events']) or any(e['kind'] == 'communication_recovered' and
                            (e['t'] < t or (e['t'] == t and side == 'right')) for e in result['system_events'])
        if failures:
            fail = failures[0]
            states[vehicle] = {'vehicle': vehicle, 't_s': t, 'side': side, 'phase': 'failed', 'active': False,
                               'position_xy_m': None, 'last_position_xy_m': fail['position_xy_m'],
                               'gimbal_deg': fail['gimbal_deg'], 'target': fail['target'],
                               'occupied': False, 'locked_occupancy': False, 'reassignable': False,
                               'command_reachable': False, 'release_after_reset_s': None,
                               'remaining_original_tasks': []}
            continue
        positive = [e for e in events if e['kind'] != 'fail' and e['t1'] > e['t0']]
        candidates = [e for e in positive if (e['t0'] <= t < e['t1'] if side == 'right' else e['t0'] < t <= e['t1'])]
        if not candidates and t == result['timeline_end_s']:
            candidates = [e for e in positive if e['t1'] == t]
        if not candidates:
            event = {'kind': 'idle', 't0': t, 't1': t, 'target': None, 'position0_xy_m': initial, 'gimbal0_deg': [0., 0.]}
        else:
            event = candidates[-1]
        kind, target = event['kind'], event['target']
        pos, angles = list(event['position0_xy_m']), list(event['gimbal0_deg'])
        if kind == 'drive':
            pos, _ = interpolate_path(event.get('planned_path', event['path']), event['speed_m_s'] * (t - event['t0']))
        elif kind in ('slew', 'reset'):
            angles = move_axes(angles, event['gimbal_goal_deg'], t - event['t0'], result['axis_speed_deg_s'])
        locked = target in result.get('locked_targets_at_5s', []) and kind in ('observe', 'reset')
        release = None
        if kind in ('observe', 'reset'):
            resets = [e for e in events if e['target'] == target and e['kind'] == 'reset']
            if resets:
                release = resets[0]['planned_end_s']
            elif kind == 'observe':
                release = event['joint_end_s'] + max(map(abs, angles)) / result['axis_speed_deg_s']
        completed = {e['target'] for e in events if e['kind'] == 'reset' and
                     (e['t1'] < t or (e['t1'] == t and side == 'right'))}
        states[vehicle] = {'vehicle': vehicle, 't_s': t, 'side': side, 'phase': kind, 'active': True,
                           'position_xy_m': pos, 'gimbal_deg': angles, 'target': target,
                           'occupied': kind != 'idle', 'locked_occupancy': locked,
                           'reassignable': kind not in ('observe', 'reset'), 'command_reachable': connected,
                           'release_after_reset_s': release,
                           'reset_from_current_pose_s': max(map(abs, angles)) / result['axis_speed_deg_s'],
                           'phase_end_s': event.get('planned_end_s'),
                           'remaining_observation_s': max(0., event['joint_end_s'] - t) if kind == 'observe' else 0.,
                           'remaining_original_tasks': [target for target in result['original_queues'][vehicle] if target not in completed]}
    return states


def read_scenarios(text):
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = [json.loads(line) for line in text.splitlines() if line.strip()]
    if isinstance(data, dict):
        data = data['scenarios'] if 'scenarios' in data else [data]
    if not isinstance(data, list):
        raise ValueError('input must be a scenario, scenario list, {scenarios:[...]}, or JSON Lines')
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scenario_json', help='JSON/JSONL file, or - for stdin')
    parser.add_argument('--dispatch-mode', choices=['preloaded', 'current_only'], default='preloaded')
    parser.add_argument('--output', help='JSON Lines output (default stdout)')
    args = parser.parse_args()
    try:
        text = sys.stdin.read() if args.scenario_json == '-' else Path(args.scenario_json).read_text()
        results = [run(s, dispatch_mode=args.dispatch_mode) for s in read_scenarios(text)]
        output = ''.join(json.dumps(r, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n' for r in results)
        if args.output:
            path = Path(args.output)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(output)
        else:
            sys.stdout.write(output)
    except (ValueError, OSError, KeyError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    main()
