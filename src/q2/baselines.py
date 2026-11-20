"""Q2(2) greedy assignment on a common finite pool with feasible straight routes."""

import itertools
import math

from ..shared.geometry import InfeasibleGeometry, compatible_pair
from ..shared.plan import Job, Leg, Plan
from ..shared.scene import coordinates
from ..shared.timeline import prepare_arrival


RULES = ('sum', 'max', 'sum_time', 'max_time', 'ready')


def choose_pair(scene, target, stations, positions, free, rule):
    options = {}
    for vehicle, origin in positions.items():
        choices = []
        for station in stations:
            leg = Leg(vehicle, station, (origin, station))
            try:
                choices.append(prepare_arrival(scene, target, leg, free[vehicle]))
            except InfeasibleGeometry:
                continue
        options[vehicle] = choices

    best_key = None
    selected = None
    ties = 0
    for v, w in itertools.combinations(sorted(options), 2):
        for a, b in itertools.product(options[v], options[w]):
            p, q = a.leg.station, b.leg.station
            if not compatible_pair(scene, p, q, a.aim[0], b.aim[0]):
                continue
            start = max(a.ready_s, b.ready_s)
            if rule == 'ready':
                primary = start
            elif rule in ('sum', 'sum_time'):
                primary = a.distance_m + b.distance_m
            else:
                primary = max(a.distance_m, b.distance_m)
            if best_key is not None and primary > best_key[0]:
                continue
            key = (primary, start if rule.endswith('_time') else 0., v, w, p, q)
            if best_key is None or primary < best_key[0]:
                ties = 0
            ties += 1
            if best_key is None or key < best_key:
                selected, best_key = (a, b), key
    if selected is None:
        raise ValueError(f'{target}: no feasible pair of vehicles and stations for {rule}')
    return selected, ties


def generate_plan(scene, scene_hash, stations, order, rule):
    if rule not in RULES:
        raise ValueError(f'Unknown allocation rule {rule!r}')
    if sorted(order) != sorted(scene.targets):
        raise ValueError('Allocation order must contain every alert exactly once')
    pool = sorted({coordinates(p, 2) for p in stations})
    positions = scene.homes.copy()
    free = dict.fromkeys(positions, 0.)
    jobs, primary_ties = [], {}
    for target in order:
        (a, b), primary_ties[target] = choose_pair(scene, target, pool, positions, free, rule)
        jobs.append(Job(target, (a.leg, b.leg)))
        end = max(a.ready_s, b.ready_s) + scene.observation_s
        for entry in (a, b):
            vehicle = entry.leg.vehicle
            positions[vehicle] = entry.leg.station
            free[vehicle] = end + entry.slew_s
    return Plan(rule, scene_hash, tuple(jobs)), primary_ties


def allocation_summary(plan, events, scored, ties):
    """Tabulate actual distances and partner waits from the completed replay."""
    legs = {}
    for event in events:
        kind = event['kind']
        key = event['target'], event['vehicle']
        if kind == 'drive':
            path = event['path']
            legs[key] = {'vehicle': event['vehicle'], 'station': event['station'], 'wait_s': 0.,
                         'drive_m': sum(math.dist(a, b) for a, b in zip(path, path[1:]))}
        elif kind == 'slew':
            legs[key]['ready_s'] = event['t1']
        elif kind == 'wait':
            legs[key]['wait_s'] = event['t1'] - event['t0']
    jobs = [{'target': job.target, 'start_s': scored['start_s'][job.target], 'primary_ties': ties[job.target],
             'legs': [legs[job.target, leg.vehicle] for leg in job.legs]} for job in plan.jobs]
    return {'rule': plan.method, 'order': [job.target for job in plan.jobs], 'latest_s': scored['latest_start_s'],
            'total_drive_m': sum(leg['drive_m'] for leg in legs.values()),
            'total_wait_s': sum(leg['wait_s'] for leg in legs.values()), 'jobs': jobs}
