"""Finite-station beam search. Return job assignments; the replay engine owns the clock."""

from dataclasses import dataclass
from functools import lru_cache
from itertools import combinations, product
import math

from ..shared.geometry import InfeasibleGeometry, compatible_pair
from ..shared.plan import Job, Leg
from ..shared.scene import coordinates
from ..shared.timeline import Available, arrival_from_state, measure_leg, turn_time


@dataclass
class Branch:
    jobs: tuple
    states: dict
    remaining: tuple
    latest: float


class BeamPlanner:
    def __init__(self, scene, plan, settings):
        self.width = settings['beam_width']
        grid = settings['grid_points_per_axis']
        if type(self.width) is not int or self.width < 1 or type(grid) is not int or grid < 2:
            raise ValueError('Expected positive beam width and at least two grid points per axis')
        self.areas = {v: coordinates(rect, 4) for v, rect in settings['vehicle_areas_xyxy_m'].items()}
        if self.areas.keys() != scene.homes.keys():
            raise ValueError('Expected one candidate area per vehicle')
        for rect in self.areas.values():
            if rect[0] >= rect[2] or rect[1] >= rect[3]:
                raise ValueError(f'Invalid candidate rectangle {rect}')
        for a, b in combinations(self.areas.values(), 2):
            gap = math.hypot(max(a[0]-b[2], b[0]-a[2], 0), max(a[1]-b[3], b[1]-a[3], 0))
            if gap < 2 * scene.radius:
                raise ValueError('Candidate areas must allow simultaneous vehicle movement')
        self.stations = {}
        for vehicle, (w, s, e, n) in self.areas.items():
            points = {(w + i*(e-w)/(grid-1), s + j*(n-s)/(grid-1)) for i, j in product(range(grid), repeat=2)}
            points.add(scene.homes[vehicle])
            points.update(leg.station for job in plan.jobs for leg in job.legs if leg.vehicle == vehicle)
            self.stations[vehicle] = {p for p in points if self.inside(vehicle, p)}

    def inside(self, vehicle, p):
        w, s, e, n = self.areas[vehicle]
        return w - 1e-9 <= p[0] <= e + 1e-9 and s - 1e-9 <= p[1] <= n + 1e-9

    def choose(self, scene, origins, pending, latest_locked, deadline):
        """Search from current healthy poses and known releases, in the announced scene."""
        if any(not self.inside(v, state.position) for v, state in origins.items()):
            return (), 'current_position_outside_candidate_area'
        pools = {v: sorted(self.stations[v] | {s.position}) for v, s in origins.items()}

        @lru_cache(None)
        def geometry(target, leg):
            try:
                return measure_leg(scene, target, leg)
            except InfeasibleGeometry:
                return None

        @lru_cache(None)
        def options(vehicle, state, target):
            arrivals = []
            for station in pools[vehicle]:
                leg = Leg(vehicle, station, (state.position, station))
                measured = geometry(target, leg)
                if measured is None:
                    continue
                arrival = arrival_from_state(scene, target, leg, state, *measured)
                if arrival.ready_s <= deadline:
                    arrivals.append(arrival)
            return sorted(arrivals, key=lambda a: (a.ready_s, a.depart_s, a.leg.station))

        def compatible(a, b):
            return compatible_pair(scene, a.leg.station, b.leg.station, a.aim[0], b.aim[0], min_sine=1e-8)

        def earliest_pair(v, w, states, target):
            left, right = options(v, states[v], target), options(w, states[w], target)
            if not left or not right:
                return None
            if compatible(left[0], right[0]):
                return left[0], right[0]
            return min(((a, b) for a, b in product(left, right) if compatible(a, b)),
                       key=lambda p: (max(p[0].ready_s, p[1].ready_s), p[0].ready_s + p[1].ready_s), default=None)

        frontier = [Branch((), origins, tuple(pending), latest_locked)]
        for _ in pending:
            branches = []
            for node in frontier:
                for target in node.remaining:
                    for v, w in combinations(node.states, 2):
                        pair = earliest_pair(v, w, node.states, target)
                        if pair is None:
                            continue
                        start = max(a.ready_s for a in pair)
                        after = node.states.copy()
                        for a in pair:
                            release = start + scene.observation_s + turn_time(scene, a.aim)
                            after[a.leg.vehicle] = Available(a.leg.station, release)
                        job = Job(target, (pair[0].leg, pair[1].leg))
                        remaining = tuple(t for t in node.remaining if t != target)
                        branches.append(Branch((*node.jobs, job), after, remaining, max(node.latest, start)))
            if not branches:
                break
            branches.sort(key=lambda n: (n.latest, sum(s.free_s for s in n.states.values())))
            frontier, seen = [], set()
            for node in branches:
                key = node.remaining, tuple(node.states.items())
                if key not in seen:
                    seen.add(key)
                    frontier.append(node)
                    if len(frontier) == self.width:
                        break
        return frontier[0].jobs, 'beam_search'
