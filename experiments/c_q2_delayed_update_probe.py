"""Fixed training-only probe: hold a delayed packet or extrapolate its two source points.

Reuses the team's paired windows, packet scheduler and stepwise duration convention.
Specification: research/C题迟到双腕包更新对照.md. No fitting/tuning or held-out entry.
AI assistance, 2026-11-20: OpenCode, Sonus / Qoder / Remote magpie,
provider magpie, model remote-magpie/qoder/smodel; release date unavailable.
Imported shared scripts retain their own notices. Human competition review required.
"""

import argparse
from collections import defaultdict
import csv
import hashlib
from pathlib import Path
import sys

sys.dont_write_bytecode = True
import numpy as np

if __package__:
    from .c_exceed_duration import runs_ns
    from .c_packet_value import PairedWindow, ROOT, SECOND, SIDES, paired_windows, replay
    from .c_records import split_records
else:
    from c_exceed_duration import runs_ns
    from c_packet_value import PairedWindow, ROOT, SECOND, SIDES, paired_windows, replay
    from c_records import split_records


OUT = Path(__file__).resolve().parents[1] / 'runs/q2_diagnostic/train_delayed_update_probe.csv'
DELAY_MS = 500
PHASES = ('post', 'all')
METRICS = ('cm', 'exceed', 'longest')
DIRECTIONS = ('improved', 'worsened', 'tied')
LEVELS = ('windows', 'records', 'environments')
CHANGE_COUNTS = tuple(f'{metric}_{direction}_{level}' for metric in METRICS
                      for level in LEVELS for direction in DIRECTIONS)
COUNTS = ('indexed_records', 'indexed_environments', 'records', 'environments',
          'windows', 'points', 'grid_intervals', 'requests', 'receipts', 'cost', 'pre_identical_windows')
SUMS = ('pending_s', 'scored_total_s')
MEANS = ('scored_mean_s', 'hold_cm', 'extrap_cm', 'delta_cm',
         'hold_exceed_s', 'extrap_exceed_s', 'delta_exceed_s', 'avoided_s', 'induced_s',
         'hold_longest_s', 'extrap_longest_s', 'delta_longest_s')
RANGES = tuple(f'{name}_{bound}' for name in ('scale_cm', 'source_gap_s', 'first_source_gap_s', 'arrival_age_s')
               for bound in ('min', 'max'))
FIELDS = ('split', 'scope', 'phase', 'hand', 'environment', 'record', 'dataset',
          *COUNTS, *SUMS, *MEANS, *RANGES, *CHANGE_COUNTS)


def request_clock(clock, anchor_ns):
    """First real scoring tick >= one second since the last observed source."""
    if clock.dtype != np.int64 or not len(clock) or np.any(np.diff(clock) <= 0):
        raise ValueError('Expected a nonempty strictly increasing integer-nanosecond clock')
    if clock[0] <= anchor_ns:
        raise ValueError('Masked scoring ticks must follow the old anchor')
    i = int(clock.searchsorted(anchor_ns + SECOND))
    if i == len(clock):
        raise ValueError('No permitted request tick in the window')
    source_ns = int(clock[i])
    return source_ns, source_ns + DELAY_MS * 10**6


def predict_pair(clock, anchor_ns, initial, receive):
    """No truth/quality arrays: only old anchor, clock and an on-receipt callback.

    The shared replay calls receive only when due. First construct its hold output,
    then apply the source-time slope ONLY to rows at/after receipt. The event grid
    supplied by the scorer includes the exact receipt nanosecond.
    """
    source_ns, arrival_ns = request_clock(clock, anchor_ns)
    calls = []

    def disclosed(source):
        if source != source_ns or calls:
            raise ValueError('Expected exactly one paired packet from the fixed request source')
        calls.append(source)
        packet = np.asarray(receive(source), dtype=float).copy()
        if packet.shape != (2, 3) or not np.isfinite(packet).all():
            raise ValueError('Invalid paired wrist packet')
        return packet

    hold, bill = replay(clock, initial.copy(), DELAY_MS, disclosed,
                        request_not_before_ns=anchor_ns + SECOND)
    if calls != [source_ns] or bill != (1, 1, 2, .5):
        raise ValueError('Packet accounting changed')
    post = clock >= arrival_ns
    if not post.any():
        raise ValueError('No post-receipt prediction')
    extrap = hold.copy()
    # Both subtractions are integer ns BEFORE conversion; receive time is not a source time.
    slope = (hold[post][0] - initial) / ((source_ns - anchor_ns) / SECOND)
    extrap[post] += ((clock[post] - source_ns) / SECOND)[:, None, None] * slope
    np.testing.assert_array_equal(hold[~post], extrap[~post])
    np.testing.assert_array_equal(hold[~post], np.broadcast_to(initial, hold[~post].shape))
    return hold, extrap, bill


