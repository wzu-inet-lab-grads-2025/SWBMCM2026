"""Replay only the handoff's one-request accounting example at two scored times.

AI-assisted fixture and accounting checks, 2026-11-20: OpenCode, GPT-6.2 Astra
routing group (OpenAI GPT family, exact routed model release date unavailable).
"""

import argparse
from bisect import bisect_left
import json
import math
from pathlib import Path

from c_q2_windows import ROOT, qualified_runs, validation_records, windows, wrist_inputs


SECOND = 1_000_000_000


def fixed_actions(history, initial_point, request, later, receive_requested_point):
    """Times are integer ns. The requested source sample is disclosed only on delivery."""
    arrival = request + SECOND // 2
    assert history < request < arrival < later
    available = (history, initial_point)
    predictions, views = {}, []
    spent, budget = 0, 2
    for time in (request, request + SECOND // 4, arrival, later):
        if time == arrival:
            available = (request, receive_requested_point())
        views.append((time, available, spent))
        if time in (request, later):
            predictions[time] = available[1]
        if time == request:
            assert spent + 2 <= budget
            spent += 2
    return views, predictions, spent, arrival - request


def verify(source, history, request, later, trace_path=None):
    def replay(ref):
        return fixed_actions(history, ref[history], request, later, lambda: ref[request])

    run = replay(source)
    views, predictions, cost, waiting = run
    assert [v[1][0] for v in views] == [history, history, request, request]
    assert [v[2] for v in views] == [0, 2, 2, 2]
    assert predictions == {request: source[history], later: source[request]}
    assert cost == 2 and waiting == SECOND // 2

    def score(references, predictions):
        return tuple(math.dist(x, references[t]) for t, x in predictions.items())

    errors = score(source, predictions)
    altered = dict(source)
    altered[later] = tuple(x + errors[1] + 1 for x in predictions[later])
    assert replay(altered) == run
    changed_errors = score(altered, predictions)
    assert changed_errors[0] == errors[0] and changed_errors[1] != errors[1]
    altered[request] = tuple(x + 1 for x in source[request])
    changed_views, changed_predictions, _, _ = replay(altered)
    assert changed_views[:2] == views[:2] and changed_views[2] != views[2]
    assert changed_predictions[request] == predictions[request]
    assert changed_predictions[later] != predictions[later]
    assert all(math.isfinite(e) for e in errors)
    if trace_path:
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        with trace_path.open('w', encoding='utf-8') as out:
            json.dump(dict(views=[dict(event=e, time_ns=t, visible_source_ns=v[0], spent_before_action=c)
                                  for e, (t, v, c) in zip(('predict_then_request', 'pending', 'receipt', 'predict'), views)],
                           scored_timestamps=len(errors), requested_source_ns=request, cost=cost,
                           pending_ns=waiting, mean_distance_raw_units=sum(errors) / len(errors)), out, indent=2)
            out.write('\n')
    return errors


def real_fixture(root, trace_path):
    # First qualifying record/window in the official validation index, before looking at errors.
    for record, folder, _ in validation_records(root):
        if folder.parent.name == 'EgoDemo':
            continue
        times, _, points, masks = wrist_inputs(folder)
        for run in qualified_runs(times, masks['both']):
            for start, request, end in windows(times, run, SECOND, 2 * SECOND):
                later = bisect_left(times, times[start] + 2 * SECOND, request + 1, end)
                assert later < end
                indices = [request - 1, request, later]
                keys = [int(times[i] - times[start]) for i in indices]
                source = {k: tuple(points['right'][i]) for k, i in zip(keys, indices)}
                verify(source, *keys, trace_path=trace_path)
                return record
    raise ValueError('No reference-qualified validation window')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--local-trace', action='store_true', help='Save the real sample clock in runs/q2/')
    args = parser.parse_args()
    source = {s * SECOND: (s, 0, 0) for s in range(3)}
    assert verify(source, 0, SECOND, 2 * SECOND) == (1.0, 1.0)
    print('Synthetic: M=2, mean distance=1, requests=1, cost=2, pending=0.5 s. Isolation passed.')
    trace = Path('runs/q2/real_clock.json') if args.local_trace else None
    print(f'{real_fixture(args.root, trace)}: 1 window, 2 scored timestamps, 1 receipt, cost=2, pending=0.5 s. Isolation passed.')
