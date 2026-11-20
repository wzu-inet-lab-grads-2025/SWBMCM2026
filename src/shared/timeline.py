"""Execute a nominal preloaded plan; event times are derived solely from commands and scene."""

import math
from collections import defaultdict
from dataclasses import dataclass

from .geometry import check_route, compatible_pair, pointing, travelled_path
from .plan import Leg

EAST = (0., 0.)


@dataclass(frozen=True)
class Available:
    position: tuple
    free_s: float
    angles: tuple = EAST
    target: str | None = None


def turn_time(scene, start, goal=EAST):
    return max(abs(a-b) for a, b in zip(start, goal)) / scene.axis_speed


def joint_observations(events):
    """Derive each synchronized start and its two vehicle IDs from physical events."""
    pairs = defaultdict(list)
    for event in events:
        if event['kind'] == 'observe':
            pairs[event['target']].append(event)
    starts = {}
    for target, pair in pairs.items():
        if len(pair) != 2 or pair[0]['vehicle'] == pair[1]['vehicle'] or pair[0]['t0'] != pair[1]['t0']:
            raise ValueError(f'{target}: missing synchronized observation pair')
        starts[target] = pair[0]['t0'], [e['vehicle'] for e in pair]
    return starts


@dataclass(frozen=True)
class Arrival:
    leg: Leg
    aim: tuple
    slew_s: float
    depart_s: float
    arrival_s: float
    distance_m: float
    continue_aim: bool = False

    @property
    def ready_s(self):
        return self.arrival_s + self.slew_s


def measure_leg(scene, target, leg):
    check_route(scene, leg.path)
    aim = pointing(scene, target, leg.station)
    distance_m = sum(math.dist(a, b) for a, b in zip(leg.path, leg.path[1:]))
    return aim, distance_m


def arrival_from_state(scene, target, leg, state, aim, distance_m):
    keep = state.target == target and distance_m == 0
    depart = state.free_s if keep else state.free_s + turn_time(scene, state.angles)
    slew = turn_time(scene, state.angles if keep else EAST, aim)
    return Arrival(leg, aim, slew, depart, depart + distance_m / scene.speed, distance_m, keep)


def prepare_arrival(scene, target, leg, depart_s):
    return arrival_from_state(scene, target, leg, Available(leg.path[0], depart_s), *measure_leg(scene, target, leg))


def replay(scene, plan, record_until_s=0.):
    if not math.isfinite(record_until_s) or record_until_s < 0:
        raise ValueError('Recording end must be finite and nonnegative')
    if sorted(job.target for job in plan.jobs) != sorted(scene.targets):
        raise ValueError('Plan must assign every alert exactly once')
    for home in scene.homes.values():
        check_route(scene, (home, home))
    origins = {v: Available(p, 0.) for v, p in scene.homes.items()}
    return execute_jobs(scene, plan.jobs, origins, record_until_s, command_s=0.)


def cancel_reset(scene, vehicle, state, command_s):
    return dict(vehicle=vehicle, target=state.target, kind='reset', t0=state.free_s,
                t1=state.free_s + turn_time(scene, state.angles), station=state.position,
                gimbal0_deg=state.angles, gimbal1_deg=EAST, gimbal_goal_deg=EAST,
                command_issued_s=command_s, command_effective_s=command_s, reason='cancel_unstarted_task')