def window_rows(w):
    """Paired scores; reference access stays here, outside predict_pair."""
    source_ns, arrival_ns = request_clock(w.clock, w.anchor_ns)
    if arrival_ns >= w.mask_end_ns:
        raise ValueError('Fixed packet must arrive before the nominal mask end')
    if w.scale.shape != (2,) or not np.isfinite(w.scale).all() or np.any(w.scale <= 0):
        raise ValueError('Invalid locked historical scale; do not silently drop this window')
    grid = np.union1d(w.clock, np.array([arrival_ns], dtype=np.int64))
    edges = np.r_[grid, np.int64(w.mask_end_ns)]
    widths = np.diff(edges)
    if np.any(widths <= 0):
        raise ValueError('Nonpositive scoring interval')
    hold, extrap, bill = predict_pair(grid, w.anchor_ns, w.initial, w.receive)
    reference = w.truth[w.clock.searchsorted(grid, side='right') - 1]
    h_error = np.linalg.norm(hold - reference, axis=2)
    v_error = np.linalg.norm(extrap - reference, axis=2)
    h_out, v_out = h_error > w.scale, v_error > w.scale
    arrival_i = int(grid.searchsorted(arrival_ns))
    np.testing.assert_array_equal(h_out[:arrival_i], v_out[:arrival_i])
    real_ticks = np.isin(grid, w.clock)
    # No synthetic receipt/reference point contributes an extra centimeter observation.
    assert int(real_ticks.sum()) == len(w.clock)
    for phase in PHASES:
        start = arrival_i if phase == 'post' else 0
        ticks = real_ticks.copy()
        ticks[:start] = False
        n = int(ticks.sum())
        if not n:
            raise ValueError('Empty phase; paired denominators must not change')
        phase_widths, phase_edges = widths[start:], edges[start:]
        for j, side in enumerate(SIDES):
            h, v = h_out[start:, j], v_out[start:, j]
            h_ns, v_ns = int(phase_widths[h].sum()), int(phase_widths[v].sum())
            avoided = int(phase_widths[h & ~v].sum())
            induced = int(phase_widths[~h & v].sum())
            assert v_ns - h_ns == induced - avoided
            hl_ns = int(runs_ns(h, phase_edges).max(initial=0))
            vl_ns = int(runs_ns(v, phase_edges).max(initial=0))
            h_sum, v_sum = 100 * h_error[ticks, j].sum(), 100 * v_error[ticks, j].sum()
            row = dict(record=w.record, environment=w.environment, phase=phase, hand=side,
                       windows=1, points=n, grid_intervals=len(phase_widths),
                       requests=bill[0], receipts=bill[1], cost=bill[2], pending_s=bill[3],
                       pre_identical_windows=1, scored_total_s=float(phase_widths.sum() / SECOND),
                       hold_cm=float(h_sum / n), extrap_cm=float(v_sum / n), delta_cm=float((v_sum-h_sum) / n),
                       hold_exceed_s=h_ns/SECOND, extrap_exceed_s=v_ns/SECOND,
                       delta_exceed_s=(v_ns-h_ns)/SECOND, avoided_s=avoided/SECOND, induced_s=induced/SECOND,
                       hold_longest_s=hl_ns/SECOND, extrap_longest_s=vl_ns/SECOND,
                       delta_longest_s=(vl_ns-hl_ns)/SECOND,
                       _hold_cm_sum=float(h_sum), _extrap_cm_sum=float(v_sum))
            row['scored_mean_s'] = row['scored_total_s']
            for name, value in (('scale_cm', float(100*w.scale[j])),
                                ('source_gap_s', (source_ns-w.anchor_ns)/SECOND),
                                ('first_source_gap_s', int(w.clock[0]-w.anchor_ns)/SECOND),
                                ('arrival_age_s', (arrival_ns-w.anchor_ns)/SECOND)):
                row[name + '_min'] = row[name + '_max'] = value
            for metric, difference in (('cm', v_sum-h_sum), ('exceed', v_ns-h_ns), ('longest', vl_ns-hl_ns)):
                for direction, sign in zip(DIRECTIONS, (-1, 1, 0)):
                    row[f'{metric}_{direction}_windows'] = int(np.sign(difference) == sign)
            yield row


