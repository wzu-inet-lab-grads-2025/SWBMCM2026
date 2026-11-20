"""Descriptive replay of the team's paired-wrist hold-last and fixed-request examples.

AI-assisted preprocessing, accounting and plotting, 2026-11-20, OpenAI GPT-6
family in OpenCode (GPT-6.2 Astra routing group, exact release date unavailable).
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

if __package__:
    from .c_q2_windows import ROOT, SIDES, qualified_runs, split_records, windows, wrist_inputs
else:
    from c_q2_windows import ROOT, SIDES, qualified_runs, split_records, windows, wrist_inputs


SECOND = 10**9
BINS = ('0-0.5', '0.5-1', '1-1.5', '1.5-end')
VALIDATION_WINDOWS = {'V01': 0, 'V02': 0, 'V03': 11, 'V04': 11, 'V05': 9, 'V06': 16, 'V07': 3}
FEE = 2


def replay(times, initial_pair, delay_ms, receive):
    """Hold each wrist separately. The one allowed packet contains two XYZ rows."""
    visible = initial_pair
    predictions = []
    request_ns = receipt_ns = None
    returned = False
    for now in times:
        if receipt_ns is not None and not returned and now >= receipt_ns:
            visible = receive(request_ns)
            returned = True
        predictions.append(visible.copy())
        if delay_ms and request_ns is None:
            request_ns = int(now)  # After submitting the first masked prediction.
            receipt_ns = request_ns + delay_ms * 10**6
    if returned != bool(delay_ms):
        raise ValueError('Fixed packet did not arrive inside the scoring window')
    requests = int(request_ns is not None)
    pending_s = (receipt_ns - request_ns) / SECOND if returned else 0
    return np.stack(predictions), (requests, int(returned), FEE * requests, pending_s)


def evaluate(root, split):
    delays = (0, 500) if split == 'validation' else (0, 250, 500, 1000)
    samples, accounts = [], []
    for record, folder, meta in split_records(root, split):
        env = meta['environment_id']
        dataset = folder.parent.name
        totals = {delay: np.zeros(4) for delay in delays}
        n_windows = n_scored = 0
        if dataset != 'EgoDemo':
            times, _, points, masks = wrist_inputs(folder)
            pairs = np.stack([points[side] for side in SIDES], axis=1)
            for run in qualified_runs(times, masks['both']):
                for _, first, end in windows(times, run, SECOND, 2 * SECOND):
                    n_windows += 1
                    n_scored += end - first
                    clock = times[first:end]
                    age = clock - times[first - 1]
                    bins = np.asarray(BINS)[np.searchsorted([SECOND//2, SECOND, 3*SECOND//2], age)]

                    def receive(source_ns):
                        if source_ns != int(clock[0]):
                            raise ValueError('Request does not identify the first masked sample')
                        return pairs[first].copy()

                    for delay in delays:
                        predictions, bill = replay(clock, pairs[first - 1], delay, receive)
                        totals[delay] += bill
                        errors = np.linalg.norm(predictions - pairs[first:end], axis=2)
                        samples.append(pd.DataFrame(dict(record=record, environment=env, window=n_windows,
                                                         delay_ms=delay, age_bin=bins, age_s=age/SECOND,
                                                         left=errors[:, 0], right=errors[:, 1])))
        if split == 'validation' and n_windows != VALIDATION_WINDOWS[record]:
            raise ValueError(f'Validation window pool changed for {record}')
        for delay in delays:
            requests, receipts, cost, pending = totals[delay]
            assert requests == receipts == (n_windows if delay else 0)
            assert cost == FEE * requests and pending == delay * requests / 1000
            accounts.append(dict(record=record, dataset=dataset, environment=env, delay_ms=delay,
                                 windows=n_windows, timestamps_per_hand=n_scored, requests=int(requests),
                                 receipts=int(receipts), cost=int(cost), pending_s=pending))
    if split == 'validation' and {r['record'] for r in accounts} != set(VALIDATION_WINDOWS):
        raise ValueError('Validation record membership changed')
    return pd.concat(samples, ignore_index=True), pd.DataFrame(accounts)


def summaries(samples):
    groups = ['delay_ms', 'age_bin']
    by_record = (samples.groupby(groups + ['environment', 'record'], as_index=False)
                 .agg(windows=('window', 'nunique'), timestamps=('window', 'size'), max_age_s=('age_s', 'max'),
                      left=('left', 'mean'), right=('right', 'mean')))
    by_record['records'] = 1
    merge = dict(windows=('windows', 'sum'), timestamps=('timestamps', 'sum'), records=('records', 'sum'),
                 max_age_s=('max_age_s', 'max'), left=('left', 'mean'), right=('right', 'mean'))
    by_env = by_record.groupby(groups + ['environment'], as_index=False).agg(**merge)
    macro = by_env.groupby(groups, as_index=False).agg(**merge, environments=('environment', 'size'))
    macro['scope'], by_env['scope'], by_record['scope'] = 'macro', 'environment', 'record'
    by_env['environments'] = by_record['environments'] = 1
    return pd.concat([macro, by_env, by_record], ignore_index=True)


def training_quantiles(samples):
    # Each eligible environment receives equal mass. Within an environment each
    # record receives equal mass, distributed over its timestamps in this bin.
    for label, group in samples[samples.delay_ms == 0].groupby('age_bin'):
        points = group.groupby('record')['record'].transform('size')
        records = group.groupby('environment')['record'].transform('nunique')
        weights = 1 / (records * points)
        for side in SIDES:
            order = np.argsort(group[side].to_numpy())
            cumulative = weights.to_numpy()[order].cumsum()
            values = group[side].to_numpy()[order]
            median, upper = values[np.searchsorted(cumulative, np.array([0.5, 0.9]) * cumulative[-1])]
            yield dict(age_bin=label, hand=side, environments=group.environment.nunique(),
                       records=group.record.nunique(), timestamps=len(group), p50=median, p90=upper)


def plot(curves, out):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6.4, 3.6), layout='constrained')
    macro = curves.query("scope == 'macro' and delay_ms == 0").set_index('age_bin').loc[list(BINS)]
    for side, marker in zip(SIDES, ('o', 's')):
        ax.plot(range(4), macro[side], marker=marker, linewidth=2, label=f'{side.title()} wrist')
    ax.set(xticks=range(4), xticklabels=['(0, 0.5]', '(0.5, 1]', '(1, 1.5]', '> 1.5'],
           xlabel='Seconds since the last pre-mask observation', ylabel='Mean 3D distance (raw units)',
           title='No re-observation on 50 validation windows', ylim=(0, None))
    ax.legend(frameon=False)
    ax.grid(axis='y', alpha=0.22)
    fig.savefig(out / 'validation_no_request.png', dpi=180)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--split', choices=('train', 'validation'), required=True)
    parser.add_argument('--out', type=Path, default=Path('runs/q2_diagnostic'))
    args = parser.parse_args()
    samples, accounts = evaluate(args.root, args.split)
    curves = pd.concat([summaries(samples), summaries(samples.assign(age_bin='all'))], ignore_index=True)
    for table in (accounts, curves):
        table.insert(0, 'method', np.where(table.delay_ms == 0, 'no_request', 'one_packet'))
    args.out.mkdir(parents=True, exist_ok=True)
    curves.to_csv(args.out / f'{args.split}_curves.csv', index=False, float_format='%.9f')
    accounts.to_csv(args.out / f'{args.split}_accounts.csv', index=False)
    if args.split == 'train':
        pd.DataFrame(training_quantiles(samples)).to_csv(args.out / 'train_no_request_quantiles.csv',
                                                        index=False, float_format='%.9f')
    else:
        plot(curves, args.out)
    print(curves.query("scope == 'macro'")[['delay_ms', 'age_bin', 'environments', 'records', 'windows',
                                             'timestamps', 'left', 'right']].to_string(index=False))


if __name__ == '__main__':
    main()
