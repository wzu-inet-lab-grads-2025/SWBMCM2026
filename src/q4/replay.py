"""Common Q4 scenario clock, preloaded queues, command execution and event scoring."""

import math
from collections import defaultdict
from dataclasses import replace

from ..shared.geometry import travelled_path
from ..shared.scene import coordinates
from ..shared.timeline import Available, EAST, execute_jobs, joint_observations, pose_at, replay, state_at


def validate_manifest(document, scene, rules, allow_stress=False):
    rows = document['scenarios']
    if not isinstance(rows, list) or not rows:
        raise ValueError('Expected a nonempty scenarios list')
    ids = set()
    for row in rows:
        name = row['id']
        if not isinstance(name, str) or not name or name in ids:
            raise ValueError(f'Invalid or duplicate scenario ID: {name!r}')
        ids.add(name)
        if row['failed_vehicle'] is not None and row['failed_vehicle'] not in scene.homes:
            raise ValueError(f'{name}: unknown failed vehicle')
        recovery = row['recovery_s']
        if recovery is not None:
            lo, hi = rules['recovery_s_left_open']
            if type(recovery) not in (int, float) or not math.isfinite(recovery) or recovery <= lo:
                raise ValueError(f'{name}: invalid recovery time')
            if recovery > hi and not allow_stress:
                raise ValueError(f'{name}: recovery after {hi} s requires --allow-stress-recovery')
        if row['new_alert'] is not None:
            theta, distance, height = coordinates(row['new_alert'], 3)
            az_min, az_max = rules['new_azimuth_deg_half_open']
            r_min, r_max = rules['new_range_m']
            if not (az_min <= theta < az_max and r_min <= distance <= r_max and height == rules['new_altitude_m']):
                raise ValueError(f'{name}: new alert outside the specified domain')
    return rows


def clip_phase(scene, event, cut):
    if event['t1'] <= cut:
        return event
    _, angles = pose_at(scene, event, cut)
    clipped = dict(event, t1=cut, interrupted=True, planned_end_s=event.get('planned_end_s', event['t1']), gimbal1_deg=angles)
    if event['kind'] == 'drive':
        path = travelled_path(event['path'], (cut - event['t0']) * scene.speed)
        clipped['path'] = path if len(path) > 1 else [path[0], path[0]]
    return clipped


def withdraw(scene, trace, cut):
    """Cut a failed vehicle's actual trace at the left limit of the disruption."""
    prefix = []
    for event in trace:
        if event['t0'] >= cut:
            break
        prefix.append(clip_phase(scene, event, cut))
    previous = prefix[-1]
    position, angles = pose_at(scene, previous, cut)
    prefix.append(dict(vehicle=previous['vehicle'], target=previous['target'], kind='fail', t0=cut, t1=cut,
                       position_xy_m=position, gimbal_deg=angles, interrupted_kind=previous['kind']))
    return prefix


def outcome(events, target_ids, cut, window):
    """Rebuild the Q4 score only from the participants' actual observation starts."""
    observations = joint_observations(events)
    if observations.keys() - set(target_ids):
        raise ValueError('Unannounced target in observation events')
    jobs = {}
    starts = []
    for target in target_ids:
        start, vehicles = observations.get(target, (None, []))
        if start is not None and window[0] <= start <= window[1]:
            starts.append(start)
        else:
            start, vehicles = None, []
        jobs[target] = dict(start_s=start, vehicles=vehicles, locked_at_5s=start is not None and start < cut)
    n, k = len(jobs), len(starts)
    return dict(jobs=jobs, N=n, k=k, eta=k/n, t_last_s=max(starts) if k == n else window[1])


def decision_snapshot(scene, events, now):
    """Preserve the executed prefix and mandatory occupation of already-started jobs."""
    states = state_at(scene, events, now)
    releases = {(e['vehicle'], e['target']): e['t1'] for e in events if e['kind'] == 'reset'}
    started = {e['target'] for e in events if e['kind'] == 'observe' and e['t0'] <= now}
    origins = {}
    for vehicle, state in states.items():
        phase, target = state['phase'], state['target']
        if phase == 'failed':
            continue
        position = tuple(state['position_xy_m'])
        if phase in ('observe', 'reset'):
            origins[vehicle] = Available(position, releases[vehicle, target])
        else:
            origins[vehicle] = Available(position, now, tuple(state['gimbal_deg']), target)
    # Keep vehicle ordering identical to the input scene, including tie-breaking in the search.
    origins = {v: origins[v] for v in scene.homes if v in origins}
    prefix = []
    for event in events:
        v = event['vehicle']
        until = origins[v].free_s if v in origins else now
        finished_reset = event['kind'] == 'reset' and event['t0'] == event['t1'] == until and event['target'] in started
        if event['t0'] < until or (event['kind'] == 'fail' and event['t0'] == until) or finished_reset:
            prefix.append(clip_phase(scene, event, until))
    return origins, prefix