def empty_row(scope, phase, side, env='', record='', dataset=''):
    row = dict.fromkeys(FIELDS)
    row.update(split='train', scope=scope, phase=phase, hand=side, environment=env, record=record, dataset=dataset)
    row.update(dict.fromkeys((*COUNTS, *SUMS, *CHANGE_COUNTS), 0))
    return row


def set_change_counts(row, level):
    for metric in METRICS:
        value = row['delta_cm' if metric == 'cm' else f'delta_{metric}_s']
        for direction, sign in zip(DIRECTIONS, (-1, 1, 0)):
            row[f'{metric}_{direction}_{level}'] = int(value is not None and np.sign(value) == sign)


def set_ranges(row, members):
    for key in RANGES:
        values = [r[key] for r in members if r[key] is not None]
        row[key] = (min(values) if key.endswith('_min') else max(values)) if values else None


def record_rows(inventory, samples):
    groups = defaultdict(list)
    for row in samples:
        groups[row['record'], row['phase'], row['hand']].append(row)
    for record, env, dataset in inventory:
        for phase in PHASES:
            for side in SIDES:
                row = empty_row('record', phase, side, env, record, dataset)
                row['indexed_records'] = row['indexed_environments'] = 1
                members = groups.get((record, phase, side), [])
                if members:
                    row['records'] = row['environments'] = 1
                    for key in (*COUNTS[4:], *SUMS):
                        row[key] = sum(r[key] for r in members)
                    for key in MEANS:
                        row[key] = float(np.mean([r[key] for r in members]))
                    # Distances use real points, durations use equal windows, within each record.
                    for method in ('hold', 'extrap'):
                        row[method+'_cm'] = sum(r['_'+method+'_cm_sum'] for r in members) / row['points']
                    row['delta_cm'] = row['extrap_cm'] - row['hold_cm']
                    for key in CHANGE_COUNTS:
                        if key.endswith('_windows'):
                            row[key] = sum(r[key] for r in members)
                    set_ranges(row, members)
                    set_change_counts(row, 'records')
                yield row


def combine(members, scope):
    first = members[0]
    row = empty_row(scope, first['phase'], first['hand'], first['environment'] if scope == 'environment' else '')
    scored = [r for r in members if r['windows']]
    for key in (*COUNTS, *SUMS, *CHANGE_COUNTS):
        row[key] = sum(r[key] for r in members)
    row['indexed_environments'] = len(members) if scope == 'macro' else 1
    row['environments'] = len(scored) if scope == 'macro' else int(bool(scored))
    for key in MEANS:
        row[key] = float(np.mean([r[key] for r in scored])) if scored else None
    set_ranges(row, members)
    if scope == 'environment':
        set_change_counts(row, 'environments')
    return row


def summarize(inventory, samples):
    records = list(record_rows(inventory, samples))
    env_groups = defaultdict(list)
    for row in records:
        env_groups[row['phase'], row['hand'], row['environment']].append(row)
    envs = [combine(members, 'environment') for members in env_groups.values()]
    macro_groups = defaultdict(list)
    for row in envs:
        macro_groups[row['phase'], row['hand']].append(row)
    return [combine(members, 'macro') for members in macro_groups.values()] + envs + records


