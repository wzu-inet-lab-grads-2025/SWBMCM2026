"""Plan hand-off. Job order specifies precedence only for shared vehicles."""

from dataclasses import asdict, dataclass

from .scene import coordinates


@dataclass(frozen=True)
class Leg:
    vehicle: str
    station: tuple
    path: tuple


@dataclass(frozen=True)
class Job:
    target: str
    legs: tuple[Leg, Leg]


@dataclass(frozen=True)
class Plan:
    method: str
    scene_sha256: str
    jobs: tuple[Job, ...]

    @classmethod
    def load(cls, document, method, scene_sha256):
        if not isinstance(method, str) or not method.strip():
            raise ValueError('A plan requires a method name')
        if document.get('schema') == 'b.nominal-replay.v1':
            document = document['plan']
        if 'q22' in document:
            rows = document['q22']['jobs']
        else:
            if (document.get('schema') != 'b.plan.v1' or document['method'] != method
                    or document['scene_sha256'] != scene_sha256):
                raise ValueError('Plan schema, method or scene differs from configuration')
            rows = document['jobs']
        jobs = []
        for row in rows:
            legs = []
            for item in row['legs']:
                station = coordinates(item['station'], 2)
                path = tuple(coordinates(p, 2) for p in item['path'])
                if not path or path[-1] != station:
                    raise ValueError(f"{row['target']}: route must end at the assigned station")
                # Canonical stationary leg for both snapshot and native formats.
                if len(path) == 1:
                    path = (station, station)
                legs.append(Leg(item['vehicle'], station, path))
            if len(legs) != 2 or legs[0].vehicle == legs[1].vehicle:
                raise ValueError(f"{row['target']}: expected two distinct vehicles")
            jobs.append(Job(row['target'], tuple(legs)))
        return cls(method, scene_sha256, tuple(jobs))

    def to_dict(self):
        return {'schema': 'b.plan.v1', **asdict(self)}
