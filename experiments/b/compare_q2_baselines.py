"""Compare the agreed heuristics on the union of homes and submitted Q2(2) stations."""

import argparse
import itertools
import json
from pathlib import Path

from q2_order_probe import Scene, schedule


def compare(scene, submissions):
    stations = set(scene.homes.values())
    for document in submissions:
        for job in document['q22']['jobs']:
            stations.update(tuple(leg['station']) for leg in job['legs'])
    pool = sorted(stations)
    rows = []
    for order in itertools.permutations(scene.targets):
        for rule in ('sum', 'max', 'sum_time', 'max_time', 'ready'):
            events = schedule(scene, pool, order, rule)
            rows.append({'order': list(order), 'rule': rule,
                         'latest_s': max(event[1] for event in events),
                         'events': [dict(zip(('target', 'start_s', 'added_m', 'ties', 'vehicle_1', 'vehicle_2',
                                              'station_1', 'station_2'), event)) for event in events]})
    return {'stations': pool, 'direct_feasible_legs_only': True, 'rows': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('summaries', nargs='+', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    scene = Scene(json.loads(Path(__file__).with_name('scene.json').read_text()))
    result = compare(scene, [json.loads(path.read_text()) for path in args.summaries])
    result['inputs'] = [str(path) for path in args.summaries]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for row in result['rows']:
        if row['order'] == ['T1', 'T2', 'T3']:
            print(row['rule'], f"{row['latest_s']:.9f} s")


if __name__ == '__main__':
    main()