def evaluate(root):
    # Reading the official split indices is allowed; every raw-data entry is train-only.
    inventory = [(record, meta['environment_id'], folder.parent.name)
                 for record, folder, meta in split_records(root, 'train')]
    samples = [row for w in paired_windows(root) for row in window_rows(w)]
    if not samples or not inventory:
        raise ValueError('Empty training pool; do not overwrite the aggregate')
    return summarize(inventory, samples)


def self_test():
    origin = 1_700_000_000_000_000_000
    source = origin + 1_002_000_000
    arrival = source + 500_000_000
    clock = origin + np.array([20_000_000, 990_000_000, 1_002_000_000,
                               1_499_000_000, 1_501_999_999, 1_502_000_000,
                               1_502_000_001, 1_999_000_000], dtype=np.int64)
    initial = np.array([[4., 5., 6.], [-1., 2., 3.]])
    slope = np.array([[.2, -.3, .5], [-.4, .6, -.2]])
    packet = initial + (source-origin)/SECOND*slope
    calls = []

    def receive(ns):
        calls.append(ns)
        assert ns == source
        return packet.copy()

    h, v, bill = predict_pair(clock, origin, initial, receive)
    pre = clock < arrival
    assert calls == [source] and bill == (1, 1, 2, .5)
    np.testing.assert_array_equal(v[pre], np.broadcast_to(initial, v[pre].shape))
    np.testing.assert_allclose(v[~pre], initial + ((clock[~pre]-origin)/SECOND)[:, None, None]*slope,
                               rtol=0, atol=1e-14)
    np.testing.assert_allclose(v[~pre][0], packet+.5*slope, rtol=0, atol=1e-14)
    assert not np.array_equal(v[~pre][0], packet)
    changed = predict_pair(clock, origin, initial, {source: packet+10}.__getitem__)
    for before, after in zip((h, v), changed[:2]):
        np.testing.assert_array_equal(before[pre], after[pre])
        assert not np.array_equal(before[~pre], after[~pre])
    # Shared scheduler really defers disclosure, including a one-nanosecond-before check.
    def forbidden(_):
        raise AssertionError('Receipt callback called before due')
    try:
        replay(clock[pre], initial, DELAY_MS, forbidden, request_not_before_ns=origin+SECOND)
    except ValueError as error:
        assert 'did not arrive' in str(error)
    else:
        raise AssertionError('Missing receipt must fail closed')

    # Change every hidden non-request reference, leaving the sole returned packet untouched.
    truth = initial + ((clock-origin)/SECOND)[:, None, None]*slope
    w = PairedWindow('R', 'E', clock, origin+2*SECOND, origin, initial, truth, np.ones(2))
    original = predict_pair(clock, origin, initial, w.receive)
    mutated = truth.copy()
    mutated[clock != source] += 100
    alternate = PairedWindow('R', 'E', clock, origin+2*SECOND, origin, initial, mutated, np.ones(2))
    repeated = predict_pair(clock, origin, initial, alternate.receive)
    for before, after in zip(original[:2], repeated[:2]):
        np.testing.assert_array_equal(before, after)
    assert list(window_rows(w))[0]['hold_cm'] != list(window_rows(alternate))[0]['hold_cm']

    # Receipt between real ticks, strict 1L, separate hands, and truncated post longest spell.
    t = np.array([100, 1000, 1400, 1600, 1900], dtype=np.int64)*10**6
    p = np.zeros((5, 2, 3))
    p[:, 0, 0] = [0, 2, 2, 4, 4]
    toy = PairedWindow('R', 'E', t, 2*SECOND, 0, np.zeros((2, 3)), p, np.ones(2))
    toy_rows = list(window_rows(toy))
    post, full = [r for r in toy_rows if r['hand'] == 'left']
    assert post['points'] == 2 and post['grid_intervals'] == 3
    assert full['points'] == 5 and full['grid_intervals'] == 6
    for key, expected in (('hold_cm', 200.), ('extrap_cm', 50.), ('hold_exceed_s', .4),
                          ('extrap_exceed_s', 0.), ('hold_longest_s', .4),
                          ('extrap_longest_s', 0.), ('avoided_s', .4), ('induced_s', 0.)):
        np.testing.assert_allclose(post[key], expected, rtol=0, atol=1e-12)
    assert full['hold_exceed_s'] == .9 and full['extrap_exceed_s'] == .5
    assert full['hold_longest_s'] == full['extrap_longest_s'] == .5
    assert all(r['hold_exceed_s'] == r['extrap_exceed_s'] == 0 for r in toy_rows if r['hand'] == 'right')
    # An already-ongoing spell is cut at receipt for post-only longest, never joined across windows.
    p[:, 1, 0] = [0, 0, 2, 2, 2]
    ongoing = list(window_rows(toy))
    assert ongoing[1]['hold_longest_s'] == ongoing[1]['extrap_longest_s'] == .5
    assert ongoing[3]['hold_longest_s'] == ongoing[3]['extrap_longest_s'] == .6
    # With no displacement, both rules coincide at every tick.
    stationary = PairedWindow('R', 'E', t, 2*SECOND, 0, initial, np.broadcast_to(initial, (5, 2, 3)), np.ones(2))
    assert all(r['delta_cm'] == r['delta_exceed_s'] == r['delta_longest_s'] == 0
               for r in window_rows(stationary))

    # Unequal windows/points/records/environments; zero-window coverage must stay visible.
    inventory = [('a', 'A', 'synthetic'), ('b', 'A', 'synthetic'), ('c', 'B', 'synthetic'), ('d', 'C', 'synthetic')]
    samples = []
    for rec, env, n, hc, vc, duration in [('a', 'A', 1, 1, 0, .1), ('a', 'A', 3, 5, 4, .5),
                                         ('b', 'A', 2, 10, 9, .7), ('c', 'B', 7, 20, 22, 1.5)]:
        row = full.copy()
        row.update(record=rec, environment=env, points=n, _hold_cm_sum=n*hc, _extrap_cm_sum=n*vc,
                   hold_exceed_s=duration)
        samples.append(row)
    summary = summarize(inventory, samples)
    macro = next(r for r in summary if r['scope'] == 'macro' and r['phase'] == 'all' and r['hand'] == 'left')
    assert macro['hold_cm'] == 13.5 and macro['extrap_cm'] == 14. and macro['delta_cm'] == .5
    assert macro['hold_exceed_s'] == 1.
    assert (macro['indexed_records'], macro['indexed_environments'], macro['records'], macro['environments']) == (4, 3, 3, 2)
    assert (macro['windows'], macro['points']) == (4, 13)
    assert macro['cm_improved_environments'] == macro['cm_worsened_environments'] == 1
    assert macro['cm_improved_records'] == 2 and macro['cm_worsened_records'] == 1
    print('Self-test passed: true source-time slope, exact due boundary, shared packet accounting, '
          'pre-receipt identity, hidden-reference isolation, strict 1L/receipt-split durations, '
          'real-point denominators, hierarchical weights and zero-window coverage.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT, help='Read-only attachment root; official train only')
    parser.add_argument('--self-test', action='store_true', help='Synthetic in-memory tests only; no data/output access')
    args = parser.parse_args()
    self_test()
    if args.self_test:
        return
    rows = evaluate(args.root)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator='\n')
        writer.writeheader()
        writer.writerows({k: f'{v:.12f}' if isinstance(v, float) else v for k, v in row.items()} for row in rows)
    for row in rows:
        if row['scope'] == 'macro':
            print(f"{row['phase']:>4} {row['hand']:>5}: cm={row['hold_cm']:.9f}->{row['extrap_cm']:.9f}; "
                  f"exceed_s={row['hold_exceed_s']:.9f}->{row['extrap_exceed_s']:.9f}; "
                  f"longest_s={row['hold_longest_s']:.9f}->{row['extrap_longest_s']:.9f}; "
                  f"windows={row['windows']} points={row['points']}")
    print(f'Python {sys.version.split()[0]}; NumPy {np.__version__}')
    print(f'{OUT.relative_to(OUT.parents[2])}: {len(rows)} aggregate rows; '
          f'SHA256 {hashlib.sha256(OUT.read_bytes()).hexdigest()}')


if __name__ == '__main__':
    main()