def execute_jobs(scene, jobs, origins, record_until_s, command_s):
    """Execute ordered commands from supplied poses and releases, including cancellation resets."""
    states = origins.copy()
    events = []
    for job in jobs:
        prepared = []
        for leg in job.legs:
            if leg.vehicle not in states or leg.path[0] != states[leg.vehicle].position:
                raise ValueError(f'{job.target}/{leg.vehicle}: unknown vehicle or disconnected route')
            prepared.append(arrival_from_state(scene, job.target, leg, states[leg.vehicle], *measure_leg(scene, job.target, leg)))

        a, b = job.legs
        alpha, beta = (entry.aim[0] for entry in prepared)
        if not compatible_pair(scene, a.station, b.station, alpha, beta):
            raise ValueError(f'{job.target}: incompatible station pair')
        start = max(entry.ready_s for entry in prepared)
        end = start + scene.observation_s
        for entry in prepared:
            leg, aim = entry.leg, entry.aim
            state = states[leg.vehicle]
            depart, arrival, ready = entry.depart_s, entry.arrival_s, entry.ready_s
            common = dict(vehicle=leg.vehicle, target=job.target, station=leg.station,
                          command_issued_s=command_s, command_effective_s=command_s)
            if depart > state.free_s:
                events.append(cancel_reset(scene, leg.vehicle, state, command_s))
            if not entry.continue_aim:
                events.append(dict(common, kind='drive', t0=depart, t1=arrival, path=leg.path,
                                   gimbal0_deg=EAST, gimbal1_deg=EAST))
            events.append(dict(common, kind='slew', t0=arrival, t1=ready,
                               gimbal0_deg=state.angles if entry.continue_aim else EAST, gimbal1_deg=aim, gimbal_goal_deg=aim))
            if ready < start:
                events.append(dict(common, kind='wait', t0=ready, t1=start, gimbal0_deg=aim, gimbal1_deg=aim))
            events.append(dict(common, kind='observe', t0=start, t1=end, gimbal0_deg=aim, gimbal1_deg=aim))
            release = end + turn_time(scene, aim)
            states[leg.vehicle] = Available(leg.station, release)
            events.append(dict(common, kind='reset', t0=end, t1=release,
                               gimbal0_deg=aim, gimbal1_deg=EAST, gimbal_goal_deg=EAST))

    for vehicle, state in list(states.items()):
        if state.angles != EAST:
            reset = cancel_reset(scene, vehicle, state, command_s)
            events.append(reset)
            states[vehicle] = Available(state.position, reset['t1'])
    horizon = max([record_until_s, *(s.free_s for s in states.values())])
    for vehicle, state in states.items():
        if state.free_s < horizon:
            events.append(dict(vehicle=vehicle, kind='idle', target=None, t0=state.free_s, t1=horizon,
                               station=state.position, gimbal0_deg=EAST, gimbal1_deg=EAST))
    return sorted(events, key=lambda event: (event['t0'], event['vehicle']))


def pose_at(scene, event, time_s):
    """Position and actual axis angles at an instant within an event."""
    phase = event['kind']
    if phase == 'drive':
        if time_s >= event['t1']:
            return event['path'][-1], EAST
        return travelled_path(event['path'], scene.speed * (time_s - event['t0']))[-1], EAST
    if phase in ('slew', 'reset') and time_s < event['t1']:
        step = scene.axis_speed * (time_s - event['t0'])
        angles = tuple(a + math.copysign(min(step, abs(b-a)), b-a)
                       for a, b in zip(event['gimbal0_deg'], event['gimbal_goal_deg']))
        return event['station'], angles
    return event['station'], event['gimbal1_deg']


def state_at(scene, events, time_s):
    """Right-continuous state; at the final reset endpoint the vehicle is idle."""
    if not 0 <= time_s <= max(e['t1'] for e in events):
        raise ValueError('Snapshot time is outside the trace')
    states = {}
    for event in events:
        if event['t0'] > time_s:
            break
        vehicle, phase = event['vehicle'], event['kind']
        if phase == 'fail':
            states[vehicle] = dict(phase='failed', target=event['target'], position_xy_m=None,
                                   last_position_xy_m=event['position_xy_m'], gimbal_deg=event['gimbal_deg'])
            continue
        position, angles = pose_at(scene, event, min(time_s, event['t1']))
        target = event['target']
        if time_s >= event['t1']:
            phase, target = 'idle', None
        states[vehicle] = dict(phase=phase, target=target, position_xy_m=position, gimbal_deg=angles)
    return states
