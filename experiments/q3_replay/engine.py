"""Single-equipment record replay. A fresh callable and fresh state per trial."""
from copy import deepcopy
from dataclasses import dataclass
import math


@dataclass
class Source:
    ref: str
    duration: float
    rows: list  # Samples with segment-relative timestamp_s; projected at dispatch.


@dataclass
class Trial:
    jobs: dict
    sources: dict
    zones: dict
    case: dict
    rules: dict
    alpha: float = 1.0
    beta: float = 0.5


def fixed_actions(actions):
    """Create a fresh policy(view) -> action; exhaustion is a caller error."""
    commands = iter(deepcopy(actions))
    def decide(view):
        return next(commands)
    return decide


class InvalidCommand(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise InvalidCommand(reason)


def number(action, key):
    value = action.get(key)
    require(type(value) in (int, float) and math.isfinite(value), key)
    return value


def execution_quote(now, origin, destination, reservation, zones, rules):
    """Service start and execution fee from public information only (Q3 §3)."""
    distance = math.dist(zones[origin], zones[destination])
    movement, fees = rules['movement'], rules['fees']
    start = now + distance / movement['speed'] + movement['changeover_s']
    fee = (fees['service_fixed'] + fees['reserve_per_second'] * reservation
           + fees['distance_per_unit'] * distance)
    return start, fee


def public_sample(row):
    """Project one source sample, including nested keys; never forward metadata.

    Missing channels and nullable numeric components are retained as supplied by
    the adapters. Malformed values in an allowed field fail closed (ValueError),
    rather than carrying arbitrary objects through numeric/quality leaves.
    """
    def scalar(value):
        if value is not None and not (type(value) in (int, float) and math.isfinite(value)):
            raise ValueError('expected finite numeric sample component or null')
        return value

    def vector(value, size):
        if not isinstance(value, list) or len(value) != size:
            raise ValueError('invalid sample vector shape')
        return [scalar(v) for v in value]

    def joints(value, size):
        if not isinstance(value, list) or len(value) != 21:
            raise ValueError('expected 21 hand joints')
        return [vector(v, size) for v in value]

    def reason(value):
        if value is None or isinstance(value, str):
            return value
        if isinstance(value, list) and all(v is None or isinstance(v, str) for v in value):
            return list(value)
        raise ValueError('invalid sample quality reason')

    def flag(value):
        if value is not None and type(value) is not bool:
            raise ValueError('invalid sample quality flag')
        return value

    def project(value, fields):
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError('expected sample channel object')
        return {key: fields[key](item) for key, item in value.items() if key in fields}

    pose = {'position': lambda v: vector(v, 3), 'orientation_xyzw': lambda v: vector(v, 4)}
    hand = {'positions': lambda v: joints(v, 3), 'orientations_xyzw': lambda v: joints(v, 4)}
    side = {'occlusion': flag, 'near_edge': flag}  # Legacy synthetic fixture.
    quality = {'is_bad': flag, 'reasons': reason,
               'left_bad_reason': reason, 'right_bad_reason': reason,
               'left': lambda v: project(v, side), 'right': lambda v: project(v, side)}
    fields = {'timestamp_s': scalar, 'hand_quality_flags': lambda v: project(v, quality)}
    for name in ('left_hand', 'right_hand'):
        fields[name] = lambda v: project(v, hand)
    for name in ('head_pose', 'left_head_camera_pose', 'right_head_camera_pose'):
        fields[name] = lambda v: project(v, pose)
    if (not isinstance(row, dict) or type(row.get('timestamp_s')) not in (int, float)
            or not math.isfinite(row['timestamp_s'])):
        raise ValueError('expected finite source-relative timestamp_s')
    return project(row, fields)


def score(jobs, horizon, ledger, elapsed, invalid):
    executions = [x for x in ledger if x['action'] == 'EXECUTE']
    completed = {x['job_id']: x['completion'] for x in executions if x['completion'] is not None}
    weights = {j: v['priority_weight'] for j, v in jobs.items()}
    total = sum(weights.values())
    return dict(weighted_completion_rate=sum(weights[j] for j in completed)/total,
                weighted_on_time_rate=sum(weights[j] for j,c in completed.items() if c <= jobs[j]['due_s'])/total,
                weighted_tardiness_s=sum(w*max(0, completed.get(j, horizon)-jobs[j]['due_s']) for j,w in weights.items()),
                elapsed_s=elapsed, cost=sum(e['fee'] for e in ledger),
                observations=sum(e['action']=='REVIEW' for e in ledger),
                observed_overrun_s=sum(e['overrun'] for e in executions),
                reserved_idle_s=sum(e['idle'] for e in executions),
                censored_units=len(executions)-len(completed), invalid_actions=invalid, valid_protocol=not invalid)


def run_trial(trial: Trial, policy):
    """Call policy(view) only when equipment is available. Return private audit log.

    The sole argument is a detached JSON-compatible public snapshot. Source IDs,
    seed, scoring ledger and callback audit log remain on the engine side.
    """
    jobs, rules, limits = trial.jobs, trial.rules, trial.case
    H, B, K = (limits[k] for k in ('horizon_s', 'cost_budget', 'observation_limit'))
    obs, service = rules['observation'], rules['service']
    widths = obs['prefix_seconds']
    time, cost, reviews, invalid = 0., 0., 0, 0
    zone = rules['initial_zone']
    public_rules = {k: rules[k] for k in ('fees', 'movement', 'observation', 'max_actions')}
    public_rules['reserve_min_s'] = service['reserve_min_s']
    level, completed = {}, {}
    calls, ledger, releases = [], [], []

    def release_through(end):
        for j in sorted(jobs, key=lambda j: (jobs[j]['release_s'], j)):
            at = jobs[j]['release_s']
            if j not in level and at <= end:
                level[j] = 0
                releases.append(dict(job_id=j, time=at, prefix_s=widths[0]))

    release_through(0)
    while time < H:
        visible = {}
        for j, i in level.items():
            visible[j] = dict(prefix_s=widths[i], rows=[public_sample(row) for row in trial.sources[j].rows
                                                         if 0 <= row['timestamp_s'] <= widths[i]])
        view = dict(time=time, zone=zone, budget_left=B-cost, reviews_left=K-reviews,
                    case=limits, rules=public_rules, zones=trial.zones, jobs=jobs, alpha=trial.alpha, beta=trial.beta,
                    completed=completed, observations=visible)
        # Spy at the actual dispatch boundary, before the callee can mutate its argument.
        arg = deepcopy(view)
        call = dict(index=len(calls), time=time, input=deepcopy(arg))
        calls.append(call)
        action = deepcopy(policy(arg))
        call['action'] = action
        try:
            require(len(calls) <= rules['max_actions'], 'action limit')
            require(isinstance(action, dict), 'action object')
            kind = action.get('action')
            require(kind in ('STOP','WAIT','REVIEW','EXECUTE'), 'unknown action')
            e = dict(action=kind, time=time, zone_before=zone, fee=0., end=time)
            if kind == 'STOP':
                ledger.append(dict(e, zone_after=zone, cost_after=cost))
                break
            if kind == 'WAIT':
                duration = number(action, 'seconds')
                require(duration > 0 and time+duration <= H, 'wait duration')
                e['end'] = time+duration
            else:
                j = action.get('job_id')
                require(isinstance(j,str) and j in level and j not in completed, 'job unavailable')
                e['job_id'] = j
                if kind == 'REVIEW':
                    end = time+obs['delay_s']
                    require(level[j] < obs['max_per_job'] and reviews < K and end <= H and cost+obs['cost'] <= B,
                            'review limit, budget or horizon')
                    reviews += 1
                    level[j] += 1  # No callbacks occur inside [time,end).
                    e.update(end=end, fee=obs['cost'], prefix_returned_s=widths[level[j]])
                else:
                    require(all(p in completed for p in jobs[j]['predecessors']), 'predecessors')
                    b = number(action, 'b_s')
                    destination = jobs[j]['zone_id']
                    start, fee = execution_quote(time, zone, destination, b, trial.zones, rules)
                    require(b >= service['reserve_min_s'] and start+b <= H and cost+fee <= B,
                            'reservation, horizon or budget')
                    # Hidden source is consulted only after admission. Same settlement as 850380e.
                    source = trial.sources[j]
                    p = trial.alpha*source.duration+trial.beta
                    c = start+p
                    finish = c if c <= H else None
                    if finish is not None:
                        completed[j] = finish
                    zone = destination
                    e.update(start=start, b=b, fee=fee, completion=finish, end=min(H,start+max(b,p)),
                             idle=max(b-p,0.) if finish is not None else 0.,
                             overrun=max(min(p,H-start)-b,0.), censored=finish is None,
                             source_ref_offline=source.ref, source_ref_logged_at=min(c,H))
            time = e['end']
            cost += e['fee']
            ledger.append(dict(e, zone_after=zone, cost_after=cost))
            release_through(time)
        except InvalidCommand as exc:
            invalid = 1
            call['rejected'] = str(exc)
            break
    return dict(calls=calls, ledger=ledger, releases=releases,
                metrics=score(jobs,H,ledger,time,invalid))