class ScenarioReplay:
    """Both policies start with the same preloaded queues and use the same physical clock."""

    def __init__(self, scene, plan, rules):
        self.scene, self.plan = scene, plan
        self.cut = rules['disturbance_s']
        self.window = rules['window_s']
        if not 0 == self.window[0] < self.cut < self.window[1] or 'T4' in scene.targets:
            raise ValueError('Expected original alerts and a disturbance strictly inside the evaluation window')

        # Reconstruct all times from the plan once, independently of the manifest.
        self.phases = defaultdict(dict)
        for event in replay(scene, plan, self.window[1]):
            if event['target'] is not None:
                self.phases[event['target'], event['vehicle']][event['kind']] = event
        self.commands = [dict(vehicle=leg.vehicle, target=job.target, issued_s=0., effective_s=0.,
                              station=leg.station, path=leg.path, source='original_schedule')
                         for job in plan.jobs for leg in job.legs]

    def preloaded_events(self, failed):
        traces = {vehicle: [] for vehicle in self.scene.homes}
        # A still-healthy partner remains at the first task whose other seat is missing.
        stuck = set()
        waiting = {}
        for job in self.plan.jobs:
            pair = [leg.vehicle for leg in job.legs]
            start = self.phases[job.target, pair[0]]['observe']['t0']
            can_start = (start <= self.window[1] and not stuck.intersection(pair)
                         and not (failed in pair and start >= self.cut))
            for vehicle in pair:
                phases = self.phases[job.target, vehicle]
                if vehicle in stuck or phases['drive']['t0'] > self.window[1]:
                    continue
                if can_start:
                    traces[vehicle].extend(e.copy() for e in phases.values())
                else:
                    traces[vehicle].extend((phases['drive'].copy(), phases['slew'].copy()))
                    last = phases['slew']
                    waiting[vehicle] = dict(vehicle=vehicle, target=job.target, kind='wait', station=last['station'],
                                            t0=last['t1'], gimbal0_deg=last['gimbal1_deg'], gimbal1_deg=last['gimbal1_deg'],
                                            command_issued_s=0., command_effective_s=0.)
            if not can_start:
                stuck.update(pair)

        endings = [trace[-1]['t1'] for v, trace in traces.items() if trace and v != failed]
        horizon = max([self.window[1], *endings])
        events = []
        for vehicle, trace in traces.items():
            end = trace[-1]['t1'] if trace else 0.
            if end < horizon:
                if vehicle in waiting:
                    trace.append(dict(waiting[vehicle], t1=horizon))
                else:
                    position = trace[-1]['station'] if trace else self.scene.homes[vehicle]
                    trace.append(dict(vehicle=vehicle, kind='idle', target=None, t0=end, t1=horizon, station=position,
                                      gimbal0_deg=EAST, gimbal1_deg=EAST))
            events.extend(withdraw(self.scene, trace, self.cut) if vehicle == failed else trace)
        events.sort(key=lambda e: (e['t0'], e['vehicle']))
        return events

    def decide(self, planner, events, commands, now, new_alert):
        # The policy sees the state at a connected tick. The external scenario ID,
        # outage schedule and any future recovery time are absent from its inputs.
        scene = self.scene
        if new_alert is not None:
            scene = replace(scene, targets={**scene.targets, 'T4': scene.project(scene.centre, new_alert)})
        origins, prefix = decision_snapshot(scene, events, now)
        locked = joint_observations(prefix)
        pending = [t for t in scene.targets if t not in locked]
        jobs, reason = planner.choose(scene, origins, pending, max((s for s, _ in locked.values()), default=0.), self.window[1])
        suffix = execute_jobs(scene, jobs, origins, self.window[1], command_s=now)
        candidate = sorted(prefix + suffix, key=lambda e: (e['t0'], e['vehicle']))
        old = outcome(events, scene.targets, self.cut, self.window)
        new = outcome(candidate, scene.targets, self.cut, self.window)
        decision = dict(at_s=now, mode='keep_preloaded', search=reason, started_targets=list(locked),
                        projected_keep=[old['k'], old['t_last_s']], projected_replan=[new['k'], new['t_last_s']])
        if new['k'] < old['k'] or (new['k'] == old['k'] and new['t_last_s'] >= old['t_last_s'] - 1e-9):
            return events, commands, decision

        decision['mode'] = 'replace_pending_queues'
        used = set()
        for job in jobs:
            for leg in job.legs:
                used.add(leg.vehicle)
                commands.append(dict(vehicle=leg.vehicle, target=job.target, source='reschedule', issued_s=now,
                                     effective_s=now, station=leg.station, path=leg.path))
        for v, state in origins.items():
            if v not in used:
                commands.append(dict(vehicle=v, target=state.target, source='reschedule', action='clear_pending_queue',
                                     issued_s=now, effective_s=now))
        return candidate, commands, decision

    def run(self, scenario, planner=None):
        failed, recovery, alert = (scenario[key] for key in ('failed_vehicle', 'recovery_s', 'new_alert'))
        events = self.preloaded_events(failed)
        commands = [c.copy() for c in self.commands]
        targets = list(self.scene.targets)
        system_events = []
        if alert is not None:
            targets.append('T4')
            system_events.append(dict(kind='new_alert', t=self.cut, target='T4', polar=scenario['new_alert']))
        if failed is not None:
            system_events.append(dict(kind='vehicle_failure', t=self.cut, vehicle=failed))
        if recovery is not None:
            system_events.append(dict(kind='communication_lost', t=self.cut))
            if recovery <= self.window[1]:
                system_events.append(dict(kind='communication_recovered', t=recovery))
        decision = None
        if planner is not None:
            if not system_events:
                decision = dict(at_s=None, mode='no_disturbance')
            elif recovery is not None and recovery > self.window[1]:
                decision = dict(at_s=None, mode='outage_through_window')
            else:
                now = self.cut if recovery is None else recovery
                events, commands, decision = self.decide(planner, events, commands, now, alert)
        result = dict(scenario_id=scenario['id'], scenario=scenario, policy='reschedule' if planner is not None else 'retain_original',
                      dispatch_mode='preloaded', rotation_model='independent_rate_limited_axes',
                      commands=commands, system_events=system_events, events=events,
                      trace_complete_until_s=max(e['t1'] for e in events),
                      state_after_disturbance=state_at(self.scene, events, self.cut),
                      **outcome(events, targets, self.cut, self.window))
        if decision is not None:
            result['decision'] = decision
        return result
