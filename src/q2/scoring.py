"""Q2(2) objective computed from synchronized observation starts."""

from ..shared.timeline import joint_observations


def score(events, targets):
    starts = {target: start for target, (start, _) in joint_observations(events).items()}
    if starts.keys() != set(targets):
        raise ValueError('Expected observation events for every assigned alert')
    return {'start_s': starts, 'latest_start_s': max(starts.values())}
