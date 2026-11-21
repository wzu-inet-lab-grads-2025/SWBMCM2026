"""Retrospective equal-spend ceiling for the established training window protocol.

The scorer alone knows the future. The number bought in each recording is
taken from the archived out-of-environment rule. AI-assisted in OpenCode,
OpenAI GPT family, GPT-6.2 Astra routing group, 2026-11-21. Release date
unavailable.
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from .c_q2_select_packet_probe import load_training, score_reference
from .c_records import ROOT


def score(root, archive):
    windows, visible, _ = load_training(root)
    scored = pd.DataFrame([score_reference(w) for w in windows])
    archive_rows = pd.read_csv(archive)
    ledger = archive_rows.query("block == 'accounting' and scope == 'record' and hand == 'both' and method == 'gain'").set_index('record', verify_integrity=True)
    if set(visible.record) != set(ledger.index):
        raise ValueError('Record pool differs from archived actions')
    rows = []
    for record, group in visible.groupby('record'):
        old = ledger.loc[record]
        n, k = len(group), int(old.requests)
        if n != old.windows or not 0 <= k <= n or set(group.environment) != {old.environment}:
            raise ValueError('Inconsistent archived quota or environment')
        potentials = scored.loc[group.index]
        gain = np.sort(potentials.g.to_numpy())[::-1]
        base = potentials.base_total_both.mean()
        random_loss = base - (k/n) * gain.mean()
        ceiling_loss = base - gain[:k].sum() / n
        if not np.isclose(random_loss - old.total_s, old.selection_total_s, rtol=0, atol=2e-10):
            raise ValueError('Equal-count comparison does not reproduce archived result')
        if ceiling_loss > min(random_loss, old.total_s) + 2e-10:
            raise ValueError('Retrospective bound exceeded')
        rows.append(dict(environment=old.environment, windows=n, packets=k, records=1,
                         positive_gain=int(np.count_nonzero(gain > 1e-12)),
                         negative_gain=int(np.count_nonzero(gain < -1e-12)),
                         random_s=random_loss, rule_s=old.total_s, hindsight_s=ceiling_loss))
    counts = ['windows', 'packets', 'records', 'positive_gain', 'negative_gain']
    seconds = ['random_s', 'rule_s', 'hindsight_s']
    env = pd.DataFrame(rows).groupby('environment', as_index=False).agg(
        {**dict.fromkeys(counts, 'sum'), **dict.fromkeys(seconds, 'mean')})
    macro = dict(environment='all', **env[counts].sum().to_dict(), **env[seconds].mean().to_dict())
    table = pd.concat([env, pd.DataFrame([macro])], ignore_index=True)
    table['available_gain_s'] = table.random_s - table.hindsight_s
    table['realised_gain_s'] = table.random_s - table.rule_s
    return table


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=ROOT)
    p.add_argument('--archive', type=Path, default=Path('research/q2_select_packet_probe.csv'))
    p.add_argument('--out', type=Path, default=Path('runs/c_feas_selection_bound/summary.csv'))
    args = p.parse_args()
    table = score(args.root, args.archive)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.out, index=False, float_format='%.12g')
    print(table.tail(1).to_string(index=False))
